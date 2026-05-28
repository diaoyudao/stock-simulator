"""自动交易 API 路由 — 配置/开关/手动触发/状态/日志。"""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services.auto_trader import (
    _get_config,
    _save_config,
    _update_field,
    _log,
    get_logs,
    run_opening_bell,
    run_intraday_monitor,
    run_closing_bell,
    start_scheduler,
    stop_scheduler,
    get_status,
    get_performance_analytics,
)

router = APIRouter()


class ConfigUpdate(BaseModel):
    strategy: str | None = None
    max_position_pct: float | None = None
    max_daily_buy_pct: float | None = None
    max_positions: int | None = None
    stop_loss_tier1: float | None = None
    stop_loss_tier2: float | None = None
    take_profit_tier1: float | None = None
    take_profit_tier2: float | None = None
    max_drawdown_pct: float | None = None
    consecutive_loss_limit: int | None = None
    monitor_interval_sec: int | None = None
    screen_top_n: int | None = None
    min_price: float | None = None
    max_price: float | None = None
    trailing_stop_enabled: int | None = None
    trailing_stop_pct: float | None = None


class ToggleRequest(BaseModel):
    enabled: bool


@router.get("/config")
async def get_config():
    return await _get_config() or {}


@router.put("/config")
async def update_config(req: ConfigUpdate):
    cfg = await _get_config()
    if not cfg:
        raise ValueError("配置表未初始化")
    updates = req.model_dump(exclude_none=True)
    cfg.update(updates)
    await _save_config(cfg)
    return cfg


@router.post("/toggle")
async def toggle(req: ToggleRequest):
    enabled = 1 if req.enabled else 0
    await _update_field(enabled=enabled)
    if enabled:
        await start_scheduler()
    else:
        await stop_scheduler()
    return await _get_config()


@router.post("/run-opening")
async def manual_opening():
    result = await run_opening_bell()
    return result


@router.post("/run-monitor")
async def manual_monitor():
    result = await run_intraday_monitor()
    return result


@router.post("/run-closing")
async def manual_closing():
    result = await run_closing_bell()
    return result


@router.post("/reset-circuit")
async def reset_circuit():
    await _update_field(
        enabled=1,
        consecutive_losses=0,
        peak_total_assets=0,
    )
    await start_scheduler()
    return {"message": "熔断已重置，自动交易已重新启用"}


@router.get("/status")
async def status():
    return await get_status()


@router.get("/logs")
async def logs(
    limit: int = Query(50, ge=1, le=200),
    run_type: str = Query(""),
    action: str = Query(""),
):
    return await get_logs(limit=limit, run_type=run_type, action=action)


@router.get("/performance")
async def performance(days: int = Query(90, ge=7, le=365)):
    return await get_performance_analytics(days=days)
