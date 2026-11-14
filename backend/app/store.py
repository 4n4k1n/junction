"""SQLite persistence: session state as JSON + an event log for demo debugging."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import List, Optional

from .models import SessionState

DB_PATH = os.getenv("LEARNLENS_DB", os.path.join(os.path.dirname(__file__), "..", "learnlens.db"))


class Store:
    def __init__(self, path: str = DB_PATH) -> None:
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, ts REAL NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL);
            """
        )

    def save(self, s: SessionState) -> None:
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO sessions(id, state, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
                (s.session_id, s.model_dump_json(), time.time()),
            )

    def load(self, session_id: str) -> Optional[SessionState]:
        with self._lock:
            row = self.db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
        return SessionState.model_validate_json(row["state"]) if row else None

    def log(self, session_id: str, kind: str, payload: dict) -> None:
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO events(session_id, ts, kind, payload) VALUES (?, ?, ?, ?)",
                (session_id, time.time(), kind, json.dumps(payload, default=str)),
            )

    def events(self, session_id: str, limit: int = 50) -> List[dict]:
        with self._lock:
            rows = self.db.execute(
                "SELECT ts, kind, payload FROM events WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [{"ts": r["ts"], "kind": r["kind"], **json.loads(r["payload"])} for r in rows]
