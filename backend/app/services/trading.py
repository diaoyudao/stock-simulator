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
    """)


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
