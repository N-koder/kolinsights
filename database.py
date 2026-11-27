"""
Database Module for KOLInsights Bot
Fully powered by MongoDB Atlas with asynchronous Motor client.
Provides 100% backward compatibility for all callers across the application.
"""

import os
import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple, Union

try:
    import aiosqlite
except ImportError:
    aiosqlite = None

import motor.motor_asyncio
from pymongo import ReturnDocument, UpdateOne

from config import DB_PATH, MONGO_URI, MONGO_DB_NAME

logger = logging.getLogger(__name__)

_mongo_client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
_mongo_db = None

def get_mongo_client() -> Optional[motor.motor_asyncio.AsyncIOMotorClient]:
    global _mongo_client
    if _mongo_client is None:
        uri = (MONGO_URI or "").strip(' "\'')
        if not uri:
            return None
        client_kwargs = {
            "serverSelectionTimeoutMS": 5000,
            "connectTimeoutMS": 5000,
            "socketTimeoutMS": 10000,
            "maxPoolSize": 50,
            "minPoolSize": 5
        }
        try:
            import certifi
            client_kwargs["tlsCAFile"] = certifi.where()
        except Exception:
            pass
        _mongo_client = motor.motor_asyncio.AsyncIOMotorClient(
            uri,
            **client_kwargs
        )
    return _mongo_client

def get_mongo_db():
    global _mongo_db
    if _mongo_db is None:
        client = get_mongo_client()
        if client is not None:
            _mongo_db = client[MONGO_DB_NAME]
    return _mongo_db

def sanitize_doc(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    d = dict(doc)
    if "_id" in d:
        del d["_id"]
    return d

def sanitize_docs(docs: List[dict]) -> List[dict]:
    res = []
    for doc in docs:
        d = dict(doc)
        if "_id" in d:
            del d["_id"]
        res.append(d)
    return res

async def get_next_sequence(name: str) -> int:
    """Atomic auto-incrementing integer sequence counter for MongoDB."""
    db = get_mongo_db()
    res = await db["counters"].find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return int(res["seq"])

async def init_db(db_path: str = DB_PATH):
    """Initializes MongoDB database connection, indexes, and sequence counters."""
    db = get_mongo_db()
    if db is None:
        logger.warning("MONGO_URI not configured, skipping MongoDB initialization.")
        return

    try:
        # Create indexes safely (wrap individually so existing index spec differences don't abort setup)
        index_specs = [
            ("calls", "id", {}),
            ("calls", "channel_id", {}),
            ("calls", "token_address", {}),
            ("calls", "created_at", {}),
            ("calls", "status", {}),
            ("calls", [("channel_id", 1), ("token_address", 1), ("created_at", -1)], {}),
            ("channels", "channel_id", {}),
            ("channels", "username", {}),
            ("alerts", [("call_id", 1), ("multiplier", 1)], {}),
            ("custom_media", "media_key", {}),
            ("milestone_media", "milestone", {}),
            ("deposit_wallets", "wallet_address", {}),
            ("bot_config", "key", {}),
            ("channel_owners", [("channel_identifier", 1), ("owner_user_id", 1)], {}),
            ("user_follows", [("channel_identifier", 1), ("user_id", 1)], {})
        ]
        for coll_name, keys, kwargs in index_specs:
            try:
                await db[coll_name].create_index(keys, **kwargs)
            except Exception:
                pass

        # Ensure counters exist and are >= existing max IDs
        max_call = await db["calls"].find_one(sort=[("id", -1)])
        max_call_id = int(max_call["id"]) if max_call and "id" in max_call else 0
        existing_call_c = await db["counters"].find_one({"_id": "call_id"})
        if not existing_call_c:
            await db["counters"].insert_one({"_id": "call_id", "seq": max_call_id})
        elif int(existing_call_c.get("seq", 0)) < max_call_id:
            await db["counters"].update_one({"_id": "call_id"}, {"$set": {"seq": max_call_id}})

        max_wallet = await db["deposit_wallets"].find_one(sort=[("id", -1)])
        max_wallet_id = int(max_wallet["id"]) if max_wallet and "id" in max_wallet else 0
        existing_w_c = await db["counters"].find_one({"_id": "wallet_id"})
        if not existing_w_c:
            await db["counters"].insert_one({"_id": "wallet_id", "seq": max_wallet_id})
        elif int(existing_w_c.get("seq", 0)) < max_wallet_id:
            await db["counters"].update_one({"_id": "wallet_id"}, {"$set": {"seq": max_wallet_id}})

        max_payment = await db["payments"].find_one(sort=[("id", -1)])
        max_payment_id = int(max_payment["id"]) if max_payment and "id" in max_payment else 0
        existing_p_c = await db["counters"].find_one({"_id": "payment_id"})
        if not existing_p_c:
            await db["counters"].insert_one({"_id": "payment_id", "seq": max_payment_id})
        elif int(existing_p_c.get("seq", 0)) < max_payment_id:
            await db["counters"].update_one({"_id": "payment_id"}, {"$set": {"seq": max_payment_id}})

        logger.info("✅ [MONGODB ATLAS] Database initialized, indexes ensured, and sequence counters synchronized.")
    except Exception as e:
        logger.error(f"❌ [MONGODB ATLAS] Error initializing database: {e}", exc_info=True)

def get_db(db_path: str = DB_PATH):
    """Compatibility context manager for legacy callers."""
    class DummyContext:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
    return DummyContext()


# ==============================================================================
# DEPOSIT WALLETS & PAYMENTS
# ==============================================================================

async def get_all_deposit_wallets(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    db = get_mongo_db()
    cursor = db["deposit_wallets"].find({}, {"_id": 0}).sort("id", 1)
    return await cursor.to_list(length=1000)

async def get_active_deposit_wallet(db_path: str = DB_PATH) -> Optional[str]:
    db = get_mongo_db()
    doc = await db["deposit_wallets"].find_one({"is_active": 1}, {"_id": 0})
    return doc["wallet_address"] if doc else None

async def add_deposit_wallet(wallet_address: str, label: str = "Deposit Wallet", set_active: bool = False, db_path: str = DB_PATH) -> int:
    db = get_mongo_db()
    clean_addr = wallet_address.strip()
    if set_active:
        await db["deposit_wallets"].update_many({}, {"$set": {"is_active": 0}})

    doc = await db["deposit_wallets"].find_one({"wallet_address": {"$regex": f"^{re.escape(clean_addr)}$", "$options": "i"}})
    if doc:
        w_id = doc["id"]
        update_data = {"label": label}
        if set_active:
            update_data["is_active"] = 1
        await db["deposit_wallets"].update_one({"_id": doc["_id"]}, {"$set": update_data})
        return w_id

    w_id = await get_next_sequence("wallet_id")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    await db["deposit_wallets"].insert_one({
        "id": w_id,
        "wallet_address": clean_addr,
        "label": label,
        "is_active": 1 if set_active else 0,
        "created_at": now_str
    })
    return w_id

async def set_active_deposit_wallet(wallet_id: int, db_path: str = DB_PATH) -> bool:
    db = get_mongo_db()
    await db["deposit_wallets"].update_many({}, {"$set": {"is_active": 0}})
    res = await db["deposit_wallets"].update_one({"id": wallet_id}, {"$set": {"is_active": 1}})
    return res.modified_count > 0 or res.matched_count > 0

async def is_tx_hash_used(tx_hash: str, db_path: str = DB_PATH) -> bool:
    db = get_mongo_db()
    clean_hash = tx_hash.strip().lower()
    doc = await db["payments"].find_one({"tx_hash": {"$regex": f"^{re.escape(clean_hash)}$", "$options": "i"}})
    return doc is not None

async def record_payment_submission(user_id: int, channel_id: Optional[int], tx_hash: str, amount_eth: float = 0.01, db_path: str = DB_PATH) -> int:
    db = get_mongo_db()
    pid = await get_next_sequence("payment_id")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    await db["payments"].insert_one({
        "id": pid,
        "user_id": user_id,
        "channel_id": channel_id,
        "tx_hash": tx_hash.strip(),
        "amount_eth": amount_eth,
        "status": "pending",
        "created_at": now_str
    })
    return pid

async def approve_payment(payment_id: int, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    db = get_mongo_db()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    doc = await db["payments"].find_one_and_update(
        {"id": payment_id},
        {"$set": {"status": "approved", "approved_at": now_str}},
        return_document=ReturnDocument.AFTER
    )
    if not doc:
        return None
    if doc.get("channel_id"):
        await set_channel_subscription(str(doc["channel_id"]), "PAID", days=30)
    return sanitize_doc(doc)


# ==============================================================================
# CHANNELS & SUBSCRIPTIONS
# ==============================================================================

async def add_channel(channel_id: int, title: str, username: Optional[str] = None, sub_type: str = "TRIAL", days: int = 7, db_path: str = DB_PATH):
    """Adds a new channel with 7-day default trial or specified days subscription."""
    db = get_mongo_db()
    clean_user = (username or "").lstrip("@").strip().lower()
    sub_type_upper = (sub_type or "TRIAL").upper().strip()

    if sub_type_upper == "VIP":
        sub_end = None
    else:
        sub_end = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    cid_str = str(channel_id).strip()
    raw_digits = cid_str.replace("-100", "").replace("-", "")

    or_conds = []
    if clean_user:
        or_conds.append({"username": {"$regex": f"^{re.escape(clean_user)}$", "$options": "i"}})
    or_conds.append({"channel_id": channel_id})
    if raw_digits.isdigit():
        or_conds.extend([
            {"channel_id": int(raw_digits)},
            {"channel_id": int(f"-100{raw_digits}")},
            {"channel_id": -int(raw_digits)}
        ])

    existing = await db["channels"].find_one({"$or": or_conds})
    if existing:
        await db["channels"].update_one(
            {"_id": existing["_id"]},
            {"$set": {
                "channel_id": channel_id,
                "title": title,
                "username": clean_user,
                "sub_type": sub_type_upper,
                "sub_end": sub_end
            }}
        )
    else:
        await db["channels"].insert_one({
            "channel_id": channel_id,
            "title": title,
            "username": clean_user,
            "sub_type": sub_type_upper,
            "sub_end": sub_end,
            "reminded_7d": 0,
            "reminded_3d": 0
        })

async def set_channel_subscription(identifier: str, sub_type: str, days: int = 30, db_path: str = DB_PATH) -> bool:
    """Updates a channel's subscription status (VIP, TRIAL, PAID, EXPIRED) by Channel ID, Username, or Title."""
    db = get_mongo_db()
    clean = str(identifier).strip()
    clean_user = clean.lstrip("@").lower()
    sub_type_upper = sub_type.upper().strip()

    if sub_type_upper == "VIP":
        sub_end = None
    elif sub_type_upper == "EXPIRED":
        sub_end = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        sub_end = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    or_conds = [
        {"username": {"$regex": f"^{re.escape(clean_user)}$", "$options": "i"}},
        {"title": {"$regex": re.escape(clean_user), "$options": "i"}}
    ]

    clean_digits = clean.replace("-100", "").replace("-", "")
    if clean_digits.isdigit():
        raw_val = int(clean_digits)
        or_conds.extend([
            {"channel_id": raw_val},
            {"channel_id": -raw_val},
            {"channel_id": int(f"-100{raw_val}")}
        ])

    res = await db["channels"].update_many(
        {"$or": or_conds},
        {"$set": {
            "sub_type": sub_type_upper,
            "sub_end": sub_end,
            "reminded_7d": 0,
            "reminded_3d": 0
        }}
    )
    return res.modified_count > 0 or res.matched_count > 0

async def is_channel_authorized(channel_id: Optional[int] = None, username: Optional[str] = None, db_path: str = DB_PATH) -> bool:
    """Checks if a channel is whitelisted AND has an active subscription (or VIP status)."""
    db = get_mongo_db()
    or_conds = []
    if channel_id:
        cid_str = str(channel_id).strip()
        raw_digits = cid_str.replace("-100", "").replace("-", "")
        or_conds.append({"channel_id": channel_id})
        if raw_digits.isdigit():
            or_conds.extend([
                {"channel_id": int(raw_digits)},
                {"channel_id": int(f"-100{raw_digits}")},
                {"channel_id": -int(raw_digits)}
            ])
    if username:
        clean_user = username.lstrip("@").strip().lower()
        if clean_user:
            or_conds.append({"username": {"$regex": f"^{re.escape(clean_user)}$", "$options": "i"}})

    if not or_conds:
        return False

    row = await db["channels"].find_one({"$or": or_conds})
    if not row:
        return False

    sub_type = (row.get("sub_type") or "TRIAL").upper()
    if sub_type in ("VIP", "LIFETIME"):
        return True
    if sub_type == "EXPIRED":
        return False

    sub_end = row.get("sub_end")
    if not sub_end:
        return True

    try:
        end_dt = datetime.strptime(str(sub_end)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < end_dt
    except Exception:
        return True

async def remove_channel(identifier: str, db_path: str = DB_PATH) -> bool:
    db = get_mongo_db()
    clean = str(identifier).strip()
    clean_user = clean.lstrip("@").lower()
    or_conds = [
        {"username": {"$regex": f"^{re.escape(clean_user)}$", "$options": "i"}},
        {"title": {"$regex": re.escape(clean_user), "$options": "i"}}
    ]
    clean_digits = clean.replace("-100", "").replace("-", "")
    if clean_digits.isdigit():
        raw_val = int(clean_digits)
        or_conds.extend([
            {"channel_id": raw_val},
            {"channel_id": -raw_val},
            {"channel_id": int(f"-100{raw_val}")}
        ])

    res = await db["channels"].delete_many({"$or": or_conds})
    return res.deleted_count > 0

async def get_all_channels(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    db = get_mongo_db()
    cursor = db["channels"].find({}, {"_id": 0}).sort("channel_id", 1)
    return await cursor.to_list(length=1000)

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

        if target_tier == "EXPIRED" and is_expired:
            filtered.append(c)
        elif not is_expired:
            if target_tier == "VIP" and stype in ("VIP", "LIFETIME"):
                filtered.append(c)
            elif target_tier == "TRIAL" and stype == "TRIAL":
                filtered.append(c)
            elif target_tier == "PAID" and stype in ("PAID", "ACTIVE"):
                filtered.append(c)
            elif target_tier == stype:
                filtered.append(c)

    return filtered

async def bulk_set_channel_status(targets: List[str], sub_type: str, days: int = 30, db_path: str = DB_PATH) -> List[str]:
    updated = []
    for t in targets:
        if await set_channel_subscription(t, sub_type, days, db_path=db_path):
            updated.append(t)
    return updated

async def update_channel_reminded(channel_id: int, field: str, value: int = 1, db_path: str = DB_PATH) -> bool:
    db = get_mongo_db()
    res = await db["channels"].update_one(
        {"channel_id": channel_id},
        {"$set": {field: value}}
    )
    return res.modified_count > 0

async def set_channel_sub_type(channel_id: int, sub_type: str, db_path: str = DB_PATH) -> bool:
    db = get_mongo_db()
    res = await db["channels"].update_one(
        {"channel_id": channel_id},
        {"$set": {"sub_type": sub_type}}
    )
    return res.modified_count > 0


# ==============================================================================
# CALLS & ALERTS ENGINE
# ==============================================================================

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
    db = get_mongo_db()
    clean_addr = token_address.strip().lower()
    clean_user = (channel_username or "").lstrip("@").strip().lower()

    or_conds = [{"channel_id": channel_id}]
    if clean_user:
        or_conds.append({"channel_username": {"$regex": f"^{re.escape(clean_user)}$", "$options": "i"}})

    existing = await db["calls"].find_one(
        {
            "$and": [
                {"$or": or_conds},
                {"token_address": {"$regex": f"^{re.escape(clean_addr)}$", "$options": "i"}}
            ]
        },
        sort=[("created_at", -1)]
    )

    is_dip_call = 0
    prev_entry_mc = 0.0

    if existing:
        existing_id = existing.get("id")
        prev_entry_mc = float(existing.get("entry_mc") or 0.0)
        created_at_str = str(existing.get("created_at") or "")

        # Cooldown check: If called by same channel within the last 30 minutes, ignore duplicate!
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
            logger.info(f"📉 [DIP RE-CALL DETECTED] {token_symbol} in '{channel_title}'! Entry MC: ${entry_mc:,.0f} vs Prev: ${prev_entry_mc:,.0f}")

    call_id = await get_next_sequence("call_id")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    doc = {
        "id": call_id,
        "channel_id": channel_id,
        "channel_title": channel_title,
        "channel_username": clean_user,
        "token_address": clean_addr,
        "chain": chain,
        "token_symbol": token_symbol,
        "token_name": token_name,
        "entry_mc": entry_mc,
        "current_mc": entry_mc,
        "ath_mc": entry_mc,
        "highest_multiplier": 1.0,
        "last_alert_multiplier": 1.0,
        "call_msg_id": call_msg_id,
        "call_msg_link": call_msg_link,
        "is_dip_call": is_dip_call,
        "prev_entry_mc": prev_entry_mc,
        "status": "active",
        "created_at": now_str,
        "updated_at": now_str
    }
    await db["calls"].insert_one(doc)
    return call_id, bool(is_dip_call), prev_entry_mc

async def get_active_calls(days: int = 14, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    db = get_mongo_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cursor = db["calls"].find(
        {
            "created_at": {"$gte": cutoff},
            "status": {"$nin": ["rugged", "dead"]}
        },
        {"_id": 0}
    ).sort("created_at", -1)
    return await cursor.to_list(length=5000)

async def get_hot_active_calls(hours: int = 2, limit: int = 50, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Lightweight query fetching ONLY hot live calls (<= 2h old, max 50 rows)."""
    db = get_mongo_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    cursor = db["calls"].find(
        {
            "created_at": {"$gte": cutoff},
            "status": {"$nin": ["rugged", "dead"]}
        },
        {"_id": 0}
    ).sort("created_at", -1).limit(limit)
    return await cursor.to_list(length=limit)

async def get_archival_active_calls_slice(hours_cutoff: int = 2, days: int = 14, offset: int = 0, limit: int = 30, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Lightweight paginated slice query for older calls directly in MongoDB."""
    db = get_mongo_db()
    days_cut = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    hours_cut = (datetime.now(timezone.utc) - timedelta(hours=hours_cutoff)).strftime("%Y-%m-%d %H:%M:%S")
    cursor = db["calls"].find(
        {
            "created_at": {"$gte": days_cut, "$lt": hours_cut},
            "status": {"$nin": ["rugged", "dead"]}
        },
        {"_id": 0}
    ).sort("created_at", -1).skip(offset).limit(limit)
    return await cursor.to_list(length=limit)

async def get_archival_active_calls_count(hours_cutoff: int = 2, days: int = 14, db_path: str = DB_PATH) -> int:
    """Returns exact count of archival calls directly without loading rows into RAM."""
    db = get_mongo_db()
    days_cut = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    hours_cut = (datetime.now(timezone.utc) - timedelta(hours=hours_cutoff)).strftime("%Y-%m-%d %H:%M:%S")
    return await db["calls"].count_documents({
        "created_at": {"$gte": days_cut, "$lt": hours_cut},
        "status": {"$nin": ["rugged", "dead"]}
    })

async def get_active_calls_count(days: int = 14, db_path: str = DB_PATH) -> int:
    """Returns exact count of all active calls directly without loading rows into RAM."""
    db = get_mongo_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    return await db["calls"].count_documents({
        "created_at": {"$gte": cutoff},
        "status": {"$nin": ["rugged", "dead"]}
    })

async def get_recent_call_keys(days: int = 7, db_path: str = DB_PATH) -> List[Tuple[Optional[str], Optional[int], str]]:
    """Fetches ONLY channel_username, channel_id, token_address for startup deduplication without loading full rows."""
    db = get_mongo_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    cursor = db["calls"].find(
        {"created_at": {"$gte": cutoff}},
        {"channel_username": 1, "channel_id": 1, "token_address": 1, "_id": 0}
    )
    docs = await cursor.to_list(length=10000)
    return [(d.get("channel_username"), d.get("channel_id"), d.get("token_address")) for d in docs]

async def update_call_price(
    call_id: int,
    current_mc: float,
    ath_mc: float,
    highest_multiplier: float,
    last_alert_multiplier: float,
    db_path: str = DB_PATH
):
    db = get_mongo_db()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    await db["calls"].update_one(
        {"id": call_id},
        {"$set": {
            "current_mc": current_mc,
            "ath_mc": ath_mc,
            "highest_multiplier": highest_multiplier,
            "last_alert_multiplier": last_alert_multiplier,
            "updated_at": now_str
        }}
    )

async def mark_call_status(call_id: int, status: str, db_path: str = DB_PATH):
    db = get_mongo_db()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    await db["calls"].update_one(
        {"id": call_id},
        {"$set": {"status": status, "updated_at": now_str}}
    )

async def update_call_metadata(call_id: int, token_symbol: str, token_name: str, chain: str, db_path: str = DB_PATH):
    db = get_mongo_db()
    await db["calls"].update_one(
        {"id": call_id},
        {"$set": {
            "token_symbol": token_symbol,
            "token_name": token_name,
            "chain": chain
        }}
    )

async def update_calls_price_batch(updates: list, db_path: str = DB_PATH):
    """Updates multiple calls' price and multipliers in 1 single bulk write transaction."""
    if not updates:
        return
    db = get_mongo_db()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    ops = [
        UpdateOne(
            {"id": u["call_id"]},
            {"$set": {
                "current_mc": u["current_mc"],
                "ath_mc": u["ath_mc"],
                "highest_multiplier": u["highest_multiplier"],
                "last_alert_multiplier": u["last_alert_multiplier"],
                "updated_at": now_str
            }}
        )
        for u in updates if u.get("call_id")
    ]
    if ops:
        await db["calls"].bulk_write(ops, ordered=False)

async def has_alert_been_sent(call_id: int, multiplier: float, db_path: str = DB_PATH) -> bool:
    db = get_mongo_db()
    doc = await db["alerts"].find_one({
        "call_id": call_id,
        "multiplier": {"$gte": multiplier - 0.01, "$lte": multiplier + 0.01}
    })
    return doc is not None

async def has_alert_been_sent_by_token(token_address: str, caller_key: str, multiplier: float, db_path: str = DB_PATH) -> bool:
    db = get_mongo_db()
    clean_addr = (token_address or "").strip().lower()
    clean_caller = (caller_key or "").strip().lower()

    or_conds = [
        {"channel_username": {"$regex": f"^{re.escape(clean_caller)}$", "$options": "i"}}
    ]
    if clean_caller.lstrip("-").isdigit():
        or_conds.append({"channel_id": int(clean_caller)})

    call_cursor = db["calls"].find(
        {
            "token_address": {"$regex": f"^{re.escape(clean_addr)}$", "$options": "i"},
            "$or": or_conds
        },
        {"id": 1, "_id": 0}
    )
    call_ids = [c["id"] async for c in call_cursor]
    if not call_ids:
        return False

    alert = await db["alerts"].find_one({
        "call_id": {"$in": call_ids},
        "multiplier": {"$gte": multiplier - 0.01, "$lte": multiplier + 0.01}
    })
    return alert is not None

async def record_alert(call_id: int, multiplier: float, alert_msg_id: Optional[int] = None, db_path: str = DB_PATH):
    db = get_mongo_db()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    await db["alerts"].update_one(
        {"call_id": call_id, "multiplier": multiplier},
        {"$setOnInsert": {
            "call_id": call_id,
            "multiplier": multiplier,
            "alert_msg_id": alert_msg_id,
            "created_at": now_str
        }},
        upsert=True
    )

async def get_channel_stats(identifier: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    """Calculates full caller stats matching AlphaRadar checkstats format."""
    db = get_mongo_db()
    clean = identifier.strip()
    clean_no_at = clean.lstrip("@").lower()

    or_conds = [
        {"channel_username": {"$regex": f"^{re.escape(clean_no_at)}$", "$options": "i"}},
        {"channel_title": {"$regex": f"^{re.escape(clean)}$", "$options": "i"}},
        {"channel_title": {"$regex": re.escape(clean_no_at), "$options": "i"}},
        {"token_address": {"$regex": f"^{re.escape(clean.lower())}$", "$options": "i"}}
    ]
    if clean.lstrip("-").isdigit():
        or_conds.append({"channel_id": int(clean)})

    cursor = db["calls"].find({"$or": or_conds}, {"_id": 0}).sort("created_at", -1)
    calls = await cursor.to_list(length=5000)

    if not calls:
        return None

    total_calls = len(calls)
    achievement_calls_2x = [c for c in calls if c.get("highest_multiplier", 1.0) >= 2.0]
    achievement_calls_5x = [c for c in calls if c.get("highest_multiplier", 1.0) >= 5.0]

    consistency = (len(achievement_calls_2x) / total_calls * 100) if total_calls > 0 else 0
    avg_entry_mc = sum(c.get("entry_mc", 0.0) for c in calls) / total_calls if total_calls > 0 else 0
    avg_x = sum(c.get("highest_multiplier", 1.0) for c in calls) / total_calls if total_calls > 0 else 1.0

    best_call = max(calls, key=lambda c: c.get("highest_multiplier", 1.0))
    last_call = calls[0]
    last_5 = calls[:5]
    top_3 = sorted(calls, key=lambda c: c.get("highest_multiplier", 1.0), reverse=True)[:3]

    sim_ath_val = sum(100 * c.get("highest_multiplier", 1.0) for c in last_5)
    sim_current_val = sum(100 * (c.get("current_mc", 1) / c["entry_mc"] if c.get("entry_mc", 0) > 0 else 1) for c in last_5)

    return {
        "channel_title": calls[0].get("channel_title"),
        "channel_username": calls[0].get("channel_username"),
        "total_calls": total_calls,
        "consistency": round(consistency, 1),
        "alpha_calls": len([c for c in calls if c.get("highest_multiplier", 1.0) >= 10.0]),
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
    db = get_mongo_db()
    cursor = db["calls"].find(
        {"token_address": {"$regex": f"^{re.escape(token_address.strip())}$", "$options": "i"}},
        {"_id": 0}
    ).sort("created_at", -1)
    return await cursor.to_list(length=1000)


# ==============================================================================
# CUSTOM & MILESTONE MEDIA
# ==============================================================================

async def set_custom_media(media_key: str, file_id: str, media_type: str, db_path: str = DB_PATH):
    db = get_mongo_db()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    await db["custom_media"].update_one(
        {"media_key": media_key.strip().lower()},
        {"$set": {
            "media_key": media_key.strip().lower(),
            "file_id": file_id.strip(),
            "media_type": media_type.strip(),
            "created_at": now_str
        }},
        upsert=True
    )

async def remove_custom_media(media_key: str, db_path: str = DB_PATH) -> bool:
    db = get_mongo_db()
    res = await db["custom_media"].delete_one({"media_key": media_key.strip().lower()})
    return res.deleted_count > 0

async def get_custom_media(media_key: str, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    db = get_mongo_db()
    doc = await db["custom_media"].find_one({"media_key": media_key.strip().lower()}, {"_id": 0})
    return doc

async def get_all_custom_media(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    db = get_mongo_db()
    cursor = db["custom_media"].find({}, {"_id": 0}).sort("media_key", 1)
    return await cursor.to_list(length=100)

async def set_milestone_media(milestone: float, file_id: str, media_type: str, db_path: str = DB_PATH):
    db = get_mongo_db()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    await db["milestone_media"].update_one(
        {"milestone": float(milestone)},
        {"$set": {
            "milestone": float(milestone),
            "file_id": file_id.strip(),
            "media_type": media_type.strip(),
            "created_at": now_str
        }},
        upsert=True
    )

async def remove_milestone_media(milestone: float, db_path: str = DB_PATH) -> bool:
    db = get_mongo_db()
    res = await db["milestone_media"].delete_one({"milestone": float(milestone)})
    return res.deleted_count > 0

async def get_milestone_media(milestone: float, db_path: str = DB_PATH) -> Optional[Dict[str, Any]]:
    db = get_mongo_db()
    doc = await db["milestone_media"].find_one({"milestone": float(milestone)}, {"_id": 0})
    return doc

async def get_all_milestone_media(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    db = get_mongo_db()
    cursor = db["milestone_media"].find({}, {"_id": 0}).sort("milestone", 1)
    return await cursor.to_list(length=100)


# ==============================================================================
# BOT CONFIGURATION & CONSISTENCY
# ==============================================================================

async def set_bot_config(key: str, value: str, db_path: str = DB_PATH):
    db = get_mongo_db()
    k = key.strip()
    await db["bot_config"].update_one(
        {"$or": [{"key": k}, {"config_key": k}]},
        {"$set": {"key": k, "value": str(value), "config_key": k, "config_value": str(value)}},
        upsert=True
    )

async def get_bot_config(key: str, db_path: str = DB_PATH) -> Optional[str]:
    db = get_mongo_db()
    k = key.strip()
    doc = await db["bot_config"].find_one({
        "$or": [
            {"key": k},
            {"config_key": k},
            {"key": {"$regex": f"^{re.escape(k)}$", "$options": "i"}},
            {"config_key": {"$regex": f"^{re.escape(k)}$", "$options": "i"}}
        ]
    }, {"_id": 0})
    if not doc:
        return None
    return doc.get("value") or doc.get("config_value")

async def get_caller_x2_calls_7d(channel_id: int, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    db = get_mongo_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    cursor = db["calls"].find(
        {
            "channel_id": channel_id,
            "highest_multiplier": {"$gte": 2.0},
            "created_at": {"$gte": cutoff}
        },
        {"_id": 0}
    ).sort("created_at", -1)
    return await cursor.to_list(length=1000)

async def record_consistency_alert(channel_id: int, milestone_count: int, db_path: str = DB_PATH) -> bool:
    db = get_mongo_db()
    doc = await db["consistency_alerts"].find_one({"channel_id": channel_id, "milestone_count": milestone_count})
    if doc:
        return False
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    await db["consistency_alerts"].insert_one({
        "channel_id": channel_id,
        "milestone_count": milestone_count,
        "created_at": now_str
    })
    return True


# ==============================================================================
# LEADERBOARDS & STATS
# ==============================================================================

async def get_recently_called_stats(db_path: str = DB_PATH) -> Dict[str, Any]:
    db = get_mongo_db()
    cutoff_1h = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

    async def get_grouped_calls(cutoff):
        pipeline = [
            {"$match": {"created_at": {"$gte": cutoff}}},
            {"$group": {
                "_id": "$token_address",
                "token_address": {"$first": "$token_address"},
                "token_symbol": {"$first": "$token_symbol"},
                "token_name": {"$first": "$token_name"},
                "call_count": {"$sum": 1},
                "latest_created_at": {"$max": "$created_at"}
            }},
            {"$sort": {"call_count": -1, "latest_created_at": -1}},
            {"$limit": 5}
        ]
        return sanitize_docs(await db["calls"].aggregate(pipeline).to_list(length=5))

    last_1h = await get_grouped_calls(cutoff_1h)
    last_24h = await get_grouped_calls(cutoff_24h)

    return {
        "last_1h": last_1h,
        "last_24h": last_24h
    }

async def get_daily_leaderboard_stats(db_path: str = DB_PATH) -> Dict[str, Any]:
    """Fetches comprehensive 24-hour daily leaderboard stats including KOL of the Day and top trending coins."""
    db = get_mongo_db()
    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

    top_coins_pipe = [
        {"$match": {"created_at": {"$gte": cutoff_24h}}},
        {"$group": {
            "_id": "$token_address",
            "token_address": {"$first": "$token_address"},
            "token_symbol": {"$first": "$token_symbol"},
            "token_name": {"$first": "$token_name"},
            "chain": {"$first": "$chain"},
            "call_count": {"$sum": 1},
            "max_mult": {"$max": "$highest_multiplier"},
            "latest_created_at": {"$max": "$created_at"}
        }},
        {"$sort": {"call_count": -1, "latest_created_at": -1}},
        {"$limit": 5}
    ]
    top_coins = sanitize_docs(await db["calls"].aggregate(top_coins_pipe).to_list(5))

    top_callers_pipe = [
        {"$match": {"created_at": {"$gte": cutoff_24h}}},
        {"$group": {
            "_id": "$channel_id",
            "channel_id": {"$first": "$channel_id"},
            "channel_title": {"$first": "$channel_title"},
            "channel_username": {"$first": "$channel_username"},
            "total_calls": {"$sum": 1},
            "win_calls": {"$sum": {"$cond": [{"$gte": ["$highest_multiplier", 2.0]}, 1, 0]}},
            "avg_mult": {"$avg": "$highest_multiplier"},
            "best_mult": {"$max": "$highest_multiplier"}
        }},
        {"$sort": {"total_calls": -1, "best_mult": -1}},
        {"$limit": 5}
    ]
    top_callers = sanitize_docs(await db["calls"].aggregate(top_callers_pipe).to_list(5))

    kol_of_day = top_callers[0] if top_callers else None
    top_call_info = None
    if kol_of_day:
        top_call_doc = await db["calls"].find_one(
            {"channel_id": kol_of_day["channel_id"], "created_at": {"$gte": cutoff_24h}},
            {"_id": 0, "token_name": 1, "token_symbol": 1, "token_address": 1, "entry_mc": 1, "ath_mc": 1, "highest_multiplier": 1, "chain": 1},
            sort=[("highest_multiplier", -1)]
        )
        top_call_info = sanitize_doc(top_call_doc)

    return {
        "top_coins": top_coins,
        "top_callers": top_callers,
        "kol_of_day": kol_of_day,
        "top_call_info": top_call_info
    }

async def get_weekly_leaderboard_stats(db_path: str = DB_PATH) -> Dict[str, Any]:
    """Fetches comprehensive 7-day weekly championship stats including KOL of the Week and top runners."""
    db = get_mongo_db()
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    weekly_callers_pipe = [
        {"$match": {"created_at": {"$gte": cutoff_7d}}},
        {"$group": {
            "_id": "$channel_id",
            "channel_id": {"$first": "$channel_id"},
            "channel_title": {"$first": "$channel_title"},
            "channel_username": {"$first": "$channel_username"},
            "total_calls": {"$sum": 1},
            "win_calls": {"$sum": {"$cond": [{"$gte": ["$highest_multiplier", 2.0]}, 1, 0]}},
            "avg_mult": {"$avg": "$highest_multiplier"},
            "best_mult": {"$max": "$highest_multiplier"}
        }},
        {"$match": {"total_calls": {"$gte": 1}}},
        {"$sort": {"total_calls": -1, "best_mult": -1}},
        {"$limit": 5}
    ]
    weekly_callers = sanitize_docs(await db["calls"].aggregate(weekly_callers_pipe).to_list(5))

    best_call_doc = await db["calls"].find_one(
        {"created_at": {"$gte": cutoff_7d}},
        {"_id": 0, "channel_title": 1, "channel_username": 1, "token_name": 1, "token_symbol": 1, "token_address": 1, "entry_mc": 1, "ath_mc": 1, "highest_multiplier": 1, "chain": 1},
        sort=[("highest_multiplier", -1)]
    )
    best_call_7d = sanitize_doc(best_call_doc)

    kol_of_week = weekly_callers[0] if weekly_callers else None
    champion_top_call = None
    if kol_of_week:
        champ_call_doc = await db["calls"].find_one(
            {"channel_id": kol_of_week["channel_id"], "created_at": {"$gte": cutoff_7d}},
            {"_id": 0, "token_name": 1, "token_symbol": 1, "token_address": 1, "entry_mc": 1, "ath_mc": 1, "highest_multiplier": 1, "chain": 1},
            sort=[("highest_multiplier", -1)]
        )
        champion_top_call = sanitize_doc(champ_call_doc)

    return {
        "weekly_callers": weekly_callers,
        "best_call_7d": best_call_7d,
        "kol_of_week": kol_of_week,
        "champion_top_call": champion_top_call
    }


# ==============================================================================
# CHANNEL OWNERSHIP & IDENTITY
# ==============================================================================

async def link_channel_owner(channel_target: str, owner_target: str, db_path: str = DB_PATH) -> bool:
    db = get_mongo_db()
    c_ident = str(channel_target).strip().lstrip("@").lower()
    o_ident = str(owner_target).strip().lstrip("@")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    await db["channel_owners"].update_one(
        {"channel_identifier": c_ident, "owner_user_id": o_ident},
        {"$set": {
            "channel_identifier": c_ident,
            "owner_user_id": o_ident,
            "created_at": now_str
        }},
        upsert=True
    )
    return True

async def unlink_channel_owner(channel_target: str, owner_target: Optional[str] = None, db_path: str = DB_PATH) -> bool:
    db = get_mongo_db()
    c_ident = str(channel_target).strip().lstrip("@").lower()
    query = {"channel_identifier": c_ident}
    if owner_target:
        query["owner_user_id"] = str(owner_target).strip().lstrip("@")

    res = await db["channel_owners"].delete_many(query)
    return res.deleted_count > 0

async def get_channel_owners(channel_identifier: Any, db_path: str = DB_PATH) -> List[str]:
    if not channel_identifier:
        return []
    db = get_mongo_db()
    c_str = str(channel_identifier).strip().lstrip("@").lower()
    raw_digits = "".join(filter(str.isdigit, c_str))

    cands = [c_str]
    if raw_digits:
        cands.extend([raw_digits, f"-100{raw_digits}", f"-{raw_digits}"])

    cursor = db["channel_owners"].find({"channel_identifier": {"$in": cands}}, {"owner_user_id": 1, "_id": 0})
    owners = []
    async for doc in cursor:
        owners.append(str(doc["owner_user_id"]))
    return list(dict.fromkeys(owners))

async def get_channel_owner(channel_identifier: Any, db_path: str = DB_PATH) -> Optional[str]:
    owners = await get_channel_owners(channel_identifier, db_path)
    return owners[0] if owners else None

async def get_all_linked_owners(db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    db = get_mongo_db()
    cursor = db["channel_owners"].find({}, {"_id": 0}).sort("channel_identifier", 1)
    return await cursor.to_list(length=1000)

async def update_channel_id_by_username(username: str, channel_id: int, db_path: str = DB_PATH) -> bool:
    db = get_mongo_db()
    clean_u = username.lstrip("@").strip().lower()
    res = await db["channels"].update_many(
        {"username": {"$regex": f"^{re.escape(clean_u)}$", "$options": "i"}},
        {"$set": {"channel_id": channel_id}}
    )
    return res.modified_count > 0

async def sync_channel_identity(old_channel_id: Optional[int], username: str, real_channel_id: int, real_title: str, db_path: str = DB_PATH) -> bool:
    db = get_mongo_db()
    clean_user = username.lstrip("@").strip().lower()
    res = await db["channels"].update_many(
        {"$or": [
            {"username": {"$regex": f"^{re.escape(clean_user)}$", "$options": "i"}},
            {"channel_id": old_channel_id if old_channel_id else real_channel_id}
        ]},
        {"$set": {
            "channel_id": real_channel_id,
            "title": real_title,
            "username": clean_user
        }}
    )
    return res.modified_count > 0


# ==============================================================================
# BOT VISITORS & FOLLOW SYSTEM
# ==============================================================================

async def record_bot_visitor(user_id: int, username: Optional[str] = None, first_name: Optional[str] = None, db_path: str = DB_PATH):
    db = get_mongo_db()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    clean_u = (username or "").lstrip("@").strip().lower()
    await db["bot_users"].update_one(
        {"user_id": user_id},
        {
            "$set": {
                "user_id": user_id,
                "username": clean_u,
                "first_name": first_name or "",
                "last_active": now_str
            },
            "$setOnInsert": {
                "created_at": now_str
            }
        },
        upsert=True
    )

async def get_visitor_stats(db_path: str = DB_PATH) -> Dict[str, Any]:
    db = get_mongo_db()
    total_users = await db["bot_users"].count_documents({})
    cutoff_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    active_24h = await db["bot_users"].count_documents({"last_active": {"$gte": cutoff_24h}})
    cursor = db["bot_users"].find({}, {"_id": 0}).sort("last_active", -1).limit(10)
    recent_users = await cursor.to_list(length=10)
    return {
        "total_users": total_users,
        "active_24h": active_24h,
        "recent_users": recent_users
    }

async def follow_channel(user_id: int, channel_target: str, db_path: str = DB_PATH) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    db = get_mongo_db()
    target_clean = channel_target.strip().lstrip("@")
    raw_digits = "".join(filter(str.isdigit, target_clean))

    or_conds = [
        {"username": {"$regex": f"^{re.escape(target_clean)}$", "$options": "i"}},
        {"title": {"$regex": re.escape(target_clean), "$options": "i"}}
    ]
    if raw_digits:
        or_conds.extend([
            {"channel_id": int(raw_digits)},
            {"channel_id": int(f"-100{raw_digits}")},
            {"channel_id": -int(raw_digits)}
        ])

    ch = await db["channels"].find_one({"$or": or_conds}, {"_id": 0})
    if not ch:
        call_doc = await db["calls"].find_one({"$or": or_conds}, {"_id": 0})
        if call_doc:
            ch = {
                "channel_id": call_doc.get("channel_id"),
                "title": call_doc.get("channel_title"),
                "username": call_doc.get("channel_username")
            }

    if not ch:
        return False, f"Channel '{channel_target}' not found.", None

    ch_id = ch.get("channel_id")
    ch_title = ch.get("username") or ch.get("title") or str(ch_id)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    await db["user_follows"].update_one(
        {"user_id": user_id, "channel_id": ch_id},
        {"$set": {
            "user_id": user_id,
            "channel_id": ch_id,
            "channel_identifier": str(ch_id),
            "created_at": now_str
        }},
        upsert=True
    )
    return True, f"Successfully following @{ch_title}!", ch

async def unfollow_channel(user_id: int, channel_target: str, db_path: str = DB_PATH) -> Tuple[bool, str]:
    db = get_mongo_db()
    target_clean = channel_target.strip().lstrip("@")
    raw_digits = "".join(filter(str.isdigit, target_clean))

    or_conds = [
        {"username": {"$regex": f"^{re.escape(target_clean)}$", "$options": "i"}},
        {"title": {"$regex": re.escape(target_clean), "$options": "i"}}
    ]
    if raw_digits:
        or_conds.extend([
            {"channel_id": int(raw_digits)},
            {"channel_id": int(f"-100{raw_digits}")},
            {"channel_id": -int(raw_digits)}
        ])

    ch = await db["channels"].find_one({"$or": or_conds}, {"_id": 0})
    if not ch:
        call_doc = await db["calls"].find_one({"$or": or_conds}, {"_id": 0})
        if call_doc:
            ch = {
                "channel_id": call_doc.get("channel_id"),
                "title": call_doc.get("channel_title"),
                "username": call_doc.get("channel_username")
            }

    if not ch:
        return False, f"Channel '{channel_target}' not found."

    ch_id = ch.get("channel_id")
    ch_title = ch.get("username") or ch.get("title") or str(ch_id)

    res = await db["user_follows"].delete_many({"user_id": user_id, "channel_id": ch_id})
    return True, f"Successfully unfollowed @{ch_title}."

async def get_user_followed_channels(user_id: int, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    db = get_mongo_db()
    cursor = db["user_follows"].find({"user_id": user_id}, {"_id": 0}).sort("created_at", -1)
    follows = await cursor.to_list(length=1000)
    if not follows:
        return []

    res = []
    for f in follows:
        cid = f.get("channel_id")
        ch = await db["channels"].find_one({"channel_id": cid}, {"_id": 0})
        if not ch:
            call_doc = await db["calls"].find_one({"channel_id": cid}, {"_id": 0})
            if call_doc:
                ch = {
                    "title": call_doc.get("channel_title"),
                    "username": call_doc.get("channel_username")
                }
        res.append({
            "channel_id": cid,
            "title": ch.get("title") if ch else "Channel",
            "username": ch.get("username") if ch else "",
            "created_at": f.get("created_at")
        })
    return res

async def get_channel_followers(channel_target: Any, db_path: str = DB_PATH) -> List[int]:
    if not channel_target:
        return []
    db = get_mongo_db()
    clean_target = str(channel_target).strip().lstrip("@")
    raw_digits = "".join(filter(str.isdigit, clean_target))

    ch_ids = set()
    if clean_target.lstrip("-").isdigit():
        val = int(clean_target)
        ch_ids.add(val)
    if raw_digits:
        ch_ids.add(int(raw_digits))
        ch_ids.add(int(f"-100{raw_digits}"))
        ch_ids.add(-int(raw_digits))

    # Search channels by username
    if clean_target:
        ch_docs = db["channels"].find({"username": {"$regex": f"^{re.escape(clean_target)}$", "$options": "i"}}, {"channel_id": 1, "_id": 0})
        async for c in ch_docs:
            if c.get("channel_id"):
                ch_ids.add(c["channel_id"])

        call_docs = db["calls"].find({"channel_username": {"$regex": f"^{re.escape(clean_target)}$", "$options": "i"}}, {"channel_id": 1, "_id": 0})
        async for c in call_docs:
            if c.get("channel_id"):
                ch_ids.add(c["channel_id"])

    cursor = db["user_follows"].find({"channel_id": {"$in": list(ch_ids)}}, {"user_id": 1, "_id": 0})
    followers = set()
    async for f in cursor:
        followers.add(int(f["user_id"]))

    return list(followers)
