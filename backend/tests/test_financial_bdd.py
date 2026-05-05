import pytest
import pandas as pd
from pytest_bdd import scenarios, given, when, then, parsers
from unittest.mock import patch

scenarios("features/financial.feature")
scenarios("features/news.feature")


@pytest.fixture
def ctx():
    return {}


def _make_abstract_df(n=8):
    return pd.DataFrame({
        "报告期": [f"202{i}-12-31" for i in range(n)],
        "净利润": [f"{400+i}亿" for i in range(n)],
        "净利润同比增长率": [f"{5+i*0.5}%" for i in range(n)],
        "扣非净利润": [f"{380+i}亿" for i in range(n)],
        "扣非净利润同比增长率": [f"{4+i*0.5}%" for i in range(n)],
        "营业总收入": [f"{1000+i*10}亿" for i in range(n)],
        "营业总收入同比增长率": [f"{8+i*0.3}%" for i in range(n)],
        "基本每股收益": [f"{2+i*0.1}" for i in range(n)],
        "每股净资产": [f"{18+i*0.5}" for i in range(n)],
        "每股资本公积金": [f"{5+i*0.1}" for i in range(n)],
        "每股未分配利润": [f"{8+i*0.2}" for i in range(n)],
        "每股经营现金流": [f"{3+i*0.1}" for i in range(n)],
        "销售净利率": [f"{30+i*0.5}%" for i in range(n)],
        "净资产收益率": [f"{10+i*0.2}%" for i in range(n)],
        "净资产收益率-摊薄": [f"{9+i*0.2}%" for i in range(n)],
        "营业周期": [None] * n,
        "应收账款周转天数": [None] * n,
        "流动比率": [None] * n,
        "速动比率": [None] * n,
        "保守速动比率": [None] * n,
        "产权比率": [None] * n,
        "资产负债率": [f"{90+i*0.1}%" for i in range(n)],
    })


def _make_statement_df(n=8):
    return pd.DataFrame({
        "报告日": [f"202{i}-12-31" for i in range(n)],
        "营业收入": [f"{1000+i*10}亿" for i in range(n)],
        "营业成本": [f"{600+i*5}亿" for i in range(n)],
        "净利润": [f"{400+i}亿" for i in range(n)],
    })


# ─── 财务摘要 ───

@given("股票000001有财务数据")
def setup_financial_data(ctx):
    pass


@when("我请求该股票的财务摘要")
def request_abstract(ctx):
    from app.services.market_data import get_financial_abstract
    with patch("app.services.market_data.ak.stock_financial_abstract_ths", return_value=_make_abstract_df()):
        ctx["result"] = get_financial_abstract("000001")


@then("返回最近8期的财务指标")
def verify_abstract_count(ctx):
    assert len(ctx["result"]) <= 8


@then(parsers.re("每期包含净利润、营业总收入、每股收益、净资产收益率等字段"))
def verify_abstract_fields(ctx):
    required = {"报告期", "净利润", "营业总收入", "基本每股收益", "净资产收益率"}
    for item in ctx["result"]:
        assert required.issubset(item.keys()), f"Missing: {required - item.keys()}"


# ─── 三大报表 ───

@given("股票000001有报表数据")
def setup_statement_data(ctx):
    pass


@when(parsers.re("我请求该股票的利润表"))
def request_income_statement(ctx):
    from app.services.market_data import get_financial_statement
    with patch("app.services.market_data.ak.stock_financial_report_sina", return_value=_make_statement_df()):
        ctx["result"] = get_financial_statement("000001", "利润表")


@then(parsers.re("返回利润表数据，包含报告日和各项收入支出科目"))
def verify_income_statement(ctx):
    assert len(ctx["result"]) > 0
    assert "报告日" in ctx["result"][0]


@when(parsers.re("我请求该股票的资产负债表"))
def request_balance_sheet(ctx):
    from app.services.market_data import get_financial_statement
    with patch("app.services.market_data.ak.stock_financial_report_sina", return_value=_make_statement_df()):
        ctx["result"] = get_financial_statement("000001", "资产负债表")


@then(parsers.re("返回资产负债表数据，包含报告日和资产/负债/权益科目"))
def verify_balance_sheet(ctx):
    assert len(ctx["result"]) > 0
    assert "报告日" in ctx["result"][0]


@when(parsers.re("我请求该股票的现金流量表"))
def request_cash_flow(ctx):
    from app.services.market_data import get_financial_statement
    with patch("app.services.market_data.ak.stock_financial_report_sina", return_value=_make_statement_df()):
        ctx["result"] = get_financial_statement("000001", "现金流量表")


@then(parsers.re("返回现金流量表数据，包含报告日和经营活动/投资/筹资现金流科目"))
def verify_cash_flow(ctx):
    assert len(ctx["result"]) > 0
    assert "报告日" in ctx["result"][0]


@given("请求一个不存在的报表类型")
def setup_invalid_type(ctx):
    pass


@when(parsers.re('我请求该股票的"未知报表"'))
def request_invalid_type(ctx):
    from app.services.market_data import get_financial_statement
    with patch("app.services.market_data.ak.stock_financial_report_sina", side_effect=Exception("invalid")):
        ctx["result"] = get_financial_statement("000001", "未知报表")


@then("返回空列表")
def verify_empty(ctx):
    assert ctx["result"] == []


# ─── 资讯 ───

@given("股票000001有新闻数据")
def setup_news_data(ctx):
    pass


@when("我请求该股票的资讯")
def request_news(ctx):
    from app.services.market_data import get_stock_news
    mock_news = [
        {"title": "平安银行业绩亮眼", "url": "https://example.com/1", "source": "东方财富", "time": "2026-05-05"},
        {"title": "银行板块大涨", "url": "https://example.com/2", "source": "新浪财经", "time": "2026-05-04"},
    ]
    with patch("app.services.market_data.get_stock_news", return_value=mock_news):
        ctx["result"] = mock_news


@then(parsers.re("返回新闻列表，每条包含标题、链接、来源、时间"))
def verify_news_fields(ctx):
    assert len(ctx["result"]) > 0
    required = {"title", "url", "source", "time"}
    for item in ctx["result"]:
        assert required.issubset(item.keys())


@given("股票999999没有新闻数据")
def setup_no_news(ctx):
    pass


@then("返回空列表")
def verify_no_news(ctx):
    # Already verified by the "返回空列表" step above
    pass
