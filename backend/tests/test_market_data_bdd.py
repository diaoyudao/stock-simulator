import pytest
from pytest_bdd import scenarios, given, when, then, parsers
from unittest.mock import patch

scenarios("features/market_data.feature")


@pytest.fixture
def market_stocks():
    return [
        {"代码": "001", "名称": "低价股A", "最新价": 2.0, "涨跌幅": 1.0, "涨跌额": 0.02,
         "今开": 2.0, "最高": 2.1, "最低": 1.9, "昨收": 1.98, "买一": 2.0, "卖一": 2.0,
         "成交量": 100, "成交额": 200, "换手率": 1.0, "市盈率-动态": 10.0,
         "市净率": 1.0, "总市值": 1e9, "流通市值": 5e8, "量比": 1.0},
        {"代码": "002", "名称": "高价股B", "最新价": 10.0, "涨跌幅": -1.0, "涨跌额": -0.1,
         "今开": 10.0, "最高": 10.1, "最低": 9.9, "昨收": 10.1, "买一": 10.0, "卖一": 10.0,
         "成交量": 200, "成交额": 2000, "换手率": 2.0, "市盈率-动态": 20.0,
         "市净率": 2.0, "总市值": 2e9, "流通市值": 1e9, "量比": 0.5},
    ]


@pytest.fixture
def st_stocks():
    return [
        {"代码": "001", "名称": "ST某股", "最新价": 3.0, "涨跌幅": 5.0, "涨跌额": 0.15,
         "今开": 3.0, "最高": 3.1, "最低": 2.9, "昨收": 2.85, "买一": 3.0, "卖一": 3.0,
         "成交量": 100, "成交额": 300, "换手率": 1.0, "市盈率-动态": 10.0,
         "市净率": 1.0, "总市值": 1e9, "流通市值": 5e8, "量比": 1.0},
        {"代码": "002", "名称": "正常股", "最新价": 3.0, "涨跌幅": 1.0, "涨跌额": 0.03,
         "今开": 3.0, "最高": 3.1, "最低": 2.9, "昨收": 2.97, "买一": 3.0, "卖一": 3.0,
         "成交量": 100, "成交额": 300, "换手率": 1.0, "市盈率-动态": 10.0,
         "市净率": 1.0, "总市值": 1e9, "流通市值": 5e8, "量比": 1.0},
    ]


@pytest.fixture
def many_stocks():
    return [
        {"代码": f"00{i}", "名称": f"股票{i}", "最新价": 3.0, "涨跌幅": float(i),
         "涨跌额": 0.01, "今开": 3.0, "最高": 3.1, "最低": 2.9, "昨收": 2.99,
         "买一": 3.0, "卖一": 3.0, "成交量": 100, "成交额": 300, "换手率": 1.0,
         "市盈率-动态": 10.0, "市净率": 1.0, "总市值": 1e9, "流通市值": 5e8, "量比": 1.0}
        for i in range(10)
    ]


@given("全市场行情中有价格为2.0元和10.0元的股票")
def setup_price_stocks(market_stocks):
    return market_stocks


@when("我筛选1到5元的股票")
def filter_by_price(market_stocks):
    from app.services.market_data import filter_low_price
    with patch("app.services.market_data.get_spot_data", return_value=market_stocks):
        filter_by_price.result = filter_low_price(min_price=1.0, max_price=5.0)


@then("只返回价格在1到5元之间的股票")
def verify_price_filter():
    result = filter_by_price.result
    assert result["total"] == 1
    assert result["items"][0]["最新价"] <= 5.0


@given('全市场行情中有名称含"ST"的股票')
def setup_st_stocks(st_stocks):
    return st_stocks


@when("我设置排除ST股票")
def filter_exclude_st(st_stocks):
    from app.services.market_data import filter_low_price
    with patch("app.services.market_data.get_spot_data", return_value=st_stocks):
        filter_exclude_st.result = filter_low_price(exclude_st=True)


@then("结果中不包含任何ST股票")
def verify_no_st():
    result = filter_exclude_st.result
    assert result["total"] == 1
    assert "ST" not in result["items"][0]["名称"]


@given("全市场行情中有10只符合条件的股票")
def setup_many_stocks(many_stocks):
    return many_stocks


@when("我请求每页3条的第1页")
def paginate_results(many_stocks):
    from app.services.market_data import filter_low_price
    with patch("app.services.market_data.get_spot_data", return_value=many_stocks):
        paginate_results.result = filter_low_price(page=1, page_size=3)


@then(parsers.parse("返回{count:d}条记录，总数为{total:d}"))
def verify_pagination(count, total):
    result = paginate_results.result
    assert len(result["items"]) == count
    assert result["total"] == total
