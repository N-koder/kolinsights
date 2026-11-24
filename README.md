# 🚀 KOLInsights / Spidyfi Alpha Tracker

A high-performance crypto KOL (Key Opinion Leader) and Telegram alpha channel scraper & price tracking bot. Built with **Telethon (MTProto)**, **python-telegram-bot**, **DexScreener API**, **GeckoTerminal API**, and **Pillow**.

---

## 🌟 Key Features

- **⚡ High-Frequency Call Scraping**: Scrapes contract addresses (Solana & EVM) directly from Telegram alpha channels in real-time.
- **📊 Real-Time Multi-Chain Price Tracking**: Dual-lane price tracking engine with sub-second polling for active calls and automatic ATH milestone calculations.
- **🎯 Smart Alert Layout**:
  - Clean card design with tap-to-copy contract address.
  - **🚀 Buy on Maestro**: 1-click direct trade via Maestro Sniper Bot.
  - **📊 Smart DEX / GMGN Button**: Automatically serves DexScreener if pair is indexed; otherwise falls back to GMGN (instant block 0 indexing) or Explorer.
  - Deep links for original message and caller analytics.
- **🎨 Visual Milestone Cards**: Generates 3D glassmorphic achievement cards (`x2`, `x3`, `x5`, `x10`, `x50`, `x100`) via Pillow.
- **🌐 Render Cloud Ready**:
  - Embedded HTTP web server listening on `0.0.0.0:$PORT` with `/health` endpoint for Render health checks.
  - 24/7 keep-alive worker to keep free-tier instances awake.
  - Headless Telethon Userbot support via `TG_SESSION_STRING`.
- **⚡ SQLite WAL Engine**: Optimized with Write-Ahead Logging (`WAL`), 30s busy timeout, and query performance indexes.

---

## 📁 Project Structure

```
├── main.py              # Main bot runner, alert dispatchers, and background tasks
├── server.py            # Embedded Render HTTP web server (/ and /health)
├── scraper.py           # Telethon MTProto userbot scraper & contract extractor
├── tracker.py           # Dual-lane price & ATH milestone tracker
├── dex_api.py           # High-speed connection-pooled DEX & GeckoTerminal client
├── database.py          # SQLite database schema, queries, and index management
├── bot_handlers.py      # Interactive Telegram bot commands & UI callbacks
├── graphics.py          # Pillow visual achievement card generator
├── config.py            # Centralized settings and environment variables
├── login_userbot.py     # Local interactive login tool to generate TG_SESSION_STRING
├── Procfile             # Process file for Render web service
├── render.yaml          # Infrastructure as Code deployment blueprint
├── requirements.txt     # Python dependencies
└── .env.example         # Environment template
```

---

## 🚀 Quick Setup & Local Run

### 1. Clone & Install Dependencies
```bash
git clone https://github.com/N-koder/kolinsights.git
cd kolinsights
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Fill in your credentials:
- `TG_API_ID` & `TG_API_HASH` (from [my.telegram.org](https://my.telegram.org))
- `BOT_TOKEN` (from [@BotFather](https://t.me/BotFather))
- `ADMIN_IDS` (comma-separated admin Telegram IDs)
- `ALERT_CHANNEL_ID` (channel ID where alerts are dispatched)

### 3. Generate Telethon Session String (Required for Cloud/Render)
Run the login helper locally once:
```bash
python login_userbot.py
```
Copy the printed `TG_SESSION_STRING` and set it in your `.env`.

### 4. Start the Bot
```bash
python main.py
```

---

## ☁️ Deploying on Render (Free Tier)

1. **Create a New Web Service** on [Render Dashboard](https://dashboard.render.com).
2. Connect this repository (`N-koder/kolinsights`).
3. Set the following settings:
   - **Environment:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`
   - **Health Check Path:** `/health`
4. Set Environment Variables:
   - `TG_API_ID`
   - `TG_API_HASH`
   - `TG_SESSION_STRING`
   - `BOT_TOKEN`
   - `ADMIN_IDS`
   - `PORT`: `10000`
   - `DB_PATH`: `spydefi.db`
   - `KEEP_ALIVE_URL`: `https://<your-render-subdomain>.onrender.com`

---

## 📜 License
MIT License.
