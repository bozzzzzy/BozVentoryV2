# Inventory Bot — Setup Guide

This guide walks you through every step needed to get the bot running. No technical experience required. Each section tells you exactly where to go, what to click, and where to paste what you find.

**Total time: about 30–45 minutes.**

---

## What you're setting up

The bot needs four things to work:

| What | Why |
|------|-----|
| **Discord Bot Token** | Lets your bot connect to Discord |
| **Anthropic API Key** | Powers the plain-English parsing (Claude AI) |
| **Google Sheets credentials** | Mirrors your inventory to a spreadsheet |
| **Your Discord IDs** | Tells the bot which channel and user to listen to |

---

## Step 1 — Enable Developer Mode in Discord

You need this to get your personal IDs later.

1. Open Discord on your computer.
2. Click the **gear icon** (⚙️) at the bottom-left next to your username.
3. In the left sidebar, scroll down and click **"Advanced"**.
4. Turn on **"Developer Mode"** (the toggle turns blue).
5. Close Settings (press Escape).

---

## Step 2 — Create Your Discord Bot

1. Go to **https://discord.com/developers/applications** in your browser.
2. Log in with your Discord account if prompted.
3. Click the blue **"New Application"** button (top right).
4. Type a name for your bot (e.g., `Inventory Bot`) and click **"Create"**.

**Get your Bot Token:**

5. In the left sidebar, click **"Bot"**.
6. Scroll down to the **"Privileged Gateway Intents"** section.
7. Turn on **"Message Content Intent"** (toggle turns green). This lets the bot read your messages.
8. Click **"Save Changes"**.
9. Scroll back up and click **"Reset Token"**, then confirm. A token will appear.
10. Click **"Copy"** — this is your `DISCORD_TOKEN`. **Save it somewhere safe — you can't see it again.**

**Invite the bot to your server:**

11. In the left sidebar, click **"OAuth2"**, then click **"URL Generator"** underneath it.
12. Under **"Scopes"**, check the box next to **"bot"**.
13. Under **"Bot Permissions"** (appears below), check these boxes:
    - Send Messages
    - Read Messages / View Channels
    - Read Message History
    - Add Reactions
14. Scroll to the bottom and copy the **"Generated URL"**.
15. Paste that URL into a new browser tab and press Enter.
16. Choose your server from the dropdown and click **"Authorize"**.

**Get your Channel ID:**

17. In Discord, navigate to the channel where you want the bot to live (e.g., `#inventory`).
18. Right-click on the channel name in the left sidebar.
19. Click **"Copy Channel ID"**.
20. Paste it somewhere — this is your `DISCORD_CHANNEL_ID`.

**Get your User ID:**

21. In Discord, find your own username anywhere (e.g., in a message you sent).
22. Right-click your username.
23. Click **"Copy User ID"**.
24. Paste it somewhere — this is your `DISCORD_USER_ID`.

---

## Step 3 — Get Your Anthropic API Key

1. Go to **https://console.anthropic.com** in your browser.
2. Sign up or log in.
3. Click **"API Keys"** in the left sidebar.
4. Click **"Create Key"**, give it a name (e.g., `inventory-bot`), and click **"Create Key"**.
5. Copy the key that appears — this is your `ANTHROPIC_API_KEY`. **Save it — it won't be shown again.**

> **Tip:** While you're here, click **"Plans & Billing"** and set a monthly spend limit of $5–$10 as a safety net. The bot uses a small model and will cost pennies per month under normal use.

---

## Step 4 — Set Up Google Sheets

This is the most involved section. Take it one step at a time.

### 4a — Create a Google Sheet

1. Go to **https://sheets.google.com** and sign in with your Google account.
2. Click the **"+"** button to create a blank spreadsheet.
3. Name it something like `Inventory Bot` (click "Untitled spreadsheet" at the top to rename).
4. Look at the URL in your browser address bar. It looks like:
   ```
   https://docs.google.com/spreadsheets/d/XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX/edit
   ```
5. Copy the long string of letters and numbers between `/d/` and `/edit`. That is your `GOOGLE_SHEETS_ID`.

### 4b — Set Up Google Cloud (one-time)

1. Go to **https://console.cloud.google.com** and sign in with the same Google account.
2. At the top of the page, click **"Select a project"**, then click **"New Project"**.
3. Name it `inventory-bot` and click **"Create"**.
4. Make sure your new project is selected in the dropdown at the top.

### 4c — Enable the Google Sheets API

1. In the search bar at the top of Google Cloud Console, type **"Google Sheets API"** and press Enter.
2. Click the **"Google Sheets API"** result.
3. Click the blue **"Enable"** button. Wait a moment for it to activate.

### 4d — Create a Service Account

1. In the Google Cloud Console, click the **hamburger menu** (☰ three horizontal lines) at the top left.
2. Hover over **"IAM & Admin"**, then click **"Service Accounts"**.
3. Click **"+ Create Service Account"** at the top.
4. Fill in:
   - **Service account name:** `inventory-bot`
   - **Service account ID:** will auto-fill as `inventory-bot`
5. Click **"Create and Continue"**.
6. On the next screen (Grant access), skip it — click **"Continue"** without selecting a role.
7. Click **"Done"**.

### 4e — Download the Credentials File

1. You should now see your new service account listed. Click on its email address (looks like `inventory-bot@your-project.iam.gserviceaccount.com`).
2. Click the **"Keys"** tab.
3. Click **"Add Key"** → **"Create new key"**.
4. Select **"JSON"** and click **"Create"**.
5. A file will download automatically — it's called something like `your-project-XXXXXXXXXX.json`.
6. **Rename that file to `creds.json`.**
7. Move `creds.json` into the `inventory-bot` folder (the same folder as `main.py`).

### 4f — Share Your Sheet with the Bot

1. Open the `creds.json` file with a text editor (Notepad on Windows, TextEdit on Mac).
2. Find the line that says `"client_email"` — it will look like:
   ```
   "client_email": "inventory-bot@your-project.iam.gserviceaccount.com"
   ```
3. Copy that email address (just the email, not the quotes).
4. Go back to your Google Sheet.
5. Click the green **"Share"** button in the top right.
6. Paste the service account email into the field.
7. Change the permission from **"Viewer"** to **"Editor"**.
8. Uncheck **"Notify people"** (the bot doesn't need an email).
9. Click **"Share"**.

---

## Step 5 — Fill In Your `.env` File

1. In the `inventory-bot` folder, find the file called **`.env.example`**.
2. Make a copy of it and rename the copy to **`.env`** (just `.env`, no `.example`).

   > On Mac: the file may be hidden. In Finder, press **Cmd + Shift + .** to show hidden files.
   > On Windows: in File Explorer, click **View → Show → Hidden items**.

3. Open `.env` in a text editor. It will look like this:

   ```
   DISCORD_TOKEN=...
   ANTHROPIC_API_KEY=...
   DISCORD_USER_ID=...
   DISCORD_CHANNEL_ID=...
   GOOGLE_SHEETS_ID=...
   GOOGLE_SERVICE_ACCOUNT_PATH=./creds.json
   DIGEST_HOUR=9
   STALE_DAYS=30
   CONFIRMATION_TIMEOUT_MINUTES=60
   ```

4. Replace each `...` with the values you collected:

   ```
   DISCORD_TOKEN=paste-your-discord-token-here
   ANTHROPIC_API_KEY=paste-your-anthropic-key-here
   DISCORD_USER_ID=paste-your-user-id-here
   DISCORD_CHANNEL_ID=paste-your-channel-id-here
   GOOGLE_SHEETS_ID=paste-your-sheets-id-here
   GOOGLE_SERVICE_ACCOUNT_PATH=./creds.json
   DIGEST_HOUR=9
   STALE_DAYS=30
   CONFIRMATION_TIMEOUT_MINUTES=60
   ```

   **Do not add quotes around the values. Do not add spaces around the `=`.**

5. Save the file.

---

## Step 6 — Install and Run the Bot

Open a **Terminal** (Mac) or **Command Prompt** (Windows) and run these commands one at a time. Copy and paste each line exactly.

**Navigate to the bot folder:**
```
cd ~/inventory-bot
```
(If you put the folder somewhere else, adjust the path accordingly.)

**Create a Python environment:**
```
python3 -m venv venv
```

**Activate it:**
- Mac/Linux:
  ```
  source venv/bin/activate
  ```
- Windows:
  ```
  venv\Scripts\activate
  ```

**Install dependencies:**
```
pip install -r requirements.txt
```

**Start the bot:**
```
python main.py
```

You should see a message like:
```
Logged in as Inventory Bot#1234
```

The bot is now live. Leave this terminal window open — closing it stops the bot.

---

## Step 7 — Test It

In the Discord channel you configured, type:

```
add Prismatic Booster Bundle x2 bought for $29.99 each today
```

The bot should reply with a confirmation summary. React with ✅ to confirm. After confirming, open your Google Sheet — the entry should appear there automatically.

---

## Quick Reference — Where Each Value Comes From

| Variable | Where to find it |
|----------|-----------------|
| `DISCORD_TOKEN` | Discord Developer Portal → Your App → Bot → Reset Token |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys |
| `DISCORD_USER_ID` | Discord → Right-click your username → Copy User ID |
| `DISCORD_CHANNEL_ID` | Discord → Right-click channel name → Copy Channel ID |
| `GOOGLE_SHEETS_ID` | Sheet URL between `/d/` and `/edit` |
| `GOOGLE_SERVICE_ACCOUNT_PATH` | Always `./creds.json` (don't change this) |
| `DIGEST_HOUR` | Hour of day (0–23) for the daily stale-items alert. Default: `9` (9 AM) |
| `STALE_DAYS` | How many days before an item is flagged as stale. Default: `30` |

---

## Starting the Bot (After Initial Setup)

Once you've completed setup, this is all you need to do each time you want to run the bot.

Open a **Terminal** (Mac) or **Command Prompt** (Windows) and run these three commands:

```
cd ~/inventory-bot
source venv/bin/activate
python main.py
```

> **Windows users:** replace the second line with `venv\Scripts\activate`

You should see:
```
Logged in as Inventory Bot#1234
```

The bot is now live. **Leave this terminal window open** — closing it stops the bot.

To stop the bot, press `Ctrl+C` in the terminal.

---

## Troubleshooting

**"DISCORD_TOKEN is missing or empty"**
→ Check your `.env` file. Make sure there are no spaces around the `=` and no quotes around the value.

**Bot is online but doesn't respond to messages**
→ Make sure you typed in the exact channel that matches `DISCORD_CHANNEL_ID`, and that your Discord user ID in `DISCORD_USER_ID` matches the account you're typing from.

**"Sheets sync failed"**
→ Double-check that you shared the Google Sheet with the service account email from `creds.json`, and that you gave it **Editor** (not Viewer) access.

**Bot goes offline when I close the terminal**
→ That's normal. To keep it running 24/7, you'd need to host it on a small server (like a Raspberry Pi or a $5/month VPS). For now, just leave the terminal open while you're using it.

**"No module named discord" or similar**
→ Make sure you activated the virtual environment first (`source venv/bin/activate` on Mac, `venv\Scripts\activate` on Windows) before running `python main.py`.
