"""Durable link ownership and webhook queue. One worker serializes mutations."""

import json
import sqlite3
from pathlib import Path
import time


class State:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=30000;
            CREATE TABLE IF NOT EXISTS links (
                destination TEXT PRIMARY KEY, source TEXT NOT NULL,
                owner TEXT NOT NULL, suffix TEXT NOT NULL, kind TEXT NOT NULL,
                signature TEXT NOT NULL, status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY, payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, error TEXT
            );
            CREATE TABLE IF NOT EXISTS font_cache (
                signature TEXT PRIMARY KEY, digest TEXT, faces TEXT, error TEXT
            );
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def links(self, owner: str) -> list[dict]:
        return [dict(row) for row in self.db.execute("SELECT * FROM links WHERE owner=?", (owner,))]

    def font(self, signature: str) -> dict | None:
        row = self.db.execute("SELECT * FROM font_cache WHERE signature=?", (signature,)).fetchone()
        return dict(row) if row else None

    def cache_font(self, signature: str, digest: str | None, faces: list | None, error=None):
        """Font parsing is keyed by inode signature; hardlinks share one entry."""
        self.db.execute(
            "INSERT OR REPLACE INTO font_cache VALUES (?,?,?,?)",
            (signature, digest, json.dumps(faces) if faces is not None else None, error),
        )
        self.db.commit()

    def enqueue(self, payload: dict) -> int:
        cursor = self.db.execute("INSERT INTO jobs(payload) VALUES (?)", (json.dumps(payload),))
        self.db.commit()
        return cursor.lastrowid

    def recover(self):
        self.db.execute("UPDATE jobs SET status='queued' WHERE status='running'")
        self.db.commit()

    def next_job(self):
        row = self.db.execute(
            "SELECT * FROM jobs WHERE status='queued' AND next_attempt<=? ORDER BY id LIMIT 1",
            (time.time(),),
        ).fetchone()
        if row:
            self.db.execute(
                "UPDATE jobs SET status='running', attempts=attempts+1 WHERE id=?", (row["id"],)
            )
            self.db.commit()
            return dict(row)
        return None

    def finish(self, job: dict, error: str | None = None):
        attempts = job["attempts"] + 1
        status = "done" if error is None else ("failed" if attempts >= 6 else "queued")
        self.db.execute(
            "UPDATE jobs SET status=?,error=?,next_attempt=? WHERE id=?",
            (status, error, time.time() + min(900, 10 * 2**attempts), job["id"]),
        )
        self.db.commit()
