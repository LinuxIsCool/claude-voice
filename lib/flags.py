"""Flag file management for claude-voice.

Flag files coordinate state between processes (file-as-IPC).
Each flag contains PID and timestamp for staleness detection.

Format: "PID TIMESTAMP\n"  (e.g., "12345 1774909577.123\n")
Legacy format: empty file (touch-only) — treated as always active.

Usage:
    from flags import write_flag, clear_flag, is_flag_active

    write_flag(STT_ACTIVE_PATH)       # Create with PID + timestamp
    is_flag_active(STT_ACTIVE_PATH)   # Check: exists, PID alive, not stale
    clear_flag(STT_ACTIVE_PATH)       # Remove
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional


def write_flag(path: Path) -> None:
    """Create a flag file with current PID and timestamp."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()} {time.time()}\n")


def clear_flag(path: Path) -> None:
    """Remove a flag file."""
    path.unlink(missing_ok=True)


def read_flag(path: Path) -> Optional[dict]:
    """Read flag file. Returns {"pid": int, "timestamp": float} or None."""
    if not path.exists():
        return None
    try:
        content = path.read_text().strip()
        if not content:
            return {"pid": 0, "timestamp": time.time()}
        parts = content.split()
        if len(parts) >= 2:
            return {"pid": int(parts[0]), "timestamp": float(parts[1])}
        return {"pid": 0, "timestamp": time.time()}
    except (ValueError, OSError):
        return None


def is_flag_active(path: Path, max_age_seconds: float = 60.0) -> bool:
    """Check if a flag is active: exists, PID alive, not stale.

    Returns True if:
      - File exists AND
      - (PID is 0 [legacy format] OR PID is alive) AND
      - (timestamp is 0 [legacy] OR age < max_age_seconds)

    Returns False if file missing, PID dead, or timestamp stale.
    Fails open for legacy (empty) flag files — assumes active.
    """
    info = read_flag(path)
    if info is None:
        return False

    pid = info["pid"]
    timestamp = info["timestamp"]

    # Check PID alive (skip for legacy flags with pid=0)
    if pid > 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False  # Process does not exist — flag is stale
        except PermissionError:
            pass  # Process exists but owned by another user — treat as alive

    # Check staleness (skip for legacy flags)
    if pid > 0 and max_age_seconds > 0:
        age = time.time() - timestamp
        if age > max_age_seconds:
            return False

    return True
