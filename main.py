import os
import io
import time
import asyncio
import logging
from typing import Optional
from telegram.ext import ApplicationBuilder
from telegram import InputFile
from telethon.tl.types import DocumentAttributeAnimated, DocumentAttributeVideo

from config import BOT_TOKEN, ALERT_CHANNEL_ID, ADMIN_IDS
from database import init_db
from bot_handlers import register_handlers
from tracker import PriceTracker
from scraper import CallScraper

# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("alpharadar_main")

_global_bot_app = None
_global_scraper = None

_sent_milestone_alerts = set()
_sent_new_call_alerts = set()
_sent_new_call_keys = set()
_telethon_media_cache = {}
_media_ram_cache = {}

def _trim_set(s: set, max_size: int = 2000):
    """Keep in-memory deduplication sets bounded to prevent unbounded memory growth."""
    if len(s) > max_size:
        excess = len(s) - max_size
        for _ in range(excess):
            try:
                s.pop()
            except KeyError:
                break

def _trim_dict(d: dict, max_size: int = 20):
    """Keep media byte caches bounded to avoid memory bloating in restricted environments."""
    if len(d) > max_size:
        keys_to_remove = list(d.keys())[:len(d) - max_size]
        for k in keys_to_remove:
            d.pop(k, None)

import io

async def resolve_media_buffer(custom_media: Optional[dict], image_bytes: Optional[bytes], bot_app=None) -> Optional[io.BytesIO]:
    """Downloads bot_app file_id to memory buffer or wraps raw image bytes for Telethon compatibility with 0ms RAM caching."""
    global _global_bot_app
    app = bot_app or _global_bot_app
    if custom_media and custom_media.get("file_id"):
        file_id = custom_media["file_id"]
        is_gif = custom_media.get("media_type") == "animation"
        ext = ".mp4" if is_gif else ".jpg"

        # 0. Instant RAM Cache hit (0ms latency!)
        if file_id in _media_ram_cache:
            buf = io.BytesIO(_media_ram_cache[file_id])
            buf.seek(0)
            buf.name = f"animation{ext}" if is_gif else "photo.jpg"
            return buf

        # 1. Try downloading via python-telegram-bot Application if available
        if app and getattr(app, "bot", None):
            try:
                file_obj = await app.bot.get_file(file_id)
                buf = io.BytesIO()
                await file_obj.download_to_memory(buf)
                raw_bytes = buf.getvalue()
                if raw_bytes:
                    _media_ram_cache[file_id] = raw_bytes
                    _trim_dict(_media_ram_cache, max_size=20)
                buf.seek(0)
                buf.name = f"animation{ext}" if is_gif else "photo.jpg"
                return buf
            except Exception as err:
                logger.debug(f"ptb get_file download notice: {err}")

        # 2. Universal HTTP Direct Fallback using BOT_TOKEN
        if BOT_TOKEN:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=15.0) as http_client:
                    resp = await http_client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile", params={"file_id": file_id})
                    if resp.status_code == 200:
                        file_path = resp.json().get("result", {}).get("file_path")
                        if file_path:
                            file_dl = await http_client.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}")
                            if file_dl.status_code == 200:
                                raw_bytes = file_dl.content
                                if raw_bytes:
                                    _media_ram_cache[file_id] = raw_bytes
                                buf = io.BytesIO(raw_bytes)
                                buf.seek(0)
                                buf.name = f"animation{ext}" if is_gif else "photo.jpg"
                                return buf
            except Exception as http_err:
                logger.warning(f"Direct HTTP custom media download failed: {http_err}")

    if image_bytes:
        buf = io.BytesIO(image_bytes)
        buf.name = "achievement.png"
        return buf
    return None

# Custom 3D Animated Emoji IDs (Paste Telegram emoji IDs here directly)
ANIMATED_EYE_EMOJI_ID = "5424892643760937442"    # Custom Eye Emoji ID
ANIMATED_GRAPH_EMOJI_ID = "6334317175158873183"  # Orange Bar Chart Emoji ID (New Call Alerts)
MILESTONE_GRAPH_EMOJI_ID = "5231200819986047254" # Milestone Alert Animated Graph Emoji ID
ANIMATED_ARROW_EMOJI_ID = "6332322343238508665"  # Custom Arrow Emoji ID between dollar amounts

def parse_html_with_custom_emojis(html_text: str):
    """
    Parses HTML text containing <tg-emoji emoji-id="12345">emoji</tg-emoji> tags
    and returns (clean_text, telethon_entities) with proper MessageEntityCustomEmoji entities for Telethon.
    """
    if not html_text:
        return "", []

    import re
    from telethon.extensions import html
    from telethon.tl.types import MessageEntityCustomEmoji

    pattern = re.compile(r'<tg-emoji\s+emoji-id=["\'](\d+)["\']>(.*?)</tg-emoji>', re.DOTALL)
    emoji_infos = []

    def replacer(match):
        doc_id = int(match.group(1))
        inner = match.group(2)
        idx = len(emoji_infos)
        emoji_infos.append((doc_id, inner))
        return f"___EMOJI_{idx}___"

    replaced_html = pattern.sub(replacer, html_text)
    clean_text, entities = html.parse(replaced_html)
    final_text = clean_text
    final_entities = list(entities or [])

    for idx, (doc_id, inner) in enumerate(emoji_infos):
        marker = f"___EMOJI_{idx}___"
        pos = final_text.find(marker)
        if pos != -1:
            utf16_prefix = final_text[:pos].encode('utf-16-le')
            utf16_offset = len(utf16_prefix) // 2

            utf16_inner = inner.encode('utf-16-le')
            utf16_length = len(utf16_inner) // 2

            utf16_marker = marker.encode('utf-16-le')
            utf16_marker_len = len(utf16_marker) // 2

            diff = utf16_marker_len - utf16_length

            final_text = final_text[:pos] + inner + final_text[pos + len(marker):]

            for ent in final_entities:
                if ent.offset > utf16_offset:
                    ent.offset -= diff

            final_entities.append(MessageEntityCustomEmoji(
                offset=utf16_offset,
                length=utf16_length,
                document_id=doc_id
            ))

    return final_text, final_entities

def get_arrow_emoji_html() -> str:
    if ANIMATED_ARROW_EMOJI_ID:
        return f'<tg-emoji emoji-id="{ANIMATED_ARROW_EMOJI_ID}">➡️</tg-emoji>'
    return "➡️"

# Consistency Alert Dedicated Custom Emoji IDs
CONSISTENCY_KEY_EMOJI_ID = "5330115548900501467"    # Custom Key / Trophy Emoji ID
CONSISTENCY_EYE_EMOJI_ID = "6334565063491330160"    # Custom Eye Emoji ID for Consistency Alert
CONSISTENCY_GRAPH_EMOJI_ID = "6334317175158873183"  # Custom Graph Emoji ID for Consistency Alert

# Official Network / Chain Custom Emoji IDs (Set via .env or directly here)
SOLANA_EMOJI_ID = "5188179893518614788"
ETHEREUM_EMOJI_ID = "5384093864652982130"
BSC_EMOJI_ID = "5384321407725358581"
TETHER_EMOJI_ID = "5188605164000395914"
BASE_EMOJI_ID = "4916227890095326351"
ARBITRUM_EMOJI_ID = "5451785452635826389"
ROBINHOOD_EMOJI_ID = "6118528380301219265"  # 3D Robinhood Chain Logo
ARC_EMOJI_ID = "6033064436690853336"        # 3D ARC Chain Logo
CASH_PRINTER_EMOJI_ID = "5429260604026153808"

GLOBE_EMOJI_ID = "6091464223706063405"      # Orange Globe Emoji ID (Default / Fallback)
TARGET_EMOJI_ID = "6091143084706374133"     # Orange KOLInsights Badge Emoji ID
COIN_EMOJI_ID = "5854874627355970534"
MEGAPHONE_EMOJI_ID = "6089368777881822113"  # Orange Megaphone Emoji ID

# 3D Orange Rank Emoji IDs for Recently Called Leaderboard
RANK_1_EMOJI_ID = "6334793912233761269"  # Orange Crown
RANK_2_EMOJI_ID = "6334599844136493120"  # Orange 1
RANK_3_EMOJI_ID = "6332478143177171124"  # Orange 2
RANK_4_EMOJI_ID = "6334493642480163639"  # Orange 3
RANK_5_EMOJI_ID = "6334360236500985140"  # Orange 4

def get_cash_printer_emoji_html() -> str:
    if CASH_PRINTER_EMOJI_ID and str(CASH_PRINTER_EMOJI_ID).isdigit():
        return f'<tg-emoji emoji-id="{CASH_PRINTER_EMOJI_ID}">💸</tg-emoji>'
    return "💸"

def get_chain_emoji_html(chain: str) -> str:
    chain_lower = (chain or "").lower().strip()
    emoji_id = ""

    if "sol" in chain_lower:
        emoji_id = SOLANA_EMOJI_ID
    elif "eth" in chain_lower:
        emoji_id = ETHEREUM_EMOJI_ID
    elif "bsc" in chain_lower or "binance" in chain_lower or "bnb" in chain_lower:
        emoji_id = BSC_EMOJI_ID
    elif "base" in chain_lower:
        emoji_id = BASE_EMOJI_ID
    elif "arb" in chain_lower or "arbitrum" in chain_lower:
        emoji_id = ARBITRUM_EMOJI_ID
    elif "robinhood" in chain_lower or "rh" in chain_lower:
        emoji_id = ROBINHOOD_EMOJI_ID
    elif "arc" in chain_lower or "arch" in chain_lower:
        emoji_id = ARC_EMOJI_ID
    elif any(k in chain_lower for k in ("tether", "usdt", "stable", "tron", "trx")):
        emoji_id = TETHER_EMOJI_ID

    if not emoji_id and GLOBE_EMOJI_ID:
        emoji_id = GLOBE_EMOJI_ID

    if emoji_id and str(emoji_id).isdigit():
        return f'<tg-emoji emoji-id="{emoji_id}">🌐</tg-emoji>'
    return "🌐"

def get_eye_emoji_html() -> str:
    if ANIMATED_EYE_EMOJI_ID:
        return f'<tg-emoji emoji-id="{ANIMATED_EYE_EMOJI_ID}">👁️</tg-emoji>'
    return "👁️"

def get_graph_emoji_html() -> str:
    if ANIMATED_GRAPH_EMOJI_ID:
        return f'<tg-emoji emoji-id="{ANIMATED_GRAPH_EMOJI_ID}">📊</tg-emoji>'
    return "📊"

def get_milestone_graph_emoji_html() -> str:
    if MILESTONE_GRAPH_EMOJI_ID:
        return f'<tg-emoji emoji-id="{MILESTONE_GRAPH_EMOJI_ID}">📊</tg-emoji>'
    return get_graph_emoji_html()

def get_target_emoji_html() -> str:
    if TARGET_EMOJI_ID and str(TARGET_EMOJI_ID).isdigit():
        return f'<tg-emoji emoji-id="{TARGET_EMOJI_ID}">🎯</tg-emoji>'
    return "🎯"

def get_coin_emoji_html() -> str:
    if COIN_EMOJI_ID and str(COIN_EMOJI_ID).isdigit():
        return f'<tg-emoji emoji-id="{COIN_EMOJI_ID}">🪙</tg-emoji>'
    return "🪙"

def get_megaphone_emoji_html() -> str:
    if MEGAPHONE_EMOJI_ID and str(MEGAPHONE_EMOJI_ID).isdigit():
        return f'<tg-emoji emoji-id="{MEGAPHONE_EMOJI_ID}">📢</tg-emoji>'
    return "📢"

def get_consistency_key_emoji_html() -> str:
    if CONSISTENCY_KEY_EMOJI_ID:
        return f'<tg-emoji emoji-id="{CONSISTENCY_KEY_EMOJI_ID}">🔑</tg-emoji>'
    return "🔑"

def get_consistency_eye_emoji_html() -> str:
    if CONSISTENCY_EYE_EMOJI_ID:
        return f'<tg-emoji emoji-id="{CONSISTENCY_EYE_EMOJI_ID}">👁️</tg-emoji>'
    return get_eye_emoji_html()

def get_consistency_graph_emoji_html() -> str:
    if CONSISTENCY_GRAPH_EMOJI_ID:
        return f'<tg-emoji emoji-id="{CONSISTENCY_GRAPH_EMOJI_ID}">📊</tg-emoji>'
    return get_graph_emoji_html()

def get_chain_slug_and_explorer(chain: str, token_addr: str):
    c = (chain or "").lower().strip()
    if "sol" in c:
        return "sol", f"https://solscan.io/token/{token_addr}", "Solscan"
    elif "base" in c:
        return "base", f"https://basescan.org/token/{token_addr}", "Basescan"
    elif "bsc" in c or "binance" in c or "bnb" in c:
        return "bsc", f"https://bscscan.com/token/{token_addr}", "BscScan"
    elif "arb" in c:
        return "arb", f"https://arbiscan.io/token/{token_addr}", "Arbiscan"
    else:
        return "eth", f"https://etherscan.io/token/{token_addr}", "Etherscan"

def build_trade_buttons(call_data: dict, bot_username: str = "kolinsightsbot"):
    """
    Builds clean, ultra-responsive trading and chart buttons:
    - Maestro 1-Click Buy Bot: https://t.me/MaestroSniperBot?start={ca}
    - Smart DEX / GMGN Button:
        * If indexed with pair -> DexScreener
        * If new launch without pair -> GMGN (indexes from block 0)
        * Fallback -> Explorer
    - Action links: View Call & View Stats
    """
    from telethon import Button
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    token_addr = str(call_data.get("token_address", "")).strip()
    chain = str(call_data.get("chain", "solana")).lower().strip()
    pair_addr = str(call_data.get("pair_address", "")).strip()
    pair_url = str(call_data.get("pair_url", "")).strip()
    call_msg_link = call_data.get("call_msg_link")

    chain_slug, explorer_url, explorer_name = get_chain_slug_and_explorer(chain, token_addr)

    # Maestro Direct Sniper Bot
    maestro_url = f"https://t.me/MaestroSniperBot?start={token_addr}"

    # Smart DEX / Chart button
    if pair_url:
        chart_label = "📊 DexScreener"
        chart_url = pair_url
    elif pair_addr:
        chart_label = "📊 DexScreener"
        chart_url = f"https://dexscreener.com/{chain_slug}/{pair_addr}"
    elif chain_slug in ("sol", "eth", "base", "bsc", "arb"):
        chart_label = "📊 GMGN"
        chart_url = f"https://gmgn.ai/{chain_slug}/token/{token_addr}"
    else:
        chart_label = f"🔍 {explorer_name}"
        chart_url = explorer_url

    tele_row1 = [
        Button.url("🚀 Buy on Maestro", maestro_url),
        Button.url(chart_label, chart_url)
    ]
    tele_row2 = []
    if call_msg_link:
        tele_row2.append(Button.url("👁️ View Call", call_msg_link))

    caller_identifier = call_data.get("channel_username") or call_data.get("channel_id")
    if caller_identifier:
        clean_caller = str(caller_identifier).lstrip("@").strip()
        stats_url = f"https://t.me/{bot_username}?start=stats_{clean_caller}"
        tele_row2.append(Button.url("📊 Stats", stats_url))

    tele_buttons = [tele_row1]
    if tele_row2:
        tele_buttons.append(tele_row2)

    bot_row1 = [
        InlineKeyboardButton("🚀 Buy on Maestro", url=maestro_url),
        InlineKeyboardButton(chart_label, url=chart_url)
    ]
    bot_row2 = []
    if call_msg_link:
        bot_row2.append(InlineKeyboardButton("👁️ View Call", url=call_msg_link))
    if caller_identifier:
        clean_caller = str(caller_identifier).lstrip("@").strip()
        stats_url = f"https://t.me/{bot_username}?start=stats_{clean_caller}"
        bot_row2.append(InlineKeyboardButton("📊 Stats", url=stats_url))

    bot_keyboard = [bot_row1]
    if bot_row2:
        bot_keyboard.append(bot_row2)
    bot_markup = InlineKeyboardMarkup(bot_keyboard)

    return tele_buttons, bot_markup

async def send_milestone_alert(call_data: dict, milestone: float, image_bytes: bytes, bot_app):
    """Sends a milestone achievement card to the alert channel and caller group."""
    if not BOT_TOKEN or not bot_app:
        return

    call_id = call_data.get("id")
    token_addr = str(call_data.get("token_address", "")).lower().strip()
    raw_user = (call_data.get("channel_username") or "").lstrip("@").lower().strip()
    ch_id = str(call_data.get("channel_id", "")).strip()
    caller_key = raw_user if raw_user else ch_id

    alert_key = f"{token_addr}_{caller_key}_{milestone}"
    if alert_key in _sent_milestone_alerts or (call_id and f"{call_id}_{milestone}" in _sent_milestone_alerts):
        logger.info(f"Skipping duplicate milestone alert {alert_key}")
        return

    from database import has_alert_been_sent, has_alert_been_sent_by_token
    if (call_id and await has_alert_been_sent(call_id, milestone)) or (token_addr and await has_alert_been_sent_by_token(token_addr, caller_key, milestone)):
        logger.info(f"Skipping duplicate milestone alert from DB check: {alert_key}")
        _sent_milestone_alerts.add(alert_key)
        if call_id:
            _sent_milestone_alerts.add(f"{call_id}_{milestone}")
        return

    _sent_milestone_alerts.add(alert_key)
    if call_id:
        _sent_milestone_alerts.add(f"{call_id}_{milestone}")
    _trim_set(_sent_milestone_alerts, max_size=2000)

    from bot_handlers import clean_markdown
    from config import get_effective_alert_channel_id
    from database import get_custom_media
    from graphics import format_number
    from telegram import InputFile

    bot_info = await bot_app.bot.get_me() if (bot_app and bot_app.bot) else None
    bot_username = bot_info.username if (bot_info and bot_info.username) else "kolinsightsbot"

    raw_user = (call_data.get('channel_username') or "").lstrip("@")
    ch_title = clean_markdown(call_data.get('channel_title') or "Unknown Channel")
    t_name = clean_markdown(call_data['token_name'])
    token_address = call_data['token_address']
    entry_str = format_number(call_data['entry_mc'])
    ath_str = format_number(call_data['ath_mc'])
    channel_id = call_data.get('channel_id')

    caller_link = f"[@{clean_markdown(raw_user)}](https://t.me/{raw_user})" if raw_user else ""

    if call_data.get("call_msg_link"):
        group_url = call_data["call_msg_link"]
    elif channel_id:
        clean_cid = str(channel_id).replace("-100", "").strip()
        group_url = f"https://t.me/c/{clean_cid}/1"
    else:
        group_url = f"https://t.me/{bot_username}?start=stats_{channel_id}"

    if raw_user:
        channel_link = f"[@{clean_markdown(raw_user)}](https://t.me/{raw_user})"
    else:
        channel_link = f"[{ch_title}]({group_url})"

    project_link = f"[{t_name}](https://t.me/{bot_username}?start=token_{token_address})"

    mult_str = f"x{int(milestone)}" if milestone.is_integer() else f"x{milestone:.1f}"

    caller_identifier = raw_user or call_data.get("channel_id")
    stats_url = f"https://t.me/{bot_username}?start=stats_{caller_identifier}"

    links_inline = []
    if call_data.get("call_msg_link"):
        links_inline.append(f"👁️ [View Call]({call_data['call_msg_link']})")
    links_inline.append(f"📊 [View Stats]({stats_url})")

    ANIMATED_EMOJI_IDS = {
        2.0: ("✌️", "5469986291380657759"),  # Telegram 3D Waving 2-Finger Animated Emoji
        3.0: ("🤟", "5471957105843904125"),  # Telegram 3D Waving 3-Finger Animated Emoji
        4.0: ("💥", "5276032951342088188"),
        5.0: ("🖖", "5463089948292824398"),
        6.0: ("⚡️", "5456140674028019486"),
        7.0: ("🔥", "5420315771991497307"),
        8.0: ("🎯", "5256131095094652290"),  # Animated Target Emoji
        9.0: ("🚀", "5445284980978621387"),
        10.0: ("🙌", "5429451536797284796"),
        11.0: ("🚀", "5445284980978621387"),
        12.0: ("🚀", "5445284980978621387"),
        13.0: ("🚀", "5445284980978621387"),
        14.0: ("🚀", "5445284980978621387"),
        15.0: ("🔥", "5420315771991497307"),
        16.0: ("🚀", "5445284980978621387"),
        17.0: ("🚀", "5445284980978621387"),
        18.0: ("🚀", "5445284980978621387"),
        19.0: ("🚀", "5445284980978621387"),
        20.0: ("🚀", "5445284980978621387"),
        50.0: ("👑", "5433758796289685818"),
        100.0: ("💎", "5427168083074628963"),
        500.0: ("🔥", "5420315771991497307"),
        1000.0: ("⚡️", "5456140674028019486")
    }

    default_3d_rocket_id = "5445284980978621387"
    emoji_tuple = ANIMATED_EMOJI_IDS.get(milestone, ("🚀", default_3d_rocket_id))
    fallback_char, custom_emoji_id = emoji_tuple[0], emoji_tuple[1]

    hand_emoji_html = f'<tg-emoji emoji-id="{custom_emoji_id}">{fallback_char}</tg-emoji>' if custom_emoji_id else fallback_char

    alpha_text = " (Alpha Call)" if 100.0 <= milestone <= 500.0 else ""

    safe_ch_title = ch_title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_t_name = t_name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_raw_user = raw_user.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    ch_link_html = f'<a href="https://t.me/{raw_user}">@{safe_raw_user}</a>' if raw_user else f'<a href="{group_url}">{safe_ch_title}</a>'
    proj_link_html = f'<a href="https://t.me/{bot_username}?start=token_{token_address}">{safe_t_name}</a>'

    # Detect if 2x was hit immediately (within 10 minutes) vs Slow Cook (>= 24 hours)
    is_immediate = False
    is_slow_cook = False
    hold_time_str = ""
    call_created = call_data.get("created_at")
    if call_created:
        try:
            from datetime import datetime, timezone
            created_str = str(call_created)[:19]
            created_dt = datetime.strptime(created_str, "%Y-%m-%d %H:%M:%S")
            now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
            total_sec = (now_dt - created_dt).total_seconds()
            if total_sec <= 600:
                is_immediate = True
            elif total_sec >= 86400:
                is_slow_cook = True
                days = int(total_sec // 86400)
                hours = int((total_sec % 86400) // 3600)
                hold_time_str = f"{days}d {hours}h" if days > 0 else f"{hours}h"
        except Exception:
            pass

    INSTANT_HEADERS = {
        2.0: "SPEED DEMON !",
        3.0: "INSTANT MOON !",
        4.0: "HYPER SPEED !",
        5.0: "LIGHTSPEED !",
        6.0: "HIGH VOLTAGE !",
        7.0: "INFERNO !",
        8.0: "BULLSEYE !",
        9.0: "SONIC BOOM !",
        10.0: "PARABOLIC !",
        20.0: "HYPERDRIVE !",
        30.0: "MONEY MATRIX !",
        40.0: "FORTUNE MULTIPLIER !",
        50.0: "UNSTOPPABLE !",
        60.0: "SUPERNOVA !",
        70.0: "COSMIC GAINS !",
        80.0: "INTERSTELLAR !",
        90.0: "ULTIMATE ALPHA !",
        100.0: "GODMODE !"
    }

    SINGLE_WORD_HEADERS = {
        2.0: "TAKEOFF !",
        3.0: "SKYROCKET !",
        4.0: "SUPERCHARGED !",
        5.0: "NEON IGNITION !",
        6.0: "HIGH VOLTAGE !",
        7.0: "INFERNO !",
        8.0: "BULLSEYE !",
        9.0: "SONIC BOOM !",
        10.0: "PARABOLIC !",
        11.0: "PARABOLIC !",
        12.0: "PARABOLIC !",
        13.0: "PARABOLIC !",
        14.0: "PARABOLIC !",
        15.0: "PARABOLIC !",
        16.0: "PARABOLIC !",
        17.0: "PARABOLIC !",
        18.0: "PARABOLIC !",
        19.0: "PARABOLIC !",
        20.0: "HYPERDRIVE !",
        30.0: "MONEY MATRIX !",
        40.0: "FORTUNE MULTIPLIER !",
        50.0: "UNSTOPPABLE !",
        60.0: "SUPERNOVA !",
        70.0: "COSMIC GAINS !",
        80.0: "INTERSTELLAR !",
        90.0: "ULTIMATE ALPHA !",
        100.0: "GODMODE !",
        500.0: "GODMODE !",
        1000.0: "GODMODE !"
    }

    # Check for Slow Cook vs Instant vs Standard Milestones
    if is_slow_cook:
        top_word = "SLOW COOK !"
        achievement_header = f"Slow Cook Smashed: <b>{mult_str}</b> (Held {hold_time_str}) {hand_emoji_html}"
        hit_text = f"{proj_link_html} called by {ch_link_html} simmered and cooked a massive <b>{mult_str}</b> over <b>{hold_time_str}</b>!"
    elif milestone in (20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0):
        top_word = INSTANT_HEADERS.get(milestone) if is_immediate else SINGLE_WORD_HEADERS.get(milestone, "HYPERDRIVE !")
        achievement_header = f"Achievement Unlocked: <b>{mult_str}</b>{alpha_text} {hand_emoji_html}"
        if is_immediate:
            hit_text = f"{proj_link_html} called by {ch_link_html} hit <b>{mult_str}</b> immediately!"
        else:
            hit_text = f"{proj_link_html} called by {ch_link_html} hit <b>{mult_str}</b>!"
    elif 20.0 < milestone < 100.0:
        top_word = "MULTIPLIER SURGE !"
        achievement_header = f"Achievement Unlocked: <b>{mult_str} Multiplier</b> {hand_emoji_html}"
        if is_immediate:
            hit_text = f"{ch_link_html} posted one of the <b>biggest X</b> of the day in the <b>KOLInsights</b> network with a <b>{mult_str}</b> on {proj_link_html} immediately!"
        else:
            hit_text = f"{ch_link_html} posted one of the <b>biggest X</b> of the day in the <b>KOLInsights</b> network with a <b>{mult_str}</b> on {proj_link_html}"
    elif is_immediate:
        top_word = INSTANT_HEADERS.get(milestone, "SPEED DEMON !")
        achievement_header = f"Achievement Unlocked: <b>{mult_str}</b>{alpha_text} {hand_emoji_html}"
        hit_text = f"{proj_link_html} called by {ch_link_html} hit <b>{mult_str}</b> immediately!"
    else:
        top_word = SINGLE_WORD_HEADERS.get(milestone, "TAKEOFF !")
        achievement_header = f"Achievement Unlocked: <b>{mult_str}</b>{alpha_text} {hand_emoji_html}"
        hit_text = f"{proj_link_html} called by {ch_link_html} hit <b>{mult_str}</b>!"

    arrow_emoji_html = get_arrow_emoji_html()

    links_inline_html = []
    eye_icon = get_eye_emoji_html()
    graph_icon = get_milestone_graph_emoji_html()
    if call_data.get("call_msg_link"):
        links_inline_html.append(f'{eye_icon} <a href="{call_data["call_msg_link"]}">View Call</a>')
    links_inline_html.append(f'{graph_icon} <a href="{stats_url}">View Stats</a>')
    joined_links = "  ".join(links_inline_html)

    DEGEN_QUOTES = {
        2.0: '🚀 "First target smashed, WAGMI !"',
        3.0: '💰 "Stacking bags, printing cash !"',
        4.0: '😎 "Paper hands in tears, gigachads in profit !"',
        5.0: '🔥 "Faded the call? Enjoy staying poor !"',
        6.0: '⚡️ "Send it straight to Valhalla !"',
        7.0: '🕯️ "We don\'t sleep, we print green candles !"',
        8.0: '🎯 "Sniper accuracy, pure alpha !"',
        9.0: '🏎️ "Brakes broken, full speed ahead !"',
        10.0: '👑 "From degenerate to generational wealth !"',
        15.0: '🌌 "Escaped Earth\'s gravity, zero regrets !"',
        20.0: '🍾 "Pop the champagne, we ain\'t stopping !"',
        50.0: '🎬 "Absolute cinema, generational gains !"',
        100.0: '💎 "Godmode activated, legends never die !"',
        500.0: '🏛️ "Built an empire out of meme coins !"',
        1000.0: '🪐 "Rewriting financial history on-chain !"'
    }

    SLOW_COOK_QUOTES = [
        '🍲 "Patience prints wealth !"',
        '🍲 "Let him cook !"',
        '🍲 "Diamond hands cooked !"',
        '🍲 "Simmered to perfection !"',
        '🍲 "Pure conviction !"',
        '🍲 "Chef\'s masterclass !"',
        '🍲 "Faded early, cooked late !"'
    ]

    if is_slow_cook:
        import random
        quote_text = random.choice(SLOW_COOK_QUOTES)
    else:
        quote_text = DEGEN_QUOTES.get(milestone, '🎬 "Absolute cinema, generational gains !"')

    chain_name = call_data.get("chain", "")
    chain_logo_html = get_chain_emoji_html(chain_name)

    safe_t_sym = clean_markdown(call_data.get('token_symbol') or 'TOKEN').replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    tele_buttons, bot_markup = build_trade_buttons(call_data, bot_username)

    caption_html = (
        f"<b>🚀 {top_word}</b>\n"
        f"{chain_logo_html} <b>Achievement Unlocked: {mult_str}</b>{alpha_text} {hand_emoji_html}\n\n"
        f"⚡ <b>Project:</b> {proj_link_html} (<code>${safe_t_sym}</code>)\n"
        f"🌐 <b>Network:</b> <b>{chain_name.upper()}</b>\n"
        f"📢 <b>Called By:</b> {ch_link_html}\n"
        f"📈 <b>Performance:</b> <b>{entry_str}</b> {arrow_emoji_html} <b>{ath_str}</b> (<b>{mult_str}</b>)\n\n"
        f"📋 <b>Contract:</b>\n"
        f"<code>{token_address}</code>\n\n"
        f"<i>{quote_text}</i>"
    )

    # Resolve main alert channel & all linked co-owners
    from database import get_channel_owners
    alert_ch = await get_effective_alert_channel_id()
    
    caller_ch_id = call_data.get("channel_id")
    caller_ch_user = call_data.get("channel_username")
    all_owners = await get_channel_owners(caller_ch_user or caller_ch_id)

    # 1. Top Priority: If slow cook, use dedicated 'slowcook' / 'slow_cook' GIF first!
    custom_media = None
    if is_slow_cook:
        custom_media = await get_custom_media("slowcook") or await get_custom_media("slow_cook")

    # 2. Immediate Surge Priority: If call hit milestone within 10 minutes, use dedicated immediate/instant/motion GIF!
    if not custom_media and is_immediate:
        custom_media = (
            await get_custom_media(f"immediate_{int(milestone)}x")
            or await get_custom_media("immediate")
            or await get_custom_media("immediately")
            or await get_custom_media("instant")
            or await get_custom_media("motion")
            or await get_custom_media("instant2x")
            or await get_custom_media("speed")
        )

    # 3. Check if exact milestone has specific media set (e.g. /setmedia 2x, 3x, 4x, 20x)
    if not custom_media:
        custom_media = (
            await get_custom_media(f"{int(milestone)}x")
            or await get_custom_media(mult_str)
            or await get_custom_media(f"{milestone:.1f}x")
            or await get_custom_media(str(milestone))
        )

    # 4. If initial call alert (1x entry), check for dedicated 'newcall' or 'call' GIF
    if not custom_media and milestone <= 1.0:
        custom_media = await get_custom_media("newcall") or await get_custom_media("call")

    # 5. If in-between step multipliers (20x-100x range), check for cashprinter
    if not custom_media and (20.0 < milestone < 100.0):
        custom_media = await get_custom_media("cashprinter") or await get_custom_media("printer")

    logger.info(f"🎨 [3D ANIMATED EMOJI LOG] Preparing milestone alert for {milestone}x with emoji tag: {hand_emoji_html} (Emoji ID: {custom_emoji_id})")

    main_msg_id_telethon = None
    main_msg_id_botapi = None

    # Step A: Post Achievement Card to Main Alert Channel first
    if alert_ch:
        if _global_scraper and _global_scraper.client and _global_scraper.client.is_connected():
            try:
                media_buf = await resolve_media_buffer(custom_media, image_bytes, bot_app)
                is_gif = custom_media and custom_media.get("media_type") == "animation"
                file_attrs = [DocumentAttributeAnimated(), DocumentAttributeVideo(duration=0, w=500, h=500, supports_streaming=True)] if is_gif else None

                clean_caption, tele_entities = parse_html_with_custom_emojis(caption_html)

                target_entity = await safe_get_telethon_entity(_global_scraper.client, alert_ch)
                if media_buf:
                    media_buf.seek(0)
                    try:
                        msg_res = await _global_scraper.client.send_file(
                            entity=target_entity,
                            file=media_buf,
                            caption=clean_caption,
                            formatting_entities=tele_entities,
                            attributes=file_attrs,
                            buttons=tele_buttons
                        )
                    except Exception:
                        media_buf.seek(0)
                        msg_res = await _global_scraper.client.send_file(
                            entity=target_entity,
                            file=media_buf,
                            caption=clean_caption,
                            formatting_entities=tele_entities,
                            attributes=file_attrs
                        )
                else:
                    try:
                        msg_res = await _global_scraper.client.send_message(
                            entity=target_entity,
                            message=clean_caption,
                            formatting_entities=tele_entities,
                            link_preview=False,
                            buttons=tele_buttons
                        )
                    except Exception:
                        msg_res = await _global_scraper.client.send_message(
                            entity=target_entity,
                            message=clean_caption,
                            formatting_entities=tele_entities,
                            link_preview=False
                        )
                if msg_res:
                    main_msg_id_telethon = msg_res.id
                    logger.info(f"📢 [MILESTONE CHANNEL USERBOT] ✅ Successfully delivered {milestone}x Achievement Card via Telethon Userbot to '{alert_ch}'! (Message ID: {msg_res.id})")
            except Exception as main_err:
                logger.warning(f"⚠️ Userbot delivery error to main channel '{alert_ch}': {main_err}. Falling back to Bot API...")

        # Fallback to Telegram Bot API if Telethon userbot is unavailable
        if not main_msg_id_telethon and bot_app and getattr(bot_app, "bot", None):
            try:
                if custom_media:
                    media_type = custom_media.get("media_type")
                    file_id = custom_media.get("file_id")
                    if media_type == "animation":
                        b_res = await bot_app.bot.send_animation(chat_id=alert_ch, animation=file_id, caption=caption_html, parse_mode="HTML", reply_markup=bot_markup)
                    else:
                        b_res = await bot_app.bot.send_photo(chat_id=alert_ch, photo=file_id, caption=caption_html, parse_mode="HTML", reply_markup=bot_markup)
                else:
                    b_res = await bot_app.bot.send_photo(chat_id=alert_ch, photo=InputFile(image_bytes, filename="achievement.png"), caption=caption_html, parse_mode="HTML", reply_markup=bot_markup)
                
                if b_res:
                    main_msg_id_botapi = b_res.message_id
                    logger.info(f"📢 [MAIN CHANNEL BOT API SUCCESS] ✅ Delivered {milestone}x Achievement Card via @kolinsightsbot to '{alert_ch}'! (Message ID: {b_res.message_id})")
            except Exception as bot_err:
                logger.error(f"❌ [MAIN CHANNEL POST FAILED] Could not deliver milestone card via Bot API to main channel '{alert_ch}': {bot_err}")

    fwd_msg_id = main_msg_id_botapi or main_msg_id_telethon

    # Step B: Forward official channel message directly to ALL linked channel co-owners' DMs via Bot API (@kolinsightsbot)
    if all_owners:
        logger.info(f"👤 [OWNER DM DISPATCH] Found {len(all_owners)} owner(s) for channel '{caller_ch_user or caller_ch_id}'. Sending DM alerts...")
        for single_owner in all_owners:
            clean_owner = str(single_owner).strip()
            forwarded = False

            if fwd_msg_id and alert_ch and bot_app and getattr(bot_app, "bot", None):
                try:
                    await bot_app.bot.forward_message(chat_id=clean_owner, from_chat_id=alert_ch, message_id=fwd_msg_id)
                    forwarded = True
                    logger.info(f"📥 [OWNER DM ALERT SUCCESS] ✅ Forwarded {milestone}x Achievement Card via @kolinsightsbot to Owner DM '{clean_owner}'!")
                except Exception as bot_fwd_err:
                    logger.warning(f"⚠️ Bot API forwarding notice to Owner DM '{clean_owner}': {bot_fwd_err}. Fallback to direct DM delivery...")

            if not forwarded and bot_app and getattr(bot_app, "bot", None):
                try:
                    if custom_media:
                        media_type = custom_media.get("media_type")
                        file_id = custom_media.get("file_id")
                        if media_type == "animation":
                            await bot_app.bot.send_animation(chat_id=clean_owner, animation=file_id, caption=caption_html, parse_mode="HTML")
                        else:
                            await bot_app.bot.send_photo(chat_id=clean_owner, photo=file_id, caption=caption_html, parse_mode="HTML")
                    else:
                        await bot_app.bot.send_photo(chat_id=clean_owner, photo=InputFile(image_bytes, filename="achievement.png"), caption=caption_html, parse_mode="HTML")
                    logger.info(f"📥 [OWNER DM ALERT SUCCESS] ✅ Delivered direct achievement card copy via @kolinsightsbot to Owner DM '{clean_owner}'!")
                except Exception as direct_err:
                    logger.error(f"❌ [OWNER DM ALERT FAILED] Could not deliver DM card to Owner '{clean_owner}': {direct_err}")

    # Dispatch DM card to all subscribed followers of this channel
    if alert_ch and bot_app and getattr(bot_app, "bot", None):
        from database import get_channel_followers
        followers = await get_channel_followers(caller_ch_user or caller_ch_id)
        if followers:
            owner_set = {str(o).replace("@", "").strip() for o in all_owners} if all_owners else set()
            logger.info(f"👥 [FOLLOWER DM DISPATCH] Found {len(followers)} follower(s) for channel '{caller_ch_user or caller_ch_id}'. Sending DM alerts...")
            for f_user_id in followers:
                if str(f_user_id) in owner_set:
                    continue
                try:
                    if fwd_msg_id:
                        await bot_app.bot.forward_message(chat_id=f_user_id, from_chat_id=alert_ch, message_id=fwd_msg_id)
                    elif custom_media:
                        file_id = custom_media.get("file_id")
                        if custom_media.get("media_type") == "animation":
                            await bot_app.bot.send_animation(chat_id=f_user_id, animation=file_id, caption=caption_html, parse_mode="HTML")
                        else:
                            await bot_app.bot.send_photo(chat_id=f_user_id, photo=file_id, caption=caption_html, parse_mode="HTML")
                    else:
                        await bot_app.bot.send_photo(chat_id=f_user_id, photo=InputFile(image_bytes, filename="achievement.png"), caption=caption_html, parse_mode="HTML")
                    logger.info(f"📥 [FOLLOWER DM ALERT SUCCESS] ✅ Delivered {milestone}x Achievement Card via @kolinsightsbot to Follower DM '{f_user_id}'!")
                except Exception as f_err:
                    logger.error(f"❌ [FOLLOWER DM ALERT FAILED] Could not deliver DM card to Follower '{f_user_id}': {f_err}")

async def send_consistency_alert(call_data: dict, count: int, calls_7d: list, bot_app):
    """Sends Consistency alert matching Image 2 layout."""
    if not BOT_TOKEN or not bot_app:
        return

    from bot_handlers import clean_markdown
    from config import get_effective_alert_channel_id
    from database import get_custom_media

    bot_info = await bot_app.bot.get_me() if (bot_app and bot_app.bot) else None
    bot_username = bot_info.username if (bot_info and bot_info.username) else "kolinsightsbot"

    raw_user = (call_data.get('channel_username') or "").lstrip("@")
    ch_title = clean_markdown(call_data.get('channel_title') or "Unknown Channel")
    safe_ch_title = ch_title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_raw_user = raw_user.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    if raw_user:
        ch_link_html = f'<a href="https://t.me/{raw_user}">@{safe_raw_user}</a>'
    else:
        channel_id = call_data.get('channel_id')
        clean_cid = str(channel_id).replace("-100", "").strip() if channel_id else ""
        group_url = f"https://t.me/c/{clean_cid}/1" if clean_cid else f"https://t.me/{bot_username}"
        ch_link_html = f'<a href="{group_url}">{safe_ch_title}</a>'

    key_icon = get_consistency_key_emoji_html()
    eye_icon = get_consistency_eye_emoji_html()
    graph_icon = get_consistency_graph_emoji_html()

    call_links_html = []
    for idx, c in enumerate(calls_7d[:5], start=1):
        c_link = c.get('call_msg_link')
        if not c_link and c.get('call_msg_id') and c.get('channel_username'):
            c_link = f"https://t.me/{c['channel_username']}/{c['call_msg_id']}"
        if c_link:
            call_links_html.append(f'{eye_icon} <a href="{c_link}">View Call {idx}</a>')

    caller_id = raw_user or call_data.get('channel_id')
    stats_url = f"https://t.me/{bot_username}?start=stats_{caller_id}"
    stats_link_html = f'{graph_icon} <a href="{stats_url}">View Stats</a>'

    joined_call_links = "\n".join(call_links_html)

    caption_html = (
        f"<b>Achievement Unlocked: Consistency is key! {key_icon}</b>\n\n"
        f"{ch_link_html} has made <b>{count} x2+ calls</b> in the past 7 days.\n\n"
        f"{joined_call_links}\n\n"
        f"{stats_link_html}"
    )

    alert_ch = await get_effective_alert_channel_id()
    if not alert_ch:
        return

    custom_media = await get_custom_media("consistency")
    alert_target = int(alert_ch) if str(alert_ch).lstrip("-").isdigit() else alert_ch

    sent_via_userbot = False
    if _global_scraper and _global_scraper.client and _global_scraper.client.is_connected():
        try:
            media_buf = await resolve_media_buffer(custom_media, None, bot_app)
            is_gif = custom_media and custom_media.get("media_type") == "animation"
            file_attrs = [DocumentAttributeAnimated(), DocumentAttributeVideo(duration=0, w=500, h=500, supports_streaming=True)] if is_gif else None

            if media_buf:
                media_buf.seek(0)
                await _global_scraper.client.send_file(entity=alert_target, file=media_buf, caption=caption_html, parse_mode="html", attributes=file_attrs)
            else:
                await _global_scraper.client.send_message(entity=alert_target, message=caption_html, parse_mode="html", link_preview=False)
            sent_via_userbot = True
        except Exception as ub_err:
            logger.warning(f"Failed to post consistency alert via Telethon Userbot ({ub_err}), falling back to Bot API...")

    if not sent_via_userbot:
        try:
            if custom_media:
                media_type = custom_media.get("media_type")
                file_id = custom_media.get("file_id")
                if media_type == "animation":
                    await bot_app.bot.send_animation(chat_id=alert_target, animation=file_id, caption=caption_html, parse_mode="HTML", reply_markup=bot_markup)
                else:
                    await bot_app.bot.send_photo(chat_id=alert_target, photo=file_id, caption=caption_html, parse_mode="HTML", reply_markup=bot_markup)
            else:
                await bot_app.bot.send_message(chat_id=alert_target, text=caption_html, parse_mode="HTML", disable_web_page_preview=True, reply_markup=bot_markup)
            logger.info(f"Posted consistency alert to chat {alert_target}")
        except Exception as e:
            logger.warning(f"Failed to post consistency alert to chat {alert_target}: {e}")

async def safe_get_telethon_entity(client, target):
    """Safely resolves Telethon entity for channel ID, username, or Peer object with numeric string normalization and dialog cache priming."""
    from telethon.tl.types import PeerChannel
    parsed_target = target
    if isinstance(target, str):
        clean_str = target.strip()
        # If it's a numeric ID formatted as string (e.g. "-1004359127119" or "4359127119")
        if (clean_str.startswith("-100") and clean_str[4:].isdigit()) or (clean_str.startswith("-") and clean_str[1:].isdigit()) or clean_str.isdigit():
            try:
                parsed_target = int(clean_str)
            except Exception:
                parsed_target = clean_str
        elif clean_str.startswith("@"):
            parsed_target = clean_str.lstrip("@")

    try:
        return await client.get_entity(parsed_target)
    except Exception as e:
        # Fallback to PeerChannel if numeric
        if isinstance(target, (int, str)):
            digits = str(target).replace("-100", "").replace("-", "").strip()
            if digits.isdigit():
                try:
                    return await client.get_entity(PeerChannel(int(digits)))
                except Exception:
                    pass

        logger.warning(f"⚠️ [TELETHON ENTITY] Direct entity resolution failed for {target}: {e}. Priming dialog cache...")
        try:
            await client.get_dialogs(limit=50)
            return await client.get_entity(parsed_target)
        except Exception as e2:
            logger.warning(f"⚠️ [TELETHON ENTITY] Dialog retry failed for {target}: {e2}")
            return parsed_target

async def send_new_call_alert(call_data: dict, bot_app=None, telethon_client=None):
    """Sends an instant new call entry alert to the main alert channel when a call is first recorded."""
    global _sent_new_call_keys, _sent_new_call_alerts, _global_bot_app, _global_scraper
    app = bot_app or _global_bot_app
    bot_app = app

    call_id = call_data.get("id")
    raw_user = (call_data.get('channel_username') or "").lstrip("@").lower()
    ch_id = call_data.get('channel_id')
    caller_key = raw_user if raw_user else str(ch_id)
    token_addr = str(call_data.get('token_address', '')).lower()

    is_dip = call_data.get("is_dip_call", False)
    prev_entry_mc = call_data.get("prev_entry_mc", 0.0)
    if is_dip:
        dedupe_key = f"{caller_key}_{token_addr}_dip_{call_id}"
    else:
        dedupe_key = f"{caller_key}_{token_addr}_{call_id}"

    if dedupe_key in _sent_new_call_keys:
        logger.info(f"Skipping duplicate new call alert for key {dedupe_key}")
        return
    _sent_new_call_keys.add(dedupe_key)
    if call_id:
        _sent_new_call_alerts.add(call_id)
    _trim_set(_sent_new_call_keys, max_size=2000)
    _trim_set(_sent_new_call_alerts, max_size=2000)

    from bot_handlers import clean_markdown
    from config import get_effective_alert_channel_id
    from graphics import format_number
    from database import get_custom_media

    alert_ch = await get_effective_alert_channel_id()
    if not alert_ch:
        return

    bot_info = None
    if bot_app and getattr(bot_app, "bot", None):
        try:
            bot_info = await bot_app.bot.get_me()
        except Exception:
            pass
    bot_username = bot_info.username if (bot_info and bot_info.username) else "kolinsightsbot"

    raw_user = (call_data.get('channel_username') or "").lstrip("@")
    ch_title = clean_markdown(call_data.get('channel_title') or "Unknown Channel")
    t_name = clean_markdown(call_data['token_name'])
    t_sym = clean_markdown(call_data['token_symbol'])
    token_address = call_data['token_address']
    entry_str = format_number(call_data['entry_mc'])
    chain_str = call_data.get('chain', 'solana').upper()

    channel_id = call_data.get('channel_id')
    if call_data.get("call_msg_link"):
        group_url = call_data["call_msg_link"]
    elif channel_id:
        clean_cid = str(channel_id).replace("-100", "").strip()
        group_url = f"https://t.me/c/{clean_cid}/1"
    else:
        group_url = f"https://t.me/{bot_username}?start=stats_{channel_id}"

    safe_ch_title = ch_title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_t_name = t_name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_t_sym = t_sym.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_raw_user = raw_user.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    if raw_user:
        ch_link_html = f'<a href="https://t.me/{raw_user}">@{safe_raw_user}</a>'
    else:
        ch_link_html = f'<a href="{group_url}">{safe_ch_title}</a>'

    proj_link_html = f'<a href="https://t.me/{bot_username}?start=token_{token_address}">{safe_t_name}</a>'

    caller_identifier = raw_user or call_data.get("channel_id")
    stats_url = f"https://t.me/{bot_username}?start=stats_{caller_identifier}"

    eye_icon = get_eye_emoji_html()
    entry_graph_icon = get_graph_emoji_html()
    stats_graph_icon = get_milestone_graph_emoji_html()

    links_inline_html = []
    if call_data.get("call_msg_link"):
        links_inline_html.append(f'{eye_icon} <a href="{call_data["call_msg_link"]}">View Call</a>')
    links_inline_html.append(f'{stats_graph_icon} <a href="{stats_url}">View Stats</a>')

    target_emoji_html = get_target_emoji_html()
    coin_emoji_html = get_coin_emoji_html()
    megaphone_emoji_html = get_megaphone_emoji_html()
    chain_emoji_html = get_chain_emoji_html(chain_str)

    joined_links = "  ".join(links_inline_html)

    tele_buttons, bot_markup = build_trade_buttons(call_data, bot_username)

    if is_dip and prev_entry_mc > 0:
        prev_entry_str = format_number(prev_entry_mc)
        dip_pct = ((prev_entry_mc - call_data['entry_mc']) / prev_entry_mc) * 100
        caption_html = (
            f"📉 <b>DIP RE-ENTRY DETECTED</b>\n\n"
            f"⚡ <b>Project:</b> {proj_link_html} (<code>${safe_t_sym}</code>)\n"
            f"{chain_emoji_html} <b>Network:</b> <b>{chain_str}</b>\n"
            f"📊 <b>Initial Entry:</b> <b>{prev_entry_str}</b>\n"
            f"📉 <b>Dip Entry MC:</b> <b>{entry_str}</b> (<b>-{dip_pct:.0f}% Dip</b>)\n"
            f"{megaphone_emoji_html} <b>Called By:</b> {ch_link_html}\n\n"
            f"📋 <b>Contract:</b>\n"
            f"<code>{token_address}</code>\n\n"
            f"<i>Tap contract address above to copy</i>"
        )
    else:
        caption_html = (
            f"{target_emoji_html} <b>ALPHA DETECTED</b>\n\n"
            f"⚡ <b>Project:</b> {proj_link_html} (<code>${safe_t_sym}</code>)\n"
            f"{chain_emoji_html} <b>Network:</b> <b>{chain_str}</b>\n"
            f"{entry_graph_icon} <b>Entry Market Cap:</b> <b>{entry_str}</b>\n"
            f"{megaphone_emoji_html} <b>Called By:</b> {ch_link_html}\n\n"
            f"📋 <b>Contract:</b>\n"
            f"<code>{token_address}</code>\n\n"
            f"<i>Tap contract address above to copy</i>"
        )

    if is_dip:
        custom_media = (
            await get_custom_media("dip")
            or await get_custom_media("diprecall")
            or await get_custom_media("newcall")
            or await get_custom_media("call")
            or await get_custom_media("trending")
            or await get_custom_media("recently_called")
        )
    else:
        custom_media = (
            await get_custom_media("newcall")
            or await get_custom_media("call")
            or await get_custom_media("trending")
            or await get_custom_media("recently_called")
        )
    alert_target = int(alert_ch) if str(alert_ch).lstrip("-").isdigit() else alert_ch

    active_client = telethon_client or (_global_scraper.client if _global_scraper else None)
    sent_via_userbot = False
    if active_client and active_client.is_connected():
        try:
            target_entity = await safe_get_telethon_entity(active_client, alert_ch)
            file_id = custom_media.get("file_id") if custom_media else None
            cached_media = _telethon_media_cache.get(file_id) if file_id else None

            media_buf = None
            if not cached_media:
                media_buf = await resolve_media_buffer(custom_media, None, bot_app)

            clean_caption, tele_entities = parse_html_with_custom_emojis(caption_html)
            emoji_entities = [e for e in tele_entities if type(e).__name__ == "MessageEntityCustomEmoji"]
            emoji_entities_count = len(emoji_entities)
            alert_type_name = "Dip Re-Call Alert" if is_dip else "New Call Alert"

            logger.info("=" * 60)
            logger.info(f"🎨 [3D EMOJI AUDIT] Preparing {alert_type_name} for '{safe_t_name}' (${safe_t_sym}) | Chain: {chain_str}")
            logger.info(f"🎨 [3D EMOJI AUDIT] Total 3D Custom Emojis Extracted: {emoji_entities_count}")
            for idx, ent in enumerate(emoji_entities, start=1):
                logger.info(f"   ├─ Emoji #{idx}: Document ID: {ent.document_id} (Offset: {ent.offset}, Length: {ent.length})")

            is_gif = custom_media and custom_media.get("media_type") == "animation"
            file_attrs = [DocumentAttributeAnimated(), DocumentAttributeVideo(duration=0, w=500, h=500, supports_streaming=True)] if is_gif else None
            file_to_send = cached_media or media_buf

            if file_to_send:
                if media_buf and not cached_media:
                    media_buf.seek(0)
                try:
                    sent_msg = await active_client.send_file(
                        entity=target_entity,
                        file=file_to_send,
                        caption=clean_caption,
                        formatting_entities=tele_entities,
                        attributes=file_attrs,
                        buttons=tele_buttons
                    )
                except Exception:
                    if media_buf:
                        media_buf.seek(0)
                    sent_msg = await active_client.send_file(
                        entity=target_entity,
                        file=file_to_send,
                        caption=clean_caption,
                        formatting_entities=tele_entities,
                        attributes=file_attrs
                    )
                if file_id and sent_msg and getattr(sent_msg, "media", None):
                    _telethon_media_cache[file_id] = sent_msg.media
                    _trim_dict(_telethon_media_cache, max_size=20)
            else:
                try:
                    sent_msg = await active_client.send_message(
                        entity=target_entity,
                        message=clean_caption,
                        formatting_entities=tele_entities,
                        link_preview=False,
                        buttons=tele_buttons
                    )
                except Exception:
                    sent_msg = await active_client.send_message(
                        entity=target_entity,
                        message=clean_caption,
                        formatting_entities=tele_entities,
                        link_preview=False
                    )
            sent_via_userbot = True
            logger.info(f"✨ [3D EMOJI AUDIT] ✅ SUCCESS! Posted new call alert via Telethon Premium Userbot to {alert_ch} with {emoji_entities_count} live 3D custom animated emojis!")
            logger.info("=" * 60)
        except Exception as ub_err:
            logger.warning(f"⚠️ [3D EMOJI AUDIT] ❌ Telethon Userbot dispatch failed ({ub_err}). Falling back to Bot API (Emojis will appear plain due to Telegram Bot API restrictions)...", exc_info=True)
    else:
        status_msg = "Client is None" if not active_client else "Client is disconnected"
        logger.warning(f"⚠️ [3D EMOJI AUDIT] Telethon Userbot NOT active ({status_msg}). Falling back to Bot API (Plain Emojis)...")

    if not sent_via_userbot:
        try:
            if custom_media:
                media_type = custom_media.get("media_type")
                file_id = custom_media.get("file_id")
                if media_type == "animation":
                    await bot_app.bot.send_animation(chat_id=alert_target, animation=file_id, caption=caption_html, parse_mode="HTML")
                else:
                    await bot_app.bot.send_photo(chat_id=alert_target, photo=file_id, caption=caption_html, parse_mode="HTML")
            else:
                await bot_app.bot.send_message(chat_id=alert_target, text=caption_html, parse_mode="HTML", disable_web_page_preview=True)
            logger.info(f"📢 [NEW CALL BOT API SUCCESS] Posted new call alert for call #{call_data.get('id')} to alert channel {alert_ch} via @kolinsightsbot (Plain Unicode Fallback)")
        except Exception as e:
            logger.warning(f"❌ [NEW CALL BOT API FAILED] Failed to post new call alert to chat {alert_ch}: {e}")

async def send_spotlight_report(token_data: dict, bot_app, chat_id=None):
    """Sends Trending Spotlight report matching Image 1 layout."""
    if not BOT_TOKEN or not bot_app:
        return

    from bot_handlers import clean_markdown
    from config import get_effective_alert_channel_id
    from graphics import format_number
    from database import get_custom_media

    bot_info = await bot_app.bot.get_me() if (bot_app and bot_app.bot) else None
    bot_username = bot_info.username if (bot_info and bot_info.username) else "kolinsightsbot"

    t_name = clean_markdown(token_data.get("name", "V4"))
    safe_t_name = t_name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    token_addr = token_data.get("address", "")
    mc_str = format_number(token_data.get("market_cap", 2900000))
    call_count = token_data.get("call_count", 8)
    slogan = token_data.get("slogan", "Rise to financial heights")

    view_calls_url = f"https://t.me/{bot_username}?start=token_{token_addr}"
    leaderboard_url = f"https://t.me/{bot_username}?start=trending"

    eye_icon = get_eye_emoji_html()
    graph_icon = get_graph_emoji_html()

    caption_html = (
        f"{eye_icon} <b>Trending Spotlight:</b>\n\n"
        f"<b>{safe_t_name}</b>\n\n"
        f"{graph_icon} <b>Marketcap:</b> {mc_str}\n\n"
        f"<b>Total Calls:</b> {call_count} {eye_icon} <a href=\"{view_calls_url}\">View Calls</a>\n\n"
        f"<i>'{slogan}'</i>\n\n"
        f"💸 <b>See our <a href=\"{leaderboard_url}\">KOLInsights Trending Leaderboard</a> for more projects!</b>"
    )

    alert_ch = chat_id or await get_effective_alert_channel_id()
    if not alert_ch:
        return

    custom_media = await get_custom_media("spotlight") or await get_custom_media("trending")
    alert_target = int(alert_ch) if str(alert_ch).lstrip("-").isdigit() else alert_ch

    sent_via_userbot = False
    if _global_scraper and _global_scraper.client and _global_scraper.client.is_connected():
        try:
            media_buf = await resolve_media_buffer(custom_media, None, bot_app)
            is_gif = custom_media and custom_media.get("media_type") == "animation"
            file_attrs = [DocumentAttributeAnimated(), DocumentAttributeVideo(duration=0, w=500, h=500, supports_streaming=True)] if is_gif else None

            if media_buf:
                media_buf.seek(0)
                await _global_scraper.client.send_file(entity=alert_target, file=media_buf, caption=caption_html, parse_mode="html", attributes=file_attrs)
            else:
                await _global_scraper.client.send_message(entity=alert_target, message=caption_html, parse_mode="html", link_preview=False)
            sent_via_userbot = True
        except Exception as ub_err:
            logger.warning(f"Failed to post spotlight report via Telethon Userbot ({ub_err}), falling back to Bot API...")

    if not sent_via_userbot:
        try:
            if custom_media:
                media_type = custom_media.get("media_type")
                file_id = custom_media.get("file_id")
                if media_type == "animation":
                    await bot_app.bot.send_animation(chat_id=alert_target, animation=file_id, caption=caption_html, parse_mode="HTML")
                else:
                    await bot_app.bot.send_photo(chat_id=alert_target, photo=file_id, caption=caption_html, parse_mode="HTML")
            else:
                await bot_app.bot.send_message(chat_id=alert_target, text=caption_html, parse_mode="HTML", disable_web_page_preview=True)
            logger.info(f"Posted spotlight report to chat {alert_target}")
        except Exception as e:
            logger.warning(f"Failed to post spotlight report to chat {alert_target}: {e}")

async def send_daily_leaderboard_report(bot_app, chat_id=None):
    """Sends Daily 24h Alpha & Trending Leaderboard Report with KOL of the Day Spotlight."""
    if not BOT_TOKEN or not bot_app:
        return

    from bot_handlers import clean_markdown
    from config import get_effective_alert_channel_id
    from database import get_daily_leaderboard_stats, get_custom_media
    from graphics import format_number

    bot_info = await bot_app.bot.get_me() if (bot_app and bot_app.bot) else None
    bot_username = bot_info.username if (bot_info and bot_info.username) else "kolinsightsbot"

    stats = await get_daily_leaderboard_stats()
    top_coins = stats.get("top_coins", [])
    top_callers = stats.get("top_callers", [])
    kol_of_day = stats.get("kol_of_day")
    top_call_info = stats.get("top_call_info")

    RANK_EMOJI_IDS = {
        0: RANK_1_EMOJI_ID,
        1: RANK_2_EMOJI_ID,
        2: RANK_3_EMOJI_ID,
        3: RANK_4_EMOJI_ID,
        4: RANK_5_EMOJI_ID,
    }
    DEFAULT_RANKS = ["👑", "🥈", "🥉", "4️⃣", "5️⃣"]

    def get_rank_emoji_html(idx: int) -> str:
        eid = RANK_EMOJI_IDS.get(idx)
        if eid and str(eid).isdigit():
            fallback = DEFAULT_RANKS[idx] if idx < len(DEFAULT_RANKS) else "🔹"
            return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'
        return DEFAULT_RANKS[idx] if idx < len(DEFAULT_RANKS) else "🔹"

    crown_icon = f'<tg-emoji emoji-id="{RANK_1_EMOJI_ID}">👑</tg-emoji>' if RANK_1_EMOJI_ID else "👑"
    diamond_icon = '<tg-emoji emoji-id="5427168083074628963">💎</tg-emoji>'
    graph_icon = get_graph_emoji_html()
    fire_icon = '<tg-emoji emoji-id="5420315771991497307">🔥</tg-emoji>'

    lines = [
        f"🌟 <b>KOLINSIGHTS DAILY ALPHA RECAP</b> 🌟\n"
    ]

    # Special Spotlight for KOL of the Day & Best Call
    if kol_of_day:
        c_title = clean_markdown(kol_of_day.get('channel_title') or "Unknown")
        c_user = kol_of_day.get('channel_username')
        c_link = f'<a href="https://t.me/{c_user}">@{clean_markdown(c_user)}</a>' if c_user else f'<b>{c_title}</b>'
        tot_calls = kol_of_day.get('total_calls', 0)
        win_calls = kol_of_day.get('win_calls', 0)
        best_mult = kol_of_day.get('best_mult', 1.0)

        lines.append(f"{crown_icon} <b>KOL OF THE DAY:</b> {c_link}")
        if top_call_info:
            t_name = clean_markdown(top_call_info.get('token_name') or "")
            t_sym = clean_markdown(top_call_info.get('token_symbol') or "")
            t_addr = top_call_info.get('token_address', '')
            t_link = f'<a href="https://t.me/{bot_username}?start=token_{t_addr}"><b>{t_name}</b> (<code>${t_sym}</code>)</a>'
            e_mc = format_number(top_call_info.get('entry_mc', 0))
            a_mc = format_number(top_call_info.get('ath_mc', 0))
            mult_val = top_call_info.get('highest_multiplier', 1.0)
            lines.append(f"{diamond_icon} <b>Top Alpha Call:</b> {t_link} | <b>x{mult_val:.1f}</b> 🚀")
            lines.append(f"   ├─ Entry: <b>{e_mc}</b> ➔ ATH: <b>{a_mc}</b>")
        lines.append(f"📈 <b>Daily Record:</b> <b>{tot_calls} Calls</b> ({win_calls} Smashed 2x+ | Best: <b>x{best_mult:.1f}</b>)\n")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

    # Top Trending Coins in 24h
    lines.append(f"\n{graph_icon} <b>TOP TRENDING PROJECTS TODAY:</b>")
    if top_coins:
        for idx, item in enumerate(top_coins):
            rank_html = get_rank_emoji_html(idx)
            t_name = clean_markdown(item['token_name'])
            t_sym = clean_markdown(item['token_symbol'])
            t_addr = item.get("token_address", "")
            max_m = item.get("max_mult", 1.0)
            project_link = f'https://t.me/{bot_username}?start=token_{t_addr}' if t_addr else '#'
            mult_badge = f" | <b>x{max_m:.1f}</b>" if max_m >= 2.0 else ""
            lines.append(f'{rank_html} <a href="{project_link}"><b>{t_name}</b> (<code>${t_sym}</code>)</a> | <code>{item["call_count"]} KOLs</code>{mult_badge}')
    else:
        lines.append("• <i>No calls recorded in the last 24 hours.</i>")

    # Top 3 Callers Today
    if top_callers:
        lines.append(f"\n{fire_icon} <b>TOP 3 CALLERS TODAY:</b>")
        for idx, c in enumerate(top_callers[:3]):
            rank_html = get_rank_emoji_html(idx)
            c_name = clean_markdown(c.get('channel_title') or "Unknown")
            c_u = c.get('channel_username')
            c_url = f'https://t.me/{c_u}' if c_u else '#'
            t_calls = c.get('total_calls', 0)
            b_mult = c.get('best_mult', 1.0)
            lines.append(f'{rank_html} <a href="{c_url}"><b>{c_name}</b></a> — <b>{t_calls} Calls</b> (Best: <b>x{b_mult:.1f}</b>)')

    lines.extend([
        "\n📢 <i>Click any project or KOL to inspect full analytics!</i>\n",
        "🔊 <b>Ad - KOLInsights</b>",
        '<a href="https://t.me/kolinsights_admin">Want your ad here? Contact @kolinsights_admin</a>'
    ])

    caption_html = "\n".join(lines)
    custom_media = await get_custom_media("daily") or await get_custom_media("trending")

    logger.info("=" * 60)
    logger.info("🌟 [DAILY ALPHA AUDIT - STAGE 1: DATA PREPARATION]")
    logger.info(f"   ├─ Top Trending Projects: {len(top_coins)} found")
    logger.info(f"   ├─ Top Callers: {len(top_callers)} found")
    if kol_of_day:
        logger.info(f"   ├─ KOL OF THE DAY: {kol_of_day.get('channel_title')} (@{kol_of_day.get('channel_username')})")
        if top_call_info:
            logger.info(f"   │   └─ Best Call: {top_call_info.get('token_name')} (${top_call_info.get('token_symbol')}) | x{top_call_info.get('highest_multiplier', 1.0):.1f}")
    media_label = "Loaded custom GIF/Photo" if custom_media else "No custom media (Text-only)"
    logger.info(f"   └─ Custom Media Asset: {media_label}")

    target_chats = set()
    if chat_id:
        target_chats.add(chat_id)
    else:
        alert_ch = await get_effective_alert_channel_id()
        if alert_ch:
            try:
                target_chats.add(int(alert_ch))
            except ValueError:
                target_chats.add(alert_ch)

    sent_via_userbot = False
    if _global_scraper and _global_scraper.client and _global_scraper.client.is_connected():
        try:
            media_buf = await resolve_media_buffer(custom_media, None, bot_app)
            is_gif = custom_media and custom_media.get("media_type") == "animation"
            file_attrs = [DocumentAttributeAnimated(), DocumentAttributeVideo(duration=0, w=500, h=500, supports_streaming=True)] if is_gif else None

            clean_caption, tele_entities = parse_html_with_custom_emojis(caption_html)
            emoji_entities = [e for e in tele_entities if type(e).__name__ == "MessageEntityCustomEmoji"]
            
            logger.info("🌟 [DAILY ALPHA AUDIT - STAGE 2: MTPROTO DISPATCH]")
            logger.info(f"   ├─ Total 3D Custom Emojis Extracted: {len(emoji_entities)}")
            logger.info(f"   └─ Target Alert Channels: {list(target_chats)}")

            for chat in target_chats:
                try:
                    target_entity = await safe_get_telethon_entity(_global_scraper.client, chat)
                    sent_via_userbot = True
                    if media_buf:
                        media_buf.seek(0)
                        await _global_scraper.client.send_file(
                            entity=target_entity,
                            file=media_buf,
                            caption=clean_caption,
                            formatting_entities=tele_entities,
                            attributes=file_attrs
                        )
                    else:
                        await _global_scraper.client.send_message(
                            entity=target_entity,
                            message=clean_caption,
                            formatting_entities=tele_entities,
                            link_preview=False
                        )
                    logger.info(f"✨ [DAILY ALPHA AUDIT - STAGE 3: SUCCESS] Posted Daily Leaderboard via Telethon Userbot to {chat}")
                except Exception as chat_err:
                    logger.warning(f"⚠️ Userbot send to chat {chat} failed: {chat_err}")
            logger.info("=" * 60)
        except Exception as ub_err:
            logger.warning(f"⚠️ [DAILY ALPHA AUDIT] Userbot dispatch failed ({ub_err}). Falling back to Bot API...")

    if not sent_via_userbot:
        for chat in target_chats:
            try:
                if custom_media:
                    media_type = custom_media.get("media_type")
                    file_id = custom_media.get("file_id")
                    if media_type == "animation":
                        await bot_app.bot.send_animation(chat_id=chat, animation=file_id, caption=caption_html, parse_mode="HTML")
                    else:
                        await bot_app.bot.send_photo(chat_id=chat, photo=file_id, caption=caption_html, parse_mode="HTML")
                else:
                    await bot_app.bot.send_message(chat_id=chat, text=caption_html, parse_mode="HTML", disable_web_page_preview=True)
                logger.info(f"📢 [DAILY ALPHA AUDIT - STAGE 3: BOT API] Posted daily leaderboard report via Bot API to chat {chat}")
            except Exception as e:
                logger.warning(f"❌ Failed to post daily leaderboard report to chat {chat}: {e}")

async def send_weekly_leaderboard_report(bot_app, chat_id=None):
    """Sends Weekly 7-Day KOL Championship Leaderboard Report with Best Caller & Best Coin."""
    if not BOT_TOKEN or not bot_app:
        return

    from bot_handlers import clean_markdown
    from config import get_effective_alert_channel_id
    from database import get_weekly_leaderboard_stats, get_custom_media
    from graphics import format_number

    bot_info = await bot_app.bot.get_me() if (bot_app and bot_app.bot) else None
    bot_username = bot_info.username if (bot_info and bot_info.username) else "kolinsightsbot"

    stats = await get_weekly_leaderboard_stats()
    weekly_callers = stats.get("weekly_callers", [])
    best_call_7d = stats.get("best_call_7d")
    kol_of_week = stats.get("kol_of_week")
    champion_top_call = stats.get("champion_top_call")

    RANK_EMOJI_IDS = {
        0: RANK_1_EMOJI_ID,
        1: RANK_2_EMOJI_ID,
        2: RANK_3_EMOJI_ID,
        3: RANK_4_EMOJI_ID,
        4: RANK_5_EMOJI_ID,
    }
    DEFAULT_RANKS = ["👑", "🥈", "🥉", "4️⃣", "5️⃣"]

    def get_rank_emoji_html(idx: int) -> str:
        eid = RANK_EMOJI_IDS.get(idx)
        if eid and str(eid).isdigit():
            fallback = DEFAULT_RANKS[idx] if idx < len(DEFAULT_RANKS) else "🔹"
            return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'
        return DEFAULT_RANKS[idx] if idx < len(DEFAULT_RANKS) else "🔹"

    crown_icon = f'<tg-emoji emoji-id="{RANK_1_EMOJI_ID}">👑</tg-emoji>' if RANK_1_EMOJI_ID else "👑"
    diamond_icon = '<tg-emoji emoji-id="5427168083074628963">💎</tg-emoji>'
    rocket_icon = '<tg-emoji emoji-id="5445284980978621387">🚀</tg-emoji>'
    target_icon = '<tg-emoji emoji-id="5256131095094652290">🎯</tg-emoji>'

    lines = [
        f"🏆 <b>WEEKLY KOL CHAMPIONSHIP LEADERBOARD</b> 🏆\n"
    ]

    # KOL of the Week Spotlight
    if kol_of_week:
        c_title = clean_markdown(kol_of_week.get('channel_title') or "Unknown")
        c_user = kol_of_week.get('channel_username')
        c_link = f'<a href="https://t.me/{c_user}">@{clean_markdown(c_user)}</a>' if c_user else f'<b>{c_title}</b>'
        tot_calls = kol_of_week.get('total_calls', 0)
        win_calls = kol_of_week.get('win_calls', 0)
        win_rate = (win_calls / tot_calls) * 100 if tot_calls > 0 else 0
        best_mult = kol_of_week.get('best_mult', 1.0)

        lines.append(f"{crown_icon} <b>KOL OF THE WEEK:</b> {c_link}")
        if champion_top_call:
            t_name = clean_markdown(champion_top_call.get('token_name') or "")
            t_sym = clean_markdown(champion_top_call.get('token_symbol') or "")
            t_addr = champion_top_call.get('token_address', '')
            t_link = f'<a href="https://t.me/{bot_username}?start=token_{t_addr}"><b>{t_name}</b> (<code>${t_sym}</code>)</a>'
            lines.append(f"{rocket_icon} <b>Biggest Runner:</b> {t_link} | <b>x{best_mult:.1f} Milestone</b>")
        lines.append(f"📊 <b>Weekly Record:</b> <b>{tot_calls} Calls</b> | <b>{win_rate:.1f}% Consistency</b>\n")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

    # Weekly Top 5 Callers Podium
    if weekly_callers:
        lines.append(f"\n🏅 <b>WEEKLY PODIUM (TOP 5 CALLERS):</b>")
        for idx, c in enumerate(weekly_callers[:5]):
            rank_html = get_rank_emoji_html(idx)
            c_name = clean_markdown(c.get('channel_title') or "Unknown")
            c_u = c.get('channel_username')
            c_url = f'https://t.me/{c_u}' if c_u else '#'
            t_calls = c.get('total_calls', 0)
            w_calls = c.get('win_calls', 0)
            w_rate = (w_calls / t_calls) * 100 if t_calls > 0 else 0
            b_mult = c.get('best_mult', 1.0)
            lines.append(f'{rank_html} <a href="{c_url}"><b>{c_name}</b></a> — <b>{t_calls} Calls</b> | <code>{w_rate:.0f}% Win</code> | Best: <b>x{b_mult:.1f}</b>')

    # Best Single Call of the Week
    if best_call_7d:
        caller_n = clean_markdown(best_call_7d.get('channel_title') or "Unknown")
        caller_u = best_call_7d.get('channel_username')
        caller_l = f'<a href="https://t.me/{caller_u}">@{clean_markdown(caller_u)}</a>' if caller_u else f'<b>{caller_n}</b>'
        tok_n = clean_markdown(best_call_7d.get('token_name') or "")
        tok_s = clean_markdown(best_call_7d.get('token_symbol') or "")
        tok_addr = best_call_7d.get('token_address', '')
        tok_link = f'<a href="https://t.me/{bot_username}?start=token_{tok_addr}"><b>{tok_n}</b> (<code>${tok_s}</code>)</a>'
        top_mult = best_call_7d.get('highest_multiplier', 1.0)
        lines.append(f"\n{diamond_icon} <b>BEST CALL OF THE WEEK:</b> {tok_link} by {caller_l} (<b>x{top_mult:.1f}</b> 🚀)")

    lines.extend([
        f'\n{target_icon} <i>"Godmode activated, legends never die !"</i>\n',
        "🔊 <b>Ad - KOLInsights</b>",
        '<a href="https://t.me/kolinsights_admin">Want your ad here? Contact @kolinsights_admin</a>'
    ])

    caption_html = "\n".join(lines)
    custom_media = await get_custom_media("weekly") or await get_custom_media("champion") or await get_custom_media("trending")

    logger.info("=" * 60)
    logger.info("🏆 [WEEKLY CHAMPION AUDIT - STAGE 1: DATA PREPARATION]")
    logger.info(f"   ├─ Weekly Ranked Callers: {len(weekly_callers)} channels")
    if kol_of_week:
        logger.info(f"   ├─ KOL OF THE WEEK: {kol_of_week.get('channel_title')} (@{kol_of_week.get('channel_username')})")
        if champion_top_call:
            logger.info(f"   │   └─ Champion Top Runner: {champion_top_call.get('token_name')} (${champion_top_call.get('token_symbol')}) | x{champion_top_call.get('highest_multiplier', 1.0):.1f}")
    if best_call_7d:
        logger.info(f"   ├─ Best Single Call 7d: {best_call_7d.get('token_name')} by {best_call_7d.get('channel_title')} | x{best_call_7d.get('highest_multiplier', 1.0):.1f}")
    media_label = "Loaded custom GIF/Photo" if custom_media else "No custom media (Text-only)"
    logger.info(f"   └─ Custom Media Asset: {media_label}")

    target_chats = set()
    if chat_id:
        target_chats.add(chat_id)
    else:
        alert_ch = await get_effective_alert_channel_id()
        if alert_ch:
            try:
                target_chats.add(int(alert_ch))
            except ValueError:
                target_chats.add(alert_ch)

    sent_via_userbot = False
    if _global_scraper and _global_scraper.client and _global_scraper.client.is_connected():
        try:
            media_buf = await resolve_media_buffer(custom_media, None, bot_app)
            is_gif = custom_media and custom_media.get("media_type") == "animation"
            file_attrs = [DocumentAttributeAnimated(), DocumentAttributeVideo(duration=0, w=500, h=500, supports_streaming=True)] if is_gif else None

            clean_caption, tele_entities = parse_html_with_custom_emojis(caption_html)
            emoji_entities = [e for e in tele_entities if type(e).__name__ == "MessageEntityCustomEmoji"]

            logger.info("🏆 [WEEKLY CHAMPION AUDIT - STAGE 2: MTPROTO DISPATCH]")
            logger.info(f"   ├─ Total 3D Custom Emojis Extracted: {len(emoji_entities)}")
            logger.info(f"   └─ Target Alert Channels: {list(target_chats)}")

            for chat in target_chats:
                try:
                    target_entity = await safe_get_telethon_entity(_global_scraper.client, chat)
                    sent_via_userbot = True
                    if media_buf:
                        media_buf.seek(0)
                        await _global_scraper.client.send_file(
                            entity=target_entity,
                            file=media_buf,
                            caption=clean_caption,
                            formatting_entities=tele_entities,
                            attributes=file_attrs
                        )
                    else:
                        await _global_scraper.client.send_message(
                            entity=target_entity,
                            message=clean_caption,
                            formatting_entities=tele_entities,
                            link_preview=False
                        )
                    logger.info(f"✨ [WEEKLY CHAMPION AUDIT - STAGE 3: SUCCESS] Posted Weekly Leaderboard via Telethon Userbot to {chat}")
                except Exception as chat_err:
                    logger.warning(f"⚠️ Userbot send to chat {chat} failed: {chat_err}")
            logger.info("=" * 60)
        except Exception as ub_err:
            logger.warning(f"⚠️ [WEEKLY CHAMPION AUDIT] Userbot dispatch failed ({ub_err}). Falling back to Bot API...")

    if not sent_via_userbot:
        for chat in target_chats:
            try:
                if custom_media:
                    media_type = custom_media.get("media_type")
                    file_id = custom_media.get("file_id")
                    if media_type == "animation":
                        await bot_app.bot.send_animation(chat_id=chat, animation=file_id, caption=caption_html, parse_mode="HTML")
                    else:
                        await bot_app.bot.send_photo(chat_id=chat, photo=file_id, caption=caption_html, parse_mode="HTML")
                else:
                    await bot_app.bot.send_message(chat_id=chat, text=caption_html, parse_mode="HTML", disable_web_page_preview=True)
                logger.info(f"📢 [WEEKLY CHAMPION AUDIT - STAGE 3: BOT API] Posted weekly leaderboard report via Bot API to chat {chat}")
            except Exception as e:
                logger.warning(f"❌ Failed to post weekly leaderboard report to chat {chat}: {e}")

async def send_recently_called_report(bot_app, chat_id=None):
    """Backwards compatibility alias for send_daily_leaderboard_report."""
    await send_daily_leaderboard_report(bot_app, chat_id=chat_id)

async def periodic_trending_poster(bot_app):
    """Background task that fires Daily 24h & Sunday Weekly Leaderboard Reports at exactly 00:00 UTC."""
    from datetime import datetime, timezone, timedelta
    logger.info("⏰ [LEADERBOARD SCHEDULER] Precision Wall-Clock Target: Daily at 00:00 UTC (Sunday for Weekly).")
    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            target_today = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            if now_utc >= target_today:
                next_run = target_today + timedelta(days=1)
            else:
                next_run = target_today

            sleep_seconds = max(1.0, (next_run - now_utc).total_seconds())
            hours, remainder = divmod(int(sleep_seconds), 3600)
            minutes, _ = divmod(remainder, 60)
            logger.info(f"⏰ [LEADERBOARD SCHEDULER] Next 00:00 UTC report scheduled in {hours}h {minutes}m (at {next_run.strftime('%Y-%m-%d %H:%M:%S')} UTC).")

            await asyncio.sleep(sleep_seconds)

            current_utc = datetime.now(timezone.utc)
            logger.info(f"⏰ [LEADERBOARD SCHEDULER] 00:00 UTC Reached! Triggering Daily 24h Alpha Recap ({current_utc.strftime('%Y-%m-%d')})...")
            await send_daily_leaderboard_report(bot_app)

            # If Sunday (weekday 6), also fire the Weekly Championship Leaderboard
            if current_utc.weekday() == 6:
                logger.info("🏆 [LEADERBOARD SCHEDULER] Sunday detected! Triggering Weekly Championship Leaderboard...")
                await asyncio.sleep(5)
                await send_weekly_leaderboard_report(bot_app)

            await asyncio.sleep(60)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic_trending_poster loop: {e}")
            await asyncio.sleep(60)

async def send_owner_dms_with_receipt(bot_app, owners: list, message_text: str, c_user: str = "") -> str:
    """Attempts to deliver reminder to channel owners in private DM and returns a detailed receipt status."""
    if not owners:
        return f"⚠️ *No owner linked in DB (use `/linkowner {c_user} @username`)*"

    receipts = []
    for o in owners:
        target = o
        if str(o).lstrip("-").isdigit():
            target = int(o)
        try:
            await bot_app.bot.send_message(chat_id=target, text=message_text, parse_mode="Markdown")
            receipts.append(f"✅ Sent to `{o}`")
        except Exception as dm_err:
            err_str = str(dm_err).lower()
            if "blocked" in err_str:
                receipts.append(f"❌ `{o}`: User has blocked the bot")
            elif "not found" in err_str or "chat not found" in err_str:
                receipts.append(f"❌ `{o}`: User hasn't started the bot in DM")
            else:
                receipts.append(f"❌ `{o}`: {str(dm_err)[:40]}")
    return "\n".join([f"• {r}" for r in receipts])

async def periodic_subscription_checker(bot_app, interval_seconds: int = 21600):
    """Background task that checks subscription expiration and sends private 7d/3d reminders."""
    from datetime import datetime, timezone
    from config import ADMIN_IDS
    from database import get_all_channels, update_channel_reminded, set_channel_sub_type
    from bot_handlers import clean_markdown

    logger.info(f"Periodic subscription checker task started (interval: {interval_seconds}s).")
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            channels = await get_all_channels()
            now_dt = datetime.now(timezone.utc)

            for ch in channels:
                sub_type = (ch.get("sub_type") or "VIP").upper()
                if sub_type in ("VIP", "LIFETIME"):
                    continue

                sub_end_str = ch.get("sub_end")
                if not sub_end_str:
                    continue

                try:
                    end_dt = datetime.strptime(str(sub_end_str)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    diff = end_dt - now_dt
                    days_remaining = diff.total_seconds() / 86400.0

                    c_title = clean_markdown(ch.get("title") or "Unknown Channel")
                    c_user = f"@{clean_markdown(ch['username'])}" if ch.get("username") else f"ID: `{ch['channel_id']}`"
                    
                    from database import get_channel_owners
                    owners = await get_channel_owners(ch.get("username") or ch.get("channel_id"))

                    # 3-Day Warning (between 2.0 and 3.5 days remaining)
                    if 2.0 <= days_remaining <= 3.5 and not ch.get("reminded_3d"):
                        owner_msg = (
                            f"🔔 **KOLInsights Subscription Reminder**\n\n"
                            f"📢 **Channel:** {c_title}\n"
                            f"⏳ **Time Remaining:** **3 Days Left** (*{end_dt.strftime('%b %d, %Y')}*)\n\n"
                            f"To ensure your channel's **live 2x/3x/5x/10x milestone cards** and tracking continue without interruption, please renew your subscription.\n\n"
                            f"💳 **Price:** `0.01 ETH / month`\n"
                            f"👉 Click **💳 Renew Subscription** in @kolinsightsbot or contact **@kolinsights_admin**!"
                        )
                        # Deliver to owner(s) and capture delivery status
                        delivery_receipt = await send_owner_dms_with_receipt(bot_app, owners, owner_msg, c_user)

                        admin_msg = (
                            f"🔔 **Subscription Notice: 3 Days Remaining**\n\n"
                            f"📢 **Channel:** {c_title} ({c_user})\n"
                            f"⏳ **Status:** Expiring in **3 Days** (*{end_dt.strftime('%b %d, %Y')}*)\n"
                            f"💳 **Renewal Fee:** `0.01 ETH / month`\n\n"
                            f"📬 **Owner DM Delivery Status:**\n{delivery_receipt}\n\n"
                            f"💡 *Contact @kolinsights_admin to process renewal or grant VIP status.*"
                        )
                        # Notify Bot Admins via Private DM
                        if bot_app and ADMIN_IDS:
                            for admin_id in ADMIN_IDS:
                                try:
                                    await bot_app.bot.send_message(chat_id=admin_id, text=admin_msg, parse_mode="Markdown")
                                except Exception as err:
                                    logger.warning(f"Could not send 3d sub reminder to admin {admin_id} for {c_title}: {err}")

                        await update_channel_reminded(ch["channel_id"], "reminded_3d", 1)

                    # 1-Day Urgent Final Notice (between 0.1 and 1.5 days remaining)
                    elif 0.1 <= days_remaining <= 1.5 and not ch.get("reminded_7d"):
                        owner_msg = (
                            f"🚨 **FINAL REMINDER: 24 Hours Left on Your Trial!**\n\n"
                            f"📢 **Channel:** {c_title}\n"
                            f"⏳ **Status:** Expiring in **24 Hours**\n\n"
                            f"Milestone card generation and call tracking will pause when the trial ends.\n\n"
                            f"💳 **Renewal:** `0.01 ETH / month`\n"
                            f"👉 Click **💳 Renew Subscription** in @kolinsightsbot or contact **@kolinsights_admin** to extend!"
                        )
                        # Deliver to owner(s) and capture delivery status
                        delivery_receipt = await send_owner_dms_with_receipt(bot_app, owners, owner_msg, c_user)

                        admin_msg = (
                            f"🚨 **URGENT: Subscription Notice (24 Hours Left)!**\n\n"
                            f"📢 **Channel:** {c_title} ({c_user})\n"
                            f"⏳ **Time Remaining:** **1 Day Left** (*{end_dt.strftime('%b %d, %Y')}*)\n"
                            f"💳 **Renewal Fee:** `0.01 ETH / month`\n\n"
                            f"📬 **Owner DM Delivery Status:**\n{delivery_receipt}\n\n"
                            f"⚠️ *Tracking will automatically be paused tomorrow if unpaid.*"
                        )
                        # Notify Bot Admins via Private DM
                        if bot_app and ADMIN_IDS:
                            for admin_id in ADMIN_IDS:
                                try:
                                    await bot_app.bot.send_message(chat_id=admin_id, text=admin_msg, parse_mode="Markdown")
                                except Exception as err:
                                    logger.warning(f"Could not send 1d sub reminder to admin {admin_id} for {c_title}: {err}")

                        await update_channel_reminded(ch["channel_id"], "reminded_7d", 1)

                    # Expired Notice
                    elif days_remaining <= 0 and sub_type != "EXPIRED":
                        msg = (
                            f"🛑 **Channel Tracking Paused (Trial / Subscription Expired)**\n\n"
                            f"📢 **Channel:** {c_title} ({c_user})\n"
                            f"🔴 **Status:** `EXPIRED`\n\n"
                            f"Tracking and milestone alerts for this channel have been paused. Contact @kolinsights_admin to reactivate."
                        )
                        # Deliver to owner(s) and capture delivery status
                        delivery_receipt = await send_owner_dms_with_receipt(bot_app, owners, msg, c_user)

                        admin_msg = (
                            f"🛑 **Channel Tracking Paused (Trial / Subscription Expired)**\n\n"
                            f"📢 **Channel:** {c_title} ({c_user})\n"
                            f"🔴 **Status:** `EXPIRED`\n\n"
                            f"📬 **Owner DM Delivery Status:**\n{delivery_receipt}\n\n"
                            f"Tracking and milestone alerts for this channel have been paused."
                        )
                        # Notify Bot Admins via Private DM
                        if bot_app and ADMIN_IDS:
                            for admin_id in ADMIN_IDS:
                                try:
                                    await bot_app.bot.send_message(chat_id=admin_id, text=admin_msg, parse_mode="Markdown")
                                except Exception as err:
                                    logger.warning(f"Could not send expired notice to admin {admin_id} for {c_title}: {err}")

                        await set_channel_sub_type(ch["channel_id"], "EXPIRED")

                except Exception as parse_err:
                    logger.warning(f"Error checking sub expiry for channel {ch.get('title')}: {parse_err}")

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in periodic_subscription_checker loop: {e}")

async def init_sent_alerts():
    """Pre-loads recent call tokens from DB into _sent_new_call_keys on startup using lightweight query."""
    global _sent_new_call_keys
    try:
        from database import get_recent_call_keys
        call_rows = await get_recent_call_keys(days=7)
        for user_name, ch_id, token_addr in call_rows:
            raw_user = (user_name or "").lstrip("@").lower()
            caller_key = raw_user if raw_user else str(ch_id or "")
            t_addr = str(token_addr or "").lower().strip()
            if t_addr:
                _sent_new_call_keys.add(f"{caller_key}_{t_addr}")
                if ch_id:
                    _sent_new_call_keys.add(f"{ch_id}_{t_addr}")
        _trim_set(_sent_new_call_keys, max_size=2000)
        logger.info(f"Pre-loaded {len(_sent_new_call_keys)} call deduplication keys from database.")
    except Exception as e:
        logger.warning(f"Could not pre-load call deduplication keys: {e}")

def cleanup_duplicate_processes():
    """Scans running OS processes and kills any lingering duplicate instances of main.py to prevent session locks."""
    current_pid = os.getpid()
    parent_pid = getattr(os, "getppid", lambda: 0)()
    protected_pids = {current_pid, parent_pid, 0, 1, 2}
    killed_count = 0
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                pid = proc.info['pid']
                if pid in protected_pids:
                    continue
                cmdline = proc.info.get('cmdline') or []
                cmd_str = " ".join(cmdline).lower()
                if ("python" in cmd_str or "python3" in cmd_str) and "main.py" in cmd_str and not any(sh in cmd_str for sh in ["entrypoint", ".sh", "/bin/sh", "/bin/bash"]):
                    logger.warning(f"🧹 [PROCESS CLEANUP] Found lingering duplicate main.py process (PID: {pid}). Terminating...")
                    proc.kill()
                    killed_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
    except ImportError:
        import subprocess
        try:
            if os.name == 'posix':
                output = subprocess.check_output(["pgrep", "-f", "python.*main.py"]).decode('utf-8')
                pids = [int(p) for p in output.split() if p.isdigit() and int(p) not in protected_pids]
                for pid in pids:
                    logger.warning(f"🧹 [PROCESS CLEANUP] Found lingering duplicate main.py process (PID: {pid}). Terminating...")
                    os.kill(pid, 9)
                    killed_count += 1
        except Exception:
            pass

    if killed_count > 0:
        logger.info(f"✨ [PROCESS CLEANUP] Successfully terminated {killed_count} duplicate background main.py process(es). Session & DB locks cleared!")
    else:
        logger.info("✨ [PROCESS CLEANUP] Clean startup: No duplicate background main.py processes found.")

_bot_start_time = time.time()

async def periodic_system_heartbeat(interval_seconds: int = 60):
    """Logs a clean, prominent heartbeat status every 60 seconds with zero-memory SQL counts."""
    from database import get_all_channels, get_active_calls_count
    import gc
    heartbeat_count = 0
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            heartbeat_count += 1
            uptime_sec = int(time.time() - _bot_start_time)
            hours, remainder = divmod(uptime_sec, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{hours}h {minutes}m {seconds}s"

            channels = await get_all_channels()
            active_calls_count = await get_active_calls_count(days=14)

            ub_name = ""
            if _global_scraper and _global_scraper.client and _global_scraper.client.is_connected():
                try:
                    me = await _global_scraper.client.get_me()
                    if me:
                        ub_name = f" (@{me.username})" if me.username else f" ({me.first_name})"
                except Exception:
                    pass
                ub_status = f"ONLINE 🟢{ub_name}"
            else:
                ub_status = "DISCONNECTED 🔴"

            # Periodic GC every 5 minutes to guarantee low memory ceiling (<100MB)
            if heartbeat_count % 5 == 0:
                gc.collect()

            logger.info("=" * 60)
            logger.info(f"💓 [SYSTEM HEARTBEAT] KOLInsights Bot is 100% ALIVE & RUNNING | Uptime: {uptime_str}")
            logger.info(f"   ├─ Telethon Userbot: {ub_status}")
            logger.info(f"   ├─ Tracked Channels: {len(channels)} active channels")
            logger.info(f"   └─ Monitored Calls: {active_calls_count} calls (14-day window)")
            logger.info("=" * 60)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Heartbeat notice: {e}")

async def keep_alive_worker():
    """Pings the web service periodically to keep Render Free tier awake 24/7."""
    ping_url = os.getenv("KEEP_ALIVE_URL") or os.getenv("RENDER_EXTERNAL_URL")
    if not ping_url:
        return
    if not ping_url.startswith("http"):
        ping_url = f"https://{ping_url}"
    health_url = f"{ping_url.rstrip('/')}/health"
    logger.info(f"🔄 [KEEP-ALIVE] Auto self-ping active for Render: {health_url}")
    while True:
        try:
            await asyncio.sleep(600)  # Ping every 10 mins
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(health_url)
                logger.info(f"🔄 [KEEP-ALIVE] Render health ping response: {resp.status_code}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Keep-alive ping notice: {e}")

async def main():
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except Exception:
            pass

    from server import start_render_web_server, HealthStatus
    from config import PORT, BOT_TOKEN, KEEP_ALIVE_URL

    logger.info("🌐 [RENDER] Starting embedded HTTP Web Service for Health Checks...")
    try:
        start_render_web_server(port=PORT, bot_app=None, scraper=None)
        HealthStatus.startup_stage = "Web Server Listening"
    except Exception as srv_err:
        logger.warning(f"Could not start Render web server: {srv_err}")

    # Process cleanup on local machines only (bypass on Linux/Render containers to prevent /proc hangs)
    if os.name != 'posix' and not os.getenv("RENDER"):
        logger.info("🚀 [STEP 1/7] Cleaning up lingering local background processes...")
        try:
            cleanup_duplicate_processes()
        except Exception:
            pass
    else:
        logger.info("🚀 [STEP 1/7] Cloud container environment detected. Skipping local process cleanup.")

    # -------------------------------------------------------------
    # STEP 2: Initialize Telegram Bot API Command Handler IMMEDIATELY
    # -------------------------------------------------------------
    logger.info("🚀 [STEP 2/7] Initializing Telegram Bot API Command Handler...")
    HealthStatus.startup_stage = "Starting Telegram Bot"
    bot_app = None
    clean_bot_token = (BOT_TOKEN or "").strip(' "\'')
    if not clean_bot_token:
        logger.warning("⚠️ [BOT_TOKEN WARNING] BOT_TOKEN missing in environment! Interactive bot commands will not respond.")
        HealthStatus.bot_error = "BOT_TOKEN missing in environment"
        HealthStatus.bot_online = False
    else:
        try:
            bot_app = ApplicationBuilder().token(clean_bot_token).concurrent_updates(True).build()
            register_handlers(bot_app)
            await bot_app.initialize()
            await bot_app.start()
            await bot_app.updater.start_polling()
            logger.info("✅ [STEP 2/7 SUCCESS] Telegram Bot Polling active! (@kolinsightsbot commands /start, /checkstats, /status, /ping are operational).")
            HealthStatus.bot_app = bot_app
            HealthStatus.bot_online = True
            HealthStatus.bot_error = None
        except Exception as bot_err:
            logger.error(f"❌ [BOT API ERROR] Telegram Bot API initialization failed: {bot_err}")
            HealthStatus.bot_error = str(bot_err)
            HealthStatus.bot_online = False
            bot_app = None

    global _global_bot_app, _global_scraper
    _global_bot_app = bot_app

    # -------------------------------------------------------------
    # STEP 3: Connect Telethon Call Scraper to Telegram IMMEDIATELY
    # -------------------------------------------------------------
    logger.info("🚀 [STEP 3/7] Connecting Telethon Call Scraper to Telegram...")
    HealthStatus.startup_stage = "Starting Telethon Userbot"
    scraper = CallScraper()
    _global_scraper = scraper
    scraper.bot_app = bot_app
    HealthStatus.scraper = scraper
    scraper_task = asyncio.create_task(scraper.start())

    # -------------------------------------------------------------
    # STEP 4: Initialize Cloud Database (MongoDB Atlas)
    # -------------------------------------------------------------
    logger.info("🚀 [STEP 4/7] Initializing MongoDB database connection...")
    HealthStatus.startup_stage = "Initializing Database"
    try:
        await asyncio.wait_for(init_db(), timeout=10.0)
        logger.info("✅ [STEP 4/7 SUCCESS] Database initialized & ready.")
    except Exception as db_err:
        logger.warning(f"Database initialization notice (continuing): {db_err}")

    # -------------------------------------------------------------
    # STEP 5: Pre-load alert deduplication keys
    # -------------------------------------------------------------
    logger.info("🚀 [STEP 5/7] Pre-loading alert deduplication keys...")
    HealthStatus.startup_stage = "Pre-loading deduplication keys"
    try:
        await asyncio.wait_for(init_sent_alerts(), timeout=6.0)
        logger.info("✅ [STEP 5/7 SUCCESS] Alert deduplication keys pre-loaded.")
    except Exception as alert_err:
        logger.warning(f"Alert pre-load notice: {alert_err}")

    try:
        from database import get_all_channels
        channels = await asyncio.wait_for(get_all_channels(), timeout=5.0)
        logger.info("=" * 60)
        if channels:
            logger.info(f"📡 KOLInsights Connected & Tracked Channels/Groups ({len(channels)}):")
            for idx, ch in enumerate(channels, start=1):
                user_str = f" (@{ch['username']})" if ch.get('username') else ""
                sub_info = f" [{ch.get('sub_type', 'VIP')}]"
                logger.info(f"  {idx}. {ch['title']}{user_str}{sub_info} [ID: {ch['channel_id']}]")
        else:
            logger.info("📡 No channels/groups registered in database yet. (Group calls auto-register when users post CAs)")
        logger.info("=" * 60)
    except Exception:
        pass

    # -------------------------------------------------------------
    # STEP 6: Starting Price Tracker background engine
    # -------------------------------------------------------------
    async def alert_callback(call_data, milestone, image_bytes):
        await send_milestone_alert(call_data, milestone, image_bytes, bot_app)

    async def consistency_callback(call_data, count, calls_7d):
        await send_consistency_alert(call_data, count, calls_7d, bot_app)

    logger.info("🚀 [STEP 6/7] Starting Price Tracker background engine...")
    HealthStatus.startup_stage = "Starting Price Tracker"
    tracker = PriceTracker(alert_callback=alert_callback, consistency_callback=consistency_callback)
    tracker_task = asyncio.create_task(tracker.start())
    logger.info("✅ [STEP 6/7 SUCCESS] Price Tracker background engine started!")

    # -------------------------------------------------------------
    # STEP 7: Starting periodic background tasks & Keep-Alive
    # -------------------------------------------------------------
    if bot_app:
        logger.info("🚀 [STEP 7/7] Starting periodic background tasks (Trending & Subscriptions)...")
        trending_task = asyncio.create_task(periodic_trending_poster(bot_app))
        sub_checker_task = asyncio.create_task(periodic_subscription_checker(bot_app))
        heartbeat_task = asyncio.create_task(periodic_system_heartbeat())
        logger.info("✅ [STEP 7/7 SUCCESS] Periodic background tasks running!")

    if KEEP_ALIVE_URL:
        asyncio.create_task(keep_alive_worker())

    HealthStatus.startup_stage = "100% Operational"
    logger.info("🎉 [KOLINSIGHTS] System 100% operational & running main event loop!")

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down KOLInsights system...")
    finally:
        tracker.stop()
        await scraper.stop()
        if bot_app:
            await bot_app.updater.stop()
            await bot_app.stop()

if __name__ == "__main__":
    asyncio.run(main())

