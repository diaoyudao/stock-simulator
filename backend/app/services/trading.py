import asyncio
import aiosqlite
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "stock_sim.db"
INITIAL_CASH = 100000.0

# ─── A股交易手续费 ───
COMMISSION_RATE = 0.00025      # 佣金万2.5
COMMISSION_MIN = 5.0           # 佣金最低5元
STAMP_TAX_RATE = 0.001         # 印花税千1（仅卖出）
TRANSFER_FEE_RATE = 0.00001    # 过户费十万1（买卖双向）


def _calc_fees(amount: float, is_sell: bool = False) -> tuple[float, float, float, float]:
    """计算交易手续费。返回 (佣金, 印花税, 过户费, 总费用)。"""
    commission = max(amount * COMMISSION_RATE, COMMISSION_MIN)
    stamp_tax = amount * STAMP_TAX_RATE if is_sell else 0.0
    transfer_fee = amount * TRANSFER_FEE_RATE
    return commission, stamp_tax, transfer_fee, commission + stamp_tax + transfer_fee

# 全局共享连接，避免每次请求都新建连接和建表
_db: aiosqlite.Connection | None = None
_tables_ready = False

# 交易锁 — 防止并发买卖导致竞态条件
_trade_lock = asyncio.Lock()


async def _get_db() -> aiosqlite.Connection:
    global _db, _tables_ready
    if _db is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _db = await aiosqlite.connect(str(DB_PATH))
        _db.row_factory = aiosqlite.Row
    if not _tables_ready:
        await _ensure_tables(_db)
        _tables_ready = True
    return _db


async def _ensure_tables(db: aiosqlite.Connection):
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS account (
            id INTEGER PRIMARY KEY DEFAULT 1,
            cash REAL NOT NULL DEFAULT 100000.0
        );
        CREATE TABLE IF NOT EXISTS positions (
            code TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            quantity INTEGER NOT NULL DEFAULT 0,
            avg_cost REAL NOT NULL DEFAULT 0.0,
            PRIMARY KEY (code)
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL CHECK(action IN ('buy', 'sell')),
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            amount REAL NOT NULL,
            fee REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
        INSERT OR IGNORE INTO account (id, cash) VALUES (1, 100000.0);
        CREATE TABLE IF NOT EXISTS watchlist (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            added_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pending_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL CHECK(action IN ('buy', 'sell')),
            quantity INTEGER NOT NULL,
            target_price REAL NOT NULL,
            avg_cost REAL NOT NULL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'filled', 'cancelled')),
            created_at REAL NOT NULL,
            filled_at REAL,
            filled_price REAL
        );
        CREATE TABLE IF NOT EXISTS daily_snapshots (
            date TEXT PRIMARY KEY,
            cash REAL NOT NULL,
            positions_value REAL NOT NULL,
            total REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS watchlist_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        INSERT OR IGNORE INTO watchlist_groups (id, name, sort_order) VALUES (1, '我的自选', 0);
        CREATE TABLE IF NOT EXISTS price_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            condition TEXT NOT NULL CHECK(condition IN ('above', 'below', 'change_up', 'change_down')),
            value REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'triggered', 'cancelled')),
            created_at REAL NOT NULL,
            triggered_at REAL,
            message TEXT
        );
        CREATE TABLE IF NOT EXISTS auto_trading_config (
            id INTEGER PRIMARY KEY DEFAULT 1,
            enabled INTEGER NOT NULL DEFAULT 0,
            strategy TEXT NOT NULL DEFAULT 'oversold_bounce',
            max_position_pct REAL NOT NULL DEFAULT 10.0,
            max_daily_buy_pct REAL NOT NULL DEFAULT 30.0,
            max_positions INTEGER NOT NULL DEFAULT 10,
            stop_loss_tier1 REAL NOT NULL DEFAULT -5.0,
            stop_loss_tier2 REAL NOT NULL DEFAULT -10.0,
            take_profit_tier1 REAL NOT NULL DEFAULT 10.0,
            take_profit_tier2 REAL NOT NULL DEFAULT 20.0,
            max_drawdown_pct REAL NOT NULL DEFAULT 15.0,
            consecutive_loss_limit INTEGER NOT NULL DEFAULT 3,
            monitor_interval_sec INTEGER NOT NULL DEFAULT 300,
            screen_top_n INTEGER NOT NULL DEFAULT 3,
            min_price REAL NOT NULL DEFAULT 1.0,
            max_price REAL NOT NULL DEFAULT 8.0,
            peak_total_assets REAL NOT NULL DEFAULT 0.0,
            consecutive_losses INTEGER NOT NULL DEFAULT 0,
            daily_bought_amount REAL NOT NULL DEFAULT 0.0,
            daily_bought_date TEXT NOT NULL DEFAULT '',
            last_opening_bell_run TEXT NOT NULL DEFAULT '',
            last_monitor_run REAL NOT NULL DEFAULT 0.0,
            updated_at REAL NOT NULL DEFAULT 0.0
        );
        INSERT OR IGNORE INTO auto_trading_config (id, enabled, updated_at) VALUES (1, 0, 0);
        CREATE TABLE IF NOT EXISTS auto_trade_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_type TEXT NOT NULL CHECK(run_type IN ('opening_bell','intraday_monitor','manual')),
            action TEXT NOT NULL CHECK(action IN ('screen','buy','sell','skip','error','circuit_break')),
            code TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            quantity INTEGER NOT NULL DEFAULT 0,
            price REAL NOT NULL DEFAULT 0.0,
            amount REAL NOT NULL DEFAULT 0.0,
            reason TEXT NOT NULL DEFAULT '',
            signal_data TEXT NOT NULL DEFAULT '',
            success INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_auto_log_created ON auto_trade_log(created_at);
    """)
    try:
        await db.execute("ALTER TABLE watchlist ADD COLUMN group_id INTEGER DEFAULT 1")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE pending_orders ADD COLUMN avg_cost REAL NOT NULL DEFAULT 0.0")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE transactions ADD COLUMN fee REAL NOT NULL DEFAULT 0")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE positions ADD COLUMN buy_date TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE positions ADD COLUMN high_water_mark REAL NOT NULL DEFAULT 0.0")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE auto_trading_config ADD COLUMN trailing_stop_enabled INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE auto_trading_config ADD COLUMN trailing_stop_pct REAL NOT NULL DEFAULT 5.0")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE auto_trading_config ADD COLUMN last_closing_bell_run TEXT NOT NULL DEFAULT ''")
    except Exception:
        pass
    # auto_trade_log CHECK迁移: 增加 'closing_bell' run_type
    try:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS auto_trade_log_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_type TEXT NOT NULL CHECK(run_type IN ('opening_bell','intraday_monitor','manual','closing_bell')),
                action TEXT NOT NULL CHECK(action IN ('screen','buy','sell','skip','error','circuit_break')),
                code TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 0,
                price REAL NOT NULL DEFAULT 0.0,
                amount REAL NOT NULL DEFAULT 0.0,
                reason TEXT NOT NULL DEFAULT '',
                signal_data TEXT NOT NULL DEFAULT '',
                success INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL
            );
            INSERT OR IGNORE INTO auto_trade_log_new SELECT * FROM auto_trade_log;
            DROP TABLE IF EXISTS auto_trade_log;
            ALTER TABLE auto_trade_log_new RENAME TO auto_trade_log;
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_auto_log_created ON auto_trade_log(created_at)")
    except Exception:
        pass
    await db.executescript("""
        CREATE TABLE IF NOT EXISTS auto_trade_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            total_assets REAL NOT NULL,
            cash REAL NOT NULL,
            positions_value REAL NOT NULL,
            daily_pnl REAL NOT NULL DEFAULT 0,
            daily_pnl_pct REAL NOT NULL DEFAULT 0,
            buys INTEGER NOT NULL DEFAULT 0,
            sells INTEGER NOT NULL DEFAULT 0,
            buy_amount REAL NOT NULL DEFAULT 0,
            sell_amount REAL NOT NULL DEFAULT 0,
            win_count INTEGER NOT NULL DEFAULT 0,
            loss_count INTEGER NOT NULL DEFAULT 0,
            consecutive_losses INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
    """)


async def get_account() -> dict:
    db = await _get_db()
    cur = await db.execute("SELECT cash FROM account WHERE id = 1")
    row = await cur.fetchone()
    return {"cash": row["cash"], "initial_cash": INITIAL_CASH}


async def get_positions() -> list[dict]:
    db = await _get_db()
    cur = await db.execute("SELECT * FROM positions WHERE quantity > 0")
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_transactions(
    limit: int = 50,
    start_date: str | None = None,
    end_date: str | None = None,
    action: str | None = None,
) -> list[dict]:
    db = await _get_db()
    conditions = []
    params: list = []
    if start_date:
        from datetime import datetime
        ts = datetime.strptime(start_date, "%Y-%m-%d").timestamp()
        conditions.append("created_at >= ?")
        params.append(ts)
    if end_date:
        from datetime import datetime
        ts = datetime.strptime(end_date + " 23:59:59", "%Y-%m-%d %H:%M:%S").timestamp()
        conditions.append("created_at <= ?")
        params.append(ts)
    if action in ("buy", "sell"):
        conditions.append("action = ?")
        params.append(action)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    cur = await db.execute(
        f"SELECT * FROM transactions{where} ORDER BY created_at DESC LIMIT ?",
        params,
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def buy_stock(code: str, name: str, quantity: int, price: float) -> dict:
    """买入股票。quantity 为股数（必须为100的整数倍）。"""
    if quantity <= 0 or quantity % 100 != 0:
        return {"error": "买入数量必须为100的整数倍"}
    amount = quantity * price
    _, _, _, total_fee = _calc_fees(amount)
    total_cost = amount + total_fee

    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    async with _trade_lock:
        db = await _get_db()
        cur = await db.execute("SELECT cash FROM account WHERE id = 1")
        row = await cur.fetchone()
        if row["cash"] < total_cost:
            return {"error": f"余额不足，需要 {total_cost:.2f}（含手续费 {total_fee:.2f}），可用 {row['cash']:.2f}"}

        await db.execute("UPDATE account SET cash = cash - ? WHERE id = 1", (total_cost,))

        cur = await db.execute("SELECT quantity, avg_cost, buy_date FROM positions WHERE code = ?", (code,))
        pos = await cur.fetchone()
        if pos:
            total_qty = pos["quantity"] + quantity
            new_avg = (pos["quantity"] * pos["avg_cost"] + quantity * price) / total_qty
            old_hwm = pos["high_water_mark"] or 0
            new_hwm = max(old_hwm, price)
            await db.execute(
                "UPDATE positions SET quantity = ?, avg_cost = ?, name = ?, high_water_mark = ? WHERE code = ?",
                (total_qty, new_avg, name, new_hwm, code),
            )
        else:
            await db.execute(
                "INSERT INTO positions (code, name, quantity, avg_cost, buy_date, high_water_mark) VALUES (?, ?, ?, ?, ?, ?)",
                (code, name, quantity, price, today, price),
            )

        await db.execute(
            "INSERT INTO transactions (code, name, action, quantity, price, amount, fee, created_at) VALUES (?, ?, 'buy', ?, ?, ?, ?, ?)",
            (code, name, quantity, price, amount, total_fee, time.time()),
        )
        await db.commit()

    return {"success": True, "action": "buy", "code": code, "quantity": quantity, "price": price, "amount": amount, "fee": total_fee}


async def sell_stock(code: str, quantity: int, price: float) -> dict:
    """卖出股票。"""
    if quantity <= 0 or quantity % 100 != 0:
        return {"error": "卖出数量必须为100的整数倍"}
    amount = quantity * price
    _, _, _, total_fee = _calc_fees(amount, is_sell=True)
    net_proceeds = amount - total_fee

    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    async with _trade_lock:
        db = await _get_db()
        cur = await db.execute("SELECT quantity, avg_cost, name, buy_date FROM positions WHERE code = ?", (code,))
        pos = await cur.fetchone()
        if not pos or pos["quantity"] < quantity:
            available = pos["quantity"] if pos else 0
            return {"error": f"持仓不足，可用 {available} 股"}

        if pos["buy_date"] == today:
            return {"error": "T+1限制：今日买入的股票不能卖出"}

        await db.execute("UPDATE account SET cash = cash + ? WHERE id = 1", (net_proceeds,))

        new_qty = pos["quantity"] - quantity
        if new_qty == 0:
            await db.execute("DELETE FROM positions WHERE code = ?", (code,))
        else:
            await db.execute("UPDATE positions SET quantity = ? WHERE code = ?", (new_qty, code))

        profit = (price - pos["avg_cost"]) * quantity - total_fee
        await db.execute(
            "INSERT INTO transactions (code, name, action, quantity, price, amount, fee, created_at) VALUES (?, ?, 'sell', ?, ?, ?, ?, ?)",
            (code, pos["name"], quantity, price, amount, total_fee, time.time()),
        )
        await db.commit()

    return {"success": True, "action": "sell", "code": code, "quantity": quantity, "price": price, "amount": amount, "fee": total_fee, "profit": profit}


async def reset_account() -> dict:
    """重置账户到初始状态。"""
    db = await _get_db()
    await db.execute("UPDATE account SET cash = ? WHERE id = 1", (INITIAL_CASH,))
    await db.execute("DELETE FROM positions")
    await db.execute("DELETE FROM transactions")
    await db.commit()
    return {"success": True, "message": "账户已重置"}


async def add_watchlist(code: str, name: str, group_id: int = 1) -> dict:
    db = await _get_db()
    await db.execute(
        "INSERT OR IGNORE INTO watchlist (code, name, added_at, group_id) VALUES (?, ?, ?, ?)",
        (code, name, time.time(), group_id),
    )
    await db.commit()
    return {"success": True}


async def remove_watchlist(code: str) -> dict:
    db = await _get_db()
    await db.execute("DELETE FROM watchlist WHERE code = ?", (code,))
    await db.commit()
    return {"success": True}


async def get_watchlist(group_id: int | None = None) -> list[dict]:
    db = await _get_db()
    if group_id:
        cur = await db.execute("SELECT code, name, group_id FROM watchlist WHERE group_id = ? ORDER BY added_at", (group_id,))
    else:
        cur = await db.execute("SELECT code, name, group_id FROM watchlist ORDER BY added_at")
    return [dict(row) for row in await cur.fetchall()]


# ─── 委托单 ───

async def create_order(code: str, name: str, action: str, quantity: int, target_price: float) -> dict:
    """创建限价委托单。"""
    if quantity <= 0 or quantity % 100 != 0:
        return {"error": "委托数量必须为100的整数倍"}
    if target_price <= 0:
        return {"error": "委托价格必须大于0"}
    if action not in ("buy", "sell"):
        return {"error": "操作类型无效"}

    async with _trade_lock:
        db = await _get_db()
        if action == "buy":
            amount = quantity * target_price
            _, _, _, total_fee = _calc_fees(amount)
            frozen = amount + total_fee
            cur = await db.execute("SELECT cash FROM account WHERE id = 1")
            row = await cur.fetchone()
            if row["cash"] < frozen:
                return {"error": f"余额不足，需要 {frozen:.2f}（含手续费 {total_fee:.2f}），可用 {row['cash']:.2f}"}
            await db.execute("UPDATE account SET cash = cash - ? WHERE id = 1", (frozen,))
        else:
            # 卖出委托：冻结持仓，防止同时直接卖出
            cur = await db.execute("SELECT quantity, avg_cost FROM positions WHERE code = ?", (code,))
            pos = await cur.fetchone()
            available = pos["quantity"] if pos else 0
            if available < quantity:
                return {"error": f"持仓不足，可用 {available} 股"}
            # 记录冻结时的原始成本价
            frozen_avg_cost = pos["avg_cost"] if pos else 0.0
            # 冻结：从持仓中扣除
            new_qty = available - quantity
            if new_qty == 0:
                await db.execute("DELETE FROM positions WHERE code = ?", (code,))
            else:
                await db.execute("UPDATE positions SET quantity = ? WHERE code = ?", (new_qty, code))

        await db.execute(
            "INSERT INTO pending_orders (code, name, action, quantity, target_price, avg_cost, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (code, name, action, quantity, target_price, frozen_avg_cost if action == "sell" else 0.0, time.time()),
        )
        await db.commit()

    return {"success": True, "action": action, "code": code, "quantity": quantity, "target_price": target_price}


async def get_orders(status: str | None = None) -> list[dict]:
    db = await _get_db()
    if status in ("pending", "filled", "cancelled"):
        cur = await db.execute("SELECT * FROM pending_orders WHERE status = ? ORDER BY created_at DESC", (status,))
    else:
        cur = await db.execute("SELECT * FROM pending_orders ORDER BY created_at DESC")
    return [dict(row) for row in await cur.fetchall()]


async def cancel_order(order_id: int) -> dict:
    async with _trade_lock:
        db = await _get_db()
        cur = await db.execute("SELECT * FROM pending_orders WHERE id = ? AND status = 'pending'", (order_id,))
        order = await cur.fetchone()
        if not order:
            return {"error": "委托单不存在或已处理"}

        if order["action"] == "buy":
            amount = order["quantity"] * order["target_price"]
            _, _, _, total_fee = _calc_fees(amount)
            await db.execute("UPDATE account SET cash = cash + ? WHERE id = 1", (amount + total_fee,))
        else:
            # 卖出委托取消：归还冻结的持仓（使用冻结时记录的原始成本价）
            original_avg_cost = order["avg_cost"] or order["target_price"]
            cur2 = await db.execute("SELECT quantity, avg_cost FROM positions WHERE code = ?", (order["code"],))
            pos = await cur2.fetchone()
            if pos:
                total_qty = pos["quantity"] + order["quantity"]
                new_avg = (pos["quantity"] * pos["avg_cost"] + order["quantity"] * original_avg_cost) / total_qty
                await db.execute("UPDATE positions SET quantity = ?, avg_cost = ? WHERE code = ?", (total_qty, new_avg, order["code"]))
            else:
                await db.execute(
                    "INSERT INTO positions (code, name, quantity, avg_cost) VALUES (?, ?, ?, ?)",
                    (order["code"], order["name"], order["quantity"], original_avg_cost),
                )

        await db.execute("UPDATE pending_orders SET status = 'cancelled' WHERE id = ?", (order_id,))
        await db.commit()

    return {"success": True}


async def check_and_fill_orders(price_map: dict[str, float]) -> list[dict]:
    """检查并执行满足条件的委托单。由行情刷新时调用。"""
    async with _trade_lock:
        db = await _get_db()
        cur = await db.execute("SELECT * FROM pending_orders WHERE status = 'pending'")
        orders = await cur.fetchall()

        if not orders:
            return []

        filled = []
        for order in orders:
            code = order["code"]
            current_price = price_map.get(code)
            if current_price is None:
                continue

            should_fill = False
            if order["action"] == "buy" and current_price <= order["target_price"]:
                should_fill = True
            elif order["action"] == "sell" and current_price >= order["target_price"]:
                should_fill = True

            if should_fill:
                result = await _fill_order(db, order, current_price)
                if result.get("success"):
                    filled.append(result)

        return filled


async def _fill_order(db: aiosqlite.Connection, order: aiosqlite.Row, fill_price: float) -> dict:
    """执行委托单成交。调用方已持有 _trade_lock。"""
    # 再次确认状态
    cur = await db.execute("SELECT status FROM pending_orders WHERE id = ?", (order["id"],))
    row = await cur.fetchone()
    if not row or row["status"] != "pending":
        return {"error": "委托单已处理"}

    if order["action"] == "buy":
        frozen = order["quantity"] * order["target_price"]
        _, _, _, frozen_fee = _calc_fees(frozen)
        frozen_total = frozen + frozen_fee

        actual = order["quantity"] * fill_price
        _, _, _, actual_fee = _calc_fees(actual)
        actual_total = actual + actual_fee

        refund = frozen_total - actual_total
        if refund > 0:
            await db.execute("UPDATE account SET cash = cash + ? WHERE id = 1", (refund,))

        cur = await db.execute("SELECT quantity, avg_cost FROM positions WHERE code = ?", (order["code"],))
        pos = await cur.fetchone()
        if pos:
            total_qty = pos["quantity"] + order["quantity"]
            new_avg = (pos["quantity"] * pos["avg_cost"] + order["quantity"] * fill_price) / total_qty
            await db.execute("UPDATE positions SET quantity = ?, avg_cost = ?, name = ? WHERE code = ?",
                             (total_qty, new_avg, order["name"], order["code"]))
        else:
            await db.execute("INSERT INTO positions (code, name, quantity, avg_cost) VALUES (?, ?, ?, ?)",
                             (order["code"], order["name"], order["quantity"], fill_price))

    else:  # sell — 持仓已在 create_order 时冻结，只需加钱
        amount = order["quantity"] * fill_price
        _, _, _, total_fee = _calc_fees(amount, is_sell=True)
        net_proceeds = amount - total_fee
        await db.execute("UPDATE account SET cash = cash + ? WHERE id = 1", (net_proceeds,))

    amount = order["quantity"] * fill_price
    _, _, _, fill_fee = _calc_fees(amount, is_sell=(order["action"] == "sell"))
    await db.execute(
        "INSERT INTO transactions (code, name, action, quantity, price, amount, fee, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (order["code"], order["name"], order["action"], order["quantity"], fill_price, amount, fill_fee, time.time()),
    )

    await db.execute(
        "UPDATE pending_orders SET status = 'filled', filled_at = ?, filled_price = ? WHERE id = ?",
        (time.time(), fill_price, order["id"]),
    )
    await db.commit()
    return {
        "success": True,
        "order_id": order["id"],
        "code": order["code"],
        "action": order["action"],
        "quantity": order["quantity"],
        "target_price": order["target_price"],
        "filled_price": fill_price,
    }


# ─── 每日资产快照 ───

async def record_daily_snapshot(price_map: dict[str, float] | None = None) -> dict:
    """记录当日资产快照。如已有则更新。"""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    db = await _get_db()
    cur = await db.execute("SELECT cash FROM account WHERE id = 1")
    row = await cur.fetchone()
    cash = row["cash"]

    positions_raw = await db.execute("SELECT code, quantity, avg_cost FROM positions WHERE quantity > 0")
    positions = await positions_raw.fetchall()

    positions_value = 0.0
    if price_map:
        for p in positions:
            positions_value += price_map.get(p["code"], p["avg_cost"]) * p["quantity"]
    else:
        for p in positions:
            positions_value += p["avg_cost"] * p["quantity"]

    total = cash + positions_value

    await db.execute(
        "INSERT OR REPLACE INTO daily_snapshots (date, cash, positions_value, total) VALUES (?, ?, ?, ?)",
        (today, round(cash, 2), round(positions_value, 2), round(total, 2)),
    )
    await db.commit()
    return {"success": True, "date": today, "cash": cash, "positions_value": positions_value, "total": total}


async def get_daily_snapshots(days: int = 90) -> list[dict]:
    db = await _get_db()
    cur = await db.execute(
        "SELECT * FROM daily_snapshots ORDER BY date DESC LIMIT ?", (days,)
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_performance_stats() -> dict:
    """计算收益统计指标。"""
    db = await _get_db()
    cur = await db.execute("SELECT * FROM daily_snapshots ORDER BY date ASC")
    snapshots = [dict(r) for r in await cur.fetchall()]

    if not snapshots:
        return {"total_return": 0, "annualized_return": 0, "max_drawdown": 0, "win_rate": 0, "profit_loss_ratio": 0, "avg_holding_days": 0}

    first = snapshots[0]["total"]
    last = snapshots[-1]["total"]
    total_return = (last - first) / first * 100 if first else 0

    from datetime import datetime
    d1 = datetime.strptime(snapshots[0]["date"], "%Y-%m-%d")
    d2 = datetime.strptime(snapshots[-1]["date"], "%Y-%m-%d")
    days_held = (d2 - d1).days
    annualized_return = ((last / first) ** (365 / max(days_held, 1)) - 1) * 100 if first and days_held > 0 else 0

    max_drawdown = 0
    peak = snapshots[0]["total"]
    for s in snapshots:
        if s["total"] > peak:
            peak = s["total"]
        dd = (peak - s["total"]) / peak * 100 if peak else 0
        if dd > max_drawdown:
            max_drawdown = dd

    # 修复：正确计算胜率/盈亏比/持仓天数
    # 查询所有卖出交易（含 code 和 created_at）
    cur2 = await db.execute("SELECT code, quantity, price, created_at FROM transactions WHERE action = 'sell' ORDER BY created_at")
    sells = [dict(r) for r in await cur2.fetchall()]

    if sells:
        # 查询每只股票的买入均价和首次买入时间
        cur3 = await db.execute(
            "SELECT code, AVG(price) as avg_price, MIN(created_at) as first_buy FROM transactions WHERE action = 'buy' GROUP BY code"
        )
        buy_stats = {r["code"]: {"avg_price": r["avg_price"], "first_buy": r["first_buy"]} for r in await cur3.fetchall()}

        wins = 0
        total_wins = 0.0
        total_losses = 0.0
        total_holding_days = 0.0

        for s in sells:
            stats = buy_stats.get(s["code"])
            avg_buy_price = stats["avg_price"] if stats else s["price"]
            buy_amount = avg_buy_price * s["quantity"]
            sell_amount = s["price"] * s["quantity"]
            _, _, _, buy_fee = _calc_fees(buy_amount)
            _, _, _, sell_fee = _calc_fees(sell_amount, is_sell=True)
            profit = (s["price"] - avg_buy_price) * s["quantity"] - buy_fee - sell_fee
            if profit > 0:
                wins += 1
                total_wins += profit
            else:
                total_losses += abs(profit)

            if stats and stats["first_buy"]:
                days = (s["created_at"] - stats["first_buy"]) / 86400
                total_holding_days += max(days, 0)

        win_rate = wins / len(sells) * 100
        profit_loss_ratio = total_wins / total_losses if total_losses > 0 else 0
        avg_holding_days = total_holding_days / len(sells)
    else:
        win_rate = 0
        profit_loss_ratio = 0
        avg_holding_days = 0

    return {
        "total_return": round(total_return, 2),
        "annualized_return": round(annualized_return, 2),
        "max_drawdown": round(max_drawdown, 2),
        "win_rate": round(win_rate, 2),
        "profit_loss_ratio": round(profit_loss_ratio, 2),
        "avg_holding_days": round(avg_holding_days, 1),
        "snapshot_count": len(snapshots),
    }


# ─── 自选股分组 ───

async def get_groups() -> list[dict]:
    db = await _get_db()
    cur = await db.execute("SELECT * FROM watchlist_groups ORDER BY sort_order, id")
    return [dict(r) for r in await cur.fetchall()]


async def create_group(name: str) -> dict:
    db = await _get_db()
    cur = await db.execute("SELECT MAX(sort_order) as m FROM watchlist_groups")
    row = await cur.fetchone()
    sort_order = (row["m"] or 0) + 1
    await db.execute("INSERT INTO watchlist_groups (name, sort_order) VALUES (?, ?)", (name, sort_order))
    await db.commit()
    return {"success": True}


async def rename_group(group_id: int, name: str) -> dict:
    db = await _get_db()
    await db.execute("UPDATE watchlist_groups SET name = ? WHERE id = ?", (name, group_id))
    await db.commit()
    return {"success": True}


async def delete_group(group_id: int) -> dict:
    if group_id == 1:
        return {"error": "不能删除默认分组"}
    db = await _get_db()
    await db.execute("UPDATE watchlist SET group_id = 1 WHERE group_id = ?", (group_id,))
    await db.execute("DELETE FROM watchlist_groups WHERE id = ?", (group_id,))
    await db.commit()
    return {"success": True}


async def move_to_group(code: str, group_id: int) -> dict:
    db = await _get_db()
    await db.execute("UPDATE watchlist SET group_id = ? WHERE code = ?", (group_id, code))
    await db.commit()
    return {"success": True}


# ─── 涨跌提醒 ───

async def create_alert(code: str, name: str, condition: str, value: float) -> dict:
    if condition not in ("above", "below", "change_up", "change_down"):
        return {"error": "条件类型无效"}
    db = await _get_db()
    await db.execute(
        "INSERT INTO price_alerts (code, name, condition, value, status, created_at) VALUES (?, ?, ?, ?, 'active', ?)",
        (code, name, condition, value, time.time()),
    )
    await db.commit()
    return {"success": True}


async def get_alerts(status: str | None = None) -> list[dict]:
    db = await _get_db()
    if status in ("active", "triggered", "cancelled"):
        cur = await db.execute("SELECT * FROM price_alerts WHERE status = ? ORDER BY created_at DESC", (status,))
    else:
        cur = await db.execute("SELECT * FROM price_alerts ORDER BY created_at DESC")
    return [dict(r) for r in await cur.fetchall()]


async def cancel_alert(alert_id: int) -> dict:
    db = await _get_db()
    await db.execute("UPDATE price_alerts SET status = 'cancelled' WHERE id = ? AND status = 'active'", (alert_id,))
    await db.commit()
    return {"success": True}


async def check_alerts(price_map: dict[str, float]) -> list[dict]:
    """检查提醒规则，返回触发的提醒列表。"""
    db = await _get_db()
    cur = await db.execute("SELECT * FROM price_alerts WHERE status = 'active'")
    alerts = await cur.fetchall()

    triggered = []
    for alert in alerts:
        current_price = price_map.get(alert["code"])
        if current_price is None:
            continue

        hit = False
        message = ""
        if alert["condition"] == "above" and current_price >= alert["value"]:
            hit = True
            message = f"{alert['name']} 当前价 {current_price:.2f} 已达到目标价 {alert['value']:.2f}"
        elif alert["condition"] == "below" and current_price <= alert["value"]:
            hit = True
            message = f"{alert['name']} 当前价 {current_price:.2f} 已跌破目标价 {alert['value']:.2f}"

        if hit:
            await db.execute(
                "UPDATE price_alerts SET status = 'triggered', triggered_at = ?, message = ? WHERE id = ?",
                (time.time(), message, alert["id"]),
            )
            await db.commit()
            triggered.append({"id": alert["id"], "code": alert["code"], "name": alert["name"], "message": message})

    return triggered
