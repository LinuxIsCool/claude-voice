"""Ambient engine for claude-voice.

Manages a background audio loop that plays while subagents are running.
The loop starts on SubagentStart (if not already running) and stops when
the last subagent finishes. Volume scales with agent count.

Uses PID tracking for lifecycle management — the loop runs as a detached
pw-play process. SessionEnd always cleans up.
"""
from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Optional

from constants import VOICE_DATA_DIR

PID_FILE = VOICE_DATA_DIR / "ambient.pid"
COUNT_FILE = VOICE_DATA_DIR / "ambient-count"


def start_loop(wav_path: Path, volume: float = 0.3) -> Optional[int]:
    """Start a looping ambient sound if not already running.

    Returns the PID of the loop process, or None if already running or failed.
    """
    if is_running():
        return None
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            ["pw-play", "--loop", f"--volume={volume:.3f}", str(wav_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Write PID atomically to avoid race with concurrent start_loop
        tmp = PID_FILE.with_suffix(".tmp")
        tmp.write_text(str(proc.pid))
        tmp.rename(PID_FILE)
        return proc.pid
    except (OSError, FileNotFoundError):
        return None


def stop_loop() -> None:
    """Stop the ambient loop if running."""
    if not PID_FILE.exists():
        return
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
    except (ValueError, ProcessLookupError, PermissionError):
        pass
    PID_FILE.unlink(missing_ok=True)


def is_running() -> bool:
    """Check if the ambient loop process is alive."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # Signal 0 = check existence only
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        PID_FILE.unlink(missing_ok=True)
        return False


def increment_agents() -> int:
    """Increment the active agent count. Returns new count.

    If the ambient loop PID is dead but count > 0, reset count to 0 first
    (crash recovery). This prevents count drift from unmatched Start/Stop events.
    """
    current = _read_count()
    # If count > 0 but ambient loop is dead, reset (stale state from crash)
    if current > 0 and not is_running():
        current = 0
    count = current + 1
    COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    COUNT_FILE.write_text(str(count))
    return count


def decrement_agents() -> int:
    """Decrement the active agent count. Returns new count (min 0)."""
    count = max(0, _read_count() - 1)
    COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    COUNT_FILE.write_text(str(count))
    return count


def get_agent_count() -> int:
    """Get current active agent count."""
    return _read_count()


def cleanup() -> None:
    """Full cleanup — stop loop, reset count. Called on SessionEnd."""
    stop_loop()
    COUNT_FILE.unlink(missing_ok=True)


def _read_count() -> int:
    """Read agent count from file."""
    try:
        return int(COUNT_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0
