import sys
import sqlite3

sys.stdout.reconfigure(encoding='utf-8')

def inspect_db():
    conn = sqlite3.connect("spydefi.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]
    print("=== Tables in spydefi.db ===")
    for table in tables:
        cursor.execute(f"SELECT count(*) FROM {table}")
        cnt = cursor.fetchone()[0]
        print(f"Table '{table}': {cnt} records")

    if "channels" in tables:
        cursor.execute("SELECT channel_id, title, username FROM channels")
        channels = cursor.fetchall()
        print("\n=== Registered Channels/Groups ===")
        for ch in channels:
            print(f"ID: {ch[0]} | Title: {ch[1]} | Username: {ch[2]}")

    if "calls" in tables:
        cursor.execute("SELECT id, token_name, token_symbol, channel_title, entry_mc, ath_mc FROM calls ORDER BY id DESC LIMIT 5")
        calls = cursor.fetchall()
        print("\n=== Recent 5 Recorded Calls ===")
        for c in calls:
            print(f"ID: {c[0]} | Token: {c[1]} (${c[2]}) | Group: {c[3]} | Entry MC: ${c[4]:,.0f} | ATH MC: ${c[5]:,.0f}")

if __name__ == "__main__":
    inspect_db()
