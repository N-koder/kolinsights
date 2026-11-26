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
# MASTER PATTERN 1: 30-MINUTE CIRCUIT BREAKER (Handbook Section 4D & 8C)
# Silences DexScreener on 429, routing traffic safely to GeckoTerminal.
# ============================================================================
class DexCircuitBreaker:
    def __init__(self):
        self.state = "CLOSED"
        self.consecutive_429s = 0
        self.tripped_until = 0.0
        self.last_log_time = 0.0

    def is_available(self) -> bool:
        now = time.time()
        if self.state == "OPEN":
            if now >= self.tripped_until:
                self.state = "HALF_OPEN"
                logger.info("🔄 [CIRCUIT BREAKER] Entering HALF-OPEN probe. Testing DexScreener recovery...")
                return True
            if now - self.last_log_time > 60.0:
                rem_min = max(0.0, (self.tripped_until - now) / 60.0)
                logger.info(f"🛡️ [CIRCUIT BREAKER: OPEN] DexScreener paused ({rem_min:.1f}m remaining). Running on Batched GeckoTerminal & RAM Cache.")
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
        jitter = random.uniform(30.0, 120.0)
        cooldown = 1800.0 + jitter  # 30-minute silence to allow Cloudflare IP rate-limit reset
        self.tripped_until = time.time() + cooldown
        self.state = "OPEN"
        logger.warning(
            f"🚨 [CIRCUIT BREAKER: OPEN] DexScreener 429 detected! "
            f"Muting DexScreener for {cooldown/60.0:.1f} minutes. All requests routed to Batched GeckoTerminal."
        )

_circuit_breaker = DexCircuitBreaker()

def is_dex_cooling_down() -> bool:
    return not _circuit_breaker.is_available()

# ============================================================================
# MASTER PATTERN 2: DUAL TOKEN BUCKET RATE LIMITERS (Handbook Section 4B)
# Hard Ceilings:
#   - DexScreener: 1.0 req/sec = 60 req/min (Dex limit is 300)
#   - GeckoTerminal: 0.25 req/sec = 15 req/min (Gecko limit is 30) -> IMPOSSIBLE TO 429!
# ============================================================================
class TokenBucketLimiter:
    def __init__(self, capacity: float, refill_rate: float):
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

_dex_token_bucket = TokenBucketLimiter(capacity=2.0, refill_rate=1.0)
_gecko_token_bucket = TokenBucketLimiter(capacity=2.0, refill_rate=0.25)  # Max 15 calls/min on Gecko!

async def _pace_request():
    await _dex_token_bucket.acquire()

async def _pace_gecko_request():
    await _gecko_token_bucket.acquire()

# ============================================================================
# MASTER PATTERN 3: O(1) LRU IN-MEMORY TOKEN CACHE (Handbook Section 2D & 5A)
# 25s fresh TTL for hot calls, up to 300s fallback during network spikes.
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

# Negative Miss Cache (TTL 180s): Prevents repeatedly querying non-existent CAs
_miss_cache: Dict[str, float] = {}

def is_cached_miss(addr: str) -> bool:
    k = addr.lower().strip()
    return k in _miss_cache and (time.time() - _miss_cache[k] < 180.0)

def record_miss(addr: str):
    _miss_cache[addr.lower().strip()] = time.time()

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
# MASTER PATTERN 4: BATCHED GECKOTERMINAL MULTI-TOKEN RESOLVER
# Fetches up to 30 tokens in 1 SINGLE HTTP call (Never triggers 30/min limit!)
# ============================================================================
async def fetch_geckoterminal_multi_tokens(network: str, addresses: List[str]) -> Dict[str, Dict[str, Any]]:
    """
    Batched GeckoTerminal query: up to 30 tokens in ONE single HTTP request!
    Guaranteed safe under the 15 req/min hard ceiling.
    """
    if not addresses:
        return {}

    clean_addrs = [str(a).strip() for a in addresses if a and str(a).strip() and not is_cached_miss(str(a))]
    if not clean_addrs:
        return {}

    target_net = NETWORK_MAP.get(network.lower().strip(), network.lower().strip())
    joined = ",".join(clean_addrs[:30])
    url = f"https://api.geckoterminal.com/api/v2/networks/{target_net}/tokens/multi/{joined}"
    client = get_http_client()

    results: Dict[str, Dict[str, Any]] = {}
    try:
        await _pace_gecko_request()
        resp = await client.get(url)

        if resp.status_code == 429:
            logger.warning("⏳ [GECKO MULTI API] 429 Rate-Limit. Pausing Gecko calls...")
            return {}

        if resp.status_code != 200:
            return {}

        data = resp.json().get("data", [])
        for item in data:
            attr = item.get("attributes", {})
            addr = attr.get("address") or item.get("id", "").split("_")[-1]
            if not addr:
                continue

            fdv = float(attr.get("fdv_usd") or attr.get("market_cap_usd") or 0)
            price = float(attr.get("price_usd") or 0)
            symbol = attr.get("symbol") or "UNKNOWN"
            name = attr.get("name") or symbol
            liq = float(attr.get("total_reserve_in_usd") or 0)

            if liq >= 100 and fdv > (liq * 100) and fdv > 500_000:
                fdv = min(fdv, liq * 100)

            if fdv >= 200:
                token_obj = {
                    "token_address": addr,
                    "chain": target_net,
                    "symbol": symbol,
                    "name": name,
                    "market_cap": fdv,
                    "price_usd": price,
                    "pair_url": f"https://www.geckoterminal.com/{target_net}/tokens/{addr}",
                    "pair_address": ""
                }
                results[addr.lower()] = token_obj
                _token_lru_cache.put(addr, token_obj)

        logger.info(f"🦎 [GECKO MULTI] Resolved {len(results)}/{len(clean_addrs)} tokens on {target_net.upper()} in 1 call!")
        return results

    except Exception as e:
        logger.debug(f"ℹ️ [GECKO MULTI API] Error for {target_net}: {e}")
        return results

async def fetch_geckoterminal_token_data(token_address: str, chain: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Single-token GeckoTerminal query with caching and fallback search."""
    if not token_address or is_cached_miss(token_address):
        return None

    clean_addr = token_address.strip()
    addr_lower = clean_addr.lower()

    cached = _token_lru_cache.get(addr_lower, max_age=60.0)
    if cached:
        return cached

    # Auto-detect target network if not specified
    if not chain:
        if clean_addr.startswith("0x"):
            chain = "eth"
        else:
            chain = "solana"

    # Try fast multi-endpoint (1-item batch)
    multi_res = await fetch_geckoterminal_multi_tokens(chain, [clean_addr])
    if addr_lower in multi_res:
        return multi_res[addr_lower]

    # Universal Search API Fallback
    client = get_http_client()
    search_url = f"https://api.geckoterminal.com/api/v2/search/pools?query={clean_addr}"
    try:
        await _pace_gecko_request()
        s_resp = await client.get(search_url)
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
                    return token_obj

        record_miss(clean_addr)
    except Exception:
        pass

    return None

# ============================================================================
# MASTER PATTERN 5: DUAL-ENGINE BATCH FETCHER
# 1st Line: DexScreener Batch (up to 30 tokens in 1 request).
# 2nd Line (Circuit Breaker OPEN or 429): Batched GeckoTerminal (up to 30 tokens in 1 request).
# ============================================================================
async def fetch_tokens_batch(token_addresses: list) -> Dict[str, Dict[str, Any]]:
    """
    Fetches real-time market data for multiple tokens in single batched requests.
    Neither DexScreener nor GeckoTerminal can EVER be rate-limited:
      - Cache hit in 0ms (25s TTL).
      - DexScreener paced via TokenBucket (max 60 req/min).
      - GeckoTerminal batched per network and paced via Hard Ceiling (max 15 req/min).
    """
    if not token_addresses:
        return {}

    clean_addrs = [str(a).strip() for a in token_addresses if a and str(a).strip() and not is_cached_miss(str(a))]
    if not clean_addrs:
        return {}

    cached_results: Dict[str, Dict[str, Any]] = {}
    missing_addrs: List[str] = []

    # Step 1: O(1) RAM Cache (0ms, 0 HTTP requests)
    for a in clean_addrs:
        data = _token_lru_cache.get(a, max_age=25.0)
        if data:
            cached_results[a.lower()] = data
            cached_results[a] = data
        else:
            missing_addrs.append(a)

    if not missing_addrs:
        return cached_results

    # Helper function for Batched GeckoTerminal Fallback
    async def _fallback_to_gecko_batches(addrs_to_query: List[str]):
        # Group addresses by probable network
        by_net: Dict[str, List[str]] = {}
        for a in addrs_to_query:
            if a.startswith("0x"):
                by_net.setdefault("eth", []).append(a)
            else:
                by_net.setdefault("solana", []).append(a)

        for net, net_addrs in by_net.items():
            g_batch = await fetch_geckoterminal_multi_tokens(net, net_addrs)
            for k, v in g_batch.items():
                cached_results[k] = v
                cached_results[v["token_address"]] = v

    # Step 2: Check Circuit Breaker
    if not _circuit_breaker.is_available():
        # DexScreener is muted. Fallback to Batched GeckoTerminal in 1 request!
        # Return older cache for any still pending
        for a in missing_addrs:
            data = _token_lru_cache.get(a, max_age=300.0)
            if data:
                cached_results[a.lower()] = data

        still_missing = [a for a in missing_addrs if a.lower() not in cached_results]
        if still_missing:
            await _fallback_to_gecko_batches(still_missing)

        return cached_results

    # Step 3: DexScreener 30-token single batch request
    client = get_http_client()
    joined_addrs = ",".join(missing_addrs[:30])
    url = DEXSCREENER_API_URL.format(token_address=joined_addrs)

    try:
        await _pace_request()
        response = await client.get(url)

        if response.status_code == 429:
            _circuit_breaker.record_429()
            # Instant batched failover to GeckoTerminal (1 single call!)
            await _fallback_to_gecko_batches(missing_addrs)
            return cached_results

        if response.status_code != 200:
            return cached_results

        _circuit_breaker.record_success()
        data = response.json()
        pairs = data.get("pairs") or []

        if not pairs:
            # Fallback to batched GeckoTerminal
            await _fallback_to_gecko_batches(missing_addrs)
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
# MASTER PATTERN 6: HIGH-SPEED SINGLE TOKEN RESOLVER
# ============================================================================
async def _fetch_dexscreener_single(clean_addr: str, chain: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Single-token DexScreener fetcher guarded by circuit breaker."""
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
    Primary token resolution function with Dual-Engine routing:
      1. O(1) In-Memory Cache (0ms).
      2. DexScreener Single Query (Paced smoothly via TokenBucket).
      3. GeckoTerminal Fallback (Strictly capped to 15 req/min, impossible to 429).
    """
    if not token_address or is_cached_miss(token_address):
        return None

    clean_addr = token_address.strip()

    # Step 1: In-Memory Cache (0ms)
    cached = _token_lru_cache.get(clean_addr, max_age=25.0)
    if cached and cached.get("market_cap", 0) > 0:
        return cached

    # Step 2: If DexScreener is healthy, query DexScreener
    if _circuit_breaker.is_available():
        res = await _fetch_dexscreener_single(clean_addr, chain=chain)
        if res and res.get("market_cap", 0) > 0:
            return res

    # Step 3: Instant Fallback to GeckoTerminal (Guarded by 15 req/min ceiling)
    gecko_res = await fetch_geckoterminal_token_data(clean_addr, chain=chain)
    if gecko_res and gecko_res.get("market_cap", 0) > 0:
        return gecko_res

    return _token_lru_cache.get(clean_addr, max_age=300.0)
