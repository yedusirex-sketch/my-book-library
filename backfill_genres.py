# backfill_genres.py

from app import (
    get_db_connection,
    _fetch_from_openlibrary,
    _fetch_from_googlebooks,
    _normalize_genre,
)

def backfill_genres():
    conn = get_db_connection()

    # Find books with missing/empty/Uncategorized genre
    rows = conn.execute("""
        SELECT id, isbn, title, author, genre
        FROM books
        WHERE genre IS NULL
           OR TRIM(genre) = ''
           OR genre = 'Uncategorized'
    """).fetchall()

    print(f"Found {len(rows)} books to backfill.")

    updated = 0

    for row in rows:
        book_id = row["id"]
        isbn = row["isbn"]
        old_genre = row["genre"]

        print(f"\nProcessing id={book_id}, ISBN={isbn}, current genre={old_genre!r}")

        # 1) Try Open Library
        meta = _fetch_from_openlibrary(isbn)

        # 2) Fallback to Google Books if needed
        if not meta:
            meta = _fetch_from_googlebooks(isbn)

        if not meta:
            print("  → No metadata found from either API. Skipping.")
            continue

        # Extract fields needed for genre inference
        title = meta.get("title") or ""
        subjects = meta.get("subjects") or []
        description = meta.get("description") or ""

        new_genre = _normalize_genre(subjects, title=title, description=description)

        if not new_genre or new_genre == "Uncategorized":
            print(f"  → Could not infer a better genre (got {new_genre!r}). Skipping.")
            continue

        print(f"  → Updating genre to {new_genre!r}")

        conn.execute(
            "UPDATE books SET genre = ? WHERE id = ?",
            (new_genre, book_id),
        )
        updated += 1

    conn.commit()
    conn.close()
    print(f"\nDone. Updated {updated} books.")

if __name__ == "__main__":
    backfill_genres()
