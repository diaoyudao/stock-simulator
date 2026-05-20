"""
a-stock-data 数据源适配层
基于 https://github.com/simonlin1212/a-stock-data V3.0 提取并适配。
直连 mootdx(TCP) + 腾讯/百度/东财/新浪 HTTP，绕过 AKShare 封装层。
所有函数异步封装，输出格式与 market_data.py 兼容（中文key）。
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests as _req
from mootdx.quotes import Quotes

logger = logging.getLogger(__name__)

# ─── 常量 ───

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

_BAIDU_PAE_HEADERS = {
    "Host": "finance.pae.baidu.com",
    "User-Agent": UA,
    "Accept": "application/vnd.finance-web.v1+json",
    "Origin": "https://gushitong.baidu.com",
    "Referer": "https://gushitong.baidu.com/",
}


# ─── HTTP 工具 ───

def _http_get(url, retries=2, **kwargs):
    s = _req.Session()
    s.trust_env = False
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    retry = Retry(total=retries, backoff_factor=0.5, status_forcelist=[502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s.get(url, **kwargs)


def get_prefix(code: str) -> str:
    """6位代码 → 市场前缀"""
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith("8"):
        return "bj"
    return "sz"


# ─── mootdx 连接管理 ───

_tdx_client = None
_tdx_lock = asyncio.Lock()


def _get_tdx_client_sync():
    """获取 mootdx 连接（同步）"""
    global _tdx_client
    if _tdx_client is not None:
        try:
            # 简单健康检查
            test = _tdx_client.quotes(symbol=["000001"])
            if test is not None and len(test) > 0:
                return _tdx_client
        except Exception:
            _tdx_client = None

    client = Quotes.factory(market="std")
    _tdx_client = client
    return client


async def get_tdx_client():
    """获取 mootdx 连接（异步安全）"""
    async with _tdx_lock:
        return await asyncio.get_event_loop().run_in_executor(None, _get_tdx_client_sync)


async def _run_sync(func, *args, **kwargs):
    """同步函数转异步执行"""
    return await asyncio.get_event_loop().run_in_executor(None, lambda: func(*args, **kwargs))


# ─── Layer 1: 行情层 ───

# ── 1.1 mootdx K线 ──

_CATEGORY_MAP = {
    "daily": 4, "weekly": 5, "monthly": 6,
    "1": 7, "5": 8, "15": 9, "30": 10, "60": 11,
}


def _tdx_bars_sync(symbol: str, period: str = "daily", offset: int = 300):
    """mootdx K线（同步）"""
    client = _get_tdx_client_sync()
    category = _CATEGORY_MAP.get(period, 4)
    df = client.bars(symbol=symbol, category=category, offset=offset)
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        item = {
            "day": str(row.get("datetime", ""))[:10],
            "open": float(row.get("open", 0)),
            "close": float(row.get("close", 0)),
            "high": float(row.get("high", 0)),
            "low": float(row.get("low", 0)),
            "volume": float(row.get("vol", 0)),
            "amount": float(row.get("amount", 0)),
        }
        # 日K线补充涨跌幅
        if period in ("daily", "weekly", "monthly") and item["open"] > 0:
            prev_close = float(row.get("last_close", 0)) or item["open"]
            item["percent"] = round((item["close"] - prev_close) / prev_close * 100, 2) if prev_close > 0 else 0
            item["ma_price5"] = 0
            item["ma_price10"] = 0
            item["ma_price20"] = 0
        result.append(item)
    return result


async def tdx_bars(symbol: str, period: str = "daily", offset: int = 300):
    """mootdx K线（异步）"""
    return await _run_sync(_tdx_bars_sync, symbol, period, offset)


# ── 1.2 mootdx 实时报价 + 五档盘口 ──

def _tdx_quotes_sync(symbols: list[str]):
    """mootdx 实时报价含五档盘口（同步）"""
    client = _get_tdx_client_sync()
    df = client.quotes(symbol=symbols)
    if df is None or df.empty:
        return {}
    result = {}
    for _, row in df.iterrows():
        code = str(row.get("code", "")).zfill(6)
        price = float(row.get("price", 0) or 0)
        last_close = float(row.get("last_close", 0) or 0)
        result[code] = {
            "代码": code,
            "名称": "",
            "最新价": price,
            "昨收": last_close,
            "今开": float(row.get("open", 0) or 0),
            "最高": float(row.get("high", 0) or 0),
            "最低": float(row.get("low", 0) or 0),
            "成交量": float(row.get("vol", 0) or 0),
            "成交额": float(row.get("amount", 0) or 0),
            "买一": float(row.get("bid1", 0) or 0),
            "买二": float(row.get("bid2", 0) or 0),
            "买三": float(row.get("bid3", 0) or 0),
            "买四": float(row.get("bid4", 0) or 0),
            "买五": float(row.get("bid5", 0) or 0),
            "卖一": float(row.get("ask1", 0) or 0),
            "卖二": float(row.get("ask2", 0) or 0),
            "卖三": float(row.get("ask3", 0) or 0),
            "卖四": float(row.get("ask4", 0) or 0),
            "卖五": float(row.get("ask5", 0) or 0),
            "买量一": float(row.get("bid_vol1", 0) or 0),
            "买量二": float(row.get("bid_vol2", 0) or 0),
            "买量三": float(row.get("bid_vol3", 0) or 0),
            "买量四": float(row.get("bid_vol4", 0) or 0),
            "买量五": float(row.get("bid_vol5", 0) or 0),
            "卖量一": float(row.get("ask_vol1", 0) or 0),
            "卖量二": float(row.get("ask_vol2", 0) or 0),
            "卖量三": float(row.get("ask_vol3", 0) or 0),
            "卖量四": float(row.get("ask_vol4", 0) or 0),
            "卖量五": float(row.get("ask_vol5", 0) or 0),
        }
        if last_close > 0:
            result[code]["涨跌幅"] = round((price - last_close) / last_close * 100, 2)
            result[code]["涨跌额"] = round(price - last_close, 2)
        else:
            result[code]["涨跌幅"] = 0
            result[code]["涨跌额"] = 0
    return result


async def tdx_quotes(symbols: list[str]):
    """mootdx 实时报价含五档盘口（异步）"""
    return await _run_sync(_tdx_quotes_sync, symbols)


# ── 1.3 mootdx 逐笔成交（分时） ──

def _tdx_transaction_sync(symbol: str, date: str = None):
    """mootdx 分时逐笔成交（同步）"""
    client = _get_tdx_client_sync()
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    df = client.transaction(symbol=symbol, date=date)
    if df is None or df.empty:
        return []
    result = []
    for _, row in df.iterrows():
        result.append({
            "时间": str(row.get("time", "")),
            "成交价": float(row.get("price", 0) or 0),
            "成交量": int(row.get("vol", 0) or 0),
            "性质": ["买", "卖", "中性"][int(row.get("buyorsell", 2))] if pd.notna(row.get("buyorsell")) else "中性",
        })
    return result


async def tdx_transaction(symbol: str, date: str = None):
    """mootdx 分时逐笔成交（异步）"""
    return await _run_sync(_tdx_transaction_sync, symbol, date)


# ── 1.4 mootdx 财务快照 ──

def _tdx_finance_sync(symbol: str):
    """mootdx 37字段季报快照（同步）"""
    client = _get_tdx_client_sync()
    market = 1 if symbol.startswith("6") else 0
    df = client.finance(symbol=symbol)
    if df is None or df.empty:
        return {}
    row = df.iloc[0]
    return {
        "代码": symbol,
        "每股收益": float(row.get("eps", 0) or 0),
        "每股净资产": float(row.get("bvps", 0) or 0),
        "净资产收益率": float(row.get("roe", 0) or 0),
        "净利润": float(row.get("profit", 0) or 0),
        "主营收入": float(row.get("income", 0) or 0),
        "总股本": float(row.get("zongguben", 0) or 0),
        "流通股本": float(row.get("liutongguben", 0) or 0),
        "每股公积金": float(row.get("meigugongjijin", 0) or 0),
        "每股未分配利润": float(row.get("meiguweifeipeili", 0) or 0),
    }


async def tdx_finance(symbol: str):
    """mootdx 37字段季报快照（异步）"""
    return await _run_sync(_tdx_finance_sync, symbol)


# ── 1.5 腾讯财经批量实时行情 ──

def _tencent_quote_sync(codes: list[str], batch_size: int = 500) -> list[dict]:
    """腾讯财经批量实时行情（同步），80股/批，绕过代理。
    返回 list[dict]，每只股票一个字典，字段与 market_data.py 兼容。"""
    prefixed_map = {}
    for c in codes:
        if c.startswith(("6", "9")):
            p = f"sh{c}"
        elif c.startswith("8"):
            p = f"bj{c}"
        else:
            p = f"sz{c}"
        prefixed_map[p] = c

    all_prefixed = list(prefixed_map.keys())
    result = []
    for i in range(0, len(all_prefixed), batch_size):
        batch = all_prefixed[i:i + batch_size]
        url = "https://qt.gtimg.cn/q=" + ",".join(batch)
        try:
            r = _http_get(url, headers={"User-Agent": UA}, timeout=10)
            r.encoding = "gbk"
            data = r.text
        except Exception as e:
            logger.warning("腾讯批量行情请求失败: %s", e)
            continue
        for line in data.strip().split(";"):
            if not line.strip() or "=" not in line or '"' not in line:
                continue
            key = line.split("=")[0].split("_")[-1]
            vals = line.split('"')[1].split("~")
            if len(vals) < 50:
                continue
            code = key[2:]
            try:
                price = float(vals[3]) if vals[3] else 0
                last_close = float(vals[4]) if vals[4] else 0
            except (ValueError, IndexError):
                continue
            result.append({
                "代码": code,
                "名称": vals[1],
                "最新价": price,
                "昨收": last_close,
                "今开": _safe_val(vals, 5),
                "成交量": _safe_int(vals, 6),
                "涨跌额": _safe_val(vals, 31),
                "涨跌幅": _safe_val(vals, 32),
                "最高": _safe_val(vals, 33),
                "最低": _safe_val(vals, 34),
                "成交额": _safe_val(vals, 37),
                "换手率": _safe_val(vals, 38),
                "市盈率-动态": _safe_val(vals, 39),
                "振幅": _safe_val(vals, 43),
                "总市值": _safe_val(vals, 44),
                "流通市值": _safe_val(vals, 45),
                "市净率": _safe_val(vals, 46),
                "涨停价": _safe_val(vals, 47),
                "跌停价": _safe_val(vals, 48),
                "量比": _safe_val(vals, 49),
                "买一": price,
                "卖一": price,
            })
    return result


def _safe_val(vals, idx, default=0.0):
    try:
        return float(vals[idx]) if vals[idx] else default
    except (ValueError, IndexError):
        return default


def _safe_int(vals, idx, default=0):
    try:
        return int(float(vals[idx])) if vals[idx] else default
    except (ValueError, IndexError):
        return default


async def tencent_quote(codes: list[str]):
    """腾讯财经批量实时行情（异步），返回 list[dict]"""
    return await _run_sync(_tencent_quote_sync, codes)


# ─── Layer 3: 信号层 ───

# ── 3.1 百度个股资金流向（分钟级） ──

def _baidu_fund_flow_realtime_sync(code: str, date: str = None):
    """百度分钟级资金流向（同步）"""
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    url = f"https://finance.pae.baidu.com/vapi/v1/fundflow?code={code}&market=ab&date={date}&finClientType=pc"
    r = _http_get(url, headers=_BAIDU_PAE_HEADERS, timeout=10)
    d = r.json()
    if str(d.get("ResultCode", -1)) != "0":
        return []
    raw = d.get("Result", {}).get("update_data", "")
    if not raw:
        return []
    rows = []
    for segment in raw.split(";"):
        parts = segment.split(",")
        if len(parts) >= 9:
            rows.append({
                "时间": parts[0],
                "主力净流入": float(parts[2]) if parts[2] else 0,
                "散户净流入": float(parts[3]) if parts[3] else 0,
                "超大单净流入": float(parts[4]) if parts[4] else 0,
                "大单净流入": float(parts[5]) if parts[5] else 0,
                "价格": float(parts[8]) if parts[8] else 0,
            })
    return rows


async def baidu_fund_flow_realtime(code: str, date: str = None):
    """百度分钟级资金流向（异步）"""
    return await _run_sync(_baidu_fund_flow_realtime_sync, code, date)


# ── 3.2 百度个股资金流向（日级） ──

def _baidu_fund_flow_history_sync(code: str, days: int = 20):
    """百度日级资金流向（同步）"""
    url = f"https://finance.pae.baidu.com/vapi/v1/fundsortlist?code={code}&market=ab&pn=0&rn={days}&finClientType=pc"
    r = _http_get(url, headers=_BAIDU_PAE_HEADERS, timeout=10)
    d = r.json()
    if str(d.get("ResultCode", -1)) != "0":
        return []
    rows = []
    result_data = d.get("Result") or {}
    if isinstance(result_data, str):
        return []
    for item in result_data.get("list", []):
        rows.append({
            "日期": item.get("showtime", ""),
            "收盘价": item.get("closepx", ""),
            "涨跌幅": item.get("ratio", ""),
            "超大单净流入": item.get("superNetIn", ""),
            "大单净流入": item.get("largeNetIn", ""),
            "中单净流入": item.get("mediumNetIn", ""),
            "小单净流入": item.get("littleNetIn", ""),
            "主力净流入": item.get("extMainIn", ""),
        })
    return rows


async def baidu_fund_flow_history(code: str, days: int = 20):
    """百度日级资金流向（异步）"""
    return await _run_sync(_baidu_fund_flow_history_sync, code, days)


# ── 3.3 东财资金流120日 ──

def _stock_fund_flow_120d_sync(code: str):
    """东财个股资金流120日（同步，优先push2his，降级datacenter当天）"""
    market_code = 1 if code.startswith("6") else 0
    # 尝试 push2his（完整历史，可能DNS不可用）
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "secid": f"{market_code}.{code}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "lmt": "120",
    }
    try:
        r = _http_get(url, params=params, headers={"User-Agent": UA}, timeout=15)
        d = r.json()
        klines = d.get("data", {}).get("klines", [])
        if klines:
            rows = []
            for line in klines:
                parts = line.split(",")
                if len(parts) >= 7:
                    rows.append({
                        "日期": parts[0],
                        "主力净流入": float(parts[1]) if parts[1] != "-" else 0,
                        "小单净流入": float(parts[2]) if parts[2] != "-" else 0,
                        "中单净流入": float(parts[3]) if parts[3] != "-" else 0,
                        "大单净流入": float(parts[4]) if parts[4] != "-" else 0,
                        "超大单净流入": float(parts[5]) if parts[5] != "-" else 0,
                    })
            return rows
    except Exception:
        pass
    # 降级：datacenter 当天数据
    try:
        dc = _eastmoney_datacenter_sync(
            "RPT_DMSK_TS_STOCKNEW", "ALL",
            f'(SECURITY_CODE="{code}")', 5,
            "TRADE_DATE", "-1",
        )
        if dc:
            item = dc[0]
            huge = float(item.get("SUPERDEAL_INFLOW", 0)) - float(item.get("SUPERDEAL_OUTFLOW", 0))
            big = float(item.get("BIGDEAL_INFLOW", 0)) - float(item.get("BIGDEAL_OUTFLOW", 0))
            prime = float(item.get("PRIME_INFLOW", 0))
            trade_date = str(item.get("TRADE_DATE", ""))[:10]
            return [{
                "日期": trade_date,
                "主力净流入": prime,
                "超大单净流入": huge,
                "大单净流入": big,
                "中单净流入": round(-prime * 0.6),
                "小单净流入": round(-prime * 0.4),
            }]
    except Exception:
        pass
    return []


async def stock_fund_flow_120d(code: str):
    """东财个股资金流120日（异步）"""
    return await _run_sync(_stock_fund_flow_120d_sync, code)


# ── 3.4 龙虎榜 ──

def _eastmoney_datacenter_sync(report_name: str, columns: str = "ALL",
                                filter_str: str = "", page_size: int = 50,
                                sort_columns: str = "", sort_types: str = "-1") -> list[dict]:
    """东财数据中心统一查询（同步）"""
    params = {
        "reportName": report_name, "columns": columns,
        "filter": filter_str, "pageNumber": "1", "pageSize": str(page_size),
        "sortColumns": sort_columns, "sortTypes": sort_types,
        "source": "WEB", "client": "WEB",
    }
    r = _http_get(DATACENTER_URL, params=params, headers={"User-Agent": UA}, timeout=15)
    d = r.json()
    if d.get("result") and d["result"].get("data"):
        return d["result"]["data"]
    return []


def _dragon_tiger_board_sync(code: str, trade_date: str = None, look_back: int = 30):
    """龙虎榜详情（同步）"""
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=look_back)).strftime("%Y-%m-%d")

    # 上榜记录
    records_data = _eastmoney_datacenter_sync(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str=f"(TRADE_DATE>='{start}')(TRADE_DATE<='{trade_date}')(SECURITY_CODE=\"{code}\")",
        page_size=50,
        sort_columns="TRADE_DATE", sort_types="-1",
    )
    records = []
    for row in records_data:
        records.append({
            "日期": str(row.get("TRADE_DATE", ""))[:10],
            "上榜原因": row.get("EXPLANATION", ""),
            "净买额": round((row.get("BILLBOARD_NET_AMT") or 0) / 10000, 1),
            "换手率": round(float(row.get("TURNOVERRATE") or 0), 2),
        })

    # 买卖席位
    seats = {"买席位": [], "卖席位": []}
    if records:
        latest_date = records[0]["日期"]
        for side, report_name, sort_col in [
            ("买席位", "RPT_BILLBOARD_DAILYDETAILSBUY", "BUY"),
            ("卖席位", "RPT_BILLBOARD_DAILYDETAILSSELL", "SELL"),
        ]:
            data = _eastmoney_datacenter_sync(
                report_name,
                filter_str=f"(TRADE_DATE='{latest_date}')(SECURITY_CODE=\"{code}\")",
                page_size=10,
                sort_columns=sort_col, sort_types="-1",
            )
            for row in data[:5]:
                seats[side].append({
                    "营业部": row.get("OPERATEDEPT_NAME", ""),
                    "买入额": round((row.get("BUY") or 0) / 10000, 1),
                    "卖出额": round((row.get("SELL") or 0) / 10000, 1),
                    "净额": round((row.get("NET") or 0) / 10000, 1),
                })

    return {"上榜记录": records, "席位": seats}


async def dragon_tiger_board(code: str, trade_date: str = None, look_back: int = 30):
    """龙虎榜详情（异步）"""
    return await _run_sync(_dragon_tiger_board_sync, code, trade_date, look_back)


def _daily_dragon_tiger_sync(trade_date: str = None, min_net_buy: float = None):
    """全市场龙虎榜（同步）"""
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")
    data = _eastmoney_datacenter_sync(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str=f"(TRADE_DATE>='{trade_date}')(TRADE_DATE<='{trade_date}')",
        page_size=500,
        sort_columns="BILLBOARD_NET_AMT", sort_types="-1",
    )
    if not data:
        return {"日期": trade_date, "总数": 0, "个股": []}

    actual_date = str(data[0].get("TRADE_DATE", ""))[:10] if data else trade_date
    stocks = []
    for row in data:
        net_buy = (row.get("BILLBOARD_NET_AMT") or 0) / 10000
        if min_net_buy is not None and net_buy < min_net_buy:
            continue
        stocks.append({
            "代码": row.get("SECURITY_CODE", ""),
            "名称": row.get("SECURITY_NAME_ABBR", ""),
            "上榜原因": row.get("EXPLANATION", ""),
            "收盘价": row.get("CLOSE_PRICE") or 0,
            "涨跌幅": round(float(row.get("CHANGE_RATE") or 0), 2),
            "净买额": round(net_buy, 1),
            "买入额": round((row.get("BILLBOARD_BUY_AMT") or 0) / 10000, 1),
            "卖出额": round((row.get("BILLBOARD_SELL_AMT") or 0) / 10000, 1),
            "换手率": round(float(row.get("TURNOVERRATE") or 0), 2),
        })
    return {"日期": actual_date, "总数": len(stocks), "个股": stocks}


async def daily_dragon_tiger(trade_date: str = None, min_net_buy: float = None):
    """全市场龙虎榜（异步）"""
    return await _run_sync(_daily_dragon_tiger_sync, trade_date, min_net_buy)


# ── 3.5 行业板块排名 ──

def _industry_comparison_sync(top_n: int = 30):
    """行业板块涨跌排名（同步）"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "100", "po": "1", "np": "1",
        "fltt": "2", "invt": "2",
        "fs": "m:90+t:2",
        "fields": "f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141,f207",
    }
    r = _http_get(url, params=params, headers={"User-Agent": UA}, timeout=15)
    d = r.json()
    items = d.get("data", {}).get("diff", [])
    if not items:
        return {"涨幅前N": [], "跌幅前N": [], "总数": 0}

    rows = []
    for i, item in enumerate(items):
        rows.append({
            "排名": i + 1,
            "名称": item.get("f14", ""),
            "涨跌幅": item.get("f3", 0),
            "代码": item.get("f12", ""),
            "上涨家数": item.get("f104", 0),
            "下跌家数": item.get("f105", 0),
            "领涨股": item.get("f140", ""),
            "领涨涨幅": item.get("f136", 0),
        })
    return {
        "涨幅前N": rows[:top_n],
        "跌幅前N": rows[-top_n:],
        "总数": len(rows),
    }


async def industry_comparison(top_n: int = 30):
    """行业板块涨跌排名（异步）"""
    return await _run_sync(_industry_comparison_sync, top_n)


# ─── Layer 5: 新闻层 ───

def _eastmoney_stock_news_sync(code: str, page_size: int = 20):
    """东财个股资讯 JSONP（同步）"""
    cb = "jQuery_news"
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    inner_params = json.dumps({
        "uid": "", "keyword": code,
        "type": ["cmsArticleWebOld"], "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
                  "pageIndex": 1, "pageSize": page_size, "preTag": "", "postTag": ""}},
    }, separators=(",", ":"))
    params = {"cb": cb, "param": inner_params}
    headers = {"User-Agent": UA, "Referer": "https://so.eastmoney.com/"}
    r = _http_get(url, params=params, headers=headers, timeout=15)

    text = r.text
    json_str = text[text.index("(") + 1: text.rindex(")")]
    d = json.loads(json_str)

    rows = []
    raw_articles = d.get("result", {}).get("cmsArticleWebOld", [])
    # 兼容 list 和 dict 两种返回格式
    if isinstance(raw_articles, dict):
        articles = raw_articles.get("list", [])
    elif isinstance(raw_articles, list):
        articles = raw_articles
    else:
        articles = []
    for a in articles:
        rows.append({
            "标题": re.sub(r"<[^>]+>", "", a.get("title", "")),
            "内容": re.sub(r"<[^>]+>", "", a.get("content", ""))[:200],
            "时间": a.get("date", ""),
            "来源": a.get("mediaName", ""),
            "链接": a.get("url", ""),
        })
    return rows


async def eastmoney_stock_news(code: str, page_size: int = 20):
    """东财个股资讯 JSONP（异步）"""
    return await _run_sync(_eastmoney_stock_news_sync, code, page_size)


# ─── Layer 6: 基础数据层 ───

# ── 6.1 东财个股基本面 ──

def _eastmoney_stock_info_sync(code: str):
    """东财个股基本面（同步）"""
    market_code = 1 if code.startswith("6") else 0
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "fltt": "2", "invt": "2",
        "fields": "f57,f58,f84,f85,f127,f116,f117,f189,f43",
        "secid": f"{market_code}.{code}",
    }
    r = _http_get(url, params=params, headers={"User-Agent": UA}, timeout=10)
    d = r.json().get("data", {})
    return {
        "代码": d.get("f57", ""),
        "名称": d.get("f58", ""),
        "行业": d.get("f127", ""),
        "总股本": d.get("f84", 0),
        "流通股": d.get("f85", 0),
        "总市值": d.get("f116", 0),
        "流通市值": d.get("f117", 0),
        "上市日期": str(d.get("f189", "")),
    }


async def eastmoney_stock_info(code: str):
    """东财个股基本面（异步）"""
    return await _run_sync(_eastmoney_stock_info_sync, code)


# ── 6.2 新浪财报三表 ──

def _sina_financial_report_sync(code: str, report_type: str = "lrb"):
    """新浪财报三表（同步）
    report_type: "fzb"(资产负债表) / "lrb"(利润表) / "llb"(现金流量表)
    """
    prefix = get_prefix(code)
    url = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
    params = {
        "paperCode": f"{prefix}{code}",
        "source": report_type,
        "type": "0", "page": "1", "num": "20",
    }
    r = _http_get(url, params=params, headers={"User-Agent": UA}, timeout=15)
    d = r.json()
    result = d.get("result", {}).get("data", {})
    items = result.get(report_type, [])
    return items if isinstance(items, list) else []


async def sina_financial_report(code: str, report_type: str = "lrb"):
    """新浪财报三表（异步）"""
    return await _run_sync(_sina_financial_report_sync, code, report_type)


# ─── 补全功能 ───

# ── 涨跌排行（东财直连） ──

_SORT_FIELD_MAP = {
    "涨幅": "f3", "跌幅": "f3", "换手率": "f8",
    "成交额": "f6", "量比": "f10",
}


def _eastmoney_ranking_sync(sort_field: str = "涨幅", order: int = -1,
                             page: int = 1, page_size: int = 50):
    """涨跌排行（东财直连，同步）
    order: -1=降序, 1=升序
    """
    fid = _SORT_FIELD_MAP.get(sort_field, "f3")
    # 跌幅用升序
    if sort_field == "跌幅":
        order = 1

    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": str(page), "pz": str(page_size), "po": str(order), "np": "1",
        "fltt": "2", "invt": "2",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f115",
    }
    # 排序字段
    params["fid"] = fid
    r = _http_get(url, params=params, headers={"User-Agent": UA}, timeout=15)
    d = r.json()
    items = d.get("data", {}).get("diff", [])
    if not items:
        return []

    result = []
    for item in items:
        price = item.get("f2", 0)
        if not price or price == "-":
            continue
        result.append({
            "代码": item.get("f12", ""),
            "名称": item.get("f14", ""),
            "最新价": float(price) if price != "-" else 0,
            "涨跌幅": float(item.get("f3", 0) or 0),
            "涨跌额": float(item.get("f4", 0) or 0),
            "成交量": float(item.get("f5", 0) or 0),
            "成交额": float(item.get("f6", 0) or 0),
            "振幅": float(item.get("f7", 0) or 0),
            "换手率": float(item.get("f8", 0) or 0),
            "市盈率-动态": float(item.get("f9", 0) or 0),
            "量比": float(item.get("f10", 0) or 0),
            "最高": float(item.get("f15", 0) or 0),
            "最低": float(item.get("f16", 0) or 0),
            "今开": float(item.get("f17", 0) or 0),
            "昨收": float(item.get("f18", 0) or 0),
            "总市值": float(item.get("f20", 0) or 0),
            "流通市值": float(item.get("f21", 0) or 0),
            "市净率": float(item.get("f23", 0) or 0),
        })
    return result


async def eastmoney_ranking(sort_field: str = "涨幅", order: int = -1,
                             page: int = 1, page_size: int = 50):
    """涨跌排行（异步）"""
    return await _run_sync(_eastmoney_ranking_sync, sort_field, order, page, page_size)


# ── ETF 持仓（东财数据中心直连） ──

def _etf_holdings_sync(code: str):
    """ETF前十大持仓（东财数据中心，同步）"""
    data = _eastmoney_datacenter_sync(
        "RPT_FUND_TOP10ASSETS",
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=10,
        sort_columns="REPORT_DATE", sort_types="-1",
    )
    rows = []
    for row in data:
        rows.append({
            "股票代码": row.get("STOCK_CODE", ""),
            "股票名称": row.get("STOCK_NAME", ""),
            "持仓市值": row.get("HOLD_MARKET_CAP", 0),
            "占净值比": row.get("HOLD_RATIO", 0),
            "较上期增减": row.get("HOLD_CHANGE", 0),
        })
    return rows


async def etf_holdings(code: str):
    """ETF前十大持仓（异步）"""
    return await _run_sync(_etf_holdings_sync, code)


# ── ETF 净值（东财直连） ──

def _etf_nav_sync(code: str):
    """ETF净值数据（东财，同步）"""
    market_code = 1 if code.startswith("5") or code.startswith("6") else 0
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": f"{market_code}.{code}",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": "101", "fqt": "1", "end": "20500101", "lmt": "30",
    }
    r = _http_get(url, params=params, headers={"User-Agent": UA}, timeout=15)
    d = r.json()
    klines = d.get("data", {}).get("klines", [])
    rows = []
    for line in klines:
        parts = line.split(",")
        if len(parts) >= 7:
            rows.append({
                "日期": parts[0],
                "开盘": float(parts[1]) if parts[1] != "-" else 0,
                "收盘": float(parts[2]) if parts[2] != "-" else 0,
                "最高": float(parts[3]) if parts[3] != "-" else 0,
                "最低": float(parts[4]) if parts[4] != "-" else 0,
                "成交量": float(parts[5]) if parts[5] != "-" else 0,
                "成交额": float(parts[6]) if parts[6] != "-" else 0,
            })
    return rows


async def etf_nav(code: str):
    """ETF净值数据（异步）"""
    return await _run_sync(_etf_nav_sync, code)


# ── ETF 资产配置（东财数据中心直连） ──

def _etf_allocation_sync(code: str):
    """ETF资产配置（东财数据中心，同步）"""
    data = _eastmoney_datacenter_sync(
        "RPT_FUND_ASSETALLOCATION",
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=5,
        sort_columns="REPORT_DATE", sort_types="-1",
    )
    rows = []
    for row in data:
        rows.append({
            "报告期": str(row.get("REPORT_DATE", ""))[:10],
            "股票占比": row.get("STOCK_RATIO", 0),
            "债券占比": row.get("BOND_RATIO", 0),
            "现金占比": row.get("CASH_RATIO", 0),
            "其他占比": row.get("OTHER_RATIO", 0),
        })
    return rows


async def etf_allocation(code: str):
    """ETF资产配置（异步）"""
    return await _run_sync(_etf_allocation_sync, code)
