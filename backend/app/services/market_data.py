import time
import logging
import threading
import numpy as np
import akshare as ak
from datetime import datetime, timedelta
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# ─── 有界缓存（LRU淘汰，防止内存泄漏） ───

class BoundedCache(OrderedDict):
    """基于 OrderedDict 的 LRU 缓存，最大容量限制，支持主动过期清理。"""

    def __init__(self, maxsize: int = 1024):
        super().__init__()
        self._maxsize = maxsize

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        while len(self) > self._maxsize:
            self.popitem(last=False)  # 淘汰最久未访问的

    def cleanup(self, timeout: float) -> int:
        """移除超时条目（value首元素为时间戳），返回清理数量。"""
        now = time.time()
        expired = [k for k, v in self.items() if now - v[0] > timeout]
        for k in expired:
            del self[k]
        return len(expired)


# ─── 缓存注册表（用于统一周期清理） ───

_all_caches: list[tuple[BoundedCache, float]] = []


def _register_cache(cache: BoundedCache, timeout: float) -> BoundedCache:
    _all_caches.append((cache, timeout))
    return cache


# ─── 缓存配置 ───

_cache_timeout = 60
_refresh_ahead = 10
_last_fetch_time = 0.0
_cached_stocks: list[dict] = []
_stock_index: dict[str, dict] = {}
_price_map_cache: dict[str, float] = {}
_fetching = False
_refreshing = False
_cache_lock = threading.Lock()
_cache_ready = threading.Event()

# 行业板块缓存（5分钟）
_sector_list_cache: list[dict] = []
_sector_list_cache_time = 0.0
_sector_constituent_cache: BoundedCache = _register_cache(BoundedCache(256), 300)  # sector_name -> (ts, codes)
_sector_constituent_timeout = 300

# 板块概览缓存（5分钟）
_sector_overview_cache: list[dict] = []
_sector_overview_cache_time = 0.0

# K线缓存（60秒）
_kline_cache: BoundedCache = _register_cache(BoundedCache(512), 60)
_kline_cache_timeout = 60

# 大盘指数缓存（60秒）
_index_cache: tuple[float, list[dict]] = (0.0, [])
_index_cache_timeout = 60

# 52周高低缓存（5分钟）
_52week_cache: BoundedCache = _register_cache(BoundedCache(1024), 300)  # code -> (ts, high, low)
_52week_cache_timeout = 300

_executor = ThreadPoolExecutor(max_workers=8)


# ─── AKShare 数据转换 ───

def _safe_float(val, default=0.0) -> float:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _convert_ak_spot(df) -> list[dict]:
    """将 ak.stock_zh_a_spot_em() 的 DataFrame 转为统一中文 key 格式。"""
    result = []
    for _, row in df.iterrows():
        price = _safe_float(row.get("最新价", 0))
        result.append({
            "代码": str(row.get("代码", "")),
            "名称": str(row.get("名称", "")),
            "最新价": price,
            "涨跌额": _safe_float(row.get("涨跌额", 0)),
            "涨跌幅": _safe_float(row.get("涨跌幅", 0)),
            "今开": _safe_float(row.get("今开", 0)),
            "最高": _safe_float(row.get("最高", 0)),
            "最低": _safe_float(row.get("最低", 0)),
            "昨收": _safe_float(row.get("昨收", 0)),
            "买一": price,  # akshare 列表不返回买卖盘，默认为最新价
            "卖一": price,
            "成交量": int(_safe_float(row.get("成交量", 0))),
            "成交额": _safe_float(row.get("成交额", 0)),
            "换手率": _safe_float(row.get("换手率", 0)),
            "市盈率-动态": _safe_float(row.get("市盈率-动态", 0)),
            "市净率": _safe_float(row.get("市净率", 0)),
            "总市值": _safe_float(row.get("总市值", 0)),
            "流通市值": _safe_float(row.get("流通市值", 0)),
            "量比": _safe_float(row.get("量比", 0)),
        })
    return result


# ─── 全市场行情 ───

def _convert_sina_spot(df) -> list[dict]:
    """将 ak.stock_zh_a_spot() (新浪) 的 DataFrame 转为统一中文 key 格式。"""
    result = []
    for _, row in df.iterrows():
        raw_code = str(row.get("代码", ""))
        code = raw_code[2:] if len(raw_code) > 6 and raw_code[:2] in ("sh", "sz", "bj") else raw_code
        price = _safe_float(row.get("最新价", 0))
        result.append({
            "代码": code,
            "名称": str(row.get("名称", "")),
            "最新价": price,
            "涨跌额": _safe_float(row.get("涨跌额", 0)),
            "涨跌幅": _safe_float(row.get("涨跌幅", 0)),
            "今开": _safe_float(row.get("今开", 0)),
            "最高": _safe_float(row.get("最高", 0)),
            "最低": _safe_float(row.get("最低", 0)),
            "昨收": _safe_float(row.get("昨收", 0)),
            "买一": _safe_float(row.get("买入", price)),
            "卖一": _safe_float(row.get("卖出", price)),
            "成交量": int(_safe_float(row.get("成交量", 0))),
            "成交额": _safe_float(row.get("成交额", 0)),
            "换手率": 0,
            "市盈率-动态": 0,
            "市净率": 0,
            "总市值": 0,
            "流通市值": 0,
            "量比": 0,
            "_degraded": True,
        })
    return result


def _fetch_all_stocks() -> list[dict]:
    """获取全市场 A 股实时行情。东方财富优先，失败降级新浪。均失败返回旧缓存。"""
    global _cached_stocks
    # 东方财富
    try:
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            return _convert_ak_spot(df)
    except Exception as e:
        logger.warning("东方财富行情API失败: %s", e)
    # 新浪备用
    try:
        df = ak.stock_zh_a_spot()
        if df is not None and not df.empty:
            logger.info("使用新浪行情备用接口，%d条", len(df))
            return _convert_sina_spot(df)
    except Exception as e:
        logger.warning("新浪行情API失败: %s", e)
    logger.warning("所有行情API失败，使用旧缓存(%d条)", len(_cached_stocks))
    return _cached_stocks


def get_spot_data() -> list[dict]:
    """获取全市场A股实时行情，带60秒缓存。过期前提前后台刷新，避免冷加载。"""
    global _cached_stocks, _last_fetch_time, _fetching, _refreshing
    global _stock_index, _price_map_cache

    def _update_cache(data):
        """原子更新缓存数据。需在 _cache_lock 内调用。"""
        global _cached_stocks, _stock_index, _price_map_cache, _last_fetch_time
        _cached_stocks = data
        _stock_index = {s["代码"]: s for s in data}
        _price_map_cache = {s["代码"]: s["最新价"] for s in data}
        _last_fetch_time = time.time()
        _cache_ready.set()

    with _cache_lock:
        now = time.time()
        # 缓存未过期：直接返回
        if _cached_stocks and now - _last_fetch_time < _cache_timeout - _refresh_ahead:
            return _cached_stocks
        # 缓存接近过期：后台刷新，返回旧数据
        if _cached_stocks and now - _last_fetch_time < _cache_timeout:
            if not _refreshing:
                _refreshing = True
                def _bg_refresh():
                    global _fetching, _refreshing
                    with _cache_lock:
                        _fetching = True
                    try:
                        data = _fetch_all_stocks()
                        if data:
                            with _cache_lock:
                                _update_cache(data)
                    finally:
                        with _cache_lock:
                            _fetching = False
                            _refreshing = False
                threading.Thread(target=_bg_refresh, daemon=True).start()
            return _cached_stocks
        # 缓存已过期
        if _fetching:
            # 另一线程正在获取，等待完成
            _cache_ready.clear()
            should_wait = True
        else:
            # 当前线程负责获取
            _fetching = True
            should_wait = False

    if should_wait:
        _cache_ready.wait(timeout=60)
        with _cache_lock:
            return _cached_stocks

    try:
        data = _fetch_all_stocks()
        if data:
            with _cache_lock:
                _update_cache(data)
        with _cache_lock:
            return _cached_stocks
    finally:
        with _cache_lock:
            _fetching = False


def get_stock_by_code(code: str) -> dict | None:
    """O(1)按代码查找股票。"""
    get_spot_data()
    return _stock_index.get(code)


def get_price_map() -> dict[str, float]:
    """返回 code->最新价 映射。"""
    get_spot_data()
    return _price_map_cache


# ─── 52 周高低 ───

def _compute_52week(code: str) -> tuple[float, float]:
    """从日K线计算52周最高/最低，缓存5分钟。"""
    now = time.time()
    if code in _52week_cache:
        ts, high, low = _52week_cache[code]
        if now - ts < _52week_cache_timeout:
            return high, low
    try:
        start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, adjust="qfq")
        if df is None or df.empty:
            return 0.0, 0.0
        high52 = float(df["最高"].max())
        low52 = float(df["最低"].min())
        _52week_cache[code] = (now, high52, low52)
        return high52, low52
    except Exception as e:
        logger.warning("API调用失败: %s", e)
        return 0.0, 0.0


def _enrich_with_52week(stocks: list[dict], limit: int = 500) -> None:
    """对预筛选股票并行计算52周高低，就地修改。"""
    if not stocks:
        return
    codes = [s["代码"] for s in stocks[:limit]]
    futures = {_executor.submit(_compute_52week, code): code for code in codes}
    results: dict[str, tuple[float, float]] = {}
    for future in as_completed(futures):
        code = futures[future]
        try:
            results[code] = future.result()
        except Exception as e:
            logger.warning("52周数据获取失败 %s: %s", code, e)
            results[code] = (0.0, 0.0)
    for s in stocks:
        high, low = results.get(s["代码"], (0.0, 0.0))
        s["52周最高"] = high
        s["52周最低"] = low


# ─── 连涨连跌 ───

def compute_consecutive_days(code: str) -> dict:
    """计算单只股票连涨/连跌天数。"""
    klines = get_stock_history(code, "daily")
    if not klines:
        return {"连涨天数": 0, "连跌天数": 0}
    up_days = 0
    down_days = 0
    for k in reversed(klines):
        change = float(k["close"]) - float(k["open"])
        if change > 0:
            if down_days > 0:
                break
            up_days += 1
        elif change < 0:
            if up_days > 0:
                break
            down_days += 1
        else:
            break
    return {"连涨天数": up_days, "连跌天数": down_days}


# ─── 筛选 ───

def filter_low_price(
    min_price: float = 1.0,
    max_price: float = 5.0,
    min_change_pct: float | None = None,
    max_change_pct: float | None = None,
    min_turnover_rate: float | None = None,
    min_volume: float | None = None,
    min_amount: float | None = None,
    min_pe: float | None = None,
    max_pe: float | None = None,
    min_pb: float | None = None,
    max_pb: float | None = None,
    min_mktcap: float | None = None,
    max_mktcap: float | None = None,
    min_nmc: float | None = None,
    max_nmc: float | None = None,
    min_amplitude: float | None = None,
    max_amplitude: float | None = None,
    min_volume_ratio: float | None = None,
    max_volume_ratio: float | None = None,
    near_52week_high: bool = False,
    near_52week_low: bool = False,
    sector: str | None = None,
    exclude_st: bool = False,
    only_st: bool = False,
    keyword: str | None = None,
    sort_by: str = "涨跌幅",
    sort_order: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """筛选低价股，返回分页结果。单次遍历筛选（量比已在行情数据中）。"""
    stocks = get_spot_data()
    # 检测降级数据（新浪备用源缺失部分字段）
    degraded = bool(stocks and stocks[0].get("_degraded"))

    # 行业筛选：按需获取成分股代码集合
    sector_codes: set[str] | None = None
    sector_failed = False
    if sector:
        sector_codes = _fetch_sector_constituents(sector)
        if sector_codes is None:
            # 全部API失败，无法按行业筛选
            sector_failed = True
            logger.warning("行业成分股API全部失败，跳过行业筛选: %s", sector)
        elif len(sector_codes) == 0:
            sector_failed = True

    # 单次遍历筛选
    # 降级数据（新浪源）缺失字段：换手率、市盈率、市净率、总市值、流通市值、量比
    _degraded_fields = {"换手率", "市盈率-动态", "市净率", "总市值", "流通市值", "量比"} if degraded else set()
    filtered = []
    for s in stocks:
        if not (min_price <= s["最新价"] <= max_price):
            continue
        if min_change_pct is not None and s["涨跌幅"] < min_change_pct:
            continue
        if max_change_pct is not None and s["涨跌幅"] > max_change_pct:
            continue
        if "换手率" not in _degraded_fields and min_turnover_rate is not None and s["换手率"] < min_turnover_rate:
            continue
        if min_volume is not None and s["成交量"] < min_volume:
            continue
        if min_amount is not None and s["成交额"] < min_amount:
            continue
        if "市盈率-动态" not in _degraded_fields:
            if min_pe is not None and s["市盈率-动态"] < min_pe:
                continue
            if max_pe is not None and s["市盈率-动态"] > max_pe:
                continue
        if "市净率" not in _degraded_fields:
            if min_pb is not None and s["市净率"] < min_pb:
                continue
            if max_pb is not None and s["市净率"] > max_pb:
                continue
        if "总市值" not in _degraded_fields:
            if min_mktcap is not None and s["总市值"] < min_mktcap:
                continue
            if max_mktcap is not None and s["总市值"] > max_mktcap:
                continue
        if "流通市值" not in _degraded_fields:
            if min_nmc is not None and s["流通市值"] < min_nmc:
                continue
            if max_nmc is not None and s["流通市值"] > max_nmc:
                continue
        amplitude = ((s["最高"] - s["最低"]) / s["昨收"] * 100) if s["昨收"] > 0 else 0
        if min_amplitude is not None and amplitude < min_amplitude:
            continue
        if max_amplitude is not None and amplitude > max_amplitude:
            continue
        if "量比" not in _degraded_fields:
            if min_volume_ratio is not None and s.get("量比", 0) < min_volume_ratio:
                continue
            if max_volume_ratio is not None and s.get("量比", 0) > max_volume_ratio:
                continue
        if exclude_st and ("ST" in s["名称"] or "st" in s["名称"]):
            continue
        if only_st and "ST" not in s["名称"] and "st" not in s["名称"]:
            continue
        if keyword and keyword not in s["名称"] and keyword not in s["代码"]:
            continue
        if sector and not sector_failed and sector_codes is not None and s["代码"] not in sector_codes:
            continue
        filtered.append(s)

    # 52周筛选：仅当请求时，对预筛选结果并行计算
    need_52week = near_52week_high or near_52week_low
    if need_52week and filtered:
        _enrich_with_52week(filtered)
        if near_52week_high:
            filtered = [s for s in filtered if s.get("52周最高") and s["最新价"] >= s["52周最高"] * 0.95]
        if near_52week_low:
            filtered = [s for s in filtered if s.get("52周最低") and s["最新价"] <= s["52周最低"] * 1.05]

    reverse = sort_order == "desc"
    filtered.sort(key=lambda s: s.get(sort_by, 0), reverse=reverse)

    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = filtered[start:end]
    # 清除内部标记
    for s in page_items:
        s.pop("_degraded", None)

    result = {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": page_items,
    }
    if sector_failed and sector:
        result["warning"] = f"行业「{sector}」成分股数据获取失败，行业筛选未生效"
    if degraded:
        result["warning"] = (result.get("warning", "") + "；" if result.get("warning") else "") + \
            "当前使用备用数据源，换手率/市盈率/市净率/市值/量比筛选暂不可用"
    return result


# ─── 股票详情 ───

def get_stock_detail(code: str) -> dict:
    """获取单只股票详细行情。并发获取买卖盘、52周、行业信息。"""
    s = get_stock_by_code(code)
    if s:
        s = dict(s)  # 浅拷贝避免污染缓存

        f_52week = _executor.submit(_compute_52week, code)
        f_consec = _executor.submit(compute_consecutive_days, code)
        f_info = _executor.submit(_fetch_stock_info, code)

        # 买卖盘
        try:
            bidask_df = ak.stock_bid_ask_em(symbol=code)
            if bidask_df is not None and not bidask_df.empty:
                bidask = dict(zip(bidask_df["item"], bidask_df["value"]))
                s["买一"] = _safe_float(bidask.get("buy_1"), s["最新价"])
                s["卖一"] = _safe_float(bidask.get("sell_1"), s["最新价"])
                if "量比" in bidask:
                    s["量比"] = _safe_float(bidask["量比"])
        except Exception as e:
            logger.warning("API调用失败: %s", e)
            pass

        # 52周
        high52, low52 = f_52week.result()
        s["52周最高"] = high52
        s["52周最低"] = low52

        # 连涨跌
        consec = f_consec.result()
        s["连涨天数"] = consec["连涨天数"]
        s["连跌天数"] = consec["连跌天数"]

        # 行业
        s["行业"] = f_info.result()

        return s

    # 非A股列表中的股票：通过个股信息接口构建
    try:
        f_info = _executor.submit(_fetch_stock_info, code)
        bidask_df = ak.stock_bid_ask_em(symbol=code)
        bidask: dict = {}
        if bidask_df is not None and not bidask_df.empty:
            bidask = dict(zip(bidask_df["item"], bidask_df["value"]))

        info = f_info.result()
        price = _safe_float(bidask.get("最新", 0))
        yesterday = _safe_float(bidask.get("昨收", 0))
        return {
            "代码": code,
            "名称": str(bidask.get("名称", "")),
            "最新价": price,
            "昨收": yesterday,
            "今开": _safe_float(bidask.get("今开", 0)),
            "成交量": int(_safe_float(bidask.get("总手", 0))),
            "最高": _safe_float(bidask.get("最高", 0)),
            "最低": _safe_float(bidask.get("最低", 0)),
            "涨跌额": _safe_float(bidask.get("涨跌", 0)),
            "涨跌幅": _safe_float(bidask.get("涨幅", 0)),
            "买一": _safe_float(bidask.get("buy_1"), price),
            "卖一": _safe_float(bidask.get("sell_1"), price),
            "成交额": _safe_float(bidask.get("金额", 0)),
            "换手率": _safe_float(bidask.get("换手", 0)),
            "市盈率-动态": 0,
            "总市值": 0,
            "流通市值": 0,
            "市净率": 0,
            "量比": _safe_float(bidask.get("量比", 0)),
            "52周最高": 0,
            "52周最低": 0,
            "连涨天数": 0,
            "连跌天数": 0,
            "行业": info,
        }
    except Exception as e:
        logger.warning("API调用失败: %s", e)
        return {}


def _fetch_stock_info(code: str) -> str:
    """获取单只股票的行业信息。"""
    try:
        df = ak.stock_individual_info_em(symbol=code)
        if df is not None and not df.empty:
            info = dict(zip(df["item"], df["value"]))
            return str(info.get("行业", ""))
    except Exception as e:
        logger.warning("API调用失败: %s", e)
        pass
    return ""


# ─── K线历史 ───

_KLINE_PERIOD_MAP = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}


def get_stock_history(code: str, period: str = "daily", start_date: str = "20250101") -> list[dict]:
    """获取单只股票历史K线，带60秒缓存。原生支持日K/周K/月K。"""
    cache_key = f"{code}:{period}"
    now = time.time()
    if cache_key in _kline_cache:
        ts, data = _kline_cache[cache_key]
        if now - ts < _kline_cache_timeout:
            return data

    ak_period = _KLINE_PERIOD_MAP.get(period, "daily")
    try:
        df = ak.stock_zh_a_hist(symbol=code, period=ak_period, start_date=start_date, adjust="qfq")
        if df is None or df.empty:
            return []
        data = _convert_kline_common(df)
        _kline_cache[cache_key] = (now, data)
        return data
    except Exception as e:
        logger.warning("API调用失败: %s", e)
        return []


# ─── 大盘指数 ───

_INDEX_TARGETS = {
    "000001": "上证指数",
    "399001": "深证成指",
    "399006": "创业板指",
}


def get_index_data() -> list[dict]:
    """获取主要大盘指数当前数据，带60秒缓存。"""
    global _index_cache
    now = time.time()
    ts, cached = _index_cache
    if cached and now - ts < _index_cache_timeout:
        return cached

    try:
        f_sh = _executor.submit(ak.stock_zh_index_spot_em, symbol="上证系列指数")
        f_sz = _executor.submit(ak.stock_zh_index_spot_em, symbol="深证系列指数")

        df_sh = f_sh.result()
        df_sz = f_sz.result()

        result = []
        for code, name in _INDEX_TARGETS.items():
            df_src = df_sh if code.startswith("000") else df_sz
            if df_src is None:
                continue
            match = df_src[df_src["代码"] == code]
            if match.empty:
                continue
            row = match.iloc[0]
            current = _safe_float(row.get("最新价", 0))
            yesterday = _safe_float(row.get("昨收", 0))
            result.append({
                "code": f"{'sh' if code.startswith('000') else 'sz'}{code}",
                "name": name,
                "current": current,
                "yesterday": yesterday,
                "change_pct": _safe_float(row.get("涨跌幅", 0)),
            })

        if result:
            _index_cache = (now, result)
        return result
    except Exception as e:
        logger.warning("API调用失败: %s", e)
        return cached or []


# ─── 行业板块 ───

def _fetch_sector_list_em() -> list[dict] | None:
    """通过东方财富获取行业板块列表。失败返回 None。"""
    try:
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return None
        sectors = []
        seen = set()
        for _, row in df.iterrows():
            name = str(row.get("板块名称", ""))
            if name in seen:
                continue
            seen.add(name)
            sectors.append({
                "name": name,
                "code": str(row.get("板块代码", "")),
            })
        return sectors
    except Exception as e:
        logger.warning("东方财富行业API失败: %s", e)
        return None


def _fetch_sector_list_sina() -> list[dict] | None:
    """通过新浪获取行业板块列表（备用）。失败返回 None。"""
    try:
        df = ak.stock_sector_spot()
        if df is None or df.empty:
            return None
        sectors = []
        for _, row in df.iterrows():
            sectors.append({
                "name": str(row.get("板块", "")),
                "code": str(row.get("label", "")),
            })
        return sectors
    except Exception as e:
        logger.warning("新浪行业API失败: %s", e)
        return None


def _fetch_sector_list() -> list[dict]:
    """获取行业板块列表，缓存5分钟。东方财富优先，失败降级新浪。"""
    global _sector_list_cache, _sector_list_cache_time
    now = time.time()
    if _sector_list_cache and now - _sector_list_cache_time < 300:
        return _sector_list_cache
    sectors = _fetch_sector_list_em() or _fetch_sector_list_sina() or []
    if sectors:
        _sector_list_cache = sectors
        _sector_list_cache_time = now
    return sectors


def _fetch_sector_constituents_em(sector_name: str) -> set[str] | None:
    """通过东方财富获取行业成分股。失败返回 None。"""
    try:
        df = ak.stock_board_industry_cons_em(symbol=sector_name)
        if df is None or df.empty:
            return None
        return set(df["代码"].astype(str).tolist())
    except Exception as e:
        logger.warning("东方财富成分股API失败: %s", e)
        return None


def _fetch_sector_constituents_sina(sector_label: str) -> set[str] | None:
    """通过新浪获取行业成分股（备用）。失败返回 None。"""
    try:
        df = ak.stock_sector_detail(sector=sector_label)
        if df is None or df.empty:
            return None
        return set(df["code"].astype(str).tolist())
    except Exception as e:
        logger.warning("新浪成分股API失败: %s", e)
        return None


def _fetch_sector_constituents(sector_name: str) -> set[str]:
    """获取某个行业的成分股代码集合，缓存5分钟。东方财富优先，失败降级新浪。"""
    now = time.time()
    if sector_name in _sector_constituent_cache:
        ts, codes = _sector_constituent_cache[sector_name]
        if now - ts < _sector_constituent_timeout:
            return codes
    codes = _fetch_sector_constituents_em(sector_name)
    if codes is None:
        # 东方财富失败，通过新浪备用接口查找
        # 先在缓存中找行业对应的label/code
        sector_label = ""
        for s in _sector_list_cache:
            if s["name"] == sector_name:
                sector_label = s["code"]
                break
        if sector_label:
            codes = _fetch_sector_constituents_sina(sector_label)
    if codes is not None:
        _sector_constituent_cache[sector_name] = (now, codes)
        return codes
    return None


def get_sector_list() -> list[dict]:
    """返回行业板块列表。"""
    return _fetch_sector_list()


def _build_sector_overview_em() -> list[dict] | None:
    """通过东方财富构建板块概览。失败返回 None。"""
    try:
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return None
        result = []
        get_spot_data()
        name_to_code = {s["名称"]: s["代码"] for s in _cached_stocks}
        for _, row in df.iterrows():
            top_name = str(row.get("领涨股票", ""))
            top_change = _safe_float(row.get("领涨股票-涨跌幅", 0))
            top_code = name_to_code.get(top_name, "")
            result.append({
                "name": str(row.get("板块名称", "")),
                "avg_change_pct": round(_safe_float(row.get("涨跌幅", 0)), 2),
                "up_count": int(_safe_float(row.get("上涨家数", 0))),
                "down_count": int(_safe_float(row.get("下跌家数", 0))),
                "amount": 0,
                "new_high_count": 0,
                "new_low_count": 0,
                "top_stocks": [{"代码": top_code, "名称": top_name, "涨跌幅": round(top_change, 2)}],
            })
        return result
    except Exception as e:
        logger.warning("东方财富板块概览失败: %s", e)
        return None


def _fetch_sector_constituents_sina_batch(labels: list[str]) -> dict[str, list[dict]]:
    """并发获取多个新浪行业的成分股。返回 {label: [{code, name, changepercent}, ...]}。限制并发为10。"""
    result = {}
    lock = threading.Lock()
    sem = threading.Semaphore(10)

    def _fetch_one(label: str):
        with sem:
            try:
                df = ak.stock_sector_detail(sector=label)
                if df is not None and not df.empty:
                    stocks = []
                    for _, row in df.iterrows():
                        stocks.append({
                            "code": str(row.get("code", "")),
                            "name": str(row.get("name", "")),
                            "changepercent": _safe_float(row.get("changepercent", 0)),
                        })
                    with lock:
                        result[label] = stocks
            except Exception as e:
                logger.warning("新浪行业成分股获取失败(%s): %s", label, e)

    futures = [_executor.submit(_fetch_one, lb) for lb in labels]
    for f in futures:
        f.result(timeout=30)
    return result


def _build_sector_overview_sina() -> list[dict] | None:
    """通过新浪构建板块概览（备用）。并发获取成分股统计涨跌家数。"""
    try:
        df = ak.stock_sector_spot()
        if df is None or df.empty:
            return None
        get_spot_data()
        name_to_code = {s["名称"]: s["代码"] for s in _cached_stocks}
        price_map = {s["代码"]: s["最新价"] for s in _cached_stocks}

        # 并发获取所有行业成分股，用于统计涨跌家数
        labels = df["label"].tolist()
        label_constituents = _fetch_sector_constituents_sina_batch(labels)

        result = []
        for _, row in df.iterrows():
            label = str(row.get("label", ""))

            # 从成分股统计涨跌家数 + 找领涨股
            up_count, down_count = 0, 0
            top_code, top_name, top_change = "", "", 0.0
            stocks_info = label_constituents.get(label)
            if stocks_info:
                for st in stocks_info:
                    chg = st["changepercent"]
                    if chg > 0:
                        up_count += 1
                    elif chg < 0:
                        down_count += 1
                    if chg > top_change:
                        top_change = chg
                        top_code = st["code"]
                        top_name = st["name"]
            else:
                # 成分股获取失败，用板块列表的领涨股
                top_name = str(row.get("股票名称", ""))
                top_change = _safe_float(row.get("个股-涨跌幅", 0))
                top_code = name_to_code.get(top_name, "")

            result.append({
                "name": str(row.get("板块", "")),
                "avg_change_pct": round(_safe_float(row.get("涨跌幅", 0)), 2),
                "up_count": up_count,
                "down_count": down_count,
                "amount": _safe_float(row.get("总成交额", 0)),
                "new_high_count": 0,
                "new_low_count": 0,
                "top_stocks": [{"代码": top_code, "名称": top_name, "涨跌幅": round(top_change, 2)}],
            })
        return result
    except Exception as e:
        logger.warning("新浪板块概览失败: %s", e)
        return None


def _enrich_sector_52week(sectors: list[dict]) -> None:
    """利用已有52周缓存，为板块概览补充创新高/新低数量。"""
    get_spot_data()
    with _cache_lock:
        price_map = dict(_price_map_cache)
    for sec in sectors:
        name = sec["name"]
        # 从成分股缓存获取该板块的股票代码
        cached = _sector_constituent_cache.get(name)
        if not cached:
            continue
        _, codes = cached
        if not codes:
            continue
        high_count, low_count = 0, 0
        for code in codes:
            if code not in _52week_cache:
                continue
            _, w_high, w_low = _52week_cache[code]
            if w_high <= 0 or w_low <= 0:
                continue
            price = price_map.get(code, 0)
            if price <= 0:
                continue
            if price >= w_high * 0.95:
                high_count += 1
            if price <= w_low * 1.05:
                low_count += 1
        sec["new_high_count"] = high_count
        sec["new_low_count"] = low_count


def get_sector_overview() -> list[dict]:
    """返回各行业板块概览，带5分钟缓存。东方财富优先，失败降级新浪。"""
    global _sector_overview_cache, _sector_overview_cache_time
    now = time.time()
    if _sector_overview_cache and now - _sector_overview_cache_time < 300:
        return _sector_overview_cache

    result = _build_sector_overview_em() or _build_sector_overview_sina()
    if result:
        result.sort(key=lambda x: x["avg_change_pct"], reverse=True)
        _enrich_sector_52week(result)
        _sector_overview_cache = result
        _sector_overview_cache_time = now
        return result
    return _sector_overview_cache or []


# ─── ETF 行情 ───

_etf_last_fetch_time = 0.0
_etf_cached: list[dict] = []
_etf_index: dict[str, dict] = {}
_etf_price_map: dict[str, float] = {}
_etf_fetching = False
_etf_refreshing = False
_etf_ready = threading.Event()


def _convert_etf_spot_em(df) -> list[dict]:
    """将 ak.fund_etf_spot_em() 的 DataFrame 转为统一中文 key 格式。"""
    result = []
    for _, row in df.iterrows():
        price = _safe_float(row.get("最新价", 0))
        result.append({
            "代码": str(row.get("代码", "")),
            "名称": str(row.get("名称", "")),
            "最新价": price,
            "涨跌额": _safe_float(row.get("涨跌额", 0)),
            "涨跌幅": _safe_float(row.get("涨跌幅", 0)),
            "今开": _safe_float(row.get("今开", 0)),
            "最高": _safe_float(row.get("最高", 0)),
            "最低": _safe_float(row.get("最低", 0)),
            "昨收": _safe_float(row.get("昨收", 0)),
            "买一": _safe_float(row.get("买入", price)),
            "卖一": _safe_float(row.get("卖出", price)),
            "成交量": int(_safe_float(row.get("成交量", 0))),
            "成交额": _safe_float(row.get("成交额", 0)),
            "换手率": _safe_float(row.get("换手率", 0)),
            "量比": _safe_float(row.get("量比", 0)),
            "_type": "etf",
        })
    return result


def _convert_etf_spot_ths(df) -> list[dict]:
    """将 ak.fund_etf_spot_ths() 的 DataFrame 转为统一中文 key 格式。
    同花顺源缺失成交量/成交额/买卖盘等实时交易字段，标记 _degraded。"""
    result = []
    for _, row in df.iterrows():
        nav = _safe_float(row.get("当前-单位净值", 0))
        prev_nav = _safe_float(row.get("前一日-单位净值", 0))
        result.append({
            "代码": str(row.get("基金代码", "")),
            "名称": str(row.get("基金名称", "")),
            "最新价": nav,
            "涨跌额": _safe_float(row.get("增长值", 0)),
            "涨跌幅": _safe_float(row.get("增长率", 0)),
            "今开": nav,
            "最高": nav,
            "最低": nav,
            "昨收": prev_nav,
            "买一": nav,
            "卖一": nav,
            "成交量": 0,
            "成交额": 0,
            "换手率": 0,
            "量比": 0,
            "_type": "etf",
            "_degraded": True,
        })
    return result


def _fetch_etf_sina_batch(codes: list[str]) -> list[dict]:
    """通过新浪行情接口批量获取ETF实时行情。分批80只，并行请求，总耗时<1秒。"""
    import requests as _req
    from concurrent.futures import ThreadPoolExecutor, as_completed

    chunk_size = 80
    chunks = [codes[i:i + chunk_size] for i in range(0, len(codes), chunk_size)]

    def _fetch_chunk(batch: list[str]) -> list[dict]:
        try:
            url = f"https://hq.sinajs.cn/list={','.join(batch)}"
            r = _req.get(url, headers={
                "Referer": "https://finance.sina.com.cn/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }, timeout=10)
            r.encoding = "gbk"
            chunk_result = []
            for line in r.text.strip().split("\n"):
                if '="' not in line:
                    continue
                raw_code = line.split("=")[0].split("_")[-1]
                code = raw_code[2:] if raw_code.startswith(("sh", "sz")) else raw_code
                val = line.split('"')[1] if '"' in line else ""
                if not val:
                    continue
                fields = val.split(",")
                if len(fields) < 32 or not fields[3]:
                    continue
                price = _safe_float(fields[3])
                yesterday = _safe_float(fields[2])
                chunk_result.append({
                    "代码": code,
                    "名称": fields[0],
                    "最新价": price,
                    "涨跌额": round(price - yesterday, 4) if yesterday else 0,
                    "涨跌幅": round((price - yesterday) / yesterday * 100, 2) if yesterday else 0,
                    "今开": _safe_float(fields[1]),
                    "最高": _safe_float(fields[4]),
                    "最低": _safe_float(fields[5]),
                    "昨收": yesterday,
                    "买一": _safe_float(fields[6]) or price,
                    "卖一": _safe_float(fields[7]) or price,
                    "成交量": int(_safe_float(fields[8])),
                    "成交额": _safe_float(fields[9]),
                    "换手率": 0,
                    "量比": 0,
                    "_type": "etf",
                })
            return chunk_result
        except Exception as e:
            logger.warning("新浪ETF行情批次失败: %s", e)
            return []

    result = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_fetch_chunk, c) for c in chunks]
        for f in as_completed(futures):
            result.extend(f.result())
    return result


_etf_code_list: list[str] = []
_etf_code_list_time: float = 0
_em_fail_time: float = 0  # 东方财富失败时间戳，5分钟内跳过


def _fetch_etf_codes_from_ths() -> list[str]:
    """从同花顺获取ETF代码列表，带30分钟缓存。"""
    global _etf_code_list, _etf_code_list_time
    now = time.time()
    if _etf_code_list and now - _etf_code_list_time < 1800:
        return _etf_code_list
    try:
        df = ak.fund_etf_spot_ths()
        if df is not None and not df.empty:
            codes = []
            for code in df["基金代码"].astype(str):
                prefix = "sh" if code.startswith("5") else "sz"
                codes.append(f"{prefix}{code}")
            _etf_code_list = codes
            _etf_code_list_time = now
            return codes
    except Exception as e:
        logger.warning("THS ETF代码列表获取失败: %s", e)
    return _etf_code_list  # 返回旧缓存


def _fetch_all_etf() -> list[dict]:
    """获取全市场ETF实时行情。新浪批量优先，东方财富备用（失败5分钟跳过），THS净值兜底。"""
    global _etf_cached, _em_fail_time
    now = time.time()
    # 新浪批量实时行情（优先，快速稳定）
    try:
        codes = _fetch_etf_codes_from_ths()
        if codes:
            data = _fetch_etf_sina_batch(codes)
            if data:
                logger.info("使用新浪ETF实时行情接口，%d条", len(data))
                return data
    except Exception as e:
        logger.warning("新浪ETF行情接口失败: %s", e)
    # 东方财富（失败后5分钟内跳过）
    if now - _em_fail_time > 300:
        try:
            df = ak.fund_etf_spot_em()
            if df is not None and not df.empty:
                _em_fail_time = 0
                return _convert_etf_spot_em(df)
        except Exception as e:
            _em_fail_time = now
            logger.warning("东方财富ETF行情API失败: %s", e)
    # THS净值兜底
    try:
        df = ak.fund_etf_spot_ths()
        if df is not None and not df.empty:
            logger.info("使用同花顺ETF净值兜底接口，%d条", len(df))
            return _convert_etf_spot_ths(df)
    except Exception as e:
        logger.warning("同花顺ETF行情API失败: %s", e)
    logger.warning("所有ETF行情API失败，使用旧缓存(%d条)", len(_etf_cached))
    return _etf_cached


def get_etf_spot_data() -> list[dict]:
    """获取全市场ETF实时行情，带60秒缓存。"""
    global _etf_cached, _etf_last_fetch_time, _etf_fetching, _etf_refreshing
    global _etf_index, _etf_price_map

    def _update(data):
        global _etf_cached, _etf_index, _etf_price_map, _etf_last_fetch_time
        _etf_cached = data
        _etf_index = {s["代码"]: s for s in data}
        _etf_price_map = {s["代码"]: s["最新价"] for s in data}
        _etf_last_fetch_time = time.time()
        _etf_ready.set()

    with _cache_lock:
        now = time.time()
        if _etf_cached and now - _etf_last_fetch_time < _cache_timeout - _refresh_ahead:
            return _etf_cached
        if _etf_cached and now - _etf_last_fetch_time < _cache_timeout:
            if not _etf_refreshing:
                _etf_refreshing = True
                def _bg():
                    global _etf_fetching, _etf_refreshing
                    with _cache_lock:
                        _etf_fetching = True
                    try:
                        data = _fetch_all_etf()
                        if data:
                            with _cache_lock:
                                _update(data)
                    finally:
                        with _cache_lock:
                            _etf_fetching = False
                            _etf_refreshing = False
                threading.Thread(target=_bg, daemon=True).start()
            return _etf_cached
        if _etf_fetching:
            _etf_ready.clear()
            should_wait = True
        else:
            _etf_fetching = True
            should_wait = False

    if should_wait:
        _etf_ready.wait(timeout=60)
        with _cache_lock:
            return _etf_cached

    try:
        data = _fetch_all_etf()
        if data:
            with _cache_lock:
                _update(data)
        with _cache_lock:
            return _etf_cached
    finally:
        with _cache_lock:
            _etf_fetching = False


def get_etf_by_code(code: str) -> dict | None:
    """O(1)按代码查找ETF。"""
    get_etf_spot_data()
    return _etf_index.get(code)


def get_etf_price_map() -> dict[str, float]:
    """返回 ETF code->最新价 映射。"""
    get_etf_spot_data()
    return _etf_price_map


def filter_etf(
    min_price: float = 0,
    max_price: float = 999,
    min_change_pct: float | None = None,
    max_change_pct: float | None = None,
    min_amount: float | None = None,
    etf_type: str | None = None,
    keyword: str | None = None,
    sort_by: str = "涨跌幅",
    sort_order: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """筛选ETF，返回分页结果。"""
    stocks = get_etf_spot_data()
    degraded = any(s.get("_degraded") for s in stocks)

    # ETF类型关键词映射
    type_keywords: dict[str, list[str]] = {
        "指数": ["沪深300", "中证500", "上证50", "创业板", "科创", "中证1000", "国证", "指数"],
        "债券": ["国债", "地方债", "信用债", "可转债", "债"],
        "商品": ["黄金", "原油", "商品", "白银", "豆粕", "有色"],
        "货币": ["货币", "理财"],
        "跨境": ["纳斯达克", "标普", "恒生", "日经", "德国", "法国", "美国", "QDII", "港股通"],
    }

    filtered = []
    for s in stocks:
        if not (min_price <= s["最新价"] <= max_price):
            continue
        if min_change_pct is not None and s["涨跌幅"] < min_change_pct:
            continue
        if max_change_pct is not None and s["涨跌幅"] > max_change_pct:
            continue
        if min_amount is not None and not degraded and s["成交额"] < min_amount:
            continue
        if etf_type and etf_type in type_keywords:
            keywords = type_keywords[etf_type]
            if not any(kw in s["名称"] for kw in keywords):
                continue
        if keyword and keyword not in s["名称"] and keyword not in s["代码"]:
            continue
        filtered.append(s)

    reverse = sort_order == "desc"
    filtered.sort(key=lambda s: s.get(sort_by, 0), reverse=reverse)

    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = filtered[start:end]
    for s in page_items:
        s.pop("_type", None)
        s.pop("_degraded", None)

    result: dict = {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": page_items,
    }
    if degraded:
        result["warning"] = "备用数据源(同花顺净值)，成交额/换手率/量比不可用"
    return result


# ─── ETF K线 + 详情 ───

_etf_kline_cache: BoundedCache = _register_cache(BoundedCache(512), 60)
_etf_kline_cache_timeout = 60
_etf_52week_cache: BoundedCache = _register_cache(BoundedCache(512), 300)
_etf_52week_cache_timeout = 300


def _compute_etf_consecutive(code: str) -> dict:
    """计算ETF连涨/连跌天数。"""
    klines = get_etf_history(code, "daily")
    if not klines:
        return {"连涨天数": 0, "连跌天数": 0}
    up_days = 0
    down_days = 0
    for k in reversed(klines):
        change = float(k["close"]) - float(k["open"])
        if change > 0:
            if down_days > 0:
                break
            up_days += 1
        elif change < 0:
            if up_days > 0:
                break
            down_days += 1
        else:
            break
    return {"连涨天数": up_days, "连跌天数": down_days}


def _compute_etf_52week(code: str) -> tuple[float, float]:
    """从ETF日K线计算52周最高/最低，缓存5分钟。"""
    now = time.time()
    if code in _etf_52week_cache:
        ts, high, low = _etf_52week_cache[code]
        if now - ts < _etf_52week_cache_timeout:
            return high, low
    try:
        klines = get_etf_history(code, "daily")
        if not klines:
            return 0.0, 0.0
        highs = [float(k["high"]) for k in klines if k.get("high")]
        lows = [float(k["low"]) for k in klines if k.get("low")]
        if not highs or not lows:
            return 0.0, 0.0
        high52 = max(highs)
        low52 = min(lows)
        _etf_52week_cache[code] = (now, high52, low52)
        return high52, low52
    except Exception as e:
        logger.warning("ETF 52周计算失败(%s): %s", code, e)
        return 0.0, 0.0


def _convert_kline_common(df, col_map: dict | None = None) -> list[dict]:
    """通用K线DataFrame转换。col_map 可覆盖列名映射，默认适配东方财富格式。"""
    m = col_map or {"日期": "day", "开盘": "open", "最高": "high", "最低": "low", "收盘": "close", "成交量": "volume"}
    result = []
    for _, row in df.iterrows():
        item = {}
        for src, dst in m.items():
            item[dst] = str(row.get(src, 0))
        result.append(item)
    return result


def get_etf_history(code: str, period: str = "daily") -> list[dict]:
    """获取ETF K线数据，缓存60秒。东方财富优先，新浪备用。"""
    now = time.time()
    key = f"{code}_{period}"
    if key in _etf_kline_cache:
        ts, data = _etf_kline_cache[key]
        if now - ts < _etf_kline_cache_timeout:
            return data
    em_col_map = {"日期": "day", "开盘": "open", "最高": "high", "最低": "low", "收盘": "close", "成交量": "volume"}
    sina_col_map = {"date": "day", "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}
    # 东方财富
    try:
        start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        df = ak.fund_etf_hist_em(symbol=code, period=period, start_date=start, adjust="qfq")
        if df is not None and not df.empty:
            data = _convert_kline_common(df, em_col_map)
            _etf_kline_cache[key] = (now, data)
            return data
    except Exception as e:
        logger.warning("东方财富ETF K线失败(%s): %s", code, e)
    # 新浪备用（仅日线）
    try:
        prefix = _sz_sh_prefix(code)
        df = ak.fund_etf_hist_sina(symbol=f"{prefix}{code}")
        if df is not None and not df.empty:
            data = _convert_kline_common(df, sina_col_map)
            _etf_kline_cache[key] = (now, data)
            return data
    except Exception as e:
        logger.warning("新浪ETF K线失败(%s): %s", code, e)
    return []


def get_etf_minute_history(code: str, period: str = "1") -> list[dict]:
    """获取ETF分钟K线，缓存60秒。"""
    now = time.time()
    key = f"{code}_min_{period}"
    if key in _etf_kline_cache:
        ts, data = _etf_kline_cache[key]
        if now - ts < _etf_kline_cache_timeout:
            return data
    col_map = {"时间": "day", "开盘": "open", "最高": "high", "最低": "low", "收盘": "close", "成交量": "volume"}
    try:
        df = ak.fund_etf_hist_min_em(symbol=code, period=period, adjust="")
        if df is not None and not df.empty:
            data = _convert_kline_common(df, col_map)
            _etf_kline_cache[key] = (now, data)
            return data
    except Exception as e:
        logger.warning("ETF分钟K线获取失败(%s): %s", code, e)
    return []


def get_etf_detail(code: str) -> dict:
    """获取ETF详情。含行情+52周+基金类型。"""
    s = get_etf_by_code(code)
    if not s:
        return {"error": "ETF代码不存在"}
    s = dict(s)

    f_52week = _executor.submit(_compute_etf_52week, code)
    f_consec = _executor.submit(_compute_etf_consecutive, code)
    try:
        high52, low52 = f_52week.result(timeout=15)
        s["52周最高"] = high52
        s["52周最低"] = low52
    except Exception:
        s["52周最高"] = 0.0
        s["52周最低"] = 0.0
    try:
        consec = f_consec.result(timeout=15)
        s["连涨天数"] = consec.get("连涨天数", 0)
        s["连跌天数"] = consec.get("连跌天数", 0)
    except Exception:
        s["连涨天数"] = 0
        s["连跌天数"] = 0

    # 推断基金类型
    type_keywords = {
        "指数": ["沪深300", "中证500", "上证50", "创业板", "科创", "中证1000", "国证", "指数"],
        "债券": ["国债", "地方债", "信用债", "可转债", "债"],
        "商品": ["黄金", "原油", "商品", "白银", "豆粕", "有色"],
        "货币": ["货币", "理财"],
        "跨境": ["纳斯达克", "标普", "恒生", "日经", "德国", "法国", "美国", "QDII", "港股通"],
    }
    s["基金类型"] = "其他"
    for t, kws in type_keywords.items():
        if any(kw in s["名称"] for kw in kws):
            s["基金类型"] = t
            break

    s.pop("_type", None)
    return s


# ─── ETF专属数据 ───

_etf_fund_flow_cache: BoundedCache = _register_cache(BoundedCache(256), 300)
_etf_fund_flow_cache_timeout = 300

_etf_nav_cache: BoundedCache = _register_cache(BoundedCache(256), 300)
_etf_nav_cache_timeout = 300

_etf_holdings_cache: BoundedCache = _register_cache(BoundedCache(256), 1800)
_etf_holdings_cache_timeout = 1800

_etf_allocation_cache: BoundedCache = _register_cache(BoundedCache(256), 1800)
_etf_allocation_cache_timeout = 1800


def get_etf_fund_flow(code: str) -> list[dict]:
    """获取ETF资金流向（每日），缓存5分钟。复用A股接口，ETF通用。"""
    cache_key = f"etf_fundflow:{code}"
    now = time.time()
    if cache_key in _etf_fund_flow_cache:
        ts, data = _etf_fund_flow_cache[cache_key]
        if now - ts < _etf_fund_flow_cache_timeout:
            return data
    try:
        market = _sz_sh_prefix(code)
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        if df is None or df.empty:
            return []
        result = []
        for _, row in df.iterrows():
            result.append({
                "date": str(row.iloc[0]),
                "close": _safe_float(row.iloc[1]),
                "change_pct": _safe_float(row.iloc[2]),
                "main_net": _safe_float(row.iloc[3]),
                "main_pct": _safe_float(row.iloc[4]),
                "huge_net": _safe_float(row.iloc[5]),
                "huge_pct": _safe_float(row.iloc[6]),
                "big_net": _safe_float(row.iloc[7]),
                "big_pct": _safe_float(row.iloc[8]),
                "mid_net": _safe_float(row.iloc[9]),
                "mid_pct": _safe_float(row.iloc[10]),
                "small_net": _safe_float(row.iloc[11]),
                "small_pct": _safe_float(row.iloc[12]),
            })
        result.reverse()
        _etf_fund_flow_cache[cache_key] = (now, result)
        return result
    except Exception as e:
        logger.warning("ETF资金流向获取失败(%s): %s", code, e)
        return []


def get_etf_nav(code: str) -> list[dict]:
    """获取ETF历史净值，缓存5分钟。"""
    cache_key = f"etf_nav:{code}"
    now = time.time()
    if cache_key in _etf_nav_cache:
        ts, data = _etf_nav_cache[cache_key]
        if now - ts < _etf_nav_cache_timeout:
            return data
    try:
        start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        df = ak.fund_etf_fund_info_em(fund=code, start_date=start, end_date=end)
        if df is None or df.empty:
            return []
        result = []
        for _, row in df.iterrows():
            result.append({
                "date": str(row["净值日期"]),
                "nav": _safe_float(row["单位净值"]),
                "acc_nav": _safe_float(row["累计净值"]),
                "growth": _safe_float(row["日增长率"]),
            })
        _etf_nav_cache[cache_key] = (now, result)
        return result
    except Exception as e:
        logger.warning("ETF净值获取失败(%s): %s", code, e)
        return []


def get_etf_holdings(code: str) -> list[dict]:
    """获取ETF十大持仓，缓存30分钟。"""
    cache_key = f"etf_holdings:{code}"
    now = time.time()
    if cache_key in _etf_holdings_cache:
        ts, data = _etf_holdings_cache[cache_key]
        if now - ts < _etf_holdings_cache_timeout:
            return data
    try:
        year = str(datetime.now().year)
        df = ak.fund_portfolio_hold_em(symbol=code, date=year)
        if df is None or df.empty:
            return []
        # 取最新季度前10
        latest_quarter = df["季度"].iloc[0] if "季度" in df.columns else None
        if latest_quarter:
            df = df[df["季度"] == latest_quarter].head(10)
        result = []
        for _, row in df.iterrows():
            result.append({
                "code": str(row.get("股票代码", "")),
                "name": str(row.get("股票名称", "")),
                "ratio": _safe_float(row.get("占净值比例", 0)),
                "shares": _safe_float(row.get("持股数", 0)),
                "market_value": _safe_float(row.get("持仓市值", 0)),
            })
        _etf_holdings_cache[cache_key] = (now, result)
        return result
    except Exception as e:
        logger.warning("ETF持仓获取失败(%s): %s", code, e)
        return []


def get_etf_allocation(code: str) -> dict:
    """获取ETF资产配置+行业配置，缓存30分钟。"""
    cache_key = f"etf_allocation:{code}"
    now = time.time()
    if cache_key in _etf_allocation_cache:
        ts, data = _etf_allocation_cache[cache_key]
        if now - ts < _etf_allocation_cache_timeout:
            return data
    result: dict = {"asset": [], "industry": []}
    # 资产配置
    try:
        df = ak.fund_individual_detail_hold_xq(symbol=code)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                result["asset"].append({
                    "type": str(row.get("资产类型", "")),
                    "ratio": _safe_float(row.get("仓位占比", 0)),
                })
    except Exception as e:
        logger.warning("ETF资产配置获取失败(%s): %s", code, e)
    # 行业配置
    try:
        year = str(datetime.now().year)
        df2 = ak.fund_portfolio_industry_allocation_em(symbol=code, date=year)
        if df2 is not None and not df2.empty:
            for _, row in df2.head(10).iterrows():
                result["industry"].append({
                    "name": str(row.get("行业类别", "")),
                    "ratio": _safe_float(row.get("占净值比例", 0)),
                })
    except Exception as e:
        logger.warning("ETF行业配置获取失败(%s): %s", code, e)
    if result["asset"] or result["industry"]:
        _etf_allocation_cache[cache_key] = (now, result)
    return result


# ─── 财务数据 ───

_financial_cache: BoundedCache = _register_cache(BoundedCache(256), 300)
_financial_cache_timeout = 300

_statement_cache: BoundedCache = _register_cache(BoundedCache(256), 300)
_statement_cache_timeout = 300

_VALID_STATEMENTS = {"利润表", "资产负债表", "现金流量表"}


def _sina_prefix(code: str) -> str:
    if code.startswith("6") or code.startswith("9"):
        return "sh"
    if code.startswith("4") or code.startswith("8"):
        return "bj"
    return "sz"


def get_financial_abstract(code: str) -> list[dict]:
    """获取个股财务摘要（同花顺），返回最近8期，缓存5分钟。"""
    cache_key = f"abstract:{code}"
    now = time.time()
    if cache_key in _financial_cache:
        ts, data = _financial_cache[cache_key]
        if now - ts < _financial_cache_timeout:
            return data
    try:
        df = ak.stock_financial_abstract_ths(symbol=code, indicator="按报告期")
        if df is None or df.empty:
            return []
        df = df.tail(8)
        result = []
        for _, row in df.iterrows():
            item = {}
            for col in df.columns:
                val = row[col]
                if val is None or (isinstance(val, float) and np.isnan(val)):
                    item[col] = ""
                else:
                    item[col] = str(val)
            result.append(item)
        result.reverse()
        _financial_cache[cache_key] = (now, result)
        return result
    except Exception as e:
        logger.warning("API调用失败: %s", e)
        return []


def get_financial_statement(code: str, statement_type: str) -> list[dict]:
    """获取三大报表原始数据，缓存5分钟。statement_type: 利润表/资产负债表/现金流量表"""
    if statement_type not in _VALID_STATEMENTS:
        return []
    cache_key = f"stmt:{code}:{statement_type}"
    now = time.time()
    if cache_key in _statement_cache:
        ts, data = _statement_cache[cache_key]
        if now - ts < _statement_cache_timeout:
            return data
    try:
        prefix = _sina_prefix(code)
        df = ak.stock_financial_report_sina(stock=f"{prefix}{code}", symbol=statement_type)
        if df is None or df.empty:
            return []
        df = df.head(8)
        result = []
        for _, row in df.iterrows():
            item = {}
            for col in df.columns:
                val = row[col]
                if val is None or (isinstance(val, float) and np.isnan(val)):
                    item[col] = ""
                else:
                    item[col] = str(val)
            result.append(item)
        _statement_cache[cache_key] = (now, result)
        return result
    except Exception as e:
        logger.warning("API调用失败: %s", e)
        return []


# ─── 个股资讯 ───

_news_cache: BoundedCache = _register_cache(BoundedCache(256), 300)
_news_cache_timeout = 300


def get_stock_news(code: str) -> list[dict]:
    """获取个股新闻资讯（东方财富），缓存5分钟。"""
    cache_key = f"news:{code}"
    now = time.time()
    if cache_key in _news_cache:
        ts, data = _news_cache[cache_key]
        if now - ts < _news_cache_timeout:
            return data
    try:
        stock = _stock_index.get(code)
        name = stock["名称"] if stock else code
        import json
        import requests

        param = json.dumps({
            "uid": "",
            "keyword": name,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": 15,
                    "preTag": "",
                    "postTag": "",
                }
            },
        }, ensure_ascii=False)

        r = requests.get(
            "https://search-api-web.eastmoney.com/search/jsonp",
            params={"cb": "jQuery", "param": param},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://so.eastmoney.com/",
            },
            timeout=10,
        )
        text = r.text
        # JSONP 包裹: jQuery({...})
        json_str = text[text.index("(") + 1 : text.rindex(")")]
        data = json.loads(json_str)

        articles = data.get("result", {}).get("cmsArticleWebOld", [])
        if isinstance(articles, dict):
            articles = articles.get("list", [])
        result = []
        for art in articles:
            title = art.get("title", "").replace("<em>", "").replace("</em>", "")
            url = art.get("url", "")
            source = art.get("mediaName", "")
            time_str = art.get("date", "")
            if title:
                result.append({
                    "title": title,
                    "url": url,
                    "source": source,
                    "time": time_str,
                })
        _news_cache[cache_key] = (now, result)
        return result
    except Exception as e:
        logger.warning("API调用失败: %s", e)
        return []


# ─── 分时图 ───

_intraday_cache: BoundedCache = _register_cache(BoundedCache(256), 60)
_intraday_cache_timeout = 60


def get_intraday(code: str) -> list[dict]:
    """获取个股当日分时成交数据，缓存60秒。"""
    cache_key = f"intraday:{code}"
    now = time.time()
    if cache_key in _intraday_cache:
        ts, data = _intraday_cache[cache_key]
        if now - ts < _intraday_cache_timeout:
            return data
    try:
        df = ak.stock_intraday_em(symbol=code)
        if df is None or df.empty:
            return []
        result = []
        for _, row in df.iterrows():
            result.append({
                "time": str(row.iloc[0]),
                "price": _safe_float(row.iloc[1]),
                "volume": int(row.iloc[2]) if row.iloc[2] else 0,
                "nature": str(row.iloc[3]),
            })
        _intraday_cache[cache_key] = (now, result)
        return result
    except Exception as e:
        logger.warning("API调用失败: %s", e)
        return []


# ─── 五档盘口 ───

_bidask_cache: BoundedCache = _register_cache(BoundedCache(256), 10)
_bidask_cache_timeout = 10


def get_bid_ask(code: str) -> dict:
    """获取个股五档盘口数据，缓存10秒。"""
    cache_key = f"bidask:{code}"
    now = time.time()
    if cache_key in _bidask_cache:
        ts, data = _bidask_cache[cache_key]
        if now - ts < _bidask_cache_timeout:
            return data
    try:
        df = ak.stock_bid_ask_em(symbol=code)
        if df is None or df.empty:
            return {}
        raw = {}
        for _, row in df.iterrows():
            raw[row["item"]] = row["value"]
        result = {}
        for i in range(1, 6):
            result[f"buy_{i}"] = _safe_float(raw.get(f"buy_{i}"))
            result[f"buy_{i}_vol"] = int(_safe_float(raw.get(f"buy_{i}_vol", 0)))
            result[f"sell_{i}"] = _safe_float(raw.get(f"sell_{i}"))
            result[f"sell_{i}_vol"] = int(_safe_float(raw.get(f"sell_{i}_vol", 0)))
        result["latest"] = _safe_float(raw.get("最新"))
        result["avg"] = _safe_float(raw.get("均价"))
        result["limit_up"] = _safe_float(raw.get("涨停"))
        result["limit_down"] = _safe_float(raw.get("跌停"))
        _bidask_cache[cache_key] = (now, result)
        return result
    except Exception as e:
        logger.warning("API调用失败: %s", e)
        return {}


# ─── 资金流向 ───

_fund_flow_cache: BoundedCache = _register_cache(BoundedCache(256), 300)
_fund_flow_cache_timeout = 300


def _sz_sh_prefix(code: str) -> str:
    """返回 sz/sh 前缀。6/9/5开头→sh，其余→sz。"""
    return "sh" if code.startswith(("6", "9", "5")) else "sz"


def get_fund_flow(code: str) -> list[dict]:
    """获取个股资金流向（每日），缓存5分钟。"""
    cache_key = f"fundflow:{code}"
    now = time.time()
    if cache_key in _fund_flow_cache:
        ts, data = _fund_flow_cache[cache_key]
        if now - ts < _fund_flow_cache_timeout:
            return data
    try:
        market = _sz_sh_prefix(code)
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        if df is None or df.empty:
            return []
        result = []
        for _, row in df.iterrows():
            result.append({
                "date": str(row.iloc[0]),
                "close": _safe_float(row.iloc[1]),
                "change_pct": _safe_float(row.iloc[2]),
                "main_net": _safe_float(row.iloc[3]),
                "main_pct": _safe_float(row.iloc[4]),
                "huge_net": _safe_float(row.iloc[5]),
                "huge_pct": _safe_float(row.iloc[6]),
                "big_net": _safe_float(row.iloc[7]),
                "big_pct": _safe_float(row.iloc[8]),
                "mid_net": _safe_float(row.iloc[9]),
                "mid_pct": _safe_float(row.iloc[10]),
                "small_net": _safe_float(row.iloc[11]),
                "small_pct": _safe_float(row.iloc[12]),
            })
        result.reverse()
        _fund_flow_cache[cache_key] = (now, result)
        return result
    except Exception as e:
        logger.warning("API调用失败: %s", e)
        return []


# ─── 分钟K线 ───

_minute_cache: BoundedCache = _register_cache(BoundedCache(512), 60)
_minute_cache_timeout = 60


def get_minute_history(code: str, period: str = "1") -> list[dict]:
    """获取分钟K线数据。period: 1/5/15/30/60，缓存60秒。"""
    cache_key = f"minute:{code}:{period}"
    now = time.time()
    if cache_key in _minute_cache:
        ts, data = _minute_cache[cache_key]
        if now - ts < _minute_cache_timeout:
            return data
    try:
        prefix = _sina_prefix(code)
        df = ak.stock_zh_a_minute(symbol=f"{prefix}{code}", period=period)
        if df is None or df.empty:
            return []
        result = []
        for _, row in df.iterrows():
            result.append({
                "day": str(row["day"]),
                "open": str(row["open"]),
                "high": str(row["high"]),
                "low": str(row["low"]),
                "close": str(row["close"]),
                "volume": str(row["volume"]),
            })
        _minute_cache[cache_key] = (now, result)
        return result
    except Exception as e:
        logger.warning("API调用失败: %s", e)
        return []


# ─── 涨跌排行 ───

def get_ranking(sort_by: str = "涨跌幅", order: str = "desc", limit: int = 50) -> list[dict]:
    """从已有行情缓存中提取排行榜。sort_by: 涨跌幅/换手率/成交额/量比, order: desc/asc"""
    stocks = get_spot_data()
    if not stocks:
        return []
    reverse = order == "desc"
    valid = [s for s in stocks if s.get(sort_by) is not None]
    valid.sort(key=lambda s: s[sort_by], reverse=reverse)
    return valid[:limit]


# ─── 龙虎榜 ───

_lhb_cache: BoundedCache = _register_cache(BoundedCache(16), 300)
_lhb_cache_timeout = 300


def get_lhb(days: int = 5) -> list[dict]:
    """获取龙虎榜数据，缓存5分钟。"""
    cache_key = "lhb"
    now = time.time()
    if cache_key in _lhb_cache:
        ts, data = _lhb_cache[cache_key]
        if now - ts < _lhb_cache_timeout:
            return data
    try:
        from datetime import date, timedelta
        end = date.today()
        start = end - timedelta(days=days)
        df = ak.stock_lhb_detail_em(
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
        if df is None or df.empty:
            return []
        result = []
        for _, row in df.iterrows():
            item = {
                "代码": str(row.get("代码", "")),
                "名称": str(row.get("名称", "")),
                "上榜日": str(row.get("上榜日", "")),
                "收盘价": _safe_float(row.get("收盘价")),
                "涨跌幅": _safe_float(row.get("涨跌幅")),
                "净买额": _safe_float(row.get("龙虎榜净买额")),
                "买入额": _safe_float(row.get("龙虎榜买入额")),
                "卖出额": _safe_float(row.get("龙虎榜卖出额")),
                "成交额": _safe_float(row.get("龙虎榜成交额")),
                "换手率": _safe_float(row.get("换手率")),
                "上榜原因": str(row.get("上榜原因", "")),
            }
            result.append(item)
        _lhb_cache[cache_key] = (now, result)
        return result
    except Exception as e:
        logger.warning("API调用失败: %s", e)
        return []


def cleanup_all_caches() -> int:
    """清理所有已注册缓存的过期条目，返回总清理数量。"""
    total = 0
    for cache, timeout in _all_caches:
        total += cache.cleanup(timeout)
    if total:
        logger.debug("缓存清理: 移除 %d 条过期条目", total)
    return total
