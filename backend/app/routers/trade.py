import asyncio
from datetime import datetime
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel

from app.services.market_data import get_spot_data
from app.services.trading import (
    buy_stock, sell_stock, get_account, get_positions, get_transactions, reset_account,
    add_watchlist, remove_watchlist, get_watchlist,
    create_order, get_orders, cancel_order, check_and_fill_orders,
    record_daily_snapshot, get_daily_snapshots, get_performance_stats,
    get_groups, create_group, rename_group, delete_group, move_to_group,
    create_alert, get_alerts, cancel_alert, check_alerts,
)

router = APIRouter()

def _check_error(result: dict):
    """如果结果包含error，抛出HTTP 400。"""
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result



# 2026年法定节假日（国务院办公厅公告）
_HOLIDAYS_2026 = {
    # 元旦
    "2026-01-01", "2026-01-02", "2026-01-03",
    # 春节
    "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
    # 清明节
    "2026-04-04", "2026-04-05", "2026-04-06",
    # 劳动节
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
    # 端午节
    "2026-06-19", "2026-06-20", "2026-06-21",
    # 中秋节
    "2026-09-25", "2026-09-26", "2026-09-27",
    # 国庆节
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
    "2026-10-06", "2026-10-07",
}
# 调休上班日（周末但需上班）
_WORKDAYS_2026 = {
    "2026-01-04",   # 元旦调休
    "2026-02-14", "2026-02-28",  # 春节调休
    "2026-05-09",   # 劳动节调休
    "2026-09-20",   # 国庆调休
    "2026-10-10",   # 国庆调休
}


def _is_workday(date_str: str | None = None) -> bool:
    """判断是否为交易日（工作日且非节假日，或调休上班日）。"""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    if date_str in _HOLIDAYS_2026:
        return False
    if date_str in _WORKDAYS_2026:
        return True
    return datetime.now().weekday() < 5 if date_str == datetime.now().strftime("%Y-%m-%d") \
        else datetime.strptime(date_str, "%Y-%m-%d").weekday() < 5


def _check_trading_time():
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    if not _is_workday(date_str):
        if date_str in _HOLIDAYS_2026:
            return False, "休市（节假日）"
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
    from app.services.market_data import get_stock_by_code
    s = get_stock_by_code(code)
    return s["最新价"] if s else None


def _build_price_map() -> dict[str, float]:
    from app.services.market_data import get_price_map
    return get_price_map()


async def _build_price_map_async() -> dict[str, float]:
    return await asyncio.to_thread(_build_price_map)


@router.get("/account")
async def account():
    acc = await get_account()
    positions = await get_positions()
    try:
        price_map = await _build_price_map_async()
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
        price_map = await _build_price_map_async()
        for pos in positions:
            current_price = price_map.get(pos["code"], pos["avg_cost"])
            pos["current_price"] = current_price
            pos["market_value"] = round(current_price * pos["quantity"], 2)
            pos["profit"] = round((current_price - pos["avg_cost"]) * pos["quantity"], 2)
            pos["profit_pct"] = round((current_price - pos["avg_cost"]) / pos["avg_cost"] * 100, 2) if pos["avg_cost"] > 0 else 0.0
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
        raise HTTPException(status_code=400, detail=f"非交易时间（{msg}）")
    price = req.price
    if price is None:
        price = await asyncio.to_thread(_get_price, req.code)
        if price is None:
            raise HTTPException(status_code=400, detail="股票代码不存在")
    result = await buy_stock(req.code, req.name, req.quantity, price)
    return _check_error(result)


@router.post("/sell")
async def sell(req: SellRequest):
    ok, msg = _check_trading_time()
    if not ok:
        raise HTTPException(status_code=400, detail=f"非交易时间（{msg}）")
    price = req.price
    if price is None:
        price = await asyncio.to_thread(_get_price, req.code)
        if price is None:
            raise HTTPException(status_code=400, detail="股票代码不存在")
    result = await sell_stock(req.code, req.quantity, price)
    return _check_error(result)


@router.get("/transactions")
async def transactions(
    limit: int = Query(50, ge=1, le=200),
    start_date: str | None = Query(None, description="YYYY-MM-DD"),
    end_date: str | None = Query(None, description="YYYY-MM-DD"),
    action: str | None = Query(None, description="buy or sell"),
):
    return await get_transactions(limit, start_date, end_date, action)


@router.get("/dashboard")
async def dashboard():
    """合并账户+持仓+市场状态，减少前端请求次数和 price_map 重复计算。"""
    acc_raw = await get_account()
    positions_raw = await get_positions()
    try:
        price_map = await _build_price_map_async()
    except Exception:
        price_map = {}

    # 计算持仓市值（复用 price_map）
    for pos in positions_raw:
        current_price = price_map.get(pos["code"], pos["avg_cost"])
        pos["current_price"] = current_price
        pos["market_value"] = round(current_price * pos["quantity"], 2)
        pos["profit"] = round((current_price - pos["avg_cost"]) * pos["quantity"], 2)
        pos["profit_pct"] = round((current_price - pos["avg_cost"]) / pos["avg_cost"] * 100, 2) if pos["avg_cost"] > 0 else 0.0

    market_value = sum(
        price_map.get(p["code"], p["avg_cost"]) * p["quantity"]
        for p in positions_raw
    )
    total = acc_raw["cash"] + market_value
    profit = total - acc_raw["initial_cash"]
    profit_pct = (profit / acc_raw["initial_cash"]) * 100

    ok, msg = _check_trading_time()
    return {
        "account": {
            "cash": round(acc_raw["cash"], 2),
            "market_value": round(market_value, 2),
            "total_assets": round(total, 2),
            "total_profit": round(profit, 2),
            "profit_pct": round(profit_pct, 2),
        },
        "positions": positions_raw,
        "market_status": {
            "is_trading_time": ok,
            "status": msg,
            "sessions": [
                {"name": "上午盘", "start": "09:30", "end": "11:30"},
                {"name": "下午盘", "start": "13:00", "end": "15:00"},
            ],
        },
    }


@router.get("/market-status")
async def market_status():
    ok, msg = _check_trading_time()
    return {
        "is_trading_time": ok,
        "status": msg,
        "sessions": [
            {"name": "上午盘", "start": "09:30", "end": "11:30"},
            {"name": "下午盘", "start": "13:00", "end": "15:00"},
        ],
    }


@router.post("/reset")
async def reset():
    return await reset_account()


class OrderRequest(BaseModel):
    code: str
    name: str = ""
    action: str  # "buy" or "sell"
    quantity: int
    target_price: float


@router.get("/watchlist")
async def watchlist(group_id: int | None = Query(None)):
    items = await get_watchlist(group_id)
    if not items:
        return []
    price_map = {s["代码"]: s for s in await asyncio.to_thread(get_spot_data)}
    result = []
    for item in items:
        stock = price_map.get(item["code"])
        if stock:
            result.append({**item, "price": stock["最新价"], "change_pct": stock["涨跌幅"], "change_amt": stock["涨跌额"]})
        else:
            result.append({**item, "price": 0, "change_pct": 0, "change_amt": 0})
    return result


class WatchRequest(BaseModel):
    code: str
    name: str = ""
    group_id: int = 1


@router.post("/watchlist/add")
async def watchlist_add(req: WatchRequest):
    return await add_watchlist(req.code, req.name, req.group_id)


@router.post("/watchlist/remove")
async def watchlist_remove(req: WatchRequest):
    return await remove_watchlist(req.code)


# ─── 自选股分组 ───

@router.get("/groups")
async def groups():
    return await get_groups()


class GroupNameRequest(BaseModel):
    name: str


class GroupIdRequest(BaseModel):
    group_id: int


@router.post("/groups/create")
async def groups_create(req: GroupNameRequest):
    return await create_group(req.name)


@router.post("/groups/rename")
async def groups_rename(group_id: int, req: GroupNameRequest):
    return await rename_group(group_id, req.name)


@router.post("/groups/delete")
async def groups_delete(req: GroupIdRequest):
    return await delete_group(req.group_id)


class MoveRequest(BaseModel):
    code: str
    group_id: int


@router.post("/watchlist/move")
async def watchlist_move(req: MoveRequest):
    return await move_to_group(req.code, req.group_id)


# ─── 涨跌提醒 ───

class AlertRequest(BaseModel):
    code: str
    name: str = ""
    condition: str  # "above" / "below"
    value: float


@router.post("/alert")
async def alert_create(req: AlertRequest):
    return await create_alert(req.code, req.name, req.condition, req.value)


@router.get("/alerts")
async def alert_list(status: str | None = Query(None)):
    return await get_alerts(status)


@router.post("/alert/{alert_id}/cancel")
async def alert_cancel(alert_id: int):
    return await cancel_alert(alert_id)


@router.post("/order")
async def create_limit_order(req: OrderRequest):
    ok, msg = _check_trading_time()
    if not ok:
        raise HTTPException(status_code=400, detail=f"非交易时间（{msg}）")
    result = await create_order(req.code, req.name, req.action, req.quantity, req.target_price)
    return _check_error(result)


@router.get("/orders")
async def orders(status: str | None = Query(None)):
    return await get_orders(status)


@router.post("/order/{order_id}/cancel")
async def cancel_limit_order(order_id: int):
    return await cancel_order(order_id)


@router.post("/orders/check")
async def check_orders():
    """手动触发委托单检查 + 提醒检查（前端轮询调用）。"""
    price_map = await _build_price_map_async()
    filled = await check_and_fill_orders(price_map)
    triggered_alerts = await check_alerts(price_map)
    await record_daily_snapshot(price_map)
    return {"filled_count": len(filled), "filled_orders": filled, "triggered_alerts": triggered_alerts}


@router.get("/daily-snapshots")
async def daily_snapshots(days: int = Query(90, ge=1, le=365)):
    return await get_daily_snapshots(days)


@router.get("/performance")
async def performance():
    return await get_performance_stats()


@router.post("/snapshot")
async def snapshot():
    """手动记录当日资产快照。"""
    try:
        price_map = await _build_price_map_async()
    except Exception:
        price_map = None
    return await record_daily_snapshot(price_map)
