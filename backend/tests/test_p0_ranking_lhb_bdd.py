import pytest
import pandas as pd
from pytest_bdd import scenarios, given, when, then, parsers
from unittest.mock import patch

scenarios("features/p0_ranking_lhb.feature")


@pytest.fixture(autouse=True)
def _clear_caches():
    from app.services.market_data import _lhb_cache
    _lhb_cache.clear()


@pytest.fixture
def ctx():
    return {}


# ─── 涨跌排行 ───

def _make_spot_items(n=20):
    return [
        {
            "代码": f"{i:06d}",
            "名称": f"测试股{i}",
            "最新价": 1.0 + i * 0.5,
            "涨跌幅": i * 0.5 - 5.0,
            "涨跌额": 0.1,
            "今开": 1.0,
            "最高": 2.0,
            "最低": 0.5,
            "昨收": 1.0,
            "买一": 1.0,
            "卖一": 1.0,
            "成交量": 1000000 + i * 100000,
            "成交额": 5000000 + i * 500000,
            "换手率": 1.0 + i * 0.5,
            "市盈率-动态": 10.0,
            "市净率": 1.0,
            "总市值": 100000000,
            "流通市值": 50000000,
            "量比": 0.5 + i * 0.3,
        }
        for i in range(1, n + 1)
    ]


def _mock_ranking(items):
    return patch("app.services.market_data.filter_low_price", return_value={"items": items, "total": len(items)})


@given("有行情数据")
def setup_spot_data(ctx):
    pass


@when("我请求涨幅排行榜")
def request_top_gainers(ctx):
    from app.services.market_data import get_ranking
    with _mock_ranking(_make_spot_items()):
        ctx["result"] = get_ranking(sort_by="涨跌幅", order="desc")


@then("返回按涨跌幅降序排列的股票列表")
def verify_top_gainers(ctx):
    result = ctx["result"]
    assert isinstance(result, list)
    assert len(result) > 1
    for i in range(len(result) - 1):
        assert result[i]["涨跌幅"] >= result[i + 1]["涨跌幅"]


@then(parsers.re("每条数据包含代码、名称、最新价、涨跌幅、成交额"))
def verify_ranking_fields(ctx):
    required = {"代码", "名称", "最新价", "涨跌幅", "成交额"}
    for item in ctx["result"]:
        assert required.issubset(item.keys()), f"Missing: {required - item.keys()}"


@when("我请求跌幅排行榜")
def request_top_losers(ctx):
    from app.services.market_data import get_ranking
    with _mock_ranking(_make_spot_items()):
        ctx["result"] = get_ranking(sort_by="涨跌幅", order="asc")


@then("返回按涨跌幅升序排列的股票列表")
def verify_top_losers(ctx):
    result = ctx["result"]
    assert isinstance(result, list)
    for i in range(len(result) - 1):
        assert result[i]["涨跌幅"] <= result[i + 1]["涨跌幅"]


@when("我请求换手率排行榜")
def request_top_turnover(ctx):
    from app.services.market_data import get_ranking
    with _mock_ranking(_make_spot_items()):
        ctx["result"] = get_ranking(sort_by="换手率", order="desc")


@then("返回按换手率降序排列的股票列表")
def verify_top_turnover(ctx):
    result = ctx["result"]
    for i in range(len(result) - 1):
        assert result[i]["换手率"] >= result[i + 1]["换手率"]


@when("我请求成交额排行榜")
def request_top_amount(ctx):
    from app.services.market_data import get_ranking
    with _mock_ranking(_make_spot_items()):
        ctx["result"] = get_ranking(sort_by="成交额", order="desc")


@then("返回按成交额降序排列的股票列表")
def verify_top_amount(ctx):
    result = ctx["result"]
    for i in range(len(result) - 1):
        assert result[i]["成交额"] >= result[i + 1]["成交额"]


@when("我请求量比排行榜")
def request_top_volume_ratio(ctx):
    from app.services.market_data import get_ranking
    with _mock_ranking(_make_spot_items()):
        ctx["result"] = get_ranking(sort_by="量比", order="desc")


@then("返回按量比降序排列的股票列表")
def verify_top_volume_ratio(ctx):
    result = ctx["result"]
    for i in range(len(result) - 1):
        assert result[i].get("量比", 0) >= result[i + 1].get("量比", 0)


# ─── 龙虎榜 ───

def _make_lhb_df(n=5):
    return pd.DataFrame({
        "序号": list(range(1, n + 1)),
        "代码": [f"{i:06d}" for i in range(1, n + 1)],
        "名称": [f"龙虎股{i}" for i in range(1, n + 1)],
        "上榜日": [f"2026-04-{28 + i}" for i in range(n)],
        "解读": ["测试解读"] * n,
        "收盘价": [10.0 + i for i in range(n)],
        "涨跌幅": [5.0 + i for i in range(n)],
        "龙虎榜净买额": [1000000.0 * i for i in range(n)],
        "龙虎榜买入额": [2000000.0 * i for i in range(n)],
        "龙虎榜卖出额": [1000000.0 * i for i in range(n)],
        "龙虎榜成交额": [3000000.0 * i for i in range(n)],
        "市场总成交额": [50000000.0 * i for i in range(n)],
        "净买额占总成交比": [1.0 + i for i in range(n)],
        "成交额占总成交比": [5.0 + i for i in range(n)],
        "换手率": [10.0 + i for i in range(n)],
        "流通市值": [100000000.0 for i in range(n)],
        "上榜原因": ["涨幅偏离值达7%"] * n,
        "上榜后1日": [float("nan")] * n,
        "上榜后2日": [float("nan")] * n,
        "上榜后5日": [float("nan")] * n,
        "上榜后10日": [float("nan")] * n,
    })


@given("有龙虎榜数据")
def setup_lhb_data(ctx):
    pass


@when("我请求最近5个交易日的龙虎榜")
def request_lhb(ctx):
    from app.services.market_data import get_lhb
    with patch("app.services.market_data.ak.stock_lhb_detail_em", return_value=_make_lhb_df()):
        ctx["result"] = get_lhb()


@then("返回龙虎榜个股列表")
def verify_lhb_list(ctx):
    result = ctx["result"]
    assert isinstance(result, list)
    assert len(result) > 0


@then(parsers.re("每条数据包含代码、名称、上榜日、涨跌幅、净买额、上榜原因"))
def verify_lhb_fields(ctx):
    required = {"代码", "名称", "上榜日", "涨跌幅", "净买额", "上榜原因"}
    for item in ctx["result"]:
        assert required.issubset(item.keys()), f"Missing: {required - item.keys()}"


@given("无龙虎榜数据")
def setup_no_lhb_data(ctx):
    pass


@when("我请求龙虎榜")
def request_lhb_empty(ctx):
    from app.services.market_data import get_lhb
    with patch("app.services.market_data.ak.stock_lhb_detail_em", side_effect=Exception("no data")):
        ctx["result"] = get_lhb()


@then("龙虎榜返回空列表")
def verify_lhb_empty(ctx):
    assert ctx["result"] == []
