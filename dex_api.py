import time
import asyncio
import httpx
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

DEXSCREENER_API_URL = "https://api.dexscreener.com/latest/dex/tokens/{token_address}"
GECKOTERMINAL_API_URL = "https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{address}"

NETWORK_MAP = {
    "solana": "solana",
    "sol": "solana",
    "eth": "eth",
    "ethereum": "eth",
    "bsc": "bsc",
    "binance": "bsc",
    "bnb": "bsc",
    "base": "base",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "polygon": "polygon_pos",
    "matic": "polygon_pos"
}

STANDARD_QUOTE_SYMBOLS = {
    "SOL", "WSOL", "USDC", "USDT", "WETH", "ETH", "WBNB", "BNB", "BTC", "WBTC",
    "USDG", "USDUG", "AMZN", "DAI", "BUSD", "TRX", "WTRX"
}

_shared_client: Optional[httpx.AsyncClient] = None
_last_request_time: float = 0.0
_request_lock = asyncio.Lock()
_ip_cooldown_until: float = 0.0

def get_http_client() -> httpx.AsyncClient:
    """Returns a persistent, high-performance connection-pooled HTTP client."""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        limits = httpx.Limits(max_keepalive_connections=50, max_connections=100, keepalive_expiry=30)
        _shared_client = httpx.AsyncClient(timeout=12.0, headers=headers, limits=limits)
    return _shared_client

async def _pace_request():
    """Lightweight non-blocking rate limiter that enforces minimum pacing without freezing concurrent coroutines."""
    global _last_request_time, _ip_cooldown_until
    now = time.time()

    if now < _ip_cooldown_until:
        wait_sec = _ip_cooldown_until - now
        logger.info(f"⏳ [DEX API] IP Cooldown active. Pausing DexScreener for {wait_sec:.1f}s for automatic reset...")
        await asyncio.sleep(min(wait_sec, 2.0))

    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < 0.1:
        await asyncio.sleep(0.1 - elapsed)
    _last_request_time = time.time()

def get_pair_score(p):
    mc_check = float(p.get("marketCap") or p.get("fdv") or 0)
    has_mc = 100.0 if mc_check > 0 else 0.01

    liq = float(p.get("liquidity", {}).get("usd", 0) or 0)
    vol_24h = float(p.get("volume", {}).get("h24", 0) or 0)
    vol_5m = float(p.get("volume", {}).get("m5", 0) or 0)
    txns_dict = p.get("txns", {}).get("h24", {}) or {}
    txns_count = int(txns_dict.get("buys", 0) or 0) + int(txns_dict.get("sells", 0) or 0)

    # Heavily downrank dead dust pools with negligible liquidity (< $50) and negligible volume (< $50)
    if liq < 50 and vol_24h < 50:
        return 0.001 * has_mc

    if liq > 50_000 and (vol_24h < 1_000 or txns_count < 10):
        eff_liq = min(liq, 100)
    else:
        eff_liq = min(liq, 500_000)

    return ((vol_24h * 1.0) + (vol_5m * 10.0) + (txns_count * 100.0) + (eff_liq * 0.1)) * has_mc

_gecko_cooldown_until: float = 0.0
_last_gecko_request_time: float = 0.0
_gecko_lock = asyncio.Lock()
_gecko_miss_cache: Dict[str, float] = {}  # addr.lower() -> last checked timestamp

async def _pace_gecko_request() -> bool:
    """Enforces strict non-burst spacing (>= 1.5s) for GeckoTerminal free API tier."""
    global _last_gecko_request_time, _gecko_cooldown_until
    now = time.time()
    if now < _gecko_cooldown_until:
        return False

    async with _gecko_lock:
        now = time.time()
        if now < _gecko_cooldown_until:
            return False
        elapsed = now - _last_gecko_request_time
        if elapsed < 1.5:
            await asyncio.sleep(1.5 - elapsed)
        _last_gecko_request_time = time.time()
        return True

async def fetch_geckoterminal_token_data(token_address: str, chain: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Targeted clean fetcher reserved EXCLUSIVELY for Robinhood and unindexed EVM chains."""
    global _gecko_cooldown_until
    clean_addr = token_address.strip()
    addr_lower = clean_addr.lower()

    # 0. Strict Chain Filter: DexScreener natively handles Solana, Base, BSC, ETH, Arbitrum, etc.
    # Completely ban Solana and standard chains from touching GeckoTerminal!
    if chain:
        chain_lower = chain.lower().strip()
        if chain_lower in ["solana", "sol", "base", "bsc", "binance", "bnb", "eth", "ethereum", "arbitrum", "arb", "polygon", "matic", "avalanche", "avax"]:
            return None

    # Solana addresses are base58 (not starting with 0x) or ending in 'pump'
    if not clean_addr.startswith("0x") or clean_addr.lower().endswith("pump"):
        return None

    # 1. Check if on 429 Cooldown
    now = time.time()
    if now < _gecko_cooldown_until:
        return None

    # 2. Check Negative Miss Cache (TTL 120s) - prevents 1s loop from hammering Gecko for unlisted/dead tokens
    if addr_lower in _gecko_miss_cache:
        if now - _gecko_miss_cache[addr_lower] < 120.0:
            return None

    # 3. Pacing check
    can_proceed = await _pace_gecko_request()
    if not can_proceed:
        return None

    client = get_http_client()
    headers = {"Accept": "application/json"}
    target_net = chain.lower().strip() if chain and chain.lower().strip() not in ["unknown", ""] else "robinhood"

    try:
        # 1. Direct Network Token API (Guarantees 100% exact FDV, price, and symbol without pool inversion)
        token_url = f"https://api.geckoterminal.com/api/v2/networks/{target_net}/tokens/{clean_addr}"
        resp = await client.get(token_url, headers=headers)

        if resp.status_code == 429:
            _gecko_cooldown_until = time.time() + 60.0
            logger.warning("⏳ [GECKO API] Rate-Limited (HTTP 429). Pausing GeckoTerminal queries for 60s...")
            return None

        if resp.status_code == 200:
            t_data = resp.json().get("data", {})
            t_attr = t_data.get("attributes", {})
            fdv = float(t_attr.get("fdv_usd") or t_attr.get("market_cap_usd") or 0)
            price = float(t_attr.get("price_usd") or 0)
            symbol = t_attr.get("symbol") or "UNKNOWN"
            name = t_attr.get("name") or symbol
            liq = float(t_attr.get("total_reserve_in_usd") or 0)

            # Liquidity Sanity Check: If FDV is > $500K on tiny liquidity (< $20K), cap FDV at 100x liquidity
            if liq >= 100 and fdv > (liq * 100) and fdv > 500_000:
                fdv = min(fdv, liq * 100)

            if fdv >= 200:
                _gecko_miss_cache.pop(addr_lower, None)
                logger.info(f"🎉 [GECKO DIRECT MATCH] ✅ Found ${symbol} on network: {target_net.upper()} | MC: ${fdv:,.0f} | Liq: ${liq:,.0f}")
                return {
                    "token_address": clean_addr,
                    "chain": target_net,
                    "symbol": symbol,
                    "name": name,
                    "market_cap": fdv,
                    "price_usd": price,
                    "pair_url": f"https://www.geckoterminal.com/{target_net}/tokens/{clean_addr}",
                    "pair_address": ""
                }

        # 2. Universal Search API Fallback (Strictly verifies target is the Base Token)
        search_url = f"https://api.geckoterminal.com/api/v2/search/pools?query={clean_addr}"
        s_resp = await client.get(search_url, headers=headers)

        if s_resp.status_code == 429:
            _gecko_cooldown_until = time.time() + 60.0
            logger.warning("⏳ [GECKO API] Rate-Limited (HTTP 429). Pausing GeckoTerminal queries for 60s...")
            return None

        if s_resp.status_code == 200:
            data = s_resp.json().get("data", [])
            for chosen_pool in data:
                pool_id = chosen_pool.get("id", "")
                chain_slug = pool_id.split("_")[0] if "_" in pool_id else "unknown"
                attr = chosen_pool.get("attributes", {})

                base_token_id = chosen_pool.get("relationships", {}).get("base_token", {}).get("data", {}).get("id", "")
                base_addr = base_token_id.split("_", 1)[1] if "_" in base_token_id else (attr.get("address") or clean_addr)

                # STRICT CHECK: Only accept pool if clean_addr matches the BASE token (avoids quote-token mixup)
                if base_addr.lower() != addr_lower:
                    continue

                liq = float(attr.get("reserve_in_usd") or 0)
                fdv = float(attr.get("fdv_usd") or attr.get("market_cap_usd") or 0)
                price = float(attr.get("base_token_price_usd") or 0)
                raw_name = attr.get("name", "Unknown Token")
                symbol = raw_name.split(" / ")[0] if " / " in raw_name else raw_name
                name = symbol

                if liq >= 100 and fdv > (liq * 100) and fdv > 500_000:
                    fdv = min(fdv, liq * 100)

                if fdv >= 200:
                    _gecko_miss_cache.pop(addr_lower, None)
                    logger.info(f"🎉 [GECKO POOL MATCH] ✅ Found ${symbol} on network: {chain_slug.upper()} | MC: ${fdv:,.0f} | Liq: ${liq:,.0f}")
                    return {
                        "token_address": clean_addr,
                        "chain": chain_slug,
                        "symbol": symbol,
                        "name": name,
                        "market_cap": fdv,
                        "price_usd": price,
                        "pair_url": f"https://www.geckoterminal.com/{chain_slug}/pools/{attr.get('address')}",
                        "pair_address": attr.get('address', '')
                    }

        # If not found or empty, cache miss for 120s
        _gecko_miss_cache[addr_lower] = time.time()

    except Exception as e:
        logger.debug(f"ℹ️ [GECKO API] Search notice for {clean_addr}: {e}")

    return None

async def fetch_tokens_batch(token_addresses: list) -> Dict[str, Dict[str, Any]]:
    """
    Fetches real-time market data for up to 30 tokens in 1 single HTTP request.
    Enforces request pacing and falls back to DexScreener Search endpoint if needed.
    """
    global _ip_cooldown_until
    if not token_addresses:
        return {}

    clean_addrs = [str(a).strip() for a in token_addresses if a and str(a).strip()]
    if not clean_addrs:
        return {}

    client = get_http_client()
    joined_addrs = ",".join(clean_addrs[:30])
    url = DEXSCREENER_API_URL.format(token_address=joined_addrs)

    try:
        await _pace_request()
        response = await client.get(url)

        if response.status_code == 429:
            _ip_cooldown_until = time.time() + 60.0
            logger.info("ℹ️ [DEX API] IP Rate-Limited by DexScreener (HTTP 429). Cooling down 60s for automatic reset...")
            return {}

        if response.status_code != 200:
            logger.debug(f"⚠️ [DEX API] HTTP {response.status_code} status for batch query.")
            return {}

        data = response.json()
        pairs = data.get("pairs") or []
        if not pairs:
            fallback_results: Dict[str, Dict[str, Any]] = {}
            for addr in clean_addrs:
                if addr.lower() in _gecko_miss_cache and (time.time() - _gecko_miss_cache[addr.lower()] < 120.0):
                    continue
                single_res = await fetch_geckoterminal_token_data(addr)
                if single_res and single_res.get("token_address"):
                    fallback_results[single_res["token_address"].lower()] = single_res
                    fallback_results[addr.lower()] = single_res
            return fallback_results

        by_token: Dict[str, list] = {}
        for p in pairs:
            addr = (p.get("baseToken", {}).get("address") or "").lower().strip()
            if addr:
                by_token.setdefault(addr, []).append(p)

        results: Dict[str, Dict[str, Any]] = {}
        for addr_key, token_pairs in by_token.items():
            valid_pairs = [p for p in token_pairs if float(p.get("liquidity", {}).get("usd", 0) or 0) >= 100]
            if not valid_pairs:
                # All pairs have liquidity < $100 -> Token has been rugged or has dead dust liquidity
                continue

            std_pairs = [
                p for p in valid_pairs
                if (p.get("quoteToken") or {}).get("symbol", "").upper() in STANDARD_QUOTE_SYMBOLS
            ]
            if not std_pairs:
                std_pairs = valid_pairs

            primary_pair = sorted(std_pairs, key=get_pair_score, reverse=True)[0]
            mc_val = float(primary_pair.get("marketCap") or 0)
            fdv_val = float(primary_pair.get("fdv") or 0)
            price_usd = float(primary_pair.get("priceUsd") or 0)
            liq = float(primary_pair.get("liquidity", {}).get("usd", 0) or 0)

            raw_mc = mc_val if mc_val > 0 else fdv_val

            # Sanity anomaly filter: if raw_mc > $2M on low liquidity (< $30K), cap MC at 100x liquidity
            if liq >= 100 and raw_mc > (liq * 100) and raw_mc > 2_000_000:
                market_cap = min(raw_mc, liq * 100)
            else:
                market_cap = raw_mc

            if market_cap < 500:
                continue

            base_addr = primary_pair.get("baseToken", {}).get("address") or addr_key
            symbol = primary_pair.get("baseToken", {}).get("symbol", "UNKNOWN")
            chain = primary_pair.get("chainId", "unknown")

            token_obj = {
                "token_address": base_addr,
                "chain": chain,
                "symbol": symbol,
                "name": primary_pair.get("baseToken", {}).get("name", symbol),
                "market_cap": market_cap,
                "price_usd": price_usd,
                "price_change": primary_pair.get("priceChange", {}),
                "pair_url": primary_pair.get("url", ""),
                "pair_address": primary_pair.get("pairAddress", "")
            }
            results[addr_key.lower()] = token_obj
            if base_addr:
                results[base_addr.lower()] = token_obj

        # Seamless Fallback for any 0x EVM token (e.g. Robinhood DEX pools) that DexScreener missed
        missing_addrs = [
            a for a in clean_addrs 
            if a.startswith("0x") and not a.lower().endswith("pump")
            and a.lower() not in results 
            and (a.lower() not in _gecko_miss_cache or (time.time() - _gecko_miss_cache[a.lower()] >= 120.0))
        ]
        if missing_addrs and time.time() >= _gecko_cooldown_until:
            for addr in missing_addrs[:1]:  # Check at most 1 Robinhood/EVM token per batch to strictly protect rate limit
                single_res = await fetch_geckoterminal_token_data(addr)
                if single_res and single_res.get("token_address"):
                    results[single_res["token_address"].lower()] = single_res
                    results[addr.lower()] = single_res

        logger.info(f"⚡ [DEX BATCH API] Successfully fetched batch of {len(results)}/{len(clean_addrs)} tokens!")
        return results

    except Exception as e:
        logger.debug(f"ℹ️ [DEX BATCH API] Batch query notice: {e}")
        return {}

async def _fetch_dexscreener_single(clean_addr: str, chain: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fast, lightweight single DexScreener token fetcher (1 clean request, zero spam)."""
    url = DEXSCREENER_API_URL.format(token_address=clean_addr)
    client = get_http_client()
    try:
        await _pace_request()
        response = await client.get(url)
        pairs = []
        if response.status_code == 429:
            _ip_cooldown_until = time.time() + 60.0
            logger.info("⏳ [DEX API] Rate-Limited (HTTP 429). Pausing DexScreener for 60s...")
            return None

        if response.status_code == 200:
            data = response.json()
            pairs = data.get("pairs") or []

        # Targeted single-chain pair fallback (only if chain is explicitly known, e.g. robinhood)
        if not pairs and chain:
            try:
                await _pace_request()
                pair_url = f"https://api.dexscreener.com/latest/dex/pairs/{chain.lower().strip()}/{clean_addr}"
                pair_resp = await client.get(pair_url)
                if pair_resp.status_code == 200:
                    pairs = pair_resp.json().get("pairs") or []
            except Exception:
                pass

        if not pairs:
            return None

        valid_pairs = [p for p in pairs if float(p.get("liquidity", {}).get("usd", 0) or 0) >= 100]
        if not valid_pairs:
            valid_pairs = [p for p in pairs if float(p.get("volume", {}).get("h24", 0) or 0) >= 100]
            if not valid_pairs:
                return None

        std_pairs = [
            p for p in valid_pairs
            if (p.get("quoteToken") or {}).get("symbol", "").upper() in STANDARD_QUOTE_SYMBOLS
        ]
        if not std_pairs:
            std_pairs = valid_pairs

        primary_pair = sorted(std_pairs, key=get_pair_score, reverse=True)[0]
        mc_val = float(primary_pair.get("marketCap") or 0)
        fdv_val = float(primary_pair.get("fdv") or 0)
        price_usd = float(primary_pair.get("priceUsd") or 0)
        liq = float(primary_pair.get("liquidity", {}).get("usd", 0) or 0)
        raw_mc = mc_val if mc_val > 0 else fdv_val

        if liq < 100 or raw_mc < 500:
            return None

        if liq >= 100 and raw_mc > (liq * 100) and raw_mc > 2_000_000:
            market_cap = min(raw_mc, liq * 100)
        else:
            market_cap = raw_mc

        base_addr = primary_pair.get("baseToken", {}).get("address") or clean_addr
        symbol = primary_pair.get("baseToken", {}).get("symbol", "UNKNOWN")
        chain = primary_pair.get("chainId", "unknown")

        return {
            "token_address": base_addr,
            "chain": chain,
            "symbol": symbol,
            "name": primary_pair.get("baseToken", {}).get("name", "Unknown Token"),
            "market_cap": market_cap,
            "price_usd": price_usd,
            "pair_url": primary_pair.get("url", ""),
            "pair_address": primary_pair.get("pairAddress", "")
        }
    except Exception:
        return None

async def fetch_token_data(token_address: str, chain: Optional[str] = None, is_new_call: bool = False) -> Optional[Dict[str, Any]]:
    """
    Fetches real-time token details using DexScreener (with GeckoTerminal fallback exclusively for Robinhood/EVM).
    """
    global _ip_cooldown_until
    clean_addr = token_address.strip()

    if is_new_call:
        # Solana and standard chains are 100% DexScreener-powered (never touch Gecko)
        if not clean_addr.startswith("0x") or (chain and chain.lower() in ["solana", "sol", "base", "bsc", "binance", "eth", "ethereum", "arbitrum", "polygon", "avalanche"]):
            return await _fetch_dexscreener_single(clean_addr, chain=chain)

        # High-Speed Parallel Race only for Robinhood / unindexed EVM
        dex_task = asyncio.create_task(_fetch_dexscreener_single(clean_addr, chain=chain))
        gecko_task = asyncio.create_task(fetch_geckoterminal_token_data(clean_addr, chain))

        done, pending = await asyncio.wait([dex_task, gecko_task], return_when=asyncio.FIRST_COMPLETED)
        
        for t in done:
            res = t.result()
            if res and res.get("market_cap", 0) > 0:
                for p in pending:
                    p.cancel()
                logger.info(f"⚡ [FAST RACE WINNER] ✅ Fetched ${res.get('symbol')} on {res.get('chain', '').upper()} in 1st round!")
                return res

        if pending:
            rem_done, _ = await asyncio.wait(pending, timeout=3.0)
            for t in rem_done:
                res = t.result()
                if res and res.get("market_cap", 0) > 0:
                    return res

        return None

    # Normal single-token fetch: Try DexScreener first; fallback to Gecko only for Robinhood/0x EVM
    res = await _fetch_dexscreener_single(clean_addr, chain=chain)
    if res and res.get("market_cap", 0) > 0:
        return res

    if clean_addr.startswith("0x") and not clean_addr.lower().endswith("pump"):
        return await fetch_geckoterminal_token_data(clean_addr, chain=chain)

    return None
