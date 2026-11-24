import sys
import sqlite3

sys.stdout.reconfigure(encoding='utf-8')

def remove_spydefi():
    conn = sqlite3.connect("spydefi.db")
    cursor = conn.cursor()

    cursor.execute("DELETE FROM channels WHERE title LIKE '%SpyDefi%' OR channel_id = 1960616143 OR channel_id = -1001960616143")
    deleted_cnt = cursor.rowcount
    conn.commit()

    print(f"Successfully deleted {deleted_cnt} record(s) matching 'SpyDefi' from channels table!")

    cursor.execute("SELECT channel_id, title, username FROM channels")
    channels = cursor.fetchall()
    print("\n=== Remaining Registered Channels/Groups ===")
    for ch in channels:
        print(f"ID: {ch[0]} | Title: {ch[1]} | Username: {ch[2]}")

    conn.close()

if __name__ == "__main__":
    remove_spydefi()
