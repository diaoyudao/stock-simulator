import pytest
import pandas as pd
from pytest_bdd import scenarios, given, when, then
from unittest.mock import patch

scenarios("features/kline.feature")


@pytest.fixture
def kline_ctx():
    return {}


def _make_kline_df(n=10):
    return pd.DataFrame({
        "日期": [f"2026-01-{i+1:02d}" for i in range(n)],
        "股票代码": ["000001"] * n,
        "开盘": [10.0] * n,
        "收盘": [10.5] * n,
        "最高": [11.0] * n,
        "最低": [9.5] * n,
        "成交量": [100000] * n,
        "成交额": [1e6] * n,
        "振幅": [1.5] * n,
        "涨跌幅": [1.0] * n,
        "涨跌额": [0.5] * n,
        "换手率": [2.0] * n,
    })


@given("股票000001有日K线数据")
def setup_daily_kline(kline_ctx):
    import app.services.market_data as md
    md._kline_cache.clear()
    kline_ctx["df"] = _make_kline_df(5)


@when("我请求日K线历史")
def request_daily_kline(kline_ctx):
    from app.services.market_data import get_stock_history
    with patch("app.services.market_data.ak.stock_zh_a_hist", return_value=kline_ctx["df"]):
        kline_ctx["result"] = get_stock_history("000001", "daily")


@then("返回的每条数据包含 day、open、high、low、close、volume 字段")
def verify_kline_fields(kline_ctx):
    required = {"day", "open", "high", "low", "close", "volume"}
    for item in kline_ctx["result"]:
        assert required.issubset(item.keys())


@then("所有字段值为字符串类型")
def verify_kline_types(kline_ctx):
    for item in kline_ctx["result"]:
        for key in ("day", "open", "high", "low", "close", "volume"):
            assert isinstance(item[key], str), f"{key} is {type(item[key])}, expected str"


@given("股票000001有历史数据")
def setup_monthly_kline(kline_ctx):
    import app.services.market_data as md
    md._kline_cache.clear()
    kline_ctx["df"] = _make_kline_df(12)


@when("我请求月K线历史")
def request_monthly_kline(kline_ctx):
    from app.services.market_data import get_stock_history
    with patch("app.services.market_data.ak.stock_zh_a_hist", return_value=kline_ctx["df"]):
        kline_ctx["result"] = get_stock_history("000001", "monthly")


@then("直接返回月K数据，无需手动聚合")
def verify_monthly_direct(kline_ctx):
    assert len(kline_ctx["result"]) > 0


@given("股票999999没有K线数据")
def setup_no_kline(kline_ctx):
    import app.services.market_data as md
    md._kline_cache.clear()


@when("我请求该股票的K线历史")
def request_empty_kline(kline_ctx):
    from app.services.market_data import get_stock_history
    with patch("app.services.market_data.ak.stock_zh_a_hist", return_value=pd.DataFrame()):
        kline_ctx["result"] = get_stock_history("999999", "daily")


@then("返回空列表")
def verify_empty_kline(kline_ctx):
    assert kline_ctx["result"] == []
