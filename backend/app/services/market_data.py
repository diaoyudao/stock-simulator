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
        data = []
        for _, row in df.iterrows():
            data.append({
                "day": str(row["日期"]),
                "open": str(row["开盘"]),
                "high": str(row["最高"]),
                "low": str(row["最低"]),
                "close": str(row["收盘"]),
                "volume": str(row["成交量"]),
            })
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
    """并发获取多个新浪行业的成分股。返回 {label: [{code, name, changepercent}, ...]}。"""
    result = {}
    lock = threading.Lock()

    def _fetch_one(label: str):
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
        except Exception:
            pass

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


def get_sector_overview() -> list[dict]:
    """返回各行业板块概览，带5分钟缓存。东方财富优先，失败降级新浪。"""
    global _sector_overview_cache, _sector_overview_cache_time
    now = time.time()
    if _sector_overview_cache and now - _sector_overview_cache_time < 300:
        return _sector_overview_cache

    result = _build_sector_overview_em() or _build_sector_overview_sina()
    if result:
        result.sort(key=lambda x: x["avg_change_pct"], reverse=True)
        _sector_overview_cache = result
        _sector_overview_cache_time = now
        return result
    return _sector_overview_cache or []


# ─── 财务数据 ───

_financial_cache: BoundedCache = _register_cache(BoundedCache(256), 300)
_financial_cache_timeout = 300

_statement_cache: BoundedCache = _register_cache(BoundedCache(256), 300)
_statement_cache_timeout = 300

_VALID_STATEMENTS = {"利润表", "资产负债表", "现金流量表"}


def _sina_prefix(code: str) -> str:
    return "sh" if code.startswith("6") or code.startswith("9") else "sz"


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
    """返回 sz/sh 前缀。"""
    return "sh" if code.startswith(("6", "9")) else "sz"


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
