import logging
import os
import re
from pathlib import Path
import gspread
from google.oauth2.service_account import Credentials
import config
import db

log = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

INV_HEADERS = ["ID", "Category", "Item Name", "Size", "Purchase Date", "Purchase Price",
               "Status", "Sale Date", "Sale Price", "Sale Group", "Notes"]
EXP_HEADERS = ["ID", "Date", "Category", "Amount", "Description"]

_SHEETS_ID_RE = re.compile(r'^[A-Za-z0-9_-]{25,60}$')


def _validate_config():
    """Validate sheets-related config at call time, not import time."""
    creds_path = Path(config.GOOGLE_SERVICE_ACCOUNT_PATH).resolve()
    project_root = Path(__file__).parent.resolve()
    if not creds_path.is_relative_to(project_root):
        raise ValueError(
            f"GOOGLE_SERVICE_ACCOUNT_PATH must be inside the project directory. Got: {creds_path}"
        )
    if not creds_path.suffix == ".json":
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_PATH must point to a .json file.")
    if not creds_path.exists():
        raise FileNotFoundError(f"Service account file not found: {creds_path}")
    if not _SHEETS_ID_RE.match(config.GOOGLE_SHEETS_ID):
        raise ValueError(f"GOOGLE_SHEETS_ID looks invalid: {config.GOOGLE_SHEETS_ID!r}")
    return creds_path


def _get_client() -> gspread.Client:
    creds_path = _validate_config()
    creds = Credentials.from_service_account_file(str(creds_path), scopes=SCOPES)
    return gspread.authorize(creds)


def _ensure_worksheets(spreadsheet: gspread.Spreadsheet):
    titles = [ws.title for ws in spreadsheet.worksheets()]
    if "Inventory" not in titles:
        ws = spreadsheet.add_worksheet("Inventory", rows=1000, cols=len(INV_HEADERS))
        ws.freeze(rows=1)
        ws.update("A1", [INV_HEADERS])
    if "Expenses" not in titles:
        ws = spreadsheet.add_worksheet("Expenses", rows=1000, cols=len(EXP_HEADERS))
        ws.freeze(rows=1)
        ws.update("A1", [EXP_HEADERS])


def _overwrite_worksheet(ws: gspread.Worksheet, rows: list[list], col_range: str):
    """Overwrite worksheet data below the header row."""
    if not rows:
        # Nothing to write — just clear everything below the header.
        ws.batch_clear([f"A2:{col_range}1000"])
        return

    num_rows = max(2, len(rows) + 1)
    ws.resize(rows=num_rows)
    ws.batch_update([{
        "range": f"A2:{col_range}{1 + len(rows)}",
        "values": rows,
    }])


def sync():
    gc = _get_client()
    spreadsheet = gc.open_by_key(config.GOOGLE_SHEETS_ID)
    _ensure_worksheets(spreadsheet)

    inv_ws = spreadsheet.worksheet("Inventory")
    inv_rows = db.get_all_inventory_for_sync()
    _overwrite_worksheet(inv_ws, inv_rows, "K")

    exp_ws = spreadsheet.worksheet("Expenses")
    exp_rows = db.get_all_expenses_for_sync()
    _overwrite_worksheet(exp_ws, exp_rows, "E")

    log.info("Sheets sync complete.")
