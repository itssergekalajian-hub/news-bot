"""
Simple SQLite-backed storage for:
  1. Deduplication - never post the same event twice
  2. Near-duplicate detection - avoid re-posting an evolving story with
     slightly different wording every run
  3. Relevance-check caching - classify each cluster's topic only once
  4. Pending clusters - stories seen but not yet confirmed across sources
"""
import json
import re
import sqlite3
import time
from difflib import SequenceMatcher
from contextlib import contextmanager


_HEADLINE_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "by", "for", "from", "in", "into",
    "is", "of", "on", "or", "the", "to", "with",
}


def normalize_headline(headline: str) -> str:
    """Normalize headline text for cross-run event matching."""
    text = re.sub(r"\[[^\]]+\]\([^)]*\)", " ", headline.casefold())
    words = re.findall(r"[a-z0-9]+", text)
    return " ".join(word for word in words if word not in _HEADLINE_STOPWORDS)


def headlines_match(first: str, second: str) -> bool:
    """Match normalized headlines by sequence similarity or keyword overlap."""
    left = normalize_headline(first)
    right = normalize_headline(second)
    if not left or not right:
        return False
    if SequenceMatcher(None, left, right).ratio() >= 0.65:
        return True
    left_words, right_words = set(left.split()), set(right.split())
    return (len(left_words & right_words) / len(left_words | right_words)) >= 0.65


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
                    normalized_headline TEXT NOT NULL DEFAULT '',
                    posted_at REAL NOT NULL
                )
            """)
            cols = [row[1] for row in conn.execute("PRAGMA table_info(posted)").fetchall()]
            if "title" not in cols:
                conn.execute("ALTER TABLE posted ADD COLUMN title TEXT NOT NULL DEFAULT ''")
            if "normalized_headline" not in cols:
                conn.execute(
                    "ALTER TABLE posted ADD COLUMN normalized_headline TEXT NOT NULL DEFAULT ''"
                )

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
                    media_type TEXT,
                    photo_candidates TEXT NOT NULL DEFAULT '[]'
                )
            """)
            entry_cols = [row[1] for row in conn.execute("PRAGMA table_info(seen_entries)").fetchall()]
            if "media_url" not in entry_cols:
                conn.execute("ALTER TABLE seen_entries ADD COLUMN media_url TEXT")
            if "media_type" not in entry_cols:
                conn.execute("ALTER TABLE seen_entries ADD COLUMN media_type TEXT")
            if "photo_candidates" not in entry_cols:
                conn.execute(
                    "ALTER TABLE seen_entries ADD COLUMN photo_candidates TEXT NOT NULL DEFAULT '[]'"
                )

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
                  media_url=None, media_type=None, photo_candidates=None):
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO seen_entries
                   (entry_id, title, link, source, lean, published, cluster_key,
                    media_url, media_type, photo_candidates)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (entry_id, title, link, source, lean, published, cluster_key,
                 media_url, media_type, json.dumps(photo_candidates or [])),
            )

    def get_recent_entries(self, window_minutes: int):
        cutoff = time.time() - window_minutes * 60
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT entry_id, title, link, source, lean, published, cluster_key,
                          media_url, media_type, photo_candidates
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

    def mark_posted(self, cluster_key: str, title: str = "",
                    normalized_headline: str = ""):
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO posted
                   (cluster_key, title, normalized_headline, posted_at)
                   VALUES (?, ?, ?, ?)""",
                (cluster_key, title,
                 normalize_headline(normalized_headline or title), time.time()),
            )

    def get_recent_posted_titles(self, limit: int = 40):
        """Return the latest normalized posted headlines for duplicate checks."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT COALESCE(NULLIF(normalized_headline, ''), title)
                   FROM posted
                   WHERE title != '' OR normalized_headline != ''
                   ORDER BY posted_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [r[0] for r in rows]

    def is_recent_headline_duplicate(self, headline: str,
                                     recent_headlines: list[str] | None = None) -> bool:
        """Check a headline against the latest 40 posted stories."""
        candidates = (recent_headlines if recent_headlines is not None
                      else self.get_recent_posted_titles(40))
        return any(headlines_match(headline, previous) for previous in candidates)

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

    def prune(self, seen_days=7, posted_days=90, evaluated_days=30, sports_days=90):
        """Delete old rows and VACUUM so the committed SQLite file stops
        growing without bound (it had crossed GitHub's 50MB warning, heading
        for the 100MB hard limit).

        seen_entries is the culprit: it stores every entry from every source
        on every run, but only entries inside the clustering window (a few
        hours) are ever read, so a few days' retention is plenty. posted and
        evaluated are kept much longer so a recurring story still dedupes and
        isn't re-evaluated. VACUUM actually shrinks the file on disk (plain
        DELETEs don't). Returns the number of rows deleted."""
        now = time.time()
        cutoffs = [
            ("seen_entries", "published", seen_days),
            ("posted", "posted_at", posted_days),
            ("evaluated", "evaluated_at", evaluated_days),
            ("sports_posted", "posted_at", sports_days),
        ]
        deleted = 0
        with self._conn() as conn:
            for table, col, days in cutoffs:
                # table/col are hardcoded above, never user input.
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE {col} < ?", (now - days * 86400,)
                )
                deleted += max(cur.rowcount, 0)
        # VACUUM must run outside a transaction - use an autocommit connection.
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        try:
            conn.execute("VACUUM")
        finally:
            conn.close()
        return deleted
