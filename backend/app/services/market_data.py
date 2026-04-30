import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict

_BASE_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
_TENCENT_URL = "https://qt.gtimg.cn/q="

_cache_timeout = 60
_last_fetch_time = 0.0
_cached_stocks: list[dict] = []
_fetching = False  # 防缓存击穿标记

# 行业板块缓存（5分钟）
_sector_cache: dict[str, str] = {}  # code -> sector_name
_sector_cache_time = 0.0
_sector_list_cache: list[dict] = []  # [{name, code}]
_sector_list_cache_time = 0.0

# 板块概览缓存（5分钟）
_sector_overview_cache: list[dict] = []
_sector_overview_cache_time = 0.0

# 连接池
_session = requests.Session()
_session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=8))
_session.mount("http://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=8))

_executor = ThreadPoolExecutor(max_workers=8)


def _fetch_page(page: int, page_size: int = 80) -> list[dict]:
    """获取单页行情数据。"""
    try:
        r = _session.get(_BASE_URL, params={
            "page": page, "num": page_size,
            "sort": "changepercent", "asc": 0,
            "node": "hs_a", "symbol": "", "_s_r_a": "page",
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data:
            return []
        return [_parse_sina_item(item) for item in data]
    except Exception:
        return []


def _fetch_all_stocks() -> list[dict]:
    """从新浪财经并发获取全市场A股实时行情。"""
    # 先获取第一页，确定总页数
    first_page = _fetch_page(1)
    if not first_page:
        return []
    page_size = 80
    # 新浪不返回总数，逐页探测；先并发请求后续页
    futures = {_executor.submit(_fetch_page, p): p for p in range(2, 85)}
    all_stocks = list(first_page)
    last_empty = False
    for future in as_completed(futures):
        result = future.result()
        if not result:
            last_empty = True
            continue
        if len(result) < page_size:
            last_empty = True
        all_stocks.extend(result)
    return all_stocks


def _parse_sina_item(item: dict) -> dict:
    """将新浪API返回的单条数据解析为统一格式。"""
    return {
        "代码": item.get("code", ""),
        "名称": item.get("name", ""),
        "最新价": float(item.get("trade", 0) or 0),
        "涨跌额": float(item.get("pricechange", 0) or 0),
        "涨跌幅": float(item.get("changepercent", 0) or 0),
        "今开": float(item.get("open", 0) or 0),
        "最高": float(item.get("high", 0) or 0),
        "最低": float(item.get("low", 0) or 0),
        "昨收": float(item.get("settlement", 0) or 0),
        "买一": float(item.get("buy", 0) or 0),
        "卖一": float(item.get("sell", 0) or 0),
        "成交量": int(float(item.get("volume", 0) or 0)),
        "成交额": float(item.get("amount", 0) or 0),
        "换手率": float(item.get("turnoverratio", 0) or 0),
        "市盈率-动态": float(item.get("per", 0) or 0),
        "市净率": float(item.get("pb", 0) or 0),
        "总市值": float(item.get("mktcap", 0) or 0) * 10000,
        "流通市值": float(item.get("nmc", 0) or 0) * 10000,
    }


def _fetch_tencent_batch(codes: list[str]) -> dict[str, dict]:
    """从腾讯API批量获取量比、52周高低等额外字段。"""
    symbols = []
    for code in codes:
        prefix = "sh" if code.startswith("6") or code.startswith("9") else "sz"
        symbols.append(f"{prefix}{code}")
    result: dict[str, dict] = {}

    def _fetch_tencent_chunk(chunk_symbols: list[str]) -> dict[str, dict]:
        chunk_result: dict[str, dict] = {}
        try:
            r = _session.get(_TENCENT_URL + ",".join(chunk_symbols), timeout=10)
            for line in r.text.split(";"):
                line = line.strip()
                if not line or "~" not in line:
                    continue
                parts = line.split("~")
                if len(parts) < 49:
                    continue
                code = parts[2] if len(parts) > 2 else ""
                if not code:
                    continue
                chunk_result[code] = {
                    "量比": float(parts[43]) if len(parts) > 43 and parts[43] else 0,
                    "52周最高": float(parts[47]) if len(parts) > 47 and parts[47] else 0,
                    "52周最低": float(parts[48]) if len(parts) > 48 and parts[48] else 0,
                }
        except Exception:
            pass
        return chunk_result

    # 并发请求腾讯API，每批50只
    chunks = [symbols[i:i+50] for i in range(0, len(symbols), 50)]
    futures = [_executor.submit(_fetch_tencent_chunk, chunk) for chunk in chunks]
    for future in as_completed(futures):
        result.update(future.result())
    return result


def _fetch_sector_list() -> list[dict]:
    """从新浪获取行业板块列表。"""
    global _sector_list_cache, _sector_list_cache_time
    now = time.time()
    if _sector_list_cache and now - _sector_list_cache_time < 300:
        return _sector_list_cache
    try:
        r = _session.get(
            "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodes",
            timeout=10,
        )
        data = r.json()
        sectors = _extract_sectors(data)
        _sector_list_cache = sectors
        _sector_list_cache_time = now
        return sectors
    except Exception:
        return []


def _extract_sectors(node) -> list[dict]:
    """递归解析新浪嵌套JSON，提取行业列表。"""
    result = []
    if isinstance(node, list):
        for item in node:
            if isinstance(item, str) and item.startswith("new_"):
                pass
            elif isinstance(item, list):
                if len(item) == 3 and isinstance(item[0], str) and isinstance(item[2], str) and item[2].startswith("new_"):
                    result.append({"name": item[0], "code": item[2]})
                else:
                    result.extend(_extract_sectors(item))
    return result


def _fetch_sector_page(sector_code: str, sector_name: str) -> list[tuple[str, str]]:
    """获取单个行业的股票列表，返回 [(code, sector_name)]。"""
    try:
        r = _session.get(_BASE_URL, params={
            "page": 1, "num": 200,
            "sort": "changepercent", "asc": 0,
            "node": sector_code, "symbol": "", "_s_r_a": "page",
        }, timeout=10)
        data = r.json()
        if data:
            return [(item.get("code", ""), sector_name) for item in data if item.get("code")]
    except Exception:
        pass
    return []


def _fetch_sector_mapping() -> dict[str, str]:
    """构建 代码→行业名称 映射（并发获取各行业板块）。"""
    global _sector_cache, _sector_cache_time
    now = time.time()
    if _sector_cache and now - _sector_cache_time < 300:
        return _sector_cache
    sectors = _fetch_sector_list()
    mapping: dict[str, str] = {}
    # 并发请求所有行业
    futures = [
        _executor.submit(_fetch_sector_page, s["code"], s["name"])
        for s in sectors[:48]
    ]
    for future in as_completed(futures):
        for code, name in future.result():
            mapping[code] = name
    _sector_cache = mapping
    _sector_cache_time = now
    return mapping


def get_sector_list() -> list[dict]:
    """返回行业板块列表。"""
    return _fetch_sector_list()


def get_sector_overview() -> list[dict]:
    """返回各行业板块概览：平均涨跌幅、涨跌家数、成交额、新高新低、领涨股。带5分钟缓存。"""
    global _sector_overview_cache, _sector_overview_cache_time
    now = time.time()
    if _sector_overview_cache and now - _sector_overview_cache_time < 300:
        return _sector_overview_cache

    stocks = get_spot_data()
    mapping = _fetch_sector_mapping()

    # 按行业分组
    groups: dict[str, list[dict]] = {}
    for s in stocks:
        sector_name = mapping.get(s["代码"])
        if not sector_name:
            continue
        groups.setdefault(sector_name, []).append(s)

    # 补充52周数据（全量，因为需要统计新高新低）
    all_codes = [s["代码"] for sector_stocks in groups.values() for s in sector_stocks]
    extra = _fetch_tencent_batch(all_codes) if all_codes else {}
    for s in stocks:
        ext = extra.get(s["代码"], {})
        s["52周最高"] = ext.get("52周最高", 0)
        s["52周最低"] = ext.get("52周最低", 0)

    # 聚合计算
    result = []
    for sector_name, sector_stocks in groups.items():
        up = sum(1 for s in sector_stocks if s["涨跌幅"] > 0)
        down = sum(1 for s in sector_stocks if s["涨跌幅"] < 0)
        avg_change = sum(s["涨跌幅"] for s in sector_stocks) / len(sector_stocks) if sector_stocks else 0
        total_amount = sum(s["成交额"] for s in sector_stocks)
        new_high = sum(1 for s in sector_stocks if s.get("52周最高") and s["最新价"] >= s["52周最高"] * 0.95)
        new_low = sum(1 for s in sector_stocks if s.get("52周最低") and s["最新价"] <= s["52周最低"] * 1.05)

        top3 = sorted(sector_stocks, key=lambda s: s["涨跌幅"], reverse=True)[:3]
        result.append({
            "name": sector_name,
            "avg_change_pct": round(avg_change, 2),
            "up_count": up,
            "down_count": down,
            "amount": round(total_amount),
            "new_high_count": new_high,
            "new_low_count": new_low,
            "top_stocks": [
                {"代码": s["代码"], "名称": s["名称"], "涨跌幅": round(s["涨跌幅"], 2)}
                for s in top3
            ],
        })

    result.sort(key=lambda x: x["avg_change_pct"], reverse=True)
    _sector_overview_cache = result
    _sector_overview_cache_time = now
    return result


def get_spot_data() -> list[dict]:
    """获取全市场A股实时行情，带60秒缓存。防止缓存击穿。"""
    global _cached_stocks, _last_fetch_time, _fetching
    now = time.time()
    if _cached_stocks and now - _last_fetch_time < _cache_timeout:
        return _cached_stocks
    if _fetching:
        # 另一个线程正在拉取，返回过期数据或空
        return _cached_stocks
    _fetching = True
    try:
        _cached_stocks = _fetch_all_stocks()
        _last_fetch_time = time.time()
        return _cached_stocks
    finally:
        _fetching = False


def _enrich_with_tencent(stocks: list[dict], codes: list[str]) -> list[dict]:
    """用腾讯数据补充量比、52周高低。"""
    extra = _fetch_tencent_batch(codes)
    for s in stocks:
        ext = extra.get(s["代码"], {})
        s["量比"] = ext.get("量比", 0)
        s["52周最高"] = ext.get("52周最高", 0)
        s["52周最低"] = ext.get("52周最低", 0)
    return stocks


def _enrich_with_consecutive_days(stocks: list[dict]) -> list[dict]:
    """计算连涨/连跌天数（从日K线）。"""
    for s in stocks:
        s["连涨天数"] = 0
        s["连跌天数"] = 0
    return stocks


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
    """筛选低价股，返回分页结果。"""
    stocks = get_spot_data()

    # 行业筛选需要先构建映射
    sector_mapping: dict[str, str] = {}
    if sector:
        sector_mapping = _fetch_sector_mapping()

    # 第一轮：用基础条件快速筛选（不含量比/52周）
    pre_filtered = []
    for s in stocks:
        if not (min_price <= s["最新价"] <= max_price):
            continue
        if min_change_pct is not None and s["涨跌幅"] < min_change_pct:
            continue
        if max_change_pct is not None and s["涨跌幅"] > max_change_pct:
            continue
        if min_turnover_rate is not None and s["换手率"] < min_turnover_rate:
            continue
        if min_volume is not None and s["成交量"] < min_volume:
            continue
        if min_amount is not None and s["成交额"] < min_amount:
            continue
        if min_pe is not None and s["市盈率-动态"] < min_pe:
            continue
        if max_pe is not None and s["市盈率-动态"] > max_pe:
            continue
        if min_pb is not None and s["市净率"] < min_pb:
            continue
        if max_pb is not None and s["市净率"] > max_pb:
            continue
        if min_mktcap is not None and s["总市值"] < min_mktcap:
            continue
        if max_mktcap is not None and s["总市值"] > max_mktcap:
            continue
        if min_nmc is not None and s["流通市值"] < min_nmc:
            continue
        if max_nmc is not None and s["流通市值"] > max_nmc:
            continue
        amplitude = ((s["最高"] - s["最低"]) / s["昨收"] * 100) if s["昨收"] > 0 else 0
        if min_amplitude is not None and amplitude < min_amplitude:
            continue
        if max_amplitude is not None and amplitude > max_amplitude:
            continue
        if exclude_st and ("ST" in s["名称"] or "st" in s["名称"]):
            continue
        if only_st and "ST" not in s["名称"] and "st" not in s["名称"]:
            continue
        if keyword and keyword not in s["名称"] and keyword not in s["代码"]:
            continue
        if sector:
            stock_sector = sector_mapping.get(s["代码"], "")
            if stock_sector != sector:
                continue
        pre_filtered.append(s)

    # 第二轮：对预筛选结果补充腾讯数据（只在需要时）
    need_tencent = min_volume_ratio is not None or max_volume_ratio is not None or near_52week_high or near_52week_low
    if need_tencent and pre_filtered:
        codes = [s["代码"] for s in pre_filtered[:500]]  # 最多500只
        _enrich_with_tencent(pre_filtered[:500], codes)

    # 第三轮：用量比/52周条件过滤
    filtered = []
    for s in pre_filtered:
        if min_volume_ratio is not None and s.get("量比", 0) < min_volume_ratio:
            continue
        if max_volume_ratio is not None and s.get("量比", 0) > max_volume_ratio:
            continue
        if near_52week_high:
            high_52 = s.get("52周最高", 0)
            if not high_52 or s["最新价"] < high_52 * 0.95:
                continue
        if near_52week_low:
            low_52 = s.get("52周最低", 0)
            if not low_52 or s["最新价"] > low_52 * 1.05:
                continue
        filtered.append(s)

    reverse = sort_order == "desc"
    if sort_by in ("量比", "52周最高", "52周最低") and need_tencent:
        filtered.sort(key=lambda s: s.get(sort_by, 0), reverse=reverse)
    else:
        filtered.sort(key=lambda s: s.get(sort_by, 0), reverse=reverse)

    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": filtered[start:end],
    }


def get_stock_detail(code: str) -> dict:
    """获取单只股票详细行情。并发拉取腾讯数据、连涨跌天数、行业映射。"""
    for s in get_spot_data():
        if s["代码"] == code:
            f_tencent = _executor.submit(_fetch_tencent_batch, [code])
            f_consec = _executor.submit(compute_consecutive_days, code)
            f_sector = _executor.submit(_fetch_sector_mapping)

            ext = f_tencent.result().get(code, {})
            s["量比"] = ext.get("量比", 0)
            s["52周最高"] = ext.get("52周最高", 0)
            s["52周最低"] = ext.get("52周最低", 0)

            consec = f_consec.result()
            s["连涨天数"] = consec["连涨天数"]
            s["连跌天数"] = consec["连跌天数"]

            s["行业"] = f_sector.result().get(code, "")
            return s

    prefix = "sh" if code.startswith("6") else "sz"
    try:
        r = _session.get(f"https://qt.gtimg.cn/q={prefix}{code}", timeout=10)
        text = r.text
        if '~' not in text:
            return {}
        parts = text.split('~')
        return {
            "代码": code,
            "名称": parts[1] if len(parts) > 1 else "",
            "最新价": float(parts[3]) if len(parts) > 3 and parts[3] else 0,
            "昨收": float(parts[4]) if len(parts) > 4 and parts[4] else 0,
            "今开": float(parts[5]) if len(parts) > 5 and parts[5] else 0,
            "成交量": int(float(parts[6])) if len(parts) > 6 and parts[6] else 0,
            "最高": float(parts[33]) if len(parts) > 33 and parts[33] else 0,
            "最低": float(parts[34]) if len(parts) > 34 and parts[34] else 0,
            "涨跌额": float(parts[31]) if len(parts) > 31 and parts[31] else 0,
            "涨跌幅": float(parts[32]) if len(parts) > 32 and parts[32] else 0,
            "买一": float(parts[9]) if len(parts) > 9 and parts[9] else 0,
            "卖一": float(parts[18]) if len(parts) > 18 and parts[18] else 0,
            "成交额": float(parts[37]) if len(parts) > 37 and parts[37] else 0,
            "换手率": float(parts[38]) if len(parts) > 38 and parts[38] else 0,
            "市盈率-动态": float(parts[39]) if len(parts) > 39 and parts[39] else 0,
            "总市值": float(parts[45]) if len(parts) > 45 and parts[45] else 0,
            "流通市值": float(parts[44]) if len(parts) > 44 and parts[44] else 0,
            "市净率": float(parts[46]) if len(parts) > 46 and parts[46] else 0,
            "量比": float(parts[43]) if len(parts) > 43 and parts[43] else 0,
            "52周最高": float(parts[47]) if len(parts) > 47 and parts[47] else 0,
            "52周最低": float(parts[48]) if len(parts) > 48 and parts[48] else 0,
            "连涨天数": 0, "连跌天数": 0, "行业": "",
        }
    except Exception:
        return {}


def get_stock_history(code: str, period: str = "daily", start_date: str = "20250101") -> list[dict]:
    """获取单只股票历史K线（通过新浪API）。"""
    prefix = "sh" if code.startswith("6") else "sz"
    symbol = f"{prefix}{code}"
    url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    scale_map = {"daily": "240", "weekly": "1200"}
    scale = scale_map.get(period, "240")
    datalen = "250" if period == "monthly" else "120"

    try:
        r = _session.get(url, params={
            "symbol": symbol, "scale": scale,
            "ma": "no", "datalen": datalen,
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data:
            return []
        if period == "monthly":
            return _aggregate_monthly(data)
        return data
    except Exception:
        return []


def _aggregate_monthly(daily_data: list[dict]) -> list[dict]:
    """将日K数据聚合为月K。"""
    months: OrderedDict[str, dict] = OrderedDict()
    for d in daily_data:
        month_key = d["day"][:7]
        if month_key not in months:
            months[month_key] = {
                "day": month_key + "-01",
                "open": d["open"],
                "high": d["high"],
                "low": d["low"],
                "close": d["close"],
                "volume": d["volume"],
            }
        else:
            m = months[month_key]
            m["high"] = str(max(float(m["high"]), float(d["high"])))
            m["low"] = str(min(float(m["low"]), float(d["low"])))
            m["close"] = d["close"]
            m["volume"] = str(int(float(m["volume"])) + int(float(d["volume"])))
    return list(months.values())


# ─── 大盘指数 ───

_INDEX_CODES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
}


def get_index_data() -> list[dict]:
    """获取主要大盘指数当前数据。"""
    symbols = ",".join(_INDEX_CODES.keys())
    try:
        r = _session.get(f"https://hq.sinajs.cn/list={symbols}", headers={
            "Referer": "https://finance.sina.com.cn",
        }, timeout=10)
        r.encoding = "gbk"
        lines = r.text.strip().split("\n")

        result = []
        for line in lines:
            parts = line.split('="')
            if len(parts) < 2:
                continue
            code = parts[0].split("hq_str_")[1]
            data = parts[1].rstrip('";').split(",")
            if len(data) < 32:
                continue
            name = data[0]
            yesterday = float(data[2])
            current = float(data[3])
            change_pct = (current - yesterday) / yesterday * 100 if yesterday else 0
            result.append({
                "code": code,
                "name": name,
                "current": current,
                "yesterday": yesterday,
                "change_pct": round(change_pct, 2),
            })
        return result
    except Exception:
        return []
