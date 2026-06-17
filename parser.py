import json
import re
import anthropic
from typing import Literal, Union
from pydantic import BaseModel, ConfigDict
from db import today_iso, iso_to_display
import config

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

# Writable fields per table — must match db.ALLOWED_FIELDS
_EDITABLE_FIELDS = {
    "inventory": frozenset({
        "item_name", "category", "size", "purchase_date", "purchase_price",
        "sale_date", "sale_price", "notes", "status",
    }),
    "expenses": frozenset({"date", "category", "amount", "description"}),
}

SYSTEM_PROMPT_TEMPLATE = """You are a parser for a personal reselling inventory bot. You receive a single plain-English message
from the user and must return a JSON object describing exactly one structured action.

CONTEXT
Today's date (ISO): {today_iso}
Today's date (display): {today_display}
Available categories: cards, clothing, sneakers, electronics, other
Current active inventory (for name-matching on sells, rips, edits):
{active_inventory_json}

ACTIONS
You must return exactly one of these JSON shapes (see "action" field):

1. add — user is recording a purchase
   {{ "action": "add", "category": "...", "item_name": "...", "size": null | "...",
     "quantity": 1, "unit_price": 0.0, "purchase_date": "YYYY-MM-DD" }}
   - quantity defaults to 1 if not stated
   - If user says "$X each", unit_price = X. If user says "$X total for N", unit_price = X/N.
   - size: ONLY include if the user explicitly mentions one. Never "N/A", never "". Use null.
   - item_name: properly capitalized, full name as the user described it.
   - category: infer from item_name. Cards = trading cards, booster boxes, packs, slabs.
     Sneakers = athletic shoes. Clothing = shirts, jackets, pants, hats.
     Electronics = phones, consoles, etc. Other = anything else.

2. sell — user is recording a sale
   {{ "action": "sell", "item_name": "...", "quantity": 1, "total_price": 0.0,
     "sale_date": "YYYY-MM-DD" }}
   - total_price is always the TOTAL across all units, not per-unit.
     If user says "$X each", total_price = X * quantity.
   - item_name: MUST match an entry in the active inventory list above (canonical casing).
     If no clear match, return a "clarify" action.

3. rip — user is recording a damaged write-off
   {{ "action": "rip", "item_name": "...", "quantity": 1 }}
   - item_name: canonical match from active inventory.

4. expense — user is logging a non-inventory cost
   {{ "action": "expense", "amount": 0.0, "description": "...",
     "category": null | "...", "date": "YYYY-MM-DD" }}

5. edit — user wants to change a field on an existing entry
   {{ "action": "edit", "target": <int> | "last", "table": "inventory" | "expenses",
     "field": "<column_name>", "new_value": "<string>" }}
   - field must be one of: item_name, category, size, purchase_date, purchase_price,
     sale_date, sale_price, notes, status  (for inventory)
     OR: date, category, amount, description  (for expenses)

6. delete — user wants to remove a SINGLE entry
   {{ "action": "delete", "target": <int> | "last", "table": "inventory" | "expenses" }}

6b. clear — user wants to remove ALL entries at once
   {{ "action": "clear", "table": "inventory" | "expenses", "category": null | "<category>" }}
   - Use this (NOT "delete") whenever the user wants to wipe everything or everything
     in one category — e.g. "clear the inventory", "delete all my items",
     "remove everything", "start fresh", "these are all test products, delete them".
   - category: ONLY set if the user limits it (e.g. "clear all sneakers" -> "sneakers").
     Otherwise null (clears the whole table).
   - This is a single action — never break a "delete everything" request into multiple deletes
     and never ask the user to confirm how many; just return one "clear" action.

7. undo — user wants to reverse the last committed action
   {{ "action": "undo" }}

8. query — user is asking a read-only question
   {{ "action": "query", "query_type": "items" | "stale" | "unsold" | "recent" | "by_category" | "expenses_sum" | "profit" | "velocity" | "leaderboard" | "cashflow",
     "filters": {{ ... }} }}

   Filter keys by query_type:
   - "items"        : "category" (optional) — lists the actual item NAMES currently held.
                      Use this when the user wants to know WHAT items they have
                      (e.g. "what do I have", "list my items", "what cards do I have").
   - "stale"        : no filters needed
   - "unsold"       : "category" (optional), "min_days_held" (int, optional)
   - "recent"       : no filters needed
   - "by_category"  : no filters needed — only category TOTALS, not item names.
   - "expenses_sum" : "period" (optional)
   - "profit"       : "period" (optional), "category" (optional)
   - "velocity"     : "category" (optional)
   - "leaderboard"  : "direction" ("best" or "worst", default "best"), "category" (optional), "period" (optional)
   - "cashflow"     : "period" (optional)

   "period" values: "week" | "month" | "quarter" | "year" | "all"

9. clarify — the message is too ambiguous to parse confidently
   {{ "action": "clarify", "reason": "short message TO the user", "options": ["option 1", "option 2"] }}
   - "reason" must address the user directly in the second person ("you"), like a chat reply.
     CORRECT: "Which item did you mean?"  WRONG: "User wants to sell an item but..."
   - Never narrate about "the user" in the third person, and never describe your own reasoning.

RULES
- Return ONLY the JSON object, no prose, no markdown fences.
- Dates default to today if not stated.
- For sells/rips/edits, if multiple inventory items could match, return "clarify".
- To wipe everything (or a whole category), return ONE "clear" action — do not use "delete" or "clarify".
- If the message doesn't match any action, return "clarify"."""


# Pydantic models — extra="forbid" rejects unexpected keys from LLM output

class AddInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["add"]
    category: Literal["cards", "clothing", "sneakers", "electronics", "other"]
    item_name: str
    size: str | None = None
    quantity: int = 1
    unit_price: float
    purchase_date: str


class SellItems(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["sell"]
    item_name: str
    quantity: int
    total_price: float
    sale_date: str


class RipItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["rip"]
    item_name: str
    quantity: int = 1


class AddExpense(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["expense"]
    amount: float
    description: str
    category: str | None = None
    date: str


class EditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["edit"]
    target: int | Literal["last"]
    table: Literal["inventory", "expenses"] = "inventory"
    field: str
    new_value: str

    def model_post_init(self, __context):
        allowed = _EDITABLE_FIELDS.get(self.table, frozenset())
        if self.field not in allowed:
            raise ValueError(f"field {self.field!r} is not editable for table {self.table!r}")


class DeleteEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["delete"]
    target: int | Literal["last"]
    table: Literal["inventory", "expenses"] = "inventory"


class ClearInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["clear"]
    table: Literal["inventory", "expenses"] = "inventory"
    category: str | None = None


class UndoLast(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["undo"]


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["query"]
    query_type: Literal["items", "stale", "unsold", "recent", "by_category", "expenses_sum", "profit", "velocity", "leaderboard", "cashflow"]
    filters: dict = {}


class NeedsClarification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["clarify"]
    reason: str
    options: list[str] = []


ParsedAction = Union[
    AddInventory, SellItems, RipItem, AddExpense,
    EditEntry, DeleteEntry, ClearInventory, UndoLast, Query, NeedsClarification
]

_ACTION_MAP = {
    "add": AddInventory,
    "sell": SellItems,
    "rip": RipItem,
    "expense": AddExpense,
    "edit": EditEntry,
    "delete": DeleteEntry,
    "clear": ClearInventory,
    "undo": UndoLast,
    "query": Query,
    "clarify": NeedsClarification,
}

_INVENTORY_NAMES = frozenset  # populated at call time


def _sanitize_inventory_for_prompt(active_inventory: list[dict]) -> list[dict]:
    """Sanitize inventory for prompt injection prevention while preserving size for disambiguation."""
    safe = []
    for item in active_inventory:
        entry = {
            "item_name": str(item.get("item_name", ""))[:200],
            "category": str(item.get("category", ""))[:50],
            "count": int(item.get("count", 1)),
        }
        size = item.get("size")
        if size is not None:
            entry["size"] = str(size)[:20]
        safe.append(entry)
    return safe


def parse(user_message: str, active_inventory: list[dict]) -> ParsedAction:
    active_names = frozenset(item["item_name"] for item in active_inventory)

    today = today_iso()
    safe_inventory = _sanitize_inventory_for_prompt(active_inventory)
    system = SYSTEM_PROMPT_TEMPLATE.format(
        today_iso=today,
        today_display=iso_to_display(today),
        active_inventory_json=json.dumps(safe_inventory, indent=2) if safe_inventory else "[]",
    )

    # Boundary markers reduce prompt injection risk from the user message.
    bounded_message = f"[USER INPUT START]\n{user_message[:2000]}\n[USER INPUT END]"

    try:
        response = _client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": bounded_message}],
        )
    except Exception:
        raise

    if not response.content:
        return NeedsClarification(action="clarify", reason="No response from parser.")

    raw = response.content[0].text.strip()
    # Strip accidental markdown fences the model may add despite instructions.
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return NeedsClarification(action="clarify", reason="Couldn't parse response. Try rephrasing.")

    if not isinstance(data, dict):
        return NeedsClarification(action="clarify", reason="Unexpected parser response format.")

    action_key = data.get("action", "clarify")
    model_cls = _ACTION_MAP.get(action_key, NeedsClarification)

    try:
        parsed = model_cls(**data)
    except Exception:
        return NeedsClarification(action="clarify", reason="Couldn't understand that. Try rephrasing.")

    # App-layer cross-check: for sell/rip, verify item_name is in active inventory.
    # When inventory is empty, any sell/rip is invalid — there's nothing to sell.
    if isinstance(parsed, (SellItems, RipItem)):
        if not active_names:
            return NeedsClarification(
                action="clarify",
                reason="No active inventory to sell from.",
            )
        if parsed.item_name not in active_names:
            return NeedsClarification(
                action="clarify",
                reason=f"'{parsed.item_name}' didn't match any active inventory item.",
                options=sorted(active_names),
            )

    return parsed
