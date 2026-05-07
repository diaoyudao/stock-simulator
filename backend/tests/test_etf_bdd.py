"""ETF BDD测试 — 筛选、详情、K线"""
import pytest
from pytest_bdd import scenarios, given, when, then, parsers
from unittest.mock import patch, MagicMock

scenarios("features/etf.feature")


def _make_etf_list():
    return [
        {"代码": "510300", "名称": "沪深300ETF", "最新价": 3.0, "涨跌幅": 5.0, "涨跌额": 0.15,
         "今开": 2.9, "最高": 3.1, "最低": 2.85, "昨收": 2.85, "买一": 3.0, "卖一": 3.01,
         "成交量": 100000, "成交额": 3000000, "换手率": 2.5, "量比": 1.2, "_type": "etf"},
        {"代码": "511010", "名称": "国债ETF", "最新价": 120.5, "涨跌幅": 0.1, "涨跌额": 0.12,
         "今开": 120.3, "最高": 120.6, "最低": 120.2, "昨收": 120.38, "买一": 120.5, "卖一": 120.51,
         "成交量": 5000, "成交额": 602500, "换手率": 0.5, "量比": 0.8, "_type": "etf"},
        {"代码": "159919", "名称": "中证500ETF", "最新价": 0.5, "涨跌幅": -3.0, "涨跌额": -0.015,
         "今开": 0.52, "最高": 0.52, "最低": 0.49, "昨收": 0.515, "买一": 0.5, "卖一": 0.501,
         "成交量": 200000, "成交额": 100000, "换手率": 5.0, "量比": 2.0, "_type": "etf"},
        {"代码": "518880", "名称": "黄金ETF", "最新价": 5.5, "涨跌幅": 1.0, "涨跌额": 0.055,
         "今开": 5.45, "最高": 5.6, "最低": 5.4, "昨收": 5.445, "买一": 5.5, "卖一": 5.51,
         "成交量": 50000, "成交额": 275000, "换手率": 1.0, "量比": 1.0, "_type": "etf"},
        {"代码": "511660", "名称": "货币ETF", "最新价": 100.0, "涨跌幅": 0.01, "涨跌额": 0.01,
         "今开": 100.0, "最高": 100.01, "最低": 99.99, "昨收": 99.99, "买一": 100.0, "卖一": 100.01,
         "成交量": 1000, "成交额": 100000, "换手率": 0.1, "量比": 0.5, "_type": "etf"},
        {"代码": "513100", "名称": "纳斯达克ETF", "最新价": 1.8, "涨跌幅": 2.0, "涨跌额": 0.036,
         "今开": 1.78, "最高": 1.85, "最低": 1.77, "昨收": 1.764, "买一": 1.8, "卖一": 1.81,
         "成交量": 80000, "成交额": 144000, "换手率": 3.0, "量比": 1.5, "_type": "etf"},
    ]


_result = {}


# ─── 价格筛选 ───

@given("ETF行情中有价格为0.5元和3.0元的ETF")
def etf_with_prices():
    _result["data"] = _make_etf_list()


@when("我筛选0到2元的ETF")
def filter_etf_by_price():
    from app.services.market_data import filter_etf
    with patch("app.services.market_data.get_etf_spot_data", return_value=_result["data"]):
        _result["filtered"] = filter_etf(min_price=0, max_price=2)


@then("只返回价格在0到2元之间的ETF")
def verify_price_filter():
    for item in _result["filtered"]["items"]:
        assert 0 <= item["最新价"] <= 2


# ─── 涨跌幅筛选 ───

@given("ETF行情中有涨跌幅为5%和-3%的ETF")
def etf_with_changes():
    _result["data"] = _make_etf_list()


@when("我筛选涨跌幅大于0的ETF")
def filter_etf_by_change():
    from app.services.market_data import filter_etf
    with patch("app.services.market_data.get_etf_spot_data", return_value=_result["data"]):
        _result["filtered"] = filter_etf(min_change_pct=0)


@then("只返回涨跌幅为正的ETF")
def verify_change_filter():
    for item in _result["filtered"]["items"]:
        assert item["涨跌幅"] > 0


# ─── 基金类型筛选 ───

@given("ETF行情中有\"沪深300ETF\"和\"国债ETF\"")
def etf_with_types():
    _result["data"] = _make_etf_list()


@when("我筛选指数类型的ETF")
def filter_etf_by_type():
    from app.services.market_data import filter_etf
    with patch("app.services.market_data.get_etf_spot_data", return_value=_result["data"]):
        _result["filtered"] = filter_etf(etf_type="指数")


@then("只返回名称包含指数关键词的ETF")
def verify_type_filter():
    type_kw = ["沪深300", "中证500", "上证50", "创业板", "科创", "中证1000", "国证", "指数"]
    for item in _result["filtered"]["items"]:
        assert any(kw in item["名称"] for kw in type_kw)


# ─── 关键词搜索 ───

@given("ETF行情中有\"510300沪深300ETF\"和\"159919中证500ETF\"")
def etf_with_names():
    _result["data"] = _make_etf_list()


@when(parsers.parse("我搜索关键词\"{keyword}\""))
def search_etf(keyword):
    from app.services.market_data import filter_etf
    with patch("app.services.market_data.get_etf_spot_data", return_value=_result["data"]):
        _result["filtered"] = filter_etf(keyword=keyword)


@then("只返回名称或代码匹配的ETF")
def verify_keyword_search():
    for item in _result["filtered"]["items"]:
        assert "沪深300" in item["名称"] or "沪深300" in item["代码"]


# ─── 分页 ───

@given("ETF行情中有10只符合条件的ETF")
def etf_ten_items():
    _result["data"] = [
        {"代码": f"51{i:04d}", "名称": f"ETF{i}", "最新价": 1.0 + i * 0.1,
         "涨跌幅": float(i), "涨跌额": 0.01, "今开": 1.0, "最高": 1.2, "最低": 0.9,
         "昨收": 1.0, "买一": 1.0, "卖一": 1.01, "成交量": 1000, "成交额": 1000,
         "换手率": 1.0, "量比": 1.0, "_type": "etf"}
        for i in range(10)
    ]


@when("我请求每页3条的第1页")
def paginate_etf():
    from app.services.market_data import filter_etf
    with patch("app.services.market_data.get_etf_spot_data", return_value=_result["data"]):
        _result["filtered"] = filter_etf(page=1, page_size=3)


@then("返回3条记录，总数为10")
def verify_pagination():
    assert len(_result["filtered"]["items"]) == 3
    assert _result["filtered"]["total"] == 10


# ─── ETF详情 ───

@given("ETF代码510300存在")
def etf_code_exists():
    pass


@when("我获取510300的详情")
def fetch_etf_detail():
    from app.services.market_data import get_etf_detail
    mock_etf = {
        "代码": "510300", "名称": "沪深300ETF", "最新价": 3.0, "涨跌幅": 5.0,
        "涨跌额": 0.15, "今开": 2.9, "最高": 3.1, "最低": 2.85, "昨收": 2.85,
        "买一": 3.0, "卖一": 3.01, "成交量": 100000, "成交额": 3000000,
        "换手率": 2.5, "量比": 1.2, "_type": "etf",
    }
    with patch("app.services.market_data.get_etf_by_code", return_value=mock_etf), \
         patch("app.services.market_data._executor") as mock_exec:
        mock_future = MagicMock()
        mock_future.result.return_value = (4.0, 2.0)
        mock_exec.submit.return_value = mock_future
        _result["detail"] = get_etf_detail("510300")


@then("返回结果包含52周最高价、52周最低价和基金类型")
def verify_etf_detail():
    r = _result["detail"]
    assert "52周最高" in r
    assert "52周最低" in r
    assert "基金类型" in r
    assert r["基金类型"] == "指数"


# ─── 不存在的ETF ───

@given("ETF代码999999不存在")
def etf_code_not_exist():
    pass


@when("我获取999999的详情")
def fetch_nonexistent_etf():
    from app.services.market_data import get_etf_detail
    with patch("app.services.market_data.get_etf_by_code", return_value=None):
        _result["detail"] = get_etf_detail("999999")


@then("返回结果包含error字段")
def verify_etf_not_found():
    assert "error" in _result["detail"]


# ─── 同花顺降级筛选 ───

@given("ETF行情来自同花顺备用源，缺失成交量/成交额/换手率字段")
def etf_ths_degraded_data():
    _result["etfs"] = [
        {"代码": "510300", "名称": "沪深300ETF", "最新价": 3.0, "涨跌幅": 5.0, "涨跌额": 0.15,
         "今开": 3.0, "最高": 3.0, "最低": 3.0, "昨收": 2.85, "买一": 3.0, "卖一": 3.0,
         "成交量": 0, "成交额": 0, "换手率": 0, "量比": 0, "_type": "etf", "_degraded": True},
        {"代码": "511010", "名称": "国债ETF", "最新价": 120.5, "涨跌幅": 0.1, "涨跌额": 0.12,
         "今开": 120.5, "最高": 120.5, "最低": 120.5, "昨收": 120.38, "买一": 120.5, "卖一": 120.5,
         "成交量": 0, "成交额": 0, "换手率": 0, "量比": 0, "_type": "etf", "_degraded": True},
    ]


@when("我按成交额筛选ETF")
def filter_etf_by_amount():
    from app.services.market_data import filter_etf
    with patch("app.services.market_data.get_etf_spot_data", return_value=_result["etfs"]):
        _result["filtered"] = filter_etf(min_amount=100000)


@then("成交额筛选被跳过，结果包含warning提示")
def verify_etf_degraded_filter():
    assert _result["filtered"]["total"] == 2
    assert "warning" in _result["filtered"]
    assert "备用数据源" in _result["filtered"]["warning"]


# ─── ETF资金流向 ───

@when("我获取510300的资金流向")
def fetch_etf_fund_flow():
    from app.services.market_data import get_etf_fund_flow
    mock_data = [
        {"date": "2026-05-06", "close": 3.5, "change_pct": 1.0,
         "main_net": -100000, "main_pct": -2.0, "huge_net": -50000, "huge_pct": -1.0,
         "big_net": -30000, "big_pct": -0.5, "mid_net": 20000, "mid_pct": 0.3,
         "small_net": 60000, "small_pct": 1.2},
    ]
    with patch("app.services.market_data.ak.stock_individual_fund_flow") as mock_ak:
        import pandas as pd
        mock_ak.return_value = pd.DataFrame({
            "日期": ["2026-05-06"], "收盘价": [3.5], "涨跌幅": [1.0],
            "主力净流入-净额": [-100000], "主力净流入-净占比": [-2.0],
            "超大单净流入-净额": [-50000], "超大单净流入-净占比": [-1.0],
            "大单净流入-净额": [-30000], "大单净流入-净占比": [-0.5],
            "中单净流入-净额": [20000], "中单净流入-净占比": [0.3],
            "小单净流入-净额": [60000], "小单净流入-净占比": [1.2],
        })
        _result["fundflow"] = get_etf_fund_flow("510300")


@then("返回结果包含日期和主力净流入字段")
def verify_etf_fund_flow():
    data = _result["fundflow"]
    assert len(data) >= 1
    assert "date" in data[0]
    assert "main_net" in data[0]


# ─── ETF净值 ───

@when("我获取510300的净值数据")
def fetch_etf_nav():
    from app.services.market_data import get_etf_nav
    with patch("app.services.market_data.ak.fund_etf_fund_info_em") as mock_ak:
        import pandas as pd
        mock_ak.return_value = pd.DataFrame({
            "净值日期": ["2026-05-06", "2026-05-05"],
            "单位净值": [3.5, 3.48],
            "累计净值": [3.5, 3.48],
            "日增长率": [0.57, -0.29],
            "申购状态": ["场内买入", "场内买入"],
            "赎回状态": ["场内卖出", "场内卖出"],
        })
        _result["nav"] = get_etf_nav("510300")


@then("返回结果包含日期和单位净值字段")
def verify_etf_nav():
    data = _result["nav"]
    assert len(data) >= 1
    assert "date" in data[0]
    assert "nav" in data[0]


# ─── ETF持仓 ───

@when("我获取510300的持仓数据")
def fetch_etf_holdings():
    from app.services.market_data import get_etf_holdings
    with patch("app.services.market_data.ak.fund_portfolio_hold_em") as mock_ak:
        import pandas as pd
        mock_ak.return_value = pd.DataFrame({
            "序号": [1, 2], "股票代码": ["600519", "300750"],
            "股票名称": ["贵州茅台", "宁德时代"],
            "占净值比例": [5.89, 2.78],
            "持股数": [675.78, 2855.22],
            "持仓市值": [1150782.02, 542948.18],
            "季度": ["2026年1季度股票投资明细", "2026年1季度股票投资明细"],
        })
        _result["holdings"] = get_etf_holdings("510300")


@then("返回结果包含股票代码和占净值比例字段")
def verify_etf_holdings():
    data = _result["holdings"]
    assert len(data) >= 1
    assert "code" in data[0]
    assert "ratio" in data[0]
