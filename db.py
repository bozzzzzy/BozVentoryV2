import sqlite3
from datetime import date, datetime
from pathlib import Path
from config import DATE_FORMAT_DISPLAY, DATE_FORMAT_STORAGE

DB_PATH = Path(__file__).parent / "data" / "inventory.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    category        TEXT NOT NULL CHECK (category IN ('cards', 'clothing', 'sneakers', 'electronics', 'other')),
    item_name       TEXT NOT NULL,
    size            TEXT,
    purchase_date   TEXT NOT NULL,
    purchase_price  REAL NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'sold', 'ripped')),
    sale_date       TEXT,
    sale_price      REAL,
    sale_group_id   INTEGER,
    shipping_cost   REAL NOT NULL DEFAULT 0,
    notes           TEXT,
    deleted_at      TEXT
);

CREATE TABLE IF NOT EXISTS expenses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    category        TEXT,
    amount          REAL NOT NULL,
    description     TEXT,
    deleted_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_inventory_active ON inventory (item_name, status, purchase_date)
    WHERE deleted_at IS NULL AND status = 'active';

CREATE INDEX IF NOT EXISTS idx_inventory_stale ON inventory (purchase_date, status)
    WHERE deleted_at IS NULL AND status = 'active';
"""

# Whitelists for dynamic SQL identifiers — SQLite cannot parameterize table/column names.
ALLOWED_TABLES = frozenset({"inventory", "expenses"})
ALLOWED_FIELDS = {
    "inventory": frozenset({
        "item_name", "category", "size", "purchase_date", "purchase_price",
        "sale_date", "sale_price", "notes", "status",
    }),
    "expenses": frozenset({"date", "category", "amount", "description"}),
}


def _validate_table(table: str):
    if table not in ALLOWED_TABLES:
        raise ValueError(f"Invalid table: {table!r}")


def _validate_field(table: str, field: str):
    _validate_table(table)
    if field not in ALLOWED_FIELDS.get(table, frozenset()):
        raise ValueError(f"Invalid field {field!r} for table {table!r}")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn):
    """Apply lightweight additive migrations to existing databases."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(inventory)").fetchall()}
    if "shipping_cost" not in cols:
        conn.execute("ALTER TABLE inventory ADD COLUMN shipping_cost REAL NOT NULL DEFAULT 0")


def today_iso() -> str:
    return date.today().strftime(DATE_FORMAT_STORAGE)


def iso_to_display(iso: str) -> str:
    return datetime.strptime(iso, DATE_FORMAT_STORAGE).strftime(DATE_FORMAT_DISPLAY)


def display_to_iso(display: str) -> str:
    return datetime.strptime(display, DATE_FORMAT_DISPLAY).strftime(DATE_FORMAT_STORAGE)


def get_active_inventory_summary(category: str | None = None) -> list[dict]:
    sql = """
        SELECT item_name, category, size, COUNT(*) as count
          FROM inventory
         WHERE status = 'active' AND deleted_at IS NULL
    """
    params: list = []
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " GROUP BY item_name, category, size ORDER BY category, item_name"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def commit_action(action, prior_state_stash: dict | None = None) -> tuple[list[int], int | None]:
    """Commit a parsed action to the DB. Returns (affected_ids, sale_group_id)."""
    a = action.action

    if a == "add":
        return _commit_add(action), None
    elif a == "sell":
        return _commit_sell(action)
    elif a == "rip":
        return _commit_rip(action), None
    elif a == "expense":
        return _commit_expense(action), None
    elif a == "edit":
        return _commit_edit(action, prior_state_stash), None
    elif a == "delete":
        return _commit_delete(action), None
    elif a == "clear":
        return _commit_clear(action), None
    elif a == "undo":
        raise ValueError("Undo is handled in main.py, not commit_action")
    else:
        raise ValueError(f"Unknown action: {a}")


def _commit_add(action) -> list[int]:
    ids = []
    with get_conn() as conn:
        for _ in range(action.quantity):
            cur = conn.execute(
                "INSERT INTO inventory (category, item_name, size, purchase_date, purchase_price) VALUES (?,?,?,?,?)",
                (action.category, action.item_name, action.size, action.purchase_date, action.unit_price),
            )
            ids.append(cur.lastrowid)
    return ids


def _commit_sell(action) -> tuple[list[int], int]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id FROM inventory
             WHERE item_name = ? AND status = 'active' AND deleted_at IS NULL
             ORDER BY purchase_date ASC, id ASC
             LIMIT ?
        """, (action.item_name, action.quantity)).fetchall()

        if len(rows) < action.quantity:
            raise ValueError(f"Only {len(rows)} unsold '{action.item_name}' found, you asked for {action.quantity}.")

        ids = [r["id"] for r in rows]
        next_group = _next_sale_group_id(conn)
        per_unit = action.total_price / action.quantity
        ship_per_unit = getattr(action, "shipping_cost", 0.0) / action.quantity

        for row_id in ids:
            conn.execute("""
                UPDATE inventory
                   SET status='sold', sale_date=?, sale_price=?, sale_group_id=?, shipping_cost=?
                 WHERE id=?
            """, (action.sale_date, per_unit, next_group, ship_per_unit, row_id))

    return ids, next_group


def _commit_rip(action) -> list[int]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id FROM inventory
             WHERE item_name = ? AND status = 'active' AND deleted_at IS NULL
             ORDER BY purchase_date ASC, id ASC
             LIMIT ?
        """, (action.item_name, action.quantity)).fetchall()

        if len(rows) < action.quantity:
            raise ValueError(f"Only {len(rows)} active '{action.item_name}' found, you asked for {action.quantity}.")

        ids = [r["id"] for r in rows]
        for row_id in ids:
            conn.execute("UPDATE inventory SET status='ripped' WHERE id=?", (row_id,))

    return ids


def _commit_expense(action) -> list[int]:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO expenses (date, category, amount, description) VALUES (?,?,?,?)",
            (action.date, action.category, action.amount, action.description),
        )
        return [cur.lastrowid]


def _commit_edit(action, prior_state_stash: dict | None) -> list[int]:
    table = action.table
    _validate_table(table)
    _validate_field(table, action.field)
    target = action.target

    with get_conn() as conn:
        if target == "last":
            row = conn.execute(
                "SELECT id FROM inventory WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 1"
                if table == "inventory" else
                "SELECT id FROM expenses WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                raise ValueError(f"No entries found in {table}.")
            target_id = row["id"]
        else:
            target_id = int(target)

        row = conn.execute(
            "SELECT * FROM inventory WHERE id=? AND deleted_at IS NULL"
            if table == "inventory" else
            "SELECT * FROM expenses WHERE id=? AND deleted_at IS NULL",
            (target_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"No entry #{target_id} found.")

        if prior_state_stash is not None:
            prior_state_stash["id"] = target_id
            prior_state_stash["table"] = table
            prior_state_stash["field"] = action.field
            prior_state_stash["old_value"] = row[action.field]

        new_value = action.new_value
        col_type = _infer_column_type(table, action.field)
        if col_type == "real":
            new_value = float(new_value.replace("$", "").replace(",", ""))
        elif col_type == "int":
            new_value = int(new_value)

        # field is validated against ALLOWED_FIELDS above — safe to interpolate
        if table == "inventory":
            conn.execute(f"UPDATE inventory SET {action.field}=? WHERE id=?", (new_value, target_id))
        else:
            conn.execute(f"UPDATE expenses SET {action.field}=? WHERE id=?", (new_value, target_id))

    return [target_id]


def _commit_delete(action) -> list[int]:
    table = action.table
    _validate_table(table)
    target = action.target

    with get_conn() as conn:
        if target == "last":
            row = conn.execute(
                "SELECT id FROM inventory WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 1"
                if table == "inventory" else
                "SELECT id FROM expenses WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if not row:
                raise ValueError(f"No entries found in {table}.")
            target_id = row["id"]
        else:
            target_id = int(target)

        row = conn.execute(
            "SELECT id FROM inventory WHERE id=? AND deleted_at IS NULL"
            if table == "inventory" else
            "SELECT id FROM expenses WHERE id=? AND deleted_at IS NULL",
            (target_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"No entry #{target_id} found.")

        if table == "inventory":
            conn.execute("UPDATE inventory SET deleted_at=? WHERE id=?", (today_iso(), target_id))
        else:
            conn.execute("UPDATE expenses SET deleted_at=? WHERE id=?", (today_iso(), target_id))

    return [target_id]


def _commit_clear(action) -> list[int]:
    """Soft-delete every active row in the table (optionally filtered by category)."""
    table = action.table
    _validate_table(table)
    category = getattr(action, "category", None)

    with get_conn() as conn:
        base = (
            "SELECT id FROM inventory WHERE deleted_at IS NULL"
            if table == "inventory" else
            "SELECT id FROM expenses WHERE deleted_at IS NULL"
        )
        params: list = []
        if category:
            base += " AND category = ?"
            params.append(category)
        ids = [r["id"] for r in conn.execute(base, params).fetchall()]

        if not ids:
            where = f" in category '{category}'" if category else ""
            raise ValueError(f"Nothing to clear — no active {table}{where}.")

        stamp = today_iso()
        if table == "inventory":
            conn.executemany("UPDATE inventory SET deleted_at=? WHERE id=?", [(stamp, i) for i in ids])
        else:
            conn.executemany("UPDATE expenses SET deleted_at=? WHERE id=?", [(stamp, i) for i in ids])

    return ids


def get_matching_items(
    text: str | None = None,
    category: str | None = None,
    size: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """Search ACTIVE inventory by name substring and/or attributes. Returns rows with IDs."""
    sql = """
        SELECT id, category, item_name, size, purchase_price, purchase_date,
               CAST(julianday('now') - julianday(purchase_date) AS INTEGER) AS days_held
          FROM inventory
         WHERE status = 'active' AND deleted_at IS NULL
    """
    params: list = []
    if text:
        sql += " AND LOWER(item_name) LIKE ?"
        params.append(f"%{text.lower()}%")
    if category:
        sql += " AND category = ?"
        params.append(category)
    if size:
        sql += " AND size = ?"
        params.append(str(size))
    if min_price is not None:
        sql += " AND purchase_price >= ?"
        params.append(float(min_price))
    if max_price is not None:
        sql += " AND purchase_price <= ?"
        params.append(float(max_price))
    sql += " ORDER BY item_name, id"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_clear_preview(table: str, category: str | None = None) -> list[dict]:
    """Return the rows a clear action would remove, for the confirmation message."""
    _validate_table(table)
    base = (
        "SELECT id, item_name, category FROM inventory WHERE deleted_at IS NULL"
        if table == "inventory" else
        "SELECT id, description AS item_name, category FROM expenses WHERE deleted_at IS NULL"
    )
    params: list = []
    if category:
        base += " AND category = ?"
        params.append(category)
    base += " ORDER BY id"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(base, params).fetchall()]


def undo_action(last_action_type: str, affected_ids: list[int], prior_state: dict | None = None):
    """Reverse the last committed action."""
    with get_conn() as conn:
        if last_action_type == "add":
            for row_id in affected_ids:
                conn.execute("UPDATE inventory SET deleted_at=? WHERE id=?", (today_iso(), row_id))

        elif last_action_type in ("sell", "rip"):
            for row_id in affected_ids:
                conn.execute("""
                    UPDATE inventory
                       SET status='active', sale_date=NULL, sale_price=NULL,
                           sale_group_id=NULL, shipping_cost=0
                     WHERE id=?
                """, (row_id,))

        elif last_action_type == "expense":
            for row_id in affected_ids:
                conn.execute("UPDATE expenses SET deleted_at=? WHERE id=?", (today_iso(), row_id))

        elif last_action_type == "edit" and prior_state:
            table = prior_state["table"]
            field = prior_state["field"]
            _validate_table(table)
            _validate_field(table, field)
            old_value = prior_state["old_value"]
            row_id = prior_state["id"]
            # table and field validated above — safe to interpolate
            if table == "inventory":
                conn.execute(f"UPDATE inventory SET {field}=? WHERE id=?", (old_value, row_id))
            else:
                conn.execute(f"UPDATE expenses SET {field}=? WHERE id=?", (old_value, row_id))

        elif last_action_type in ("delete", "clear") and prior_state:
            # Use the table recorded at delete time to restore only the correct rows.
            table = prior_state.get("table", "inventory")
            _validate_table(table)
            for row_id in affected_ids:
                if table == "inventory":
                    conn.execute("UPDATE inventory SET deleted_at=NULL WHERE id=?", (row_id,))
                else:
                    conn.execute("UPDATE expenses SET deleted_at=NULL WHERE id=?", (row_id,))


def get_stale_items(stale_days: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, category, item_name, size, purchase_date, purchase_price,
                   CAST(julianday('now') - julianday(purchase_date) AS INTEGER) AS days_held
              FROM inventory
             WHERE status = 'active' AND deleted_at IS NULL
               AND julianday('now') - julianday(purchase_date) > ?
             ORDER BY purchase_date ASC
        """, (stale_days,)).fetchall()
    return [dict(r) for r in rows]


def get_unsold_items(category: str | None = None, min_days_held: int | None = None) -> list[dict]:
    query = """
        SELECT id, category, item_name, size, purchase_date, purchase_price,
               CAST(julianday('now') - julianday(purchase_date) AS INTEGER) AS days_held
          FROM inventory
         WHERE status = 'active' AND deleted_at IS NULL
    """
    params = []
    if category:
        query += " AND category = ?"
        params.append(category)
    if min_days_held is not None:
        query += " AND julianday('now') - julianday(purchase_date) >= ?"
        params.append(min_days_held)
    query += " ORDER BY purchase_date DESC"

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_recent_items(limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, category, item_name, status, purchase_date, purchase_price, sale_price
              FROM inventory
             WHERE deleted_at IS NULL
             ORDER BY id DESC
             LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def _year_clause(year, date_col: str) -> tuple[str, list]:
    """Return (SQL fragment, params) restricting date_col to a calendar year."""
    if not year:
        return "", []
    return f" AND {date_col} >= ? AND {date_col} <= ?", [f"{int(year)}-01-01", f"{int(year)}-12-31"]


def get_sales_for_export(year=None) -> list[dict]:
    """Sold units (taxable events) for the given calendar year, or all if year is None."""
    year_sql, params = _year_clause(year, "sale_date")
    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT id, category, item_name, size, purchase_date, purchase_price,
                   sale_date, sale_price, shipping_cost
              FROM inventory
             WHERE status = 'sold' AND deleted_at IS NULL {year_sql}
             ORDER BY sale_date, id
        """, params).fetchall()
    return [dict(r) for r in rows]


def get_expenses_for_export(year=None) -> list[dict]:
    """Expenses for the given calendar year, or all if year is None."""
    year_sql, params = _year_clause(year, "date")
    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT id, date, category, amount, description
              FROM expenses
             WHERE deleted_at IS NULL {year_sql}
             ORDER BY date, id
        """, params).fetchall()
    return [dict(r) for r in rows]


def get_all_inventory_for_sync() -> list[list]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, category, item_name, size, purchase_date, purchase_price,
                   status, sale_date, sale_price, shipping_cost, sale_group_id, notes
              FROM inventory
             WHERE deleted_at IS NULL
             ORDER BY id DESC
        """).fetchall()
    result = []
    for r in rows:
        result.append([
            r["id"], r["category"], r["item_name"], r["size"] or "",
            iso_to_display(r["purchase_date"]) if r["purchase_date"] else "",
            f'${r["purchase_price"]:.2f}' if r["purchase_price"] is not None else "",
            r["status"],
            iso_to_display(r["sale_date"]) if r["sale_date"] else "",
            f'${r["sale_price"]:.2f}' if r["sale_price"] is not None else "",
            f'${r["shipping_cost"]:.2f}' if r["shipping_cost"] else "",
            r["sale_group_id"] or "",
            r["notes"] or "",
        ])
    return result


def get_all_expenses_for_sync() -> list[list]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, date, category, amount, description
              FROM expenses
             WHERE deleted_at IS NULL
             ORDER BY id DESC
        """).fetchall()
    result = []
    for r in rows:
        result.append([
            r["id"],
            iso_to_display(r["date"]) if r["date"] else "",
            r["category"] or "",
            f'${r["amount"]:.2f}' if r["amount"] is not None else "",
            r["description"] or "",
        ])
    return result


def get_edit_old_value(action) -> str | None:
    """Return the current value of the field being edited, for display purposes."""
    table = action.table
    target = action.target
    try:
        _validate_table(table)
        _validate_field(table, action.field)
    except ValueError:
        return None

    with get_conn() as conn:
        if target == "last":
            row = conn.execute(
                "SELECT * FROM inventory WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 1"
                if table == "inventory" else
                "SELECT * FROM expenses WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM inventory WHERE id=? AND deleted_at IS NULL"
                if table == "inventory" else
                "SELECT * FROM expenses WHERE id=? AND deleted_at IS NULL",
                (int(target),)
            ).fetchone()

    if not row:
        return None
    try:
        return str(row[action.field])
    except (IndexError, KeyError):
        return None


def get_entry_id(action) -> int | None:
    """Resolve 'last' to an actual ID, return None if not found."""
    table = action.table
    target = action.target
    try:
        _validate_table(table)
    except ValueError:
        return None
    with get_conn() as conn:
        if target == "last":
            row = conn.execute(
                "SELECT id FROM inventory WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 1"
                if table == "inventory" else
                "SELECT id FROM expenses WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return row["id"] if row else None
        return int(target)


def record_delete_table(action) -> dict:
    """Return the prior_state dict for a delete action, recording which table was affected."""
    return {"table": action.table}


def _next_sale_group_id(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(sale_group_id) as mx FROM inventory").fetchone()
    return (row["mx"] or 0) + 1


def _infer_column_type(table: str, field: str) -> str:
    real_cols = {
        "inventory": {"purchase_price", "sale_price"},
        "expenses": {"amount"},
    }
    int_cols = {
        "inventory": {"quantity", "sale_group_id"},
        "expenses": set(),
    }
    if field in real_cols.get(table, set()):
        return "real"
    if field in int_cols.get(table, set()):
        return "int"
    return "text"


# ── V1.5 analytics helpers ─────────────────────────────────────────────────────

def _period_clause(period: str | None, date_col: str) -> tuple[str, list]:
    """Return (SQL fragment, params) for an optional period filter on date_col."""
    if not period or period == "all":
        return "", []
    if period == "week":
        return f" AND {date_col} >= date('now', '-7 days')", []
    if period == "month":
        return f" AND {date_col} >= date('now', '-1 month')", []
    if period == "quarter":
        return f" AND {date_col} >= date('now', '-3 months')", []
    if period == "year":
        return f" AND {date_col} >= date('now', '-1 year')", []
    return "", []


def get_fifo_preview(item_name: str, quantity: int) -> list[dict]:
    """Return the FIFO rows that would be consumed by a sell — for P&L preview only."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, purchase_price, purchase_date
              FROM inventory
             WHERE item_name = ? AND status = 'active' AND deleted_at IS NULL
             ORDER BY purchase_date ASC, id ASC
             LIMIT ?
        """, (item_name, quantity)).fetchall()
    if len(rows) < quantity:
        raise ValueError(f"Only {len(rows)} unsold '{item_name}' available.")
    return [dict(r) for r in rows]


def get_profit_summary(period: str | None = None, category: str | None = None) -> dict:
    """Returns revenue, COGS, gross profit, expenses, net profit, and by-category breakdown."""
    period_sql, period_params = _period_clause(period, "i.sale_date")
    cat_sql = " AND i.category = ?" if category else ""
    cat_params = [category] if category else []

    with get_conn() as conn:
        totals = conn.execute(f"""
            SELECT COALESCE(SUM(i.sale_price), 0)     AS revenue,
                   COALESCE(SUM(i.purchase_price), 0)  AS cogs,
                   COALESCE(SUM(i.shipping_cost), 0)   AS shipping,
                   COUNT(*)                             AS units_sold
              FROM inventory i
             WHERE i.status = 'sold' AND i.deleted_at IS NULL
               {period_sql} {cat_sql}
        """, period_params + cat_params).fetchone()

        cats = conn.execute(f"""
            SELECT i.category,
                   COALESCE(SUM(i.sale_price), 0)    AS revenue,
                   COALESCE(SUM(i.purchase_price), 0) AS cogs,
                   COALESCE(SUM(i.shipping_cost), 0)  AS shipping,
                   COUNT(*)                            AS units_sold
              FROM inventory i
             WHERE i.status = 'sold' AND i.deleted_at IS NULL
               {period_sql} {cat_sql}
             GROUP BY i.category
             ORDER BY (SUM(i.sale_price) - SUM(i.purchase_price) - SUM(i.shipping_cost)) DESC
        """, period_params + cat_params).fetchall()

        exp_period_sql, exp_params = _period_clause(period, "e.date")
        exp_row = conn.execute(f"""
            SELECT COALESCE(SUM(e.amount), 0) AS expenses
              FROM expenses e
             WHERE e.deleted_at IS NULL {exp_period_sql}
        """, exp_params).fetchone()

    revenue = totals["revenue"]
    cogs = totals["cogs"]
    shipping = totals["shipping"]
    expenses = exp_row["expenses"]
    return {
        "revenue": revenue,
        "cogs": cogs,
        "shipping": shipping,
        "gross_profit": revenue - cogs - shipping,
        "expenses": expenses,
        "net_profit": revenue - cogs - shipping - expenses,
        "units_sold": totals["units_sold"],
        "by_category": [
            {
                "category": r["category"],
                "revenue": r["revenue"],
                "cogs": r["cogs"],
                "shipping": r["shipping"],
                "gross_profit": r["revenue"] - r["cogs"] - r["shipping"],
                "units_sold": r["units_sold"],
            }
            for r in cats
        ],
    }


def get_velocity_stats(category: str | None = None) -> dict:
    """Returns per-category velocity (avg/median days-to-sell) plus slow current stock."""
    from statistics import median as _median

    cat_sql = " AND category = ?" if category else ""
    cat_params = [category] if category else []

    with get_conn() as conn:
        sold = conn.execute(f"""
            SELECT category, item_name,
                   CAST(julianday(sale_date) - julianday(purchase_date) AS INTEGER) AS days_to_sell
              FROM inventory
             WHERE status = 'sold' AND deleted_at IS NULL AND sale_date IS NOT NULL
               {cat_sql}
             ORDER BY category, days_to_sell
        """, cat_params).fetchall()

        cat_agg = conn.execute(f"""
            SELECT category,
                   ROUND(AVG(julianday(sale_date) - julianday(purchase_date)), 1) AS avg_days,
                   COUNT(*) AS units_sold
              FROM inventory
             WHERE status = 'sold' AND deleted_at IS NULL AND sale_date IS NOT NULL
               {cat_sql}
             GROUP BY category
             ORDER BY avg_days ASC
        """, cat_params).fetchall()

        active = conn.execute(f"""
            SELECT id, category, item_name, size,
                   CAST(julianday('now') - julianday(purchase_date) AS INTEGER) AS days_held
              FROM inventory
             WHERE status = 'active' AND deleted_at IS NULL
               {cat_sql}
        """, cat_params).fetchall()

    by_cat: dict[str, list] = {}
    for r in sold:
        by_cat.setdefault(r["category"], []).append(r)

    categories = []
    cat_avg: dict[str, float] = {}
    for agg in cat_agg:
        c = agg["category"]
        rows_c = by_cat.get(c, [])
        days_list = [r["days_to_sell"] for r in rows_c]
        med = round(_median(days_list), 1) if days_list else 0.0
        fastest = min(rows_c, key=lambda r: r["days_to_sell"], default=None)
        slowest = max(rows_c, key=lambda r: r["days_to_sell"], default=None)
        cat_avg[c] = agg["avg_days"]
        categories.append({
            "category": c,
            "avg_days": agg["avg_days"],
            "median_days": med,
            "fastest_item": fastest["item_name"] if fastest else None,
            "fastest_days": fastest["days_to_sell"] if fastest else None,
            "slowest_item": slowest["item_name"] if slowest else None,
            "slowest_days": slowest["days_to_sell"] if slowest else None,
            "units_sold": agg["units_sold"],
        })

    slow_current = []
    for r in active:
        c = r["category"]
        if c in cat_avg and r["days_held"] > cat_avg[c]:
            slow_current.append({
                "id": r["id"],
                "item_name": r["item_name"],
                "size": r["size"],
                "category": c,
                "days_held": r["days_held"],
                "category_avg_days": cat_avg[c],
                "days_over_avg": r["days_held"] - cat_avg[c],
            })
    slow_current.sort(key=lambda x: x["days_over_avg"], reverse=True)
    return {"categories": categories, "slow_current": slow_current[:20]}


def get_roi_leaderboard(
    direction: str = "best",
    category: str | None = None,
    period: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Returns best or worst flips by ROI%, with optional category and period filters."""
    period_sql, period_params = _period_clause(period, "sale_date")
    cat_sql = " AND category = ?" if category else ""
    cat_params = [category] if category else []
    order = "DESC" if direction == "best" else "ASC"

    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT id, item_name, category,
                   purchase_price, sale_price,
                   (sale_price - purchase_price) AS profit,
                   ROUND((sale_price - purchase_price) / purchase_price * 100.0, 1) AS roi_pct,
                   purchase_date, sale_date,
                   CAST(julianday(sale_date) - julianday(purchase_date) AS INTEGER) AS days_held
              FROM inventory
             WHERE status = 'sold' AND deleted_at IS NULL AND purchase_price > 0
               {period_sql} {cat_sql}
             ORDER BY roi_pct {order}
             LIMIT ?
        """, period_params + cat_params + [limit]).fetchall()
    return [dict(r) for r in rows]


def get_cash_flow(period: str | None = None) -> dict:
    """Returns cash in/out for the period plus current capital locked in active inventory."""
    period_sql_sale, sale_params = _period_clause(period, "sale_date")
    period_sql_buy,  buy_params  = _period_clause(period, "purchase_date")
    period_sql_exp,  exp_params  = _period_clause(period, "date")

    with get_conn() as conn:
        cash_in = conn.execute(f"""
            SELECT COALESCE(SUM(sale_price), 0) AS v FROM inventory
             WHERE status = 'sold' AND deleted_at IS NULL {period_sql_sale}
        """, sale_params).fetchone()["v"]

        purchases = conn.execute(f"""
            SELECT COALESCE(SUM(purchase_price), 0) AS v FROM inventory
             WHERE deleted_at IS NULL {period_sql_buy}
        """, buy_params).fetchone()["v"]

        expenses = conn.execute(f"""
            SELECT COALESCE(SUM(amount), 0) AS v FROM expenses
             WHERE deleted_at IS NULL {period_sql_exp}
        """, exp_params).fetchone()["v"]

        locked = conn.execute("""
            SELECT category, COUNT(*) AS units, COALESCE(SUM(purchase_price), 0) AS capital
              FROM inventory
             WHERE status = 'active' AND deleted_at IS NULL
             GROUP BY category ORDER BY capital DESC
        """).fetchall()

    by_cat = [{"category": r["category"], "units": r["units"], "capital": r["capital"]} for r in locked]
    return {
        "cash_in": cash_in,
        "purchases": purchases,
        "expenses": expenses,
        "cash_out": purchases + expenses,
        "net_cash": cash_in - (purchases + expenses),
        "capital_locked_total": sum(r["capital"] for r in by_cat),
        "capital_locked_by_category": by_cat,
    }
