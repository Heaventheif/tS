"""Persistent memory of the videos already handed out, kept per keyword.

Every video that search_videos() returns is written here under its keyword;
the next search for the same keyword skips anything already recorded, so the
same link is never delivered twice.

Two interchangeable backends share one schema:

  * SQLite  (standard library, zero setup) — local runs and the CLI.
                file: $TIKTOK_DB or ./seen_videos.db
  * Postgres (psycopg) — needed on Vercel, whose filesystem is ephemeral.
                url:  $DATABASE_URL (or $POSTGRES_URL), e.g. a free Neon database.

Keywords are normalised (Unicode NFKC, lower-cased, whitespace collapsed) so
"NASA", " nasa " and "Nasa" share one history. Sort / published / country
filters are deliberately NOT part of the key: one keyword = one history.
"""
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_DB = "seen_videos.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_videos (
    keyword    TEXT NOT NULL,
    video_id   TEXT NOT NULL,
    url        TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (keyword, video_id)
)
"""


def normalize_keyword(query):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", query or "")).strip().casefold()


class Store:
    """Shared logic; subclasses only supply the connection and the placeholder."""
    placeholder = "?"

    def __init__(self, conn):
        self.conn = conn
        self.conn.execute(SCHEMA)

    def _run(self, sql, params=()):
        return self.conn.execute(sql.replace("?", self.placeholder), params)

    def known_ids(self, query, ids):
        """The subset of `ids` already handed out for this keyword."""
        ids = [str(i) for i in ids]
        if not ids:
            return set()
        marks = ",".join("?" * len(ids))
        rows = self._run(f"SELECT video_id FROM seen_videos WHERE keyword = ? AND video_id IN ({marks})",
                         [normalize_keyword(query), *ids]).fetchall()
        return {row[0] for row in rows}

    def claim(self, query, videos):
        """Record `videos` as delivered; return only those this call really inserted.

        INSERT .. ON CONFLICT DO NOTHING makes the claim atomic: if two requests
        race for the same video, exactly one of them gets it.
        """
        key, now, claimed = normalize_keyword(query), datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), []
        for video in videos:
            cur = self._run("INSERT INTO seen_videos (keyword, video_id, url, first_seen) VALUES (?, ?, ?, ?) "
                            "ON CONFLICT (keyword, video_id) DO NOTHING",
                            (key, str(video["id"]), video["url"], now))
            if cur.rowcount == 1:
                claimed.append(video)
        return claimed

    def count(self, query):
        return self._run("SELECT COUNT(*) FROM seen_videos WHERE keyword = ?", (normalize_keyword(query),)).fetchone()[0]

    def forget(self, query):
        """Clear the history of one keyword; returns how many rows were removed."""
        return self._run("DELETE FROM seen_videos WHERE keyword = ?", (normalize_keyword(query),)).rowcount

    def close(self):
        self.conn.close()


class SQLiteStore(Store):
    @classmethod
    def connect(cls, path=DEFAULT_DB):
        # isolation_level=None -> autocommit; timeout waits out a concurrent writer
        return cls(sqlite3.connect(path, timeout=30, isolation_level=None))


class PostgresStore(Store):
    placeholder = "%s"

    @classmethod
    def connect(cls, url):
        try:
            import psycopg
        except ImportError as e:
            raise RuntimeError("a Postgres URL is set but psycopg is not installed: pip install 'psycopg[binary]'") from e
        # prepare_threshold=None: safe behind pgbouncer / Neon's pooled endpoint
        return cls(psycopg.connect(_clean_url(url), autocommit=True, prepare_threshold=None))


def _clean_url(url):
    """Drop query params libpq does not know (Vercel's Supabase integration adds `supa=`)."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "supa"]
    return urlunsplit(parts._replace(query=urlencode(query)))


def open_store(path=None):
    """Pick the backend from the environment. Returns None when no durable storage is available.

    An explicit `path` always means SQLite. Otherwise: $DATABASE_URL / $POSTGRES_URL -> Postgres,
    $TIKTOK_DB -> SQLite, on Vercel without a database -> None (its disk is not persistent),
    anywhere else -> ./seen_videos.db.
    """
    if path:
        return SQLiteStore.connect(path)
    url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if url:
        return PostgresStore.connect(url)
    path = os.environ.get("TIKTOK_DB")
    if path:
        return SQLiteStore.connect(path)
    if os.environ.get("VERCEL"):
        return None
    return SQLiteStore.connect(DEFAULT_DB)
