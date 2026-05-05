import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np


def _make_spot_df(n=5):
    return pd.DataFrame({
        "序号": range(1, n + 1),
        "代码": [f"00000{i}" for i in range(1, n + 1)],
        "名称": [f"测试股票{i}" for i in range(1, n + 1)],
        "最新价": [10.0 + i for i in range(n)],
        "涨跌额": [0.5] * n,
        "涨跌幅": [1.0 + i * 0.1 for i in range(n)],
        "成交量": [100000.0] * n,
        "成交额": [1000000.0] * n,
        "振幅": [2.0] * n,
        "最高": [11.0 + i for i in range(n)],
        "最低": [9.0 + i for i in range(n)],
        "今开": [10.0 + i for i in range(n)],
        "昨收": [9.5 + i for i in range(n)],
        "量比": [1.0] * n,
        "换手率": [2.0] * n,
        "市盈率-动态": [15.0] * n,
        "市净率": [1.5] * n,
        "总市值": [1e10] * n,
        "流通市值": [5e9] * n,
        "涨速": [0.0] * n,
        "5分钟涨跌": [0.0] * n,
        "60日涨跌幅": [10.0] * n,
        "年初至今涨跌幅": [5.0] * n,
    })


def _make_kline_df(n=10):
    return pd.DataFrame({
        "日期": pd.date_range("2026-01-01", periods=n, freq="D").strftime("%Y-%m-%d"),
        "股票代码": ["000001"] * n,
        "开盘": [10.0] * n,
        "收盘": [10.5] * n,
        "最高": [11.0] * n,
        "最低": [9.5] * n,
        "成交量": [100000] * n,
        "成交额": [1000000.0] * n,
        "振幅": [1.5] * n,
        "涨跌幅": [1.0] * n,
        "涨跌额": [0.5] * n,
        "换手率": [2.0] * n,
    })


class TestConvertAkSpot:
    def test_basic_conversion(self):
        from app.services.market_data import _convert_ak_spot
        df = _make_spot_df(3)
        result = _convert_ak_spot(df)
        assert len(result) == 3
        assert result[0]["代码"] == "000001"
        assert result[0]["名称"] == "测试股票1"
        assert result[0]["最新价"] == 10.0
        assert result[0]["量比"] == 1.0

    def test_nan_replaced_with_zero(self):
        from app.services.market_data import _convert_ak_spot
        df = _make_spot_df(1)
        df.loc[0, "量比"] = np.nan
        df.loc[0, "市盈率-动态"] = np.nan
        result = _convert_ak_spot(df)
        assert result[0]["量比"] == 0.0
        assert result[0]["市盈率-动态"] == 0.0

    def test_buy_sell_default_to_price(self):
        from app.services.market_data import _convert_ak_spot
        df = _make_spot_df(1)
        result = _convert_ak_spot(df)
        assert result[0]["买一"] == result[0]["最新价"]
        assert result[0]["卖一"] == result[0]["最新价"]

    def test_volume_is_int(self):
        from app.services.market_data import _convert_ak_spot
        df = _make_spot_df(1)
        result = _convert_ak_spot(df)
        assert isinstance(result[0]["成交量"], int)

    def test_market_cap_no_multiplier(self):
        """akshare 的总市值已以元为单位，不再乘以 10000。"""
        from app.services.market_data import _convert_ak_spot
        df = _make_spot_df(1)
        df.loc[0, "总市值"] = 1e10
        result = _convert_ak_spot(df)
        assert result[0]["总市值"] == 1e10


class TestSafeFloat:
    def test_normal_value(self):
        from app.services.market_data import _safe_float
        assert _safe_float(3.14) == 3.14

    def test_nan(self):
        from app.services.market_data import _safe_float
        assert _safe_float(float("nan")) == 0.0

    def test_none(self):
        from app.services.market_data import _safe_float
        assert _safe_float(None) == 0.0

    def test_string(self):
        from app.services.market_data import _safe_float
        assert _safe_float("3.14") == 3.14

    def test_invalid_string(self):
        from app.services.market_data import _safe_float
        assert _safe_float("abc") == 0.0


class TestFetchAllStocks:
    @patch("app.services.market_data.ak.stock_zh_a_spot_em")
    def test_success(self, mock_ak):
        from app.services.market_data import _fetch_all_stocks
        mock_ak.return_value = _make_spot_df(3)
        result = _fetch_all_stocks()
        assert len(result) == 3
        assert result[0]["代码"] == "000001"

    @patch("app.services.market_data.ak.stock_zh_a_spot_em")
    def test_empty(self, mock_ak):
        from app.services.market_data import _fetch_all_stocks
        mock_ak.return_value = pd.DataFrame()
        result = _fetch_all_stocks()
        assert result == []

    @patch("app.services.market_data.ak.stock_zh_a_spot_em")
    def test_exception(self, mock_ak):
        from app.services.market_data import _fetch_all_stocks
        mock_ak.side_effect = Exception("network error")
        result = _fetch_all_stocks()
        assert result == []


class TestFilterLowPrice:
    @patch("app.services.market_data.get_spot_data")
    def test_price_range_filter(self, mock_spot):
        from app.services.market_data import filter_low_price
        mock_spot.return_value = [
            {"代码": "001", "名称": "A", "最新价": 2.0, "涨跌幅": 1.0, "涨跌额": 0.02,
             "今开": 2.0, "最高": 2.1, "最低": 1.9, "昨收": 1.98, "买一": 2.0, "卖一": 2.0,
             "成交量": 100, "成交额": 200, "换手率": 1.0, "市盈率-动态": 10.0,
             "市净率": 1.0, "总市值": 1e9, "流通市值": 5e8, "量比": 1.0},
            {"代码": "002", "名称": "B", "最新价": 10.0, "涨跌幅": -1.0, "涨跌额": -0.1,
             "今开": 10.0, "最高": 10.1, "最低": 9.9, "昨收": 10.1, "买一": 10.0, "卖一": 10.0,
             "成交量": 200, "成交额": 2000, "换手率": 2.0, "市盈率-动态": 20.0,
             "市净率": 2.0, "总市值": 2e9, "流通市值": 1e9, "量比": 0.5},
        ]
        result = filter_low_price(min_price=1.0, max_price=5.0)
        assert result["total"] == 1
        assert result["items"][0]["代码"] == "001"

    @patch("app.services.market_data.get_spot_data")
    def test_st_exclusion(self, mock_spot):
        from app.services.market_data import filter_low_price
        mock_spot.return_value = [
            {"代码": "001", "名称": "ST某股", "最新价": 3.0, "涨跌幅": 5.0, "涨跌额": 0.15,
             "今开": 3.0, "最高": 3.1, "最低": 2.9, "昨收": 2.85, "买一": 3.0, "卖一": 3.0,
             "成交量": 100, "成交额": 300, "换手率": 1.0, "市盈率-动态": 10.0,
             "市净率": 1.0, "总市值": 1e9, "流通市值": 5e8, "量比": 1.0},
            {"代码": "002", "名称": "正常股", "最新价": 3.0, "涨跌幅": 1.0, "涨跌额": 0.03,
             "今开": 3.0, "最高": 3.1, "最低": 2.9, "昨收": 2.97, "买一": 3.0, "卖一": 3.0,
             "成交量": 100, "成交额": 300, "换手率": 1.0, "市盈率-动态": 10.0,
             "市净率": 1.0, "总市值": 1e9, "流通市值": 5e8, "量比": 1.0},
        ]
        result = filter_low_price(exclude_st=True)
        assert result["total"] == 1
        assert result["items"][0]["名称"] == "正常股"

    @patch("app.services.market_data.get_spot_data")
    def test_pagination(self, mock_spot):
        from app.services.market_data import filter_low_price
        mock_spot.return_value = [
            {"代码": f"00{i}", "名称": f"S{i}", "最新价": 3.0, "涨跌幅": float(i),
             "涨跌额": 0.01, "今开": 3.0, "最高": 3.1, "最低": 2.9, "昨收": 2.99,
             "买一": 3.0, "卖一": 3.0, "成交量": 100, "成交额": 300, "换手率": 1.0,
             "市盈率-动态": 10.0, "市净率": 1.0, "总市值": 1e9, "流通市值": 5e8, "量比": 1.0}
            for i in range(10)
        ]
        result = filter_low_price(page=1, page_size=3)
        assert result["total"] == 10
        assert len(result["items"]) == 3
        assert result["page"] == 1


class TestGetStockHistory:
    def setup_method(self):
        import app.services.market_data as md
        md._kline_cache.clear()

    @patch("app.services.market_data.ak.stock_zh_a_hist")
    def test_daily_kline(self, mock_ak):
        from app.services.market_data import get_stock_history
        mock_ak.return_value = _make_kline_df(5)
        result = get_stock_history("000001", "daily")
        assert len(result) == 5
        assert "day" in result[0]
        assert "open" in result[0]
        assert "close" in result[0]
        assert isinstance(result[0]["day"], str)
        assert isinstance(result[0]["open"], str)

    @patch("app.services.market_data.ak.stock_zh_a_hist")
    def test_monthly_kline(self, mock_ak):
        from app.services.market_data import get_stock_history
        mock_ak.return_value = _make_kline_df(3)
        result = get_stock_history("000001", "monthly")
        mock_ak.assert_called_once_with(
            symbol="000001", period="monthly", start_date="20250101", adjust="qfq"
        )
        assert len(result) == 3

    @patch("app.services.market_data.ak.stock_zh_a_hist")
    def test_empty_result(self, mock_ak):
        from app.services.market_data import get_stock_history
        mock_ak.return_value = pd.DataFrame()
        result = get_stock_history("000001", "daily")
        assert result == []

    @patch("app.services.market_data.ak.stock_zh_a_hist")
    def test_exception(self, mock_ak):
        from app.services.market_data import get_stock_history
        mock_ak.side_effect = Exception("error")
        result = get_stock_history("000001", "daily")
        assert result == []


class TestGetIndexData:
    @patch("app.services.market_data.ak.stock_zh_index_spot_em")
    def test_three_indices(self, mock_ak):
        from app.services.market_data import get_index_data

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

        mock_ak.side_effect = side_effect
        result = get_index_data()
        assert len(result) == 3
        assert result[0]["code"] == "sh000001"
        assert result[0]["name"] == "上证指数"
        assert result[0]["current"] == 4100.0


class TestComputeConsecutiveDays:
    def test_consecutive_down(self):
        """最近一天下跌，前一天上涨，连跌1天。"""
        from app.services.market_data import compute_consecutive_days
        klines = [
            {"day": "1", "open": "10", "close": "11", "high": "11", "low": "10", "volume": "100"},
            {"day": "2", "open": "11", "close": "12", "high": "12", "low": "11", "volume": "100"},
            {"day": "3", "open": "12", "close": "11", "high": "12", "low": "11", "volume": "100"},
        ]
        with patch("app.services.market_data.get_stock_history", return_value=klines):
            result = compute_consecutive_days("000001")
        assert result["连涨天数"] == 0
        assert result["连跌天数"] == 1

    def test_empty_klines(self):
        from app.services.market_data import compute_consecutive_days
        with patch("app.services.market_data.get_stock_history", return_value=[]):
            result = compute_consecutive_days("000001")
        assert result["连涨天数"] == 0
        assert result["连跌天数"] == 0


class TestSectorList:
    @patch("app.services.market_data.ak.stock_board_industry_name_em")
    def test_sector_list(self, mock_ak):
        from app.services.market_data import _fetch_sector_list
        mock_ak.return_value = pd.DataFrame({
            "排名": [1, 2],
            "板块名称": ["银行", "地产"],
            "板块代码": ["BK1", "BK2"],
        })
        # 清除缓存
        import app.services.market_data as md
        md._sector_list_cache = []
        md._sector_list_cache_time = 0
        result = _fetch_sector_list()
        assert len(result) == 2
        assert result[0]["name"] == "银行"
        assert result[0]["code"] == "BK1"
