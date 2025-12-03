import sqlite3
from app import DB_PATH, fetch_book_info

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

rows = cur.execute(
    "SELECT id, isbn FROM books WHERE cover_url IS NULL OR cover_url = ''"
).fetchall()

print(f"Found {len(rows)} books without covers.")

for row in rows:
    book_id = row["id"]
    isbn = row["isbn"]
    print(f"Fetching cover for {isbn} (id={book_id})...")

    title, author, cover_url = fetch_book_info(isbn)
    if cover_url:
        cur.execute(
            "UPDATE books SET cover_url = ? WHERE id = ?",
            (cover_url, book_id),
        )
        conn.commit()
        print("  → updated.")
    else:
        print("  → no cover found.")

conn.close()
print("Done.")
