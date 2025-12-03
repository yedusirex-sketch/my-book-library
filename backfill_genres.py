import sqlite3
from app import DB_PATH, fetch_book_info

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

rows = cur.execute(
    "SELECT id, isbn FROM books WHERE genre IS NULL OR TRIM(genre) = ''"
).fetchall()

print(f"Found {len(rows)} books without genres.")

for row in rows:
    book_id = row["id"]
    isbn = row["isbn"]

    print(f"Fetching genre for ISBN {isbn} (id={book_id})...")

    title, author, cover_url, genre = fetch_book_info(isbn)

    if genre:
        cur.execute(
            "UPDATE books SET genre = ? WHERE id = ?",
            (genre, book_id),
        )
        conn.commit()
        print(f"  → Updated to genre: {genre}")
    else:
        print("  → No genre found, leaving as NULL.")

conn.close()
print("Done.")
