import os
from typing import Optional
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

def clean_env(val: Optional[str]) -> str:
    """Strips leading/trailing whitespace and optional wrapping quotes added in cloud dashboards."""
    if val is None:
        return ""
    s = str(val).strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    return s

# Configuration variables
TG_API_ID = clean_env(os.getenv("TG_API_ID"))
TG_API_HASH = clean_env(os.getenv("TG_API_HASH"))
TG_SESSION_STRING = clean_env(os.getenv("TG_SESSION_STRING") or os.getenv("SESSION_STRING"))
BOT_TOKEN = clean_env(os.getenv("BOT_TOKEN"))

raw_alert_channel = clean_env(os.getenv("ALERT_CHANNEL_ID", ""))
if raw_alert_channel in ("-1001234567890", "1234567890", "123456789", ""):
    ALERT_CHANNEL_ID = None
else:
    ALERT_CHANNEL_ID = raw_alert_channel

raw_admin_ids = clean_env(os.getenv("ADMIN_IDS", ""))
ADMIN_IDS = [int(x.strip()) for x in raw_admin_ids.split(",") if x.strip().isdigit()]

DB_PATH = clean_env(os.getenv("DB_PATH", "spydefi.db")) or "spydefi.db"
TRACK_INTERVAL = int(clean_env(os.getenv("TRACK_INTERVAL", "3")) or "3")

# MongoDB Atlas Configuration
MONGO_URI = clean_env(os.getenv("MONGO_URI") or os.getenv("MONGODB_URI") or "")
MONGO_DB_NAME = clean_env(os.getenv("MONGO_DB_NAME", "kolinsights")) or "kolinsights"

# Web Service & Render Environment Configuration
PORT = int(clean_env(os.getenv("PORT", "8080")) or "8080")
KEEP_ALIVE_URL = clean_env(os.getenv("KEEP_ALIVE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "")

ANIMATED_EYE_EMOJI_ID = os.getenv("ANIMATED_EYE_EMOJI_ID", "").strip()
ANIMATED_GRAPH_EMOJI_ID = os.getenv("ANIMATED_GRAPH_EMOJI_ID", "").strip()
DEPOSIT_WALLET_ADDRESS = os.getenv("DEPOSIT_WALLET_ADDRESS", "").strip()

SOLANA_EMOJI_ID = os.getenv("SOLANA_EMOJI_ID", "").strip()
ETHEREUM_EMOJI_ID = os.getenv("ETHEREUM_EMOJI_ID", "").strip()
BSC_EMOJI_ID = os.getenv("BSC_EMOJI_ID", "").strip()
BASE_EMOJI_ID = os.getenv("BASE_EMOJI_ID", "").strip()
ARBITRUM_EMOJI_ID = os.getenv("ARBITRUM_EMOJI_ID", "").strip()
TETHER_EMOJI_ID = os.getenv("TETHER_EMOJI_ID", "").strip()

TARGET_EMOJI_ID = os.getenv("TARGET_EMOJI_ID", "").strip()
COIN_EMOJI_ID = os.getenv("COIN_EMOJI_ID", "").strip()
MEGAPHONE_EMOJI_ID = os.getenv("MEGAPHONE_EMOJI_ID", "").strip()

# Multiplier milestones for generating achievement alerts (All integer steps 2.0-100.0, plus 500.0, 1000.0)
MILESTONE_THRESHOLDS = [
    2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0,
    21.0, 22.0, 23.0, 24.0, 25.0, 26.0, 27.0, 28.0, 29.0, 30.0,
    31.0, 32.0, 33.0, 34.0, 35.0, 36.0, 37.0, 38.0, 39.0, 40.0,
    41.0, 42.0, 43.0, 44.0, 45.0, 46.0, 47.0, 48.0, 49.0, 50.0,
    51.0, 52.0, 53.0, 54.0, 55.0, 56.0, 57.0, 58.0, 59.0, 60.0,
    61.0, 62.0, 63.0, 64.0, 65.0, 66.0, 67.0, 68.0, 69.0, 70.0,
    71.0, 72.0, 73.0, 74.0, 75.0, 76.0, 77.0, 78.0, 79.0, 80.0,
    81.0, 82.0, 83.0, 84.0, 85.0, 86.0, 87.0, 88.0, 89.0, 90.0,
    91.0, 92.0, 93.0, 94.0, 95.0, 96.0, 97.0, 98.0, 99.0, 100.0,
    500.0, 1000.0
]

async def get_effective_alert_channel_id() -> Optional[str]:
    """Returns alert channel from .env or dynamically configured DB value."""
    if ALERT_CHANNEL_ID:
        return ALERT_CHANNEL_ID
    from database import get_bot_config
    db_val = await get_bot_config("ALERT_CHANNEL_ID")
    return db_val if db_val else None

async def get_effective_deposit_wallet() -> str:
    """Returns deposit wallet address from DB active deposit wallet or .env value."""
    from database import get_active_deposit_wallet
    active_w = await get_active_deposit_wallet()
    if active_w:
        return active_w
    if DEPOSIT_WALLET_ADDRESS:
        return DEPOSIT_WALLET_ADDRESS
    return "0xb774E2edd9118274d71d97d9DA7216D8998c36C2"
