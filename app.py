from flask import Flask, render_template, request, redirect, url_for, jsonify, session
import sqlite3
import os
import requests
import psycopg2
import psycopg2.extras
from datetime import timedelta
import time
import logging
from contextlib import contextmanager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# FIX #1: Use environment variable for secret key
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    logger.error("SECRET_KEY environment variable not set!")
    raise ValueError("SECRET_KEY must be set in environment variables")

API_TIMEOUT = 2.0  # seconds, tweak as you like

app.permanent_session_lifetime = timedelta(days=30)

DB_PATH = os.path.join("db", "books.db")

# If DATABASE_URL is set (in Render), we'll use Postgres instead of SQLite
DATABASE_URL = os.environ.get("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)


class PostgresConnection:
    """
    Small wrapper so Postgres behaves a bit like sqlite3.Connection:
    - .execute(query, params) returns a cursor with fetchone/fetchall
    - .commit()
    - .close()
    """

    def __init__(self, dsn: str):
        # Render Postgres generally requires SSL
        self.conn = psycopg2.connect(dsn, sslmode="require")

    def execute(self, query, params=()):
        # Convert sqlite-style "?" placeholders to psycopg2-style "%s"
        q = query.replace("?", "%s")
        cur = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(q, params)
        return cur

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


# FIX #4: Add context manager for automatic connection cleanup
@contextmanager
def get_db_connection():
    """Return a connection with row access by column name. Auto-closes on exit."""
    conn = None
    try:
        if USE_POSTGRES:
            conn = PostgresConnection(DATABASE_URL)
        else:
            # Default: local SQLite (no env var set)
            os.makedirs("db", exist_ok=True)
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row

        yield conn

    except Exception as e:
        logger.error(f"Database connection error: {e}", exc_info=True)
        if conn:
            try:
                conn.close()
            except:
                pass
        raise
    finally:
        if conn:
            try:
                conn.close()
            except Exception as e:
                logger.error(f"Error closing connection: {e}")


def init_db():
    """Create / migrate DB schema for either SQLite or Postgres."""
    with get_db_connection() as conn:
        if USE_POSTGRES:
            # Postgres schema
            conn.execute("""
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE,
                    password TEXT,
                    role TEXT CHECK (role IN ('admin','user')) NOT NULL
                )
            """)

            # FIX #2: Add indexes for better performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_books_isbn ON books(isbn)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_books_genre ON books(genre)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_books_author ON books(author)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_books_added_at ON books(added_at DESC)")

            conn.commit()
        else:
            # SQLite schema
            conn.execute("""
                CREATE TABLE IF NOT EXISTS books (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    isbn TEXT UNIQUE,
                    title TEXT,
                    author TEXT,
                    cover_url TEXT,
                    genre TEXT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE,
                    password TEXT,
                    role TEXT CHECK( role IN ('admin','user') ) NOT NULL
                )
            """)

            # FIX #2: Add indexes for SQLite
            conn.execute("CREATE INDEX IF NOT EXISTS idx_books_isbn ON books(isbn)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_books_genre ON books(genre)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_books_author ON books(author)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_books_added_at ON books(added_at DESC)")

            conn.commit()

    logger.info("Database initialized successfully")


def require_login(func):
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return func(*args, **kwargs)

    wrapper.__name__ = func.__name__
    return wrapper


def _normalize_genre(subjects, title=None, description=None):
    """
    Map messy subjects/title/description into a small, clean set of genres.
    Returns one of a controlled list, or None if nothing fits.
    """

    # Controlled genre set we care about
    GENRES = [
        "Crime",
        "Comedy",
        "Thriller",
        "Fantasy",
        "Science Fiction",
        "Horror",
        "Mystery",
        "Romance",
        "Young Adult",
        "Poetry",
        "Biography",
        "History",
        "Philosophy",
        "Self-Help",
        "Business",
        "Literary Fiction",
        "Non-Fiction",
        "Anthology",
    ]

    title = (title or "").lower()
    description = (description or "").lower()
    subjects = [s.lower() for s in (subjects or [])]

    text = " ".join(subjects + [title, description])

    def has(*words):
        return any(w in text for w in words)

    # Prioritised rules
    if has("crime", "detective", "police", "noir", "murder"):
        return "Crime"
    if has("comedy", "humor", "humour", "comedic", "satire", "parody"):
        return "Comedy"
    if has("thriller", "suspense", "conspiracy"):
        return "Thriller"
    if has("fantasy", "magic", "dragon", "wizard", "mythical"):
        return "Fantasy"
    if has("science fiction", "sci-fi", "sci fi", "space", "dystopian", "post-apocalyptic", "cyberpunk"):
        return "Science Fiction"
    if has("horror", "ghost", "haunted", "supernatural", "vampire"):
        return "Horror"
    if has("mystery", "whodunit", "detective story"):
        return "Mystery"
    if has("romance", "love story", "romantic"):
        return "Romance"
    if has("young adult", "ya", "teen fiction", "adolescent"):
        return "Young Adult"
    if has("poetry", "poem", "verse"):
        return "Poetry"
    if has("biography", "memoir", "autobiography"):
        return "Biography"
    if has("history", "historical"):
        return "History"
    if has("philosophy", "existentialism", "ethics", "metaphysics"):
        return "Philosophy"
    if has("self-help", "self help", "personal growth", "motivation"):
        return "Self-Help"
    if has("business", "management", "leadership", "entrepreneur", "economics"):
        return "Business"
    if has("anthology", "collection", "short stories", "compiled"):
        return "Anthology"

    # If it's fiction but we couldn't classify → Literary Fiction
    if has("fiction"):
        return "Literary Fiction"

    # Non-fiction catch-all
    if has("language", "culture", "society", "politics", "essays", "social life", "reportage"):
        return "Non-Fiction"

    # Last fallback: pick a non-generic subject
    GENERIC = {"fiction", "nonfiction", "literature", "juvenile fiction", "juvenile nonfiction"}
    for s in subjects:
        if s not in GENERIC:
            return s.title()

    return None


def _fetch_from_openlibrary(isbn: str):
    """
    Try to fetch metadata from Open Library.
    Returns dict with keys: title, authors(list), cover_url, subjects(list), description(str or None).
    Or None if nothing found.
    """
    try:
        url = "https://openlibrary.org/api/books"
        params = {
            "bibkeys": f"ISBN:{isbn}",
            "format": "json",
            "jscmd": "data",
        }
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        key = f"ISBN:{isbn}"
        if key not in data:
            return None

        entry = data[key]
        title = entry.get("title")
        authors = [a.get("name") for a in entry.get("authors", []) if a.get("name")]
        cover = entry.get("cover") or {}
        cover_url = cover.get("medium") or cover.get("large") or cover.get("small")
        subjects = [s.get("name") for s in entry.get("subjects", []) if s.get("name")]

        description = entry.get("description")
        if isinstance(description, dict):
            description = description.get("value")
        elif not isinstance(description, str):
            description = None

        return {
            "title": title,
            "authors": authors,
            "cover_url": cover_url,
            "subjects": subjects,
            "description": description,
        }

    except Exception as e:
        # FIX #5: Proper logging instead of print
        logger.error(f"Open Library error for ISBN {isbn}: {e}", exc_info=True)
        return None


def _fetch_from_googlebooks(isbn: str):
    """
    Fallback to Google Books if Open Library doesn't have useful info.
    Returns dict similar to _fetch_from_openlibrary.
    """
    try:
        url = "https://www.googleapis.com/books/v1/volumes"
        params = {"q": f"isbn:{isbn}"}
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items")
        if not items:
            return None

        volume_info = items[0].get("volumeInfo", {})
        title = volume_info.get("title")
        authors = volume_info.get("authors") or []
        image_links = volume_info.get("imageLinks") or {}
        cover_url = (
                image_links.get("thumbnail")
                or image_links.get("smallThumbnail")
        )
        # Google categories are coarser, but we can still use them as subjects
        subjects = volume_info.get("categories") or []
        description = volume_info.get("description")

        return {
            "title": title,
            "authors": authors,
            "cover_url": cover_url,
            "subjects": subjects,
            "description": description,
        }

    except Exception as e:
        # FIX #5: Proper logging instead of print
        logger.error(f"Google Books error for ISBN {isbn}: {e}", exc_info=True)
        return None


def fetch_cover_by_title_author(title: str, author: str | None = None):
    """
    Try to fetch a cover image using title + optional author via Google Books.
    Returns a cover URL or None.
    """
    try:
        q = f'intitle:{title}'
        if author:
            q += f' inauthor:{author}'

        url = "https://www.googleapis.com/books/v1/volumes"
        params = {
            "q": q,
            "maxResults": 5,
        }
        resp = requests.get(url, params=params, timeout=API_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items") or []
        for item in items:
            info = item.get("volumeInfo", {})
            image_links = info.get("imageLinks") or {}
            cover_url = (
                    image_links.get("thumbnail")
                    or image_links.get("smallThumbnail")
            )
            if cover_url:
                return cover_url

        return None

    except Exception as e:
        # FIX #5: Proper logging
        logger.error(f"fetch_cover_by_title_author error for '{title}' / '{author}': {e}", exc_info=True)
        return None


def normalize_author_name(name: str) -> str:
    """Normalize author strings like 'Christie, Agatha' -> 'Agatha Christie'."""
    name = (name or "").strip()

    # If it's "Last, First"
    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            last, first = parts
            return f"{first} {last}"

    return name


def get_book_from_db_by_isbn(isbn: str):
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT title, author, cover_url, genre FROM books WHERE isbn = ?",
            (isbn,)
        ).fetchone()

        if not row:
            return None

        return {
            "title": row["title"],
            "author": row["author"],
            "cover_url": row["cover_url"],
            "genre": row["genre"],
        }


def fetch_book_info(isbn: str):
    """
    Fetch book title, author, cover, and genre.

    0. Check local DB (cache) first.
    1. Then external APIs if needed.
    """

    # 0) Local DB cache
    try:
        cached = get_book_from_db_by_isbn(isbn)
        if cached:
            return (
                cached["title"] or "Unknown title",
                cached["author"] or "Unknown author",
                cached["cover_url"],
                cached["genre"],
            )
    except Exception as e:
        logger.error(f"Error checking DB cache for ISBN {isbn}: {e}", exc_info=True)

    # 1) External APIs
    meta = _fetch_from_openlibrary(isbn)

    if not meta:
        meta = _fetch_from_googlebooks(isbn)

    if not meta:
        # Nothing found anywhere
        return "Unknown title", "Unknown author", None, None

    title = meta.get("title") or "Unknown title"
    authors = meta.get("authors") or []
    authors = [normalize_author_name(a) for a in authors]
    author_str = ", ".join(authors) if authors else "Unknown author"
    cover_url = meta.get("cover_url")
    subjects = meta.get("subjects") or []
    description = meta.get("description")

    genre = _normalize_genre(subjects, title=title, description=description)

    return title, author_str, cover_url, genre


# FIX #3: Health check endpoint
@app.route("/health")
def health():
    """Health check endpoint for monitoring."""
    try:
        # Quick DB check
        with get_db_connection() as conn:
            conn.execute("SELECT 1").fetchone()
        return jsonify({"status": "healthy", "database": "connected"}), 200
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return jsonify({"status": "unhealthy", "error": str(e)}), 503


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        try:
            with get_db_connection() as conn:
                user = conn.execute(
                    "SELECT * FROM users WHERE username=? AND password=?",
                    (username, password)
                ).fetchone()

                if user:
                    session.permanent = True
                    session["user"] = user["username"]
                    session["role"] = user["role"]
                    logger.info(f"User {username} logged in successfully")
                    return redirect(url_for("index"))
                else:
                    logger.warning(f"Failed login attempt for username: {username}")
                    return render_template("login.html", error="Invalid credentials")
        except Exception as e:
            logger.error(f"Login error: {e}", exc_info=True)
            return render_template("login.html", error="An error occurred. Please try again.")

    return render_template("login.html")


@app.route("/")
@require_login
def index():
    try:
        with get_db_connection() as conn:
            # Total books
            total_row = conn.execute("SELECT COUNT(*) AS c FROM books").fetchone()
            total_books = total_row["c"] if total_row else 0

            # Genres + counts
            genre_rows = conn.execute("""
                SELECT
                    COALESCE(NULLIF(TRIM(genre), ''), 'Uncategorized') AS genre_label,
                    COUNT(*) AS count
                FROM books
                GROUP BY genre_label
                ORDER BY count DESC, genre_label ASC
            """).fetchall()

            # Authors + counts
            author_rows = conn.execute("""
                SELECT
                    author,
                    COUNT(*) AS count
                FROM books
                GROUP BY author
                ORDER BY count DESC, author ASC
            """).fetchall()

            return render_template(
                "index.html",
                total_books=total_books,
                genre_stats=genre_rows,
                author_stats=author_rows,
            )
    except Exception as e:
        logger.error(f"Error loading index: {e}", exc_info=True)
        return "An error occurred loading the page", 500


@app.route("/add", methods=["GET", "POST"])
@require_login
def add_book():
    if request.method == "POST":
        isbn = (request.form.get("isbn") or "").strip()
        title = (request.form.get("title") or "").strip()
        author = (request.form.get("author") or "").strip()
        cover_url = (request.form.get("cover_url") or "").strip()
        genre = (request.form.get("genre") or "").strip()

        if not isbn:
            return jsonify({"error": "ISBN is required"}), 400

        # If we get here without title/author, something's wrong with frontend
        if not title or not author:
            return jsonify({"error": "Missing title/author in request"}), 400

        # If genre is still blank, try to infer it.
        if not genre:
            try:
                _, _, _, g = fetch_book_info(isbn)
                if g:
                    genre = g
            except Exception as e:
                logger.error(f"Error fetching genre for ISBN {isbn}: {e}")

        # Optional: still try to fetch a cover if we have none
        if (not cover_url) and title and author:
            try:
                alt_cover = fetch_cover_by_title_author(title, author)
                if alt_cover:
                    cover_url = alt_cover
            except Exception as e:
                logger.error(f"Error fetching cover for {title}: {e}")

        try:
            with get_db_connection() as conn:
                conn.execute(
                    "INSERT INTO books (isbn, title, author, cover_url, genre) VALUES (?, ?, ?, ?, ?)",
                    (isbn, title, author, cover_url, genre),
                )
                conn.commit()
                logger.info(f"Added book: {title} (ISBN: {isbn})")
            return jsonify({"ok": True}), 200
        except (sqlite3.IntegrityError, psycopg2.IntegrityError):
            logger.warning(f"Duplicate ISBN attempt: {isbn}")
            return jsonify({"error": "This book is already in your library."}), 409
        except Exception as e:
            logger.error(f"Error adding book: {e}", exc_info=True)
            return jsonify({"error": "An error occurred adding the book"}), 500

    return render_template("add.html")


@app.route("/books")
@require_login
def books():
    """List and search books with pagination."""
    q = (request.args.get("q") or "").strip()

    # Pagination
    try:
        page = int(request.args.get("page", 1))
        if page < 1:
            page = 1
    except:
        page = 1

    PER_PAGE = 20
    offset = (page - 1) * PER_PAGE

    try:
        with get_db_connection() as conn:
            if q:
                like = f"%{q}%"

                # Use ILIKE for Postgres (case-insensitive), LIKE for SQLite
                if USE_POSTGRES:
                    total_row = conn.execute("""
                        SELECT COUNT(*) AS c
                        FROM books
                        WHERE title ILIKE ? OR author ILIKE ? OR isbn ILIKE ?
                    """, (like, like, like)).fetchone()

                    rows = conn.execute("""
                        SELECT *
                        FROM books
                        WHERE title ILIKE ? OR author ILIKE ? OR isbn ILIKE ?
                        ORDER BY added_at DESC
                        LIMIT ? OFFSET ?
                    """, (like, like, like, PER_PAGE, offset)).fetchall()
                else:
                    total_row = conn.execute("""
                        SELECT COUNT(*) AS c
                        FROM books
                        WHERE LOWER(title) LIKE LOWER(?) OR LOWER(author) LIKE LOWER(?) OR LOWER(isbn) LIKE LOWER(?)
                    """, (like, like, like)).fetchone()

                    rows = conn.execute("""
                        SELECT *
                        FROM books
                        WHERE LOWER(title) LIKE LOWER(?) OR LOWER(author) LIKE LOWER(?) OR LOWER(isbn) LIKE LOWER(?)
                        ORDER BY added_at DESC
                        LIMIT ? OFFSET ?
                    """, (like, like, like, PER_PAGE, offset)).fetchall()

            else:
                total_row = conn.execute("SELECT COUNT(*) AS c FROM books").fetchone()

                rows = conn.execute("""
                    SELECT *
                    FROM books
                    ORDER BY added_at DESC
                    LIMIT ? OFFSET ?
                """, (PER_PAGE, offset)).fetchall()

            total_books = total_row["c"]
            total_pages = max((total_books + PER_PAGE - 1) // PER_PAGE, 1)

            return render_template(
                "books.html",
                books=rows,
                query=q,
                page=page,
                total_pages=total_pages,
                total_books=total_books,
            )
    except Exception as e:
        logger.error(f"Error loading books: {e}", exc_info=True)
        return "An error occurred loading books", 500


@app.route("/genres/<genre_label>")
@require_login
def books_by_genre(genre_label):
    """Show all books for a given genre with pagination."""
    try:
        page = int(request.args.get("page", 1))
        if page < 1:
            page = 1
    except:
        page = 1

    PER_PAGE = 20
    offset = (page - 1) * PER_PAGE

    try:
        with get_db_connection() as conn:
            total_row = conn.execute("""
                SELECT COUNT(*) AS c
                FROM books
                WHERE COALESCE(NULLIF(TRIM(genre), ''), 'Uncategorized') = ?
            """, (genre_label,)).fetchone()

            rows = conn.execute("""
                SELECT *
                FROM books
                WHERE COALESCE(NULLIF(TRIM(genre), ''), 'Uncategorized') = ?
                ORDER BY added_at DESC
                LIMIT ? OFFSET ?
            """, (genre_label, PER_PAGE, offset)).fetchall()

            total_books = total_row["c"]
            total_pages = max((total_books + PER_PAGE - 1) // PER_PAGE, 1)

            return render_template(
                "genre_books.html",
                books=rows,
                genre_label=genre_label,
                page=page,
                total_pages=total_pages,
                total_books=total_books,
            )
    except Exception as e:
        logger.error(f"Error loading genre books: {e}", exc_info=True)
        return "An error occurred loading books", 500


@app.route("/api/preview_book")
@require_login
def api_preview_book():
    start = time.time()
    isbn = (request.args.get("isbn") or "").strip()
    if not isbn:
        return jsonify({"error": "ISBN is required"}), 400

    try:
        title, author, cover_url, genre = fetch_book_info(isbn)
        elapsed = time.time() - start
        logger.info(f"/api/preview_book for {isbn} took {elapsed:.2f}s")

        return jsonify({
            "isbn": isbn,
            "title": title,
            "author": author,
            "cover_url": cover_url,
            "genre": genre,
        })
    except Exception as e:
        logger.error(f"Error previewing book {isbn}: {e}", exc_info=True)
        return jsonify({"error": "Failed to fetch book information"}), 500


@app.route("/edit/<int:book_id>", methods=["GET", "POST"])
@require_login
def edit_book(book_id):
    try:
        with get_db_connection() as conn:
            if request.method == "POST":
                title = request.form.get("title", "").strip()
                author = request.form.get("author", "").strip()
                genre = request.form.get("genre", "").strip()

                conn.execute("""
                    UPDATE books 
                    SET title=?, author=?, genre=?
                    WHERE id=?
                """, (title, author, genre, book_id))
                conn.commit()
                logger.info(f"Updated book ID {book_id}")
                return redirect(url_for("books"))

            # GET → show the edit form
            book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()

            # Full genre list for dropdown
            GENRES = [
                "Crime", "Comedy", "Thriller", "Fantasy", "Science Fiction", "Horror",
                "Mystery", "Romance", "Young Adult", "Poetry", "Biography",
                "History", "Philosophy", "Self-Help", "Business",
                "Literary Fiction", "Non-Fiction", "Uncategorized",
                "Anthology"
            ]

            return render_template("edit_book.html", book=book, GENRES=GENRES)
    except Exception as e:
        logger.error(f"Error editing book {book_id}: {e}", exc_info=True)
        return "An error occurred", 500


@app.route("/delete/<int:book_id>", methods=["POST"])
@require_login
def delete_book(book_id):
    if session.get("role") != "admin":
        logger.warning(f"Unauthorized delete attempt by {session.get('user')}")
        return "Unauthorized", 403

    try:
        with get_db_connection() as conn:
            conn.execute("DELETE FROM books WHERE id=?", (book_id,))
            conn.commit()
            logger.info(f"Deleted book ID {book_id}")
        return redirect(url_for("books"))
    except Exception as e:
        logger.error(f"Error deleting book {book_id}: {e}", exc_info=True)
        return "An error occurred", 500


@app.route("/authors/<author_name>")
@require_login
def books_by_author(author_name):
    """Show all books for a given author with pagination."""
    try:
        page = int(request.args.get("page", 1))
        if page < 1:
            page = 1
    except:
        page = 1

    PER_PAGE = 20
    offset = (page - 1) * PER_PAGE

    try:
        with get_db_connection() as conn:
            total_row = conn.execute("""
                SELECT COUNT(*) AS c
                FROM books
                WHERE author = ?
            """, (author_name,)).fetchone()

            rows = conn.execute("""
                SELECT *
                FROM books
                WHERE author = ?
                ORDER BY added_at DESC
                LIMIT ? OFFSET ?
            """, (author_name, PER_PAGE, offset)).fetchall()

            total_books = total_row["c"]
            total_pages = max((total_books + PER_PAGE - 1) // PER_PAGE, 1)

            return render_template(
                "author_books.html",
                books=rows,
                author_name=author_name,
                page=page,
                total_pages=total_pages,
                total_books=total_books,
            )
    except Exception as e:
        logger.error(f"Error loading author books: {e}", exc_info=True)
        return "An error occurred loading books", 500


@app.route("/logout")
def logout():
    username = session.get("user", "unknown")
    session.clear()
    logger.info(f"User {username} logged out")
    return redirect(url_for("login"))


# Initialize database on startup
init_db()

if __name__ == "__main__":
    # Ensure debug is False in production
    debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() == "true"
    app.run(debug=debug_mode, host="0.0.0.0", port=5000)