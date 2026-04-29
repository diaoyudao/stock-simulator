from datetime import datetime
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services.market_data import get_spot_data
from app.services.trading import (
    buy_stock, sell_stock, get_account, get_positions, get_transactions, reset_account,
    add_watchlist, remove_watchlist, get_watchlist,
)

router = APIRouter()


def _check_trading_time():
    now = datetime.now()
    if now.weekday() >= 5:
        return False, "休市（周末）"
    t = now.hour * 60 + now.minute
    if t < 9 * 60 + 30:
        return False, "尚未开盘"
    if t <= 11 * 60 + 30:
        return True, "交易中"
    if t < 13 * 60:
        return False, "午间休市"
    if t <= 15 * 60:
        return True, "交易中"
    return False, "已收盘"


class BuyRequest(BaseModel):
    code: str
    name: str = ""
    quantity: int
    price: float | None = None


class SellRequest(BaseModel):
    code: str
    quantity: int
    price: float | None = None


def _get_price(code: str) -> float | None:
    for s in get_spot_data():
        if s["代码"] == code:
            return s["最新价"]
    return None


def _build_price_map() -> dict[str, float]:
    return {s["代码"]: s["最新价"] for s in get_spot_data()}


@router.get("/account")
async def account():
    acc = await get_account()
    positions = await get_positions()
    try:
        price_map = _build_price_map()
        market_value = sum(
            price_map.get(p["code"], p["avg_cost"]) * p["quantity"]
            for p in positions
        )
    except Exception:
        market_value = sum(p["avg_cost"] * p["quantity"] for p in positions)

    total = acc["cash"] + market_value
    profit = total - acc["initial_cash"]
    profit_pct = (profit / acc["initial_cash"]) * 100

    return {
        "cash": round(acc["cash"], 2),
        "market_value": round(market_value, 2),
        "total_assets": round(total, 2),
        "total_profit": round(profit, 2),
        "profit_pct": round(profit_pct, 2),
    }


@router.get("/positions")
async def positions():
    positions = await get_positions()
    try:
        price_map = _build_price_map()
        for pos in positions:
            current_price = price_map.get(pos["code"], pos["avg_cost"])
            pos["current_price"] = current_price
            pos["market_value"] = round(current_price * pos["quantity"], 2)
            pos["profit"] = round((current_price - pos["avg_cost"]) * pos["quantity"], 2)
            pos["profit_pct"] = round((current_price - pos["avg_cost"]) / pos["avg_cost"] * 100, 2)
    except Exception:
        for pos in positions:
            pos["current_price"] = pos["avg_cost"]
            pos["market_value"] = round(pos["avg_cost"] * pos["quantity"], 2)
            pos["profit"] = 0.0
            pos["profit_pct"] = 0.0
    return positions


@router.post("/buy")
async def buy(req: BuyRequest):
    ok, msg = _check_trading_time()
    if not ok:
        return {"error": f"非交易时间（{msg}）"}
    price = req.price
    if price is None:
        price = _get_price(req.code)
        if price is None:
            return {"error": "股票代码不存在"}
    return await buy_stock(req.code, req.name, req.quantity, price)


@router.post("/sell")
async def sell(req: SellRequest):
    ok, msg = _check_trading_time()
    if not ok:
        return {"error": f"非交易时间（{msg}）"}
    price = req.price
    if price is None:
        price = _get_price(req.code)
        if price is None:
            return {"error": "股票代码不存在"}
    return await sell_stock(req.code, req.quantity, price)


@router.get("/transactions")
async def transactions(
    limit: int = Query(50, ge=1, le=200),
    start_date: str | None = Query(None, description="YYYY-MM-DD"),
    end_date: str | None = Query(None, description="YYYY-MM-DD"),
    action: str | None = Query(None, description="buy or sell"),
):
    return await get_transactions(limit, start_date, end_date, action)


@router.post("/reset")
async def reset():
    return await reset_account()


class WatchRequest(BaseModel):
    code: str
    name: str = ""


@router.get("/watchlist")
async def watchlist():
    items = await get_watchlist()
    if not items:
        return []
    price_map = {s["代码"]: s for s in get_spot_data()}
    result = []
    for item in items:
        stock = price_map.get(item["code"])
        if stock:
            result.append({**item, "price": stock["最新价"], "change_pct": stock["涨跌幅"], "change_amt": stock["涨跌额"]})
        else:
            result.append({**item, "price": 0, "change_pct": 0, "change_amt": 0})
    return result


@router.post("/watchlist/add")
async def watchlist_add(req: WatchRequest):
    return await add_watchlist(req.code, req.name)


@router.post("/watchlist/remove")
async def watchlist_remove(req: WatchRequest):
    return await remove_watchlist(req.code)
