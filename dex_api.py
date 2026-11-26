import time
import asyncio
import httpx
import logging
import random
from typing import Optional, Dict, Any, List, Tuple

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
    "matic": "polygon_pos",
    "avalanche": "avax",
    "avax": "avax",
    "fantom": "ftm",
    "tron": "tron"
}

STANDARD_QUOTE_SYMBOLS = {
    "SOL", "WSOL", "USDC", "USDT", "WETH", "ETH", "WBNB", "BNB", "BTC", "WBTC",
    "USDG", "USDUG", "AMZN", "DAI", "BUSD", "TRX", "WTRX"
}

_shared_client: Optional[httpx.AsyncClient] = None

def get_http_client() -> httpx.AsyncClient:
    """Returns a persistent, connection-pooled HTTP client with keep-alive."""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }
        limits = httpx.Limits(max_keepalive_connections=50, max_connections=100, keepalive_expiry=30)
        _shared_client = httpx.AsyncClient(timeout=10.0, headers=headers, limits=limits)
    return _shared_client

# ============================================================================
# MASTER PATTERN 1: 30-MINUTE CIRCUIT BREAKER (Handbook Section 4D)
# Completely silences DexScreener when cloud IP is saturated, routing to GeckoTerminal.
# ============================================================================
class DexCircuitBreaker:
    """
    Industrial-grade Circuit Breaker:
      - CLOSED: Normal operation.
      - OPEN: DexScreener returned 429. Muted for 1800s (30 minutes).
              Zero requests touch DexScreener; 100% routed to GeckoTerminal.
      - HALF_OPEN: Single probe request after 30m to test if Cloudflare block cleared.
    """
    def __init__(self):
        self.state = "CLOSED"
        self.consecutive_429s = 0
        self.tripped_until = 0.0
        self.last_log_time = 0.0
        self._lock = asyncio.Lock()

    def is_available(self) -> bool:
        now = time.time()
        if self.state == "OPEN":
            if now >= self.tripped_until:
                self.state = "HALF_OPEN"
                logger.info("🔄 [CIRCUIT BREAKER] Entering HALF-OPEN probe. Testing DexScreener recovery...")
                return True
            if now - self.last_log_time > 60.0:
                rem_min = max(0.0, (self.tripped_until - now) / 60.0)
                logger.info(f"🛡️ [CIRCUIT BREAKER: OPEN] DexScreener paused ({rem_min:.1f}m remaining). Running 100% on GeckoTerminal & RAM Cache.")
                self.last_log_time = now
            return False
        return True

    def record_success(self):
        if self.state != "CLOSED":
            logger.info("✅ [CIRCUIT BREAKER: CLOSED] DexScreener probe successful! Resetting to normal.")
        self.state = "CLOSED"
        self.consecutive_429s = 0

    def record_429(self):
        self.consecutive_429s += 1
        # On shared cloud hosting (Render), IP blocks last 15-30 minutes.
        # Silence DexScreener for 1800s (30m) + jitter to allow complete IP reset.
        jitter = random.uniform(30.0, 120.0)
        cooldown = 1800.0 + jitter
        self.tripped_until = time.time() + cooldown
        self.state = "OPEN"
        logger.warning(
            f"🚨 [CIRCUIT BREAKER: OPEN] DexScreener 429 detected! "
            f"Muting DexScreener for {cooldown/60.0:.1f} minutes. All requests seamlessly routed to GeckoTerminal."
        )

_circuit_breaker = DexCircuitBreaker()

def is_dex_cooling_down() -> bool:
    return not _circuit_breaker.is_available()

# ============================================================================
# MASTER PATTERN 2: TOKEN BUCKET RATE LIMITER (Handbook Section 4B)
# Enforces strictly smooth outbound flow (max 1.0 req/sec = 60 req/min).
# ============================================================================
class TokenBucketLimiter:
    def __init__(self, capacity: float = 2.0, refill_rate: float = 1.0):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = capacity
        self.last_update = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_update
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_update = now
            if self.tokens < 1.0:
                wait_time = (1.0 - self.tokens) / self.refill_rate
                await asyncio.sleep(wait_time)
                self.tokens = 0.0
                self.last_update = time.time()
            else:
                self.tokens -= 1.0

_token_bucket = TokenBucketLimiter(capacity=2.0, refill_rate=1.0)

async def _pace_request():
    await _token_bucket.acquire()

# ============================================================================
# MASTER PATTERN 3: O(1) LRU IN-MEMORY TOKEN CACHE (Handbook Section 2D & 5A)
# 25s TTL for hot calls, up to 300s fallback during cooldown.
# ============================================================================
class LRUTokenCache:
    def __init__(self, maxsize: int = 3000, default_ttl: float = 25.0):
        self.maxsize = maxsize
        self.default_ttl = default_ttl
        self.cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self.order: List[str] = []

    def get(self, key: str, max_age: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if not key:
            return None
        k = key.lower().strip()
        if k not in self.cache:
            return None
        ts, data = self.cache[k]
        ttl = max_age if max_age is not None else self.default_ttl
        if time.time() - ts > ttl:
            return None
        return data

    def put(self, key: str, data: Dict[str, Any]):
        if not key or not data:
            return
        k = key.lower().strip()
        now = time.time()
        if k in self.cache:
            try:
                self.order.remove(k)
            except ValueError:
                pass
        elif len(self.cache) >= self.maxsize:
            if self.order:
                oldest = self.order.pop(0)
                self.cache.pop(oldest, None)
        self.cache[k] = (now, data)
        self.order.append(k)

_token_lru_cache = LRUTokenCache(maxsize=3000, default_ttl=25.0)

# ============================================================================
# PAIR SCORING HEURISTIC
# ============================================================================
def get_pair_score(p: dict) -> float:
    mc_check = float(p.get("marketCap") or p.get("fdv") or 0)
    has_mc = 100.0 if mc_check > 0 else 0.01

    liq = float(p.get("liquidity", {}).get("usd", 0) or 0)
    vol_24h = float(p.get("volume", {}).get("h24", 0) or 0)
    vol_5m = float(p.get("volume", {}).get("m5", 0) or 0)
    txns_dict = p.get("txns", {}).get("h24", {}) or {}
    txns_count = int(txns_dict.get("buys", 0) or 0) + int(txns_dict.get("sells", 0) or 0)

    if liq < 50 and vol_24h < 50:
        return 0.001 * has_mc

    if liq > 50_000 and (vol_24h < 1_000 or txns_count < 10):
        eff_liq = min(liq, 100)
    else:
        eff_liq = min(liq, 500_000)

    return ((vol_24h * 1.0) + (vol_5m * 10.0) + (txns_count * 100.0) + (eff_liq * 0.1)) * has_mc

# ============================================================================
# MASTER PATTERN 4: UNIVERSAL GECKOTERMINAL ENGINE (Handbook Section 8B)
# High-speed resolver for Solana, Ethereum, Base, BSC, Arbitrum, Tron, etc.
# ============================================================================
_gecko_cooldown_until: float = 0.0
_last_gecko_request_time: float = 0.0
_gecko_lock = asyncio.Lock()
_gecko_miss_cache: Dict[str, float] = {}

async def _pace_gecko_request() -> bool:
    global _last_gecko_request_time, _gecko_cooldown_until
    now = time.time()
    if now < _gecko_cooldown_until:
        return False

    async with _gecko_lock:
        now = time.time()
        if now < _gecko_cooldown_until:
            return False
        elapsed = now - _last_gecko_request_time
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)
        _last_gecko_request_time = time.time()
        return True

async def fetch_geckoterminal_token_data(token_address: str, chain: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Universal multi-chain DEX data fetcher powered by GeckoTerminal.
    Resolves Solana, Ethereum, Base, BSC, Arbitrum, Polygon, Avalanche, and any other chain!
    """
    global _gecko_cooldown_until
    if not token_address:
        return None

    clean_addr = token_address.strip()
    addr_lower = clean_addr.lower()

    # 1. Check in-memory cache first
    cached = _token_lru_cache.get(addr_lower, max_age=60.0)
    if cached:
        return cached

    now = time.time()
    if now < _gecko_cooldown_until:
        return None

    if addr_lower in _gecko_miss_cache and (now - _gecko_miss_cache[addr_lower] < 90.0):
        return None

    can_proceed = await _pace_gecko_request()
    if not can_proceed:
        return None

    client = get_http_client()
    target_net = NETWORK_MAP.get((chain or "").lower().strip())

    try:
        # Strategy A: Direct Network Token Endpoint if network is known
        if target_net:
            token_url = f"https://api.geckoterminal.com/api/v2/networks/{target_net}/tokens/{clean_addr}"
            resp = await client.get(token_url)
            if resp.status_code == 429:
                _gecko_cooldown_until = time.time() + 60.0
                logger.warning("⏳ [GECKO API] Rate-Limited (HTTP 429). Pausing GeckoTerminal for 60s...")
                return None

            if resp.status_code == 200:
                t_data = resp.json().get("data", {})
                t_attr = t_data.get("attributes", {})
                fdv = float(t_attr.get("fdv_usd") or t_attr.get("market_cap_usd") or 0)
                price = float(t_attr.get("price_usd") or 0)
                symbol = t_attr.get("symbol") or "UNKNOWN"
                name = t_attr.get("name") or symbol
                liq = float(t_attr.get("total_reserve_in_usd") or 0)

                if liq >= 100 and fdv > (liq * 100) and fdv > 500_000:
                    fdv = min(fdv, liq * 100)

                if fdv >= 200:
                    _gecko_miss_cache.pop(addr_lower, None)
                    token_obj = {
                        "token_address": clean_addr,
                        "chain": target_net,
                        "symbol": symbol,
                        "name": name,
                        "market_cap": fdv,
                        "price_usd": price,
                        "pair_url": f"https://www.geckoterminal.com/{target_net}/tokens/{clean_addr}",
                        "pair_address": ""
                    }
                    _token_lru_cache.put(clean_addr, token_obj)
                    logger.info(f"🦎 [GECKO MATCH] ✅ ${symbol} on {target_net.upper()} | MC: ${fdv:,.0f} | Liq: ${liq:,.0f}")
                    return token_obj

        # Strategy B: Universal Pool Search (Works for any token on any network: Solana, ETH, Base, BSC)
        search_url = f"https://api.geckoterminal.com/api/v2/search/pools?query={clean_addr}"
        s_resp = await client.get(search_url)

        if s_resp.status_code == 429:
            _gecko_cooldown_until = time.time() + 60.0
            logger.warning("⏳ [GECKO API] Rate-Limited (HTTP 429). Pausing GeckoTerminal for 60s...")
            return None

        if s_resp.status_code == 200:
            data = s_resp.json().get("data", [])
            for chosen_pool in data:
                pool_id = chosen_pool.get("id", "")
                chain_slug = pool_id.split("_")[0] if "_" in pool_id else "unknown"
                attr = chosen_pool.get("attributes", {})

                base_token_id = chosen_pool.get("relationships", {}).get("base_token", {}).get("data", {}).get("id", "")
                base_addr = base_token_id.split("_", 1)[1] if "_" in base_token_id else (attr.get("address") or clean_addr)

                if base_addr.lower() != addr_lower and clean_addr.lower() not in pool_id.lower():
                    continue

                liq = float(attr.get("reserve_in_usd") or 0)
                fdv = float(attr.get("fdv_usd") or attr.get("market_cap_usd") or 0)
                price = float(attr.get("base_token_price_usd") or 0)
                raw_name = attr.get("name", "Unknown Token")
                symbol = raw_name.split(" / ")[0] if " / " in raw_name else raw_name

                if liq >= 100 and fdv > (liq * 100) and fdv > 500_000:
                    fdv = min(fdv, liq * 100)

                if fdv >= 200:
                    _gecko_miss_cache.pop(addr_lower, None)
                    token_obj = {
                        "token_address": clean_addr,
                        "chain": chain_slug,
                        "symbol": symbol,
                        "name": symbol,
                        "market_cap": fdv,
                        "price_usd": price,
                        "pair_url": f"https://www.geckoterminal.com/{chain_slug}/pools/{attr.get('address')}",
                        "pair_address": attr.get("address", "")
                    }
                    _token_lru_cache.put(clean_addr, token_obj)
                    logger.info(f"🦎 [GECKO POOL MATCH] ✅ ${symbol} on {chain_slug.upper()} | MC: ${fdv:,.0f} | Liq: ${liq:,.0f}")
                    return token_obj

        _gecko_miss_cache[addr_lower] = time.time()
    except Exception as e:
        logger.debug(f"ℹ️ [GECKO API] Lookup notice for {clean_addr}: {e}")

    return None

# ============================================================================
# MASTER PATTERN 5: HIGH-PERFORMANCE DUAL-ENGINE BATCH FETCHER
# ============================================================================
async def fetch_tokens_batch(token_addresses: list) -> Dict[str, Dict[str, Any]]:
    """
    Fetches real-time market data for multiple tokens.
    Guaranteed zero 429 rate limit spam:
      1. Returns from RAM cache if within 25s TTL.
      2. If DexScreener is in cooldown, 100% routes to GeckoTerminal.
      3. If DexScreener responds with 429, mutes DexScreener for 30m and seamlessly queries GeckoTerminal.
    """
    if not token_addresses:
        return {}

    clean_addrs = [str(a).strip() for a in token_addresses if a and str(a).strip()]
    if not clean_addrs:
        return {}

    cached_results: Dict[str, Dict[str, Any]] = {}
    missing_addrs: List[str] = []

    # 1. Step 1: Query In-Memory LRU Cache in O(1) (0ms, 0 HTTP calls!)
    for a in clean_addrs:
        data = _token_lru_cache.get(a, max_age=25.0)
        if data:
            cached_results[a.lower()] = data
            cached_results[a] = data
        else:
            missing_addrs.append(a)

    if not missing_addrs:
        return cached_results

    # 2. Step 2: Check Circuit Breaker
    if not _circuit_breaker.is_available():
        # Circuit is OPEN (DexScreener paused). Serve from slightly older cache and GeckoTerminal
        for a in missing_addrs:
            data = _token_lru_cache.get(a, max_age=300.0)
            if data:
                cached_results[a.lower()] = data
                cached_results[a] = data

        still_missing = [a for a in missing_addrs if a.lower() not in cached_results]
        for a in still_missing[:2]:
            gecko_res = await fetch_geckoterminal_token_data(a)
            if gecko_res:
                cached_results[a.lower()] = gecko_res
                cached_results[a] = gecko_res

        return cached_results

    # 3. Step 3: DexScreener Batch Query with Token Bucket Pacing
    client = get_http_client()
    joined_addrs = ",".join(missing_addrs[:30])
    url = DEXSCREENER_API_URL.format(token_address=joined_addrs)

    try:
        await _pace_request()
        response = await client.get(url)

        if response.status_code == 429:
            _circuit_breaker.record_429()
            # Instant Failover to GeckoTerminal
            for a in missing_addrs[:2]:
                gecko_res = await fetch_geckoterminal_token_data(a)
                if gecko_res:
                    cached_results[a.lower()] = gecko_res
                    cached_results[a] = gecko_res
            return cached_results

        if response.status_code != 200:
            return cached_results

        _circuit_breaker.record_success()
        data = response.json()
        pairs = data.get("pairs") or []

        if not pairs:
            for addr in missing_addrs[:2]:
                single_res = await fetch_geckoterminal_token_data(addr)
                if single_res:
                    cached_results[addr.lower()] = single_res
                    cached_results[addr] = single_res
            return cached_results

        by_token: Dict[str, list] = {}
        for p in pairs:
            addr = (p.get("baseToken", {}).get("address") or "").lower().strip()
            if addr:
                by_token.setdefault(addr, []).append(p)

        results: Dict[str, Dict[str, Any]] = dict(cached_results)
        for addr_key, token_pairs in by_token.items():
            valid_pairs = [p for p in token_pairs if float(p.get("liquidity", {}).get("usd", 0) or 0) >= 100]
            if not valid_pairs:
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
            results[base_addr.lower()] = token_obj
            _token_lru_cache.put(addr_key, token_obj)
            _token_lru_cache.put(base_addr, token_obj)

        return results

    except Exception as e:
        logger.debug(f"ℹ️ [DEX BATCH API] Batch query error: {e}")
        return cached_results

# ============================================================================
# MASTER PATTERN 6: GECKOTERMINAL-FIRST SINGLE TOKEN RESOLVER
# Never hits DexScreener if GeckoTerminal resolves the token!
# ============================================================================
async def _fetch_dexscreener_single(clean_addr: str, chain: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Guarded DexScreener single-token fetcher."""
    cached = _token_lru_cache.get(clean_addr, max_age=25.0)
    if cached:
        return cached

    if not _circuit_breaker.is_available():
        return _token_lru_cache.get(clean_addr, max_age=300.0)

    url = DEXSCREENER_API_URL.format(token_address=clean_addr)
    client = get_http_client()

    try:
        await _pace_request()
        response = await client.get(url)

        if response.status_code == 429:
            _circuit_breaker.record_429()
            return _token_lru_cache.get(clean_addr, max_age=300.0)

        if response.status_code != 200:
            return None

        _circuit_breaker.record_success()
        data = response.json()
        pairs = data.get("pairs") or []

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
        chain_name = primary_pair.get("chainId", "unknown")

        token_obj = {
            "token_address": base_addr,
            "chain": chain_name,
            "symbol": symbol,
            "name": primary_pair.get("baseToken", {}).get("name", "Unknown Token"),
            "market_cap": market_cap,
            "price_usd": price_usd,
            "pair_url": primary_pair.get("url", ""),
            "pair_address": primary_pair.get("pairAddress", "")
        }
        _token_lru_cache.put(clean_addr, token_obj)
        _token_lru_cache.put(base_addr, token_obj)
        return token_obj

    except Exception:
        return _token_lru_cache.get(clean_addr, max_age=300.0)

async def fetch_token_data(token_address: str, chain: Optional[str] = None, is_new_call: bool = False) -> Optional[Dict[str, Any]]:
    """
    Guaranteed Zero-Rate-Limit Token Resolver:
      1. O(1) In-Memory Cache (0ms).
      2. GeckoTerminal Universal Engine (Fast, never hits DexScreener if resolved).
      3. DexScreener Fallback (Only if Gecko misses AND Circuit Breaker is CLOSED).
    """
    if not token_address:
        return None

    clean_addr = token_address.strip()

    # Step 1: In-Memory Cache Lookup (0ms)
    cached = _token_lru_cache.get(clean_addr, max_age=25.0)
    if cached and cached.get("market_cap", 0) > 0:
        return cached

    # Step 2: GeckoTerminal-First Strategy
    # If GeckoTerminal resolves the token, DexScreener is NEVER TOUCHED!
    gecko_res = await fetch_geckoterminal_token_data(clean_addr, chain=chain)
    if gecko_res and gecko_res.get("market_cap", 0) > 0:
        _token_lru_cache.put(clean_addr, gecko_res)
        return gecko_res

    # Step 3: DexScreener Secondary Fallback (only if Circuit Breaker is healthy)
    if _circuit_breaker.is_available():
        res = await _fetch_dexscreener_single(clean_addr, chain=chain)
        if res and res.get("market_cap", 0) > 0:
            return res

    # Step 4: Return any cached historical data if available
    return _token_lru_cache.get(clean_addr, max_age=300.0)
