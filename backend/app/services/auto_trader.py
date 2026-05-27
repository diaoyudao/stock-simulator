"""自动交易系统 — 策略驱动闭环：AI选股买入 + 止损止盈卖出 + 完整风控。"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from app.services.ai_analysis import screen_stocks
from app.services.market_data import get_spot_data
from app.services.trading import (
    buy_stock,
    sell_stock,
    get_account,
    get_positions,
    _get_db,
    _calc_fees,
)

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))

_HOLIDAYS = {
    "2026-01-01", "2026-01-02", "2026-01-03",
    "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
    "2026-04-06",  # 清明
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
    "2026-06-19",  # 端午
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
    "2026-10-06", "2026-10-07",
}

_scheduler_task: asyncio.Task | None = None
_tier1_partial_sold: set[str] = set()


# ── Config CRUD ──────────────────────────────────────────────

async def _get_config() -> dict:
    db = await _get_db()
    cur = await db.execute("SELECT * FROM auto_trading_config WHERE id = 1")
    row = await cur.fetchone()
    if not row:
        return {}
    cols = [d[0] for d in cur.description or []]
    return dict(zip(cols, row))


async def _save_config(cfg: dict) -> None:
    db = await _get_db()
    fields = [k for k in cfg if k != "id"]
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    cfg["updated_at"] = time.time()
    await db.execute(f"UPDATE auto_trading_config SET {sets} WHERE id = 1", cfg)
    await db.commit()


async def _update_field(**kwargs) -> None:
    db = await _get_db()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [time.time()]
    await db.execute(
        f"UPDATE auto_trading_config SET {sets}, updated_at = ? WHERE id = 1", vals
    )
    await db.commit()


# ── Log ──────────────────────────────────────────────────────

async def _log(
    run_type: str,
    action: str,
    code: str = "",
    name: str = "",
    quantity: int = 0,
    price: float = 0.0,
    amount: float = 0.0,
    reason: str = "",
    signal_data: str = "",
    success: int = 1,
) -> None:
    db = await _get_db()
    await db.execute(
        """INSERT INTO auto_trade_log
           (run_type, action, code, name, quantity, price, amount, reason, signal_data, success, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_type, action, code, name, quantity, price, amount, reason, signal_data, success, time.time()),
    )
    await db.commit()


async def get_logs(limit: int = 50, run_type: str = "", action: str = "") -> list[dict]:
    db = await _get_db()
    where = []
    params: list = []
    if run_type:
        where.append("run_type = ?")
        params.append(run_type)
    if action:
        where.append("action = ?")
        params.append(action)
    sql = f"SELECT * FROM auto_trade_log {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY id DESC LIMIT ?"
    params.append(limit)
    cur = await db.execute(sql, params)
    rows = await cur.fetchall()
    cols = [d[0] for d in cur.description or []]
    return [dict(zip(cols, r)) for r in rows]


# ── Trading Time Helpers ────────────────────────────────────

def _is_workday(date_str: str | None = None) -> bool:
    if date_str is None:
        date_str = datetime.now(_CST).strftime("%Y-%m-%d")
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if dt.weekday() >= 5:
        return False
    if date_str in _HOLIDAYS:
        return False
    return True


def _is_in_session(t_min: int) -> bool:
    """t_min = hour*60+minute, 检查是否在交易时段内（含午休）。"""
    return (9 * 60 + 30 <= t_min <= 11 * 60 + 30) or (13 * 60 <= t_min <= 15 * 60)


def _today_str() -> str:
    return datetime.now(_CST).strftime("%Y-%m-%d")


# ── Daily State Reset ───────────────────────────────────────

async def _reset_daily_state_if_new_day() -> dict:
    config = await _get_config()
    today = _today_str()
    global _tier1_partial_sold
    if config.get("daily_bought_date") != today:
        await _update_field(
            daily_bought_date=today,
            daily_bought_amount=0.0,
        )
        _tier1_partial_sold.clear()
        config = await _get_config()
    return config


# ── Risk Control Engine ─────────────────────────────────────

class RiskResult:
    __slots__ = ("passed", "reason")

    def __init__(self, passed: bool, reason: str = ""):
        self.passed = passed
        self.reason = reason


async def check_pre_trade_risks(config: dict, price: float, quantity: int) -> RiskResult:
    trade_value = price * quantity
    if trade_value <= 0:
        return RiskResult(False, "交易金额无效")

    account = await get_account()
    positions = await get_positions()
    positions_value = sum(p.get("quantity", 0) * p.get("avg_cost", 0) for p in positions)
    total_assets = (account.get("cash") or 0) + positions_value

    # 更新峰值资产
    peak = config.get("peak_total_assets", 0) or 0
    if total_assets > peak:
        await _update_field(peak_total_assets=total_assets)
        config["peak_total_assets"] = total_assets

    # 1. 最大持仓数
    max_pos = config.get("max_positions", 10)
    if len(positions) >= max_pos:
        return RiskResult(False, f"已达最大持仓数限制（{max_pos}只）")

    # 2. 单笔上限
    max_pct = config.get("max_position_pct", 10)
    if trade_value > total_assets * max_pct / 100:
        return RiskResult(False, f"单笔金额超过总资产{max_pct}%限制")

    # 3. 日买上限
    daily_limit = total_assets * (config.get("max_daily_buy_pct", 30) / 100)
    daily_used = config.get("daily_bought_amount", 0) or 0
    if daily_used + trade_value > daily_limit:
        return RiskResult(False, f"今日买入已达上限（已用{daily_used:.0f}/{daily_limit:.0f}）")

    # 4. 回撤熔断
    dd_pct = config.get("max_drawdown_pct", 15)
    if peak > 0:
        drawdown = (peak - total_assets) / peak * 100
        if drawdown >= dd_pct:
            await _update_field(enabled=0)
            await _log("intraday_monitor", "circuit_break", reason=f"回撤{drawdown:.1f}%≥{dd_pct}%，自动暂停")
            return RiskResult(False, f"回撤{drawdown:.1f}%触及风控线，已暂停")

    # 5. 连亏熔断
    loss_limit = config.get("consecutive_loss_limit", 3)
    losses = config.get("consecutive_losses", 0) or 0
    if losses >= loss_limit:
        await _update_field(enabled=0)
        await _log("intraday_monitor", "circuit_break", reason=f"连续亏损{losses}次≥{loss_limit}，自动暂停")
        return RiskResult(False, f"连续亏损{losses}次触发熔断，已暂停")

    return RiskResult(True)


def evaluate_sell_signal(pnl_pct: float, config: dict) -> str:
    """返回信号类型: 'none' | 'tier2_full' | 'tier1_half'"""
    sl2 = config.get("stop_loss_tier2", -10)
    tp2 = config.get("take_profit_tier2", 20)
    sl1 = config.get("stop_loss_tier1", -5)
    tp1 = config.get("take_profit_tier1", 10)

    if pnl_pct <= sl2 or pnl_pct >= tp2:
        return "tier2_full"
    if pnl_pct <= sl1 or pnl_pct >= tp1:
        return "tier1_half"
    return "none"


# ── Price Map ───────────────────────────────────────────────

async def _build_price_map() -> dict[str, float]:
    try:
        data = await asyncio.to_thread(get_spot_data)
        if isinstance(data, list) and data:
            return {s.get("代码", ""): s.get("最新价", 0) or 0 for s in data if s.get("代码")}
    except Exception as e:
        logger.warning("构建价格映射失败: %s", e)
    return {}


# ── Opening Bell Routine ────────────────────────────────────

async def run_opening_bell() -> dict:
    summary = {"action": "opening_bell", "buys": 0, "skips": 0, "errors": 0}
    try:
        config = await _reset_daily_state_if_new_day()

        if not _is_workday():
            await _log("opening_bell", "skip", reason="非交易日")
            return {**summary, "reason": "非交易日"}

        await _log("opening_bell", "screen")

        results = await screen_stocks(
            min_price=config.get("min_price", 1),
            max_price=config.get("max_price", 8),
            top_n=(config.get("screen_top_n", 3) or 3) * 3,
            exclude_st=True,
            strategy=config.get("strategy", "oversold_bounce"),
        )
        if not results or not results.get("results"):
            await _log("opening_bell", "error", reason="选股结果为空")
            return {**summary, "reason": "选股无结果"}

        top_n = config.get("screen_top_n", 3) or 3
        picks = results["results"][:top_n]

        held_codes = {p["code"] for p in (await get_positions())}
        price_map = await _build_price_map()

        account = await get_account()
        positions = await get_positions()
        pos_value = sum(p.get("quantity", 0) * p.get("avg_cost", 0) for p in positions)
        total_assets = (account.get("cash") or 0) + pos_value

        for stock in picks:
            code = stock.get("代码", "")
            name = stock.get("名称", "")
            score = stock.get("综合得分", 0)

            if code in held_codes:
                summary["skips"] += 1
                await _log("opening_bell", "skip", code=code, name=name,
                          reason=f"已持仓，跳过(score={score})")
                continue

            price = price_map.get(code) or stock.get("最新价", 0)
            if not price or price <= 0:
                summary["errors"] += 1
                await _log("opening_bell", "error", code=code, name=name,
                          reason="无法获取价格")
                continue

            # 计算等权仓位
            per_max = total_assets * (config.get("max_position_pct", 10) / 100)
            equal_alloc = total_assets / len(picks)
            daily_budget = total_assets * (config.get("max_daily_buy_pct", 30) / 100) - (config.get("daily_bought_amount", 0) or 0)
            alloc = min(per_max, equal_alloc, daily_budget)
            if alloc <= 0:
                summary["skips"] += 1
                await _log("opening_bell", "skip", code=code, name=name,
                          reason="日预算耗尽")
                continue

            qty = int(alloc / price // 100) * 100
            if qty < 100:
                summary["skips"] += 1
                await _log("opening_bell", "skip", code=code, name=name,
                          reason=f"计算数量不足100股(alloc={alloc:.0f})")
                continue

            risk = await check_pre_trade_risks(config, price, qty)
            if not risk.passed:
                summary["skips"] += 1
                await _log("opening_bell", "skip", code=code, name=name,
                          reason=risk.reason)
                continue

            result = await buy_stock(code, name, qty, price)
            if result.get("success"):
                summary["buys"] += 1
                amt = price * qty
                new_daily = (config.get("daily_bought_amount", 0) or 0) + amt
                await _update_field(daily_bought_amount=new_daily)
                await _log("opening_bell", "buy", code=code, name=name,
                          quantity=qty, price=price, amount=amt,
                          reason=f"score={score}", signal_data=json.dumps(stock.get("因子明细", {}), ensure_ascii=False))
            else:
                summary["errors"] += 1
                await _log("opening_bell", "error", code=code, name=name,
                          reason=result.get("error", "未知错误"))

        await _update_field(last_opening_bell_run=_today_str())

    except Exception as e:
        logger.error("开盘扫描异常: %s", e, exc_info=True)
        summary["errors"] += 1
        await _log("opening_bell", "error", reason=str(e)[:200], success=0)

    return summary


# ── Intraday Monitor ───────────────────────────────────────

async def run_intraday_monitor() -> dict:
    summary = {"action": "intraday_monitor", "sells": 0, "skips": 0, "errors": 0}
    try:
        config = await _reset_daily_state_if_new_day()

        if not _is_workday():
            await _log("intraday_monitor", "skip", reason="非交易日")
            return {**summary, "reason": "非交易日"}

        positions = await get_positions()
        if not positions:
            return {**summary, "reason": "无持仓"}

        price_map = await _build_price_map()
        account = await get_account()
        pos_value = sum(p.get("quantity", 0) * p.get("avg_cost", 0) for p in positions)
        total_assets = (account.get("cash") or 0) + pos_value

        # 更新峰值
        peak = config.get("peak_total_assets", 0) or 0
        if total_assets > peak:
            await _update_field(peak_total_assets=total_assets)

        for pos in positions:
            code = pos.get("code", "")
            name = pos.get("name", "")
            qty = pos.get("quantity", 0)
            avg_cost = pos.get("avg_cost", 0)

            current_price = price_map.get(code)
            if not current_price or current_price <= 0:
                continue

            pnl_pct = (current_price - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0
            signal = evaluate_sell_signal(pnl_pct, config)

            if signal == "none":
                continue

            global _tier1_partial_sold
            if signal == "tier1_half" and code in _tier1_partial_sold:
                continue

            # 计算卖出数量
            if signal == "tier2_full":
                sell_qty = qty
                sig_label = f"{'止盈' if pnl_pct > 0 else '止损'}全出({pnl_pct:+.1f}%)"
            else:  # tier1_half
                sell_qty = int(qty * 0.5 // 100) * 100
                sig_label = f"{'止盈' if pnl_pct > 0 else '止损'}减半({pnl_pct:+.1f}%)"
                _tier1_partial_sold.add(code)

            if sell_qty < 100:
                continue

            result = await sell_stock(code, sell_qty, current_price)
            if result.get("success"):
                summary["sells"] += 1
                amt = current_price * sell_qty
                profit = (current_price - avg_cost) * sell_qty
                # 扣除买卖手续费
                _, _, _, buy_fee = _calc_fees(avg_cost * sell_qty)
                _, _, _, sell_fee = _calc_fees(current_price * sell_qty, is_sell=True)
                profit -= buy_fee + sell_fee
                # 更新连亏计数
                if profit < 0:
                    new_losses = (config.get("consecutive_losses", 0) or 0) + 1
                    await _update_field(consecutive_losses=new_losses)
                else:
                    await _update_field(consecutive_losses=0)

                await _log("intraday_monitor", "sell", code=code, name=name,
                          quantity=sell_qty, price=current_price, amount=amt,
                          reason=sig_label, signal_data=json.dumps({"pnl_pct": round(pnl_pct, 2)}))
            else:
                summary["errors"] += 1
                await _log("intraday_monitor", "error", code=code, name=name,
                          reason=result.get("error", ""))

        # 回撤检查
        peak = (await _get_config()).get("peak_total_assets", 0) or 0
        if peak > 0:
            dd = (peak - total_assets) / peak * 100
            dd_limit = config.get("max_drawdown_pct", 15)
            if dd >= dd_limit:
                await _update_field(enabled=0)
                await _log("intraday_monitor", "circuit_break",
                          reason=f"回撤{dd:.1f}%≥{dd_limit}%")

        await _update_field(last_monitor_run=time.time())

    except Exception as e:
        logger.error("盘中监控异常: %s", e, exc_info=True)
        summary["errors"] += 1
        await _log("intraday_monitor", "error", reason=str(e)[:200], success=0)

    return summary


# ── Scheduler ───────────────────────────────────────────────

async def start_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("自动交易调度器已启动")


async def stop_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
        _scheduler_task = None
    logger.info("自动交易调度器已停止")


async def _scheduler_loop() -> None:
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("调度器tick异常: %s", e)
        await asyncio.sleep(30)


async def _tick() -> None:
    config = await _get_config()
    if not config.get("enabled"):
        return

    now = datetime.now(_CST)
    t = now.hour * 60 + now.minute
    today = _today_str()

    # 开盘窗口检测: 9:30-9:35，每日一次
    if 9 * 60 + 30 <= t <= 9 * 60 + 35:
        if config.get("last_opening_bell_run") != today:
            logger.info("触发开盘扫描")
            await run_opening_bell()
            return

    # 盘中监控: 交易时段内，按间隔执行
    if _is_in_session(t):
        last_run = config.get("last_monitor_run", 0) or 0
        interval = config.get("monitor_interval_sec", 300) or 300
        if time.time() - last_run >= interval:
            logger.info("触发盘中监控")
            await run_intraday_monitor()


# ── Status ──────────────────────────────────────────────────

async def get_status() -> dict:
    config = await _get_config()
    logs_today = await get_logs(limit=200)
    buys_today = sum(1 for l in logs_today if l.get("action") == "buy")
    sells_today = sum(1 for l in logs_today if l.get("action") == "sell")
    buy_amt = sum(l.get("amount", 0) for l in logs_today if l.get("action") == "buy")
    sell_amt = sum(l.get("amount", 0) for l in logs_today if l.get("action") == "sell")

    status_text = "已停止"
    if config.get("enabled"):
        if (config.get("consecutive_losses", 0) or 0) >= (config.get("consecutive_loss_limit", 3) or 3):
            status_text = "已熔断"
        else:
            status_text = "运行中"

    return {
        **config,
        "status_text": status_text,
        "scheduler_running": _scheduler_task is not None and not _scheduler_task.done(),
        "today_summary": {
            "buys": buys_today,
            "sells": sells_today,
            "buy_amount": round(buy_amt, 2),
            "sell_amount": round(sell_amt, 2),
        },
    }
