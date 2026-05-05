import pytest
import pandas as pd
from pytest_bdd import scenarios, when, then
from unittest.mock import patch

scenarios("features/market_index.feature")


@pytest.fixture
def index_ctx():
    return {}


@when("我请求大盘指数数据")
def request_indices(index_ctx):
    import app.services.market_data as md
    md._index_cache = (0.0, [])

    def side_effect(symbol):
        if "上证" in symbol:
            return pd.DataFrame({
                "代码": ["000001"], "名称": ["上证指数"],
                "最新价": [4100.0], "涨跌幅": [0.5], "涨跌额": [20.0],
                "昨收": [4080.0],
            })
        else:
            return pd.DataFrame({
                "代码": ["399001", "399006"], "名称": ["深证成指", "创业板指"],
                "最新价": [15000.0, 3600.0], "涨跌幅": [-0.1, 0.3], "涨跌额": [-15.0, 10.0],
                "昨收": [15015.0, 3590.0],
            })

    with patch("app.services.market_data.ak.stock_zh_index_spot_em", side_effect=side_effect):
        from app.services.market_data import get_index_data
        index_ctx["result"] = get_index_data()


@then("返回上证指数、深证成指、创业板指三个指数")
def verify_three_indices(index_ctx):
    result = index_ctx["result"]
    assert len(result) == 3
    names = {r["name"] for r in result}
    assert "上证指数" in names
    assert "深证成指" in names
    assert "创业板指" in names


@then("每个指数包含 code、name、current、yesterday、change_pct 字段")
def verify_index_fields(index_ctx):
    required = {"code", "name", "current", "yesterday", "change_pct"}
    for item in index_ctx["result"]:
        assert required.issubset(item.keys())


@then("上证指数的 code 为 sh000001")
def verify_sh_code(index_ctx):
    sh = [r for r in index_ctx["result"] if r["name"] == "上证指数"][0]
    assert sh["code"] == "sh000001"
