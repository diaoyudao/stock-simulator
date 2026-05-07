from fastapi import APIRouter, Query
from app.services.market_data import (
    filter_etf, get_etf_detail, get_etf_history, get_etf_minute_history,
    get_etf_fund_flow, get_etf_nav, get_etf_holdings, get_etf_allocation,
)
from app.utils import validate_code

router = APIRouter()


@router.get("/spot")
async def spot(
    min_price: float = Query(0, ge=0),
    max_price: float = Query(999, ge=0),
    min_change_pct: float | None = Query(None),
    max_change_pct: float | None = Query(None),
    min_amount: float | None = Query(None, description="最小成交额(元)"),
    etf_type: str | None = Query(None, description="基金类型: 指数/债券/商品/货币/跨境"),
    keyword: str | None = Query(None),
    sort_by: str = Query("涨跌幅"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """ETF筛选列表。"""
    valid_sort = {"涨跌幅", "最新价", "成交额", "成交量", "换手率", "涨跌额"}
    if sort_by not in valid_sort:
        sort_by = "涨跌幅"
    return filter_etf(
        min_price=min_price, max_price=max_price,
        min_change_pct=min_change_pct, max_change_pct=max_change_pct,
        min_amount=min_amount, etf_type=etf_type, keyword=keyword,
        sort_by=sort_by, sort_order=sort_order,
        page=page, page_size=page_size,
    )


@router.get("/detail/{code}")
async def detail(code: str):
    """ETF详情。"""
    validate_code(code)
    return get_etf_detail(code)


@router.get("/history/{code}")
async def history(code: str, period: str = Query("daily")):
    """ETF K线数据。"""
    validate_code(code)
    valid = {"daily", "weekly", "monthly"}
    if period not in valid:
        period = "daily"
    return get_etf_history(code, period)


@router.get("/minute/{code}")
async def minute(code: str, period: str = Query("1")):
    """ETF分钟K线。"""
    validate_code(code)
    valid = {"1", "5", "15", "30", "60"}
    if period not in valid:
        period = "1"
    return get_etf_minute_history(code, period)


@router.get("/fund-flow/{code}")
async def fund_flow(code: str):
    """ETF资金流向。"""
    validate_code(code)
    return get_etf_fund_flow(code)


@router.get("/nav/{code}")
async def nav(code: str):
    """ETF历史净值。"""
    validate_code(code)
    return get_etf_nav(code)


@router.get("/holdings/{code}")
async def holdings(code: str):
    """ETF十大持仓。"""
    validate_code(code)
    return get_etf_holdings(code)


@router.get("/allocation/{code}")
async def allocation(code: str):
    """ETF资产/行业配置。"""
    validate_code(code)
    return get_etf_allocation(code)
