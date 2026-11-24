import sqlite3, os

db_path = r"c:\Users\webwo\Desktop\Codevault\telegram\spidyfi_bot\spydefi.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    print("Cleaning up duplicate call records in spydefi.db...")
    cursor.execute("""
        DELETE FROM calls
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM calls
            GROUP BY channel_id, LOWER(token_address)
        )
    """)
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    print(f"Removed {deleted} duplicate call records from spydefi.db!")
else:
    print("No spydefi.db found to clean.")
