import pytest
import pandas as pd
from pytest_bdd import scenarios, given, when, then, parsers
from unittest.mock import patch

scenarios("features/detail_enhance.feature")


@pytest.fixture(autouse=True)
def _clear_caches():
    """每个场景前清除所有新功能缓存。"""
    from app.services.market_data import (
        _intraday_cache, _bidask_cache, _fund_flow_cache, _minute_cache,
    )
    _intraday_cache.clear()
    _bidask_cache.clear()
    _fund_flow_cache.clear()
    _minute_cache.clear()


@pytest.fixture
def ctx():
    return {}


# ─── 分时图 ───

def _make_intraday_df(n=240):
    times = [f"09:{30+i//60:02d}:{i%60:02d}" if i < 30 else f"13:{(i-30)//60:02d}:{(i-30)%60:02d}" for i in range(min(n, 240))]
    return pd.DataFrame({
        "时间": times,
        "成交价": [11.0 + i * 0.001 for i in range(len(times))],
        "成交量": [1000 + i * 10 for i in range(len(times))],
        "成交性质": ["主动买"] * len(times),
    })


@given("股票000001有分时成交数据")
def setup_intraday_data(ctx):
    pass


@when("我请求该股票的分时数据")
def request_intraday(ctx):
    from app.services.market_data import get_intraday
    with patch("app.services.market_data.ak.stock_intraday_em", return_value=_make_intraday_df()):
        ctx["result"] = get_intraday("000001")


@then("返回当日每分钟成交数据列表")
def verify_intraday_list(ctx):
    result = ctx["result"]
    assert isinstance(result, list)
    assert len(result) > 0


@then(parsers.re("每条数据包含时间、成交价、成交量、成交性质"))
def verify_intraday_fields(ctx):
    required = {"time", "price", "volume", "nature"}
    for item in ctx["result"]:
        assert required.issubset(item.keys()), f"Missing: {required - item.keys()}"


@given("当前非交易时间")
def setup_non_trading_time(ctx):
    pass


@then("返回最近交易日的分时数据")
def verify_intraday_last_trading_day(ctx):
    result = ctx["result"]
    assert isinstance(result, list)
    assert len(result) > 0


@given(parsers.re("股票(?P<code>\\d+)不存在"))
def setup_nonexistent_stock(ctx, code):
    ctx["bad_code"] = code


@when(parsers.re("我请求不存在的股票的分时数据"))
def request_intraday_nonexistent(ctx):
    from app.services.market_data import get_intraday
    code = ctx.get("bad_code", "999999")
    with patch("app.services.market_data.ak.stock_intraday_em", side_effect=Exception("not found")):
        ctx["result"] = get_intraday(code)


@then("分时返回空列表")
def verify_intraday_empty(ctx):
    assert ctx["result"] == []


# ─── 五档盘口 ───

def _make_bidask_df():
    return pd.DataFrame({
        "item": [
            "sell_5", "sell_5_vol", "sell_4", "sell_4_vol", "sell_3", "sell_3_vol",
            "sell_2", "sell_2_vol", "sell_1", "sell_1_vol",
            "buy_1", "buy_1_vol", "buy_2", "buy_2_vol", "buy_3", "buy_3_vol",
            "buy_4", "buy_4_vol", "buy_5", "buy_5_vol",
            "最新", "均价", "涨停", "跌停",
        ],
        "value": [
            11.55, 500000, 11.54, 600000, 11.53, 450000,
            11.52, 1073000, 11.51, 867400,
            11.50, 97900, 11.49, 274100, 11.48, 836100,
            11.47, 513600, 11.46, 166100,
            11.50, 11.52, 12.67, 10.37,
        ],
    })


@given("股票000001有盘口数据")
def setup_bidask_data(ctx):
    pass


@when("我请求该股票的五档盘口")
def request_bidask(ctx):
    from app.services.market_data import get_bid_ask
    with patch("app.services.market_data.ak.stock_bid_ask_em", return_value=_make_bidask_df()):
        ctx["result"] = get_bid_ask("000001")


@then(parsers.re("返回买1至买5和卖1至卖5的价格与挂单量"))
def verify_bidask_levels(ctx):
    result = ctx["result"]
    assert isinstance(result, dict)
    for i in range(1, 6):
        assert f"buy_{i}" in result, f"Missing buy_{i}"
        assert f"buy_{i}_vol" in result, f"Missing buy_{i}_vol"
        assert f"sell_{i}" in result, f"Missing sell_{i}"
        assert f"sell_{i}_vol" in result, f"Missing sell_{i}_vol"


@then("返回最新价、均价、涨停价、跌停价")
def verify_bidask_extra(ctx):
    result = ctx["result"]
    for key in ["latest", "avg", "limit_up", "limit_down"]:
        assert key in result, f"Missing {key}"


@when(parsers.re("我请求不存在的股票的五档盘口"))
def request_bidask_nonexistent(ctx):
    from app.services.market_data import get_bid_ask
    code = ctx.get("bad_code", "999999")
    with patch("app.services.market_data.ak.stock_bid_ask_em", side_effect=Exception("not found")):
        ctx["result"] = get_bid_ask(code)


@then("返回空字典")
def verify_empty_dict(ctx):
    assert ctx["result"] == {}


# ─── 资金流向 ───

def _make_fund_flow_df(n=10):
    return pd.DataFrame({
        "日期": [f"2025-11-{3+i}" for i in range(n)],
        "收盘价": [11.4 + i * 0.02 for i in range(n)],
        "涨跌幅": [0.5 + i * 0.1 for i in range(n)],
        "主力净流入-净额": [62891094.0 - i * 1000000 for i in range(n)],
        "主力净流入-净占比": [5.8 - i * 0.2 for i in range(n)],
        "超大单净流入-净额": [36970422.0 for i in range(n)],
        "超大单净流入-净占比": [3.41 for i in range(n)],
        "大单净流入-净额": [25920672.0 for i in range(n)],
        "大单净流入-净占比": [2.39 for i in range(n)],
        "中单净流入-净额": [-56320640.0 for i in range(n)],
        "中单净流入-净占比": [-5.19 for i in range(n)],
        "小单净流入-净额": [-6570449.0 for i in range(n)],
        "小单净流入-净占比": [-0.61 for i in range(n)],
    })


@given("股票000001有资金流向数据")
def setup_fund_flow_data(ctx):
    pass


@when("我请求该股票的资金流向")
def request_fund_flow(ctx):
    from app.services.market_data import get_fund_flow
    with patch("app.services.market_data.ak.stock_individual_fund_flow", return_value=_make_fund_flow_df()):
        ctx["result"] = get_fund_flow("000001")


@then("返回近期每日资金流向列表")
def verify_fund_flow_list(ctx):
    result = ctx["result"]
    assert isinstance(result, list)
    assert len(result) > 0


@then(parsers.re("每条数据包含日期、主力净流入净额及净占比、超大单/大单/中单/小单分类"))
def verify_fund_flow_fields(ctx):
    required = {"date", "main_net", "main_pct", "huge_net", "huge_pct", "big_net", "big_pct", "mid_net", "mid_pct", "small_net", "small_pct"}
    for item in ctx["result"]:
        assert required.issubset(item.keys()), f"Missing: {required - item.keys()}"


@when(parsers.re("我请求不存在的股票的资金流向"))
def request_fund_flow_nonexistent(ctx):
    from app.services.market_data import get_fund_flow
    code = ctx.get("bad_code", "999999")
    with patch("app.services.market_data.ak.stock_individual_fund_flow", side_effect=Exception("not found")):
        ctx["result"] = get_fund_flow(code)


@then("资金流向返回空列表")
def verify_fund_flow_empty(ctx):
    assert ctx["result"] == []


# ─── 分钟K线 ───

def _make_minute_kline_df(n=48):
    return pd.DataFrame({
        "day": [f"2026-05-05 09:{30+i:02d}:00" for i in range(n)],
        "open": [11.0 + i * 0.01 for i in range(n)],
        "high": [11.1 + i * 0.01 for i in range(n)],
        "low": [10.9 + i * 0.01 for i in range(n)],
        "close": [11.0 + i * 0.01 for i in range(n)],
        "volume": [10000 + i * 100 for i in range(n)],
    })


@given("股票000001有分钟K线数据")
def setup_minute_kline_data(ctx):
    pass


@when("我请求该股票的5分钟K线")
def request_minute_kline(ctx):
    from app.services.market_data import get_minute_history
    with patch("app.services.market_data.ak.stock_zh_a_minute", return_value=_make_minute_kline_df()):
        ctx["result"] = get_minute_history("000001", period="1")


@then("返回近期5分钟K线数据列表")
def verify_minute_kline_list(ctx):
    result = ctx["result"]
    assert isinstance(result, list)
    assert len(result) > 0


@then(parsers.re("每条数据包含时间、开盘价、最高价、最低价、收盘价、成交量"))
def verify_minute_kline_fields(ctx):
    required = {"day", "open", "high", "low", "close", "volume"}
    for item in ctx["result"]:
        assert required.issubset(item.keys()), f"Missing: {required - item.keys()}"


@when(parsers.re("我请求不存在的股票的5分钟K线"))
def request_minute_kline_nonexistent(ctx):
    from app.services.market_data import get_minute_history
    code = ctx.get("bad_code", "999999")
    with patch("app.services.market_data.ak.stock_zh_a_minute", side_effect=Exception("not found")):
        ctx["result"] = get_minute_history(code, period="1")


@then("分钟K线返回空列表")
def verify_minute_kline_empty(ctx):
    assert ctx["result"] == []
