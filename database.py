import aiosqlite
import logging
from typing import Optional, List, Dict, Any, Tuple
from config import DB_PATH

logger = logging.getLogger(__name__)

from contextlib import asynccontextmanager

@asynccontextmanager
async def get_db(db_path: str = DB_PATH):
    """Async context manager helper to open SQLite connection with WAL mode, capped memory cache, and 30s busy timeout."""
    db = await aiosqlite.connect(db_path, timeout=30.0)
    try:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("PRAGMA busy_timeout=30000;")
        await db.execute("PRAGMA synchronous=NORMAL;")
        await db.execute("PRAGMA cache_size=-2000;")
        await db.execute("PRAGMA temp_store=FILE;")
        yield db
    finally:
        await db.close()

async def init_db(db_path: str = DB_PATH):
    """Initialize database schema with WAL mode and 30s busy timeout."""
    async with get_db(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER UNIQUE NOT NULL,
                title TEXT,
                username TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                channel_title TEXT,
                channel_username TEXT,
                token_address TEXT NOT NULL,
                chain TEXT,
                token_symbol TEXT,
                token_name TEXT,
                entry_mc REAL DEFAULT 0,
                ath_mc REAL DEFAULT 0,
                current_mc REAL DEFAULT 0,
                highest_multiplier REAL DEFAULT 1.0,
                last_alert_multiplier REAL DEFAULT 1.0,
                is_dip_call INTEGER DEFAULT 0,
                prev_entry_mc REAL DEFAULT 0,
                call_msg_id INTEGER,
                call_msg_link TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        try:
            await db.execute("ALTER TABLE calls ADD COLUMN is_dip_call INTEGER DEFAULT 0;")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE calls ADD COLUMN prev_entry_mc REAL DEFAULT 0;")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE calls ADD COLUMN status TEXT;")
        except Exception:
            pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_id INTEGER NOT NULL,
                multiplier REAL NOT NULL,
                alert_msg_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (call_id) REFERENCES calls (id)
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS milestone_media (
                milestone REAL PRIMARY KEY,
                file_id TEXT NOT NULL,
                media_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS custom_media (
                media_key TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                media_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_config (
                config_key TEXT PRIMARY KEY,
                config_value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS consistency_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                milestone_count INTEGER NOT NULL,
                alert_msg_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_follows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, channel_id)
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS channel_owners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id INTEGER NOT NULL,
                owner_username TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(channel_id, owner_username)
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel_id INTEGER,
                tx_hash TEXT UNIQUE NOT NULL,
                amount_eth REAL DEFAULT 0.01,
                status TEXT DEFAULT 'PENDING',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deposit_wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT UNIQUE NOT NULL,
                label TEXT DEFAULT 'TRC20 USDT',
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        # Safe schema migration for subscription columns in channels table
        async with db.execute("PRAGMA table_info(channels)") as cursor:
            cols = [row[1] for row in await cursor.fetchall()]
        
        if "sub_type" not in cols:
            await db.execute("ALTER TABLE channels ADD COLUMN sub_type TEXT DEFAULT 'VIP'")
        if "sub_end" not in cols:
            await db.execute("ALTER TABLE channels ADD COLUMN sub_end TIMESTAMP DEFAULT NULL")
        if "reminded_7d" not in cols:
            await db.execute("ALTER TABLE channels ADD COLUMN reminded_7d INTEGER DEFAULT 0")
        if "reminded_3d" not in cols:
            await db.execute("ALTER TABLE channels ADD COLUMN reminded_3d INTEGER DEFAULT 0")
        if "owner_username" not in cols:
            await db.execute("ALTER TABLE channels ADD COLUMN owner_username TEXT DEFAULT NULL")
        if "owner_user_id" not in cols:
            await db.execute("ALTER TABLE channels ADD COLUMN owner_user_id INTEGER DEFAULT NULL")

        # Auto-seed initial wallet if deposit_wallets table is empty
        async with db.execute("SELECT COUNT(*) FROM deposit_wallets") as cursor:
            cnt = (await cursor.fetchone())[0]
            if cnt == 0:
                from config import DEPOSIT_WALLET_ADDRESS
                seed_wallet = DEPOSIT_WALLET_ADDRESS if DEPOSIT_WALLET_ADDRESS else "0xb774E2edd9118274d71d97d9DA7216D8998c36C2"
                await db.execute("INSERT OR IGNORE INTO deposit_wallets (wallet_address, label, is_active) VALUES (?, 'Main Wallet', 1)", (seed_wallet,))


        # Performance Indexes for sub-millisecond query execution
        await db.execute("CREATE INDEX IF NOT EXISTS idx_calls_token_addr ON calls (token_address);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_calls_channel_id ON calls (channel_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_calls_created_at ON calls (created_at);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_calls_status ON calls (status);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_alerts_call_id ON alerts (call_id);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_channels_channel_id ON channels (channel_id);")

        await db.commit()
        logger.info("Database initialized successfully.")

async def get_all_deposit_wallets(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deposit_wallets ORDER BY id ASC") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_active_deposit_wallet(db_path: str = DB_PATH) -> Optional[str]:
    async with get_db(db_path) as db:
        async with db.execute("SELECT wallet_address FROM deposit_wallets WHERE is_active = 1 LIMIT 1") as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0]
    return None

async def add_deposit_wallet(wallet_address: str, label: str = "Deposit Wallet", set_active: bool = False, db_path: str = DB_PATH) -> int:
    clean_addr = wallet_address.strip()
    async with get_db(db_path) as db:
        if set_active:
            await db.execute("UPDATE deposit_wallets SET is_active = 0")
        cursor = await db.execute("""
            INSERT INTO deposit_wallets (wallet_address, label, is_active)
            VALUES (?, ?, ?)
            ON CONFLICT(wallet_address) DO UPDATE SET label=excluded.label
        """, (clean_addr, label, 1 if set_active else 0))
        await db.commit()
        return cursor.lastrowid

async def set_active_deposit_wallet(wallet_id: int, db_path: str = DB_PATH) -> bool:
    async with get_db(db_path) as db:
        await db.execute("UPDATE deposit_wallets SET is_active = 0")
        cursor = await db.execute("UPDATE deposit_wallets SET is_active = 1 WHERE id = ?", (wallet_id,))
        await db.commit()
        return cursor.rowcount > 0

async def is_tx_hash_used(tx_hash: str, db_path: str = DB_PATH) -> bool:
    clean_tx = tx_hash.strip().lower()
    async with get_db(db_path) as db:
        async with db.execute("SELECT 1 FROM payments WHERE LOWER(tx_hash) = ?", (clean_tx,)) as cursor:
            return (await cursor.fetchone()) is not None

async def record_payment_submission(user_id: int, channel_id: Optional[int], tx_hash: str, amount_eth: float = 0.01, db_path: str = DB_PATH) -> int:
    clean_tx = tx_hash.strip().lower()
    async with get_db(db_path) as db:
        cursor = await db.execute("""
            INSERT INTO payments (user_id, channel_id, tx_hash, amount_eth, status)
            VALUES (?, ?, ?, ?, 'PENDING')
        """, (user_id, channel_id, clean_tx, amount_eth))
        await db.commit()
        return cursor.lastrowid

async def approve_payment(payment_id: int, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            p = dict(row)

        await db.execute("UPDATE payments SET status = 'APPROVED' WHERE id = ?", (payment_id,))
        if p.get("channel_id"):
            await set_channel_subscription(str(p["channel_id"]), "PAID", days=30, db_path=db_path)
        await db.commit()
        return p

async def add_channel(channel_id: int, title: str, username: Optional[str] = None, sub_type: str = "TRIAL", days: int = 7, db_path: str = DB_PATH):
    """Adds a new channel with 7-day default trial or specified days subscription."""
    from datetime import datetime, timezone, timedelta
    clean_user = (username or "").lstrip("@").strip().lower()
    sub_type_upper = (sub_type or "TRIAL").upper().strip()
    
    if sub_type_upper == "VIP":
        sub_end = None
    else:
        sub_end = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    cid_str = str(channel_id).strip()
    raw_digits = cid_str.replace("-100", "").replace("-", "")

    async with get_db(db_path) as db:
        # Check if already exists by username or ID
        existing_row = None
        if clean_user:
            async with db.execute("SELECT id FROM channels WHERE LOWER(username) = ?", (clean_user,)) as cur:
                existing_row = await cur.fetchone()

        if not existing_row and raw_digits and len(raw_digits) >= 5:
            async with db.execute("SELECT id FROM channels WHERE CAST(channel_id AS TEXT) LIKE '%' || ? || '%'", (raw_digits,)) as cur:
                existing_row = await cur.fetchone()

        if existing_row:
            existing_id = existing_row[0]
            await db.execute("""
                UPDATE channels 
                SET channel_id = ?, title = ?, username = ?, sub_type = ?, sub_end = ?
                WHERE id = ?
            """, (channel_id, title, clean_user, sub_type_upper, sub_end, existing_id))
        else:
            await db.execute("""
                INSERT INTO channels (channel_id, title, username, sub_type, sub_end, reminded_7d, reminded_3d)
                VALUES (?, ?, ?, ?, ?, 0, 0)
            """, (channel_id, title, clean_user, sub_type_upper, sub_end))
        await db.commit()

async def set_channel_subscription(identifier: str, sub_type: str, days: int = 30, db_path: str = DB_PATH) -> bool:
    """Updates a channel's subscription status (VIP, TRIAL, PAID, EXPIRED) by Channel ID, Username, or Title."""
    from datetime import datetime, timezone, timedelta
    clean = str(identifier).strip()
    clean_user = clean.lstrip("@").lower()
    sub_type_upper = sub_type.upper().strip()

    if sub_type_upper == "VIP":
        sub_end = None
    elif sub_type_upper == "EXPIRED":
        sub_end = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        sub_end = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    # Build candidate numeric channel_id strings (handling -100 prefixes, positive IDs, and raw string)
    clean_digits = clean.replace("-100", "").replace("-", "")
    possible_ids = list(set([clean, clean_digits, f"-100{clean_digits}", f"-{clean_digits}"]))

    async with get_db(db_path) as db:
        id_placeholders = ",".join(["?"] * len(possible_ids))
        cursor = await db.execute(f"""
            UPDATE channels 
            SET sub_type = ?,
                sub_end = ?,
                reminded_7d = 0,
                reminded_3d = 0
            WHERE CAST(channel_id AS TEXT) IN ({id_placeholders})
               OR LOWER(username) = ?
               OR LOWER(title) LIKE '%' || ? || '%'
        """, [sub_type_upper, sub_end] + possible_ids + [clean_user, clean_user])
        await db.commit()
        return cursor.rowcount > 0

async def is_channel_authorized(channel_id: Optional[int] = None, username: Optional[str] = None, db_path: str = DB_PATH) -> bool:
    """Checks if a channel is whitelisted AND has an active subscription (or VIP status)."""
    from datetime import datetime, timezone
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = None
        if channel_id:
            cid_str = str(channel_id).strip()
            raw_digits = cid_str.replace("-100", "").replace("-", "")
            possible_ids = list(set([cid_str, raw_digits, f"-100{raw_digits}", f"-{raw_digits}"]))
            
            id_placeholders = ",".join(["?"] * len(possible_ids))
            async with db.execute(f"""
                SELECT * FROM channels
                WHERE CAST(channel_id AS TEXT) IN ({id_placeholders})
                   OR (length(?) > 5 AND CAST(channel_id AS TEXT) LIKE '%' || ? || '%')
            """, possible_ids + [raw_digits, raw_digits]) as cursor:
                r = await cursor.fetchone()
                if r:
                    row = dict(r)

        if not row and username:
            clean_user = username.lstrip("@").lower().strip()
            if clean_user:
                async with db.execute("SELECT * FROM channels WHERE LOWER(username) = ? OR LOWER(title) LIKE '%' || ? || '%'", (clean_user, clean_user)) as cursor:
                    r = await cursor.fetchone()
                    if r:
                        row = dict(r)

        if not row:
            return False

        sub_type = (row.get("sub_type") or "VIP").upper()
        if sub_type in ("VIP", "LIFETIME"):
            return True

        if sub_type == "EXPIRED":
            return False

        sub_end_str = row.get("sub_end")
        if not sub_end_str:
            return True

        try:
            sub_end_dt = datetime.strptime(str(sub_end_str)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            now_dt = datetime.now(timezone.utc)
            if now_dt > sub_end_dt:
                # Auto-expire channel tracking
                await db.execute("UPDATE channels SET sub_type = 'EXPIRED' WHERE id = ?", (row["id"],))
                await db.commit()
                logger.info(f"Subscription expired for channel '{row['title']}' ({row['channel_id']}). Tracking paused.")
                return False
            return True
        except Exception:
            return True

async def remove_channel(identifier: str, db_path: str = DB_PATH) -> bool:
    clean = str(identifier).lstrip("@").strip()
    clean_lower = clean.lower()
    async with get_db(db_path) as db:
        if clean.lstrip("-").isdigit():
            c1 = await db.execute("DELETE FROM channels WHERE channel_id = ?", (int(clean),))
            c2 = await db.execute("DELETE FROM calls WHERE channel_id = ?", (int(clean),))
            await db.commit()
            return (c1.rowcount + c2.rowcount) > 0
        else:
            c1 = await db.execute("""
                DELETE FROM channels 
                WHERE LOWER(username) = ? OR LOWER(title) = ?
            """, (clean_lower, clean_lower))
            c2 = await db.execute("""
                DELETE FROM calls 
                WHERE LOWER(channel_username) = ? OR LOWER(channel_title) = ?
            """, (clean_lower, clean_lower))
            await db.commit()
            return (c1.rowcount + c2.rowcount) > 0

async def get_all_channels(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT channel_id, title, username, created_at,
                   COALESCE(sub_type, 'VIP') AS sub_type,
                   sub_end,
                   COALESCE(reminded_7d, 0) AS reminded_7d,
                   COALESCE(reminded_3d, 0) AS reminded_3d
            FROM (
                SELECT id, channel_id, title, username, created_at, sub_type, sub_end, reminded_7d, reminded_3d FROM channels
                UNION ALL
                SELECT NULL AS id, channel_id, channel_title AS title, channel_username AS username, MIN(created_at) AS created_at, 'VIP' AS sub_type, NULL AS sub_end, 0 AS reminded_7d, 0 AS reminded_3d
                FROM calls
                WHERE channel_id NOT IN (SELECT channel_id FROM channels)
                GROUP BY channel_id
            )
            ORDER BY created_at ASC
        """) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def get_channels_by_tier(tier_filter: Optional[str] = None, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Returns channels filtered by tier: 'VIP', 'TRIAL', 'PAID', 'EXPIRED', or 'ALL'."""
    all_channels = await get_all_channels(db_path=db_path)
    if not tier_filter or tier_filter.upper() == "ALL":
        return all_channels

    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    target_tier = tier_filter.upper().strip()

    filtered = []
    for c in all_channels:
        stype = (c.get("sub_type") or "VIP").upper()
        sub_end_str = c.get("sub_end")
        
        is_expired = False
        if stype == "EXPIRED":
            is_expired = True
        elif sub_end_str and stype not in ("VIP", "LIFETIME"):
            try:
                sub_end_dt = datetime.strptime(str(sub_end_str)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                if now_utc > sub_end_dt:
                    is_expired = True
            except Exception:
                pass

        if target_tier == "EXPIRED":
            if is_expired:
                filtered.append(c)
        elif target_tier in ("VIP", "LIFETIME"):
            if stype in ("VIP", "LIFETIME") and not is_expired:
                filtered.append(c)
        elif target_tier == "TRIAL":
            if stype == "TRIAL" and not is_expired:
                filtered.append(c)
        elif target_tier == "PAID":
            if stype == "PAID" and not is_expired:
                filtered.append(c)

    return filtered

async def bulk_set_channel_status(targets: List[str], sub_type: str, days: int = 30, db_path: str = DB_PATH) -> List[str]:
    """Bulk updates subscription status for multiple channels. Returns list of updated target names."""
    updated = []
    for t in targets:
        clean = t.strip()
        if not clean:
            continue
        success = await set_channel_subscription(clean, sub_type, days=days, db_path=db_path)
        if success:
            updated.append(clean)
    return updated

async def record_call(
    channel_id: int,
    channel_title: str,
    channel_username: Optional[str],
    token_address: str,
    chain: str,
    token_symbol: str,
    token_name: str,
    entry_mc: float,
    call_msg_id: Optional[int] = None,
    call_msg_link: Optional[str] = None,
    db_path: str = DB_PATH
) -> Tuple[Optional[int], bool, float]:
    """
    Records a call. If token was previously called by the same channel at a higher price,
    records a Dip Re-Call entry. Returns (call_id, is_dip_call, prev_entry_mc).
    """
    clean_addr = token_address.strip().lower()
    clean_user = (channel_username or "").lstrip("@").strip().lower()

    async with get_db(db_path) as db:
        is_dip_call = 0
        prev_entry_mc = 0.0

        async with db.execute("""
            SELECT id, entry_mc, created_at FROM calls
            WHERE (
                channel_id = ? 
                OR (? != '' AND LOWER(channel_username) = ?)
            )
            AND LOWER(token_address) = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (channel_id, clean_user, clean_user, clean_addr)) as cursor:
            existing = await cursor.fetchone()
            if existing:
                existing_id, prev_entry_mc, created_at_str = existing[0], float(existing[1] or 0), str(existing[2] or "")
                
                # Cooldown check: If called by same channel within the last 30 minutes, ignore duplicate!
                from datetime import datetime, timezone
                try:
                    call_time = datetime.strptime(created_at_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    age_minutes = (datetime.now(timezone.utc) - call_time).total_seconds() / 60.0
                except Exception:
                    age_minutes = 999.0

                if age_minutes < 30.0:
                    logger.info(f"Duplicate call ignored (30m cooldown active: {age_minutes:.1f}m ago) for {token_symbol} in channel '{channel_title}'. Existing call ID: {existing_id}")
                    return None, False, 0.0

                # Check if this new call is a legitimate Dip Re-Call (entry MC at least 25% lower than previous entry MC)
                if prev_entry_mc > 0 and entry_mc < (prev_entry_mc * 0.75):
                    is_dip_call = 1
                    dip_pct = ((prev_entry_mc - entry_mc) / prev_entry_mc) * 100
                    logger.info(f"📉 [DIP RE-CALL AUDIT] Valid deep dip re-entry ({dip_pct:.0f}% drop, {age_minutes:.0f}m later) for {token_symbol} in '{channel_title}'! Initial: ${prev_entry_mc:,.0f} ➔ Dip: ${entry_mc:,.0f}")
                else:
                    logger.info(f"Duplicate call ignored (insufficient dip or already called) for {token_symbol} in '{channel_title}'. Existing call ID: {existing_id}")
                    return None, False, 0.0

        cursor = await db.execute("""
            INSERT INTO calls (
                channel_id, channel_title, channel_username,
                token_address, chain, token_symbol, token_name,
                entry_mc, ath_mc, current_mc, highest_multiplier, last_alert_multiplier,
                is_dip_call, prev_entry_mc, call_msg_id, call_msg_link
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 1.0, ?, ?, ?, ?)
        """, (
            channel_id, channel_title, channel_username,
            token_address, chain, token_symbol, token_name,
            entry_mc, entry_mc, entry_mc, is_dip_call, prev_entry_mc,
            call_msg_id, call_msg_link
        ))
        await db.commit()
        return cursor.lastrowid, bool(is_dip_call), prev_entry_mc

async def get_active_calls(days: int = 14, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute("""
                SELECT * FROM calls
                WHERE created_at >= datetime('now', '-' || ? || ' days')
                  AND (status IS NULL OR status NOT IN ('rugged', 'dead'))
                ORDER BY created_at DESC
            """, (days,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception:
            async with db.execute("""
                SELECT * FROM calls
                WHERE created_at >= datetime('now', '-' || ? || ' days')
                ORDER BY created_at DESC
            """, (days,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

async def get_hot_active_calls(hours: int = 2, limit: int = 50, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Lightweight query fetching ONLY hot live calls directly in SQL (<= 2h old, max 50 rows)."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute("""
                SELECT * FROM calls
                WHERE created_at >= datetime('now', '-' || ? || ' hours')
                  AND (status IS NULL OR status NOT IN ('rugged', 'dead'))
                ORDER BY created_at DESC
                LIMIT ?
            """, (hours, limit)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception:
            async with db.execute("""
                SELECT * FROM calls
                WHERE created_at >= datetime('now', '-' || ? || ' hours')
                ORDER BY created_at DESC
                LIMIT ?
            """, (hours, limit)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

async def get_archival_active_calls_slice(hours_cutoff: int = 2, days: int = 14, offset: int = 0, limit: int = 30, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Lightweight paginated slice query for older calls directly in SQL (30 rows max, 0 memory bloat)."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute("""
                SELECT * FROM calls
                WHERE created_at >= datetime('now', '-' || ? || ' days')
                  AND created_at < datetime('now', '-' || ? || ' hours')
                  AND (status IS NULL OR status NOT IN ('rugged', 'dead'))
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """, (days, hours_cutoff, limit, offset)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception:
            async with db.execute("""
                SELECT * FROM calls
                WHERE created_at >= datetime('now', '-' || ? || ' days')
                  AND created_at < datetime('now', '-' || ? || ' hours')
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """, (days, hours_cutoff, limit, offset)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

async def get_archival_active_calls_count(hours_cutoff: int = 2, days: int = 14, db_path: str = DB_PATH) -> int:
    """Returns exact count of archival calls directly in SQL without loading rows into RAM."""
    async with get_db(db_path) as db:
        try:
            async with db.execute("""
                SELECT COUNT(*) FROM calls
                WHERE created_at >= datetime('now', '-' || ? || ' days')
                  AND created_at < datetime('now', '-' || ? || ' hours')
                  AND (status IS NULL OR status NOT IN ('rugged', 'dead'))
            """, (days, hours_cutoff)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
        except Exception:
            async with db.execute("""
                SELECT COUNT(*) FROM calls
                WHERE created_at >= datetime('now', '-' || ? || ' days')
                  AND created_at < datetime('now', '-' || ? || ' hours')
            """, (days, hours_cutoff)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

async def get_active_calls_count(days: int = 14, db_path: str = DB_PATH) -> int:
    """Returns exact count of all active calls directly in SQL without loading rows into RAM."""
    async with get_db(db_path) as db:
        try:
            async with db.execute("""
                SELECT COUNT(*) FROM calls
                WHERE created_at >= datetime('now', '-' || ? || ' days')
                  AND (status IS NULL OR status NOT IN ('rugged', 'dead'))
            """, (days,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
        except Exception:
            async with db.execute("""
                SELECT COUNT(*) FROM calls
                WHERE created_at >= datetime('now', '-' || ? || ' days')
            """, (days,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

async def get_recent_call_keys(days: int = 7, db_path: str = DB_PATH) -> List[Tuple[Optional[str], Optional[int], str]]:
    """Fetches ONLY channel_username, channel_id, token_address for startup deduplication without loading full rows."""
    async with get_db(db_path) as db:
        async with db.execute("""
            SELECT channel_username, channel_id, token_address FROM calls
            WHERE created_at >= datetime('now', '-' || ? || ' days')
        """, (days,)) as cursor:
            return await cursor.fetchall()

async def update_call_price(
    call_id: int,
    current_mc: float,
    ath_mc: float,
    highest_multiplier: float,
    last_alert_multiplier: float,
    db_path: str = DB_PATH
):
    async with get_db(db_path) as db:
        await db.execute("""
            UPDATE calls
            SET current_mc = ?,
                ath_mc = ?,
                highest_multiplier = ?,
                last_alert_multiplier = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (current_mc, ath_mc, highest_multiplier, last_alert_multiplier, call_id))
        await db.commit()

async def mark_call_status(call_id: int, status: str, db_path: str = DB_PATH):
    async with get_db(db_path) as db:
        await db.execute("UPDATE calls SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (status, call_id))
        await db.commit()

async def update_call_metadata(call_id: int, token_symbol: str, token_name: str, chain: str, db_path: str = DB_PATH):
    """Updates call record with real token symbol, name, and chain once indexed on DEX."""
    async with get_db(db_path) as db:
        await db.execute("""
            UPDATE calls
            SET token_symbol = ?,
                token_name = ?,
                chain = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (token_symbol, token_name, chain, call_id))
        await db.commit()

async def update_calls_price_batch(updates: list, db_path: str = DB_PATH):
    """Updates multiple calls' price and multipliers in 1 single SQLite transaction."""
    if not updates:
        return
    async with get_db(db_path) as db:
        await db.executemany("""
            UPDATE calls
            SET current_mc = ?,
                ath_mc = ?,
                highest_multiplier = ?,
                last_alert_multiplier = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, [
            (u["current_mc"], u["ath_mc"], u["highest_multiplier"], u["last_alert_multiplier"], u["call_id"])
            for u in updates if u.get("call_id")
        ])
        await db.commit()

async def has_alert_been_sent(call_id: int, multiplier: float, db_path: str = DB_PATH) -> bool:
    async with get_db(db_path) as db:
        async with db.execute("""
            SELECT 1 FROM alerts WHERE call_id = ? AND ABS(multiplier - ?) < 0.01
        """, (call_id, multiplier)) as cursor:
            row = await cursor.fetchone()
            return row is not None

async def has_alert_been_sent_by_token(token_address: str, caller_key: str, multiplier: float, db_path: str = DB_PATH) -> bool:
    clean_addr = (token_address or "").strip().lower()
    clean_caller = (caller_key or "").strip().lower()
    async with get_db(db_path) as db:
        async with db.execute("""
            SELECT 1 FROM alerts a
            JOIN calls c ON a.call_id = c.id
            WHERE LOWER(c.token_address) = ?
              AND (LOWER(c.channel_username) = ? OR CAST(c.channel_id AS TEXT) = ?)
              AND ABS(a.multiplier - ?) < 0.01
            LIMIT 1
        """, (clean_addr, clean_caller, clean_caller, multiplier)) as cursor:
            row = await cursor.fetchone()
            return row is not None

async def record_alert(call_id: int, multiplier: float, alert_msg_id: Optional[int] = None, db_path: str = DB_PATH):
    async with get_db(db_path) as db:
        await db.execute("""
            INSERT OR IGNORE INTO alerts (call_id, multiplier, alert_msg_id)
            VALUES (?, ?, ?)
        """, (call_id, multiplier, alert_msg_id))
        await db.commit()

async def get_channel_stats(identifier: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    """Calculates full caller stats matching AlphaRadar checkstats format."""
    clean = identifier.strip()
    clean_no_at = clean.lstrip("@").lower()
    
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        
        # Search by username, title, channel ID, token_address, or title substring
        async with db.execute("""
            SELECT * FROM calls 
            WHERE LOWER(channel_username) = ? 
               OR LOWER(channel_title) = ?
               OR CAST(channel_id AS TEXT) = ?
               OR LOWER(token_address) = ?
               OR LOWER(channel_title) LIKE '%' || ? || '%'
            ORDER BY created_at DESC
        """, (clean_no_at, clean.lower(), clean, clean.lower(), clean_no_at)) as cursor:
            rows = await cursor.fetchall()
            
        if not rows:
            return None

        calls = [dict(r) for r in rows]
        total_calls = len(calls)
        
        achievement_calls_2x = [c for c in calls if c['highest_multiplier'] >= 2.0]
        achievement_calls_5x = [c for c in calls if c['highest_multiplier'] >= 5.0]
        
        # Calculate stats
        consistency = (len(achievement_calls_2x) / total_calls * 100) if total_calls > 0 else 0
        avg_entry_mc = sum(c['entry_mc'] for c in calls) / total_calls if total_calls > 0 else 0
        avg_x = sum(c['highest_multiplier'] for c in calls) / total_calls if total_calls > 0 else 1.0
        
        # Best call
        best_call = max(calls, key=lambda c: c['highest_multiplier'])
        last_call = calls[0]
        
        # Last 5 calls
        last_5 = calls[:5]
        
        # Top 3 calls
        top_3 = sorted(calls, key=lambda c: c['highest_multiplier'], reverse=True)[:3]
        
        # Simulation: $100 into last 5 calls
        sim_ath_val = sum(100 * c['highest_multiplier'] for c in last_5)
        sim_current_val = sum(100 * (c['current_mc'] / c['entry_mc'] if c['entry_mc'] > 0 else 1) for c in last_5)

        return {
            "channel_title": calls[0]['channel_title'],
            "channel_username": calls[0]['channel_username'],
            "total_calls": total_calls,
            "consistency": round(consistency, 1),
            "alpha_calls": len([c for c in calls if c['highest_multiplier'] >= 10.0]),
            "best_call": best_call,
            "last_call": last_call,
            "avg_entry_mc": avg_entry_mc,
            "avg_x": round(avg_x, 1),
            "calls_2x_plus": len(achievement_calls_2x),
            "calls_5x_plus": len(achievement_calls_5x),
            "last_5": last_5,
            "top_3": top_3,
            "sim_ath_val": round(sim_ath_val, 2),
            "sim_current_val": round(sim_current_val, 2)
        }

async def get_calls_by_token(token_address: str, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Returns calls for a token, ordered by creation time to find the FIRST caller."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM calls
            WHERE LOWER(token_address) = LOWER(?)
            ORDER BY created_at ASC
        """, (token_address,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def set_custom_media(media_key: str, file_id: str, media_type: str, db_path: str = DB_PATH):
    key_clean = media_key.lower().strip()
    async with get_db(db_path) as db:
        await db.execute("""
            INSERT INTO custom_media (media_key, file_id, media_type)
            VALUES (?, ?, ?)
            ON CONFLICT(media_key) DO UPDATE SET file_id=excluded.file_id, media_type=excluded.media_type
        """, (key_clean, file_id, media_type))
        await db.commit()

async def remove_custom_media(media_key: str, db_path: str = DB_PATH) -> bool:
    key_clean = media_key.lower().strip()
    async with get_db(db_path) as db:
        cursor = await db.execute("DELETE FROM custom_media WHERE media_key = ?", (key_clean,))
        await db.commit()
        return cursor.rowcount > 0

async def get_custom_media(media_key: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    key_clean = media_key.lower().strip()
    possible_keys = [key_clean]
    if key_clean.startswith("x"):
        raw_num = key_clean[1:]
        possible_keys.extend([f"{raw_num}x", raw_num])
    elif key_clean.endswith("x"):
        raw_num = key_clean[:-1]
        possible_keys.extend([f"x{raw_num}", raw_num])
    else:
        possible_keys.extend([f"{key_clean}x", f"x{key_clean}"])

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        for pk in possible_keys:
            async with db.execute("SELECT * FROM custom_media WHERE media_key = ?", (pk,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
        
        # Legacy fallback if numeric
        try:
            val = float(key_clean.replace("x", ""))
            async with db.execute("SELECT * FROM milestone_media WHERE milestone = ?", (val,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
        except ValueError:
            return None

async def get_all_custom_media(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM custom_media ORDER BY media_key ASC") as cursor:
            rows = await cursor.fetchall()
            results = [dict(r) for r in rows]
            
        async with db.execute("SELECT * FROM milestone_media ORDER BY milestone ASC") as cursor:
            rows_m = await cursor.fetchall()
            for r in rows_m:
                d = dict(r)
                m_val = d['milestone']
                m_str = f"{int(m_val)}x" if m_val.is_integer() else f"{m_val}x"
                if not any(x['media_key'] == m_str for x in results):
                    results.append({"media_key": m_str, "file_id": d["file_id"], "media_type": d["media_type"]})
        return results

async def set_milestone_media(milestone: float, file_id: str, media_type: str, db_path: str = DB_PATH):
    m_str = f"{int(milestone)}x" if milestone.is_integer() else f"{milestone}x"
    await set_custom_media(m_str, file_id, media_type, db_path=db_path)

async def remove_milestone_media(milestone: float, db_path: str = DB_PATH) -> bool:
    m_str = f"{int(milestone)}x" if milestone.is_integer() else f"{milestone}x"
    return await remove_custom_media(m_str, db_path=db_path)

async def get_milestone_media(milestone: float, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    m_str = f"{int(milestone)}x" if milestone.is_integer() else f"{milestone}x"
    return await get_custom_media(m_str, db_path=db_path)

async def get_all_milestone_media(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    return await get_all_custom_media(db_path=db_path)

async def set_bot_config(key: str, value: str, db_path: str = DB_PATH):
    async with get_db(db_path) as db:
        await db.execute("""
            INSERT INTO bot_config (config_key, config_value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(config_key) DO UPDATE SET config_value=excluded.config_value, updated_at=CURRENT_TIMESTAMP
        """, (key, value))
        await db.commit()

async def get_bot_config(key: str, db_path: str = DB_PATH) -> Optional[str]:
    async with get_db(db_path) as db:
        async with db.execute("SELECT config_value FROM bot_config WHERE config_key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def get_caller_x2_calls_7d(channel_id: int, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM calls
            WHERE channel_id = ?
              AND highest_multiplier >= 2.0
              AND created_at >= datetime('now', '-7 days')
            ORDER BY created_at DESC
        """, (channel_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def record_consistency_alert(channel_id: int, milestone_count: int, db_path: str = DB_PATH) -> bool:
    async with get_db(db_path) as db:
        try:
            await db.execute("""
                INSERT INTO consistency_alerts (channel_id, milestone_count)
                VALUES (?, ?)
            """, (channel_id, milestone_count))
            await db.commit()
            return True
        except Exception:
            return False

async def get_recently_called_stats(db_path: str = DB_PATH) -> Dict[str, Any]:
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        
        async with db.execute("""
            SELECT token_symbol, token_name, token_address, COUNT(*) as call_count
            FROM calls
            WHERE created_at >= datetime('now', '-1 hour')
            GROUP BY token_address
            ORDER BY call_count DESC, created_at DESC
            LIMIT 5
        """) as cursor:
            last_1h = [dict(r) for r in await cursor.fetchall()]

        async with db.execute("""
            SELECT token_symbol, token_name, token_address, COUNT(*) as call_count
            FROM calls
            WHERE created_at >= datetime('now', '-24 hours')
            GROUP BY token_address
            ORDER BY call_count DESC, created_at DESC
            LIMIT 5
        """) as cursor:
            last_24h = [dict(r) for r in await cursor.fetchall()]

        return {
            "last_1h": last_1h,
            "last_24h": last_24h
        }

async def get_daily_leaderboard_stats(db_path: str = DB_PATH) -> Dict[str, Any]:
    """Fetches comprehensive 24-hour daily leaderboard stats including KOL of the Day and top trending coins."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row

        # 1. Top 5 Trending Projects in past 24 hours
        async with db.execute("""
            SELECT token_symbol, token_name, token_address, chain, COUNT(*) as call_count, MAX(highest_multiplier) as max_mult
            FROM calls
            WHERE created_at >= datetime('now', '-24 hours')
            GROUP BY token_address
            ORDER BY call_count DESC, created_at DESC
            LIMIT 5
        """) as cursor:
            top_coins = [dict(r) for r in await cursor.fetchall()]

        # 2. Top Callers in past 24 hours
        async with db.execute("""
            SELECT channel_id, channel_title, channel_username,
                   COUNT(*) as total_calls,
                   SUM(CASE WHEN highest_multiplier >= 2.0 THEN 1 ELSE 0 END) as win_calls,
                   AVG(highest_multiplier) as avg_mult,
                   MAX(highest_multiplier) as best_mult
            FROM calls
            WHERE created_at >= datetime('now', '-24 hours')
            GROUP BY channel_id
            ORDER BY total_calls DESC, best_mult DESC
            LIMIT 5
        """) as cursor:
            top_callers = [dict(r) for r in await cursor.fetchall()]

        # 3. Best Single Alpha Call for the #1 Caller of the Day
        kol_of_day = top_callers[0] if top_callers else None
        top_call_info = None
        if kol_of_day:
            async with db.execute("""
                SELECT token_name, token_symbol, token_address, entry_mc, ath_mc, highest_multiplier, chain
                FROM calls
                WHERE channel_id = ? AND created_at >= datetime('now', '-24 hours')
                ORDER BY highest_multiplier DESC
                LIMIT 1
            """, (kol_of_day["channel_id"],)) as cursor:
                row = await cursor.fetchone()
                if row:
                    top_call_info = dict(row)

        return {
            "top_coins": top_coins,
            "top_callers": top_callers,
            "kol_of_day": kol_of_day,
            "top_call_info": top_call_info
        }

async def get_weekly_leaderboard_stats(db_path: str = DB_PATH) -> Dict[str, Any]:
    """Fetches comprehensive 7-day weekly championship stats including KOL of the Week and top runners."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row

        # 1. Weekly Top 5 Callers (7 Days)
        async with db.execute("""
            SELECT channel_id, channel_title, channel_username,
                   COUNT(*) as total_calls,
                   SUM(CASE WHEN highest_multiplier >= 2.0 THEN 1 ELSE 0 END) as win_calls,
                   AVG(highest_multiplier) as avg_mult,
                   MAX(highest_multiplier) as best_mult
            FROM calls
            WHERE created_at >= datetime('now', '-7 days')
            GROUP BY channel_id
            HAVING total_calls >= 1
            ORDER BY total_calls DESC, best_mult DESC
            LIMIT 5
        """) as cursor:
            weekly_callers = [dict(r) for r in await cursor.fetchall()]

        # 2. Best Single Call across all channels in past 7 days
        best_call_7d = None
        async with db.execute("""
            SELECT channel_title, channel_username, token_name, token_symbol, token_address, entry_mc, ath_mc, highest_multiplier, chain
            FROM calls
            WHERE created_at >= datetime('now', '-7 days')
            ORDER BY highest_multiplier DESC
            LIMIT 1
        """) as cursor:
            row = await cursor.fetchone()
            if row:
                best_call_7d = dict(row)

        # 3. Champion / KOL of the Week (#1 Caller)
        kol_of_week = weekly_callers[0] if weekly_callers else None
        champion_top_call = None
        if kol_of_week:
            async with db.execute("""
                SELECT token_name, token_symbol, token_address, entry_mc, ath_mc, highest_multiplier, chain
                FROM calls
                WHERE channel_id = ? AND created_at >= datetime('now', '-7 days')
                ORDER BY highest_multiplier DESC
                LIMIT 1
            """, (kol_of_week["channel_id"],)) as cursor:
                row = await cursor.fetchone()
                if row:
                    champion_top_call = dict(row)

        return {
            "weekly_callers": weekly_callers,
            "best_call_7d": best_call_7d,
            "kol_of_week": kol_of_week,
            "champion_top_call": champion_top_call
        }

async def link_channel_owner(channel_target: str, owner_target: str, db_path: str = DB_PATH) -> bool:
    """Links an owner's personal username or ID to a tracked channel in DB."""
    clean_ch = str(channel_target).strip()
    """Links one or multiple owner usernames/IDs to a tracked channel in DB."""
    clean_ch = str(channel_target).strip().lstrip("@")
    owners_raw = [o.strip() for o in owner_target.replace(",", " ").split() if o.strip()]
    if not owners_raw:
        return False

    formatted_owners = []
    for o in owners_raw:
        clean_o = str(o).strip()
        if not clean_o.startswith("@") and not clean_o.lstrip("-").isdigit():
            clean_o = f"@{clean_o.lstrip('@')}"
        formatted_owners.append(clean_o)

    primary_owner = formatted_owners[0]

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        # Find channel_id
        if clean_ch.lstrip("-").isdigit():
            async with db.execute("SELECT channel_id FROM channels WHERE channel_id = ?", (int(clean_ch),)) as cur:
                row = await cur.fetchone()
        else:
            async with db.execute("SELECT channel_id FROM channels WHERE LOWER(username) = LOWER(?)", (clean_ch,)) as cur:
                row = await cur.fetchone()

        if not row:
            async with db.execute("SELECT DISTINCT channel_id FROM calls WHERE LOWER(channel_username) = LOWER(?) OR CAST(channel_id AS TEXT) LIKE '%' || ? || '%' LIMIT 1", (clean_ch, clean_ch)) as cur:
                row = await cur.fetchone()

        if not row:
            return False

        ch_id = row["channel_id"]

        # Update primary owner in channels table
        await db.execute("UPDATE channels SET owner_username = ? WHERE channel_id = ?", (primary_owner, ch_id))

        # Insert all owners into channel_owners table
        for o in formatted_owners:
            try:
                await db.execute(
                    "INSERT INTO channel_owners (channel_id, owner_username) VALUES (?, ?)",
                    (ch_id, o)
                )
            except Exception:
                pass

        await db.commit()
        return True

async def unlink_channel_owner(channel_target: str, owner_target: Optional[str] = None, db_path: str = DB_PATH) -> bool:
    """Unlinks a specific owner or all owners from a tracked channel in DB."""
    if not channel_target:
        return False

    clean_ch = str(channel_target).strip().lstrip("@")
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        # Find channel_id
        if clean_ch.lstrip("-").isdigit():
            async with db.execute("SELECT channel_id FROM channels WHERE channel_id = ?", (int(clean_ch),)) as cur:
                row = await cur.fetchone()
        else:
            async with db.execute("SELECT channel_id FROM channels WHERE LOWER(username) = LOWER(?)", (clean_ch,)) as cur:
                row = await cur.fetchone()

        if not row:
            async with db.execute("SELECT DISTINCT channel_id FROM calls WHERE LOWER(channel_username) = LOWER(?) OR CAST(channel_id AS TEXT) LIKE '%' || ? || '%' LIMIT 1", (clean_ch, clean_ch)) as cur:
                row = await cur.fetchone()

        if not row:
            return False

        ch_id = row["channel_id"]

        if owner_target:
            clean_owner = str(owner_target).strip()
            if not clean_owner.startswith("@") and not clean_owner.lstrip("-").isdigit():
                clean_owner = f"@{clean_owner.lstrip('@')}"

            # Delete specific owner
            await db.execute(
                "DELETE FROM channel_owners WHERE channel_id = ? AND (LOWER(owner_username) = LOWER(?) OR owner_username = ?)",
                (ch_id, clean_owner, clean_owner)
            )

            # Check remaining owners
            async with db.execute("SELECT owner_username FROM channel_owners WHERE channel_id = ?", (ch_id,)) as cur:
                rem_rows = await cur.fetchall()
                if rem_rows:
                    new_primary = rem_rows[0]["owner_username"]
                    await db.execute("UPDATE channels SET owner_username = ? WHERE channel_id = ?", (new_primary, ch_id))
                else:
                    await db.execute("UPDATE channels SET owner_username = NULL, owner_user_id = NULL WHERE channel_id = ?", (ch_id,))
        else:
            # Remove all owners
            await db.execute("DELETE FROM channel_owners WHERE channel_id = ?", (ch_id,))
            await db.execute("UPDATE channels SET owner_username = NULL, owner_user_id = NULL WHERE channel_id = ?", (ch_id,))

        await db.commit()
        return True

async def get_channel_owners(channel_identifier: Any, db_path: str = DB_PATH) -> List[str]:
    """Returns list of all owner_usernames or owner_user_ids for a given channel."""
    if not channel_identifier:
        return []
    clean_id = str(channel_identifier).lstrip("@").strip()
    raw_digits = "".join(filter(str.isdigit, clean_id))

    owners = set()
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        
        # 1. Check primary owner from channels
        query = "SELECT owner_username, owner_user_id, channel_id FROM channels WHERE LOWER(username) = LOWER(?)"
        params = [clean_id]
        if raw_digits:
            query += " OR CAST(channel_id AS TEXT) LIKE '%' || ? || '%'"
            params.append(raw_digits)

        ch_id = None
        async with db.execute(query, params) as cursor:
            row = await cursor.fetchone()
            if row:
                ch_id = row["channel_id"]
                if row["owner_username"]:
                    owners.add(row["owner_username"])
                if row["owner_user_id"]:
                    owners.add(str(row["owner_user_id"]))

        # 2. Check channel_owners table
        if ch_id:
            async with db.execute("SELECT owner_username FROM channel_owners WHERE channel_id = ?", (ch_id,)) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    if r["owner_username"]:
                        owners.add(r["owner_username"])

    return list(owners)

async def get_channel_owner(channel_identifier: Any, db_path: str = DB_PATH) -> Optional[str]:
    """Backward-compatible helper returning primary owner username or ID."""
    all_owners = await get_channel_owners(channel_identifier, db_path)
    return all_owners[0] if all_owners else None

async def get_all_linked_owners(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Returns all channels that have linked owner_usernames or owner_user_ids, including co-owners."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        
        async with db.execute("""
            SELECT id, channel_id, title, username, owner_username, owner_user_id
            FROM channels
            WHERE (owner_username IS NOT NULL AND owner_username != '')
               OR (owner_user_id IS NOT NULL AND owner_user_id != 0)
        """) as cursor:
            ch_rows = [dict(r) for r in await cursor.fetchall()]

        ch_map = {}
        for c in ch_rows:
            cid = c["channel_id"]
            owners = set()
            if c.get("owner_username"):
                owners.add(c["owner_username"])
            if c.get("owner_user_id"):
                owners.add(str(c["owner_user_id"]))
            ch_map[cid] = {
                "channel_id": cid,
                "title": c.get("title") or "Channel",
                "username": c.get("username") or "",
                "owners": owners
            }

        async with db.execute("""
            SELECT co.channel_id, co.owner_username, c.title, c.username
            FROM channel_owners co
            LEFT JOIN channels c ON co.channel_id = c.channel_id
        """) as cursor:
            co_rows = [dict(r) for r in await cursor.fetchall()]
            for r in co_rows:
                cid = r["channel_id"]
                if cid not in ch_map:
                    ch_map[cid] = {
                        "channel_id": cid,
                        "title": r.get("title") or "Channel",
                        "username": r.get("username") or "",
                        "owners": set()
                    }
                if r.get("owner_username"):
                    ch_map[cid]["owners"].add(r["owner_username"])

        results = []
        for cid, data in ch_map.items():
            if data["owners"]:
                results.append({
                    "channel_id": cid,
                    "title": data["title"],
                    "username": data["username"],
                    "owner_username": ", ".join(sorted(data["owners"])),
                    "owners_list": sorted(list(data["owners"]))
                })
        return results

async def update_channel_id_by_username(username: str, channel_id: int, db_path: str = DB_PATH) -> bool:
    """Updates channel_id for a channel matching username where channel_id is 0 or NULL."""
    if not username:
        return False
    clean_user = username.strip().lstrip("@")
    async with get_db(db_path) as db:
        cursor = await db.execute("""
            UPDATE channels
            SET channel_id = ?
            WHERE LOWER(username) = LOWER(?) AND (channel_id = 0 OR channel_id IS NULL)
        """, (channel_id, clean_user))
        await db.commit()
        return cursor.rowcount > 0

async def sync_channel_identity(old_channel_id: Optional[int], username: str, real_channel_id: int, real_title: str, db_path: str = DB_PATH) -> bool:
    """Synchronizes real numeric Telegram channel ID and title across channels, calls, and channel_owners."""
    if not username and not old_channel_id:
        return False
    clean_user = username.strip().lstrip("@").lower() if username else ""
    async with get_db(db_path) as db:
        # 1. Update channels table
        if clean_user and old_channel_id:
            await db.execute("""
                UPDATE channels 
                SET channel_id = ?, title = ? 
                WHERE LOWER(username) = ? OR channel_id = ?
            """, (real_channel_id, real_title, clean_user, old_channel_id))
        elif clean_user:
            await db.execute("""
                UPDATE channels 
                SET channel_id = ?, title = ? 
                WHERE LOWER(username) = ?
            """, (real_channel_id, real_title, clean_user))
        elif old_channel_id:
            await db.execute("""
                UPDATE channels 
                SET channel_id = ?, title = ? 
                WHERE channel_id = ?
            """, (real_channel_id, real_title, old_channel_id))

        # 2. Update calls table for historical calls
        if clean_user:
            await db.execute("""
                UPDATE calls 
                SET channel_id = ?, channel_title = ? 
                WHERE LOWER(channel_username) = ?
            """, (real_channel_id, real_title, clean_user))
        if old_channel_id and old_channel_id != real_channel_id:
            await db.execute("""
                UPDATE calls 
                SET channel_id = ?, channel_title = ? 
                WHERE channel_id = ?
            """, (real_channel_id, real_title, old_channel_id))
            await db.execute("""
                UPDATE channel_owners 
                SET channel_id = ? 
                WHERE channel_id = ?
            """, (real_channel_id, old_channel_id))

        await db.commit()
        return True

async def record_bot_visitor(user_id: int, username: Optional[str] = None, first_name: Optional[str] = None, db_path: str = DB_PATH):
    """Records or updates a user visiting the bot."""
    if not user_id:
        return
    async with get_db(db_path) as db:
        await db.execute("""
            INSERT INTO bot_users (user_id, username, first_name, last_seen)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                username = COALESCE(EXCLUDED.username, bot_users.username),
                first_name = COALESCE(EXCLUDED.first_name, bot_users.first_name),
                last_seen = CURRENT_TIMESTAMP
        """, (user_id, username, first_name))
        await db.commit()

async def get_visitor_stats(db_path: str = DB_PATH) -> Dict[str, Any]:
    """Returns analytics stats and active visitors list for the bot."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT COUNT(*) as total FROM bot_users") as cur:
            row_total = await cur.fetchone()
            total_users = row_total["total"] if row_total else 0

        async with db.execute("SELECT COUNT(*) as active_24h FROM bot_users WHERE last_seen >= datetime('now', '-1 day')") as cur:
            row_24h = await cur.fetchone()
            active_24h = row_24h["active_24h"] if row_24h else 0

        async with db.execute("SELECT COUNT(*) as active_7d FROM bot_users WHERE last_seen >= datetime('now', '-7 days')") as cur:
            row_7d = await cur.fetchone()
            active_7d = row_7d["active_7d"] if row_7d else 0

        async with db.execute("SELECT COUNT(*) as active_30d FROM bot_users WHERE last_seen >= datetime('now', '-30 days')") as cur:
            row_30d = await cur.fetchone()
            active_30d = row_30d["active_30d"] if row_30d else 0

        async with db.execute("""
            SELECT user_id, username, first_name, first_seen, last_seen
            FROM bot_users
            WHERE last_seen >= datetime('now', '-1 day')
            ORDER BY last_seen DESC
            LIMIT 25
        """) as cur:
            active_rows = await cur.fetchall()
            active_today_users = [dict(r) for r in active_rows]

        return {
            "total_users": total_users,
            "active_24h": active_24h,
            "active_7d": active_7d,
            "active_30d": active_30d,
            "active_today_users": active_today_users
        }

async def follow_channel(user_id: int, channel_target: str, db_path: str = DB_PATH) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Subscribes a user to direct DM milestone alerts for a specific channel by username, title, or channel_id.
    """
    target_clean = channel_target.strip().lstrip("@")
    raw_digits = "".join(filter(str.isdigit, target_clean))

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        
        # 1. Search in channels table
        query = """
            SELECT * FROM channels 
            WHERE LOWER(username) = LOWER(?) 
               OR LOWER(title) LIKE '%' || LOWER(?) || '%'
        """
        params = [target_clean, target_clean]
        if raw_digits:
            query += " OR CAST(channel_id AS TEXT) LIKE '%' || ? || '%'"
            params.append(raw_digits)

        async with db.execute(query, params) as cur:
            ch = await cur.fetchone()

        # 2. Search fallback in calls table if legacy channel
        if not ch:
            query_calls = """
                SELECT channel_id, channel_title as title, channel_username as username 
                FROM calls 
                WHERE LOWER(channel_username) = LOWER(?) 
                   OR LOWER(channel_title) LIKE '%' || LOWER(?) || '%'
            """
            params_calls = [target_clean, target_clean]
            if raw_digits:
                query_calls += " OR CAST(channel_id AS TEXT) LIKE '%' || ? || '%'"
                params_calls.append(raw_digits)
            query_calls += " LIMIT 1"
            
            async with db.execute(query_calls, params_calls) as cur:
                ch = await cur.fetchone()

        if not ch:
            return False, f"Channel '{channel_target}' not found in KOLInsights tracking database.", None

        channel_dict = dict(ch)
        channel_id = channel_dict["channel_id"]

        try:
            await db.execute(
                "INSERT INTO user_follows (user_id, channel_id) VALUES (?, ?)",
                (user_id, channel_id)
            )
            await db.commit()
            return True, f"Successfully followed @{channel_dict.get('username') or channel_dict.get('title')}!", channel_dict
        except Exception:
            return True, f"You are already following @{channel_dict.get('username') or channel_dict.get('title')}!", channel_dict

async def unfollow_channel(user_id: int, channel_target: str, db_path: str = DB_PATH) -> Tuple[bool, str]:
    """Unsubscribes a user from DM milestone alerts for a channel."""
    target_clean = channel_target.strip().lstrip("@")
    raw_digits = "".join(filter(str.isdigit, target_clean))

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        query = """
            SELECT channel_id, title, username FROM channels 
            WHERE LOWER(username) = LOWER(?) OR LOWER(title) LIKE '%' || LOWER(?) || '%'
        """
        params = [target_clean, target_clean]
        if raw_digits:
            query += " OR CAST(channel_id AS TEXT) LIKE '%' || ? || '%'"
            params.append(raw_digits)

        async with db.execute(query, params) as cur:
            ch = await cur.fetchone()

        if not ch:
            async with db.execute("SELECT DISTINCT channel_id, channel_title as title, channel_username as username FROM calls WHERE LOWER(channel_username) = LOWER(?) OR CAST(channel_id AS TEXT) LIKE '%' || ? || '%' LIMIT 1", (target_clean, raw_digits if raw_digits else target_clean)) as cur:
                ch = await cur.fetchone()

        if not ch:
            return False, f"Channel '{channel_target}' not found."

        ch_dict = dict(ch) if ch else {}
        ch_id = str(ch_dict.get("channel_id") or "")
        ch_digits = "".join(filter(str.isdigit, ch_id))
        ch_title = ch_dict.get("username") or ch_dict.get("title") or ch_id

        await db.execute("""
            DELETE FROM user_follows 
            WHERE user_id = ? AND (
                CAST(channel_id AS TEXT) = ? 
                OR (length(?) > 0 AND CAST(channel_id AS TEXT) LIKE '%' || ? || '%')
            )
        """, (user_id, ch_id, ch_digits, ch_digits))
        await db.commit()
        return True, f"Successfully unfollowed @{ch_title}."

async def get_user_followed_channels(user_id: int, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Returns list of channels followed by user_id."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT uf.channel_id, 
                   COALESCE(c.title, ca.channel_title, 'Channel') as title, 
                   COALESCE(c.username, ca.channel_username, '') as username,
                   uf.created_at
            FROM user_follows uf
            LEFT JOIN channels c ON uf.channel_id = c.channel_id
            LEFT JOIN (SELECT channel_id, channel_title, channel_username FROM calls GROUP BY channel_id) ca ON uf.channel_id = ca.channel_id
            WHERE uf.user_id = ?
            ORDER BY uf.created_at DESC
        """, (user_id,)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

async def get_channel_followers(channel_target: Any, db_path: str = DB_PATH) -> List[int]:
    """Returns list of user_ids following channel_target (supports numeric channel_id or @username)."""
    if not channel_target:
        return []
    clean_target = str(channel_target).strip().lstrip("@")
    is_numeric_id = clean_target.lstrip("-").isdigit()
    raw_digits = "".join(filter(str.isdigit, clean_target)) if is_numeric_id else ""

    followers = set()
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row

        ch_ids = set()
        if raw_digits and len(raw_digits) >= 5:
            ch_ids.add(raw_digits)
            if not str(channel_target).startswith("-100"):
                ch_ids.add(f"-100{raw_digits}")

        if clean_target:
            async with db.execute("""
                SELECT channel_id FROM channels 
                WHERE LOWER(username) = LOWER(?) OR (length(?) >= 5 AND CAST(channel_id AS TEXT) LIKE '%' || ? || '%')
            """, (clean_target, raw_digits, raw_digits)) as cur:
                for r in await cur.fetchall():
                    if r["channel_id"]:
                        ch_ids.add(str(r["channel_id"]))

            async with db.execute("""
                SELECT DISTINCT channel_id FROM calls 
                WHERE LOWER(channel_username) = LOWER(?) OR (length(?) >= 5 AND CAST(channel_id AS TEXT) LIKE '%' || ? || '%')
            """, (clean_target, raw_digits, raw_digits)) as cur:
                for r in await cur.fetchall():
                    if r["channel_id"]:
                        ch_ids.add(str(r["channel_id"]))

        for cid in ch_ids:
            c_digits = "".join(filter(str.isdigit, cid))
            async with db.execute("""
                SELECT DISTINCT user_id FROM user_follows 
                WHERE CAST(channel_id AS TEXT) = ? 
                   OR (length(?) >= 5 AND CAST(channel_id AS TEXT) LIKE '%' || ? || '%')
            """, (cid, c_digits, c_digits)) as cur:
                for r in await cur.fetchall():
                    followers.add(r["user_id"])

    return list(followers)
