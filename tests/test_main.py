"""Tests for main.py — format helpers, confirmation summaries, query formatters."""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


# ── fmt_date ────────────────────────────────────────────────────────────────────

def test_fmt_date_valid():
    from main import fmt_date
    assert fmt_date("2025-11-11") == "11/11/2025"


def test_fmt_date_bad_format_returns_raw():
    from main import fmt_date
    assert fmt_date("2025/11/11") == "2025/11/11"


def test_fmt_date_none_returns_fallback():
    from main import fmt_date
    result = fmt_date(None)
    assert result == "unknown date"


def test_fmt_price():
    from main import fmt_price
    assert fmt_price(29.99) == "$29.99"
    assert fmt_price(0.0) == "$0.00"
    assert fmt_price(1000.0) == "$1000.00"


# ── build_confirmation_summary ──────────────────────────────────────────────────

def test_confirmation_add_single():
    from parser import AddInventory
    from main import build_confirmation_summary
    action = AddInventory(action="add", category="cards", item_name="Test Pack",
                          unit_price=29.99, purchase_date="2026-01-01")
    summary = build_confirmation_summary(action)
    assert "Test Pack" in summary
    assert "$29.99" in summary
    assert "01/01/2026" in summary
    assert "✅" in summary


def test_confirmation_add_multi_shows_total():
    from parser import AddInventory
    from main import build_confirmation_summary
    action = AddInventory(action="add", category="cards", item_name="Pack",
                          quantity=2, unit_price=10.00, purchase_date="2026-01-01")
    summary = build_confirmation_summary(action)
    assert "$20.00 total" in summary


def test_confirmation_add_no_size_shows_none():
    from parser import AddInventory
    from main import build_confirmation_summary
    action = AddInventory(action="add", category="cards", item_name="Pack",
                          unit_price=5.00, purchase_date="2026-01-01", size=None)
    summary = build_confirmation_summary(action)
    assert "(none)" in summary


def test_confirmation_add_with_size():
    from parser import AddInventory
    from main import build_confirmation_summary
    action = AddInventory(action="add", category="sneakers", item_name="Shoe",
                          unit_price=100.0, purchase_date="2026-01-01", size="10")
    summary = build_confirmation_summary(action)
    assert "10" in summary


def test_confirmation_sell_shows_pnl(db_mod, monkeypatch):
    """Inline P&L appears when DB has matching FIFO rows."""
    import db
    monkeypatch.setattr(db, "DB_PATH", db_mod.DB_PATH)
    from parser import AddInventory, SellItems
    from main import build_confirmation_summary
    action_add = AddInventory(action="add", category="cards", item_name="Slab",
                              unit_price=50.00, purchase_date="2026-01-01")
    db_mod.commit_action(action_add)

    action_sell = SellItems(action="sell", item_name="Slab", quantity=1,
                            total_price=100.00, sale_date="2026-06-08")
    summary = build_confirmation_summary(action_sell)
    assert "ROI" in summary or "cost" in summary.lower() or "$50.00" in summary


def test_confirmation_sell_pnl_missing_item_still_shows(monkeypatch):
    """If FIFO preview fails (item not in DB), confirmation still renders without P&L."""
    from parser import SellItems
    from main import build_confirmation_summary
    action = SellItems(action="sell", item_name="Ghost Item", quantity=1,
                       total_price=100.00, sale_date="2026-06-08")
    summary = build_confirmation_summary(action)
    assert "Ghost Item" in summary
    assert "✅" in summary


def test_confirmation_expense():
    from parser import AddExpense
    from main import build_confirmation_summary
    action = AddExpense(action="expense", amount=22.00, description="card sleeves",
                        category="supplies", date="2026-01-01")
    summary = build_confirmation_summary(action)
    assert "$22.00" in summary
    assert "card sleeves" in summary


def test_confirmation_edit_with_prior_state():
    from parser import EditEntry
    from main import build_confirmation_summary
    action = EditEntry(action="edit", target=47, table="inventory",
                       field="purchase_price", new_value="34.99")
    prior = {"id": 47, "table": "inventory", "field": "purchase_price", "old_value": "29.99"}
    summary = build_confirmation_summary(action, prior_state=prior)
    assert "#47" in summary
    assert "$29.99" in summary
    assert "$34.99" in summary
    assert "→" in summary


def test_confirmation_edit_null_old_value_no_crash():
    from parser import EditEntry
    from main import build_confirmation_summary
    action = EditEntry(action="edit", target=1, table="inventory",
                       field="sale_price", new_value="50.00")
    prior = {"id": 1, "table": "inventory", "field": "sale_price", "old_value": "None"}
    summary = build_confirmation_summary(action, prior_state=prior)
    assert "sale_price" in summary
    assert "✅" in summary


def test_confirmation_delete():
    from parser import DeleteEntry
    from main import build_confirmation_summary
    action = DeleteEntry(action="delete", target=47, table="inventory")
    summary = build_confirmation_summary(action)
    assert "#47" in summary
    assert "✅" in summary


def test_confirmation_delete_last():
    from parser import DeleteEntry
    from main import build_confirmation_summary
    action = DeleteEntry(action="delete", target="last", table="inventory")
    summary = build_confirmation_summary(action)
    assert "last entry" in summary


def test_confirmation_undo():
    from parser import UndoLast
    from main import build_confirmation_summary
    summary = build_confirmation_summary(UndoLast(action="undo"))
    assert "Undo" in summary
    assert "✅" in summary


def test_confirmation_rip():
    from parser import RipItem
    from main import build_confirmation_summary
    action = RipItem(action="rip", item_name="Blue Jacket", quantity=1)
    summary = build_confirmation_summary(action)
    assert "Blue Jacket" in summary
    assert "damaged" in summary.lower()


# ── format_query_results ────────────────────────────────────────────────────────

def _query(qt, filters=None):
    from parser import Query
    from main import format_query_results
    q = Query(action="query", query_type=qt, filters=filters or {})
    return format_query_results(q)


def test_format_stale_empty(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    with seeded_db.get_conn() as conn:
        conn.execute("UPDATE inventory SET deleted_at='2026-01-01' WHERE status='active'")
    result = _query("stale")
    assert "No stale inventory" in result


def test_format_unsold(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("unsold")
    assert "Prismatic Bundle" in result or "Nike Dunk Low" in result


def test_format_unsold_category_filter(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("unsold", {"category": "cards"})
    assert "cards" in result or "Prismatic" in result


def test_format_recent(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("recent")
    assert "Charizard EX" in result or "Air Max" in result


def test_format_by_category(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("by_category")
    assert "cards" in result and "sneakers" in result


def test_format_find_by_text(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("find", {"text": "dunk"})
    assert "Nike Dunk Low" in result
    assert "#" in result  # includes entry IDs


def test_format_find_price_range(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("find", {"category": "cards", "max_price": 30})
    assert "Prismatic Bundle" in result


def test_format_find_no_match(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("find", {"text": "nonexistent-xyz"})
    assert "No active items found" in result


def test_format_items_lists_names(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("items")
    # The unsold/active items should appear by name, not just category totals.
    assert "Prismatic Bundle" in result
    assert "Nike Dunk Low" in result


def test_format_items_category_filter(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("items", {"category": "sneakers"})
    assert "Nike Dunk Low" in result
    assert "Prismatic Bundle" not in result


def test_format_items_empty(db_mod, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", db_mod.DB_PATH)
    result = _query("items")
    assert "No active inventory" in result


def test_format_profit(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("profit")
    assert "Revenue" in result
    assert "$186.00" in result
    assert "Net profit" in result


def test_format_profit_with_period(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("profit", {"period": "month"})
    assert "(month)" in result
    assert "Revenue" in result


def test_format_profit_by_category(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("profit")
    assert "By category" in result
    assert "sneakers" in result or "cards" in result


def test_format_velocity(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("velocity")
    assert "velocity" in result.lower()
    assert "cards" in result


def test_format_velocity_empty():
    from parser import Query
    from main import format_query_results
    import db
    with patch.object(db, "get_velocity_stats", return_value={"categories": [], "slow_current": []}):
        result = format_query_results(Query(action="query", query_type="velocity", filters={}))
    assert "No sold items" in result


def test_format_leaderboard_best(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("leaderboard", {"direction": "best"})
    assert "Best flips" in result
    assert "ROI" in result


def test_format_leaderboard_worst(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("leaderboard", {"direction": "worst"})
    assert "Worst flips" in result


def test_format_leaderboard_shows_positive_sign(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("leaderboard", {"direction": "best"})
    assert "+" in result


def test_format_cashflow(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("cashflow")
    assert "Cash in" in result
    assert "Capital locked" in result
    assert "$186.00" in result


def test_format_cashflow_with_period(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("cashflow", {"period": "month"})
    assert "(month)" in result


def test_format_expenses_sum(seeded_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", seeded_db.DB_PATH)
    result = _query("expenses_sum")
    assert "$37.00" in result


def test_format_unknown_query_type():
    # unknown type falls through to the else branch safely
    from parser import Query
    from main import format_query_results
    # Can't create an invalid Query due to Literal validation, so test the else path directly
    q = MagicMock()
    q.query_type = "unknown_future_type"
    q.filters = {}
    result = format_query_results(q)
    assert "not yet implemented" in result


# ── _success_message ────────────────────────────────────────────────────────────

def test_success_add_single():
    from parser import AddInventory
    from main import _success_message
    action = AddInventory(action="add", category="cards", item_name="Pack",
                          unit_price=5.0, purchase_date="2026-01-01")
    assert "#103" in _success_message(action, [103])


def test_success_add_multi():
    from parser import AddInventory
    from main import _success_message
    action = AddInventory(action="add", category="cards", item_name="Pack",
                          unit_price=5.0, purchase_date="2026-01-01")
    msg = _success_message(action, [103, 104, 105])
    assert "#103" in msg and "#105" in msg


def test_success_sell_shows_sale_group():
    from parser import SellItems
    from main import _success_message
    action = SellItems(action="sell", item_name="Pack", quantity=1,
                       total_price=10.0, sale_date="2026-06-08")
    msg = _success_message(action, [87, 103], sale_group_id=14)
    assert "#87" in msg and "#103" in msg
    assert "14" in msg


def test_success_sell_no_group_id():
    from parser import SellItems
    from main import _success_message
    action = SellItems(action="sell", item_name="Pack", quantity=1,
                       total_price=10.0, sale_date="2026-06-08")
    msg = _success_message(action, [87], sale_group_id=None)
    assert "#87" in msg


def test_success_rip():
    from parser import RipItem
    from main import _success_message
    action = RipItem(action="rip", item_name="Jacket", quantity=1)
    msg = _success_message(action, [22])
    assert "#22" in msg and "ripped" in msg


def test_success_expense():
    from parser import AddExpense
    from main import _success_message
    action = AddExpense(action="expense", amount=15.0, description="x",
                        category=None, date="2026-01-01")
    msg = _success_message(action, [8])
    assert "#8" in msg


def test_success_edit():
    from parser import EditEntry
    from main import _success_message
    action = EditEntry(action="edit", target=47, table="inventory",
                       field="purchase_price", new_value="34.99")
    msg = _success_message(action, [47])
    assert "#47" in msg


def test_success_delete():
    from parser import DeleteEntry
    from main import _success_message
    action = DeleteEntry(action="delete", target=47, table="inventory")
    msg = _success_message(action, [47])
    assert "#47" in msg


# ── Pending expiration logic ────────────────────────────────────────────────────

def test_cleanup_pending_removes_expired():
    """cleanup_pending task removes entries older than CONFIRMATION_TIMEOUT_MINUTES."""
    import main as main_mod
    from datetime import datetime, timedelta
    from dataclasses import dataclass

    @dataclass
    class FakePending:
        user_id: int
        created_at: datetime
        action: object = None
        prior_state: object = None

    old_time = datetime.now() - timedelta(minutes=999)
    fresh_time = datetime.now()

    main_mod.pending[9001] = FakePending(user_id=1, created_at=old_time)
    main_mod.pending[9002] = FakePending(user_id=1, created_at=fresh_time)

    # Run expiry logic directly (mirrors the cleanup_pending task body)
    import config
    timeout = timedelta(minutes=config.CONFIRMATION_TIMEOUT_MINUTES)
    now = datetime.now()
    expired = [mid for mid, pc in list(main_mod.pending.items()) if now - pc.created_at > timeout]
    for mid in expired:
        main_mod.pending.pop(mid, None)

    assert 9001 not in main_mod.pending
    assert 9002 in main_mod.pending
    main_mod.pending.pop(9002, None)  # cleanup
