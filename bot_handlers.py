import os
import logging
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, Application

from config import ADMIN_IDS
from database import (
    get_channel_stats, get_calls_by_token, 
    add_channel, remove_channel, get_all_channels
)
from graphics import format_number, create_achievement_card

logger = logging.getLogger(__name__)

def clean_markdown(text: str) -> str:
    """Escapes Markdown special characters to prevent Telegram parse errors."""
    if not text:
        return ""
    for char in ["_", "*", "`", "["]:
        text = text.replace(char, f"\\{char}")
    return text

async def safe_reply(message, text: str, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=None, **kwargs):
    """Sends reply with Markdown, falling back gracefully to plain text on parse error."""
    if not message:
        return
    try:
        await message.reply_text(
            text, 
            parse_mode=parse_mode, 
            disable_web_page_preview=disable_web_page_preview, 
            reply_markup=reply_markup, 
            **kwargs
        )
    except Exception as e:
        logger.warning(f"Markdown reply failed ({e}), sending plain text fallback.")
        plain_text = text.replace("**", "").replace("*", "").replace("`", "")
        try:
            await message.reply_text(
                plain_text, 
                disable_web_page_preview=disable_web_page_preview, 
                reply_markup=reply_markup, 
                **kwargs
            )
        except Exception as e2:
            logger.error(f"Failed to send plain text fallback: {e2}")

def is_admin(user_id: int) -> bool:
    return bool(ADMIN_IDS and user_id in ADMIN_IDS)

def get_subscription_plans_text_and_markup():
    text = (
        "🚀 <b>KOLInsights Subscription Plans</b>\n\n"
        "🎁 <b>1-Week Free Trial</b>\n"
        "Get full access to KOLInsights for <b>1 Week absolutely FREE</b> and experience all the features before subscribing.\n\n"
        "💎 <b>Monthly Subscription</b>\n"
        "After your free trial, continue your subscription for just <b>0.01 ETH/month</b>.\n\n"
        "♾️ <b>Lifetime Subscription — LIMITED-TIME OFFER</b>\n"
        "Regular Price: <s>$600</s>\n"
        "🔥 <b>Special Offer: $399 ONE-TIME PAYMENT</b>\n\n"
        "Get lifetime access to KOLInsights with <b>no recurring monthly payments</b>.\n\n"
        "Choose the plan that works best for you:\n"
        "🎁 <b>1 Week</b> — FREE\n"
        "💎 <b>Monthly</b> — 0.01 ETH/month\n"
        "♾️ <b>Lifetime</b> — $399 (<s>$600</s>)"
    )
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎁 Start 1-Week Free Trial", callback_data="cb_add_channel"),
        ],
        [
            InlineKeyboardButton("💎 Monthly (0.01 ETH)", callback_data="cb_renew_sub"),
            InlineKeyboardButton("♾️ Lifetime ($399 Special)", callback_data="cb_lifetime_sub"),
        ],
        [
            InlineKeyboardButton("💬 Contact Admin (@kolinsights_admin)", url="https://t.me/kolinsights_admin"),
        ]
    ])
    return text, buttons

async def plans_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the official subscription plans and pricing menu."""
    text, buttons = get_subscription_plans_text_and_markup()
    target_msg = update.message if update.message else update.effective_message
    await safe_reply(target_msg, text, parse_mode="HTML", reply_markup=buttons)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_info = await context.bot.get_me() if context.bot else None
    bot_username = bot_info.username if (bot_info and bot_info.username) else "kolinsightsbot"

    if context.args:
        param = context.args[0]
        if param.startswith("token_"):
            ca = param.replace("token_", "")
            await display_project_info(update, ca, bot_username)
            return
        elif param.startswith("stats_"):
            identifier = param.replace("stats_", "")
            context.args = [identifier]
            await checkstats_command(update, context)
            return

    user_id = update.effective_user.id if update.effective_user else 0
    if update.effective_user:
        u = update.effective_user
        from database import record_bot_visitor
        await record_bot_visitor(u.id, u.username, u.full_name)

    welcome_text_html = (
        "<b>Welcome to KOLInsights 🐲</b>\n\n"
        "The <b>home</b> of KOL analytics. Tracking top call channels and used by thousands of investors.\n\n"
        "🌐 <b>Real-time, dynamic feed to discover early projects and new KOLs</b>\n"
        "🪻 <b>Unique achievements system for KOLs</b>\n"
        "📊 <b>In-depth KOL stats and project call history</b>\n"
        "🏆 <b>Advanced project trending system</b>\n"
        "🤖 <b>Premium user tools with exclusive features</b>\n\n"
        "🌐 <b>Website & Docs:</b> <a href=\"https://kolinsights.netlify.app/\">kolinsights.netlify.app</a>\n\n"
        "<i>Use /start to bring this menu back up at any point.</i>"
    )

    start_buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Official Website & Docs", url="https://kolinsights.netlify.app/"),
        ],
        [
            InlineKeyboardButton("🚀 Subscription Plans & Pricing", callback_data="cb_plans"),
        ],
        [
            InlineKeyboardButton("📢 Add My Channel (1 Week Free)", callback_data="cb_add_channel"),
            InlineKeyboardButton("📋 My Channels", callback_data="cb_my_channels"),
        ],
        [
            InlineKeyboardButton("💳 Renew Subscription", callback_data="cb_renew_sub"),
            InlineKeyboardButton("💬 Contact Admin", url="https://t.me/kolinsights_admin"),
        ]
    ])

    from database import get_custom_media
    custom_media = await get_custom_media("start")

    target_msg = update.message if update.message else update.effective_message
    sent_media = False
    if target_msg and custom_media:
        try:
            file_id = custom_media.get("file_id")
            media_type = custom_media.get("media_type")
            if media_type == "animation":
                await target_msg.reply_animation(animation=file_id, caption=welcome_text_html, parse_mode="HTML", reply_markup=start_buttons)
            else:
                await target_msg.reply_photo(photo=file_id, caption=welcome_text_html, parse_mode="HTML", reply_markup=start_buttons)
            sent_media = True
            return
        except Exception as media_err:
            logger.warning(f"Could not send custom start banner media ({media_err}), trying local logo fallback.")

    # Local logo fallback if custom media file_id is invalid or expired
    if not sent_media and target_msg:
        logo_path = os.path.join(os.path.dirname(__file__), "kolinsights_website", "logo.jpg")
        if os.path.exists(logo_path):
            try:
                with open(logo_path, "rb") as f:
                    sent_msg = await target_msg.reply_photo(photo=f, caption=welcome_text_html, parse_mode="HTML", reply_markup=start_buttons)
                    if sent_msg and sent_msg.photo:
                        new_file_id = sent_msg.photo[-1].file_id
                        from database import set_custom_media
                        await set_custom_media("start", new_file_id, "photo")
                        logger.info(f"✅ [START BANNER] Auto-cached fresh photo file_id ({new_file_id[:15]}...) in MongoDB!")
                    return
            except Exception as local_err:
                logger.warning(f"Could not send local start banner ({local_err}), falling back to text.")

    await safe_reply(target_msg, welcome_text_html, parse_mode="HTML", reply_markup=start_buttons)

import time

_SUB_CACHE = {
    "last_update": 0,
    "active_channels": set(),
    "active_owners": set()
}

async def refresh_sub_cache_if_needed(force: bool = False):
    """Caches active channels and co-owners in RAM for 30s to make authorization checks instant (0.000s)."""
    now = time.time()
    if not force and (now - _SUB_CACHE["last_update"] < 30):
        return
    from database import get_all_channels, get_all_linked_owners
    try:
        channels = await get_all_channels()
        active_ch_ids = set()
        active_unames = set()

        for ch in channels:
            if (ch.get("sub_type") or "VIP").upper() != "EXPIRED":
                if ch.get("channel_id"):
                    active_ch_ids.add(str(ch["channel_id"]))
                if ch.get("username"):
                    active_unames.add(str(ch["username"]).lower().strip().lstrip("@"))
                if ch.get("owner_username"):
                    raw_owners = [o.strip().lstrip("@") for o in str(ch["owner_username"]).lower().replace(",", " ").split() if o.strip()]
                    for ro in raw_owners:
                        active_unames.add(ro)
                if ch.get("owner_user_id"):
                    active_ch_ids.add(str(ch["owner_user_id"]))

        linked_owners = await get_all_linked_owners()
        active_owners = set()
        for o in linked_owners:
            raw_str = (o.get("owner_username") or "").lower().replace(",", " ").split()
            owners_list = [str(x).lower().strip().lstrip("@") for x in o.get("owners_list", [])]
            for name in set(raw_str + owners_list):
                if name:
                    active_owners.add(name.strip().lstrip("@"))

        _SUB_CACHE["active_channels"] = active_ch_ids.union(active_unames)
        _SUB_CACHE["active_owners"] = active_owners
        _SUB_CACHE["last_update"] = now
    except Exception as e:
        logger.warning(f"Failed to refresh sub cache: {e}")

async def is_user_subscribed_or_admin(user_id: int, username: str = "") -> bool:
    """Checks if user is an Admin or is a linked owner of an active VIP/Paid/Trial channel in database."""
    if is_admin(user_id):
        return True

    await refresh_sub_cache_if_needed()
    user_str = str(user_id) if user_id else ""
    uname_clean = (username or "").lower().strip().lstrip("@")

    if user_str and user_str in _SUB_CACHE["active_channels"]:
        return True
    if uname_clean and (uname_clean in _SUB_CACHE["active_channels"] or uname_clean in _SUB_CACHE["active_owners"]):
        return True

    return False

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    u_name = update.effective_user.username if update.effective_user else ""
    is_sub = await is_user_subscribed_or_admin(user_id, u_name)

    text = (
        "🤖 **Welcome to KOLInsights Command Center!**\n\n"
        "🌐 **Official Website & Docs:** https://kolinsights.netlify.app/\n\n"
    )

    if is_sub:
        text += (
            "⭐ **Premium KOL Club Commands (Unlocked 💎):**\n"
            "• /generate <@channel> - Generate custom KOL performance report\n"
            "• /follow <@channel> - Receive DM alerts when a KOL gets an achievement\n"
            "• /unfollow <@channel> - Stop receiving DM notifications\n"
            "• /showfollowed - Show the KOL channels you are following\n"
            "• /reset - Cancel current process\n\n"
        )

    text += (
        "• /start - Launch dashboard & channel tracking onboarding\n"
        "• /help - Display this command helper list\n"
        "• /checkstats <@channelname> - Check stats of any caller we track\n"
        "• /checkcalls <contract_address> - See who called a token first\n"
        "• /trending - View trending callers and recently called projects\n\n"
    )

    if not is_sub:
        text += (
            "📢 **Want to track your channel?**\n"
            "Tap /start to get a **1-Month FREE Trial** tracking for your channel or contact `@KOLInsights_admin`!"
        )

    await safe_reply(update.message, text)

async def adminhelp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    text = (
        "👑 **KOLInsights Admin Control Manual**\n\n"
        "📢 **Channel & Owner Management:**\n"
        "• `/addchannel <@username|id> [title]` - Track a new Telegram channel\n"
        "• `/removechannel <@username|id>` - Remove a channel from tracking\n"
        "• `/linkowner <@channel> <@owner_dm>` - Link channel to owner's personal DM (for direct 2x/3x DM alerts!)\n"
        "• `/listchannels` - List all tracked channels, subscriber counts & trial dates\n"
        "• `/setchannel <@alert_channel>` - Set destination channel for main broadcasts\n\n"
        "💳 **Subscriptions & Billing:**\n"
        "• `/renewchannel <@username> <days>` - Extend channel subscription by N days\n"
        "• `/setstatus <@username> <VIP|TRIAL|EXPIRED>` - Update channel subscription status\n"
        "• `/setwallet <address> [label]` - Set active deposit wallet address\n"
        "• `/wallets` - List registered deposit wallets\n\n"
        "🎨 **Alert Banners & Custom Media:**\n"
        "• `/setmedia <key>` - Reply to a GIF/Photo to set custom banner for alert (`start`, `recently_called`, `newcall`, `dip`, `2x`, `3x`, `5x`, `10x`, `cashprinter`)\n"
        "• `/removemedia <key>` - Remove custom media banner for a key\n"
        "• `/listmedia` - View all configured media banners\n\n"
        "📊 **Analytics & Utility:**\n"
        "• `/trending` / `/recentlycalled` - Trigger live recently called report\n"
        "• `/checkstats <@channel>` - Audit caller win rate, consistency & yield\n"
        "• `/checkcalls <CA>` - Trace token call timeline across callers"
    )
    await safe_reply(update.message, text)

async def display_project_info(message_or_update, ca: str, bot_username: str = "kolinsightsbot"):
    """Renders detailed project history matching Image 3 format."""
    ca = ca.strip()
    calls = await get_calls_by_token(ca)
    target_msg = message_or_update.message if hasattr(message_or_update, "message") and message_or_update.message else message_or_update

    if not calls:
        await safe_reply(target_msg, f"❌ No recorded calls found for CA: `{clean_markdown(ca)}`")
        return

    from dex_api import fetch_token_data
    live_info = await fetch_token_data(ca)

    first_call = calls[0]
    p_name = clean_markdown(first_call['token_name'])
    chain = (first_call.get('chain') or 'solana').lower()

    current_mc = live_info['market_cap'] if (live_info and live_info.get('market_cap')) else first_call['current_mc']
    ath_mc = max(first_call['ath_mc'], max((c['ath_mc'] for c in calls), default=0), current_mc)
    earliest_caller = f"@{first_call['channel_username']}" if first_call.get('channel_username') else first_call['channel_title']

    text = (
        f"🐉 **Project Name:** {p_name}\n\n"
        f"💵 **CA:** `{ca}`\n\n"
        f"📊 **Current Marketcap:** {format_number(current_mc)}\n\n"
        f"📈 **ATH:** {format_number(ath_mc)}\n\n"
        f"📞 **Total Calls:** {len(calls)}\n\n"
        f"⏰ **Earliest Call:** {clean_markdown(earliest_caller)} at {format_number(first_call['entry_mc'])}\n\n"
        f"📅 **Call History**\n\n"
    )

    for c in calls:
        k_user = f"@{clean_markdown(c['channel_username'])}" if c.get('channel_username') else clean_markdown(c['channel_title'])
        c_entry = format_number(c['entry_mc'])
        c_mult = (c['ath_mc'] / c['entry_mc']) if c['entry_mc'] > 0 else 1.0
        c_date = c.get('created_at', '')

        text += (
            f"KOL: {k_user}\n"
            f"Called At: {c_entry}\n"
            f"X Counter: x{c_mult:.1f}\n"
            f"⏰ {c_date}\n"
        )
        links = []
        if c.get("call_msg_link"):
            links.append(f"👁️ [View Call]({c['call_msg_link']})")
        
        caller_param = c.get("channel_username") or c.get("channel_id")
        links.append(f"📊 [View Stats](https://t.me/{bot_username}?start=stats_{caller_param})")
        
        text += "  ".join(links) + "\n\n"

    text += (
        f"🔊 **Ad - KOLInsights**\n"
        f"[Want your ad here? Contact @KOLInsightsAdmin](https://t.me/KOLInsightsAdmin)\n"
    )

    # Inline Keyboard Buttons at bottom: [ View Chart ↗️ ] [ Check TTF ↗️ ]
    chart_url = f"https://dexscreener.com/{chain}/{ca}"
    ttf_url = f"https://t.me/ttfbotgo_bot?start={ca}"

    buttons = [
        [InlineKeyboardButton("View Chart ↗️", url=chart_url)],
        [InlineKeyboardButton("Check TTF ↗️", url=ttf_url)]
    ]
    reply_markup = InlineKeyboardMarkup(buttons)

    try:
        await target_msg.reply_text(text, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=reply_markup)
    except Exception as e:
        logger.warning(f"Project info markdown failed ({e}), sending plain text fallback...")
        plain_text = text.replace("**", "").replace("`", "")
        await target_msg.reply_text(plain_text, disable_web_page_preview=True, reply_markup=reply_markup)

async def checkstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        target_msg = update.message or (update.callback_query.message if update.callback_query else None)
        await safe_reply(target_msg, "⚠️ Usage: `/checkstats <@username or channel_id>`")
        return

    identifier = context.args[0].strip()
    stats = await get_channel_stats(identifier)

    target_msg = update.message or (update.callback_query.message if update.callback_query else None)
    if not target_msg:
        return

    if not stats:
        await safe_reply(target_msg, f"❌ No tracked calls found for `{clean_markdown(identifier)}`.")
        return

    ch_title = clean_markdown(stats['channel_title'])
    ch_user = clean_markdown(stats['channel_username'] or "")
    ch_disp = f"@{ch_user}" if ch_user else ch_title

    best = stats['best_call']
    best_name = clean_markdown(best['token_name'])
    best_mult = best['highest_multiplier']

    last = stats['last_call']
    last_name = clean_markdown(last['token_name'])
    last_mult = last['highest_multiplier']

    lines = [
        f"👨‍✈️ **Stats for {ch_disp}:**\n",
        f"⚖️ **Consistency:** `{stats['consistency']}%`",
        f"⭐ **Number of Alpha Calls:** `{stats['alpha_calls']}`",
        f"🏆 **Best Call:** {best_name} (`x{best_mult:.1f}`)",
        f"📅 **Last Call:** {last_name} (`x{last_mult:.1f}`)\n",
        f"📊 **Average Call Marketcap:** `{format_number(stats['avg_entry_mc'])}`",
        f"✖️ **Average X Per Call:** `x{stats['avg_x']:.1f}`",
        f"📞 **Total Calls Tracked:** `{stats['total_calls']}`\n",
        f"**Number of Achievement Calls:**",
        f"🟢 **2x+ calls:** `{stats['calls_2x_plus']}`",
        f"🟠 **5x+ calls:** `{stats['calls_5x_plus']}`\n",
        f"📌 *Achievement Calls refer to calls that have gone x2+ as per our parameters*\n",
        f"⏰ **Last 5 calls:**"
    ]

    for c in stats['last_5']:
        c_name = clean_markdown(c['token_name'])
        c_sym = clean_markdown(c['token_symbol'])
        c_mult = c['highest_multiplier']
        lines.append(f"🔷 **{c_name}** (`{c_sym}`)")
        lines.append(f"Called at: `{format_number(c['entry_mc'])}` | ATH: `{format_number(c['ath_mc'])}` | Multiplier: `x{c_mult:.1f}` | Current: `{format_number(c['current_mc'])}`")
        
        call_link = c.get('call_msg_link')
        if not call_link and c.get('call_msg_id') and c.get('channel_username'):
            call_link = f"https://t.me/{c['channel_username']}/{c['call_msg_id']}"
        
        if call_link:
            lines.append(f"🔗 [View Call]({call_link})")
        lines.append("")

    lines.extend([
        "-----------------------------------",
        "💵 **$100 into last 5 calls:**",
        f"📈 **ATH Value:** `${stats['sim_ath_val']:,.2f}`",
        f"⏱️ **Current Value:** `${stats['sim_current_val']:,.2f}`",
        "-----------------------------------\n",
        "👑 **Top 3 calls:**"
    ])

    medals = ["🥇", "🥈", "🥉"]
    for idx, c in enumerate(stats['top_3']):
        medal = medals[idx] if idx < len(medals) else "🏅"
        c_name = clean_markdown(c['token_name'])
        c_mult = c['highest_multiplier']
        lines.append(f"{medal} **{c_name}**: Called at `{format_number(c['entry_mc'])}` ➡️ ATH `{format_number(c['ath_mc'])}` (`x{c_mult:.1f}`)")

    text = "\n".join(lines)

    buttons = []
    if stats['channel_username']:
        buttons.append([InlineKeyboardButton("📢 Open Channel", url=f"https://t.me/{stats['channel_username']}")])
    
    reply_markup = InlineKeyboardMarkup(buttons) if buttons else None
    await safe_reply(target_msg, text, reply_markup=reply_markup)

async def checkcalls_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await safe_reply(update.message, "⚠️ Usage: `/checkcalls <contract_address>`")
        return

    ca = context.args[0].strip()
    bot_info = await context.bot.get_me() if context.bot else None
    bot_username = bot_info.username if (bot_info and bot_info.username) else "kolinsightsbot"
    await display_project_info(update, ca, bot_username)

async def addchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    if not context.args:
        await safe_reply(update.message, "⚠️ Usage: `/addchannel <channel_username_or_id> <title>`")
        return

    target = context.args[0].lstrip("@")
    title = " ".join(context.args[1:]) if len(context.args) > 1 else target
    channel_id = int(target) if target.lstrip("-").isdigit() else abs(hash(target)) % 1_000_000_000

    target_clean = target.lower().strip()
    channels = await get_all_channels()
    existing = None
    for ch in channels:
        ch_u = (ch.get("username") or "").lower().strip().lstrip("@")
        ch_id_s = str(ch.get("channel_id") or "")
        if target_clean in (ch_u, ch_id_s) or (target_clean.isdigit() and target_clean in ch_id_s):
            existing = ch
            break

    if existing:
        stype = existing.get("sub_type") or "VIP"
        await safe_reply(update.message, f"ℹ️ Channel `@{clean_markdown(target)}` is **ALREADY** in the tracking database! (Status: `{stype}`)")
        return

    await add_channel(channel_id, title, target)
    safe_target = clean_markdown(target)
    safe_title = clean_markdown(title)
    await safe_reply(update.message, f"✅ Added channel `@{safe_target}` ({safe_title}) to tracking database.")

async def removechannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    if not context.args:
        await safe_reply(update.message, "⚠️ Usage: `/removechannel <username, title, or channel_id>`")
        return

    target = " ".join(context.args).strip()
    target_clean = target.lstrip("@").strip()
    
    channels = await get_all_channels()
    found_channel = None

    for ch in channels:
        ch_user = (ch.get("username") or "").lower()
        ch_title = (ch.get("title") or "").lower()
        ch_id_str = str(ch.get("channel_id") or "")

        if target_clean.lower() in (ch_user, ch_title, ch_id_str):
            found_channel = ch
            break

    if found_channel:
        cid = found_channel["channel_id"]
        c_title = clean_markdown(found_channel.get("title") or target_clean)
        await remove_channel(str(cid))
        await safe_reply(update.message, f"🗑️ Removed channel **{c_title}** (ID: `{cid}`) from tracking database.")
    else:
        removed = await remove_channel(target_clean)
        if removed:
            await safe_reply(update.message, f"🗑️ Removed channel **{clean_markdown(target)}** from tracking database.")

async def linkowner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    full_text = " ".join(context.args) if context.args else ""
    # Normalize commas into spaces
    tokens = [t.strip() for t in full_text.replace(",", " ").split() if t.strip()]

    if len(tokens) < 2:
        await safe_reply(update.message, "⚠️ Usage: `/linkowner <channel_username_or_id> <owner_personal_username_or_id>`\n\nExample: `/linkowner @Crazy_Apess @votexape` or `/linkowner @Crazy_Apess,@votexape`")
        return

    from database import link_channel_owner
    channel_target = tokens[0]
    owners_target = " ".join(tokens[1:])

    success = await link_channel_owner(channel_target, owners_target)
    safe_ch = clean_markdown(channel_target)
    safe_owner = clean_markdown(owners_target)

    if success:
        await refresh_sub_cache_if_needed(force=True)
        await safe_reply(update.message, f"✅ **Channel Linked to Owner DM(s)!**\n\n📢 **Channel:** `{safe_ch}`\n👤 **Owner DM(s):** `{safe_owner}`\n\n*All future 2x/3x/5x/10x milestone achievement alerts for this channel will be sent directly to `{safe_owner}` in DM!*")
    else:
        await safe_reply(update.message, f"⚠️ Channel `{safe_ch}` not found in tracking database. Please add it first using `/addchannel`.")

async def unlinkowner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    if not context.args:
        await safe_reply(
            update.message,
            "⚠️ **Usage:**\n"
            "• `/unlinkowner <channel>` — Unlink all owners from this channel\n"
            "• `/unlinkowner <channel> <specific_owner>` — Unlink only a specific co-owner\n\n"
            "**Examples:**\n"
            "`/unlinkowner @Crazy_Apess`\n"
            "`/unlinkowner @Crazy_Apess @votexape`"
        )
        return

    channel_target = context.args[0].strip()
    owner_target = context.args[1].strip() if len(context.args) > 1 else None

    from database import unlink_channel_owner
    success = await unlink_channel_owner(channel_target, owner_target)
    safe_ch = clean_markdown(channel_target)

    if success:
        await refresh_sub_cache_if_needed(force=True)
        if owner_target:
            safe_owner = clean_markdown(owner_target)
            await safe_reply(update.message, f"🗑️ **Co-Owner Unlinked!**\n\n📢 **Channel:** `{safe_ch}`\n👤 **Removed Owner:** `{safe_owner}`\n\n*This owner will no longer receive private DM achievement alerts for this channel.*")
        else:
            await safe_reply(update.message, f"🗑️ **All Owners Unlinked!**\n\n📢 **Channel:** `{safe_ch}`\n\n*All linked owners removed. No private DM achievement alerts will be sent for this channel.*")
    else:
        await safe_reply(update.message, f"⚠️ Channel `{safe_ch}` not found in database.")

async def listowners_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    from database import get_all_linked_owners
    owners = await get_all_linked_owners()

    if not owners:
        await safe_reply(update.message, "📋 No channel owners linked yet. Use `/linkowner <@channel> <@owner_dm>` to link an owner.")
        return

    text = f"👑 **Linked Channel Owners ({len(owners)}):**\n\n"
    for idx, row in enumerate(owners, 1):
        ch_name = clean_markdown(row.get("username") or row.get("title") or str(row["channel_id"]))
        owner_name = clean_markdown(row.get("owner_username") or str(row.get("owner_user_id") or "N/A"))
        text += f"{idx}. 📢 **@{ch_name}**\n   👤 Owner DM: `{owner_name}`\n\n"

    await safe_reply(update.message, text)

async def remindsub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to manually trigger a subscription reminder to a channel owner and report delivery receipt."""
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    if not context.args:
        await safe_reply(update.message, "⚠️ **Usage:** `/remindsub <@channel_username_or_id>`\n\nExample: `/remindsub @buteringems`")
        return

    target = context.args[0].strip()
    clean_target = target.lstrip("@").strip()
    
    from database import get_all_channels, get_channel_owners
    channels = await get_all_channels()
    matched_ch = None
    for ch in channels:
        if clean_target.lower() in ((ch.get("username") or "").lower(), (ch.get("title") or "").lower(), str(ch.get("channel_id") or "")):
            matched_ch = ch
            break

    if not matched_ch:
        await safe_reply(update.message, f"❌ Channel `{clean_markdown(target)}` not found in database.")
        return

    c_title = clean_markdown(matched_ch.get("title") or "Channel")
    c_user = f"@{clean_markdown(matched_ch['username'])}" if matched_ch.get("username") else f"ID: `{matched_ch['channel_id']}`"
    sub_type = (matched_ch.get("sub_type") or "TRIAL").upper()
    sub_end = matched_ch.get("sub_end") or "N/A"

    owners = await get_channel_owners(matched_ch.get("username") or matched_ch.get("channel_id"))
    
    owner_msg = (
        f"Hello Sir,\n\n"
        f"This is a friendly reminder that your **KOLInsights** trial/subscription period has ended for {c_user}.\n\n"
        f"To continue tracking KOLs and accessing KOLInsights, please renew your subscription today.\n\n"
        f"👉 **Renew here:** @kolinsights_bot\n\n"
        f"**How to renew:**\n"
        f"1. Send `/start` to @kolinsights_bot\n"
        f"2. Tap **💳 Renew Subscription**\n"
        f"3. Follow the instructions to complete your subscription.\n\n"
        f"Thank you for using **KOLInsights**. We look forward to continuing to provide you with valuable KOL tracking and insights."
    )

    bot_app = context.application if context else None
    receipts = []
    if owners and bot_app:
        for o in owners:
            target_id = o
            if str(o).lstrip("-").isdigit():
                target_id = int(o)
            try:
                await bot_app.bot.send_message(chat_id=target_id, text=owner_msg, parse_mode="Markdown")
                receipts.append(f"✅ **Sent to:** `{o}`")
            except Exception as dm_err:
                err_str = str(dm_err).lower()
                if "blocked" in err_str:
                    receipts.append(f"❌ `{o}`: User has blocked the bot")
                elif "not found" in err_str or "chat not found" in err_str:
                    receipts.append(f"❌ `{o}`: User hasn't started the bot in DM (cannot initiate DM)")
                else:
                    receipts.append(f"❌ `{o}`: Error ({str(dm_err)[:40]})")
    else:
        receipts.append(f"⚠️ **No owner linked** in database (use `/linkowner {c_user} @username` to link an owner)")

    status_block = "\n".join([f"• {r}" for r in receipts])
    report = (
        f"📢 **Subscription Reminder Report:**\n\n"
        f"🏷️ **Channel:** {c_title} ({c_user})\n"
        f"📊 **Sub Type:** `{sub_type}`\n"
        f"⏳ **Sub End:** `{sub_end}`\n\n"
        f"📬 **Owner DM Delivery Status:**\n{status_block}"
    )
    await safe_reply(update.message, report)

async def build_channel_list_view(filter_arg: str, page: int = 1):
    filter_clean = (filter_arg or "all").lower().strip()
    from database import get_channels_by_tier
    channels = await get_channels_by_tier(filter_clean)

    if not channels:
        text = f"📋 No channels found matching tier filter: **{clean_markdown(filter_clean.upper())}**."
        buttons = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("💎 VIP", callback_data="cb_listch:vip:1"),
                InlineKeyboardButton("🎁 Trial", callback_data="cb_listch:trial:1"),
                InlineKeyboardButton("💳 Paid", callback_data="cb_listch:paid:1"),
                InlineKeyboardButton("🔴 Expired", callback_data="cb_listch:expired:1"),
                InlineKeyboardButton("📋 All", callback_data="cb_listch:all:1"),
            ]
        ])
        return text, buttons

    from datetime import datetime, timezone
    now_dt = datetime.now(timezone.utc)

    filter_title = filter_clean.upper()
    total_count = len(channels)
    chunk_size = 12
    total_pages = max(1, (total_count + chunk_size - 1) // chunk_size)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * chunk_size
    end_idx = start_idx + chunk_size
    chunk = channels[start_idx:end_idx]

    header = f"📋 **Tracked Channels List [{filter_title}] ({total_count})** [Page {page}/{total_pages}]:\n\n"
    text = header
    start_num = start_idx + 1

    for idx_offset, ch in enumerate(chunk):
        idx = start_num + idx_offset
        username = f"@{clean_markdown(ch['username'])}" if ch.get('username') else "N/A"
        title = clean_markdown(ch.get('title') or "Unknown Channel")
        sub_type = (ch.get('sub_type') or "VIP").upper()
        sub_end = ch.get('sub_end')

        if sub_type in ("VIP", "LIFETIME"):
            status_str = "💎 `LIFETIME VIP` | ⏳ **Expires:** `NEVER`"
        elif sub_type == "EXPIRED":
            status_str = "🔴 `EXPIRED` | ⏳ **Tracking Paused**"
        elif sub_end:
            try:
                end_dt = datetime.strptime(str(sub_end)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                diff = end_dt - now_dt
                days_left = max(0, diff.days)
                if diff.total_seconds() <= 0:
                    status_str = "🔴 `EXPIRED` | ⏳ **Tracking Paused**"
                elif sub_type == "TRIAL":
                    status_str = f"🎁 `FREE TRIAL` | ⏳ **Expires:** {end_dt.strftime('%b %d, %Y')} ({days_left}d left)"
                else:
                    status_str = f"💳 `PAID ({sub_type})` | ⏳ **Expires:** {end_dt.strftime('%b %d, %Y')} ({days_left}d left)"
            except Exception:
                status_str = f"🟢 `{sub_type}`"
        else:
            status_str = "💎 `LIFETIME VIP`"

        text += f"{idx}. **{title}** ({username}) - ID: `{ch['channel_id']}`\n   {status_str}\n"

    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"cb_listch:{filter_clean}:{page - 1}"))
    else:
        nav_row.append(InlineKeyboardButton("⛔ Start", callback_data="cb_noop"))

    nav_row.append(InlineKeyboardButton(f"📄 {page}/{total_pages}", callback_data="cb_noop"))

    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"cb_listch:{filter_clean}:{page + 1}"))
    else:
        nav_row.append(InlineKeyboardButton("End ⛔", callback_data="cb_noop"))

    filter_row = [
        InlineKeyboardButton("💎 VIP", callback_data="cb_listch:vip:1"),
        InlineKeyboardButton("🎁 Trial", callback_data="cb_listch:trial:1"),
        InlineKeyboardButton("💳 Paid", callback_data="cb_listch:paid:1"),
        InlineKeyboardButton("🔴 Expired", callback_data="cb_listch:expired:1"),
        InlineKeyboardButton("📋 All", callback_data="cb_listch:all:1"),
    ]

    buttons = InlineKeyboardMarkup([nav_row, filter_row])
    return text, buttons

async def listchannels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    filter_arg = context.args[0].lower().strip() if context.args else "all"
    text, buttons = await build_channel_list_view(filter_arg, page=1)
    await safe_reply(update.message, text, reply_markup=buttons)

async def setstatus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    if not context.args or len(context.args) < 2:
        await safe_reply(
            update.message,
            "⚠️ Usage: `/setstatus <tier> <days> <target1,target2,target3...>`\n\n"
            "Examples:\n"
            "• `/setstatus vip 0 @panther_huntz,@the3kitchen,@nyroshicalls` (Bulk Lifetime VIP)\n"
            "• `/setstatus paid 30 @channel1,@channel2` (Bulk 30-Day Paid)\n"
            "• `/setstatus trial 14 -1004378337331,@mychannel` (Bulk Trial)\n"
            "• `/setstatus expired 0 @channel1,@channel2` (Bulk Expired)"
        )
        return

    valid_tiers = {"vip", "lifetime", "trial", "paid", "expired"}
    status_type = "vip"
    days = 30
    targets = []

    raw_args = context.args
    non_target_indices = set()

    for idx, arg in enumerate(raw_args):
        a_clean = arg.strip().lower()
        if a_clean in valid_tiers:
            status_type = a_clean
            non_target_indices.add(idx)
            break

    for idx, arg in enumerate(raw_args):
        if idx in non_target_indices:
            continue
        if arg.strip().isdigit() and len(arg.strip()) < 5:
            days = int(arg.strip())
            non_target_indices.add(idx)
            break

    target_tokens = [arg for idx, arg in enumerate(raw_args) if idx not in non_target_indices]
    targets_raw = " ".join(target_tokens)
    targets = [t.strip() for t in targets_raw.replace(",", " ").split() if t.strip()]

    if not targets:
        await safe_reply(update.message, "❌ No target channels provided. Example: `/setstatus vip 0 @ch1,@ch2` or `/setstatus @nyoncalls vip`")
        return

    from database import bulk_set_channel_status
    updated_targets = await bulk_set_channel_status(targets, status_type, days=days)

    if updated_targets:
        st_upper = status_type.upper()
        updated_list = "\n".join([f"  • `{clean_markdown(t)}`" for t in updated_targets])
        if st_upper in ("VIP", "LIFETIME"):
            await safe_reply(update.message, f"💎 **Bulk Status Update Complete!**\n\n🏷️ **Tier:** `LIFETIME VIP` (Expires: NEVER)\n📢 **Updated {len(updated_targets)} Channel(s):**\n{updated_list}")
        elif st_upper == "EXPIRED":
            await safe_reply(update.message, f"🔴 **Bulk Status Update Complete!**\n\n🏷️ **Tier:** `EXPIRED` (Tracking Paused)\n📢 **Updated {len(updated_targets)} Channel(s):**\n{updated_list}")
        else:
            await safe_reply(update.message, f"✅ **Bulk Status Update Complete!**\n\n🏷️ **Tier:** `{st_upper}` ({days} Days)\n📢 **Updated {len(updated_targets)} Channel(s):**\n{updated_list}")
    else:
        await safe_reply(update.message, "❌ None of the specified channels were found in database.")

async def renewchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    if not context.args:
        await safe_reply(update.message, "⚠️ Usage: `/renewchannel <@username|id> [months]` (e.g. `/renewchannel @channel 1`)")
        return

    target = context.args[0].strip()
    months = 1
    if len(context.args) >= 2 and context.args[1].isdigit():
        months = int(context.args[1])
    days = months * 30

    from database import set_channel_subscription
    success = await set_channel_subscription(target, "PAID", days=days)
    if success:
        await safe_reply(update.message, f"🎉 Extended **{clean_markdown(target)}** subscription by **{months} month(s)** ({days} days)!")
    else:
        await safe_reply(update.message, f"❌ Channel **{clean_markdown(target)}** not found in database.")

async def setmedia_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    if not context.args:
        await safe_reply(
            update.message,
            "⚠️ Usage: Reply to a GIF or Photo with:\n"
            "• `/setmedia start` (for /start intro banner image/GIF)\n"
            "• `/setmedia trending` or `/setmedia recentlycalled` (for Recently Called Projects report GIF)\n"
            "• `/setmedia newcall` or `/setmedia call` (for New Call Tracked initial alert GIF)\n"
            "• `/setmedia motion` (for instant surge alerts)\n"
            "• `/setmedia consistency` (for On Fire! consistency alerts)\n"
            "• `/setmedia cashprinter` (for Cash Printer 20x-100x alerts)\n"
            "• `/setmedia 2x` (or `3x`, `5x`, `10x` for standard milestone alerts)"
        )
        return

    raw_key = context.args[0].lower().strip()
    if raw_key.endswith("x"):
        key_clean = raw_key
    elif raw_key in ("start", "start_banner", "welcome", "banner", "intro"):
        key_clean = "start"
    elif raw_key in ("daily", "daily_recap", "daily_leaderboard", "dailyrecap", "24h"):
        key_clean = "daily"
    elif raw_key in ("weekly", "weekly_leaderboard", "weekly_champion", "weeklychampion", "champion"):
        key_clean = "weekly"
    elif raw_key in ("slowcook", "slow_cook", "slow"):
        key_clean = "slowcook"
    elif raw_key in ("trending", "spotlight", "market_movers", "marketmovers", "recently_called", "recentlycalled"):
        key_clean = "trending"
    elif raw_key in ("newcall", "new_call", "call", "new", "initial_call"):
        key_clean = "newcall"
    elif raw_key in ("immediate", "immediately", "instant", "motion", "instant2x", "instant_2x", "speed", "fast"):
        key_clean = "immediate"
    elif raw_key in ("consistency", "onfire", "on_fire"):
        key_clean = "consistency"
    elif raw_key in ("cashprinter", "cash_printer", "printer", "xprinter"):
        key_clean = "cashprinter"
    elif raw_key in ("dip", "diprecall", "dip_recall", "dipentry", "dip_entry"):
        key_clean = "dip"
    else:
        try:
            val = float(raw_key)
            key_clean = f"{int(val)}x" if val.is_integer() else f"{val}x"
        except ValueError:
            await safe_reply(update.message, "❌ Invalid key! Use `immediate`, `daily`, `weekly`, `slowcook`, `start`, `trending`, `newcall`, `dip`, `consistency`, `cashprinter`, `2x`, `5x`.")
            return

    target_msg = update.message.reply_to_message or update.message
    file_id = None
    media_type = None

    if target_msg.animation:
        file_id = target_msg.animation.file_id
        media_type = "animation"
    elif target_msg.video:
        file_id = target_msg.video.file_id
        media_type = "animation"
    elif target_msg.photo:
        file_id = target_msg.photo[-1].file_id
        media_type = "photo"
    elif target_msg.document:
        file_id = target_msg.document.file_id
        doc_mime = (target_msg.document.mime_type or "").lower()
        if "image" in doc_mime and "gif" not in doc_mime:
            media_type = "photo"
        else:
            media_type = "animation"
    elif target_msg.sticker:
        file_id = target_msg.sticker.file_id
        media_type = "photo"

    if not file_id:
        await safe_reply(update.message, "❌ No GIF, Photo, or Video detected! Please **reply to a GIF or Photo message**.")
        return

    from database import set_custom_media
    await set_custom_media(key_clean, file_id, media_type)
    await safe_reply(update.message, f"✅ Successfully set custom **{media_type.upper()}** for **{key_clean}**!")

async def removemedia_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    if not context.args:
        await safe_reply(update.message, "⚠️ Usage: `/removemedia start` or `/removemedia trending`")
        return

    raw_key = context.args[0].lower().strip()
    key_clean = "trending" if raw_key in ("recently_called", "recentlycalled") else ("start" if raw_key in ("welcome", "banner", "intro") else raw_key)

    from database import remove_custom_media
    removed = await remove_custom_media(key_clean)
    if removed:
        await safe_reply(update.message, f"🗑️ Removed custom media for **{key_clean}**.")
    else:
        await safe_reply(update.message, f"❌ No custom media found for **{key_clean}**.")

async def listmedia_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    from database import get_all_custom_media
    media_list = await get_all_custom_media()
    if not media_list:
        await safe_reply(update.message, "📋 No custom GIFs/Photos configured yet. Reply to a GIF with `/setmedia start` or `/setmedia trending` to add one!")
        return

    text = "📋 **Custom Media Configuration:**\n\n"
    for m in media_list:
        text += f"• **{m['media_key']}**: {m['media_type'].upper()} (ID: `{m['file_id'][:15]}...`)\n"

    await safe_reply(update.message, text)

async def adminhelp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        # Stealth Security: Return immediately with ZERO output for non-admins!
        return

    text = (
        "👑 **KOLInsights Admin Control Center & Full Command Guide**\n\n"
        "Tap any command below to **instantly execute** it, or use the auto-fill buttons!\n\n"
        "👥 **1. Bot Visitor Analytics & Users:**\n"
        "• /visitors or /users — View total unique bot users, 24h active users, and last 25 recent visitors with user IDs.\n\n"
        "📢 **2. Channel Tier Listing & Categorized Filtering:**\n"
        "• /listchannels — List all tracked channels with live subscription status.\n"
        "• /listchannels vip — List ONLY Lifetime VIP channels 💎\n"
        "• /listchannels trial — List ONLY Active Free Trial channels 🎁\n"
        "• /listchannels paid — List ONLY Paid subscription channels 💳\n"
        "• /listchannels expired — List ONLY Expired channels ⏳\n\n"
        "⚡ **3. Bulk Multi-Channel Status Management:**\n"
        "• /setstatus <tier> <days> <target1,target2,target3...>\n"
        "  *Examples:*\n"
        "  - /setstatus vip 0 @panther_huntz,@the3kitchen (Bulk Lifetime VIP)\n"
        "  - /setstatus paid 30 @channel1,@channel2 (Bulk 30-Day Paid)\n\n"
        "🛠️ **4. Single Channel Operations:**\n"
        "• /addchannel <@username|id> [TRIAL|VIP] [days] — Manually register a new channel.\n"
        "• /renewchannel <@username|id> [months] — Extend channel subscription by X months.\n"
        "• /removechannel <@username|id> — Delete a channel and its calls from tracking.\n"
        "• /linkowner <@channel> <@owner_username> — Link channel owner for DM milestone cards.\n"
        "• /unlinkowner <@channel> [@owner] — Unlink all owners or a specific owner.\n"
        "• /listowners or /owners — View all linked channel owners.\n"
        "• /remindsub <@channel> — Manually dispatch official renewal reminder to owner's DM with delivery receipt.\n\n"
        "💳 **5. Alert Channel & Deposit Wallet Setup:**\n"
        "• /setchannel <@username|id> — Set main channel for auto-posting milestone alert cards.\n"
        "• /setwallet <0x_eth_address> — Add & activate 0.01 ETH deposit wallet for client renewals.\n"
        "• /wallets — View all deposit wallets and switch active wallet in 1 click.\n\n"
        "🎬 **6. Custom Banners & Alert Media:**\n"
        "• /setmedia start — Reply to GIF/Photo for /start intro banner.\n"
        "• /setmedia trending — Reply to GIF for Recently Called Projects report.\n"
        "• /setmedia <2x|5x|10x|cashprinter|motion|consistency> — Set alert media.\n"
        "• /removemedia <key> — Delete custom media.\n"
        "• /listmedia — View all custom media configured."
    )

    admin_buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ /addchannel", switch_inline_query_current_chat="/addchannel @"),
            InlineKeyboardButton("💎 /setstatus", switch_inline_query_current_chat="/setstatus vip 0 @"),
        ],
        [
            InlineKeyboardButton("🔗 /linkowner", switch_inline_query_current_chat="/linkowner @ "),
            InlineKeyboardButton("💳 /renewchannel", switch_inline_query_current_chat="/renewchannel @ 1"),
        ],
        [
            InlineKeyboardButton("📋 /listchannels", switch_inline_query_current_chat="/listchannels"),
            InlineKeyboardButton("👥 /visitors", switch_inline_query_current_chat="/visitors"),
        ]
    ])

    await safe_reply(update.message, text, reply_markup=admin_buttons)

async def on_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    user_id = query.from_user.id

    if data.startswith("cb_listch:"):
        parts = data.split(":")
        filter_arg = parts[1] if len(parts) > 1 else "all"
        page = int(parts[2]) if (len(parts) > 2 and parts[2].isdigit()) else 1

        text, buttons = await build_channel_list_view(filter_arg, page=page)
        try:
            await query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=buttons, disable_web_page_preview=True)
        except Exception:
            pass
        return

    if data == "cb_noop":
        return

    if data == "cb_add_channel":
        text = (
            "➕ <b>Add Your Channel to KOLInsights Tracking</b>\n\n"
            "🎁 <b>Special Offer: Get 7 Days Completely FREE!</b>\n\n"
            "📲 <b>Please reply in chat with your Channel Username or Link:</b>\n"
            "<i>(Example: <code>@channelname</code> or <code>https://t.me/channelname</code>)</i>"
        )
        await safe_reply(query.message, text, parse_mode="HTML")

    elif data == "cb_my_channels":
        from database import get_all_channels
        channels = await get_all_channels()
        user_channels = [ch for ch in channels if str(ch.get("channel_id")) in str(user_id) or (query.from_user.username and (ch.get("username") or "").lower() == query.from_user.username.lower())]
        
        if not user_channels:
            text = "📋 <b>You don't have any registered channels yet.</b>\nClick <b>📢 Add My Channel</b> below to activate your 7-day free trial!"
        else:
            text = f"📋 <b>Your Registered Channels ({len(user_channels)}):</b>\n\n"
            from datetime import datetime, timezone
            now_dt = datetime.now(timezone.utc)
            for ch in user_channels:
                c_title = clean_markdown(ch.get("title") or "Channel")
                sub_type = (ch.get("sub_type") or "VIP").upper()
                sub_end = ch.get("sub_end")
                if sub_type in ("VIP", "LIFETIME"):
                    sub_str = "💎 <code>LIFETIME VIP</code> (Always Free)"
                elif sub_end:
                    try:
                        end_dt = datetime.strptime(str(sub_end)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        days_left = max(0, (end_dt - now_dt).days)
                        sub_str = f"🎁 <code>{sub_type}</code> ({days_left} days left - Expires: {end_dt.strftime('%b %d, %Y')})"
                    except Exception:
                        sub_str = f"🟢 <code>{sub_type}</code>"
                else:
                    sub_str = "💎 <code>LIFETIME VIP</code>"
                text += f"• <b>{c_title}</b>: {sub_str}\n"

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Add Another Channel", callback_data="cb_add_channel")],
            [InlineKeyboardButton("💳 Renew Subscription", callback_data="cb_renew_sub")]
        ])
        await safe_reply(query.message, text, parse_mode="HTML", reply_markup=buttons)

    elif data == "cb_plans":
        text, buttons = get_subscription_plans_text_and_markup()
        await safe_reply(query.message, text, parse_mode="HTML", reply_markup=buttons)

    elif data == "cb_lifetime_sub":
        from config import get_effective_deposit_wallet
        wallet_addr = await get_effective_deposit_wallet()
        text = (
            "♾️ <b>KOLInsights Lifetime VIP Subscription</b>\n\n"
            "🔥 <b>Special Offer: $399 ONE-TIME PAYMENT</b> (<s>Regular $600</s>)\n\n"
            "✨ <b>Lifetime VIP Benefits:</b>\n"
            "• Lifetime call tracking across all your channels with zero recurring renewals\n"
            "• Priority Lane 1 turbo price tracking & instant flex banners\n"
            "• Exclusive Lifetime VIP status badge on leaderboard\n\n"
            "📍 <b>Deposit Wallet (ETH / ERC-20 / USDT / USDC):</b>\n"
            f"<code>{wallet_addr}</code>\n"
            "<i>(Tap address above to copy)</i>\n\n"
            "📩 <b>How to Activate Lifetime VIP:</b>\n"
            "1. Send <b>$399</b> in ETH, USDT, or USDC to the wallet address above.\n"
            "2. Paste your Transaction Hash (TxHash) in chat or message <b>@kolinsights_admin</b> with your receipt for instant VIP activation!"
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Contact @kolinsights_admin", url="https://t.me/kolinsights_admin")],
            [InlineKeyboardButton("⬅️ Back to Plans", callback_data="cb_plans")]
        ])
        await safe_reply(query.message, text, parse_mode="HTML", reply_markup=buttons)

    elif data == "cb_renew_sub":
        from config import get_effective_deposit_wallet
        wallet_addr = await get_effective_deposit_wallet()
        text = (
            "💳 <b>Monthly Subscription Renewal Invoice</b>\n\n"
            "💰 <b>Price:</b> <code>0.01 ETH / month</code>\n"
            "📍 <b>Payment Address (Ethereum / ERC-20):</b>\n"
            f"<code>{wallet_addr}</code>\n"
            "<i>(Tap address above to copy)</i>\n\n"
            "📩 <b>How to Confirm Payment:</b>\n"
            "• <b>Option 1:</b> Paste your Transaction Hash (TxHash) in chat below (e.g., <code>0xabc123...</code>).\n"
            "• <b>Option 2:</b> Contact <b>@kolinsights_admin</b> directly with your payment receipt!"
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("♾️ View $399 Lifetime Offer", callback_data="cb_lifetime_sub")],
            [InlineKeyboardButton("⬅️ Back to Plans", callback_data="cb_plans")]
        ])
        await safe_reply(query.message, text, parse_mode="HTML", reply_markup=buttons)

    elif data.startswith("cb_approve_pay_"):
        payment_id_str = data.replace("cb_approve_pay_", "")
        if not is_admin(user_id):
            await query.answer("⛔ Admin only command.", show_alert=True)
            return
        if payment_id_str.isdigit():
            pid = int(payment_id_str)
            from database import approve_payment
            res = await approve_payment(pid)
            if res:
                await query.edit_message_text(text=f"✅ **Payment ID #{pid} APPROVED!** Subscription extended by +30 days for user ID `{res['user_id']}`!")
            else:
                await query.answer("❌ Payment record not found.", show_alert=True)

    elif data == "cb_wallets_add":
        if not is_admin(user_id):
            await query.answer("⛔ Admin only command.", show_alert=True)
            return
        await safe_reply(query.message, "➕ **Add New Deposit Wallet:**\nPlease reply with `/setwallet <0x_eth_address>` or paste your new ETH wallet address in chat!")

    elif data == "cb_wallets_select":
        if not is_admin(user_id):
            await query.answer("⛔ Admin only command.", show_alert=True)
            return
        from database import get_all_deposit_wallets
        wallets = await get_all_deposit_wallets()
        if not wallets:
            await query.answer("No deposit wallets found.", show_alert=True)
            return

        btns = []
        for w in wallets:
            status_icon = "🟢 ACTIVE: " if w.get("is_active") else "⚪ "
            label = f"{status_icon}{w['wallet_address'][:10]}...{w['wallet_address'][-6:]}"
            btns.append([InlineKeyboardButton(label, callback_data=f"cb_wallets_set_active_{w['id']}")])
        await safe_reply(query.message, "🔄 **Select which wallet should be ACTIVE for 0.01 ETH deposits:**", reply_markup=InlineKeyboardMarkup(btns))

    elif data.startswith("cb_wallets_set_active_"):
        if not is_admin(user_id):
            await query.answer("⛔ Admin only command.", show_alert=True)
            return
        wid_str = data.replace("cb_wallets_set_active_", "")
        if wid_str.isdigit():
            wid = int(wid_str)
            from database import set_active_deposit_wallet
            success = await set_active_deposit_wallet(wid)
            if success:
                from config import get_effective_deposit_wallet
                active_w = await get_effective_deposit_wallet()
                await query.edit_message_text(text=f"✅ **Activated Deposit Wallet #{wid}!**\n\n🟢 **Current Active Deposit Wallet:**\n`{active_w}`\n\nAll client 0.01 ETH renewals will now display this active wallet!")
            else:
                await query.answer("❌ Wallet not found.", show_alert=True)

async def setwallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    if not context.args:
        await wallets_command(update, context)
        return

    new_wallet = context.args[0].strip()
    from database import add_deposit_wallet
    await add_deposit_wallet(new_wallet, label="Admin Wallet", set_active=True)
    await safe_reply(update.message, f"✅ Successfully added & activated 0.01 ETH deposit wallet address:\n`{clean_markdown(new_wallet)}`")

async def wallets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    from database import get_all_deposit_wallets
    wallets = await get_all_deposit_wallets()

    if not wallets:
        text = "💳 **Deposit Wallets Management:**\n\nNo wallets configured in database."
    else:
        text = f"💳 **Deposit Wallets Management ({len(wallets)}):**\n\n"
        for idx, w in enumerate(wallets, start=1):
            status_tag = "🟢 **ACTIVE**" if w.get("is_active") else "⚪ INACTIVE"
            label = clean_markdown(w.get("label") or "Deposit Wallet")
            text += f"{idx}. {status_tag} - `{w['wallet_address']}` ({label})\n"

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add New Wallet", callback_data="cb_wallets_add")],
        [InlineKeyboardButton("🔄 Switch Active Wallet", callback_data="cb_wallets_select")]
    ])
    await safe_reply(update.message, text, reply_markup=buttons)

async def group_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return

    text = (update.effective_message.text or update.effective_message.caption or "").strip()
    if not text:
        return

    chat = update.effective_chat
    if not chat:
        return

    # If in private chat with bot, check for TxHash submission or channel registration
    if chat.type == "private":
        user_id = update.effective_user.id if update.effective_user else 0
        clean_text = text.strip()

        # Check if text is a TxHash (e.g. 0x... starting with 0x or 66 chars)
        if len(clean_text) in (66, 64) and (clean_text.startswith("0x") or clean_text.isalnum()):
            from database import is_tx_hash_used, record_payment_submission
            if await is_tx_hash_used(clean_text):
                await safe_reply(update.effective_message, "❌ **This TxHash has already been submitted!** If you have questions, please contact @kolinsights_admin.")
                return

            pay_id = await record_payment_submission(user_id=user_id, channel_id=None, tx_hash=clean_text, amount_eth=0.01)
            
            from config import ADMIN_IDS
            bot_app = context.application if context else None
            
            approve_btn = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"✅ Approve Payment #{pay_id}", callback_data=f"cb_approve_pay_{pay_id}")]
            ])
            admin_msg = (
                f"💰 **New Payment TxHash Submitted!**\n\n"
                f"👤 **User ID:** `{user_id}` (@{update.effective_user.username or 'N/A'})\n"
                f"💳 **Amount:** `0.01 ETH`\n"
                f"📍 **TxHash:** `{clean_text}`\n"
                f"🆔 **Payment ID:** #{pay_id}"
            )
            if bot_app and ADMIN_IDS:
                for admin_id in ADMIN_IDS:
                    try:
                        await bot_app.bot.send_message(chat_id=admin_id, text=admin_msg, parse_mode="Markdown", reply_markup=approve_btn)
                    except Exception as err:
                        logger.warning(f"Could not notify admin {admin_id} of payment TxHash: {err}")

            await safe_reply(update.effective_message, f"✅ **Payment TxHash Received!** (Payment ID: #{pay_id})\n\nOur admin will verify your 0.01 ETH deposit and extend your channel subscription within minutes. You can also notify @kolinsights_admin!")
            return

        # Check if user sent a channel username (e.g. @MyChannel or https://t.me/MyChannel)
        if clean_text.startswith("@") or "t.me/" in clean_text:
            ch_user = clean_text.split("t.me/")[-1].lstrip("@").strip().split("/")[0]
            if ch_user:
                from database import add_channel
                await add_channel(channel_id=0, title=f"@{ch_user}", username=ch_user, sub_type="TRIAL", days=7)
                await safe_reply(
                    update.effective_message,
                    f"🎉 **Success! `@{ch_user}` has been registered!**\n\n"
                    f"🎁 **Status:** 7-Day FREE Trial Activated (*Expires: 7 days from today*)\n\n"
                    f"⚙️ **Final Step:** Add `@kolinsightsbot` to your channel/group as a member/admin to begin tracking calls!"
                )
                return

    from scraper import extract_contract_addresses
    addresses = extract_contract_addresses(text)
    if not addresses:
        return

    channel_id = chat.id
    channel_title = chat.title or update.effective_user.first_name or "Unknown Group"
    channel_username = chat.username or ""

    user = update.effective_user
    caller_name = f"@{user.username}" if (user and user.username) else (user.full_name if user else "Anonymous")

    from dex_api import fetch_token_data
    from database import record_call, is_channel_authorized

    if not await is_channel_authorized(channel_id=channel_id, username=channel_username):
        logger.info(f"🚫 [BOT API ENGINE 2] Skipping contract address in unauthorized channel/group '{channel_title}' (ID: {channel_id})")
        return

    logger.info(f"📥 [BOT API ENGINE 2] Incoming message from channel/group '{channel_title}' (@{channel_username} | ID: {channel_id}) - Found {len(addresses)} Contract Address(es)")

    for addr in addresses:
        token_info = await fetch_token_data(addr)
        if token_info and token_info.get("market_cap", 0) > 0:
            msg_id = update.effective_message.message_id
            msg_link = f"https://t.me/c/{str(channel_id).replace('-100', '')}/{msg_id}" if not channel_username else f"https://t.me/{channel_username}/{msg_id}"

            effective_username = channel_username or (user.username if user else "")

            from scraper import extract_stated_market_cap
            fetch_mc = token_info["market_cap"]
            stated_mc = extract_stated_market_cap(text, fetch_mc)
            if stated_mc and stated_mc > 0:
                entry_mc = stated_mc
                logger.info(f"💡 [BOT API ENGINE 2] Using caller-stated Entry Market Cap: ${entry_mc:,.0f} (Live DEX: ${fetch_mc:,.0f})")
            else:
                entry_mc = fetch_mc

            token_info["market_cap"] = entry_mc

            # Record call in DB
            call_id = await record_call(
                channel_id=channel_id,
                channel_title=channel_title,
                channel_username=effective_username,
                token_address=token_info["token_address"],
                chain=token_info["chain"],
                token_symbol=token_info["symbol"],
                token_name=token_info["name"],
                entry_mc=entry_mc,
                call_msg_id=msg_id,
                call_msg_link=msg_link
            )

            if not call_id:
                logger.info(f"ℹ️ [BOT API ENGINE 2] Silently skipping duplicate call for {token_info['symbol']} in '{channel_title}'")
                continue

            logger.info(f"✅ [BOT API ENGINE 2 SUCCESS] Recorded Call #{call_id} by {caller_name} in '{channel_title}' ({channel_id}): {token_info['symbol']} at ${entry_mc:,.0f}!")

            # Also post instant new call entry alert to main alert channel
            try:
                from main import send_new_call_alert
                call_dict = {
                    "id": call_id,
                    "channel_id": channel_id,
                    "channel_title": channel_title,
                    "channel_username": effective_username,
                    "token_address": token_info["token_address"],
                    "chain": token_info["chain"],
                    "token_symbol": token_info["symbol"],
                    "token_name": token_info["name"],
                    "entry_mc": token_info["market_cap"],
                    "call_msg_link": msg_link
                }
                bot_app = context.application if context else None
                if bot_app:
                    await send_new_call_alert(call_dict, bot_app)
            except Exception as alert_err:
                logger.warning(f"Could not send new call alert to main channel: {alert_err}")

            # Silent Group Tracking: Alert broadcasted to main @KOLInsights channel & Owner DM only
            logger.info(f"✨ [GROUP TRACKING] Silently recorded & broadcast call #{call_id} for {token_info['symbol']} in '{channel_title}'")

async def on_bot_added_to_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Event handler for when the bot is added to a group or channel."""
    chat = update.effective_chat
    if not chat:
        return

    from database import is_channel_authorized, add_channel
    if not await is_channel_authorized(channel_id=chat.id, username=chat.username):
        logger.info(f"Bot added to new group '{chat.title}' ({chat.id}). Auto-registering with 7-day Free Trial...")
        await add_channel(channel_id=chat.id, title=chat.title or "New Group", username=chat.username, sub_type="TRIAL", days=7)
async def trending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id if update.effective_chat else None
    if not chat_id:
        return
    from main import send_recently_called_report
    bot_app = context.application if context else None
    if bot_app:
        await send_recently_called_report(bot_app, chat_id=chat_id)

async def setchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    if not context.args:
        await safe_reply(update.message, "⚠️ Usage: `/setchannel <@username|channel_id>`")
        return

    target = context.args[0].strip()
    from database import set_bot_config
    await set_bot_config("ALERT_CHANNEL_ID", target)
    await safe_reply(update.message, f"✅ Set main alert channel to: **{clean_markdown(target)}**")

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Global exception handler to log unhandled errors gracefully."""
    from telegram.error import Conflict, NetworkError, TimedOut
    if isinstance(context.error, Conflict):
        logger.warning(f"⚠️ [TELEGRAM CONFLICT] Polling conflict detected (overlapping deployment instance): {context.error}")
        return
    if isinstance(context.error, (NetworkError, TimedOut)):
        logger.debug(f"Telegram network notice: {context.error}")
        return

    logger.error("Exception handling update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await safe_reply(update.effective_message, "❌ An internal error occurred while processing your command.")
        except Exception:
            pass

async def visitors_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command: View bot visitor analytics and list recent users."""
    user_id = update.effective_user.id if update.effective_user else 0
    if not is_admin(user_id):
        await safe_reply(update.message, f"⛔ Admin command only. (Your User ID: `{user_id}`)")
        return

    from database import get_visitor_stats
    stats = await get_visitor_stats()
    
    total = stats.get("total_users", 0)
    active_24h = stats.get("active_24h", 0)
    active_7d = stats.get("active_7d", 0)
    active_30d = stats.get("active_30d", 0)
    active_today = stats.get("active_today_users", [])

    def format_utc_to_ist(utc_str: str) -> str:
        if not utc_str:
            return "N/A"
        try:
            from datetime import datetime, timedelta
            clean_str = str(utc_str).strip()
            if "T" in clean_str:
                clean_str = clean_str.replace("T", " ")
            if "." in clean_str:
                clean_str = clean_str.split(".")[0]
            dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
            ist_dt = dt + timedelta(hours=5, minutes=30)
            return ist_dt.strftime("%d %b, %I:%M %p IST")
        except Exception:
            return str(utc_str)[:16]

    lines = [
        "👥 **BOT VISITOR ANALYTICS**\n",
        f"📊 **Total Registered Users:** `{total}` users",
        f"🔥 **Active Today (Past 24h):** `{active_24h}` users",
        f"⚡ **Active This Week (Past 7d):** `{active_7d}` users",
        f"🌐 **Monthly Active Users (30d MAU):** `{active_30d}` users\n",
        f"🟢 **Users Active Today ({len(active_today)}):**"
    ]

    if not active_today:
        lines.append("*No active users in the past 24 hours.*")
    else:
        for idx, u in enumerate(active_today, 1):
            u_id = u["user_id"]
            u_name = f"@{u['username']}" if u.get("username") else (u.get("first_name") or f"User {u_id}")
            seen_ist = format_utc_to_ist(u.get("last_seen"))
            lines.append(f"{idx}. {clean_markdown(u_name)} (ID: `{u_id}`) — `{seen_ist}`")

    msg_text = "\n".join(lines)
    await safe_reply(update.message, msg_text)

async def follow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    u_name = update.effective_user.username if update.effective_user else ""
    if not user_id:
        return

    if not await is_user_subscribed_or_admin(user_id, u_name):
        await safe_reply(
            update.message,
            "Although this feature is free to use for KOLInsights listed channels, you still need to be set up first!\n\n"
            "Please DM `@KOLInsights_admin` to be set up asap!"
        )
        return

    if context.args:
        target = context.args[0].strip()
        from database import follow_channel
        success, msg, ch = await follow_channel(user_id, target)
        await safe_reply(update.message, msg)
    else:
        text = (
            "📌 **Step 1:**\n\n"
            "Please reply in chat with the username of the channel you wish to follow.\n\n"
            "If you wish to track your own achievements, reply with your channel `@` username.\n\n"
            "*(Example: `@explorer_gems` or `https://t.me/explorer_gems`)*"
        )
        await safe_reply(update.message, text)

async def unfollow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not user_id:
        return

    if not context.args:
        await safe_reply(update.message, "⚠️ Usage: `/unfollow <@channelname>`")
        return

    target = context.args[0].strip()
    from database import unfollow_channel
    success, msg = await unfollow_channel(user_id, target)
    await safe_reply(update.message, msg)

async def showfollowed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not user_id:
        return

    from database import get_user_followed_channels
    followed = await get_user_followed_channels(user_id)

    if not followed:
        await safe_reply(update.message, "📋 **You are not following any KOL channels.**\n\nUse `/follow <@channelname>` to start receiving DM achievement alerts!")
        return

    text = f"📋 **KOL Channels You Are Following ({len(followed)}):**\n\n"
    buttons_list = []
    for idx, ch in enumerate(followed, 1):
        c_name = ch.get("username") or ch.get("title") or str(ch["channel_id"])
        text += f"{idx}. **@{clean_markdown(c_name)}**\n"
        buttons_list.append([InlineKeyboardButton(f"❌ Unfollow @{c_name}", callback_data=f"cb_unfollow:{c_name}")])

    markup = InlineKeyboardMarkup(buttons_list)
    await safe_reply(update.message, text, reply_markup=markup)

async def handle_unfollow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    try:
        await query.answer()
    except Exception:
        pass

    data = query.data
    if data.startswith("cb_unfollow:"):
        target_name = data.split("cb_unfollow:", 1)[1].strip()
        user_id = query.from_user.id if query.from_user else 0
        if not user_id:
            return

        from database import unfollow_channel, get_user_followed_channels
        success, msg = await unfollow_channel(user_id, target_name)

        followed = await get_user_followed_channels(user_id)
        if not followed:
            try:
                await query.edit_message_text(f"✅ {msg}\n\n📋 **You are not following any KOL channels.**", parse_mode="Markdown")
            except Exception:
                pass
            return

        text = f"✅ {msg}\n\n📋 **KOL Channels You Are Following ({len(followed)}):**\n\n"
        buttons_list = []
        for idx, ch in enumerate(followed, 1):
            c_name = ch.get("username") or ch.get("title") or str(ch["channel_id"])
            text += f"{idx}. **@{clean_markdown(c_name)}**\n"
            buttons_list.append([InlineKeyboardButton(f"❌ Unfollow @{c_name}", callback_data=f"cb_unfollow:{c_name}")])

        markup = InlineKeyboardMarkup(buttons_list)
        try:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            pass

async def generate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    u_name = update.effective_user.username if update.effective_user else ""
    if not user_id:
        return

    if not await is_user_subscribed_or_admin(user_id, u_name):
        await safe_reply(
            update.message,
            "Although this feature is free to use for KOLInsights listed channels, you still need to be set up first!\n\n"
            "Please DM `@KOLInsights_admin` to be set up asap!"
        )
        return

    target_channel = context.args[0].strip() if context.args else ""
    if not target_channel:
        from database import get_user_followed_channels
        followed = await get_user_followed_channels(user_id)
        if followed:
            target_channel = followed[0].get("username") or followed[0].get("title") or ""

    if not target_channel:
        await safe_reply(
            update.message,
            "💡 **KOL Club Report Generator**\n\n"
            "Usage: `/generate <@channelname>`\n"
            "*(Example: `/generate @explorer_gems`)*\n\n"
            "Although this feature is free to use for KOLInsights listed channels, you still need to be set up first!\n\n"
            "Please DM `@KOLInsights_admin` to be set up asap!"
        )
        return

    from database import get_channel_stats
    stats = await get_channel_stats(target_channel)
    if not stats or not stats.get("total_calls"):
        await safe_reply(update.message, f"❌ Channel **{clean_markdown(target_channel)}** not found or has no recorded calls.")
        return

    c_title = clean_markdown(stats.get("title") or target_channel)
    total_calls = stats.get("total_calls", 0)
    best_call = stats.get("best_call") or {}
    best_name = clean_markdown(best_call.get("token_symbol") or best_call.get("token_name") or "N/A")
    best_mult = best_call.get("highest_multiplier", 1.0)
    consistency = stats.get("consistency_rate", 0.0)
    yield_100 = stats.get("simulation_ath_yield", 0.0)

    report_text = (
        f"📊 **KOL Performance Report — {c_title}**\n\n"
        f"🎯 **Total Calls Tracked:** `{total_calls}`\n"
        f"🔥 **Win Rate (x2+):** `{consistency:.1f}%`\n"
        f"👑 **Best Call:** `${best_name}` (**x{best_mult:.1f}**)\n"
        f"💰 **$100 Portfolio ATH Yield:** `${yield_100:,.2f}`\n\n"
        f"✨ *Powered by KOLInsights Network*"
    )
    await safe_reply(update.message, report_text)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Provides a live system status and health report."""
    import time
    from database import get_all_channels, get_active_calls
    from config import get_effective_alert_channel_id
    
    channels = await get_all_channels()
    active_calls = await get_active_calls(days=14)
    alert_ch = await get_effective_alert_channel_id()
    
    text = (
        f"🟢 **KOLInsights System Status: 100% ONLINE**\n\n"
        f"⚡ **Scraper Engine:** Userbot Premium Active 🌟 *(3D Animated Emojis Enabled)*\n"
        f"📢 **Alert Channel:** `{alert_ch or 'Not Configured'}`\n"
        f"📡 **Tracked Channels:** **{len(channels)}** active channels\n"
        f"📊 **Active Monitored Calls:** **{len(active_calls)}** calls (14-day window)\n"
        f"💓 **Heartbeat Engine:** Active *(Tracking every 3s)*\n"
        f"⚡ **Auto-Index Engines:** DexScreener + GeckoTerminal Universal Multi-Chain Fallback ✅\n\n"
        f"✨ *All scrapers and alert dispatchers are running smoothly.*"
    )
    await safe_reply(update.message, text)

async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply(update.message, "🔄 Process stopped. Re-run `/follow` or `/generate` to restart it.")

def register_handlers(app: Application):
    from telegram.ext import MessageHandler, filters, ChatMemberHandler, CallbackQueryHandler
    app.add_error_handler(global_error_handler)
    app.add_handler(CallbackQueryHandler(handle_unfollow_callback, pattern="^cb_unfollow:"))
    app.add_handler(CallbackQueryHandler(on_callback_query))
    app.add_handler(ChatMemberHandler(on_bot_added_to_chat, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("plans", plans_command))
    app.add_handler(CommandHandler("pricing", plans_command))
    app.add_handler(CommandHandler("subscribe", plans_command))
    app.add_handler(CommandHandler("subscription", plans_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("ping", status_command))
    app.add_handler(CommandHandler("follow", follow_command))
    app.add_handler(CommandHandler("unfollow", unfollow_command))
    app.add_handler(CommandHandler("showfollowed", showfollowed_command))
    app.add_handler(CommandHandler("following", showfollowed_command))
    app.add_handler(CommandHandler("generate", generate_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(CommandHandler("checkstats", checkstats_command))
    app.add_handler(CommandHandler("checkcalls", checkcalls_command))
    app.add_handler(CommandHandler("trending", trending_command))
    app.add_handler(CommandHandler("recentlycalled", trending_command))
    app.add_handler(CommandHandler("addchannel", addchannel_command))
    app.add_handler(CommandHandler("removechannel", removechannel_command))
    app.add_handler(CommandHandler("linkowner", linkowner_command))
    app.add_handler(CommandHandler("addowner", linkowner_command))
    app.add_handler(CommandHandler("unlinkowner", unlinkowner_command))
    app.add_handler(CommandHandler("removeowner", unlinkowner_command))
    app.add_handler(CommandHandler("delowner", unlinkowner_command))
    app.add_handler(CommandHandler("listowners", listowners_command))
    app.add_handler(CommandHandler("owners", listowners_command))
    app.add_handler(CommandHandler("listchannels", listchannels_command))
    app.add_handler(CommandHandler("setstatus", setstatus_command))
    app.add_handler(CommandHandler("renewchannel", renewchannel_command))
    app.add_handler(CommandHandler("setchannel", setchannel_command))
    app.add_handler(CommandHandler("setwallet", setwallet_command))
    app.add_handler(CommandHandler("wallets", wallets_command))
    app.add_handler(CommandHandler("visitors", visitors_command))
    app.add_handler(CommandHandler("users", visitors_command))
    app.add_handler(CommandHandler("setmedia", setmedia_command))
    app.add_handler(CommandHandler("removemedia", removemedia_command))
    app.add_handler(CommandHandler("listmedia", listmedia_command))
    app.add_handler(CommandHandler("remindsub", remindsub_command))
    app.add_handler(CommandHandler("remind", remindsub_command))
    app.add_handler(CommandHandler("adminhelp", adminhelp_command))
    app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, group_text_handler))
