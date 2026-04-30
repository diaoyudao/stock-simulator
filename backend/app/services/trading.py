import aiosqlite
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "stock_sim.db"
INITIAL_CASH = 100000.0


async def _get_db() -> aiosqlite.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(str(DB_PATH))
    db.row_factory = aiosqlite.Row
    await _ensure_tables(db)
    return db


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
    """)
    # 为 watchlist 表添加 group_id 列（兼容已有数据库）
    try:
        await db.execute("ALTER TABLE watchlist ADD COLUMN group_id INTEGER DEFAULT 1")
    except Exception:
        pass  # 列已存在


async def get_account() -> dict:
    db = await _get_db()
    try:
        cur = await db.execute("SELECT cash FROM account WHERE id = 1")
        row = await cur.fetchone()
        return {"cash": row["cash"], "initial_cash": INITIAL_CASH}
    finally:
        await db.close()


async def get_positions() -> list[dict]:
    db = await _get_db()
    try:
        cur = await db.execute("SELECT * FROM positions WHERE quantity > 0")
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_transactions(
    limit: int = 50,
    start_date: str | None = None,
    end_date: str | None = None,
    action: str | None = None,
) -> list[dict]:
    db = await _get_db()
    try:
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
    finally:
        await db.close()


async def buy_stock(code: str, name: str, quantity: int, price: float) -> dict:
    """买入股票。quantity 为股数（必须为100的整数倍）。"""
    if quantity <= 0 or quantity % 100 != 0:
        return {"error": "买入数量必须为100的整数倍"}
    amount = quantity * price

    db = await _get_db()
    try:
        cur = await db.execute("SELECT cash FROM account WHERE id = 1")
        row = await cur.fetchone()
        if row["cash"] < amount:
            return {"error": f"余额不足，需要 {amount:.2f}，可用 {row['cash']:.2f}"}

        await db.execute("UPDATE account SET cash = cash - ? WHERE id = 1", (amount,))

        cur = await db.execute("SELECT quantity, avg_cost FROM positions WHERE code = ?", (code,))
        pos = await cur.fetchone()
        if pos:
            total_qty = pos["quantity"] + quantity
            new_avg = (pos["quantity"] * pos["avg_cost"] + quantity * price) / total_qty
            await db.execute(
                "UPDATE positions SET quantity = ?, avg_cost = ?, name = ? WHERE code = ?",
                (total_qty, new_avg, name, code),
            )
        else:
            await db.execute(
                "INSERT INTO positions (code, name, quantity, avg_cost) VALUES (?, ?, ?, ?)",
                (code, name, quantity, price),
            )

        await db.execute(
            "INSERT INTO transactions (code, name, action, quantity, price, amount, created_at) VALUES (?, ?, 'buy', ?, ?, ?, ?)",
            (code, name, quantity, price, amount, time.time()),
        )
        await db.commit()
        return {"success": True, "action": "buy", "code": code, "quantity": quantity, "price": price, "amount": amount}
    finally:
        await db.close()


async def sell_stock(code: str, quantity: int, price: float) -> dict:
    """卖出股票。"""
    if quantity <= 0 or quantity % 100 != 0:
        return {"error": "卖出数量必须为100的整数倍"}
    amount = quantity * price

    db = await _get_db()
    try:
        cur = await db.execute("SELECT quantity, avg_cost, name FROM positions WHERE code = ?", (code,))
        pos = await cur.fetchone()
        if not pos or pos["quantity"] < quantity:
            available = pos["quantity"] if pos else 0
            return {"error": f"持仓不足，可用 {available} 股"}

        await db.execute("UPDATE account SET cash = cash + ? WHERE id = 1", (amount,))

        new_qty = pos["quantity"] - quantity
        if new_qty == 0:
            await db.execute("DELETE FROM positions WHERE code = ?", (code,))
        else:
            await db.execute("UPDATE positions SET quantity = ? WHERE code = ?", (new_qty, code))

        profit = (price - pos["avg_cost"]) * quantity
        await db.execute(
            "INSERT INTO transactions (code, name, action, quantity, price, amount, created_at) VALUES (?, ?, 'sell', ?, ?, ?, ?)",
            (code, pos["name"], quantity, price, amount, time.time()),
        )
        await db.commit()
        return {"success": True, "action": "sell", "code": code, "quantity": quantity, "price": price, "amount": amount, "profit": profit}
    finally:
        await db.close()


async def reset_account() -> dict:
    """重置账户到初始状态。"""
    db = await _get_db()
    try:
        await db.execute("UPDATE account SET cash = ? WHERE id = 1", (INITIAL_CASH,))
        await db.execute("DELETE FROM positions")
        await db.execute("DELETE FROM transactions")
        await db.commit()
        return {"success": True, "message": "账户已重置"}
    finally:
        await db.close()


async def add_watchlist(code: str, name: str, group_id: int = 1) -> dict:
    db = await _get_db()
    try:
        await db.execute(
            "INSERT OR IGNORE INTO watchlist (code, name, added_at, group_id) VALUES (?, ?, ?, ?)",
            (code, name, time.time(), group_id),
        )
        await db.commit()
        return {"success": True}
    finally:
        await db.close()


async def remove_watchlist(code: str) -> dict:
    db = await _get_db()
    try:
        await db.execute("DELETE FROM watchlist WHERE code = ?", (code,))
        await db.commit()
        return {"success": True}
    finally:
        await db.close()


async def get_watchlist(group_id: int | None = None) -> list[dict]:
    db = await _get_db()
    try:
        if group_id:
            cur = await db.execute("SELECT code, name, group_id FROM watchlist WHERE group_id = ? ORDER BY added_at", (group_id,))
        else:
            cur = await db.execute("SELECT code, name, group_id FROM watchlist ORDER BY added_at")
        return [dict(row) for row in await cur.fetchall()]
    finally:
        await db.close()


# ─── 委托单 ───

async def create_order(code: str, name: str, action: str, quantity: int, target_price: float) -> dict:
    """创建限价委托单。"""
    if quantity <= 0 or quantity % 100 != 0:
        return {"error": "委托数量必须为100的整数倍"}
    if target_price <= 0:
        return {"error": "委托价格必须大于0"}
    if action not in ("buy", "sell"):
        return {"error": "操作类型无效"}

    db = await _get_db()
    try:
        if action == "buy":
            amount = quantity * target_price
            cur = await db.execute("SELECT cash FROM account WHERE id = 1")
            row = await cur.fetchone()
            if row["cash"] < amount:
                return {"error": f"余额不足，需要 {amount:.2f}，可用 {row['cash']:.2f}"}
            # 冻结资金
            await db.execute("UPDATE account SET cash = cash - ? WHERE id = 1", (amount,))
        else:
            cur = await db.execute("SELECT quantity FROM positions WHERE code = ?", (code,))
            pos = await cur.fetchone()
            available = pos["quantity"] if pos else 0
            if available < quantity:
                return {"error": f"持仓不足，可用 {available} 股"}

        await db.execute(
            "INSERT INTO pending_orders (code, name, action, quantity, target_price, status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (code, name, action, quantity, target_price, time.time()),
        )
        await db.commit()
        return {"success": True, "action": action, "code": code, "quantity": quantity, "target_price": target_price}
    finally:
        await db.close()


async def get_orders(status: str | None = None) -> list[dict]:
    db = await _get_db()
    try:
        if status in ("pending", "filled", "cancelled"):
            cur = await db.execute("SELECT * FROM pending_orders WHERE status = ? ORDER BY created_at DESC", (status,))
        else:
            cur = await db.execute("SELECT * FROM pending_orders ORDER BY created_at DESC")
        return [dict(row) for row in await cur.fetchall()]
    finally:
        await db.close()


async def cancel_order(order_id: int) -> dict:
    db = await _get_db()
    try:
        cur = await db.execute("SELECT * FROM pending_orders WHERE id = ? AND status = 'pending'", (order_id,))
        order = await cur.fetchone()
        if not order:
            return {"error": "委托单不存在或已处理"}

        # 释放冻结资金（买入委托）
        if order["action"] == "buy":
            amount = order["quantity"] * order["target_price"]
            await db.execute("UPDATE account SET cash = cash + ? WHERE id = 1", (amount,))

        await db.execute("UPDATE pending_orders SET status = 'cancelled' WHERE id = ?", (order_id,))
        await db.commit()
        return {"success": True}
    finally:
        await db.close()


async def check_and_fill_orders(price_map: dict[str, float]) -> list[dict]:
    """检查并执行满足条件的委托单。由行情刷新时调用。"""
    db = await _get_db()
    try:
        cur = await db.execute("SELECT * FROM pending_orders WHERE status = 'pending'")
        orders = await cur.fetchall()
    finally:
        await db.close()

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
            result = await _fill_order(order, current_price)
            if result.get("success"):
                filled.append(result)

    return filled


async def _fill_order(order: aiosqlite.Row, fill_price: float) -> dict:
    """执行委托单成交。"""
    db = await _get_db()
    try:
        # 再次确认状态
        cur = await db.execute("SELECT status FROM pending_orders WHERE id = ?", (order["id"],))
        row = await cur.fetchone()
        if not row or row["status"] != "pending":
            return {"error": "委托单已处理"}

        if order["action"] == "buy":
            # 资金已在创建时冻结，按成交价结算差额
            frozen = order["quantity"] * order["target_price"]
            actual = order["quantity"] * fill_price
            refund = frozen - actual
            if refund > 0:
                await db.execute("UPDATE account SET cash = cash + ? WHERE id = 1", (refund,))

            # 更新持仓
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

        else:  # sell
            amount = order["quantity"] * fill_price
            await db.execute("UPDATE account SET cash = cash + ? WHERE id = 1", (amount,))

            cur = await db.execute("SELECT quantity, avg_cost FROM positions WHERE code = ?", (order["code"],))
            pos = await cur.fetchone()
            if pos:
                new_qty = pos["quantity"] - order["quantity"]
                if new_qty == 0:
                    await db.execute("DELETE FROM positions WHERE code = ?", (order["code"],))
                else:
                    await db.execute("UPDATE positions SET quantity = ? WHERE code = ?", (new_qty, order["code"]))

        # 记录交易
        amount = order["quantity"] * fill_price
        await db.execute(
            "INSERT INTO transactions (code, name, action, quantity, price, amount, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (order["code"], order["name"], order["action"], order["quantity"], fill_price, amount, time.time()),
        )

        # 更新委托单状态
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
    except Exception as e:
        return {"error": str(e)}
    finally:
        await db.close()


# ─── 每日资产快照 ───

async def record_daily_snapshot(price_map: dict[str, float] | None = None) -> dict:
    """记录当日资产快照。如已有则更新。"""
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    db = await _get_db()
    try:
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
    finally:
        await db.close()


async def get_daily_snapshots(days: int = 90) -> list[dict]:
    db = await _get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM daily_snapshots ORDER BY date DESC LIMIT ?", (days,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def get_performance_stats() -> dict:
    """计算收益统计指标。"""
    db = await _get_db()
    try:
        cur = await db.execute("SELECT * FROM daily_snapshots ORDER BY date ASC")
        snapshots = [dict(r) for r in await cur.fetchall()]

        if not snapshots:
            return {"total_return": 0, "annualized_return": 0, "max_drawdown": 0, "win_rate": 0, "profit_loss_ratio": 0, "avg_holding_days": 0}

        # 总收益率
        first = snapshots[0]["total"]
        last = snapshots[-1]["total"]
        total_return = (last - first) / first * 100 if first else 0

        # 年化收益率
        from datetime import datetime
        d1 = datetime.strptime(snapshots[0]["date"], "%Y-%m-%d")
        d2 = datetime.strptime(snapshots[-1]["date"], "%Y-%m-%d")
        days_held = (d2 - d1).days
        annualized_return = ((last / first) ** (365 / max(days_held, 1)) - 1) * 100 if first and days_held > 0 else 0

        # 最大回撤
        max_drawdown = 0
        peak = snapshots[0]["total"]
        for s in snapshots:
            if s["total"] > peak:
                peak = s["total"]
            dd = (peak - s["total"]) / peak * 100 if peak else 0
            if dd > max_drawdown:
                max_drawdown = dd

        # 交易胜率/盈亏比
        cur2 = await db.execute("SELECT action, quantity, price, amount FROM transactions WHERE action = 'sell' ORDER BY created_at")
        sells = await cur2.fetchall()

        wins = 0
        total_wins = 0
        total_losses = 0
        for s in sells:
            # 简化：用金额正负判断
            cur3 = await db.execute(
                "SELECT avg_cost FROM positions WHERE code = (SELECT code FROM transactions WHERE id = ?)",
                (s[0] if False else 0,),
            )
            profit = s["price"] - s.get("avg_cost", s["price"])
            if profit > 0:
                wins += 1
                total_wins += profit * s["quantity"]
            else:
                total_losses += abs(profit * s["quantity"])

        win_rate = wins / len(sells) * 100 if sells else 0
        profit_loss_ratio = total_wins / total_losses if total_losses > 0 else 0

        # 平均持仓周期（简化计算）
        avg_holding_days = days_held / len(sells) if sells else 0

        return {
            "total_return": round(total_return, 2),
            "annualized_return": round(annualized_return, 2),
            "max_drawdown": round(max_drawdown, 2),
            "win_rate": round(win_rate, 2),
            "profit_loss_ratio": round(profit_loss_ratio, 2),
            "avg_holding_days": round(avg_holding_days, 1),
            "snapshot_count": len(snapshots),
        }
    finally:
        await db.close()


# ─── 自选股分组 ───

async def get_groups() -> list[dict]:
    db = await _get_db()
    try:
        cur = await db.execute("SELECT * FROM watchlist_groups ORDER BY sort_order, id")
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def create_group(name: str) -> dict:
    db = await _get_db()
    try:
        cur = await db.execute("SELECT MAX(sort_order) as m FROM watchlist_groups")
        row = await cur.fetchone()
        sort_order = (row["m"] or 0) + 1
        await db.execute("INSERT INTO watchlist_groups (name, sort_order) VALUES (?, ?)", (name, sort_order))
        await db.commit()
        return {"success": True}
    finally:
        await db.close()


async def rename_group(group_id: int, name: str) -> dict:
    db = await _get_db()
    try:
        await db.execute("UPDATE watchlist_groups SET name = ? WHERE id = ?", (name, group_id))
        await db.commit()
        return {"success": True}
    finally:
        await db.close()


async def delete_group(group_id: int) -> dict:
    if group_id == 1:
        return {"error": "不能删除默认分组"}
    db = await _get_db()
    try:
        await db.execute("UPDATE watchlist SET group_id = 1 WHERE group_id = ?", (group_id,))
        await db.execute("DELETE FROM watchlist_groups WHERE id = ?", (group_id,))
        await db.commit()
        return {"success": True}
    finally:
        await db.close()


async def move_to_group(code: str, group_id: int) -> dict:
    db = await _get_db()
    try:
        await db.execute("UPDATE watchlist SET group_id = ? WHERE code = ?", (group_id, code))
        await db.commit()
        return {"success": True}
    finally:
        await db.close()
