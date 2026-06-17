import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import discord
from discord.ext import tasks

import config
import db
import sheets
import digest
from parser import (
    parse, ParsedAction,
    AddInventory, SellItems, RipItem, AddExpense,
    EditEntry, DeleteEntry, ClearInventory, UndoLast, Query, NeedsClarification,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


@dataclass
class PendingConfirmation:
    action: ParsedAction
    user_id: int
    created_at: datetime
    prior_state: dict | None = None


@dataclass
class LastAction:
    action_type: str
    affected_ids: list[int]
    sale_group_id: int | None = None
    prior_state: dict | None = None


@dataclass
class PendingClarification:
    original_message: str
    options: list[str]
    created_at: datetime


pending: dict[int, PendingConfirmation] = {}
pending_clarification: dict[int, PendingClarification] = {}
query_messages: set[int] = set()
last_committed: LastAction | None = None

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
client = discord.Client(intents=intents)


# ── Formatting helpers ──────────────────────────────────────────────────────────

def fmt_price(val: float) -> str:
    return f"${val:.2f}"


def fmt_date(iso: str) -> str:
    try:
        return db.iso_to_display(iso)
    except (ValueError, TypeError):
        return iso or "unknown date"


def build_confirmation_summary(action: ParsedAction, prior_state: dict | None = None) -> str:
    if isinstance(action, AddInventory):
        size_str = f"Size: {action.size}" if action.size else "Size: (none)"
        total = action.unit_price * action.quantity
        price_str = (
            f"{fmt_price(action.unit_price)} each ({fmt_price(total)} total)"
            if action.quantity > 1
            else fmt_price(action.unit_price)
        )
        return (
            f"Adding to inventory:\n"
            f"• {action.item_name} ×{action.quantity}\n"
            f"• Category: {action.category}\n"
            f"• {size_str}\n"
            f"• {price_str} on {fmt_date(action.purchase_date)}\n\n"
            f"React {config.CONFIRM_EMOJI} to confirm or {config.CANCEL_EMOJI} to cancel."
        )

    elif isinstance(action, SellItems):
        per_unit = action.total_price / action.quantity
        price_str = (
            f"{fmt_price(per_unit)} each ({fmt_price(action.total_price)} total)"
            if action.quantity > 1
            else fmt_price(action.total_price)
        )
        pnl_line = ""
        try:
            fifo = db.get_fifo_preview(action.item_name, action.quantity)
            avg_cost = sum(r["purchase_price"] for r in fifo) / len(fifo)
            profit_per = per_unit - avg_cost
            roi_pct = (profit_per / avg_cost * 100) if avg_cost else 0.0
            sign = "+" if profit_per >= 0 else ""
            pnl_line = (
                f"\n• Avg cost {fmt_price(avg_cost)} → "
                f"{sign}{fmt_price(profit_per)}/unit ({sign}{roi_pct:.1f}% ROI)"
            )
        except Exception:
            pass
        return (
            f"Recording sale:\n"
            f"• {action.quantity}× {action.item_name} (oldest unsold)\n"
            f"• {price_str} on {fmt_date(action.sale_date)}"
            f"{pnl_line}\n\n"
            f"React {config.CONFIRM_EMOJI} to confirm or {config.CANCEL_EMOJI} to cancel."
        )

    elif isinstance(action, RipItem):
        return (
            f"Writing off as damaged:\n"
            f"• {action.quantity}× {action.item_name} (oldest active)\n\n"
            f"React {config.CONFIRM_EMOJI} to confirm or {config.CANCEL_EMOJI} to cancel."
        )

    elif isinstance(action, AddExpense):
        cat_str = f" [{action.category}]" if action.category else ""
        return (
            f"Logging expense:\n"
            f"• {fmt_price(action.amount)}{cat_str} — {action.description}\n"
            f"• Date: {fmt_date(action.date)}\n\n"
            f"React {config.CONFIRM_EMOJI} to confirm or {config.CANCEL_EMOJI} to cancel."
        )

    elif isinstance(action, EditEntry):
        old_val = prior_state.get("old_value", "?") if prior_state else "?"
        target_str = f"#{prior_state['id']}" if prior_state and "id" in prior_state else str(action.target)
        if action.field in ("purchase_price", "sale_price", "amount"):
            try:
                old_disp = fmt_price(float(old_val)) if old_val not in ("?", "None", None, "") else "?"
                new_disp = fmt_price(float(action.new_value.replace("$", "").replace(",", "")))
            except (ValueError, TypeError):
                old_disp = str(old_val)
                new_disp = action.new_value
        else:
            old_disp = old_val
            new_disp = action.new_value
        return (
            f"Editing entry {target_str}:\n"
            f"• {action.field}: {old_disp} → {new_disp}\n\n"
            f"React {config.CONFIRM_EMOJI} to confirm or {config.CANCEL_EMOJI} to cancel."
        )

    elif isinstance(action, DeleteEntry):
        target_str = "last entry" if action.target == "last" else f"entry #{action.target}"
        return (
            f"Deleting {target_str} from {action.table}.\n\n"
            f"React {config.CONFIRM_EMOJI} to confirm or {config.CANCEL_EMOJI} to cancel."
        )

    elif isinstance(action, ClearInventory):
        rows = db.get_clear_preview(action.table, action.category)
        count = len(rows)
        scope = f" {action.category}" if action.category else ""
        preview = "\n".join(f"• {r['item_name']}" for r in rows[:10])
        if count > 10:
            preview += f"\n…and {count - 10} more"
        if count == 0:
            return f"Nothing to clear — no active{scope} {action.table}."
        return (
            f"⚠️ Clearing ALL {count}{scope} {action.table} {'entry' if count == 1 else 'entries'}:\n"
            f"{preview}\n\n"
            f"This removes everything listed. You can undo it right after.\n"
            f"React {config.CONFIRM_EMOJI} to confirm or {config.CANCEL_EMOJI} to cancel."
        )

    elif isinstance(action, UndoLast):
        return (
            f"Undo last committed action?\n\n"
            f"React {config.CONFIRM_EMOJI} to confirm or {config.CANCEL_EMOJI} to cancel."
        )

    return "Unknown action. React ✅ to confirm or ❌ to cancel."


def format_query_results(action: Query) -> str:
    qt = action.query_type
    filters = action.filters

    if qt == "stale":
        items = db.get_stale_items(config.STALE_DAYS)
        if not items:
            return "No stale inventory right now."
        lines = [f"Stale inventory (top {min(len(items), 20)} by age):"]
        for item in items[:20]:
            size_str = f" (size {item['size']})" if item["size"] else ""
            lines.append(f"{item['days_held']}d — #{item['id']} {item['item_name']}{size_str} — bought {fmt_price(item['purchase_price'])}")
        if len(items) > 20:
            lines.append(f"...and {len(items) - 20} more — see the sheet")
        return "```\n" + "\n".join(lines) + "\n```"

    elif qt == "unsold":
        category = filters.get("category")
        min_days = filters.get("min_days_held")
        items = db.get_unsold_items(category=category, min_days_held=min_days)
        if not items:
            return "No unsold inventory matching your criteria."
        lines = ["Unsold inventory:"]
        for item in items[:20]:
            size_str = f" (size {item['size']})" if item["size"] else ""
            lines.append(f"#{item['id']} {item['item_name']}{size_str} [{item['category']}] — {fmt_price(item['purchase_price'])} on {fmt_date(item['purchase_date'])} ({item['days_held']}d)")
        if len(items) > 20:
            lines.append(f"...and {len(items) - 20} more — see the sheet")
        return "```\n" + "\n".join(lines) + "\n```"

    elif qt == "recent":
        items = db.get_recent_items(limit=10)
        if not items:
            return "No recent entries."
        lines = ["Recent entries:"]
        for item in items:
            sale_str = f" → sold {fmt_price(item['sale_price'])}" if item["status"] == "sold" and item["sale_price"] else ""
            lines.append(f"#{item['id']} [{item['status']}] {item['item_name']} — {fmt_price(item['purchase_price'])}{sale_str} ({item['purchase_date']})")
        return "```\n" + "\n".join(lines) + "\n```"

    elif qt == "by_category":
        items = db.get_unsold_items()
        if not items:
            return "No active inventory."
        counts: dict[str, int] = {}
        totals: dict[str, float] = {}
        for it in items:
            c = it["category"]
            counts[c] = counts.get(c, 0) + 1
            totals[c] = totals.get(c, 0.0) + it["purchase_price"]
        lines = ["Active inventory by category:"]
        for c in sorted(counts):
            lines.append(f"  {c:<12} {counts[c]:>3} units — {fmt_price(totals[c])} invested")
        return "```\n" + "\n".join(lines) + "\n```"

    elif qt == "expenses_sum":
        period = filters.get("period")
        period_sql, params = db._period_clause(period, "date")
        with db.get_conn() as conn:
            row = conn.execute(
                f"SELECT COALESCE(SUM(amount),0) AS total, COUNT(*) AS cnt "
                f"FROM expenses WHERE deleted_at IS NULL {period_sql}",
                params,
            ).fetchone()
        period_label = f" ({period})" if period and period != "all" else ""
        return f"Expenses{period_label}: {fmt_price(row['total'])} across {row['cnt']} entries."

    elif qt == "profit":
        period = filters.get("period")
        category = filters.get("category")
        data = db.get_profit_summary(period=period, category=category)
        period_label = f" ({period})" if period and period != "all" else ""
        cat_label = f" [{category}]" if category else ""
        lines = [f"Profit summary{period_label}{cat_label}:"]
        lines.append(f"  Revenue:       {fmt_price(data['revenue'])}  ({data['units_sold']} units sold)")
        lines.append(f"  COGS:        − {fmt_price(data['cogs'])}")
        lines.append(f"  Gross profit:  {fmt_price(data['gross_profit'])}")
        lines.append(f"  Expenses:    − {fmt_price(data['expenses'])}")
        lines.append(f"  Net profit:    {fmt_price(data['net_profit'])}")
        if data["by_category"]:
            lines.append("")
            lines.append("  By category:")
            for cat in data["by_category"]:
                gp = cat["gross_profit"]
                sign = "+" if gp >= 0 else ""
                lines.append(f"    {cat['category']:<12} {sign}{fmt_price(gp)}  ({cat['units_sold']} sold)")
        return "```\n" + "\n".join(lines) + "\n```"

    elif qt == "velocity":
        category = filters.get("category")
        data = db.get_velocity_stats(category=category)
        cat_label = f" [{category}]" if category else ""
        lines = [f"Sell velocity{cat_label}:"]
        if not data["categories"]:
            lines.append("  No sold items to analyze yet.")
        else:
            for cat in data["categories"]:
                lines.append(
                    f"  {cat['category']:<12}  avg {cat['avg_days']}d  "
                    f"median {cat['median_days']}d  ({cat['units_sold']} sold)"
                )
                if cat["fastest_item"]:
                    lines.append(f"    ↑ fastest: {cat['fastest_item']} ({cat['fastest_days']}d)")
                if cat["slowest_item"] and cat["slowest_item"] != cat["fastest_item"]:
                    lines.append(f"    ↓ slowest: {cat['slowest_item']} ({cat['slowest_days']}d)")
        if data["slow_current"]:
            lines.append("")
            lines.append("  Above-average holds right now:")
            for it in data["slow_current"][:10]:
                size_s = f" sz{it['size']}" if it["size"] else ""
                lines.append(
                    f"    #{it['id']} {it['item_name']}{size_s} — "
                    f"{it['days_held']}d (avg {it['category_avg_days']}d, +{it['days_over_avg']:.0f}d)"
                )
        return "```\n" + "\n".join(lines) + "\n```"

    elif qt == "leaderboard":
        direction = filters.get("direction", "best")
        category = filters.get("category")
        period = filters.get("period")
        rows = db.get_roi_leaderboard(direction=direction, category=category, period=period)
        label = "Best" if direction == "best" else "Worst"
        period_label = f" ({period})" if period and period != "all" else ""
        cat_label = f" [{category}]" if category else ""
        lines = [f"{label} flips by ROI{period_label}{cat_label}:"]
        if not rows:
            lines.append("  No sold items match.")
        else:
            for r in rows:
                sign = "+" if r["roi_pct"] >= 0 else ""
                lines.append(
                    f"  #{r['id']} {r['item_name']} [{r['category']}]  "
                    f"{fmt_price(r['purchase_price'])} → {fmt_price(r['sale_price'])}  "
                    f"{sign}{fmt_price(r['profit'])} ({sign}{r['roi_pct']}%)  {r['days_held']}d"
                )
        return "```\n" + "\n".join(lines) + "\n```"

    elif qt == "cashflow":
        period = filters.get("period")
        data = db.get_cash_flow(period=period)
        period_label = f" ({period})" if period and period != "all" else ""
        lines = [f"Cash flow{period_label}:"]
        lines.append(f"  Cash in  (sales):      {fmt_price(data['cash_in'])}")
        lines.append(f"  Cash out (purchases): − {fmt_price(data['purchases'])}")
        lines.append(f"  Cash out (expenses):  − {fmt_price(data['expenses'])}")
        lines.append(f"  ─────────────────────────────")
        lines.append(f"  Net cash:              {fmt_price(data['net_cash'])}")
        if data["capital_locked_by_category"]:
            lines.append(f"")
            lines.append(f"  Capital locked in unsold inventory: {fmt_price(data['capital_locked_total'])}")
            for cat in data["capital_locked_by_category"]:
                lines.append(f"    {cat['category']:<12} {cat['units']:>3} units — {fmt_price(cat['capital'])}")
        return "```\n" + "\n".join(lines) + "\n```"

    else:
        return f"`{qt}` is not yet implemented."


# ── Event handlers ──────────────────────────────────────────────────────────────

@client.event
async def on_ready():
    log.info(f"Logged in as {client.user}")
    db.init_db()
    cleanup_pending.start()
    daily_digest.start()


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.author.id != config.DISCORD_USER_ID:
        return
    if message.channel.id != config.DISCORD_CHANNEL_ID:
        return

    # Resolve a pending clarification if the user replied with a number.
    user_message = message.content
    pc = pending_clarification.pop(message.author.id, None)
    if pc:
        stripped = message.content.strip()
        if stripped.isdigit():
            choice = int(stripped)
            if 1 <= choice <= len(pc.options):
                selected = pc.options[choice - 1]
                user_message = f"{pc.original_message} (specifically: {selected})"
            else:
                pending_clarification[message.author.id] = pc
                await message.reply(f"Please pick a number between 1 and {len(pc.options)}.")
                return
        # Non-digit reply: treat as a fresh message (pc already discarded).

    active_inventory = db.get_active_inventory_summary()

    try:
        action = parse(user_message, active_inventory)
    except Exception:
        log.exception("Parser error")
        await message.reply("Couldn't reach the parser. Try again in a sec.")
        return

    if isinstance(action, Query):
        result = format_query_results(action)
        bot_msg = await message.reply(result)
        await bot_msg.add_reaction(config.CONFIRM_EMOJI)
        query_messages.add(bot_msg.id)
        return

    if isinstance(action, NeedsClarification):
        reply = action.reason
        if action.options:
            numbered = "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(action.options))
            reply += f"\n{numbered}"
            pending_clarification[message.author.id] = PendingClarification(
                original_message=user_message,
                options=action.options,
                created_at=datetime.now(),
            )
        await message.reply(reply)
        return

    prior_state: dict = {}
    if isinstance(action, EditEntry):
        old_val = db.get_edit_old_value(action)
        resolved_id = db.get_entry_id(action)
        if old_val is not None and resolved_id is not None:
            prior_state = {
                "id": resolved_id,
                "table": action.table,
                "field": action.field,
                "old_value": old_val,
            }
    elif isinstance(action, DeleteEntry):
        prior_state = db.record_delete_table(action)
    elif isinstance(action, ClearInventory):
        prior_state = {"table": action.table}

    try:
        summary = build_confirmation_summary(action, prior_state if prior_state else None)
    except Exception:
        log.exception("Failed to build confirmation summary")
        await message.reply("Couldn't format that action. Try rephrasing.")
        return
    bot_msg = await message.reply(summary)
    await bot_msg.add_reaction(config.CONFIRM_EMOJI)
    await bot_msg.add_reaction(config.CANCEL_EMOJI)

    pending[bot_msg.id] = PendingConfirmation(
        action=action,
        user_id=message.author.id,
        created_at=datetime.now(),
        prior_state=prior_state if prior_state else None,
    )


@client.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    global last_committed

    if user.bot:
        return
    if user.id != config.DISCORD_USER_ID:
        return
    if reaction.message.channel.id != config.DISCORD_CHANNEL_ID:
        return

    # Delete query result messages when the user reacts with ✅.
    if reaction.message.id in query_messages:
        if str(reaction.emoji) == config.CONFIRM_EMOJI:
            query_messages.discard(reaction.message.id)
            try:
                await reaction.message.delete()
            except discord.HTTPException:
                pass
        return

    # Read-then-pop: check ownership before removing from pending,
    # so an unauthorized reactor doesn't evict the entry before the owner reacts.
    confirmation = pending.get(reaction.message.id)
    if confirmation is None:
        return
    if confirmation.user_id != user.id:
        return
    pending.pop(reaction.message.id, None)

    action = confirmation.action
    emoji = str(reaction.emoji)
    channel = reaction.message.channel

    if emoji == config.CONFIRM_EMOJI:
        try:
            if isinstance(action, UndoLast):
                if last_committed is None:
                    await reaction.message.reply("Nothing to undo.")
                    return
                db.undo_action(last_committed.action_type, last_committed.affected_ids, last_committed.prior_state)
                notice = f"Undid last action ({last_committed.action_type})."
                last_committed = None
                await reaction.message.delete()
                await channel.send(notice)
            else:
                prior_stash: dict = {}
                if confirmation.prior_state:
                    prior_stash = confirmation.prior_state.copy()

                affected_ids, sale_group_id = db.commit_action(action, prior_stash if prior_stash else None)
                last_committed = LastAction(
                    action_type=action.action,
                    affected_ids=affected_ids,
                    sale_group_id=sale_group_id,
                    prior_state=prior_stash if prior_stash else None,
                )

                notice = _success_message(action, affected_ids, sale_group_id)
                await reaction.message.delete()
                await channel.send(notice)

                try:
                    sheets.sync()
                except Exception:
                    log.exception("Sheets sync failed")
                    await channel.send("⚠️ Sheets sync failed — check logs. SQLite is up to date.")

        except ValueError as e:
            await reaction.message.reply(str(e))
        except Exception:
            log.exception("Commit error")
            await reaction.message.reply("Something went wrong — check logs.")

    elif emoji == config.CANCEL_EMOJI:
        try:
            await reaction.message.delete()
        except discord.HTTPException:
            pass


def _success_message(action: ParsedAction, affected_ids: list[int], sale_group_id: int | None = None) -> str:
    if isinstance(action, AddInventory):
        if len(affected_ids) == 1:
            return f"Added entry #{affected_ids[0]}."
        return f"Added entries #{affected_ids[0]}–#{affected_ids[-1]}."

    elif isinstance(action, SellItems):
        id_str = ", ".join(f"#{i}" for i in affected_ids)
        group_str = f" Sale group #{sale_group_id}." if sale_group_id is not None else ""
        return f"Marked {id_str} as sold.{group_str}"

    elif isinstance(action, RipItem):
        id_str = ", ".join(f"#{i}" for i in affected_ids)
        return f"Marked {id_str} as ripped."

    elif isinstance(action, AddExpense):
        return f"Logged expense #{affected_ids[0]}."

    elif isinstance(action, EditEntry):
        return f"Updated entry #{affected_ids[0]}."

    elif isinstance(action, DeleteEntry):
        return f"Deleted entry #{affected_ids[0]}."

    elif isinstance(action, ClearInventory):
        scope = f" {action.category}" if action.category else ""
        n = len(affected_ids)
        return f"Cleared {n}{scope} {action.table} {'entry' if n == 1 else 'entries'}. Say \"undo\" to restore."

    return "Done."


# ── Background tasks ────────────────────────────────────────────────────────────

@tasks.loop(minutes=10)
async def cleanup_pending():
    now = datetime.now()
    timeout = timedelta(minutes=config.CONFIRMATION_TIMEOUT_MINUTES)
    expired = [msg_id for msg_id, pc in list(pending.items()) if now - pc.created_at > timeout]
    for msg_id in expired:
        pending.pop(msg_id, None)
        log.info(f"Expired pending confirmation for message {msg_id}")
    clarification_timeout = timedelta(minutes=5)
    expired_cl = [uid for uid, cl in list(pending_clarification.items()) if now - cl.created_at > clarification_timeout]
    for uid in expired_cl:
        pending_clarification.pop(uid, None)
        log.info(f"Expired pending clarification for user {uid}")


@tasks.loop(hours=1)
async def daily_digest():
    if not digest.should_run_digest():
        return
    digest.mark_digest_ran()
    msg = digest.build_digest_message()
    if msg is None:
        return
    channel = client.get_channel(config.DISCORD_CHANNEL_ID)
    if channel:
        await channel.send(msg)


if __name__ == "__main__":
    client.run(config.DISCORD_TOKEN)
