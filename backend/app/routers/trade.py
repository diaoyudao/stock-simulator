from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services.market_data import get_spot_data
from app.services.trading import (
    buy_stock, sell_stock, get_account, get_positions, get_transactions, reset_account,
)

router = APIRouter()


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
    price = req.price
    if price is None:
        price = _get_price(req.code)
        if price is None:
            return {"error": "股票代码不存在"}
    return await buy_stock(req.code, req.name, req.quantity, price)


@router.post("/sell")
async def sell(req: SellRequest):
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
