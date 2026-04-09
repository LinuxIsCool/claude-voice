"""Voice queue client for claude-voice.

Enqueues TTS speech for scheduled playback by the queue daemon.
Falls back to direct playback if the daemon isn't running — graceful degradation.

Usage in router.py:
    from queue_client import enqueue_speech
    result = enqueue_speech(wav_path, priority, agent_id, volume)
    if result is None:
        play_sound(wav_path, volume=volume)  # Daemon not running — play directly
"""
from __future__ import annotations

import json
import os
import socket
from typing import Optional

import subprocess

from constants import QUEUE_SOCKET


def _detect_tmux_pane() -> str:
    """Detect TMUX_PANE when env var is not inherited (e.g., in hook subprocesses).

    Falls back to querying tmux directly for the active pane of the
    session that owns our parent process.
    """
    try:
        # Method 1: Check if we're in a tmux session at all
        if not os.environ.get("TMUX"):
            return "_global"
        # Method 2: Ask tmux for the active pane
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#{pane_id}"],
            capture_output=True, text=True, timeout=1,
        )
        pane_id = result.stdout.strip()
        return pane_id if pane_id else "_global"
    except Exception:
        return "_global"


def enqueue_speech(
    wav_path: str,
    priority: int = 1,
    agent_id: str = "",
    volume: float = 0.8,
    category: str = "tts",
) -> Optional[dict]:
    """Enqueue a WAV for scheduled playback via the queue/arbiter daemon.

    Returns the daemon's response dict on success, or None if:
      - Queue daemon not running (socket doesn't exist)
      - Connection failed
      - Timeout

    When None is returned, the caller should play the WAV directly
    using play_sound() — this is graceful degradation.

    Args:
        wav_path: Absolute path to the WAV file to play.
        priority: Queue priority (0=LOW, 1=NORMAL, 2=CRITICAL from theme.json).
                  Maps to queue: 0→20, 1→50, 2→100.
        agent_id: Persona slug for speaker transition detection.
        volume: Legacy volume (arbiter recalculates at playback time).
        category: Sound category for gain chain lookup (tts, earcon, etc).
    """
    if not QUEUE_SOCKET.exists():
        return None  # Daemon not running — caller plays directly

    # Map theme.json priority (0/1/2) to queue priority (20/50/100)
    queue_priority = {0: 20, 1: 50, 2: 100}.get(priority, 50)

    try:
        request = json.dumps({
            "type": "enqueue",
            "wav_path": wav_path,
            "priority": queue_priority,
            "agent_id": agent_id or os.environ.get("PERSONA_SLUG", ""),
            "volume": volume,
            "pane_id": os.environ.get("TMUX_PANE", "") or _detect_tmux_pane(),
            "category": category,
        })
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(str(QUEUE_SOCKET))
            s.sendall((request + "\n").encode("utf-8"))

            buf = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    break

            return json.loads(buf.split(b"\n")[0])
    except Exception:
        return None  # Fail open — caller plays directly
