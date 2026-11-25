import sqlite3
import pymongo

from config import DB_PATH, MONGO_URI, MONGO_DB_NAME

sqlite_path = DB_PATH
mongo_uri = MONGO_URI

s_conn = sqlite3.connect(sqlite_path)
s_conn.row_factory = sqlite3.Row
s_cur = s_conn.cursor()

m_client = pymongo.MongoClient(mongo_uri)
m_db = m_client["kolinsights"]

tables = [
    "channels", "calls", "alerts", "milestone_media", "custom_media", 
    "bot_config", "consistency_alerts", "payments", "deposit_wallets", 
    "bot_users", "user_follows", "channel_owners"
]

for t in tables:
    try:
        s_cur.execute(f"SELECT * FROM {t}")
        rows = [dict(r) for r in s_cur.fetchall()]
        if rows:
            coll = m_db[t]
            coll.delete_many({})
            coll.insert_many(rows)
            print(f"✅ Migrated {len(rows)} records into MongoDB collection '{t}'")
        else:
            print(f"ℹ️ Table '{t}' is empty")
    except Exception as e:
        print(f"❌ Error migrating {t}: {e}")

# Create indexes on MongoDB
m_db["calls"].create_index("id")
m_db["calls"].create_index("channel_id")
m_db["calls"].create_index("token_address")
m_db["calls"].create_index("created_at")
m_db["calls"].create_index("status")
m_db["channels"].create_index("channel_id", unique=True)
m_db["channels"].create_index("username")
m_db["alerts"].create_index([("call_id", 1), ("multiplier", 1)])
m_db["custom_media"].create_index("tag", unique=True)
m_db["deposit_wallets"].create_index("wallet_address", unique=True)
m_db["bot_config"].create_index("key", unique=True)
m_db["channel_owners"].create_index([("channel_identifier", 1), ("owner_user_id", 1)])
m_db["user_follows"].create_index([("channel_identifier", 1), ("user_id", 1)])

s_conn.close()
m_client.close()
print("🎉 Full SQLite to MongoDB Atlas Migration Complete!")
