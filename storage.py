"""
Simple SQLite-backed storage for:
  1. Deduplication - never post the same event twice
  2. Near-duplicate detection - avoid re-posting an evolving story with
     slightly different wording every run
  3. Relevance-check caching - classify each cluster's topic only once
  4. Pending clusters - stories seen but not yet confirmed across sources
"""
import sqlite3
import time
from contextlib import contextmanager


class Storage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS posted (
                    cluster_key TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    posted_at REAL NOT NULL
                )
            """)
            # Migrate older DBs that predate the title column
            cols = [row[1] for row in conn.execute("PRAGMA table_info(posted)").fetchall()]
            if "title" not in cols:
                conn.execute("ALTER TABLE posted ADD COLUMN title TEXT NOT NULL DEFAULT ''")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS evaluated (
                    cluster_key TEXT PRIMARY KEY,
                    relevant INTEGER NOT NULL,
                    evaluated_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS seen_entries (
                    entry_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    link TEXT NOT NULL,
                    source TEXT NOT NULL,
                    lean TEXT NOT NULL,
                    published REAL NOT NULL,
                    cluster_key TEXT,
                    media_url TEXT,
                    media_type TEXT
                )
            """)
            entry_cols = [row[1] for row in conn.execute("PRAGMA table_info(seen_entries)").fetchall()]
            if "media_url" not in entry_cols:
                conn.execute("ALTER TABLE seen_entries ADD COLUMN media_url TEXT")
            if "media_type" not in entry_cols:
                conn.execute("ALTER TABLE seen_entries ADD COLUMN media_type TEXT")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS sports_posted (
                    sports_key TEXT PRIMARY KEY,
                    posted_at REAL NOT NULL
                )
            """)

    def has_seen_entry(self, entry_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_entries WHERE entry_id = ?", (entry_id,)
            ).fetchone()
            return row is not None

    def add_entry(self, entry_id, title, link, source, lean, published, cluster_key,
                  media_url=None, media_type=None):
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO seen_entries
                   (entry_id, title, link, source, lean, published, cluster_key,
                    media_url, media_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (entry_id, title, link, source, lean, published, cluster_key,
                 media_url, media_type),
            )

    def get_recent_entries(self, window_minutes: int):
        cutoff = time.time() - window_minutes * 60
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT entry_id, title, link, source, lean, published, cluster_key,
                          media_url, media_type
                   FROM seen_entries WHERE published >= ?""",
                (cutoff,),
            ).fetchall()
        return rows

    def is_posted(self, cluster_key: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM posted WHERE cluster_key = ?", (cluster_key,)
            ).fetchone()
            return row is not None

    def mark_posted(self, cluster_key: str, title: str = ""):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO posted (cluster_key, title, posted_at) VALUES (?, ?, ?)",
                (cluster_key, title, time.time()),
            )

    def get_recent_posted_titles(self, lookback_hours: float):
        """Returns a list of titles posted within the lookback window - used
        to detect near-duplicate/re-hashed stories before posting again."""
        cutoff = time.time() - lookback_hours * 3600
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT title FROM posted WHERE posted_at >= ? AND title != ''",
                (cutoff,),
            ).fetchall()
        return [r[0] for r in rows]

    def is_evaluated(self, cluster_key: str):
        """Returns None if never evaluated, else True/False for relevance."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT relevant FROM evaluated WHERE cluster_key = ?", (cluster_key,)
            ).fetchone()
            if row is None:
                return None
            return bool(row[0])

    def mark_evaluated(self, cluster_key: str, relevant: bool):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO evaluated (cluster_key, relevant, evaluated_at) VALUES (?, ?, ?)",
                (cluster_key, int(relevant), time.time()),
            )

    def update_entry_cluster(self, entry_id: str, cluster_key: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE seen_entries SET cluster_key = ? WHERE entry_id = ?",
                (cluster_key, entry_id),
            )

    def is_sports_posted(self, sports_key: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM sports_posted WHERE sports_key = ?", (sports_key,)
            ).fetchone()
            return row is not None

    def mark_sports_posted(self, sports_key: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sports_posted (sports_key, posted_at) VALUES (?, ?)",
                (sports_key, time.time()),
            )
