"""Audio playback engine -- non-blocking sound via PipeWire.

Provides fire-and-forget playback. Never blocks, never raises.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

# Module-level cached backend
_backend_path: Optional[str] = None
_backend_name: Optional[str] = None
_detected: bool = False

FALLBACK_CHAIN = [
    ("pw-play", "pw-play"),
    ("paplay", "paplay"),
    ("aplay", "aplay"),
    ("mpv", "mpv"),
]


def detect_backend() -> tuple[str, str]:
    """Probe system for available audio tool. Cached after first call.

    Returns (path, name) tuple. ("", "none") if nothing found.
    """
    global _backend_path, _backend_name, _detected
    if _detected:
        return (_backend_path or "", _backend_name or "none")
    _detected = True
    for name, binary in FALLBACK_CHAIN:
        path = shutil.which(binary)
        if path:
            _backend_path = path
            _backend_name = name
            return (path, name)
    _backend_path = ""
    _backend_name = "none"
    return ("", "none")


def _build_args(name: str, path: str, sound: Path, volume: float, sink: str = "") -> list[str]:
    """Build CLI args for each backend.

    pw-play:  native PipeWire, explicit volume flag, optional --target sink
    paplay:   PulseAudio compat, no per-stream volume
    aplay:    ALSA direct, quiet mode
    mpv:      universal fallback with volume scaling
    """
    match name:
        case "pw-play":
            args = [path, f"--volume={volume:.3f}",
                    "-P", '{"application.name":"claude-voice"}']
            if sink:
                args.append(f"--target={sink}")
            args.append(str(sound))
            return args
        case "paplay":
            return [path, str(sound)]
        case "aplay":
            return [path, "-q", str(sound)]
        case "mpv":
            return [
                path,
                "--no-terminal",
                "--no-video",
                f"--volume={int(volume * 100)}",
                str(sound),
            ]
        case _:
            return [path, str(sound)]


def play_sound(
    path: Path,
    volume: float = 1.0,
    sink: str = "",
) -> Optional[subprocess.Popen[Any]]:
    """Play a sound file non-blockingly.

    Returns Popen handle if started, None on failure.
    Caller must NOT call .wait() -- fire and forget.

    Args:
        path: Absolute path to WAV/audio file.
        volume: 0.0-1.0 effective volume (after spatial mixing) passed to backend.
        sink: PipeWire target sink name (e.g. "hdmi-output-0"). Empty = default.
    """
    if not path.exists():
        return None
    backend_path, backend_name = detect_backend()
    if not backend_path:
        return None
    args = _build_args(backend_name, backend_path, path, volume, sink=sink)
    try:
        return subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # Detach from terminal signals (Ctrl+C safe)
        )
    except (OSError, FileNotFoundError):
        return None
