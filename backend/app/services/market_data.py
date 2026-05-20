import time
import logging
import threading
import numpy as np
import akshare as ak
import asyncio
from app.services import astock_data
from datetime import datetime, timedelta
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# ─── 直接 HTTP 请求头 ───

_SINA_HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}
_EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


def _http_get(url, retries=2, **kwargs):
    """直接HTTP GET请求，绕过系统代理，自动重试。"""
    import requests as _req
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    s = _req.Session()
    s.trust_env = False
    retry = Retry(total=retries, backoff_factor=0.5, status_forcelist=[502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s.get(url, **kwargs)


def _em_secid(code: str) -> str:
    """东方财富 secid：6/9/5开头→1.{code}(沪)，其余→0.{code}(深/京)"""
    if code.startswith(("6", "9", "5")):
        return f"1.{code}"
    return f"0.{code}"


def _sina_prefix(code: str) -> str:
    """新浪行情前缀：6/9→sh，4/8→bj，其余→sz"""
    if code.startswith("6") or code.startswith("9"):
        return "sh"
    if code.startswith("4") or code.startswith("8"):
        return "bj"
    return "sz"


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


# 东方财富字段 -> 中文key映射
_EM_SPOT_FIELDS = "f2,f3,f4,f5,f6,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23"
_EM_SPOT_FIELD_MAP = {
    "f2": "最新价", "f3": "涨跌幅", "f4": "涨跌额", "f5": "成交量",
    "f6": "成交额", "f8": "换手率", "f9": "市盈率-动态", "f10": "量比",
    "f12": "代码", "f14": "名称", "f15": "最高", "f16": "最低",
    "f17": "今开", "f18": "昨收", "f20": "总市值", "f21": "流通市值", "f23": "市净率",
}


def _fetch_spot_em_direct() -> list[dict]:
    """通过东方财富push2 API直接获取全市场A股实时行情，分页请求。"""
    all_stocks = []
    page = 1
    page_size = 5000
    while True:
        try:
            r = _http_get(
                "https://push2.eastmoney.com/api/qt/clist/get",
                params={
                    "pn": page, "pz": page_size, "po": "1", "np": "1",
                    "fltt": "2", "invt": "2", "fid": "f3",
                    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                    "fields": _EM_SPOT_FIELDS,
                },
                headers=_EM_HEADERS, timeout=15,
            )
            data = r.json().get("data")
            if not data or not data.get("diff"):
                break
            diff = data["diff"]
            total = data.get("total", 0)
            # diff可能是dict({"0":{...},"1":{...}})或list([...])
            items = diff.values() if isinstance(diff, dict) else diff
            for item in items:
                price = _safe_float(item.get("f2", 0))
                if price <= 0:
                    continue  # 跳过停牌/无报价
                result_item = {}
                for em_f, cn_key in _EM_SPOT_FIELD_MAP.items():
                    val = item.get(em_f, "")
                    if cn_key in ("代码", "名称"):
                        result_item[cn_key] = str(val)
                    elif cn_key == "成交量":
                        result_item[cn_key] = int(_safe_float(val, 0))
                    else:
                        result_item[cn_key] = _safe_float(val, 0)
                result_item["买一"] = price
                result_item["卖一"] = price
                all_stocks.append(result_item)
            if len(all_stocks) >= total:
                break
            page += 1
        except Exception as e:
            logger.warning("东方财富直接行情API第%d页失败: %s", page, e)
            break
    if all_stocks:
        logger.info("东方财富直接行情API成功，%d条", len(all_stocks))
    return all_stocks


def _fetch_all_stocks() -> list[dict]:
    """获取全市场 A 股实时行情。新回退链：腾讯批量 → mootdx → 新浪 → 旧缓存。"""
    global _cached_stocks
    # 方案1：腾讯批量（主源，字段最全：含PE/PB/量比/换手率）
    try:
        data = _fetch_spot_tencent_batch()
        if data and len(data) > 1000:
            logger.info("腾讯批量行情成功，%d条", len(data))
            return data
    except Exception as e:
        logger.warning("腾讯批量行情失败: %s", e)
    # 方案2：mootdx TCP（降级1，绕过DNS/代理，缺PE/PB/量比）
    try:
        data = _fetch_spot_tdx()
        if data and len(data) > 1000:
            logger.info("mootdx行情成功，%d条（降级模式）", len(data))
            return data
    except Exception as e:
        logger.warning("mootdx行情失败: %s", e)
    # 方案3：新浪 getHQNodeData 全量（降级2，字段少）
    try:
        data = _fetch_spot_sina_hq_node()
        if data and len(data) > 1000:
            logger.info("新浪全量行情成功，%d条（降级模式）", len(data))
            return data
    except Exception as e:
        logger.warning("新浪全量行情失败: %s", e)
    # 方案4：东方财富 push2（可能被DNS污染，最后尝试）
    try:
        data = _fetch_spot_em_direct()
        if data:
            logger.info("东财push2行情成功，%d条", len(data))
            return data
    except Exception as e:
        logger.warning("东财push2行情失败: %s", e)
    logger.warning("所有行情API失败，使用旧缓存(%d条)", len(_cached_stocks))
    return _cached_stocks


# ─── 代码列表缓存（供腾讯批量用） ───

_code_list_cache: dict = {"data": None, "ts": 0}
_CODE_LIST_TTL = 300  # 5分钟


def _fetch_code_list() -> list[str]:
    """获取全市场A股代码列表。优先缓存/行情数据，降级mootdx/新浪。5分钟缓存。"""
    now = time.time()
    if _code_list_cache["data"] and now - _code_list_cache["ts"] < _CODE_LIST_TTL:
        return _code_list_cache["data"]
    # 优先：从已缓存的行情数据提取代码列表
    if _cached_stocks and len(_cached_stocks) > 1000:
        codes = [s["代码"] for s in _cached_stocks]
        _code_list_cache["data"] = codes
        _code_list_cache["ts"] = now
        return codes
    # 方案2：mootdx TCP 获取（1-2秒，绕过DNS/代理）
    try:
        client = astock_data._get_tdx_client_sync()
        codes = []
        for market in [0, 1]:  # 0=深市, 1=沪市
            df = client.stocks(market=market)
            if df is not None and not df.empty:
                for code in df["code"]:
                    c = str(code).zfill(6)
                    # A股代码：深市00/30开头(排除39指数), 沪市60/68开头, 北证8开头
                    is_ashare = False
                    if c[:1] in ("0", "3") and not c.startswith("39"):
                        is_ashare = True
                    elif c[:2] in ("60", "68"):
                        is_ashare = True
                    elif c[:1] == "8":
                        is_ashare = True
                    if is_ashare:
                        codes.append(c)
        if len(codes) > 1000:
            _code_list_cache["data"] = codes
            _code_list_cache["ts"] = now
            return codes
    except Exception as e:
        logger.warning("mootdx代码列表获取失败: %s", e)
    # 方案3：新浪分页获取（串行，可能被限流）
    codes = []
    try:
        for page in range(1, 80):
            try:
                page_codes = _fetch_sina_page_codes(page)
                if not page_codes:
                    break
                codes.extend(page_codes)
                if len(page_codes) < 80:
                    break
            except Exception:
                break
    except Exception as e:
        logger.warning("新浪代码列表获取失败: %s", e)
    if codes:
        _code_list_cache["data"] = codes
        _code_list_cache["ts"] = now
    return _code_list_cache["data"] or codes


def _fetch_sina_page_codes(page: int) -> list[str]:
    """新浪单页代码列表"""
    url = (f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"Market_Center.getHQNodeData?page={page}&num=80&sort=symbol&asc=1"
           f"&node=hs_a&symbol=&_s_r_a=auto")
    r = _http_get(url, headers=_SINA_HEADERS, timeout=10)
    data = r.json()
    return [str(item.get("code", "")) for item in data if item.get("code")]


def _fetch_spot_tencent_batch() -> list[dict]:
    """腾讯批量获取全市场行情（同步），调用 astock_data._tencent_quote_sync。"""
    codes = _fetch_code_list()
    if not codes:
        return []
    return astock_data._tencent_quote_sync(codes)


def _fetch_spot_tdx() -> list[dict]:
    """mootdx TCP 获取全市场行情（同步），缺PE/PB/量比，标记降级。
    直接用 mootdx stocks() 获取代码列表，不依赖新浪API。"""
    try:
        client = astock_data._get_tdx_client_sync()
        # 从 mootdx 获取A股代码列表
        codes = []
        for market in [0, 1]:
            try:
                df = client.stocks(market=market)
                if df is not None and not df.empty:
                    for code in df["code"]:
                        c = str(code).zfill(6)
                        if c[:1] in ("6", "9", "0", "3", "8"):
                            codes.append(c)
            except Exception:
                continue
        if not codes:
            return []
        # 批量获取行情
        result = []
        batch_size = 500
        for i in range(0, len(codes), batch_size):
            batch = codes[i:i + batch_size]
            try:
                quotes = astock_data._tdx_quotes_sync(batch)
                for q in quotes.values():
                    price = q.get("最新价", 0)
                    if price <= 0:
                        continue
                    s = {
                        "代码": q["代码"], "名称": q["名称"],
                        "最新价": price, "昨收": q["昨收"],
                        "今开": q["今开"], "最高": q["最高"], "最低": q["最低"],
                        "成交量": q["成交量"], "成交额": q["成交额"],
                        "涨跌幅": q["涨跌幅"], "涨跌额": q["涨跌额"],
                        "买一": price, "卖一": price,
                        "换手率": 0, "市盈率-动态": 0, "市净率": 0,
                        "总市值": 0, "流通市值": 0, "量比": 0,
                        "振幅": 0, "涨停价": 0, "跌停价": 0,
                        "_degraded": True,
                    }
                    result.append(s)
            except Exception as e:
                logger.warning("mootdx批量查询第%d批失败: %s", i // batch_size, e)
                continue
        return result
    except Exception as e:
        logger.warning("mootdx行情获取失败: %s", e)
        return []


def _fetch_spot_sina_hq_node() -> list[dict]:
    """新浪 getHQNodeData 分页全量获取。新浪返回含PE/PB/换手率/市值，字段较全。"""
    try:
        total = 5500
        pages = (total + 79) // 80
        result = []
        for page in range(1, min(pages + 1, 80)):
            try:
                page_data = _fetch_sina_page_stocks(page)
                if not page_data:
                    break
                result.extend(page_data)
                if len(page_data) < 80:
                    break
            except Exception:
                break
        return result
    except Exception as e:
        logger.warning("新浪全量行情获取失败: %s", e)
        return []


def _fetch_sina_page_stocks(page: int) -> list[dict]:
    """新浪单页行情数据"""
    url = (f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"Market_Center.getHQNodeData?page={page}&num=80&sort=symbol&asc=1"
           f"&node=hs_a&symbol=&_s_r_a=auto")
    r = _http_get(url, headers=_SINA_HEADERS, timeout=10)
    data = r.json()
    result = []
    for item in data:
        code = str(item.get("code", ""))
        price = _safe_float(item.get("trade", 0))
        if price <= 0:
            continue
        yesterday = _safe_float(item.get("settlement", 0))
        # 新浪字段: per=PE, pb=PB, mktcap=总市值(亿), nmc=流通市值(亿), turnoverratio=换手率
        mktcap = _safe_float(item.get("mktcap", 0))
        nmc = _safe_float(item.get("nmc", 0))
        result.append({
            "代码": code,
            "名称": str(item.get("name", "")),
            "最新价": price,
            "昨收": yesterday,
            "今开": _safe_float(item.get("open", 0)),
            "最高": _safe_float(item.get("high", 0)),
            "最低": _safe_float(item.get("low", 0)),
            "涨跌额": _safe_float(item.get("pricechange", 0)),
            "涨跌幅": _safe_float(item.get("changepercent", 0)),
            "成交量": int(_safe_float(item.get("volume", 0))),
            "成交额": _safe_float(item.get("amount", 0)),
            "买一": _safe_float(item.get("buy", price)),
            "卖一": _safe_float(item.get("sell", price)),
            "换手率": _safe_float(item.get("turnoverratio", 0)),
            "市盈率-动态": _safe_float(item.get("per", 0)),
            "市净率": _safe_float(item.get("pb", 0)),
            "总市值": mktcap * 100000000 if mktcap else 0,  # 亿→元
            "流通市值": nmc * 100000000 if nmc else 0,
            "量比": 0,  # 新浪无量比
            "振幅": 0,
            "涨停价": 0,
            "跌停价": 0,
        })
    return result


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
        klines = get_stock_history(code, "daily", start_date=start)
        if not klines:
            return 0.0, 0.0
        highs = [float(k["high"]) for k in klines if k.get("high")]
        lows = [float(k["low"]) for k in klines if k.get("low")]
        if not highs or not lows:
            return 0.0, 0.0
        high52 = max(highs)
        low52 = min(lows)
        _52week_cache[code] = (now, high52, low52)
        return high52, low52
    except Exception as e:
        logger.warning("52周数据计算失败 %s: %s", code, e)
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

    # 板块筛选：按需获取成分股代码集合
    sector_codes: set[str] | None = None
    sector_failed = False
    if sector:
        sector_codes = _fetch_sector_constituents(sector)
        if sector_codes is None:
            # 全部API失败，无法按板块筛选
            sector_failed = True
            logger.warning("板块成分股API全部失败，跳过板块筛选: %s", sector)
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
        result["warning"] = f"板块「{sector}」成分股数据获取失败，板块筛选未生效"
    if degraded:
        result["warning"] = (result.get("warning", "") + "；" if result.get("warning") else "") + \
            "当前使用备用数据源，换手率/市盈率/市净率/市值/量比筛选暂不可用"
    return result


# ─── 股票详情 ───

def _fetch_bidask_sina(code: str) -> dict | None:
    """通过新浪行情接口获取五档盘口数据。返回 None 表示失败。"""
    prefix = _sina_prefix(code)
    url = f"https://hq.sinajs.cn/list={prefix}{code}"
    r = _http_get(url, headers=_SINA_HEADERS, timeout=10)
    r.encoding = "gbk"
    val = r.text.split('"')[1] if '"' in r.text else ""
    if not val:
        return None
    fields = val.split(",")
    if len(fields) < 32:
        return None

    result = {}
    # 新浪5档买卖盘字段：买1量(10) 买1价(11) 买2量(12) 买2价(13) ... 买5量(18) 买5价(19)
    # 卖1量(20) 卖1价(21) 卖2量(22) 卖2价(23) ... 卖5量(28) 卖5价(29)
    for i in range(1, 6):
        buy_vol_idx = 10 + (i - 1) * 2
        buy_price_idx = 11 + (i - 1) * 2
        sell_vol_idx = 20 + (i - 1) * 2
        sell_price_idx = 21 + (i - 1) * 2
        result[f"buy_{i}"] = _safe_float(fields[buy_price_idx])
        result[f"buy_{i}_vol"] = int(_safe_float(fields[buy_vol_idx], 0))
        result[f"sell_{i}"] = _safe_float(fields[sell_price_idx])
        result[f"sell_{i}_vol"] = int(_safe_float(fields[sell_vol_idx], 0))

    price = _safe_float(fields[3])
    yesterday = _safe_float(fields[2])
    volume = int(_safe_float(fields[8], 0))  # 手
    amount = _safe_float(fields[9], 0)
    result["latest"] = price
    result["avg"] = round(amount / (volume * 100), 3) if volume > 0 else price
    # 涨停/跌停：普通10%，ST 5%
    name = fields[0]
    limit_pct = 0.05 if "ST" in name or "st" in name else 0.10
    result["limit_up"] = round(yesterday * (1 + limit_pct), 2) if yesterday else 0
    result["limit_down"] = round(yesterday * (1 - limit_pct), 2) if yesterday else 0
    return result


def get_stock_detail(code: str) -> dict:
    """获取单只股票详细行情。并发获取买卖盘、52周、行业信息。"""
    s = get_stock_by_code(code)
    if s:
        s = dict(s)  # 浅拷贝避免污染缓存

        f_52week = _executor.submit(_compute_52week, code)
        f_consec = _executor.submit(compute_consecutive_days, code)
        f_info = _executor.submit(_fetch_stock_info, code)

        # 买卖盘（新浪优先 → mootdx → AKShare备用）
        # 注意：mootdx在行情刷新后连接状态不稳定，不优先使用
        bidask_ok = False
        # 1. 新浪HTTP（快速稳定）
        try:
            bidask = _fetch_bidask_sina(code)
            if bidask:
                s["买一"] = bidask["buy_1"] or s["最新价"]
                s["卖一"] = bidask["sell_1"] or s["最新价"]
                bidask_ok = True
        except Exception as e:
            logger.warning("新浪盘口API失败: %s", e)
        # 2. mootdx TCP（降级，可能慢）
        if not bidask_ok:
            try:
                tdx_q = astock_data._tdx_quotes_sync([code])
                if code in tdx_q:
                    q = tdx_q[code]
                    s["买一"] = q.get("买一") or s["最新价"]
                    s["卖一"] = q.get("卖一") or s["最新价"]
                    bidask_ok = True
            except Exception as e:
                logger.warning("mootdx盘口API失败: %s", e)
        # 3. AKShare（走东财push2，可能被封锁）
        if not bidask_ok:
            try:
                bidask_df = ak.stock_bid_ask_em(symbol=code)
                if bidask_df is not None and not bidask_df.empty:
                    bidask = dict(zip(bidask_df["item"], bidask_df["value"]))
                    s["买一"] = _safe_float(bidask.get("buy_1"), s["最新价"])
                    s["卖一"] = _safe_float(bidask.get("sell_1"), s["最新价"])
                    if "量比" in bidask:
                        s["量比"] = _safe_float(bidask["量比"])
            except Exception as e:
                logger.warning("AKShare盘口API失败: %s", e)

        # 52周
        high52, low52 = f_52week.result()
        s["52周最高"] = high52
        s["52周最低"] = low52

        # 连涨跌
        consec = f_consec.result()
        s["连涨天数"] = consec["连涨天数"]
        s["连跌天数"] = consec["连跌天数"]

        # 板块
        s["板块"] = f_info.result()

        return s

    # 非A股列表中的股票：通过盘口接口构建
    try:
        f_info = _executor.submit(_fetch_stock_info, code)
        bidask = None
        # 1. 新浪HTTP 优先
        try:
            bidask = _fetch_bidask_sina(code)
        except Exception:
            pass
        # 2. mootdx TCP 降级
        if not bidask:
            try:
                tdx_q = astock_data._tdx_quotes_sync([code])
                if code in tdx_q:
                    q = tdx_q[code]
                    bidask = {
                        "latest": q.get("最新价", 0),
                        "buy_1": q.get("买一", 0),
                        "sell_1": q.get("卖一", 0),
                    }
            except Exception:
                pass
        # 3. AKShare备用
        if not bidask:
            bidask_df = ak.stock_bid_ask_em(symbol=code)
            if bidask_df is not None and not bidask_df.empty:
                raw = dict(zip(bidask_df["item"], bidask_df["value"]))
                bidask = {
                    "latest": _safe_float(raw.get("最新")),
                    "buy_1": _safe_float(raw.get("buy_1")),
                    "sell_1": _safe_float(raw.get("sell_1")),
                }

        info = f_info.result()
        if bidask:
            price = bidask.get("latest", 0) or 0
            # 新浪返回完整数据时用新浪字段
            prefix = _sina_prefix(code)
            r2 = _http_get(f"https://hq.sinajs.cn/list={prefix}{code}", headers=_SINA_HEADERS, timeout=10)
            r2.encoding = "gbk"
            val2 = r2.text.split('"')[1] if '"' in r2.text else ""
            if val2:
                f = val2.split(",")
                price = _safe_float(f[3])
                yesterday = _safe_float(f[2])
                return {
                    "代码": code, "名称": f[0], "最新价": price, "昨收": yesterday,
                    "今开": _safe_float(f[1]), "成交量": int(_safe_float(f[8], 0)),
                    "最高": _safe_float(f[4]), "最低": _safe_float(f[5]),
                    "涨跌额": round(price - yesterday, 2) if yesterday else 0,
                    "涨跌幅": round((price - yesterday) / yesterday * 100, 2) if yesterday else 0,
                    "买一": bidask.get("buy_1", price) or price,
                    "卖一": bidask.get("sell_1", price) or price,
                    "成交额": _safe_float(f[9]), "换手率": 0,
                    "市盈率-动态": 0, "总市值": 0, "流通市值": 0, "市净率": 0, "量比": 0,
                    "52周最高": 0, "52周最低": 0, "连涨天数": 0, "连跌天数": 0, "板块": info,
                }
        return {
            "代码": code, "名称": "", "最新价": 0, "昨收": 0,
            "今开": 0, "成交量": 0, "最高": 0, "最低": 0,
            "涨跌额": 0, "涨跌幅": 0, "买一": 0, "卖一": 0,
            "成交额": 0, "换手率": 0, "市盈率-动态": 0,
            "总市值": 0, "流通市值": 0, "市净率": 0, "量比": 0,
            "52周最高": 0, "52周最低": 0, "连涨天数": 0, "连跌天数": 0, "板块": info,
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
_EM_KLT_MAP = {"daily": 101, "weekly": 102, "monthly": 103}
_EM_MINUTE_KLT_MAP = {"1": 1, "5": 5, "15": 15, "30": 30, "60": 60}


def _fetch_kline_em_direct(code: str, period: str = "daily", start_date: str = "20250101") -> list[dict]:
    """通过东方财富push2his API直接获取K线数据。返回空列表表示失败。"""
    klt = _EM_KLT_MAP.get(period, 101)
    secid = _em_secid(code)
    r = _http_get(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "secid": secid, "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": klt, "fqt": "1", "beg": start_date, "end": "20500101",
        },
        headers=_EM_HEADERS, timeout=15,
    )
    data = r.json().get("data")
    if not data or not data.get("klines"):
        return []
    result = []
    for line in data["klines"]:
        parts = line.split(",")
        if len(parts) < 6:
            continue
        result.append({
            "day": parts[0], "open": parts[1], "close": parts[2],
            "high": parts[3], "low": parts[4], "volume": parts[5],
        })
    return result


def get_stock_history(code: str, period: str = "daily", start_date: str = "20250101") -> list[dict]:
    """获取单只股票历史K线，带60秒缓存。mootdx优先，东方财富/AKShare备用。"""
    cache_key = f"{code}:{period}"
    now = time.time()
    if cache_key in _kline_cache:
        ts, data = _kline_cache[cache_key]
        if now - ts < _kline_cache_timeout:
            return data

    # mootdx TCP直连（优先）
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            raw = pool.submit(lambda: asyncio.run(astock_data.tdx_bars(code, period, 300))).result()
        if raw:
            _kline_cache[cache_key] = (now, raw)
            return raw
    except Exception as e:
        logger.warning("mootdx K线API失败: %s", e)

    # 东方财富直接HTTP
    try:
        data = _fetch_kline_em_direct(code, period, start_date)
        if data:
            _kline_cache[cache_key] = (now, data)
            return data
    except Exception as e:
        logger.warning("东方财富K线直接API失败: %s", e)

    # AKShare备用
    ak_period = _KLINE_PERIOD_MAP.get(period, "daily")
    try:
        df = ak.stock_zh_a_hist(symbol=code, period=ak_period, start_date=start_date, adjust="qfq")
        if df is not None and not df.empty:
            data = _convert_kline_common(df)
            _kline_cache[cache_key] = (now, data)
            return data
    except Exception as e:
        logger.warning("AKShare K线API失败: %s", e)
    return []


# ─── 大盘指数 ───

_INDEX_TARGETS = {
    "000001": "上证指数",
    "399001": "深证成指",
    "399006": "创业板指",
}


def _fetch_index_sina() -> list[dict]:
    """通过新浪行情接口获取大盘指数，单次请求。"""
    codes = ["sh000001", "sz399001", "sz399006"]
    url = f"https://hq.sinajs.cn/list={','.join(codes)}"
    r = _http_get(url, headers=_SINA_HEADERS, timeout=10)
    r.encoding = "gbk"

    result = []
    for line in r.text.strip().split("\n"):
        if '="' not in line:
            continue
        raw_code = line.split("=")[0].split("_")[-1]
        val = line.split('"')[1] if '"' in line else ""
        if not val:
            continue
        fields = val.split(",")
        if len(fields) < 4:
            continue
        code_num = raw_code[2:] if raw_code.startswith(("sh", "sz")) else raw_code
        if code_num not in _INDEX_TARGETS:
            continue
        current = _safe_float(fields[3])
        yesterday = _safe_float(fields[2])
        change_pct = round((current - yesterday) / yesterday * 100, 2) if yesterday else 0
        result.append({
            "code": raw_code,
            "name": _INDEX_TARGETS[code_num],
            "current": current,
            "yesterday": yesterday,
            "change_pct": change_pct,
        })
    return result


def get_index_data() -> list[dict]:
    """获取主要大盘指数当前数据，带60秒缓存。腾讯行情优先，新浪/AKShare备用。"""
    global _index_cache
    now = time.time()
    ts, cached = _index_cache
    if cached and now - ts < _index_cache_timeout:
        return cached

    # 腾讯行情直连（优先，指数需用正确前缀）
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            def _fetch_indices_tencent():
                codes = "sh000001,sz399001,sz399006"
                url = f"https://qt.gtimg.cn/q={codes}"
                r = _http_get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                r.encoding = "gbk"
                result = []
                for line in r.text.strip().split(";"):
                    if '="' not in line or not line.strip():
                        continue
                    key = line.split("=")[0].split("_")[-1]  # e.g. sh000001
                    val = line.split('"')[1]
                    if not val:
                        continue
                    vals = val.split("~")
                    if len(vals) < 53:
                        continue
                    code_num = key[2:]  # strip sh/sz prefix
                    if code_num not in _INDEX_TARGETS:
                        continue
                    price = float(vals[3]) if vals[3] else 0
                    last_close = float(vals[4]) if vals[4] else 0
                    change_pct = round((price - last_close) / last_close * 100, 2) if last_close else 0
                    result.append({
                        "code": key,
                        "name": _INDEX_TARGETS[code_num],
                        "current": price,
                        "yesterday": last_close,
                        "change_pct": change_pct,
                    })
                return result
            result = pool.submit(_fetch_indices_tencent).result()
            if result:
                _index_cache = (now, result)
                return result
    except Exception as e:
        logger.warning("腾讯指数API失败: %s", e)

    # 新浪直接接口
    try:
        result = _fetch_index_sina()
        if result:
            _index_cache = (now, result)
            return result
    except Exception as e:
        logger.warning("新浪指数直接API失败: %s", e)

    # 东方财富AKShare备用
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
        logger.warning("AKShare指数API失败: %s", e)
        return cached or []


# ─── 申万行业板块 ───


def _fetch_sector_list() -> list[dict]:
    """获取申万行业板块列表，缓存5分钟。31一级行业 + 131二级行业。"""
    global _sector_list_cache, _sector_list_cache_time
    now = time.time()
    if _sector_list_cache and now - _sector_list_cache_time < 300:
        return _sector_list_cache
    industries = get_sw_industries()
    sectors = [
        {"name": ind["name"], "code": str(ind["code"])}
        for ind in industries
    ]
    if sectors:
        _sector_list_cache = sectors
        _sector_list_cache_time = now
    return sectors


def _fetch_sector_constituents(sector_name: str) -> set[str] | None:
    """获取申万行业成分股代码集合，缓存5分钟。"""
    now = time.time()
    if sector_name in _sector_constituent_cache:
        ts, codes = _sector_constituent_cache[sector_name]
        if now - ts < _sector_constituent_timeout:
            return codes
    # 申万行业成分股
    codes = _fetch_sw_industry_constituents(sector_name)
    if codes is not None:
        _sector_constituent_cache[sector_name] = (now, codes)
        return codes
    return None


def _fetch_sw_industry_constituents(industry_name: str) -> set[str] | None:
    """通过申万行业分类获取成分股。用硬编码映射查行业代码。"""
    # 从硬编码映射查找行业代码
    code = None
    for c, n in _SW_L1_INDUSTRIES:
        if n == industry_name:
            code = c
            break
    if not code:
        for c, n, _ in _SW_L2_INDUSTRIES:
            if n == industry_name:
                code = c
                break
    if not code:
        return None

    try:
        df_cons = ak.index_component_sw(symbol=code)
        if df_cons is not None and not df_cons.empty:
            col = "证券代码" if "证券代码" in df_cons.columns else df_cons.columns[1]
            return set(df_cons[col].astype(str).tolist())
    except Exception as e:
        logger.debug("申万行业成分股查询失败(%s/%s): %s", industry_name, code, e)
    return None


def get_sector_list() -> list[dict]:
    """返回申万行业板块列表。"""
    return _fetch_sector_list()


# ─── 申万行业 ───

_sw_industry_cache: list[dict] | None = None
_sw_industry_cache_time: float = 0
_SW_INDUSTRY_CACHE_TIMEOUT = 3600  # 1小时


# 申万2021版一级行业代码映射（legulegu/push2均不可用时使用）
_SW_L1_INDUSTRIES = [
    ("801010", "农林牧渔"), ("801030", "基础化工"), ("801040", "钢铁"),
    ("801050", "有色金属"), ("801080", "电子"), ("801110", "家用电器"),
    ("801120", "食品饮料"), ("801130", "纺织服饰"), ("801140", "轻工制造"),
    ("801150", "医药生物"), ("801160", "公用事业"), ("801170", "交通运输"),
    ("801180", "房地产"), ("801200", "商贸零售"), ("801210", "社会服务"),
    ("801230", "银行"), ("801250", "非银金融"), ("801260", "建筑材料"),
    ("801270", "建筑装饰"), ("801280", "电力设备"), ("801290", "机械设备"),
    ("801710", "计算机"), ("801720", "传媒"), ("801730", "通信"),
    ("801740", "煤炭"), ("801750", "石油石化"), ("801760", "环保"),
    ("801770", "美容护理"), ("801780", "国防军工"), ("801790", "综合"),
    ("801880", "汽车"),
]

# 申万2021版二级行业代码映射
_SW_L2_INDUSTRIES = [
    ("801011", "种植业", "农林牧渔"), ("801012", "渔业", "农林牧渔"),
    ("801013", "饲料", "农林牧渔"), ("801014", "农产品加工", "农林牧渔"),
    ("801015", "林业", "农林牧渔"), ("801016", "养殖", "农林牧渔"),
    ("801017", "农业综合", "农林牧渔"),
    ("801031", "化学原料", "基础化工"), ("801032", "化学制品", "基础化工"),
    ("801033", "塑料", "基础化工"), ("801034", "橡胶", "基础化工"),
    ("801035", "农化制品", "基础化工"), ("801036", "非金属材料", "基础化工"),
    ("801041", "普钢", "钢铁"), ("801042", "特钢", "钢铁"),
    ("801051", "工业金属", "有色金属"), ("801052", "贵金属", "有色金属"),
    ("801053", "小金属", "有色金属"), ("801054", "金属新材料", "有色金属"),
    ("801081", "半导体", "电子"), ("801082", "元件", "电子"),
    ("801083", "光学光电子", "电子"), ("801084", "消费电子", "电子"),
    ("801085", "其他电子", "电子"), ("801086", "电子化学品", "电子"),
    ("801111", "白色家电", "家用电器"), ("801112", "黑色家电", "家用电器"),
    ("801113", "小家电", "家用电器"), ("801114", "厨卫电器", "家用电器"),
    ("801115", "照明设备", "家用电器"), ("801116", "家电零部件", "家用电器"),
    ("801121", "白酒", "食品饮料"), ("801122", "非白酒", "食品饮料"),
    ("801123", "调味发酵品", "食品饮料"), ("801124", "乳品", "食品饮料"),
    ("801125", "预加工食品", "食品饮料"), ("801126", "零食", "食品饮料"),
    ("801127", "保健品", "食品饮料"), ("801128", "软饮料", "食品饮料"),
    ("801129", "啤酒", "食品饮料"), ("80112A", "其他酒类", "食品饮料"),
    ("801131", "服装", "纺织服饰"), ("801132", "家纺", "纺织服饰"),
    ("801133", "饰品", "纺织服饰"), ("801134", "鞋帽", "纺织服饰"),
    ("801135", "纺织制造", "纺织服饰"),
    ("801141", "造纸", "轻工制造"), ("801142", "包装印刷", "轻工制造"),
    ("801143", "家居", "轻工制造"), ("801144", "文娱用品", "轻工制造"),
    ("801145", "其他轻工", "轻工制造"),
    ("801151", "化学制药", "医药生物"), ("801152", "中药", "医药生物"),
    ("801153", "生物制品", "医药生物"), ("801154", "医药商业", "医药生物"),
    ("801155", "医疗器械", "医药生物"), ("801156", "医疗服务", "医药生物"),
    ("801161", "电力", "公用事业"), ("801162", "燃气", "公用事业"),
    ("801163", "环保", "公用事业"), ("801164", "水务", "公用事业"),
    ("801171", "铁路公路", "交通运输"), ("801172", "航空机场", "交通运输"),
    ("801173", "航运港口", "交通运输"), ("801174", "物流", "交通运输"),
    ("801175", "公交", "交通运输"),
    ("801181", "房地产", "房地产"), ("801182", "房地产服务", "房地产"),
    ("801201", "一般零售", "商贸零售"), ("801202", "专业零售", "商贸零售"),
    ("801203", "贸易", "商贸零售"), ("801204", "互联网电商", "商贸零售"),
    ("801211", "酒店餐饮", "社会服务"), ("801212", "旅游及景区", "社会服务"),
    ("801213", "教育", "社会服务"), ("801214", "专业服务", "社会服务"),
    ("801231", "银行", "银行"),
    ("801251", "证券", "非银金融"), ("801252", "保险", "非银金融"),
    ("801253", "多元金融", "非银金融"),
    ("801261", "水泥", "建筑材料"), ("801262", "玻璃玻纤", "建筑材料"),
    ("801263", "装修建材", "建筑材料"),
    ("801271", "房屋建设", "建筑装饰"), ("801272", "装修装饰", "建筑装饰"),
    ("801273", "基础建设", "建筑装饰"), ("801274", "专业工程", "建筑装饰"),
    ("801275", "工程咨询", "建筑装饰"),
    ("801281", "电池", "电力设备"), ("801282", "光伏", "电力设备"),
    ("801283", "风电", "电力设备"), ("801284", "电机", "电力设备"),
    ("801285", "电网设备", "电力设备"),
    ("801291", "通用设备", "机械设备"), ("801292", "专用设备", "机械设备"),
    ("801293", "仪器仪表", "机械设备"), ("801294", "自动化设备", "机械设备"),
    ("801711", "计算机设备", "计算机"), ("801712", "IT服务", "计算机"),
    ("801713", "软件开发", "计算机"),
    ("801721", "出版", "传媒"), ("801722", "影视", "传媒"),
    ("801723", "数字媒体", "传媒"), ("801724", "游戏", "传媒"),
    ("801725", "营销", "传媒"), ("801726", "电视广播", "传媒"),
    ("801727", "院线", "传媒"),
    ("801731", "通信服务", "通信"), ("801732", "通信设备", "通信"),
    ("801741", "煤炭开采", "煤炭"), ("801742", "焦炭", "煤炭"),
    ("801751", "油气开采", "石油石化"), ("801752", "油服工程", "石油石化"),
    ("801753", "炼化及贸易", "石油石化"),
    ("801761", "环境治理", "环保"), ("801762", "环保设备", "环保"),
    ("801771", "个护用品", "美容护理"), ("801772", "化妆品", "美容护理"),
    ("801773", "生活用纸", "美容护理"),
    ("801781", "航天装备", "国防军工"), ("801782", "航空装备", "国防军工"),
    ("801783", "地兵装备", "国防军工"), ("801784", "军工电子", "国防军工"),
    ("801791", "综合", "综合"),
    ("801881", "乘用车", "汽车"), ("801882", "商用车", "汽车"),
    ("801883", "汽车零部件", "汽车"), ("801884", "摩托车", "汽车"),
    ("801885", "汽车服务", "汽车"),
]


def get_sw_industries() -> list[dict]:
    """申万行业分级：31一级行业 + 二级行业。缓存1小时。动态查成分股数量。"""
    global _sw_industry_cache, _sw_industry_cache_time
    now = time.time()
    if _sw_industry_cache and now - _sw_industry_cache_time < _SW_INDUSTRY_CACHE_TIMEOUT:
        return _sw_industry_cache

    result = []
    for code, name in _SW_L1_INDUSTRIES:
        count = _get_sw_constituent_count(code)
        result.append({"name": name, "code": code, "level": 1, "count": count, "parent": "", "pe": 0})
    for code, name, parent in _SW_L2_INDUSTRIES:
        count = _get_sw_constituent_count(code)
        result.append({"name": name, "code": code, "level": 2, "count": count, "parent": parent, "pe": 0})

    _sw_industry_cache = result
    _sw_industry_cache_time = now
    return result


def _get_sw_constituent_count(code: str) -> int:
    """查申万行业成分股数量。"""
    try:
        df = ak.index_component_sw(symbol=code)
        if df is not None and not df.empty:
            return len(df)
    except Exception:
        pass
    return 0


def _build_sector_overview_sw() -> list[dict] | None:
    """基于东财行业资金流构建板块概览，含主力/净额/强度数据。"""
    try:
        df = ak.stock_fund_flow_industry()
        if df is None or df.empty:
            return None
        get_spot_data()
        name_to_code = {s["名称"]: s["代码"] for s in _cached_stocks}
        result = []
        for _, row in df.iterrows():
            name = str(row.get("行业", ""))
            top_name = str(row.get("领涨股", ""))
            top_change = _safe_float(row.get("领涨股-涨跌幅", 0))
            top_code = name_to_code.get(top_name, "")
            inflow = _safe_float(row.get("流入资金", 0))
            outflow = _safe_float(row.get("流出资金", 0))
            main_net = _safe_float(row.get("净额", 0))
            total_amount = inflow + outflow
            result.append({
                "name": name,
                "avg_change_pct": round(_safe_float(row.get("行业-涨跌幅", 0)), 2),
                "up_count": int(_safe_float(row.get("公司家数", 0))),
                "down_count": 0,
                "amount": main_net * 1e8,
                "total_amount": round(total_amount, 2),
                "main_net": round(main_net, 2),
                "new_high_count": 0,
                "new_low_count": 0,
                "top_stocks": [{"代码": top_code, "名称": top_name, "涨跌幅": round(top_change, 2)}],
            })
        return result
    except Exception as e:
        logger.warning("行业资金流概览构建失败: %s", e)
        return None


def _enrich_sector_52week(sectors: list[dict]) -> None:
    """利用已有52周缓存，为板块概览补充创新高/新低数量。"""
    get_spot_data()
    with _cache_lock:
        price_map = dict(_price_map_cache)
    for sec in sectors:
        name = sec["name"]
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
    """返回申万一级行业概览，带5分钟缓存。"""
    global _sector_overview_cache, _sector_overview_cache_time
    now = time.time()
    if _sector_overview_cache and now - _sector_overview_cache_time < 300:
        return _sector_overview_cache

    result = _build_sector_overview_sw()
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
    """获取ETF资金流向（每日），缓存5分钟。datacenter优先，push2his/AKShare备用。"""
    cache_key = f"etf_fundflow:{code}"
    now = time.time()
    if cache_key in _etf_fund_flow_cache:
        ts, data = _etf_fund_flow_cache[cache_key]
        if now - ts < _etf_fund_flow_cache_timeout:
            return data
    # 1. 东财数据中心（当天数据，始终可用）
    current_day = []
    try:
        current_day = _fetch_fund_flow_datacenter(code)
        if current_day:
            _save_fund_flow_history(code, current_day)
    except Exception as e:
        logger.warning("东财数据中心ETF资金流向失败(%s): %s", code, e)
    # 2. push2his完整历史（可能不可用）
    full_history = []
    try:
        full_history = _fetch_fund_flow_em_direct(code)
    except Exception as e:
        logger.debug("push2his ETF资金流向失败(%s): %s", code, e)
    # 3. AKShare备用
    if not full_history:
        try:
            market = _sz_sh_prefix(code)
            df = ak.stock_individual_fund_flow(stock=code, market=market)
            if df is not None and not df.empty:
                full_history = _parse_fund_flow_ak(df)
                _save_fund_flow_history(code, full_history)
        except Exception as e:
            logger.debug("AKShare ETF资金流向API失败(%s): %s", code, e)
    if full_history:
        result = full_history
    else:
        result = _load_fund_flow_history(code)
        if current_day:
            existing_dates = {r["date"] for r in result}
            for r in current_day:
                if r["date"] not in existing_dates:
                    result.append(r)
            result.sort(key=lambda x: x["date"])
    _etf_fund_flow_cache[cache_key] = (now, result)
    return result


def get_etf_nav(code: str) -> list[dict]:
    """获取ETF历史净值，缓存5分钟。astock_data东财直连优先，AKShare备用。"""
    cache_key = f"etf_nav:{code}"
    now = time.time()
    if cache_key in _etf_nav_cache:
        ts, data = _etf_nav_cache[cache_key]
        if now - ts < _etf_nav_cache_timeout:
            return data
    # astock_data 东财直连（优先）
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            raw = pool.submit(lambda: asyncio.run(astock_data.etf_nav(code))).result()
        if raw:
            result = []
            for item in raw:
                result.append({
                    "date": item.get("日期", ""),
                    "nav": item.get("收盘", 0),
                    "acc_nav": 0,
                    "growth": 0,
                })
            _etf_nav_cache[cache_key] = (now, result)
            return result
    except Exception as e:
        logger.warning("astock_data ETF净值失败: %s", e)
    # AKShare备用
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
    """获取ETF十大持仓，缓存30分钟。astock_data东财数据中心优先，AKShare备用。"""
    cache_key = f"etf_holdings:{code}"
    now = time.time()
    if cache_key in _etf_holdings_cache:
        ts, data = _etf_holdings_cache[cache_key]
        if now - ts < _etf_holdings_cache_timeout:
            return data
    # astock_data 东财数据中心（优先）
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            raw = pool.submit(lambda: asyncio.run(astock_data.etf_holdings(code))).result()
        if raw:
            _etf_holdings_cache[cache_key] = (now, raw)
            return raw
    except Exception as e:
        logger.warning("astock_data ETF持仓失败: %s", e)
    # AKShare备用
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
    """获取ETF资产配置+行业配置，缓存30分钟。astock_data东财数据中心优先，AKShare备用。"""
    cache_key = f"etf_allocation:{code}"
    now = time.time()
    if cache_key in _etf_allocation_cache:
        ts, data = _etf_allocation_cache[cache_key]
        if now - ts < _etf_allocation_cache_timeout:
            return data
    result: dict = {"asset": [], "industry": []}
    # astock_data 东财数据中心资产配置（优先）
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            raw = pool.submit(lambda: asyncio.run(astock_data.etf_allocation(code))).result()
        if raw:
            for item in raw:
                result["asset"].append({
                    "type": "股票" if item.get("股票占比", 0) > 0 else "其他",
                    "ratio": max(item.get("股票占比", 0), item.get("债券占比", 0), item.get("现金占比", 0), item.get("其他占比", 0)),
                })
    except Exception as e:
        logger.warning("astock_data ETF资产配置失败: %s", e)
    # AKShare资产配置补充（如有astock_data无数据则走原有逻辑）
    if not result["asset"]:
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



def get_financial_abstract(code: str) -> list[dict]:
    """获取个股财务摘要，返回最近8期，缓存5分钟。mootdx季报优先，AKShare同花顺备用。"""
    cache_key = f"abstract:{code}"
    now = time.time()
    if cache_key in _financial_cache:
        ts, data = _financial_cache[cache_key]
        if now - ts < _financial_cache_timeout:
            return data
    # mootdx 37字段季报快照（优先）
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            raw = pool.submit(lambda: asyncio.run(astock_data.tdx_finance(code))).result()
        if raw:
            result = [raw]
            _financial_cache[cache_key] = (now, result)
            return result
    except Exception as e:
        logger.warning("mootdx财务快照失败: %s", e)
    # AKShare同花顺（备用）
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
    """获取三大报表原始数据，缓存5分钟。新浪直连优先，AKShare备用。statement_type: 利润表/资产负债表/现金流量表"""
    if statement_type not in _VALID_STATEMENTS:
        return []
    cache_key = f"stmt:{code}:{statement_type}"
    now = time.time()
    if cache_key in _statement_cache:
        ts, data = _statement_cache[cache_key]
        if now - ts < _statement_cache_timeout:
            return data
    # 新浪直连（优先，同源替换）
    try:
        _type_map = {"利润表": "lrb", "资产负债表": "fzb", "现金流量表": "llb"}
        report_type = _type_map.get(statement_type, "lrb")
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            raw = pool.submit(lambda: asyncio.run(astock_data.sina_financial_report(code, report_type))).result()
        if raw:
            _statement_cache[cache_key] = (now, raw)
            return raw
    except Exception as e:
        logger.warning("新浪财报直连失败: %s", e)
    # AKShare备用
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
    """获取个股新闻资讯，缓存5分钟。astock_data东财JSONP优先，现有东财搜索API备用。"""
    cache_key = f"news:{code}"
    now = time.time()
    if cache_key in _news_cache:
        ts, data = _news_cache[cache_key]
        if now - ts < _news_cache_timeout:
            return data
    # astock_data 东财JSONP（优先）
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            raw = pool.submit(lambda: asyncio.run(astock_data.eastmoney_stock_news(code, 15))).result()
        if raw:
            result = []
            for item in raw:
                result.append({
                    "title": item.get("标题", ""),
                    "url": item.get("链接", ""),
                    "source": item.get("来源", ""),
                    "time": item.get("时间", ""),
                })
            _news_cache[cache_key] = (now, result)
            return result
    except Exception as e:
        logger.warning("astock_data资讯API失败: %s", e)
    # 现有东财搜索API（备用）
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


def _fetch_intraday_em_direct(code: str) -> list[dict]:
    """通过东方财富push2his API直接获取分时成交数据。返回空列表表示失败。"""
    secid = _em_secid(code)
    r = _http_get(
        "https://push2his.eastmoney.com/api/qt/stock/trends2/get",
        params={
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        },
        headers=_EM_HEADERS, timeout=15,
    )
    data = r.json().get("data")
    if not data or not data.get("trends"):
        return []
    result = []
    prev_price = 0.0
    for line in data["trends"]:
        parts = line.split(",")
        if len(parts) < 4:
            continue
        price = _safe_float(parts[1])
        volume = int(_safe_float(parts[2], 0))
        # 推导买卖性质：价格>前价=买盘，<前价=卖盘，=前价=中性
        if prev_price > 0:
            if price > prev_price:
                nature = "买盘"
            elif price < prev_price:
                nature = "卖盘"
            else:
                nature = "中性盘"
        else:
            nature = "中性盘"
        prev_price = price
        result.append({
            "time": parts[0], "price": price, "volume": volume, "nature": nature,
        })
    return result


def get_intraday(code: str) -> list[dict]:
    """获取个股当日分时成交数据，缓存60秒。mootdx优先，东方财富直接HTTP/AKShare备用。"""
    cache_key = f"intraday:{code}"
    now = time.time()
    if cache_key in _intraday_cache:
        ts, data = _intraday_cache[cache_key]
        if now - ts < _intraday_cache_timeout:
            return data
    # mootdx TCP直连（优先，最稳定）
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = pool.submit(lambda: asyncio.run(astock_data.tdx_transaction(code))).result()
        if result:
            # 转换为前端期望的格式
            converted = []
            for item in result:
                converted.append({
                    "time": item.get("时间", ""),
                    "price": item.get("成交价", 0),
                    "volume": item.get("成交量", 0),
                    "nature": item.get("性质", "中性"),
                })
            _intraday_cache[cache_key] = (now, converted)
            return converted
    except Exception as e:
        logger.warning("mootdx分时API失败: %s", e)
    # 东方财富直接HTTP
    try:
        result = _fetch_intraday_em_direct(code)
        if result:
            _intraday_cache[cache_key] = (now, result)
            return result
    except Exception as e:
        logger.warning("东方财富分时直接API失败: %s", e)
    # AKShare备用
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
        logger.warning("AKShare分时API失败: %s", e)
        return []


# ─── 五档盘口 ───

_bidask_cache: BoundedCache = _register_cache(BoundedCache(256), 10)
_bidask_cache_timeout = 10


def get_bid_ask(code: str) -> dict:
    """获取个股五档盘口数据，缓存10秒。mootdx优先，新浪直接/AKShare备用。"""
    cache_key = f"bidask:{code}"
    now = time.time()
    if cache_key in _bidask_cache:
        ts, data = _bidask_cache[cache_key]
        if now - ts < _bidask_cache_timeout:
            return data
    # mootdx TCP直连（优先，含完整五档）
    try:
        quotes = astock_data._tdx_quotes_sync([code])
        if quotes and code in quotes:
            q = quotes[code]
            result = {}
            cn_nums = ["一", "二", "三", "四", "五"]
            for i in range(1, 6):
                cn = cn_nums[i - 1]
                result[f"buy_{i}"] = q.get(f"买{cn}", 0)
                result[f"buy_{i}_vol"] = int(q.get(f"买量{cn}", 0))
                result[f"sell_{i}"] = q.get(f"卖{cn}", 0)
                result[f"sell_{i}_vol"] = int(q.get(f"卖量{cn}", 0))
            result["latest"] = q.get("最新价", 0)
            result["limit_up"] = 0
            result["limit_down"] = 0
            _bidask_cache[cache_key] = (now, result)
            return result
    except Exception as e:
        logger.warning("mootdx盘口API失败: %s", e)
    # 新浪直接接口
    try:
        result = _fetch_bidask_sina(code)
        if result:
            _bidask_cache[cache_key] = (now, result)
            return result
    except Exception as e:
        logger.warning("新浪盘口直接API失败: %s", e)
    # AKShare备用
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
        logger.warning("AKShare盘口API失败: %s", e)
        return {}


# ─── 资金流向 ───

_fund_flow_cache: BoundedCache = _register_cache(BoundedCache(256), 300)
_fund_flow_cache_timeout = 300


def _sz_sh_prefix(code: str) -> str:
    """返回 sz/sh 前缀。6/9/5开头→sh，其余→sz。"""
    return "sh" if code.startswith(("6", "9", "5")) else "sz"


def _fetch_fund_flow_datacenter(code: str) -> list[dict]:
    """通过东方财富数据中心获取当天资金流向（push2his不可用时的替代方案）。"""
    r = _http_get(
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        params={
            "reportName": "RPT_DMSK_TS_STOCKNEW", "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code}")',
            "pageNumber": "1", "pageSize": "5",
            "sortColumns": "TRADE_DATE", "sortTypes": "-1",
            "source": "WEB", "client": "WEB",
        },
        headers={"User-Agent": "Mozilla/5.0"}, timeout=15,
    )
    d = r.json()
    items = (d.get("result") or {}).get("data") or []
    if not items:
        return []
    item = items[0]
    huge_net = _safe_float(item.get("SUPERDEAL_INFLOW", 0)) - _safe_float(item.get("SUPERDEAL_OUTFLOW", 0))
    big_net = _safe_float(item.get("BIGDEAL_INFLOW", 0)) - _safe_float(item.get("BIGDEAL_OUTFLOW", 0))
    main_net = _safe_float(item.get("PRIME_INFLOW", 0))
    non_main = -main_net  # 中单+小单 ≈ -主力
    close = _safe_float(item.get("CLOSE_PRICE", 0))
    change_pct = _safe_float(item.get("CHANGE_RATE", 0))
    trade_date = item.get("TRADE_DATE", "")
    if trade_date:
        trade_date = trade_date[:10]
    # 近似拆分中单/小单（主力=超大+大，其余按3:2拆分）
    mid_net = round(non_main * 0.6)
    small_net = round(non_main * 0.4)
    return [{
        "date": trade_date, "close": close, "change_pct": change_pct,
        "main_net": main_net, "main_pct": 0,
        "huge_net": huge_net, "huge_pct": 0,
        "big_net": big_net, "big_pct": 0,
        "mid_net": mid_net, "mid_pct": 0,
        "small_net": small_net, "small_pct": 0,
    }]


def _fetch_fund_flow_em_direct(code: str) -> list[dict]:
    """通过东方财富push2his API直接获取资金流向数据。返回空列表表示失败。"""
    secid = _em_secid(code)
    r = _http_get(
        "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
        params={
            "secid": secid, "fields1": "f1,f2,f3",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
            "lmt": "0", "klt": "101",
        },
        headers=_EM_HEADERS, timeout=15,
    )
    data = r.json().get("data")
    if not data or not data.get("klines"):
        return []
    result = []
    for line in data["klines"]:
        # 东方财富字段顺序：日期,主力净流入,小单净流入,中单净流入,大单净流入,超大单净流入,主力%,小单%,中单%,大单%,超大单%,收盘价,涨跌幅
        parts = line.split(",")
        if len(parts) < 13:
            continue
        result.append({
            "date": parts[0], "close": _safe_float(parts[11]),
            "change_pct": _safe_float(parts[12]),
            "main_net": _safe_float(parts[1]), "main_pct": _safe_float(parts[6]),
            "huge_net": _safe_float(parts[5]), "huge_pct": _safe_float(parts[10]),
            "big_net": _safe_float(parts[4]), "big_pct": _safe_float(parts[9]),
            "mid_net": _safe_float(parts[3]), "mid_pct": _safe_float(parts[8]),
            "small_net": _safe_float(parts[2]), "small_pct": _safe_float(parts[7]),
        })
    result.reverse()
    return result


def _parse_fund_flow_ak(df) -> list[dict]:
    """将AKShare资金流向DataFrame转为统一格式。"""
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
    return result


def _fund_flow_history_path(code: str):
    """资金流向历史快照文件路径。"""
    from pathlib import Path
    d = Path(__file__).resolve().parent.parent.parent / "data" / "fund_flow"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{code}.json"


def _load_fund_flow_history(code: str) -> list[dict]:
    """从本地文件加载历史资金流向数据。"""
    import json
    p = _fund_flow_history_path(code)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_fund_flow_history(code: str, data: list[dict]):
    """追加保存资金流向数据到本地文件（去重按日期）。"""
    import json
    existing = _load_fund_flow_history(code)
    existing_dates = {r["date"] for r in existing}
    for r in data:
        if r["date"] not in existing_dates:
            existing.append(r)
            existing_dates.add(r["date"])
    existing.sort(key=lambda x: x["date"])
    # 保留最近120天
    if len(existing) > 120:
        existing = existing[-120:]
    p = _fund_flow_history_path(code)
    p.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")


def get_fund_flow(code: str) -> list[dict]:
    """获取个股资金流向（每日），缓存5分钟。datacenter当天+本地历史优先，push2his备用。"""
    cache_key = f"fundflow:{code}"
    now = time.time()
    if cache_key in _fund_flow_cache:
        ts, data = _fund_flow_cache[cache_key]
        if now - ts < _fund_flow_cache_timeout:
            return data
    # 1. 东财数据中心（当天数据，始终可用）
    current_day = []
    try:
        current_day = _fetch_fund_flow_datacenter(code)
        if current_day:
            _save_fund_flow_history(code, current_day)
    except Exception as e:
        logger.warning("东财数据中心资金流向失败: %s", e)
    # 2. push2his完整历史（含百分比字段，可能DNS不可用）
    full_history = []
    try:
        full_history = _fetch_fund_flow_em_direct(code)
        if full_history:
            _save_fund_flow_history(code, full_history)
    except Exception as e:
        logger.debug("push2his资金流向失败: %s", e)
    # 3. AKShare备用（也依赖push2his，大概率失败）
    if not full_history:
        try:
            market = _sz_sh_prefix(code)
            df = ak.stock_individual_fund_flow(stock=code, market=market)
            if df is not None and not df.empty:
                full_history = _parse_fund_flow_ak(df)
                _save_fund_flow_history(code, full_history)
        except Exception as e:
            logger.debug("AKShare资金流向API失败: %s", e)
    # 合并数据：有完整历史（含百分比）就用，否则用本地历史+当天
    if full_history:
        result = full_history
    else:
        result = _load_fund_flow_history(code)
        if current_day:
            existing_dates = {r["date"] for r in result}
            for r in current_day:
                if r["date"] not in existing_dates:
                    result.append(r)
            result.sort(key=lambda x: x["date"])
    # 用当天datacenter数据补充close/change_pct（历史数据可能缺失）
    if current_day and result:
        today = current_day[0].get("date", "")
        for r in result:
            if r["date"] == today and r.get("close", 0) == 0:
                r["close"] = current_day[0].get("close", 0)
                r["change_pct"] = current_day[0].get("change_pct", 0)
    _fund_flow_cache[cache_key] = (now, result)
    return result


# ─── 分钟K线 ───

_minute_cache: BoundedCache = _register_cache(BoundedCache(512), 60)
_minute_cache_timeout = 60


def get_minute_history(code: str, period: str = "1") -> list[dict]:
    """获取分钟K线数据。period: 1/5/15/30/60，缓存60秒。东方财富直接HTTP优先，AKShare备用。"""
    cache_key = f"minute:{code}:{period}"
    now = time.time()
    if cache_key in _minute_cache:
        ts, data = _minute_cache[cache_key]
        if now - ts < _minute_cache_timeout:
            return data
    # mootdx TCP直连（优先）
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result = pool.submit(lambda: asyncio.run(astock_data.tdx_bars(code, period, 300))).result()
        if result:
            # 转换为前端期望的格式
            converted = []
            for item in result:
                converted.append({
                    "day": item.get("day", ""),
                    "open": str(item.get("open", 0)),
                    "close": str(item.get("close", 0)),
                    "high": str(item.get("high", 0)),
                    "low": str(item.get("low", 0)),
                    "volume": str(item.get("volume", 0)),
                })
            _minute_cache[cache_key] = (now, converted)
            return converted
    except Exception as e:
        logger.warning("mootdx分钟K线API失败: %s", e)
    # 东方财富直接HTTP（复用K线API，改klt参数）
    try:
        klt = _EM_MINUTE_KLT_MAP.get(period, 1)
        secid = _em_secid(code)
        r = _http_get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            params={
                "secid": secid, "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": klt, "fqt": "1", "beg": "0", "end": "20500101",
            },
            headers=_EM_HEADERS, timeout=15,
        )
        data = r.json().get("data")
        if data and data.get("klines"):
            result = []
            for line in data["klines"]:
                parts = line.split(",")
                if len(parts) < 6:
                    continue
                result.append({
                    "day": parts[0], "open": parts[1], "close": parts[2],
                    "high": parts[3], "low": parts[4], "volume": parts[5],
                })
            if result:
                _minute_cache[cache_key] = (now, result)
                return result
    except Exception as e:
        logger.warning("东方财富分钟K线直接API失败: %s", e)
    # AKShare备用
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
        logger.warning("AKShare分钟K线API失败: %s", e)
        return []


# ─── 涨跌排行 ───

def get_ranking(sort_by: str = "涨跌幅", order: str = "desc", limit: int = 50) -> list[dict]:
    """涨跌排行。东财直连优先，行情缓存备用。sort_by: 涨跌幅/换手率/成交额/量比, order: desc/asc"""
    # 东财直连（优先，数据更全更实时）
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            raw = pool.submit(lambda: asyncio.run(astock_data.eastmoney_ranking(sort_by, -1 if order == "desc" else 1, 1, limit))).result()
        if raw:
            # 转换为前端期望的中文key格式
            result = []
            for item in raw:
                result.append({
                    "代码": item.get("代码", ""),
                    "名称": item.get("名称", ""),
                    "最新价": item.get("最新价", 0),
                    "涨跌幅": item.get("涨跌幅", 0),
                    "涨跌额": item.get("涨跌额", 0),
                    "成交量": item.get("成交量", 0),
                    "成交额": item.get("成交额", 0),
                    "换手率": item.get("换手率", 0),
                    "量比": item.get("量比", 0),
                    "市盈率-动态": item.get("市盈率-动态", 0),
                    "市净率": item.get("市净率", 0),
                })
            return result
    except Exception as e:
        logger.warning("东财排行直连失败: %s", e)
    # 行情缓存备用
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
    """获取龙虎榜数据，缓存5分钟。astock_data东财数据中心优先，AKShare备用。"""
    cache_key = "lhb"
    now = time.time()
    if cache_key in _lhb_cache:
        ts, data = _lhb_cache[cache_key]
        if now - ts < _lhb_cache_timeout:
            return data
    # astock_data 东财数据中心（优先）
    try:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            raw = pool.submit(lambda: asyncio.run(astock_data.daily_dragon_tiger())).result()
        stocks = raw.get("个股", [])
        if stocks:
            result = []
            for item in stocks:
                result.append({
                    "代码": item.get("代码", ""),
                    "名称": item.get("名称", ""),
                    "上榜日": raw.get("日期", ""),
                    "收盘价": item.get("收盘价", 0),
                    "涨跌幅": item.get("涨跌幅", 0),
                    "净买额": item.get("净买额", 0),
                    "买入额": item.get("买入额", 0),
                    "卖出额": item.get("卖出额", 0),
                    "成交额": 0,
                    "换手率": item.get("换手率", 0),
                    "上榜原因": item.get("上榜原因", ""),
                })
            _lhb_cache[cache_key] = (now, result)
            return result
    except Exception as e:
        logger.warning("astock_data龙虎榜API失败: %s", e)
    # AKShare备用
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
