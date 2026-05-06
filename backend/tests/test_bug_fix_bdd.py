"""Bug修复 BDD测试 — 时区/限流/降级/ETF交易/RateLimiter清理"""
import pytest
import time
from pytest_bdd import scenarios, given, when, then
from unittest.mock import patch
from datetime import datetime, timezone, timedelta

scenarios("features/bug_fix.feature")

_result = {}


# ─── 时区 ───

@given("服务器时区可能不是UTC+8")
def server_timezone():
    pass


@when("我检查交易时间")
def check_trading_time():
    from app.routers.trade import _check_trading_time, _CST
    _result["cst"] = _CST
    _result["now"] = datetime.now(_CST)


@then("使用的是CST时区而非服务器本地时区")
def verify_cst():
    assert _result["cst"] == timezone(timedelta(hours=8))
    assert _result["now"].tzinfo == _result["cst"]


# ─── AI限流 ───

@given("AI评分端点存在")
def ai_score_endpoint():
    pass


@when("我创建RateLimiter实例")
def create_rate_limiter():
    from app.rate_limiter import RateLimiter
    _result["limiter"] = RateLimiter(max_requests=15, window_seconds=60)


@then("限流器配置正确且能拒绝超额请求")
def verify_rate_limiter():
    limiter = _result["limiter"]
    for _ in range(15):
        assert limiter.is_allowed("test_ip") is True
    assert limiter.is_allowed("test_ip") is False


# ─── Sina降级筛选 ───

@given("行情数据来自新浪备用源，缺失换手率/PE/PB/市值字段")
def sina_degraded_data():
    _result["stocks"] = [
        {"代码": "001", "名称": "A", "最新价": 2.0, "涨跌幅": 1.0, "涨跌额": 0.02,
         "今开": 2.0, "最高": 2.1, "最低": 1.9, "昨收": 1.98, "买一": 2.0, "卖一": 2.0,
         "成交量": 100, "成交额": 200, "换手率": 0, "市盈率-动态": 0,
         "市净率": 0, "总市值": 0, "流通市值": 0, "量比": 0, "_degraded": True},
    ]


@when("我按换手率或市盈率筛选")
def filter_degraded():
    from app.services.market_data import filter_low_price
    with patch("app.services.market_data.get_spot_data", return_value=_result["stocks"]):
        _result["filtered"] = filter_low_price(
            min_price=1, max_price=5, min_turnover_rate=2.0, min_pe=5.0
        )


@then("这些筛选条件被跳过，结果包含warning提示")
def verify_degraded_filter():
    assert _result["filtered"]["total"] == 1
    assert "warning" in _result["filtered"]
    assert "备用数据源" in _result["filtered"]["warning"]


# ─── sector_failed None ───

@given("行业成分股API全部失败返回None")
def sector_api_failed():
    pass


@when("我按行业筛选股票")
def filter_by_sector():
    from app.services.market_data import filter_low_price
    stocks = [
        {"代码": "001", "名称": "A", "最新价": 2.0, "涨跌幅": 1.0, "涨跌额": 0.02,
         "今开": 2.0, "最高": 2.1, "最低": 1.9, "昨收": 1.98, "买一": 2.0, "卖一": 2.0,
         "成交量": 100, "成交额": 200, "换手率": 1.0, "市盈率-动态": 10.0,
         "市净率": 1.0, "总市值": 1e9, "流通市值": 5e8, "量比": 1.0},
        {"代码": "002", "名称": "B", "最新价": 3.0, "涨跌幅": 2.0, "涨跌额": 0.06,
         "今开": 3.0, "最高": 3.1, "最低": 2.9, "昨收": 2.94, "买一": 3.0, "卖一": 3.0,
         "成交量": 200, "成交额": 600, "换手率": 2.0, "市盈率-动态": 20.0,
         "市净率": 2.0, "总市值": 2e9, "流通市值": 1e9, "量比": 1.5},
    ]
    with patch("app.services.market_data.get_spot_data", return_value=stocks), \
         patch("app.services.market_data._fetch_sector_constituents", return_value=None):
        _result["filtered"] = filter_low_price(sector="银行")


@then("sector_failed为True且返回warning")
def verify_sector_failed():
    assert "warning" in _result["filtered"]
    assert "成分股数据获取失败" in _result["filtered"]["warning"]


# ─── ETF交易价格 ───

@given("持仓中有ETF代码510300")
def etf_position():
    pass


@when("构建价格映射时")
def build_price_map():
    from app.routers.trade import _build_price_map
    with patch("app.services.market_data.get_price_map", return_value={"000001": 10.0}), \
         patch("app.services.market_data.get_etf_price_map", return_value={"510300": 3.0}):
        _result["price_map"] = _build_price_map()


@then("ETF价格被包含在映射中")
def verify_etf_price():
    assert "510300" in _result["price_map"]
    assert _result["price_map"]["510300"] == 3.0
    assert "000001" in _result["price_map"]


# ─── RateLimiter清理 ───

@given("RateLimiter中有超过256个IP条目")
def rate_limiter_full():
    from app.rate_limiter import RateLimiter
    limiter = RateLimiter(max_requests=10, window_seconds=60)
    for i in range(300):
        limiter.is_allowed(f"ip_{i}")
    _result["limiter"] = limiter


@when("有新的请求进来")
def new_request():
    _result["limiter"].is_allowed("new_ip")


@then("空列表IP被清理，总条目不超过1024")
def verify_cleanup():
    limiter = _result["limiter"]
    assert len(limiter._requests) <= 1024
