"""Event logger for claude-voice.

Records every hook invocation with what played, what theme was active,
volume, and whether TTS fired. Two-layer storage following Legion conventions:

  - JSONL: append-only monthly files, <1ms writes, full payloads
  - SQLite: queryable, WAL mode, async fork writes (non-blocking)

Both layers capture the complete event record — zero truncation.
Errors write to stderr with prefix; never raise; never crash a hook.

Data paths:
  ~/.claude/local/voice/events/YYYY-MM.jsonl   (JSONL time series)
  ~/.claude/local/voice/voice.db               (SQLite queries)
  ~/.claude/local/health/voice-heartbeat       (health monitoring)
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

from constants import VOICE_DATA_DIR

DATA_DIR = VOICE_DATA_DIR
EVENTS_DIR = DATA_DIR / "events"
DB_PATH = DATA_DIR / "voice.db"

# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY,
    ts        TEXT    NOT NULL,
    session_id TEXT,
    event     TEXT    NOT NULL,
    theme     TEXT,
    sound     TEXT,
    tts_text  TEXT,
    tts_voice TEXT,
    volume    REAL,
    muted     INTEGER NOT NULL DEFAULT 0,
    elapsed_ms INTEGER,
    focus_state TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_event   ON events(event);
CREATE INDEX IF NOT EXISTS idx_events_ts      ON events(ts);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
) STRICT;
"""

_PRAGMAS = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA busy_timeout = 5000;
"""


def _ensure_db() -> None:
    """Create DB and schema if not present. Migrate if needed. Called once per process."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=5)
    try:
        con.executescript(_PRAGMAS + _SCHEMA)
        # Migration: add focus_state column if missing (Phase 3.5 Layer 0)
        cursor = con.execute("PRAGMA table_info(events)")
        columns = {row[1] for row in cursor.fetchall()}
        if "focus_state" not in columns:
            con.execute("ALTER TABLE events ADD COLUMN focus_state TEXT")
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# JSONL writer — <1ms target, monthly rotation
# ---------------------------------------------------------------------------

def _jsonl_path() -> Path:
    """Return current month's JSONL file path."""
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    return EVENTS_DIR / f"{month}.jsonl"


def _write_jsonl(record: dict) -> None:
    """Append one record to the current monthly JSONL file.

    Uses fcntl.flock for concurrent-safe appends (multiple hook processes
    may fire simultaneously). Full payload — never truncate.
    """
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _jsonl_path()
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# SQLite writer — async fork (non-blocking parent)
# ---------------------------------------------------------------------------

def _write_sqlite(record: dict) -> None:
    """Write record to SQLite in a daemon thread (non-blocking parent).

    Uses threading.Thread(daemon=True) instead of os.fork() to avoid
    the fork+imported-modules deadlock risk (Python's import machinery
    holds locks that can't be safely inherited by a forked child).
    SQLite WAL mode allows concurrent readers while thread writes.
    """

    def _do_write() -> None:
        try:
            _ensure_db()
            con = sqlite3.connect(str(DB_PATH), timeout=5)
            try:
                con.executescript(_PRAGMAS)
                con.execute(
                    """
                    INSERT INTO events
                        (ts, session_id, event, theme, sound,
                         tts_text, tts_voice, volume, muted, elapsed_ms,
                         focus_state)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.get("ts"),
                        record.get("sid"),
                        record.get("event"),
                        record.get("theme"),
                        record.get("sound"),
                        record.get("tts_text"),
                        record.get("tts_voice"),
                        record.get("volume"),
                        int(record.get("muted", False)),
                        record.get("ms"),
                        record.get("focus_state"),
                    ),
                )
                con.commit()
            finally:
                con.close()
        except Exception as e:
            sys.stderr.write(f"claude-voice: logger SQLite write: {e}\n")

    t = threading.Thread(target=_do_write, daemon=True)
    t.start()
    # Brief join to give the write a chance to complete before process exits.
    # Hook processes are short-lived — without this, daemon threads die on exit.
    # 50ms is enough for SQLite WAL write, well within the 150ms budget.
    t.join(timeout=0.05)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def log_event(
    event: str,
    session_id: Optional[str],
    *,
    theme: Optional[str] = None,
    sound: Optional[str] = None,
    tts_text: Optional[str] = None,
    tts_voice: Optional[str] = None,
    volume: Optional[float] = None,
    muted: bool = False,
    elapsed_ms: Optional[int] = None,
    focus_state: Optional[str] = None,
) -> None:
    """Log a voice hook event. Never raises.

    Full payload — every field is stored as-is. JSONL write is synchronous
    (<1ms). SQLite write is async via fork (non-blocking).

    Args:
        event:       Claude Code hook event name (e.g. "Stop", "SessionStart").
        session_id:  Session UUID from hook payload.
        theme:       Active theme name (e.g. "default", "starcraft").
        sound:       Filename of sound played, or None if muted/no sound.
        tts_text:    Text spoken by TTS, or None.
        tts_voice:   Kokoro voice preset used, or None.
        volume:      Effective volume 0.0-1.0 (after spatial mixing).
        muted:       Whether global mute was active.
        elapsed_ms:  Wall time from hook entry to audio dispatch (ms).
        focus_state: Spatial state: focused/same_window/same_session/other_session/no_tmux.
    """
    try:
        ts = datetime.now(timezone.utc).isoformat()
        record: Dict[str, Any] = {
            "ts": ts,
            "sid": session_id,
            "event": event,
            "theme": theme,
            "sound": sound,
            "volume": volume,
            "muted": muted,
        }
        if tts_text is not None:
            record["tts_text"] = tts_text
            record["tts_voice"] = tts_voice
        if elapsed_ms is not None:
            record["ms"] = elapsed_ms
        if focus_state is not None:
            record["focus_state"] = focus_state

        _write_jsonl(record)
        _write_sqlite(record)
    except Exception as e:
        sys.stderr.write(f"claude-voice: log_event: {e}\n")
