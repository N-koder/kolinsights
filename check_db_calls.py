import sqlite3, os

for db_name in ["spydefi.db", "alpharadar.db", "test_spydefi.db"]:
    path = os.path.join(r"c:\Users\webwo\Desktop\Codevault\telegram\spidyfi_bot", db_name)
    if os.path.exists(path):
        print(f"\n=== INSPECTING {db_name} ===")
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT id, channel_id, channel_username, token_symbol, token_address, entry_mc, created_at FROM calls ORDER BY id DESC LIMIT 15")
        rows = cursor.fetchall()
        for r in rows:
            d = dict(r)
            print(f"ID:{d['id']} | CH:{d['channel_id']} | USER:{d['channel_username']} | SYM:{d['token_symbol']} | ADDR:{d['token_address']} | MC:{d['entry_mc']} | TIME:{d['created_at']}")
        conn.close()
