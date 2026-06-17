"""Tests for db.py — schema, CRUD, FIFO, undo, analytics."""
import pytest
from datetime import date


# ── Schema & helpers ────────────────────────────────────────────────────────────

def test_init_db_creates_tables(db_mod):
    with db_mod.get_conn() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "inventory" in tables
    assert "expenses" in tables


def test_init_db_idempotent(db_mod):
    db_mod.init_db()  # second call should not raise
    with db_mod.get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]
    assert count == 0


def test_today_iso_format(db_mod):
    today = db_mod.today_iso()
    assert len(today) == 10
    assert today[4] == "-" and today[7] == "-"


def test_iso_to_display(db_mod):
    assert db_mod.iso_to_display("2025-11-11") == "11/11/2025"


def test_display_to_iso(db_mod):
    assert db_mod.display_to_iso("11/11/2025") == "2025-11-11"


def test_iso_to_display_bad_input(db_mod):
    with pytest.raises(ValueError):
        db_mod.iso_to_display("not-a-date")


# ── Whitelist validation ────────────────────────────────────────────────────────

def test_validate_table_rejects_unknown(db_mod):
    with pytest.raises(ValueError, match="Invalid table"):
        db_mod._validate_table("evil; DROP TABLE inventory")


def test_validate_table_accepts_known(db_mod):
    db_mod._validate_table("inventory")
    db_mod._validate_table("expenses")


def test_validate_field_rejects_unknown(db_mod):
    with pytest.raises(ValueError, match="Invalid field"):
        db_mod._validate_field("inventory", "deleted_at")


def test_validate_field_rejects_internal_columns(db_mod):
    for col in ("id", "deleted_at", "sale_group_id"):
        with pytest.raises(ValueError):
            db_mod._validate_field("inventory", col)


def test_validate_field_accepts_writable(db_mod):
    for col in ("item_name", "category", "size", "purchase_price", "notes"):
        db_mod._validate_field("inventory", col)
    for col in ("date", "category", "amount", "description"):
        db_mod._validate_field("expenses", col)


# ── Add ────────────────────────────────────────────────────────────────────────

def test_commit_add_single(db_mod):
    from parser import AddInventory
    action = AddInventory(action="add", category="cards", item_name="Test Card",
                          unit_price=5.00, purchase_date="2026-01-01")
    ids, group = db_mod.commit_action(action)
    assert len(ids) == 1
    assert group is None
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT * FROM inventory WHERE id=?", (ids[0],)).fetchone()
    assert row["item_name"] == "Test Card"
    assert row["purchase_price"] == 5.00
    assert row["status"] == "active"
    assert row["deleted_at"] is None


def test_commit_add_multi_unit(db_mod):
    from parser import AddInventory
    action = AddInventory(action="add", category="cards", item_name="Booster Pack",
                          quantity=3, unit_price=4.50, purchase_date="2026-01-01")
    ids, _ = db_mod.commit_action(action)
    assert len(ids) == 3
    with db_mod.get_conn() as conn:
        rows = conn.execute("SELECT * FROM inventory WHERE item_name='Booster Pack'").fetchall()
    assert len(rows) == 3
    assert all(r["purchase_price"] == 4.50 for r in rows)


def test_commit_add_no_size_stored_as_null(db_mod):
    from parser import AddInventory
    action = AddInventory(action="add", category="cards", item_name="Card",
                          unit_price=1.00, purchase_date="2026-01-01", size=None)
    ids, _ = db_mod.commit_action(action)
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT size FROM inventory WHERE id=?", (ids[0],)).fetchone()
    assert row["size"] is None


# ── Sell / FIFO ────────────────────────────────────────────────────────────────

def _add_items(db_mod, item_name, qty, price, purchase_date="2026-01-01", category="cards"):
    from parser import AddInventory
    action = AddInventory(action="add", category=category, item_name=item_name,
                          quantity=qty, unit_price=price, purchase_date=purchase_date)
    return db_mod.commit_action(action)[0]


def test_commit_sell_fifo_order(db_mod):
    from parser import SellItems
    _add_items(db_mod, "Pack", 1, 4.00, "2026-01-01")
    _add_items(db_mod, "Pack", 1, 5.00, "2026-01-05")

    action = SellItems(action="sell", item_name="Pack", quantity=1,
                       total_price=20.00, sale_date="2026-06-01")
    ids, group = db_mod.commit_action(action)
    assert len(ids) == 1
    assert group is not None
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT * FROM inventory WHERE id=?", (ids[0],)).fetchone()
    assert row["purchase_price"] == 4.00  # oldest first
    assert row["status"] == "sold"
    assert row["sale_price"] == 20.00


def test_commit_sell_multi_unit_splits_price(db_mod):
    from parser import SellItems
    _add_items(db_mod, "Slab", 2, 100.00)
    action = SellItems(action="sell", item_name="Slab", quantity=2,
                       total_price=300.00, sale_date="2026-06-01")
    ids, group = db_mod.commit_action(action)
    assert len(ids) == 2
    with db_mod.get_conn() as conn:
        rows = conn.execute("SELECT sale_price, sale_group_id FROM inventory WHERE id IN (?,?)", ids).fetchall()
    assert all(r["sale_price"] == 150.00 for r in rows)
    assert all(r["sale_group_id"] == group for r in rows)


def test_commit_sell_stores_per_unit_shipping(db_mod):
    from parser import SellItems
    _add_items(db_mod, "Tee", 2, 10.00)
    action = SellItems(action="sell", item_name="Tee", quantity=2,
                       total_price=80.00, sale_date="2026-06-01", shipping_cost=12.00)
    ids, _ = db_mod.commit_action(action)
    with db_mod.get_conn() as conn:
        rows = conn.execute("SELECT shipping_cost FROM inventory WHERE id IN (?,?)", ids).fetchall()
    assert all(r["shipping_cost"] == 6.00 for r in rows)


def test_shipping_reduces_profit(db_mod):
    from parser import SellItems
    _add_items(db_mod, "Hat", 1, 10.00)
    db_mod.commit_action(SellItems(action="sell", item_name="Hat", quantity=1,
                                   total_price=50.00, sale_date="2026-06-01", shipping_cost=8.00))
    p = db_mod.get_profit_summary()
    assert p["shipping"] == 8.00
    assert p["gross_profit"] == 50.00 - 10.00 - 8.00
    assert p["net_profit"] == 50.00 - 10.00 - 8.00


def test_undo_sell_clears_shipping(db_mod):
    from parser import SellItems
    add_ids = _add_items(db_mod, "Jacket", 1, 20.00)
    sell_ids, _ = db_mod.commit_action(SellItems(action="sell", item_name="Jacket", quantity=1,
                                                 total_price=60.00, sale_date="2026-06-01", shipping_cost=10.00))
    db_mod.undo_action("sell", sell_ids)
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT status, shipping_cost FROM inventory WHERE id=?", (add_ids[0],)).fetchone()
    assert row["status"] == "active"
    assert row["shipping_cost"] == 0


def test_migration_adds_shipping_column(db_mod):
    with db_mod.get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(inventory)").fetchall()}
    assert "shipping_cost" in cols


def test_commit_sell_insufficient_stock_raises(db_mod):
    from parser import SellItems
    _add_items(db_mod, "Rare Card", 1, 50.00)
    action = SellItems(action="sell", item_name="Rare Card", quantity=3,
                       total_price=150.00, sale_date="2026-06-01")
    with pytest.raises(ValueError, match="Only 1"):
        db_mod.commit_action(action)


def test_commit_sell_no_stock_raises(db_mod):
    from parser import SellItems
    action = SellItems(action="sell", item_name="Ghost Item", quantity=1,
                       total_price=10.00, sale_date="2026-06-01")
    with pytest.raises(ValueError):
        db_mod.commit_action(action)


def test_commit_sell_sale_group_increments(db_mod):
    from parser import SellItems
    _add_items(db_mod, "CardA", 1, 5.00)
    _add_items(db_mod, "CardB", 1, 5.00)
    _, g1 = db_mod.commit_action(SellItems(action="sell", item_name="CardA", quantity=1,
                                            total_price=10.00, sale_date="2026-06-01"))
    _, g2 = db_mod.commit_action(SellItems(action="sell", item_name="CardB", quantity=1,
                                            total_price=10.00, sale_date="2026-06-01"))
    assert g2 == g1 + 1


# ── Rip ────────────────────────────────────────────────────────────────────────

def test_commit_rip(db_mod):
    from parser import RipItem
    _add_items(db_mod, "Jacket", 1, 35.00, category="clothing")
    action = RipItem(action="rip", item_name="Jacket", quantity=1)
    ids, _ = db_mod.commit_action(action)
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT status FROM inventory WHERE id=?", (ids[0],)).fetchone()
    assert row["status"] == "ripped"


def test_commit_rip_insufficient_stock_raises(db_mod):
    from parser import RipItem
    action = RipItem(action="rip", item_name="No Such Item", quantity=1)
    with pytest.raises(ValueError):
        db_mod.commit_action(action)


# ── Expense ────────────────────────────────────────────────────────────────────

def test_commit_expense(db_mod):
    from parser import AddExpense
    action = AddExpense(action="expense", amount=15.00, description="toploaders",
                        category="shipping", date="2026-01-10")
    ids, _ = db_mod.commit_action(action)
    assert len(ids) == 1
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT * FROM expenses WHERE id=?", (ids[0],)).fetchone()
    assert row["amount"] == 15.00
    assert row["category"] == "shipping"
    assert row["deleted_at"] is None


# ── Edit ────────────────────────────────────────────────────────────────────────

def test_commit_edit_price(db_mod):
    from parser import EditEntry
    add_ids = _add_items(db_mod, "Card", 1, 29.99)
    action = EditEntry(action="edit", target=add_ids[0], table="inventory",
                       field="purchase_price", new_value="34.99")
    ids, _ = db_mod.commit_action(action)
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT purchase_price FROM inventory WHERE id=?", (ids[0],)).fetchone()
    assert row["purchase_price"] == pytest.approx(34.99)


def test_commit_edit_last_target(db_mod):
    from parser import EditEntry
    _add_items(db_mod, "Card", 1, 10.00)
    _add_items(db_mod, "Shoe", 1, 80.00, category="sneakers")
    action = EditEntry(action="edit", target="last", table="inventory",
                       field="item_name", new_value="Nike Air Force 1")
    ids, _ = db_mod.commit_action(action)
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT item_name FROM inventory WHERE id=?", (ids[0],)).fetchone()
    assert row["item_name"] == "Nike Air Force 1"


def test_commit_edit_strips_dollar_sign(db_mod):
    from parser import EditEntry
    add_ids = _add_items(db_mod, "Card", 1, 10.00)
    action = EditEntry(action="edit", target=add_ids[0], table="inventory",
                       field="purchase_price", new_value="$19.99")
    db_mod.commit_action(action)
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT purchase_price FROM inventory WHERE id=?", (add_ids[0],)).fetchone()
    assert row["purchase_price"] == pytest.approx(19.99)


def test_commit_edit_nonexistent_id_raises(db_mod):
    from parser import EditEntry
    action = EditEntry(action="edit", target=99999, table="inventory",
                       field="item_name", new_value="x")
    with pytest.raises(ValueError, match="No entry"):
        db_mod.commit_action(action)


def test_commit_edit_blocked_field_raises(db_mod):
    from parser import EditEntry
    with pytest.raises(ValueError):
        EditEntry(action="edit", target=1, table="inventory",
                  field="deleted_at", new_value="2026-01-01")


# ── Delete ────────────────────────────────────────────────────────────────────

def test_commit_delete_soft(db_mod):
    from parser import DeleteEntry
    add_ids = _add_items(db_mod, "Card", 1, 5.00)
    action = DeleteEntry(action="delete", target=add_ids[0], table="inventory")
    db_mod.commit_action(action)
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT deleted_at FROM inventory WHERE id=?", (add_ids[0],)).fetchone()
    assert row["deleted_at"] is not None


def test_commit_delete_last(db_mod):
    from parser import DeleteEntry
    _add_items(db_mod, "Card", 1, 5.00)
    action = DeleteEntry(action="delete", target="last", table="inventory")
    ids, _ = db_mod.commit_action(action)
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT deleted_at FROM inventory WHERE id=?", (ids[0],)).fetchone()
    assert row["deleted_at"] is not None


def test_commit_delete_nonexistent_raises(db_mod):
    from parser import DeleteEntry
    action = DeleteEntry(action="delete", target=99999, table="inventory")
    with pytest.raises(ValueError, match="No entry"):
        db_mod.commit_action(action)


def test_deleted_rows_excluded_from_active_summary(db_mod):
    from parser import AddInventory, DeleteEntry
    add_ids = _add_items(db_mod, "Ghost Card", 1, 5.00)
    db_mod.commit_action(DeleteEntry(action="delete", target=add_ids[0], table="inventory"))
    summary = db_mod.get_active_inventory_summary()
    assert all(s["item_name"] != "Ghost Card" for s in summary)


# ── Clear (bulk delete) ─────────────────────────────────────────────────────────

def test_commit_clear_removes_all_active(db_mod):
    from parser import ClearInventory
    _add_items(db_mod, "Card A", 1, 5.00)
    _add_items(db_mod, "Card B", 2, 6.00)
    ids, group = db_mod.commit_action(ClearInventory(action="clear", table="inventory"))
    assert len(ids) == 3
    assert group is None
    assert db_mod.get_active_inventory_summary() == []


def test_commit_clear_by_category(db_mod):
    from parser import ClearInventory
    _add_items(db_mod, "Pikachu", 1, 5.00, category="cards")
    _add_items(db_mod, "Air Force 1", 1, 90.00, category="sneakers")
    ids, _ = db_mod.commit_action(ClearInventory(action="clear", table="inventory", category="cards"))
    assert len(ids) == 1
    remaining = db_mod.get_active_inventory_summary()
    assert len(remaining) == 1
    assert remaining[0]["item_name"] == "Air Force 1"


def test_commit_clear_empty_raises(db_mod):
    from parser import ClearInventory
    with pytest.raises(ValueError, match="Nothing to clear"):
        db_mod.commit_action(ClearInventory(action="clear", table="inventory"))


def test_undo_clear_restores_all(db_mod):
    from parser import ClearInventory
    _add_items(db_mod, "Card A", 1, 5.00)
    _add_items(db_mod, "Card B", 2, 6.00)
    ids, _ = db_mod.commit_action(ClearInventory(action="clear", table="inventory"))
    db_mod.undo_action("clear", ids, {"table": "inventory"})
    assert len(db_mod.get_active_inventory_summary()) == 2


def test_get_clear_preview_lists_rows(db_mod):
    _add_items(db_mod, "Preview Card", 2, 5.00)
    preview = db_mod.get_clear_preview("inventory")
    assert len(preview) == 2
    assert all(p["item_name"] == "Preview Card" for p in preview)


# ── Undo ───────────────────────────────────────────────────────────────────────

def test_undo_add(db_mod):
    add_ids = _add_items(db_mod, "Card", 2, 5.00)
    db_mod.undo_action("add", add_ids)
    with db_mod.get_conn() as conn:
        rows = conn.execute("SELECT deleted_at FROM inventory WHERE id IN (?,?)", add_ids).fetchall()
    assert all(r["deleted_at"] is not None for r in rows)


def test_undo_sell_restores_active(db_mod):
    from parser import SellItems
    add_ids = _add_items(db_mod, "Pack", 1, 5.00)
    sell_ids, _ = db_mod.commit_action(
        SellItems(action="sell", item_name="Pack", quantity=1,
                  total_price=10.00, sale_date="2026-06-01")
    )
    db_mod.undo_action("sell", sell_ids)
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT status, sale_price, sale_group_id FROM inventory WHERE id=?",
                           (sell_ids[0],)).fetchone()
    assert row["status"] == "active"
    assert row["sale_price"] is None
    assert row["sale_group_id"] is None


def test_undo_edit_restores_old_value(db_mod):
    from parser import EditEntry
    add_ids = _add_items(db_mod, "Card", 1, 29.99)
    prior = {"id": add_ids[0], "table": "inventory", "field": "purchase_price", "old_value": 29.99}
    action = EditEntry(action="edit", target=add_ids[0], table="inventory",
                       field="purchase_price", new_value="50.00")
    db_mod.commit_action(action, prior)
    db_mod.undo_action("edit", add_ids, prior_state=prior)
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT purchase_price FROM inventory WHERE id=?", (add_ids[0],)).fetchone()
    assert row["purchase_price"] == pytest.approx(29.99)


def test_undo_delete_restores_row(db_mod):
    from parser import DeleteEntry
    add_ids = _add_items(db_mod, "Card", 1, 5.00)
    prior_state = {"table": "inventory"}
    db_mod.commit_action(DeleteEntry(action="delete", target=add_ids[0], table="inventory"))
    db_mod.undo_action("delete", add_ids, prior_state=prior_state)
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT deleted_at FROM inventory WHERE id=?", (add_ids[0],)).fetchone()
    assert row["deleted_at"] is None


def test_undo_expense(db_mod):
    from parser import AddExpense
    ids, _ = db_mod.commit_action(
        AddExpense(action="expense", amount=10.00, description="test",
                   category=None, date="2026-01-01")
    )
    db_mod.undo_action("expense", ids)
    with db_mod.get_conn() as conn:
        row = conn.execute("SELECT deleted_at FROM expenses WHERE id=?", (ids[0],)).fetchone()
    assert row["deleted_at"] is not None


# ── Queries ────────────────────────────────────────────────────────────────────

def test_get_active_inventory_summary(db_mod):
    _add_items(db_mod, "Pack A", 2, 5.00)
    _add_items(db_mod, "Pack B", 1, 10.00)
    summary = db_mod.get_active_inventory_summary()
    names = {s["item_name"] for s in summary}
    assert "Pack A" in names and "Pack B" in names


def test_get_active_inventory_summary_category_filter(db_mod):
    _add_items(db_mod, "Pikachu", 1, 5.00, category="cards")
    _add_items(db_mod, "Air Force 1", 2, 90.00, category="sneakers")
    summary = db_mod.get_active_inventory_summary(category="sneakers")
    assert len(summary) == 1
    assert summary[0]["item_name"] == "Air Force 1"
    assert summary[0]["count"] == 2


def test_get_matching_items_by_text(seeded_db):
    rows = seeded_db.get_matching_items(text="dunk")
    assert len(rows) == 1
    assert rows[0]["item_name"] == "Nike Dunk Low"
    assert "id" in rows[0]


def test_get_matching_items_text_is_case_insensitive(seeded_db):
    assert len(seeded_db.get_matching_items(text="PRISMATIC")) == 1


def test_get_matching_items_by_category_and_size(db_mod):
    _add_items(db_mod, "Air Max 90", 1, 100.0, category="sneakers")
    with db_mod.get_conn() as conn:
        conn.execute("UPDATE inventory SET size='10' WHERE item_name='Air Max 90'")
    _add_items(db_mod, "Dunk", 1, 90.0, category="sneakers")
    rows = db_mod.get_matching_items(category="sneakers", size="10")
    assert len(rows) == 1
    assert rows[0]["item_name"] == "Air Max 90"


def test_get_matching_items_price_range(db_mod):
    _add_items(db_mod, "Cheap", 1, 5.0, category="cards")
    _add_items(db_mod, "Mid", 1, 50.0, category="cards")
    _add_items(db_mod, "Pricey", 1, 500.0, category="cards")
    rows = db_mod.get_matching_items(min_price=10, max_price=100)
    assert {r["item_name"] for r in rows} == {"Mid"}


def test_get_matching_items_excludes_sold_and_deleted(db_mod):
    from parser import SellItems, DeleteEntry
    _add_items(db_mod, "SoldCard", 1, 5.0)
    dead_ids = _add_items(db_mod, "DeadCard", 1, 5.0)
    _add_items(db_mod, "KeepCard", 1, 5.0)
    db_mod.commit_action(SellItems(action="sell", item_name="SoldCard", quantity=1,
                                   total_price=10.0, sale_date="2026-02-01"))
    db_mod.commit_action(DeleteEntry(action="delete", target=dead_ids[0], table="inventory"))
    names = [r["item_name"] for r in db_mod.get_matching_items(text="card")]
    assert "KeepCard" in names
    assert "SoldCard" not in names
    assert "DeadCard" not in names


def test_get_unsold_items_category_filter(seeded_db):
    cards = seeded_db.get_unsold_items(category="cards")
    assert all(r["category"] == "cards" for r in cards)


def test_get_unsold_items_min_days_filter(seeded_db):
    items = seeded_db.get_unsold_items(min_days_held=1)
    assert all(r["days_held"] >= 1 for r in items)


def test_get_recent_items_limit(seeded_db):
    items = seeded_db.get_recent_items(limit=2)
    assert len(items) <= 2


def test_get_stale_items(seeded_db):
    items = seeded_db.get_stale_items(stale_days=1)
    # Both active items were purchased many days ago relative to today
    assert len(items) >= 1
    assert all(r["days_held"] > 1 for r in items)


def test_soft_deleted_rows_excluded_from_sync(db_mod):
    from parser import AddInventory, DeleteEntry
    add_ids = _add_items(db_mod, "Gone Card", 1, 5.00)
    db_mod.commit_action(DeleteEntry(action="delete", target=add_ids[0], table="inventory"))
    rows = db_mod.get_all_inventory_for_sync()
    assert all(r[2] != "Gone Card" for r in rows)


# ── Period clause ────────────────────────────────────────────────────────────

def test_period_clause_all_returns_empty(db_mod):
    sql, params = db_mod._period_clause("all", "date")
    assert sql == "" and params == []


def test_period_clause_none_returns_empty(db_mod):
    sql, params = db_mod._period_clause(None, "date")
    assert sql == "" and params == []


def test_period_clause_week_contains_days(db_mod):
    sql, _ = db_mod._period_clause("week", "purchase_date")
    assert "-7 days" in sql


def test_period_clause_month(db_mod):
    sql, _ = db_mod._period_clause("month", "date")
    assert "-1 month" in sql


def test_period_clause_quarter(db_mod):
    sql, _ = db_mod._period_clause("quarter", "date")
    assert "-3 months" in sql


def test_period_clause_year(db_mod):
    sql, _ = db_mod._period_clause("year", "date")
    assert "-1 year" in sql


# ── Analytics ────────────────────────────────────────────────────────────────

def test_get_fifo_preview_correct_order(db_mod):
    _add_items(db_mod, "Slab", 1, 10.00, "2026-01-01")
    _add_items(db_mod, "Slab", 1, 20.00, "2026-01-10")
    preview = db_mod.get_fifo_preview("Slab", 1)
    assert len(preview) == 1
    assert preview[0]["purchase_price"] == 10.00  # oldest


def test_get_fifo_preview_raises_on_insufficient(db_mod):
    _add_items(db_mod, "Rare", 1, 50.00)
    with pytest.raises(ValueError):
        db_mod.get_fifo_preview("Rare", 5)


def test_get_profit_summary_all_time(seeded_db):
    data = seeded_db.get_profit_summary()
    assert data["revenue"] == pytest.approx(186.0)
    assert data["cogs"] == pytest.approx(94.0)
    assert data["gross_profit"] == pytest.approx(92.0)
    assert data["expenses"] == pytest.approx(37.0)
    assert data["net_profit"] == pytest.approx(55.0)
    assert data["units_sold"] == 3


def test_get_profit_summary_category_filter(seeded_db):
    data = seeded_db.get_profit_summary(category="cards")
    assert data["units_sold"] == 2
    assert data["revenue"] == pytest.approx(51.0)


def test_get_profit_summary_empty_period(seeded_db):
    data = seeded_db.get_profit_summary(period="week")
    # All sales are in the past (seeded with historical dates), so result is 0
    assert data["revenue"] == 0.0
    assert data["net_profit"] == 0.0


def test_get_profit_summary_by_category_breakdown(seeded_db):
    data = seeded_db.get_profit_summary()
    cats = {c["category"] for c in data["by_category"]}
    assert "cards" in cats and "sneakers" in cats


def test_get_velocity_stats(seeded_db):
    data = seeded_db.get_velocity_stats()
    cats = {c["category"] for c in data["categories"]}
    assert "cards" in cats and "sneakers" in cats
    card_cat = next(c for c in data["categories"] if c["category"] == "cards")
    assert card_cat["units_sold"] == 2
    assert card_cat["avg_days"] == pytest.approx(3.0)


def test_get_velocity_stats_category_filter(seeded_db):
    data = seeded_db.get_velocity_stats(category="sneakers")
    assert all(c["category"] == "sneakers" for c in data["categories"])


def test_get_velocity_stats_slow_current(seeded_db):
    slow = seeded_db.get_velocity_stats()["slow_current"]
    names = [s["item_name"] for s in slow]
    assert "Nike Dunk Low" in names or "Prismatic Bundle" in names


def test_get_velocity_stats_empty(db_mod):
    data = db_mod.get_velocity_stats()
    assert data["categories"] == []
    assert data["slow_current"] == []


def test_get_roi_leaderboard_best(seeded_db):
    rows = seeded_db.get_roi_leaderboard(direction="best")
    roi_vals = [r["roi_pct"] for r in rows]
    assert roi_vals == sorted(roi_vals, reverse=True)


def test_get_roi_leaderboard_worst(seeded_db):
    rows = seeded_db.get_roi_leaderboard(direction="worst")
    roi_vals = [r["roi_pct"] for r in rows]
    assert roi_vals == sorted(roi_vals)


def test_get_roi_leaderboard_category_filter(seeded_db):
    rows = seeded_db.get_roi_leaderboard(category="cards")
    assert all(r["category"] == "cards" for r in rows)


def test_get_roi_leaderboard_empty_period(seeded_db):
    rows = seeded_db.get_roi_leaderboard(period="week")
    assert rows == []


def test_get_cash_flow_all_time(seeded_db):
    data = seeded_db.get_cash_flow()
    assert data["cash_in"] == pytest.approx(186.0)
    assert data["expenses"] == pytest.approx(37.0)
    assert data["capital_locked_total"] == pytest.approx(139.99)
    cats = {c["category"] for c in data["capital_locked_by_category"]}
    assert "cards" in cats and "sneakers" in cats


def test_get_cash_flow_empty_period(seeded_db):
    data = seeded_db.get_cash_flow(period="week")
    assert data["cash_in"] == 0.0
    # Capital locked is always current state, not period-filtered
    assert data["capital_locked_total"] == pytest.approx(139.99)
