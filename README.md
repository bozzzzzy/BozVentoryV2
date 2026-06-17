# Discord Inventory Bot

Track reselling inventory (cards, clothing, sneakers, electronics) by typing plain English in a Discord channel. The bot parses messages with Claude Haiku, confirms via reaction, commits to SQLite, and mirrors to Google Sheets.

## Setup

See the **Setup checklist** in `inventory_bot_spec.md` on your Desktop, or follow these steps:

1. **Discord** — create a bot, enable Message Content Intent, copy the token.
2. **Anthropic** — create an API key at console.anthropic.com.
3. **Google Sheets** — create a sheet, create a service account, download the JSON key, share the sheet with the service account as Editor.
4. **Copy config**: `cp .env.example .env` then fill in all values.
5. **Install**: `python -m venv venv && source venv/bin/activate && pip install -r requirements.txt`
6. **Run**: `python main.py`

## Usage

Type plain English in your configured Discord channel:

| Intent | Examples |
|--------|---------|
| Add inventory | `add Prismatic Booster Bundle x2 bought for $29.99 each today` |
| Record sale | `sold 2 prismatic booster bundles for $80` |
| Write off | `the blue jacket ripped` |
| Log expense | `$15 in shipping supplies` |
| Edit entry | `fix the last entry's price to $34.99` |
| Delete entry | `delete entry 47` |
| Undo | `undo last` |
| Query | `what's been sitting longest` / `show unsold cards` / `recent` |

React ✅ to confirm or ❌ to cancel any action. Queries reply immediately without confirmation.
