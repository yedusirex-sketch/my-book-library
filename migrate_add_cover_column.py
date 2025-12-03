import sqlite3
from app import DB_PATH  # reuse the same DB path

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

try:
    cur.execute("ALTER TABLE books ADD COLUMN cover_url TEXT")
    conn.commit()
    print("✅ Added cover_url column to books table.")
except sqlite3.OperationalError as e:
    # If you run it twice you'll get "duplicate column name" – that's fine
    print("⚠️ Could not add column (maybe it already exists?):", e)

conn.close()
