from fastapi import APIRouter, Query

from app.services.market_data import (
    filter_low_price, get_stock_detail, get_stock_history,
    get_sector_list, get_sector_overview, compute_consecutive_days,
    get_index_data, get_financial_abstract, get_financial_statement,
    get_stock_news, get_intraday, get_bid_ask, get_fund_flow,
    get_minute_history,
)

router = APIRouter()


@router.get("/spot")
def spot(
    min_price: float = Query(1.0),
    max_price: float = Query(5.0),
    min_change_pct: float | None = Query(None),
    max_change_pct: float | None = Query(None),
    min_turnover_rate: float | None = Query(None),
    min_volume: float | None = Query(None),
    min_amount: float | None = Query(None),
    min_pe: float | None = Query(None),
    max_pe: float | None = Query(None),
    min_pb: float | None = Query(None),
    max_pb: float | None = Query(None),
    min_mktcap: float | None = Query(None),
    max_mktcap: float | None = Query(None),
    min_nmc: float | None = Query(None),
    max_nmc: float | None = Query(None),
    min_amplitude: float | None = Query(None),
    max_amplitude: float | None = Query(None),
    min_volume_ratio: float | None = Query(None),
    max_volume_ratio: float | None = Query(None),
    near_52week_high: bool = Query(False),
    near_52week_low: bool = Query(False),
    sector: str | None = Query(None),
    exclude_st: bool = Query(False),
    only_st: bool = Query(False),
    keyword: str | None = Query(None),
    sort_by: str = Query("涨跌幅"),
    sort_order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    return filter_low_price(
        min_price=min_price, max_price=max_price,
        min_change_pct=min_change_pct, max_change_pct=max_change_pct,
        min_turnover_rate=min_turnover_rate, min_volume=min_volume,
        min_amount=min_amount,
        min_pe=min_pe, max_pe=max_pe,
        min_pb=min_pb, max_pb=max_pb,
        min_mktcap=min_mktcap, max_mktcap=max_mktcap,
        min_nmc=min_nmc, max_nmc=max_nmc,
        min_amplitude=min_amplitude, max_amplitude=max_amplitude,
        min_volume_ratio=min_volume_ratio, max_volume_ratio=max_volume_ratio,
        near_52week_high=near_52week_high, near_52week_low=near_52week_low,
        sector=sector,
        exclude_st=exclude_st, only_st=only_st,
        keyword=keyword,
        sort_by=sort_by, sort_order=sort_order,
        page=page, page_size=page_size,
    )


@router.get("/sectors")
def sectors():
    return get_sector_list()


@router.get("/sector-overview")
def sector_overview():
    return get_sector_overview()


@router.get("/consecutive/{code}")
def consecutive(code: str):
    return compute_consecutive_days(code)


@router.get("/detail/{code}")
def detail(code: str):
    data = get_stock_detail(code)
    if not data:
        return {"error": "stock not found"}
    return data


@router.get("/history/{code}")
def history(
    code: str,
    period: str = Query("daily"),
    start_date: str = Query("20250101"),
):
    return get_stock_history(code, period=period, start_date=start_date)


@router.get("/indices")
def indices():
    return get_index_data()


@router.get("/financial/abstract/{code}")
def financial_abstract(code: str):
    return get_financial_abstract(code)


@router.get("/financial/statement/{code}")
def financial_statement(code: str, type: str = Query("利润表")):
    return get_financial_statement(code, type)


@router.get("/news/{code}")
def stock_news(code: str):
    return get_stock_news(code)


@router.get("/intraday/{code}")
def intraday(code: str):
    return get_intraday(code)


@router.get("/bidask/{code}")
def bidask(code: str):
    return get_bid_ask(code)


@router.get("/fund-flow/{code}")
def fund_flow(code: str):
    return get_fund_flow(code)


@router.get("/minute/{code}")
def minute_history(code: str, period: str = Query("1")):
    return get_minute_history(code, period)
