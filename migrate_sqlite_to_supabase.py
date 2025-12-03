import os
import sqlite3
import psycopg2
import psycopg2.extras

DB_PATH = os.path.join("db", "books.db")


def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"Local SQLite DB not found at {DB_PATH}")

    # 1. Read from local SQLite
    sqlite_conn = sqlite3.connect(DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    cur_sqlite = sqlite_conn.cursor()

    rows = cur_sqlite.execute(
        "SELECT isbn, title, author, cover_url, genre, added_at FROM books"
    ).fetchall()

    print(f"Found {len(rows)} books in local SQLite.")

    # 2. Connect to Supabase Postgres (via pooler URL)
    pg_conn = psycopg2.connect(database_url, cursor_factory=psycopg2.extras.RealDictCursor)
    pg_cur = pg_conn.cursor()

    # Make sure the books table exists (init_db will also do this on the server)
    pg_cur.execute("""
        CREATE TABLE IF NOT EXISTS books (
            id SERIAL PRIMARY KEY,
            isbn TEXT UNIQUE,
            title TEXT,
            author TEXT,
            cover_url TEXT,
            genre TEXT,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    pg_conn.commit()

    inserted = 0

    for row in rows:
        try:
            pg_cur.execute("""
                INSERT INTO books (isbn, title, author, cover_url, genre, added_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (isbn) DO NOTHING
            """, (
                row["isbn"],
                row["title"],
                row["author"],
                row["cover_url"],
                row["genre"],
                row["added_at"],
            ))
            if pg_cur.rowcount == 1:
                inserted += 1
        except Exception as e:
            print(f"Error inserting ISBN {row['isbn']}: {e}")

    pg_conn.commit()
    print(f"Inserted {inserted} new books into Supabase.")

    pg_cur.close()
    pg_conn.close()
    sqlite_conn.close()


if __name__ == "__main__":
    main()
