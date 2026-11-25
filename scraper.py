import re
import logging
import asyncio
from typing import List, Optional
from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat

from config import TG_API_ID, TG_API_HASH
from database import record_call, get_all_channels, add_channel, is_channel_authorized
from dex_api import fetch_token_data

logger = logging.getLogger(__name__)

SOLANA_CA_REGEX = re.compile(r'(?:[a-zA-Z0-9]+_)?\b([1-9A-HJ-NP-Za-km-z]{32,44})\b')
EVM_CA_REGEX = re.compile(r'(?:[a-zA-Z0-9]+_)?\b(0x[a-fA-F0-9]{40,64})\b')

URL_CA_REGEX = re.compile(
    r'(?:dexscreener\.com/[^/\s]+/|pump\.fun/(?:coin/)?|solscan\.io/(?:token|account)/|etherscan\.io/token/|basescan\.org/token/|birdeye\.so/[^/\s]+/token/|dextools\.io/app/[^/\s]+/pair-explorer/|dextools\.io/[^/\s]+/[^/\s]+/|geckoterminal\.com/[^/\s]+/pools/|geckoterminal\.com/[^/\s]+/tokens/|bullx\.io/[^?\s]+|photon-[^/\s]+\.tinyastro\.io/en/lp/|moonshot\.money/token/|gmgn\.ai/(?:m/)?(?:[a-zA-Z0-9_-]+/)?token/)(?:[a-zA-Z0-9]+_)?(0x[a-fA-F0-9]{40,64}|[1-9A-HJ-NP-Za-km-z]{32,44})',
    re.IGNORECASE
)

def extract_contract_addresses(text: str) -> List[str]:
    """Extract potential Solana and EVM contract addresses from text, DEX URLs, and GMGN referral links."""
    if not text:
        return []

    # 1. Extract addresses directly embedded inside URLs
    url_matches = [m.group(1) for m in URL_CA_REGEX.finditer(text) if m.group(1)]

    # 2. Extract standalone EVM and Solana regex matches
    evm_matches = [m.group(1) for m in EVM_CA_REGEX.finditer(text) if m.group(1)]
    sol_matches = [m.group(1) for m in SOLANA_CA_REGEX.finditer(text) if m.group(1)]

    sol_filtered = []
    for addr in sol_matches:
        if not addr.isdigit() and len(addr) >= 32:
            sol_filtered.append(addr)

    raw_candidates = list(set(url_matches + evm_matches + sol_filtered))
    
    # Filter out truncated substrings if the full longer address is present
    final_addrs = []
    for addr in raw_candidates:
        if not any(other != addr and other.lower().startswith(addr.lower()) for other in raw_candidates):
            final_addrs.append(addr)

    return final_addrs

def extract_stated_market_cap(text: str, fetch_mc: Optional[float] = None) -> Optional[float]:
    """Extracts stated market cap (e.g. '$40k', '40k MC', '$150k', 'MCAP $32.4K', 'Bought at 80k', '(40k)') from call message text if present."""
    if not text:
        return None

    # Exclude pure liquidity/volume lines from false matches
    cleaned_lines = []
    for line in text.splitlines():
        if re.search(r'\b(?:liquidity|liquid|vol|volume|24h\s*vol)\b', line, re.IGNORECASE) and not re.search(r'\b(?:mcap|market\s*cap|mc)\b', line, re.IGNORECASE):
            continue
        cleaned_lines.append(line)
    filtered_text = "\n".join(cleaned_lines)

    def parse_tuples(tup_list):
        res = []
        for val_str, unit in tup_list:
            try:
                val = float(val_str)
                u = unit.upper()
                if u == 'K':
                    val *= 1_000
                elif u == 'M':
                    val *= 1_000_000
                elif u == 'B':
                    val *= 1_000_000_000
                if val > 0:
                    res.append(val)
            except Exception:
                pass
        return res

    # 1. High Priority: Explicit Market Cap / Entry Keyword Patterns & Currency tags
    explicit_mcap_pattern = re.compile(
        r'\b(?:mcap|market\s*cap|mc|cap|entry|entered|bought|buy|aped|in\s*at|called\s*at|around|about|at|@)\s*[:=]?\s*\$?(\d+(?:\.\d+)?)\s*([kmbKMB])\b|\$?(\d+(?:\.\d+)?)\s*([kmbKMB])\s*(?:mcap|market\s*cap|mc|cap)\b|\(\s*\$?(\d+(?:\.\d+)?)\s*([kmbKMB])\s*\)|\$(\d+(?:\.\d+)?)\s*([kmbKMB])\b',
        re.IGNORECASE
    )
    explicit_matches = []
    for m in explicit_mcap_pattern.finditer(filtered_text):
        groups = [g for g in m.groups() if g]
        if len(groups) >= 2:
            explicit_matches.append((groups[0], groups[1]))

    # 2. General pattern: Allow bare 'k/K' (e.g. '40k', '130k'), but require '$' for 'm/M' or 'b/B' to prevent '3m chart' / '5m ago' time false-matches!
    general_pattern = re.compile(r'\b(\d+(?:\.\d+)?)\s*([kK])\b|\$(\d+(?:\.\d+)?)\s*([mMbB])\b', re.IGNORECASE)
    general_matches_raw = general_pattern.findall(filtered_text)
    general_matches = []
    for g in general_matches_raw:
        valid_pairs = [x for x in g if x]
        if len(valid_pairs) >= 2:
            general_matches.append((valid_pairs[0], valid_pairs[1]))

    explicit_candidates = parse_tuples(explicit_matches)
    general_candidates = parse_tuples(general_matches)

    candidates = explicit_candidates if explicit_candidates else general_candidates
    if not candidates:
        return None

    # If fetch_mc (live DEX MC) is available, pick candidate within 0.01x - 150.0x range!
    if fetch_mc and fetch_mc > 0:
        valid_candidates = [c for c in candidates if 0.01 <= (c / fetch_mc) <= 150.0]
        if valid_candidates:
            if explicit_candidates:
                kw_valid = [c for c in explicit_candidates if 0.01 <= (c / fetch_mc) <= 150.0]
                if kw_valid:
                    return kw_valid[0]
            return min(valid_candidates, key=lambda c: abs(c - fetch_mc))
        else:
            return None

    filtered = [c for c in candidates if c < 500_000_000]
    return filtered[0] if filtered else candidates[0]

INVALID_SYMBOLS = {"PAIR", "EXPLORER", "TOKEN", "CONTRACT", "ADDRESS", "TX", "HASH", "SCAN", "POOL", "ROBINHOOD", "DEX", "HOLD", "BUY", "SELL", "MCAP", "SOL", "ETH", "BNB"}

def extract_token_metadata_fallback(scan_text: str, addr: str):
    """Extracts chain, symbol, and token name from message text or embed when DEX has not indexed the pair yet."""
    chain = "solana" if not addr.startswith("0x") else "ethereum"
    low_text = scan_text.lower()
    if "robinhood" in low_text:
        chain = "robinhood"
    elif "arc" in low_text or "arch" in low_text:
        chain = "arc"
    elif "base" in low_text:
        chain = "base"
    elif "arbitrum" in low_text or "arb" in low_text:
        chain = "arbitrum"
    elif "bsc" in low_text or "binance" in low_text:
        chain = "bsc"

    symbol = "UNKNOWN"
    name = "New Launch"

    # Match GMGN / Terminal title pattern e.g. "USDUG - Unstable Global Dollar | GMGN.AI"
    gmgn_match = re.search(r'([A-Za-z0-9_]+)\s*-\s*([^|\-\n]+)', scan_text)
    if gmgn_match:
        s_cand = gmgn_match.group(1).strip().strip("_")
        n_cand = gmgn_match.group(2).strip().strip("_")
        if (
            2 <= len(s_cand) <= 12 
            and re.search(r'[A-Za-z]', s_cand) 
            and s_cand.upper() not in INVALID_SYMBOLS 
            and not n_cand.lower().startswith(("explorer", "http", "pair"))
        ):
            symbol = s_cand.upper()
            name = n_cand

    # Match ticker in text like $USDUG or $WADDLES (Must contain at least one letter and not be pure underscores)
    if symbol == "UNKNOWN":
        for sym_match in re.finditer(r'\$([A-Za-z0-9_]{2,10})\b', scan_text):
            cand = sym_match.group(1).strip("_").upper()
            if len(cand) >= 2 and re.search(r'[A-Za-z]', cand) and cand not in INVALID_SYMBOLS:
                symbol = cand
                name = symbol
                break

    # Match standalone uppercase words (e.g. "WDOG", "PEPE", "DINO", "PLUTO")
    if symbol == "UNKNOWN":
        COMMON_STOPWORDS = {
            "THE", "THIS", "BUY", "APE", "APED", "SELL", "NOW", "GEM", "GEMS", "CALL", "CALLS", 
            "NEW", "HOLD", "DEGEN", "SOL", "ETH", "BSC", "AND", "FOR", "WITH", "FROM", "HERE", 
            "MOON", "PUMP", "GOOD", "FAST", "SOON", "NEXT", "LIVE", "AIRDROP", "ALPHA", "WHALE", 
            "TOP", "HOT", "VERY", "BIG", "JUST", "LAUNCH", "LAUNCHED", "FAIR", "STEALTH", "OUT",
            "LOOKS", "SEND", "SENDING", "PLAY", "PLAYS", "INTO", "DONT", "FADE", "ENTRY", "TARGET",
            "RH", "CA", "ON", "IN", "AT", "IS", "MY", "ME", "OF", "OR", "TO", "UP", "OK", "SO", "IF"
        }
        for word_match in re.finditer(r'\b([A-Z]{3,10})\b', scan_text):
            w = word_match.group(1).upper()
            if w not in INVALID_SYMBOLS and w not in COMMON_STOPWORDS:
                symbol = w
                name = w
                break

    # Clean up name from decorative underscores
    if not name or name.strip("_ -") == "" or name.lower().startswith(("explorer", "pair", "http")):
        name = symbol if symbol != "UNKNOWN" else "New Launch"

    return chain, symbol, name

class CallScraper:
    def __init__(self, session_name: str = "alpharadar_userbot"):
        self.session_name = session_name
        self.client: Optional[TelegramClient] = None
        self._processed_events: dict = {}  # (channel_id, event_id) -> timestamp
        self.is_authorized = False
        self.me = None
        self.status_reason = "Initializing..."

    async def start(self):
        if not TG_API_ID or not TG_API_HASH:
            self.status_reason = "TG_API_ID / TG_API_HASH missing in environment"
            logger.info("ℹ️ [SCRAPER HYBRID ENGINE] TG_API_ID or TG_API_HASH missing. Operating in ENGINE 2: Official Telegram Bot API Fallback Mode.")
            return

        from telethon.sessions import StringSession
        from config import TG_SESSION_STRING

        try:
            clean_session = (TG_SESSION_STRING or "").strip()
            if (clean_session.startswith('"') and clean_session.endswith('"')) or (clean_session.startswith("'") and clean_session.endswith("'")):
                clean_session = clean_session[1:-1].strip()

            clean_api_id = int(str(TG_API_ID).strip(' "\''))
            clean_api_hash = str(TG_API_HASH).strip(' "\'')

            if clean_session:
                logger.info("🔑 [SCRAPER HYBRID ENGINE] Initializing Telethon Userbot via TG_SESSION_STRING...")
                self.client = TelegramClient(StringSession(clean_session), clean_api_id, clean_api_hash)
            else:
                self.status_reason = "TG_SESSION_STRING not set in Render environment"
                logger.info(f"📁 [SCRAPER HYBRID ENGINE] Initializing Telethon Userbot via local session file ({self.session_name}.session)...")
                self.client = TelegramClient(self.session_name, clean_api_id, clean_api_hash)

            await self.client.connect()
            is_auth = await self.client.is_user_authorized()
            if not is_auth:
                self.is_authorized = False
                self.status_reason = "Session string unauthorized or expired"
                logger.warning("⚠️ [SCRAPER HYBRID ENGINE] Telethon Userbot not authorized! Operating in ENGINE 2: Official Telegram Bot API Fallback Mode.")
                return

            self.is_authorized = True
            try:
                self.me = await self.client.get_me()
                user_display = f"@{self.me.username}" if (self.me and self.me.username) else (self.me.first_name if self.me else "Authorized")
                self.status_reason = f"Authorized as {user_display}"
            except Exception:
                self.status_reason = "Authorized"

            logger.info(f"✅ [STEP 7/7 SUCCESS] Telethon Userbot Authorized ({self.status_reason})! Operating in ENGINE 1: PREMIUM MODE.")

            # Spawn 60-second background Auto-Resolver loop for ID 0 channels
            asyncio.create_task(self._auto_resolver_loop())

        except Exception as e:
            self.status_reason = f"Connection error: {e}"
            logger.warning(f"⚠️ [SCRAPER HYBRID ENGINE] Userbot connection notice: {e} ➔ Operating in ENGINE 2: Official Telegram Bot API Fallback Mode.")
            return

        @self.client.on(events.NewMessage)
        @self.client.on(events.MessageEdited)
        async def handle_message_event(event):
            try:
                # Ensure message is from a channel, supergroup, or group
                chat = await event.get_chat()
                if not isinstance(chat, (Channel, Chat)):
                    return

                # Format channel_id for Bot API compatibility (-100xxxxxxxxx)
                raw_id = chat.id
                str_id = str(raw_id)
                if str_id.startswith("-100") or str_id.startswith("-"):
                    channel_id = raw_id
                elif isinstance(chat, Channel):
                    channel_id = int(f"-100{raw_id}")
                else:
                    channel_id = raw_id

                # Deduplicate message edit events from Telegram preview updates
                import time as _t
                event_key = (channel_id, getattr(event, "id", 0))
                now_t = _t.time()
                if getattr(event, "is_edit", False):
                    if event_key in self._processed_events and (now_t - self._processed_events[event_key]) < 180.0:
                        return
                self._processed_events[event_key] = now_t
                if len(self._processed_events) > 500:
                    old_keys = [k for k, v in self._processed_events.items() if now_t - v > 300.0]
                    for k in old_keys:
                        self._processed_events.pop(k, None)

                # Extract text and webpage preview embed text
                text = getattr(event, "raw_text", None) or getattr(event, "text", "") or ""
                media = getattr(event.message, "media", None) if getattr(event, "message", None) else None
                webpage = getattr(media, "webpage", None) if media else None
                
                # Exclude stale DexScreener cached title from stated MC parsing, but keep CA address scanning
                if webpage:
                    wp_title = getattr(webpage, "title", "") or ""
                    wp_desc = getattr(webpage, "description", "") or ""
                    wp_site = getattr(webpage, "site_name", "") or ""
                    wp_url = getattr(webpage, "url", "") or ""
                    scan_text = f"{text}\n{wp_title}\n{wp_desc}\n{wp_site}"
                    
                    # If embed is NOT from DexScreener (e.g. Defined.fi), allow embed text for stated MC
                    if "dexscreener" not in wp_url.lower() and "dexscreener" not in wp_site.lower():
                        stated_text_source = scan_text
                    else:
                        stated_text_source = text
                else:
                    scan_text = text
                    stated_text_source = text

                addresses = extract_contract_addresses(scan_text)
                if not addresses:
                    return


                # Format channel_id for Bot API compatibility (-100xxxxxxxxx)
                raw_id = chat.id
                str_id = str(raw_id)
                if str_id.startswith("-100") or str_id.startswith("-"):
                    channel_id = raw_id
                elif isinstance(chat, Channel):
                    channel_id = int(f"-100{raw_id}")
                else:
                    channel_id = raw_id

                channel_title = getattr(chat, "title", None) or "Unknown Channel"
                channel_username = getattr(chat, "username", None) or ""

                if not await is_channel_authorized(channel_id=channel_id, username=channel_username):
                    logger.info(f"🚫 [USERBOT ENGINE 1] Skipping call from unauthorized channel '{channel_title}' (ID: {channel_id})")
                    return

                logger.info(f"📥 [USERBOT ENGINE 1] Incoming MTProto message from channel '{channel_title}' (@{channel_username} | ID: {channel_id}) - Found {len(addresses)} Contract Address(es)")

                seen_tokens_in_msg = set()
                for addr in addresses:
                    clean_addr = addr.strip().lower()
                    if clean_addr in seen_tokens_in_msg:
                        continue

                    logger.info(f"🔍 [USERBOT ENGINE 1] Processing CA: {addr} from '{channel_title}'...")
                    token_info = await fetch_token_data(addr, is_new_call=True)
                    msg_link = f"https://t.me/c/{abs(raw_id)}/{event.id}" if not channel_username else f"https://t.me/{channel_username}/{event.id}"

                    # Instant Pre-Index Check: If caller already provided stated MC in message/embed, dispatch immediately!
                    if not token_info or token_info.get("market_cap", 0) <= 0:
                        stated_mc = extract_stated_market_cap(stated_text_source)
                        if stated_mc and stated_mc > 0:
                            chain_cand, sym_cand, name_cand = extract_token_metadata_fallback(scan_text, addr)
                            if sym_cand == "UNKNOWN":
                                # Fast 1-second DEX indexing wait before falling back to UNKNOWN
                                await asyncio.sleep(1.0)
                                token_info = await fetch_token_data(addr, is_new_call=True)

                            if not token_info or token_info.get("market_cap", 0) <= 0:
                                if sym_cand not in INVALID_SYMBOLS and not name_cand.lower().startswith(("explorer", "pair", "http")):
                                    logger.info(f"⚡ [INSTANT PRE-INDEX DISPATCH] CA {addr} unindexed on DEX, but caller stated ${stated_mc:,.0f} MC! Alerting immediately...")
                                    token_info = {
                                        "token_address": addr,
                                        "chain": chain_cand,
                                        "symbol": sym_cand,
                                        "name": name_cand,
                                        "market_cap": stated_mc,
                                        "price_usd": 0.0,
                                        "pair_url": f"https://dexscreener.com/{chain_cand}/{addr}",
                                        "pair_address": ""
                                    }
                    if not token_info or token_info.get("market_cap", 0) <= 0:
                        logger.info(f"⏳ [USERBOT ENGINE 1] CA {addr} from '{channel_title}' is unindexed. Launching instant sub-second retry queue...")
                        asyncio.create_task(
                            self._retry_unindexed_ca(
                                addr=addr,
                                channel_id=channel_id,
                                channel_title=channel_title,
                                channel_username=channel_username,
                                event_id=event.id,
                                msg_link=msg_link,
                                text=stated_text_source
                            )
                        )
                        continue

                    base_token = (token_info.get("token_address") or "").lower().strip()
                    if base_token in seen_tokens_in_msg:
                        logger.debug(f"ℹ️ [USERBOT ENGINE 1] Silently skipping duplicate pair link for {token_info.get('symbol')} in same message.")
                        continue
                    seen_tokens_in_msg.add(clean_addr)
                    seen_tokens_in_msg.add(base_token)
                    
                    fetch_mc = token_info["market_cap"]
                    stated_mc = extract_stated_market_cap(stated_text_source, fetch_mc)
                    
                    # Primary Entry MC: Use caller stated MC if present in text, else live DEX MC
                    if stated_mc and stated_mc > 0:
                        entry_mc = stated_mc
                        logger.info(f"💡 [USERBOT ENGINE 1] Using caller-stated Entry Market Cap: ${entry_mc:,.0f} (Live DEX: ${fetch_mc:,.0f})")
                    else:
                        entry_mc = fetch_mc

                    token_info["market_cap"] = entry_mc
                    token_info["fetch_mc"] = fetch_mc

                    call_id, is_dip_call, prev_entry_mc = await record_call(
                        channel_id=channel_id,
                        channel_title=channel_title,
                        channel_username=channel_username,
                        token_address=token_info["token_address"],
                        chain=token_info["chain"],
                        token_symbol=token_info["symbol"],
                        token_name=token_info["name"],
                        entry_mc=entry_mc,
                        call_msg_id=event.id,
                        call_msg_link=msg_link
                    )

                    if not call_id:
                        logger.info(f"ℹ️ [USERBOT ENGINE 1] Silently skipping duplicate call for {token_info['symbol']} in '{channel_title}'")
                        continue
                    
                    if is_dip_call:
                        logger.info(f"📉 [USERBOT ENGINE 1 SUCCESS] Recorded Dip Re-Call #{call_id}: ${token_info['symbol']} at ${entry_mc:,.0f} MC (Dip from ${prev_entry_mc:,.0f}) by {channel_title} (ID: {channel_id})")
                    else:
                        logger.info(f"🎯 [USERBOT ENGINE 1 SUCCESS] Recorded New Call #{call_id}: ${token_info['symbol']} at ${entry_mc:,.0f} MC by {channel_title} (ID: {channel_id})")

                    # Also post instant call entry alert to main alert channel
                    try:
                        from main import send_new_call_alert
                        call_dict = {
                            "id": call_id,
                            "channel_id": channel_id,
                            "channel_title": channel_title,
                            "channel_username": channel_username,
                            "token_address": token_info["token_address"],
                            "chain": token_info["chain"],
                            "token_symbol": token_info["symbol"],
                            "token_name": token_info["name"],
                            "entry_mc": entry_mc,
                            "call_msg_link": msg_link,
                            "pair_address": token_info.get("pair_address", ""),
                            "pair_url": token_info.get("pair_url", "")
                        }
                        asyncio.create_task(send_new_call_alert(call_dict, bot_app=getattr(self, 'bot_app', None), telethon_client=self.client))
                    except Exception as alert_err:
                        logger.warning(f"Could not send new call alert to main channel: {alert_err}")

                        # Send instant text confirmation reply in the group/channel
                        confirmation_text = (
                            f"🎯 **Call Tracked by KOLInsights!**\n\n"
                            f"🪙 Token: **{token_info['name']}** (`${token_info['symbol']}`)\n"
                            f"📊 Entry Market Cap: **${token_info['market_cap']:,.0f}**\n"
                            f"🌐 Chain: **{token_info['chain'].upper()}**\n"
                            f"📢 Caller: **{channel_title}**"
                        )
                        try:
                            await event.reply(confirmation_text, parse_mode="md")
                        except Exception as reply_err:
                            logger.debug(f"Could not send call confirmation reply in {channel_title}: {reply_err}")

            except Exception as e:
                logger.error(f"Error processing channel message: {e}", exc_info=True)

    async def resolve_unresolved_channels(self):
        """
        Universal Auto-Sync & Auto-Join Engine:
        - Resolves real Telegram channel IDs and official titles for all tracked channels.
        - Automatically joins any unjoined public channels so userbot receives live message events.
        - Synchronizes SQLite channels, calls, and channel_owners tables seamlessly.
        """
        if not self.client or not await self.client.is_user_authorized():
            return
        try:
            from database import get_all_channels, sync_channel_identity
            from telethon.tl.functions.channels import JoinChannelRequest
            
            all_ch = await get_all_channels()
            for ch in all_ch:
                user_name = ch.get("username") or ch.get("title") or ""
                user_name = str(user_name).lstrip("@").strip()
                if not user_name or " " in user_name:
                    continue
                try:
                    entity = await self.client.get_entity(user_name)
                    if entity:
                        raw_id = getattr(entity, "id", None)
                        if raw_id:
                            str_id = str(raw_id)
                            formatted_id = int(f"-100{str_id}") if not str_id.startswith("-") else int(str_id)
                            real_title = getattr(entity, "title", None) or getattr(entity, "first_name", None) or ch.get("title") or user_name
                            
                            # Auto-join channel if not already a member
                            try:
                                await self.client(JoinChannelRequest(entity))
                            except Exception:
                                pass

                            current_id = ch.get("channel_id")
                            current_title = ch.get("title")
                            if current_id != formatted_id or current_title != real_title:
                                await sync_channel_identity(current_id, user_name, formatted_id, real_title)
                                logger.info(f"✨ [ID AUTO-SYNC & AUTO-JOIN] Synced @{user_name} ➔ Real Telegram ID: {formatted_id} | Title: '{real_title}'")
                except Exception as res_err:
                    logger.debug(f"Could not auto-sync Telegram ID for @{user_name}: {res_err}")
        except Exception as err:
            logger.debug(f"Auto-sync sweep notice: {err}")

    async def _auto_resolver_loop(self):
        """Background loop running every 60 seconds to auto-sync & auto-join all tracked channels."""
        while self.client and self.client.is_connected():
            await self.resolve_unresolved_channels()
            await asyncio.sleep(60)

    async def _retry_unindexed_ca(self, addr: str, channel_id: int, channel_title: str, channel_username: str, event_id: int, msg_link: str, text: str):
        """
        Retries unindexed / pre-launch CAs with rapid sub-second & progressive intervals.
        Catches fresh pairs instantly as soon as liquidity is created!
        """
        RETRY_INTERVALS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0]
        logger.info(f"⚡ [USERBOT RETRY QUEUE] CA {addr} from '{channel_title}' is unindexed. Starting rapid sub-second retry queue ({len(RETRY_INTERVALS)} attempts)...")
        
        for attempt, delay in enumerate(RETRY_INTERVALS, 1):
            await asyncio.sleep(delay)
            token_info = await fetch_token_data(addr, is_new_call=True)
            if token_info and token_info.get("market_cap", 0) > 0:
                logger.info(f"⚡ [USERBOT RETRY SUCCESS] CA {addr} indexed on attempt #{attempt} (after {delay}s delay)! Recording call...")
                fetch_mc = token_info["market_cap"]
                stated_mc = extract_stated_market_cap(text, fetch_mc)
                entry_mc = stated_mc if (stated_mc and stated_mc > 0) else fetch_mc
                token_info["market_cap"] = entry_mc
                token_info["fetch_mc"] = fetch_mc

                call_id, is_dip_call, prev_entry_mc = await record_call(
                    channel_id=channel_id,
                    channel_title=channel_title,
                    channel_username=channel_username,
                    token_address=token_info["token_address"],
                    chain=token_info["chain"],
                    token_symbol=token_info["symbol"],
                    token_name=token_info["name"],
                    entry_mc=entry_mc,
                    call_msg_id=event_id,
                    call_msg_link=msg_link
                )

                if not call_id:
                    logger.info(f"ℹ️ [USERBOT RETRY QUEUE] Call for {token_info['symbol']} in '{channel_title}' was already recorded.")
                    return

                try:
                    from main import send_new_call_alert
                    call_dict = {
                        "id": call_id,
                        "channel_id": channel_id,
                        "channel_title": channel_title,
                        "channel_username": channel_username,
                        "token_address": token_info["token_address"],
                        "chain": token_info["chain"],
                        "token_symbol": token_info["symbol"],
                        "token_name": token_info["name"],
                        "entry_mc": entry_mc,
                        "call_msg_link": msg_link,
                        "pair_address": token_info.get("pair_address", ""),
                        "pair_url": token_info.get("pair_url", "")
                    }
                    asyncio.create_task(send_new_call_alert(call_dict, bot_app=getattr(self, 'bot_app', None), telethon_client=self.client))
                except Exception as alert_err:
                    logger.warning(f"Could not send new call alert from retry queue: {alert_err}")
                return

        logger.info(f"⌛ [USERBOT RETRY QUEUE] CA {addr} from '{channel_title}' remained unindexed after all retry attempts.")

    async def stop(self):
        if self.client:
            await self.client.disconnect()
            logger.info("CallScraper client stopped.")
