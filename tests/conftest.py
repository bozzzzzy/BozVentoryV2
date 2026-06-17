"""Shared fixtures for all test modules."""
import os
import pytest

# Set all required env vars before any project module is imported.
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ.setdefault("DISCORD_USER_ID", "111111111111111111")
os.environ.setdefault("DISCORD_CHANNEL_ID", "222222222222222222")
os.environ.setdefault("GOOGLE_SHEETS_ID", "a" * 26)
os.environ.setdefault("GOOGLE_SERVICE_ACCOUNT_PATH", "./creds.json")


@pytest.fixture()
def db_mod(monkeypatch, tmp_path):
    """Return the db module wired to a fresh temporary SQLite database."""
    import db as db_module
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    db_module.init_db()
    return db_module


@pytest.fixture()
def seeded_db(db_mod):
    """DB with a standard set of inventory rows and expenses for analytics tests."""
    with db_mod.get_conn() as conn:
        conn.executescript("""
            INSERT INTO inventory (category, item_name, size, purchase_date, purchase_price, status, sale_date, sale_price, sale_group_id)
                VALUES ('cards',     'Charizard EX',        NULL, '2026-01-01', 4.50,  'sold', '2026-01-04', 25.00, 1);
            INSERT INTO inventory (category, item_name, size, purchase_date, purchase_price, status, sale_date, sale_price, sale_group_id)
                VALUES ('cards',     'Charizard EX',        NULL, '2026-01-05', 4.50,  'sold', '2026-01-08', 26.00, 2);
            INSERT INTO inventory (category, item_name, size, purchase_date, purchase_price, status, sale_date, sale_price, sale_group_id)
                VALUES ('sneakers',  'Air Max 90',          '10', '2026-02-01', 85.00, 'sold', '2026-02-15', 135.00, 3);
            INSERT INTO inventory (category, item_name, size, purchase_date, purchase_price)
                VALUES ('cards',     'Prismatic Bundle',    NULL, '2026-03-01', 29.99);
            INSERT INTO inventory (category, item_name, size, purchase_date, purchase_price)
                VALUES ('sneakers',  'Nike Dunk Low',       '9',  '2026-02-10', 110.00);
            INSERT INTO expenses (date, category, amount, description)
                VALUES ('2026-01-10', 'shipping', 15.00, 'toploaders');
            INSERT INTO expenses (date, category, amount, description)
                VALUES ('2026-02-01', 'supplies', 22.00, 'card sleeves');
        """)
    return db_mod
