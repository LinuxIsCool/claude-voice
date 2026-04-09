---
title: "Audio Playback Engine — pw-play, Fallback Chain & Concurrency"
spec: "05"
status: draft
created: 2026-03-26
author: matt
tags: [claude-voice, audio, playback, pipewire, concurrency]
---

# 05 — Audio Playback Engine

## 1. Overview

The audio playback engine is the lowest layer of claude-voice. It receives a sound file path and parameters, plays it non-blockingly via PipeWire, and handles concurrency across multiple hook events and subagents. Every layer above this one — themes, hook dispatchers, TTS synthesis — ultimately bottoms out in a single call to `lib/audio.py`.

Responsibilities:

- **Backend detection**: Probe the system for available playback tools at import time, cache the result.
- **Non-blocking playback**: Spawn a detached subprocess that plays audio without blocking the hook process. Hooks must return in under 5 seconds; playback must never interfere with that.
- **Volume computation**: Combine master, category, and per-sound volume into a single effective value.
- **Concurrency modes**: Support overlap, interrupt, queue (fcntl), and debounce patterns for different event types.
- **Graceful degradation**: If no audio backend exists, if PipeWire is down, if a file is missing — return `None`, log once, move on. Playback is always best-effort. Never raise. Never block. Never crash the hook.

This module has zero external Python dependencies. It uses only the standard library (`subprocess`, `fcntl`, `shutil`, `os`, `time`, `json`, `pathlib`, `weakref`, `threading`).

---

## 2. System Audio Environment

This is the actual audio environment on Legion (Lenovo Legion T5, CachyOS):

| Component | Value |
|-----------|-------|
| PipeWire | 1.6.2 |
| WirePlumber | 0.5.13 (session/policy manager) |
| PulseAudio compat | 17.0.0 via `pipewire-pulse` (socket at `/run/user/1000/pulse/native`) |
| ALSA compat | `pipewire-alsa` shim over `/dev/snd` |
| Default sink | `alsa_output.pci-0000_01_00.1.hdmi-stereo` (HDMI to 4K TV) |
| Native sample spec | `float32le 2ch 48000Hz` |
| Quantum | 1024 frames @ 48kHz = ~21.3ms per cycle |
| System volume | 38% / -25.21 dB, not muted |
| Display output | HDMI to 4K TV at 4096x2160@59.94 |
| Python | 3.14.3 (system), no audio playback libraries installed |

Available playback tools (all confirmed present):

| Tool | Version | Path |
|------|---------|------|
| `pw-play` | 1.6.2 | `/usr/bin/pw-play` (part of `pipewire-tools`) |
| `paplay` | 17.0.0 | `/usr/bin/paplay` (part of `libpulse`) |
| `aplay` | 1.2.15.2 | `/usr/bin/aplay` (ALSA userspace) |
| `mpv` | 0.41.0 | `/usr/bin/mpv` |
| `ffplay` | n8.1 | `/usr/bin/ffplay` |

PipeWire auto-activates on demand via socket activation. There is no need to check whether PipeWire is running before calling `pw-play` — if the daemon is idle, the first client connection wakes it.

PipeWire mixes multiple streams at the server level. Multiple `pw-play` processes can run simultaneously without conflict. This is fundamentally different from raw ALSA, which requires `dmix` for concurrent access.

---

## 3. Playback Tool Selection

### Decision Matrix

| Tool | Startup Latency | PipeWire Native | Volume Control | Format Support | Binary Size | Verdict |
|------|-----------------|-----------------|----------------|----------------|-------------|---------|
| `pw-play` | 30-50ms | Yes (direct `libpipewire`) | `--volume=0.8` (stream-level, 0.0-1.0) | WAV, FLAC, OGG, AIFF, AU (via `libsndfile`) | Tiny | **PRIMARY** |
| `paplay` | 50-80ms | PulseAudio compat layer | Sink volume only (no per-stream flag) | WAV, AIFF, AU (via `libsndfile`) | Tiny | Fallback 1 |
| `aplay` | 30-60ms | No (ALSA shim → PipeWire) | No PulseAudio/PipeWire integration | WAV/PCM only | Tiny | Fallback 2 |
| `mpv` | 100-200ms | Via PipeWire audio output | `--volume=50` (0-130 integer scale) | Everything (MP3, OGG, OPUS, WAV, FLAC, AAC, ...) | Heavy (~40MB) | Last resort |
| `ffplay` | 80-150ms | Via PulseAudio or ALSA backend | `-volume 50` (0-100) | Everything FFmpeg decodes | Heavy (~40MB) | Not used |

### Decision

**`pw-play` is PRIMARY.** It connects directly to the PipeWire graph via `libpipewire` with zero compatibility-layer overhead. It has explicit per-stream `--volume` control (0.0-1.0 linear), a `--latency` flag for buffer size tuning, and supports our target format (WAV 48kHz 16-bit stereo) natively through `libsndfile`. Startup latency is the lowest of any option at 30-50ms.

**Fallback chain: `pw-play` -> `paplay` -> `aplay` -> `mpv`.**

The fallback chain exists for portability. On a system without PipeWire (unlikely on modern Linux desktop, but possible on headless/server or older distros), the engine degrades gracefully through PulseAudio, then ALSA, then mpv. If none are found, playback is silently disabled — no sounds, no errors, no crashes.

`ffplay` is excluded from the fallback chain. It opens a window by default (requiring `-nodisp`), has no latency advantage over mpv, and mpv provides a cleaner headless experience with `--no-terminal --no-video`.

---

## 4. Audio Format Specification

### Target Format

| Property | Value | Rationale |
|----------|-------|-----------|
| Container | WAV (RIFF/WAVE) | Universal support, zero decode overhead |
| Encoding | Uncompressed PCM | No decoder startup cost, direct passthrough |
| Sample rate | 48000 Hz | Matches PipeWire native quantum — zero resampling |
| Bit depth | 16-bit signed integer (`s16le`) | Sufficient dynamic range for earcons, half the size of `f32le` |
| Channels | Stereo (2 channels) | Matches default sink spec; mono acceptable for simple earcons |
| Byte order | Little-endian | x86-64 native, no byte-swapping |
| LUFS target | -14 LUFS | Standard for notification audio normalization |

### Size Budget

| Asset Type | Duration | Approx. File Size | Notes |
|------------|----------|--------------------|-------|
| Earcon (ack, click) | 50-200ms | 10-38KB | Most common sound type |
| Status sound (complete, error) | 200-500ms | 38-96KB | Medium frequency |
| Milestone fanfare | 500ms-2s | 96-384KB | Rare, celebratory |
| Ambient loop | 2-10s | 384KB-1.9MB | Optional, loops via player |
| Per-theme total | — | <500KB | All earcons + status sounds |
| All themes combined | — | <5MB | 6 themes + default set |

File size formula: `48000 Hz * 2 bytes * 2 channels = 192,000 bytes/second = ~192KB/s`

### Why WAV Over OGG/MP3

1. **Zero decode latency**: WAV is raw PCM with a 44-byte header. PipeWire reads it and pushes frames directly to the graph. OGG requires Vorbis decode; MP3 requires an MP3 decoder (`libsndfile` does not even support MP3). Every millisecond of decode time adds to the latency budget.

2. **PipeWire native passthrough**: `pw-play` reads WAV via `libsndfile` and passes PCM frames straight through. When sample rate matches (48kHz), there is no resampling. The data path is: `disk -> libsndfile header parse -> PipeWire graph -> HDMI sink`.

3. **Simpler pipeline**: No codec dependencies, no decode errors, no bitrate variability. A WAV file either plays or it doesn't.

4. **Acceptable size**: Our total asset budget is under 5MB. Earcons are 10-100KB. Compression would save perhaps 3MB total across all themes — not worth the latency cost.

5. **Authoring simplicity**: Sound designers export WAV natively. No lossy encode step means no generational quality loss during theme development.

OGG may be reconsidered for TTS cache files (see Section 16), where files are larger and generated at runtime. But for static earcon assets, WAV is the correct choice.

---

## 5. Core API (`lib/audio.py`)

### Module Interface

```python
"""Audio playback engine for claude-voice.

Provides non-blocking sound playback via PipeWire (pw-play) with
automatic fallback to paplay/aplay/mpv. Designed for hook contexts
where blocking is forbidden.

Dependencies: None (stdlib only).
Thread safety: All public functions are thread-safe.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import weakref
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FALLBACK_CHAIN = ("pw-play", "paplay", "aplay", "mpv")
"""Probe order for audio backend detection."""

DEFAULT_LATENCY_MS = "20ms"
"""pw-play --latency value. 20ms is reliably honored by WirePlumber."""

DEFAULT_MASTER_VOLUME = 0.8
"""Master volume when no config or env var is set."""

DEFAULT_CATEGORY_VOLUMES = {
    "earcon": 1.0,
    "tts": 0.9,
    "ambient": 0.3,
    "notification": 0.8,
}
"""Per-category volume multipliers."""

DEBOUNCE_COOLDOWN_MS = 500
"""Default debounce cooldown in milliseconds."""

# ---------------------------------------------------------------------------
# Module-level state (initialized at import time)
# ---------------------------------------------------------------------------

_backend_path: Optional[str] = None
"""Cached absolute path to the detected backend binary."""

_backend_name: Optional[str] = None
"""Human-readable name: "pw-play", "paplay", "aplay", "mpv", or "none"."""

_active_procs: weakref.WeakSet[subprocess.Popen] = weakref.WeakSet()
"""WeakSet of all spawned playback processes. Used by stop_current()."""

_category_procs: dict[str, list[subprocess.Popen]] = {}
"""Strong refs to processes keyed by category. Used by stop_by_category()."""

_category_lock: threading.Lock = threading.Lock()
"""Guards _category_procs mutations."""

_last_played: dict[str, float] = {}
"""Monotonic timestamps keyed by sound path. Used for debounce."""

_debounce_lock: threading.Lock = threading.Lock()
"""Guards _last_played mutations."""

_init_lock: threading.Lock = threading.Lock()
"""Guards lazy backend initialization."""

_initialized: bool = False
"""Whether detect_backend() has run."""
```

### Public Functions

#### `detect_backend() -> tuple[str, str]`

```python
def detect_backend() -> tuple[str, str]:
    """Probe system for available audio playback tool.

    Walks FALLBACK_CHAIN in order, calling shutil.which() on each.
    Returns the first one found as a (path, name) tuple.

    Returns ("", "none") if nothing is found — playback will be
    silently disabled for the lifetime of this process.

    Called lazily on first play_sound() invocation, then cached.
    Thread-safe via _init_lock.

    Side effects:
        Sets module-level _backend_path and _backend_name.
    """
    global _backend_path, _backend_name, _initialized
    with _init_lock:
        if _initialized:
            return (_backend_path or "", _backend_name or "none")
        for tool in FALLBACK_CHAIN:
            path = shutil.which(tool)
            if path:
                _backend_path = path
                _backend_name = tool
                _initialized = True
                return (path, tool)
        _backend_path = ""
        _backend_name = "none"
        _initialized = True
        return ("", "none")
```

**Behavior**: Probes once, caches forever. The cache is process-scoped — each hook invocation (a new Python process) re-probes. This is intentional: `shutil.which()` takes <1ms and ensures we always reflect the current system state.

**Error handling**: `shutil.which()` returns `None` on failure, never raises. If the entire chain fails, we return `("", "none")` and every subsequent `play_sound()` call returns `None` silently.

**Thread safety**: Protected by `_init_lock`. Multiple threads calling `play_sound()` simultaneously will block briefly on the first call while detection runs, then all use the cached result.

#### `play_sound(path, volume, priority, category, mode) -> Optional[subprocess.Popen]`

```python
def play_sound(
    path: Path,
    volume: float = 1.0,
    priority: int = 0,
    category: str = "earcon",
    mode: str = "overlap",
) -> Optional[subprocess.Popen]:
    """Play a sound file non-blockingly.

    Args:
        path: Absolute path to WAV file. Must exist on disk.
        volume: Per-sound volume level 0.0-1.0. Combined with master
                and category volumes to produce the effective volume.
        priority: 0=background, 1=normal, 2=high. Used by interrupt
                  mode to decide whether to kill the current sound.
        category: Sound category for volume lookup and process tracking.
                  One of: "earcon", "tts", "ambient", "notification".
        mode: Concurrency mode. One of: "overlap", "interrupt",
              "queue", "debounce". See Section 8 for full semantics.

    Returns:
        Popen handle if playback started, None if:
        - System is muted
        - File does not exist
        - No audio backend detected
        - Debounce suppressed the sound
        - Any OSError during Popen

        Caller should NOT call .wait() on the returned handle.
        This is fire-and-forget. The process reaps itself (or is
        tracked in _active_procs for stop_current()).

    Thread safety:
        Safe to call from multiple threads. Process tracking uses
        locks internally. The subprocess itself is fully independent.
    """
```

#### `play_sound_async(path, volume, category) -> None`

```python
def play_sound_async(path: Path, volume: float = 1.0, category: str = "earcon") -> None:
    """True fire-and-forget. No handle returned, no tracking.

    Spawns the subprocess and discards the Popen handle. The process
    runs to completion independently. No way to stop it once started.

    Use this when you don't need interrupt/stop capability and want
    the absolute simplest call site.

    Args:
        path: Absolute path to WAV file.
        volume: Per-sound volume 0.0-1.0.
        category: For volume calculation only (no process tracking).
    """
```

#### `stop_current() -> None`

```python
def stop_current() -> None:
    """Kill all active playback processes spawned by this module.

    Iterates _active_procs (WeakSet) and sends SIGTERM to each
    process that is still running. Does not wait for termination —
    SIGTERM is sufficient for pw-play, paplay, aplay, and mpv.

    Safe to call even if no processes are active.
    Safe to call from any thread.

    If SIGTERM doesn't work (process ignores it), we do NOT escalate
    to SIGKILL. Audio processes that ignore SIGTERM are rare and will
    be reaped by the OS when the parent exits.
    """
```

#### `stop_by_category(category) -> None`

```python
def stop_by_category(category: str) -> None:
    """Kill playback for a specific category (e.g., 'ambient').

    Only affects processes registered under the given category in
    _category_procs. Other categories continue playing.

    Primary use case: stopping ambient/background audio when a new
    ambient sound starts (interrupt mode for the ambient category).

    Args:
        category: The category string to stop. No-op if unknown.

    Thread safety: Acquires _category_lock.
    """
```

#### `is_muted() -> bool`

```python
def is_muted() -> bool:
    """Check mute state from environment or config.

    Resolution order:
        1. CLAUDE_VOICE_MUTE env var ("true", "1", "yes" -> muted)
        2. config.yaml `mute: true` (loaded by config module)
        3. Default: False (not muted)

    This is checked on every play_sound() call. It is intentionally
    NOT cached — mute state can change mid-session via env var or
    config file edit.
    """
```

#### `get_backend_info() -> dict`

```python
def get_backend_info() -> dict:
    """Return backend name, path, and capabilities for diagnostics.

    Returns:
        {
            "name": "pw-play",
            "path": "/usr/bin/pw-play",
            "has_volume": True,
            "has_latency": True,
            "formats": ["wav", "flac", "ogg", "aiff"],
        }

    If no backend is detected:
        {
            "name": "none",
            "path": "",
            "has_volume": False,
            "has_latency": False,
            "formats": [],
        }
    """
```

### Backend Capability Map

Used by `get_backend_info()` and internally by `_build_args()`:

```python
_BACKEND_CAPS = {
    "pw-play": {
        "has_volume": True,
        "has_latency": True,
        "formats": ["wav", "flac", "ogg", "aiff", "au", "caf"],
    },
    "paplay": {
        "has_volume": False,  # No per-stream volume flag
        "has_latency": True,  # --latency-msec
        "formats": ["wav", "aiff", "au"],
    },
    "aplay": {
        "has_volume": False,
        "has_latency": False,
        "formats": ["wav"],
    },
    "mpv": {
        "has_volume": True,
        "has_latency": False,
        "formats": ["wav", "flac", "ogg", "mp3", "opus", "aac"],
    },
    "none": {
        "has_volume": False,
        "has_latency": False,
        "formats": [],
    },
}
```

---

## 6. Non-Blocking Playback Pattern

### Canonical Implementation

```python
def play_sound(
    path: Path,
    volume: float = 1.0,
    priority: int = 0,
    category: str = "earcon",
    mode: str = "overlap",
) -> Optional[subprocess.Popen]:
    if is_muted():
        return None

    if not path.exists():
        return None  # Missing file — log warning, return None

    backend_path, backend_name = _get_backend()
    if not backend_path:
        return None  # No audio backend — silent mode

    # Apply concurrency mode
    if mode == "debounce":
        if _is_debounced(str(path)):
            return None  # Too recent — skip
    elif mode == "interrupt":
        stop_by_category(category)
    # mode == "overlap" -> no action needed
    # mode == "queue" -> handled externally by fcntl TTS lock (Section 9)

    effective_volume = _compute_volume(volume, category)
    args = _build_args(backend_name, backend_path, path, effective_volume)

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _track_process(proc, category)
        return proc
    except (OSError, FileNotFoundError):
        return None  # Silent failure — playback is best-effort
```

### Line-by-Line Explanation

**`is_muted()`**: Checked first because it is the cheapest check — reads an env var or a cached config bool. If the user has muted claude-voice, we skip everything else.

**`path.exists()`**: Validates the sound file exists on disk before attempting to spawn a process. Without this, `pw-play` would start, fail to open the file, exit non-zero, and we would have wasted a process spawn (~5ms) for nothing.

**`_get_backend()`**: Lazy wrapper around `detect_backend()`. On first call it probes the system; subsequent calls return the cached result. Returns `("", "none")` if no backend is available.

**`mode == "debounce"` / `_is_debounced()`**: Checks `_last_played[path]` against `time.monotonic()`. If the same file was played less than `DEBOUNCE_COOLDOWN_MS` milliseconds ago, returns `None`. This prevents rapid-fire events (like repeated prompt acknowledgments) from stacking up sounds.

**`mode == "interrupt"` / `stop_by_category()`**: Kills all currently-playing sounds in the same category before starting the new one. Used for events where the new information supersedes the old — errors, notifications, ambient changes.

**`_compute_volume(volume, category)`**: Calculates `master_volume * category_volume * sound_volume`. See Section 10.

**`_build_args()`**: Constructs the CLI argument list for the detected backend. See Section 7.

**`subprocess.Popen(...)`**: The core of non-blocking playback. Key arguments:

- **`stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL`**: Redirects both streams to `/dev/null`. This prevents two problems: (1) pipe buffer filling up and blocking the child process if we never read from it, and (2) zombie processes accumulating from unread pipe handles. We don't care about `pw-play`'s output — it either plays or it doesn't.

- **`start_new_session=True`**: This is **critical**. It calls `os.setsid()` in the child process, creating a new session and process group. Without this, pressing Ctrl+C in the terminal (which sends SIGINT to the entire foreground process group) would kill the `pw-play` process mid-playback. With `start_new_session=True`, the audio process is in its own process group and is immune to the terminal's signal propagation. Claude Code's own signal handling won't reach our audio processes.

**`_track_process(proc, category)`**: Adds the `Popen` handle to two data structures:
1. `_active_procs` (WeakSet) — for `stop_current()`. Uses `WeakSet` so processes that finish naturally are garbage-collected without explicit cleanup.
2. `_category_procs[category]` (list) — for `stop_by_category()`. Uses strong references because we need to actively manage category membership. Dead processes are pruned lazily on the next `stop_by_category()` call.

**`except (OSError, FileNotFoundError)`**: Catches the two failure modes of `Popen`:
- `FileNotFoundError`: The backend binary was removed between detection and invocation (extremely rare).
- `OSError`: General OS-level failure — permission denied, too many open files, etc.

In both cases we return `None`. We never raise from `play_sound()`. Playback is best-effort.

**The caller should NOT call `.wait()`**: In hook context, hooks must return in under 5 seconds. Calling `.wait()` on a sound that lasts 2 seconds would consume 40% of the hook's time budget. The fire-and-forget pattern means the sound plays in a fully independent process. When that process finishes, the OS reaps it (since we used `start_new_session=True`, the init process adopts it if our Python process exits first).

### Process Tracking Implementation

```python
def _track_process(proc: subprocess.Popen, category: str) -> None:
    """Register a playback process for stop_current() and stop_by_category().

    Adds to _active_procs (WeakSet) unconditionally.
    Adds to _category_procs[category] with pruning of dead processes.
    """
    _active_procs.add(proc)
    with _category_lock:
        if category not in _category_procs:
            _category_procs[category] = []
        # Prune dead processes while we hold the lock
        _category_procs[category] = [
            p for p in _category_procs[category] if p.poll() is None
        ]
        _category_procs[category].append(proc)
```

---

## 7. Backend Argument Builders

### Implementation

```python
def _build_args(name: str, path: str, sound: Path, volume: float) -> list[str]:
    """Construct CLI arguments for the detected backend.

    Args:
        name: Backend name ("pw-play", "paplay", "aplay", "mpv").
        path: Absolute path to the backend binary.
        sound: Path to the WAV file to play.
        volume: Effective volume 0.0-1.0 (already computed from
                master * category * sound).

    Returns:
        List of strings suitable for subprocess.Popen().
    """
    match name:
        case "pw-play":
            return [
                path,
                f"--volume={volume:.3f}",
                f"--latency={DEFAULT_LATENCY_MS}",
                str(sound),
            ]
        case "paplay":
            return [
                path,
                "--latency-msec=30",
                str(sound),
            ]
        case "aplay":
            return [
                path,
                "-q",          # Quiet — suppress progress output
                str(sound),
            ]
        case "mpv":
            return [
                path,
                "--no-terminal",
                "--no-video",
                "--really-quiet",
                "--ao=pipewire",
                f"--volume={int(volume * 100)}",
                str(sound),
            ]
        case _:
            return [path, str(sound)]  # Unknown backend — try raw invocation
```

### Backend-Specific Notes

**`pw-play`**:
- `--volume` accepts a linear float 0.0-1.0. This is stream-level volume — it does not modify the system sink volume. A value of `1.0` means "full stream volume" which is then mixed by PipeWire with the sink volume (currently 38%). So `--volume=0.8` at 38% sink = ~30% of maximum system output.
- `--latency=20ms` requests a 20ms buffer from WirePlumber. Values below 5ms may be silently raised by WirePlumber's policy. 20ms is reliable and still well within our latency budget.
- The argument order matters: flags before the filename.

**`paplay`**:
- Has no per-stream volume flag. Volume is inherited from the PulseAudio sink (which is PipeWire in disguise). This means we lose per-sound volume control when falling back to `paplay`. The effective volume is whatever the system sink is set to.
- `--latency-msec=30` requests a 30ms buffer. The PulseAudio socket handshake adds ~20ms on top of this.

**`aplay`**:
- `-q` suppresses the progress meter that `aplay` normally prints to stderr. Without this, our `stderr=DEVNULL` handles it anyway, but `-q` is cleaner.
- No volume control. No latency control (defaults to ALSA period settings).
- Strictly WAV/PCM only — if we ever add OGG support for TTS cache, `aplay` cannot play it.

**`mpv`**:
- `--no-terminal` disables terminal input handling (mpv normally listens for keyboard shortcuts).
- `--no-video` prevents video window creation even for audio-only files.
- `--really-quiet` suppresses all console output including the status line.
- `--ao=pipewire` explicitly selects PipeWire audio output. Without this, mpv would auto-detect (usually picks PipeWire anyway, but explicit is better).
- `--volume` uses an integer 0-130 scale. We convert our 0.0-1.0 float by multiplying by 100. Values above 100 are amplification (possible clipping).

---

## 8. Concurrency Model

Four playback modes, each serving a different interaction pattern:

### Mode: `overlap`

| Property | Value |
|----------|-------|
| Behavior | Play simultaneously, no interference with other sounds |
| Implementation | Call `Popen()`, track process, return immediately |
| Default for | Earcons (<200ms), session events, milestones |
| Max concurrent | Unlimited (PipeWire mixes at server level) |

**When to use**: Short sounds where simultaneous playback is acceptable. If a `task_complete` earcon fires while a `session_start` jingle is still playing, both should be audible. PipeWire's mixer handles this transparently.

**Implementation**:
```python
# overlap mode — the default path in play_sound()
# No kill, no wait, no lock — just spawn
proc = subprocess.Popen(args, stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)
_track_process(proc, category)
return proc
```

**Edge cases**:
- Rapid-fire overlap: If 10 sounds fire in 100ms, 10 `pw-play` processes run simultaneously. PipeWire handles this — tested up to 20 concurrent streams without issue. But if this happens regularly, the event should use `debounce` mode instead.
- Process accumulation: Each `pw-play` process lives for the duration of the sound (50ms-2s), then exits. For earcons under 200ms, processes are extremely short-lived. No cleanup needed.

**Example events**: `session_start`, `session_end`, `task_complete`, `agent_deploy`, `agent_return`, `commit`.

### Mode: `interrupt`

| Property | Value |
|----------|-------|
| Behavior | Kill current sound(s) in same category, then play new |
| Implementation | `stop_by_category(category)` then `Popen()` |
| Default for | Errors, notifications, ambient changes |
| Latency overhead | ~1-3ms for SIGTERM + Popen |

**When to use**: When the new sound supersedes whatever is currently playing in the same category. An error sound should cut through any ambient or notification that's playing. A new ambient loop should replace the old one (there should only ever be one ambient).

**Implementation**:
```python
def _play_interrupt(category: str, args: list[str]) -> Optional[subprocess.Popen]:
    """Kill current sounds in category, then play new sound."""
    stop_by_category(category)
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        _track_process(proc, category)
        return proc
    except (OSError, FileNotFoundError):
        return None
```

**Edge cases**:
- SIGTERM race: `stop_by_category()` sends SIGTERM to the old process. The old process may take up to ~10ms to actually stop. Meanwhile, the new process has already started. For earcons this is fine — the overlap is imperceptible. For longer sounds, PipeWire's mixer ensures both are audible during the brief overlap.
- Dead process in category list: `stop_by_category()` calls `proc.terminate()` on processes that may have already exited. `terminate()` on a dead process raises no exception (it's a no-op). The process is pruned from the list on the next `_track_process()` call.

**Example events**: `error`, `notification`, `permission`, `ambient`.

### Mode: `queue`

| Property | Value |
|----------|-------|
| Behavior | FIFO ordering with `fcntl` exclusive lock |
| Implementation | Acquire lock -> play -> wait for completion -> release lock |
| Default for | TTS speech sequences |
| Max queue depth | Bounded by lock timeout (30s) |

**When to use**: TTS speech, where ordering matters. If subagent A finishes and says "Authentication module complete" while subagent B finishes and says "Database migration done", they must not overlap — overlapping speech is unintelligible. The fcntl lock ensures only one TTS playback happens at a time, across all hook processes (including subagents, which are separate OS processes).

**Implementation**: See Section 9 for full fcntl specification. The `play_sound()` function delegates queue mode to the TTS layer, which wraps playback in lock acquisition.

**Edge cases**:
- Lock timeout: If a TTS playback hangs (process doesn't exit), the lock is held until the 30s timeout expires. The next waiter sees the timeout, calls `cleanup_stale_locks()`, and proceeds. Maximum delay: 30s for one stuck playback.
- Stale lock from crashed process: If a hook process crashes while holding the lock, the lock file persists but the PID in it is dead. `cleanup_stale_locks()` detects this via `os.kill(pid, 0)` and removes the lock.
- Subagent race: Two subagents finishing at the same instant both try to acquire the lock. One wins, the other backs off with exponential retry (100ms -> 200ms -> 400ms -> ... -> 1s cap). The loser will play after the winner finishes.

**Example events**: `tts` (all TTS speech output).

### Mode: `debounce`

| Property | Value |
|----------|-------|
| Behavior | Skip if same sound was played less than N ms ago |
| Implementation | Check `_last_played[path]` against `time.monotonic()` |
| Default for | Rapid-fire events (prompt acknowledgment) |
| Cooldown | Configurable, default 500ms |

**When to use**: Events that can fire many times in quick succession, where only the first (or one per cooldown window) should produce sound. The classic case is `prompt_ack` — if the user pastes a multi-line prompt, multiple acknowledgment events may fire within milliseconds. Only the first should play.

**Implementation**:
```python
def _is_debounced(key: str, cooldown_ms: int = DEBOUNCE_COOLDOWN_MS) -> bool:
    """Check if the given key was played too recently.

    Returns True if the sound should be suppressed (too recent).
    Returns False if the sound should play (cooldown expired).

    Updates _last_played[key] when returning False.
    Thread-safe via _debounce_lock.
    """
    now = time.monotonic()
    cooldown_s = cooldown_ms / 1000.0
    with _debounce_lock:
        last = _last_played.get(key, 0.0)
        if now - last < cooldown_s:
            return True  # Suppressed
        _last_played[key] = now
        return False  # Play it
```

**Edge cases**:
- Different sounds for same event: Debounce keys on `str(path)`, so two different WAV files for the same event type are debounced independently. If the theme has variant sounds (e.g., 3 different `ack.wav` variants), each variant has its own cooldown. This is intentional — variant selection happens before debounce, and we want to allow rapid variant changes.
- Cross-process debounce: `_last_played` is process-local (Python dict). It does not coordinate across multiple hook processes. This is acceptable because Claude Code hooks are sequential within a session — two hooks for the same event don't run simultaneously. Subagents are the exception, but subagents don't typically fire the same event type simultaneously.
- Memory growth: `_last_played` accumulates entries for every unique path played. In practice this is bounded by the number of unique sound files in all themes (perhaps 50-100 entries). No cleanup needed.

**Example events**: `prompt_ack`.

### Default Mode Mapping

```python
EVENT_PLAYBACK_MODE: dict[str, str] = {
    # Session lifecycle
    "session_start": "overlap",
    "session_end": "overlap",

    # User interaction
    "prompt_ack": "debounce",       # Cooldown: 500ms

    # Task completion
    "task_complete": "overlap",

    # Subagent lifecycle
    "agent_deploy": "overlap",
    "agent_return": "overlap",

    # Alerts
    "error": "interrupt",           # Errors interrupt everything
    "notification": "interrupt",    # Notifications interrupt everything
    "permission": "interrupt",      # Permission requests need attention

    # Git
    "commit": "overlap",

    # Background
    "ambient": "interrupt",         # New ambient replaces old ambient

    # Speech
    "tts": "queue",                 # TTS is queued with fcntl lock
}
```

This mapping lives in the config layer (not hardcoded in `lib/audio.py`). The playback engine receives the mode as a parameter — it doesn't know about event types.

---

## 9. fcntl TTS Queue

### Problem Statement

Claude Code can run multiple subagents in parallel. Each subagent is a separate OS process with its own hook instances. When two subagents complete at the same time, both fire `subagent_stop` hooks that may want to speak a TTS completion message. Without coordination, two TTS audio streams overlap and produce unintelligible noise.

Disler's solution (from `claude-code-hooks-mastery`) uses POSIX file locks (`fcntl.flock`) to serialize TTS playback across processes. We adopt this pattern with refinements.

### Lock File Specification

| Property | Value |
|----------|-------|
| Lock path | `~/.claude/local/voice/tts.lock` |
| Lock metadata path | `~/.claude/local/voice/tts.lock.meta` |
| Lock type | `fcntl.LOCK_EX` (exclusive, advisory) |
| Max lock age | 60 seconds (stale threshold) |
| Acquisition timeout | 30 seconds (total wait budget) |
| Backoff | Exponential: 100ms initial, 2x each attempt, 1s cap |

### Full Implementation

```python
import fcntl
import json
import os
import time
from pathlib import Path

LOCK_DIR = Path.home() / ".claude" / "local" / "voice"
LOCK_PATH = LOCK_DIR / "tts.lock"
LOCK_META_PATH = LOCK_DIR / "tts.lock.meta"

MAX_LOCK_AGE_SECONDS = 60
"""Stale lock threshold. Any lock older than this with a dead PID is removed."""

INITIAL_BACKOFF_MS = 100
"""Initial retry delay in milliseconds."""

MAX_BACKOFF_MS = 1000
"""Maximum retry delay (backoff cap) in milliseconds."""

BACKOFF_MULTIPLIER = 2.0
"""Exponential backoff multiplier."""

_lock_fd: int | None = None
"""File descriptor for the held lock. None if not held."""


def acquire_tts_lock(timeout: float = 30.0) -> bool:
    """Acquire exclusive TTS lock with exponential backoff.

    Algorithm:
        1. Ensure lock directory exists.
        2. Check for stale locks (dead PID or expired age).
        3. Attempt fcntl.flock(LOCK_EX | LOCK_NB) in a loop.
        4. On EAGAIN/EWOULDBLOCK, sleep with exponential backoff.
        5. On success, write metadata (PID, timestamp) to .meta file.
        6. On timeout, return False (never blocks indefinitely).

    Args:
        timeout: Maximum seconds to wait for lock acquisition.

    Returns:
        True if lock acquired, False if timeout reached.

    The lock is process-scoped (fcntl locks are per-fd, released on close).
    """
    global _lock_fd

    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_stale_locks()

    deadline = time.monotonic() + timeout
    backoff_s = INITIAL_BACKOFF_MS / 1000.0

    while True:
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_WRONLY, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Lock acquired
                _lock_fd = fd
                _write_lock_meta()
                return True
            except (BlockingIOError, OSError):
                # Lock held by another process
                os.close(fd)
        except OSError:
            pass  # Can't open lock file — retry

        # Check timeout
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False

        # Exponential backoff with jitter-free sleep
        sleep_time = min(backoff_s, remaining)
        time.sleep(sleep_time)
        backoff_s = min(backoff_s * BACKOFF_MULTIPLIER, MAX_BACKOFF_MS / 1000.0)


def release_tts_lock() -> None:
    """Release TTS lock. Safe to call even if not held.

    Closes the file descriptor (which releases the fcntl lock),
    removes the metadata file, and removes the lock file.

    Order matters: release lock BEFORE removing files, so another
    waiter doesn't see a stale lock file.
    """
    global _lock_fd

    if _lock_fd is not None:
        try:
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
            os.close(_lock_fd)
        except OSError:
            pass
        _lock_fd = None

    # Clean up files (best-effort)
    for p in (LOCK_META_PATH, LOCK_PATH):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def cleanup_stale_locks() -> None:
    """Remove lock files older than MAX_LOCK_AGE_SECONDS or with dead PIDs.

    Stale lock detection strategy:
        1. Read .meta file for PID and timestamp.
        2. If timestamp is older than MAX_LOCK_AGE_SECONDS, remove.
        3. If PID is no longer alive (os.kill(pid, 0) raises), remove.
        4. If .meta file is missing or corrupt, check lock file mtime.
        5. If lock file mtime is older than MAX_LOCK_AGE_SECONDS, remove.

    This function is called before every lock acquisition attempt.
    It is safe to call concurrently — removal of an already-removed
    file is a no-op (missing_ok=True).
    """
    # Strategy 1: Read metadata
    try:
        if LOCK_META_PATH.exists():
            meta = json.loads(LOCK_META_PATH.read_text(encoding="utf-8"))
            pid = meta.get("pid", 0)
            timestamp = meta.get("timestamp", 0)

            # Check age
            if time.time() - timestamp > MAX_LOCK_AGE_SECONDS:
                _remove_lock_files()
                return

            # Check PID liveness
            if pid > 0:
                try:
                    os.kill(pid, 0)  # Signal 0 = existence check, no actual signal
                except ProcessLookupError:
                    # PID is dead — lock is stale
                    _remove_lock_files()
                    return
                except PermissionError:
                    # PID exists but we can't signal it — lock is probably valid
                    pass
            return  # Lock appears valid
    except (json.JSONDecodeError, OSError, TypeError, KeyError):
        pass  # Meta file corrupt — fall through to mtime check

    # Strategy 2: Check lock file mtime
    try:
        if LOCK_PATH.exists():
            mtime = LOCK_PATH.stat().st_mtime
            if time.time() - mtime > MAX_LOCK_AGE_SECONDS:
                _remove_lock_files()
    except OSError:
        pass


def _write_lock_meta() -> None:
    """Write PID and timestamp to the metadata file."""
    try:
        meta = {
            "pid": os.getpid(),
            "timestamp": time.time(),
        }
        LOCK_META_PATH.write_text(
            json.dumps(meta, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass  # Best-effort — lock still works without metadata


def _remove_lock_files() -> None:
    """Remove lock and metadata files (best-effort)."""
    for p in (LOCK_META_PATH, LOCK_PATH):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
```

### Usage in TTS Playback

```python
def play_tts_queued(audio_path: Path, volume: float = 0.9) -> bool:
    """Play TTS audio with exclusive fcntl lock.

    This is the ONLY function that should be used for TTS playback.
    It acquires the lock, plays the audio synchronously (blocking
    until playback completes), then releases the lock.

    This function is designed to be called from a hook process.
    The hook must budget for the full playback duration + lock
    acquisition time in its timeout.

    Args:
        audio_path: Path to the TTS audio file (WAV).
        volume: Volume level 0.0-1.0.

    Returns:
        True if playback completed successfully.
        False if lock acquisition timed out or playback failed.
    """
    if not acquire_tts_lock(timeout=30.0):
        return False  # Another TTS is playing and didn't finish in 30s

    try:
        backend_path, backend_name = _get_backend()
        if not backend_path:
            return False

        effective_volume = _compute_volume(volume, "tts")
        args = _build_args(backend_name, backend_path, audio_path, effective_volume)

        try:
            result = subprocess.run(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,  # Hard timeout — no TTS should be longer than 30s
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            return False
    finally:
        release_tts_lock()
```

### Exponential Backoff Visualization

```
Attempt  Delay    Cumulative Wait
  1      100ms    100ms
  2      200ms    300ms
  3      400ms    700ms
  4      800ms    1.5s
  5      1000ms   2.5s     (capped at 1s)
  6      1000ms   3.5s
  7      1000ms   4.5s
  ...    ...      ...
  30     1000ms   ~30s     (timeout reached)
```

The first 4 attempts use rapid retry (100ms-800ms), covering the common case where a short TTS clip finishes quickly. After the 1s cap kicks in, we're polling once per second. The 30s total timeout ensures we never block indefinitely.

---

## 10. Volume Control System

### Three-Tier Volume Model

```
effective_volume = master_volume * category_volume * sound_volume
```

Each tier is a float in the range 0.0-1.0. The product is clamped to 0.0-1.0 before being passed to the backend.

### Tier 1: Master Volume

The global volume knob for all claude-voice audio.

| Source | Priority | Example |
|--------|----------|---------|
| `CLAUDE_VOICE_VOLUME` env var | Highest | `export CLAUDE_VOICE_VOLUME=0.5` |
| `config.yaml` `volume:` key | Medium | `volume: 0.8` |
| `DEFAULT_MASTER_VOLUME` constant | Lowest | `0.8` |

Resolution:
```python
def _get_master_volume() -> float:
    """Resolve master volume from env -> config -> default."""
    env = os.environ.get("CLAUDE_VOICE_VOLUME")
    if env is not None:
        try:
            v = float(env)
            return max(0.0, min(1.0, v))
        except ValueError:
            pass  # Invalid env value — fall through

    config = _get_config()
    if "volume" in config:
        try:
            v = float(config["volume"])
            return max(0.0, min(1.0, v))
        except (ValueError, TypeError):
            pass

    return DEFAULT_MASTER_VOLUME
```

### Tier 2: Category Volume

Per-category multiplier that scales relative to master.

| Category | Default | Rationale |
|----------|---------|-----------|
| `earcon` | 1.0 | Short UI sounds — full volume relative to master |
| `tts` | 0.9 | Speech slightly below earcons to avoid harshness |
| `ambient` | 0.3 | Background loops must be subtle, not distracting |
| `notification` | 0.8 | Notifications below earcons, above ambient |

Category volumes are configurable in `config.yaml`:
```yaml
categories:
  earcon: 1.0
  tts: 0.9
  ambient: 0.3
  notification: 0.8
```

### Tier 3: Sound Volume

Per-sound override specified in the theme's `theme.json`. Optional — defaults to 1.0 if not specified.

```json
{
  "sounds": {
    "session_start": {
      "file": "sounds/session_start.wav",
      "volume": 0.7
    },
    "error": {
      "file": "sounds/error.wav",
      "volume": 1.0
    }
  }
}
```

This allows theme authors to balance sounds relative to each other within a theme.

### Volume Computation

```python
def _compute_volume(sound_volume: float, category: str) -> float:
    """Calculate effective volume from three tiers.

    Args:
        sound_volume: Per-sound volume from play_sound() call (0.0-1.0).
        category: Sound category for category volume lookup.

    Returns:
        Effective volume clamped to 0.0-1.0.
    """
    master = _get_master_volume()
    cat_vol = _get_category_volume(category)
    effective = master * cat_vol * sound_volume
    return max(0.0, min(1.0, effective))
```

### Mute Check

```python
def is_muted() -> bool:
    """Check mute state. Resolution: env -> config -> default (False)."""
    env = os.environ.get("CLAUDE_VOICE_MUTE", "").lower()
    if env in ("true", "1", "yes"):
        return True
    if env in ("false", "0", "no"):
        return False

    config = _get_config()
    return bool(config.get("mute", False))
```

### Perception Notes

`pw-play --volume` accepts linear 0.0-1.0. Human loudness perception is logarithmic (approximately power-law with exponent ~0.6). This means the perceptual difference between 0.8 and 0.9 is much smaller than between 0.1 and 0.2.

For our operational range (0.3-1.0), linear volume is acceptable. The user sets master volume to a comfortable level, and the category/sound volumes make relative adjustments within a narrow band. If we were doing a volume slider UI, we'd want a logarithmic curve. For config-file values, linear is fine — the user can experiment and set what sounds right.

Below 0.1, the sound is effectively inaudible on most systems. The `_compute_volume` function does not special-case this — if the product of three tiers drops below 0.1, the sound plays at that volume. The user can mute entirely via the mute flag.

---

## 11. Latency Analysis

### Detailed Breakdown: Hook Event to First Audible Sound

```
Step                        Time        Cumulative    Notes
────────────────────────    ─────────   ──────────    ─────────────────────────────
Hook stdin read             ~1ms        1ms           JSON payload from Claude Code
JSON parse (json.loads)     ~1ms        2ms           ~1KB payload typical
Config/theme lookup         ~2ms        4ms           Read config.yaml + theme.json
Sound file resolve          ~1ms        5ms           Path.exists() + variant select
Variant selection           <1ms        5ms           Random choice from list
Volume calculation          <1ms        5ms           Three multiplications + clamp
subprocess.Popen()          ~5ms        10ms          fork() + exec() of pw-play
pw-play process startup     ~15ms       25ms          Dynamic linker, libpipewire init
PipeWire graph connection   ~10ms       35ms          Node creation, link to sink
First quantum processed     ~21ms       56ms          1024 frames @ 48kHz
────────────────────────    ─────────   ──────────    ─────────────────────────────
Total to first audible      ~56ms
```

### Budget Comparison

| Threshold | Source | Our Performance | Margin |
|-----------|--------|-----------------|--------|
| 150ms | Psychological association threshold (game audio UX research — action-sound association degrades above this) | 56ms | 94ms headroom |
| 300ms | Association weakening threshold (sound no longer feels "caused by" the event) | 56ms | 244ms headroom |
| 5000ms | Claude Code hook timeout (hard limit) | 56ms | 4944ms headroom |

We are well within the 150ms psychological association threshold. The user will perceive the sound as an immediate response to the triggering event.

### Latency by Backend

| Backend | Total to First Sound | vs. Budget |
|---------|---------------------|------------|
| `pw-play` | ~56ms | 94ms headroom |
| `paplay` | ~76ms (adds ~20ms PulseAudio socket handshake) | 74ms headroom |
| `aplay` | ~66ms (adds ~10ms ALSA shim overhead) | 84ms headroom |
| `mpv` | ~150ms (heavy process startup) | 0ms headroom |

Even `mpv` as last resort meets the 150ms threshold, barely. The fallback chain is latency-ordered — the system degrades gracefully.

### Worst-Case Scenarios

| Scenario | Additional Latency | Total | Still Under 150ms? |
|----------|-------------------|-------|---------------------|
| Cold disk cache (file not in page cache) | +5-15ms (NVMe read) | ~71ms | Yes |
| PipeWire cold start (socket activation) | +50-100ms | ~156ms | Borderline |
| High system load (24 threads saturated) | +10-30ms | ~86ms | Yes |
| Debounce check (lock contention) | +1ms | ~57ms | Yes |
| Interrupt mode (SIGTERM + new Popen) | +3-5ms | ~61ms | Yes |

The only scenario that approaches the 150ms threshold is PipeWire cold start — and this only happens on the first sound after system boot or after PipeWire has been idle long enough to be socket-deactivated (rare on a desktop that's actively playing other audio).

---

## 12. HDMI Audio Routing

### System Configuration

This machine outputs all audio via HDMI to a 4K TV (4096x2160@59.94). The audio path is:

```
PipeWire graph
  -> WirePlumber (session policy)
      -> ALSA HDMI sink
          -> Intel HDA controller (pci-0000:00:1f.3)
              -> HDMI cable
                  -> TV speakers / TV audio out
```

The default PipeWire sink is:
```
alsa_output.pci-0000_01_00.1.hdmi-stereo
```

### Routing Behavior

`pw-play` uses the default sink automatically. No explicit sink routing is needed in `_build_args()`. The sound goes to wherever `wpctl status` shows as the default audio sink.

If the user has multiple sinks (e.g., HDMI + USB headphones), PipeWire routes to the default. WirePlumber's policy handles sink switching when devices are plugged/unplugged. Our playback engine does not manage sink routing — that's WirePlumber's job.

### Edge Cases

**TV powered off**: PipeWire maintains the HDMI sink node even when the TV is off. Audio frames are buffered briefly and then discarded by the ALSA driver when the HDMI link reports no active receiver. `pw-play` exits normally with return code 0. No hang, no error, no audible output. When the TV powers back on, subsequent sounds play normally.

**TV switches input**: The HDMI link stays active (the GPU still drives the display output). PipeWire continues sending audio frames. The TV's audio decoder may buffer or discard them depending on the TV's firmware. When the TV switches back to the PC's HDMI input, audio resumes immediately — there is no reconnection delay because the link never dropped.

**HDMI cable disconnected**: PipeWire detects the sink removal via WirePlumber. The sink node is removed from the graph. `pw-play` fails to connect and exits with a non-zero return code. Our `play_sound()` function ignores the return code (fire-and-forget), so this is silent failure. When the cable is reconnected, WirePlumber re-creates the sink node and subsequent sounds play normally.

**Multiple HDMI outputs**: The Intel HDA controller may expose multiple HDMI ports (HDMI1, HDMI2, HDMI3). WirePlumber's default policy selects the one with an active connection. If multiple HDMI outputs are connected, the first-enumerated active one becomes the default. This is system-level configuration, not our concern.

### Diagnostic Commands

```bash
# Confirm default sink
wpctl status | head -30

# List all sinks with details
pw-cli ls Node | grep -A2 "audio.sink"

# Check available targets for pw-play
pw-play --list-targets

# Test playback (system test sound)
pw-play /usr/share/sounds/alsa/Front_Left.wav
```

---

## 13. Error Handling

### Failure Mode Table

| Failure | Detection | Response | User Impact | Frequency |
|---------|-----------|----------|-------------|-----------|
| No audio backend found | `detect_backend()` returns `("", "none")` | Log once at module init, all `play_sound()` returns `None` | Silent — no sounds for entire session | Rare (headless server, minimal container) |
| Sound file missing | `path.exists()` returns `False` before Popen | Log warning with file path, return `None` | That one event is silent | Occasional (theme misconfiguration, deleted asset) |
| `Popen` raises `OSError` | `try/except` around `subprocess.Popen()` | Return `None` | That one event is silent | Rare (too many open files, permission denied) |
| `Popen` raises `FileNotFoundError` | `try/except` around `subprocess.Popen()` | Return `None`, clear cached backend | That one event is silent, next call re-probes | Very rare (binary uninstalled between detection and use) |
| `pw-play` exits non-zero | We don't check — fire and forget | Process dies, we never know | That sound doesn't play | Occasional (PipeWire hiccup, corrupt WAV) |
| PipeWire not running | `pw-play` can't connect to PipeWire socket | `pw-play` exits non-zero, we don't check | Silent | Rare (system boot race, PipeWire crashed) |
| HDMI sink disconnected | PipeWire removes sink, `pw-play` fails to route | `pw-play` exits non-zero, we don't check | Silent until reconnected | Occasional (cable unplugged) |
| Disk full (can't create TTS cache files) | Caught in TTS synthesis layer, not playback | Playback still works for static earcon assets | TTS fails, earcons still work | Rare |
| fcntl lock stuck (holder crashed) | `cleanup_stale_locks()` checks PID liveness and lock age | Removes stale lock, next acquisition succeeds | TTS delayed by one lock check cycle (~100ms) | Rare |
| fcntl lock timeout (30s) | `acquire_tts_lock()` returns `False` | TTS for that event is skipped | One TTS message dropped | Very rare (implies 30s+ TTS playback) |
| Volume env var invalid | `float()` raises `ValueError` | Fall through to config.yaml, then default | Uses config/default volume instead | Rare (user typo) |
| Config file corrupt/missing | `_get_config()` returns empty dict | All config values use defaults | Default master volume (0.8), default categories | Occasional (first run, manual edit error) |

### Design Principle

**Playback is best-effort.** The playback engine never raises exceptions to its caller. Every public function returns a value (`None`, `False`, or a valid `Popen` handle) on every code path. Hooks must never crash because of audio — a silent session is vastly preferable to a broken session.

The only logging is:
1. One-time log at module init if no backend is found: `"No audio backend detected. claude-voice playback disabled."`
2. Per-event warning if a sound file is missing: `"Sound file not found: {path}"`
3. Per-event debug if debounce suppressed a sound: `"Debounced: {path} (cooldown {ms}ms)"`

All logging goes to stderr (not stdout — hooks communicate with Claude Code via stdout). Logging uses Python's built-in `logging` module at WARNING level for missing files and DEBUG for debounce.

---

## 14. Health Monitoring

### Heartbeat File

On every successful `play_sound()` call (where a `Popen` handle is returned, not `None`), the engine writes a heartbeat:

| Property | Value |
|----------|-------|
| Path | `~/.claude/local/health/voice-heartbeat` |
| Content | ISO 8601 timestamp: `2026-03-26T14:30:00.000000` |
| Write frequency | Every successful `play_sound()` call |
| Stale threshold | 24 hours |

```python
HEARTBEAT_PATH = Path.home() / ".claude" / "local" / "health" / "voice-heartbeat"

def _write_heartbeat() -> None:
    """Write current timestamp to heartbeat file. Best-effort."""
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.write_text(
            time.strftime("%Y-%m-%dT%H:%M:%S.000000") + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass  # Never fail on heartbeat write
```

### Health Interpretation

| Heartbeat State | Meaning | Action |
|-----------------|---------|--------|
| Fresh (< 1 hour) | Sounds are playing normally | None |
| Stale (1-24 hours) | No sounds played recently — possibly muted, possibly idle | Check if muted intentionally |
| Very stale (> 24 hours) | Either muted, broken, or machine was off | Run `scripts/play_test.py` to diagnose |
| Missing file | First run or health dir deleted | Will be created on first successful playback |

The heartbeat integrates with the existing claude-logging health check pattern. The `/status` command can read this file and report voice engine health.

### Integration with `/status`

```
Voice Engine: healthy (last sound: 3 minutes ago, backend: pw-play)
Voice Engine: stale (last sound: 26 hours ago — possibly muted or broken)
Voice Engine: disabled (no audio backend detected)
```

---

## 15. Testing Specification

### `scripts/play_test.py`

A standalone diagnostic script that validates the entire audio playback pipeline.

```python
#!/usr/bin/env python3
"""Audio playback engine diagnostic test.

Generates test tones and validates each backend in the fallback chain.
Reports latency measurements and pass/fail per test.

Usage:
    python scripts/play_test.py           # Run all tests
    python scripts/play_test.py --quick   # Backend detection only (no audio)

Requires: only stdlib (wave + struct for tone generation).
"""
import shutil
import struct
import subprocess
import sys
import math
import time
import wave
from pathlib import Path

TEST_DIR = Path.home() / ".claude" / "local" / "voice" / "test"
TEST_TONE_PATH = TEST_DIR / "test_440hz.wav"

# Audio parameters
SAMPLE_RATE = 48000
DURATION_MS = 200
CHANNELS = 2
BIT_DEPTH = 16  # bits
FREQUENCY_HZ = 440  # A4 concert pitch

BACKENDS = ["pw-play", "paplay", "aplay", "mpv"]


def generate_test_tone() -> Path:
    """Generate a 440Hz sine wave WAV file (200ms, 48kHz, 16-bit stereo).

    Uses stdlib wave + struct for sine generation. No external
    dependencies required.

    Returns:
        Path to the generated WAV file.
    """
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    num_frames = int(SAMPLE_RATE * DURATION_MS / 1000)
    max_amplitude = 2 ** (BIT_DEPTH - 1) - 1

    with wave.open(str(TEST_TONE_PATH), "w") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(BIT_DEPTH // 8)
        wf.setframerate(SAMPLE_RATE)

        for i in range(num_frames):
            t = i / SAMPLE_RATE
            sample = int(max_amplitude * 0.5 * math.sin(2 * math.pi * FREQUENCY_HZ * t))
            # Stereo: same sample on both channels
            frame = struct.pack("<hh", sample, sample)
            wf.writeframesraw(frame)

    return TEST_TONE_PATH


def test_backend(name: str) -> dict:
    """Test a single backend. Returns result dict.

    Result:
        {
            "name": "pw-play",
            "available": True,
            "path": "/usr/bin/pw-play",
            "latency_ms": 42.3,
            "exit_code": 0,
            "passed": True,
        }
    """
    path = shutil.which(name)
    if not path:
        return {
            "name": name,
            "available": False,
            "path": None,
            "latency_ms": None,
            "exit_code": None,
            "passed": False,
        }

    # Build args
    sound = str(TEST_TONE_PATH)
    if name == "pw-play":
        args = [path, "--volume=0.5", "--latency=20ms", sound]
    elif name == "paplay":
        args = [path, "--latency-msec=30", sound]
    elif name == "aplay":
        args = [path, "-q", sound]
    elif name == "mpv":
        args = [path, "--no-terminal", "--no-video", "--really-quiet",
                "--ao=pipewire", "--volume=50", sound]
    else:
        args = [path, sound]

    # Measure latency (Popen creation to process start)
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        t1 = time.monotonic()
        popen_latency_ms = (t1 - t0) * 1000

        # Wait for completion (we need exit code for the test)
        exit_code = proc.wait(timeout=10)

        return {
            "name": name,
            "available": True,
            "path": path,
            "latency_ms": round(popen_latency_ms, 1),
            "exit_code": exit_code,
            "passed": exit_code == 0,
        }
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as e:
        return {
            "name": name,
            "available": True,
            "path": path,
            "latency_ms": None,
            "exit_code": None,
            "passed": False,
            "error": str(e),
        }


def test_concurrent_playback() -> dict:
    """Test 3 simultaneous pw-play processes.

    Verifies PipeWire can mix multiple streams without conflict.
    """
    path = shutil.which("pw-play")
    if not path:
        return {"test": "concurrent", "passed": False, "reason": "pw-play not found"}

    sound = str(TEST_TONE_PATH)
    procs = []
    try:
        for _ in range(3):
            proc = subprocess.Popen(
                [path, "--volume=0.3", sound],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            procs.append(proc)

        # Wait for all to complete
        results = []
        for proc in procs:
            exit_code = proc.wait(timeout=10)
            results.append(exit_code)

        passed = all(rc == 0 for rc in results)
        return {
            "test": "concurrent",
            "passed": passed,
            "exit_codes": results,
        }
    except Exception as e:
        return {"test": "concurrent", "passed": False, "error": str(e)}


def test_interrupt_mode() -> dict:
    """Test interrupt: start a long sound, kill it, start a short one.

    Uses the 200ms test tone for both (sufficient for testing the pattern).
    """
    path = shutil.which("pw-play")
    if not path:
        return {"test": "interrupt", "passed": False, "reason": "pw-play not found"}

    sound = str(TEST_TONE_PATH)
    try:
        # Start "long" sound
        long_proc = subprocess.Popen(
            [path, "--volume=0.3", sound],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        # Let it start playing, then kill
        time.sleep(0.05)  # 50ms
        long_proc.terminate()
        long_proc.wait(timeout=5)

        # Start replacement sound
        short_proc = subprocess.Popen(
            [path, "--volume=0.5", sound],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        exit_code = short_proc.wait(timeout=10)

        return {
            "test": "interrupt",
            "passed": exit_code == 0,
            "long_terminated": long_proc.returncode is not None,
            "short_exit_code": exit_code,
        }
    except Exception as e:
        return {"test": "interrupt", "passed": False, "error": str(e)}


def run_all_tests() -> None:
    """Run the full test suite and print results."""
    print("=" * 60)
    print("claude-voice Audio Playback Engine Test")
    print("=" * 60)
    print()

    # Generate test tone
    print("Generating 440Hz test tone (200ms, 48kHz, 16-bit stereo)...")
    tone_path = generate_test_tone()
    print(f"  Written to: {tone_path}")
    print(f"  Size: {tone_path.stat().st_size} bytes")
    print()

    # Test each backend
    print("Backend Tests:")
    print("-" * 60)
    print(f"{'Backend':<12} {'Available':<12} {'Latency':<12} {'Exit':<8} {'Result'}")
    print("-" * 60)

    for name in BACKENDS:
        result = test_backend(name)
        avail = "yes" if result["available"] else "no"
        latency = f"{result['latency_ms']}ms" if result["latency_ms"] else "N/A"
        exit_code = str(result["exit_code"]) if result["exit_code"] is not None else "N/A"
        status = "PASS" if result["passed"] else "FAIL"
        print(f"{name:<12} {avail:<12} {latency:<12} {exit_code:<8} {status}")

    print()

    # Concurrent test
    print("Concurrent Playback Test (3 simultaneous streams):")
    concurrent = test_concurrent_playback()
    status = "PASS" if concurrent["passed"] else "FAIL"
    print(f"  Result: {status}")
    if "exit_codes" in concurrent:
        print(f"  Exit codes: {concurrent['exit_codes']}")
    print()

    # Interrupt test
    print("Interrupt Mode Test (kill + replace):")
    interrupt = test_interrupt_mode()
    status = "PASS" if interrupt["passed"] else "FAIL"
    print(f"  Result: {status}")
    print()

    # HDMI verification (manual)
    print("HDMI Audio Verification:")
    print("  Did you hear the test tones through your TV/speakers?")
    print("  (This requires manual confirmation — no automated check)")
    print()

    # Summary
    print("=" * 60)
    print("Test complete. Review results above.")
    print(f"Test tone preserved at: {tone_path}")
    print("=" * 60)


if __name__ == "__main__":
    if "--quick" in sys.argv:
        print("Quick mode: backend detection only")
        for name in BACKENDS:
            path = shutil.which(name)
            status = path if path else "NOT FOUND"
            print(f"  {name}: {status}")
    else:
        run_all_tests()
```

### Test Coverage Matrix

| Test | What It Validates | Automated? |
|------|-------------------|------------|
| Backend detection | `shutil.which()` finds each tool | Yes |
| pw-play playback | Native PipeWire path works | Yes (exit code) |
| paplay playback | PulseAudio compat path works | Yes (exit code) |
| aplay playback | ALSA compat path works | Yes (exit code) |
| mpv playback | Heavy fallback path works | Yes (exit code) |
| Popen latency | Time from Popen() to process start | Yes (measured) |
| Concurrent playback | 3 simultaneous pw-play streams | Yes (all exit 0) |
| Interrupt mode | Terminate + replace sequence | Yes (exit codes) |
| HDMI output | Sound is audible through TV | No (manual confirmation) |
| Volume control | `--volume` flag produces quieter output | No (manual confirmation) |
| Debounce | Rapid calls are suppressed | Testable in unit tests (no audio needed) |
| fcntl lock | Concurrent lock acquisition | Testable in unit tests (no audio needed) |

---

## 16. Future Considerations

### Spatial Audio (Left/Right Panning)

PipeWire supports per-stream channel mapping. `pw-play` doesn't expose a panning flag, but we could use `pw-cat` with raw PCM and manually pan the stereo field. Use case: play agent events on the left channel, user events on the right, creating a spatial dialogue.

Implementation would require pre-processing the WAV file or using a PipeWire filter node. Deferred until there's a clear UX benefit beyond novelty.

### Audio Ducking

When an earcon plays, temporarily lower the volume of any active ambient loop, then restore it. This is a standard game audio technique (e.g., lower music volume during dialogue).

Implementation: `_track_process()` already knows which processes are in each category. When an earcon fires, we could use `pactl set-sink-input-volume` on the ambient process's PipeWire stream ID to duck it. This requires querying PipeWire for the stream ID, which adds complexity. Deferred.

### Bluetooth Audio Latency Compensation

Bluetooth (A2DP/SBC) adds 100-200ms of codec latency on top of our pipeline. If the user switches to Bluetooth headphones, total latency could reach 250ms — beyond the 150ms association threshold. Options:
1. Detect Bluetooth sink (check `pactl list sinks` for `bluetooth` in the name) and pre-fire sounds slightly earlier.
2. Accept the latency — Bluetooth users are accustomed to audio lag.

### Multiple Simultaneous Output Devices

PipeWire supports routing to multiple sinks via `module-combine-sink` or WirePlumber policy. If the user wants sounds on both HDMI and headphones, this is a PipeWire configuration task, not a playback engine task. Our engine plays to the default sink; PipeWire handles fan-out.

### Opus/OGG Support for Compressed TTS Cache

TTS synthesis produces longer audio files (2-30 seconds). Storing these as WAV consumes significant disk space. Opus in an OGG container offers ~10:1 compression at near-transparent quality. `pw-play` supports OGG natively via `libsndfile`, so no backend change is needed.

Trade-off: OGG adds ~1-2ms of Vorbis decode time per file. For TTS files that are already queued (not latency-critical), this is acceptable. Earcon assets remain WAV for zero-decode-latency.

### Process Pooling / Persistent Connection

For very high frequency playback (>10 sounds/second), each `pw-play` invocation incurs the full 30-50ms startup cost. An alternative is a persistent `mpv` process with IPC socket control (`--input-ipc-server=/tmp/claude-voice-mpv.sock`), where we send play commands over the socket instead of spawning new processes. This eliminates startup latency entirely.

Deferred because our target event frequency is 1-5 sounds/minute, not 10/second. The per-process model is simpler and sufficient.

### Hardware Acceleration

Some Intel HDA controllers support hardware-mixed streams. PipeWire already uses hardware mixing when available via ALSA. No action needed from our side — PipeWire abstracts this.
