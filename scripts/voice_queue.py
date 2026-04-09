# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Voice queue daemon — schedules who speaks when.

Priority-based scheduling for multi-agent TTS output. Only one agent speaks
at a time. Higher priority items play first. Speaker transitions get a 300ms
pause. Stale items expire after 30s.

Earcons bypass this queue — they're short and low overlap risk.
Only TTS speech is queued.

Usage:
    # Start daemon
    uv run scripts/voice_queue.py

    # Stop daemon
    uv run scripts/voice_queue.py --stop

    # Check status
    uv run scripts/voice_queue.py --check

Design derived from LinuxIsCool/claude-plugins-public voice coordination system.
"""
from __future__ import annotations

import heapq
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# SYNC: lib/constants.py:QUEUE_SOCKET — must match
SOCKET_PATH = Path("~/.claude/local/voice/queue.sock").expanduser()
# SYNC: lib/constants.py:QUEUE_PID — must match
PID_PATH = Path("~/.claude/local/voice/queue.pid").expanduser()
LOG_PATH = Path("~/.claude/local/voice/queue.log").expanduser()

# Read from config.yaml if available
CONFIG_PATH = Path("~/.claude/local/voice/config.yaml").expanduser()
MAX_ITEMS = 50
MAX_WAIT_SECONDS = 30
SPEAKER_TRANSITION_MS = 300
# INTERRUPT_THRESHOLD removed — preemption not implemented
# Disable idle timeout when running under systemd (systemd manages lifecycle).
# Only timeout when manually started.
_UNDER_SYSTEMD = os.environ.get("INVOCATION_ID") is not None  # systemd sets this
IDLE_TIMEOUT = 0 if _UNDER_SYSTEMD else 30 * 60  # 0 = no timeout

# SYNC: lib/constants.py:STT_ACTIVE_PATH — must match
STT_ACTIVE_PATH = Path("~/.claude/local/voice/stt-active").expanduser()
# SYNC: should be in lib/constants.py — not yet, but single source here
TTS_PLAYING_PATH = Path("~/.claude/local/voice/tts-playing").expanduser()
VOICE_STATE_PATH = Path("~/.claude/local/voice/speaking-now.json").expanduser()
FOCUS_STATE_PATH = Path("~/.claude/local/voice/focus-state").expanduser()

# Try to use flags module for stale-aware checking, fallback to exists()
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    from flags import is_flag_active as _is_flag_active, write_flag as _write_flag, clear_flag as _clear_flag
    from volume import compute_gain_chain as _compute_gain_chain
    def _stt_is_active():
        return _is_flag_active(STT_ACTIVE_PATH, max_age_seconds=120)
    _has_gain_chain = True
except ImportError:
    _has_gain_chain = False
    def _compute_gain_chain(category, agent_id, config, policy_vol=1.0):
        return {"final": config.get("volume", 0.8) * float(config.get("system_gain", 1.0))}
    def _stt_is_active():
        if not STT_ACTIVE_PATH.exists():
            return False
        try:
            return (time.time() - STT_ACTIVE_PATH.stat().st_mtime) < 120
        except OSError:
            return False
    def _write_flag(p):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(os.getpid()))
    def _clear_flag(p):
        p.unlink(missing_ok=True)

# Attempt to read queue config from config.yaml
# Health tracking (module-level so _get_health() can read them)
_health_playback_start: float = 0.0
_health_last_advance: float = 0.0
_health_queue_length: int = 0
_health_is_playing: bool = False


def _get_health() -> dict:
    """Return health diagnostics for --check."""
    now = time.monotonic()
    playback_duration = (now - _health_playback_start) if _health_is_playing else 0.0
    time_since_advance = now - _health_last_advance if _health_last_advance > 0 else 0.0

    # Flag freshness checks
    stt_stale = False
    tts_stale = False
    try:
        if STT_ACTIVE_PATH.exists():
            age = time.time() - STT_ACTIVE_PATH.stat().st_mtime
            stt_stale = age > 120
    except OSError:
        pass
    try:
        if TTS_PLAYING_PATH.exists():
            age = time.time() - TTS_PLAYING_PATH.stat().st_mtime
            tts_stale = age > 60
    except OSError:
        pass

    warnings = []
    if playback_duration > 30:
        warnings.append(f"playback running {playback_duration:.0f}s")
    if _health_queue_length > 0 and time_since_advance > 60:
        warnings.append(f"queue stalled ({time_since_advance:.0f}s since last advance)")
    if stt_stale:
        warnings.append("stt-active flag stale (>120s)")
    if tts_stale:
        warnings.append("tts-playing flag stale (>60s)")

    return {
        "ok": len(warnings) == 0,
        "playback_duration_s": round(playback_duration, 1),
        "time_since_advance_s": round(time_since_advance, 1),
        "stt_stale": stt_stale,
        "tts_stale": tts_stale,
        "warnings": warnings,
    }


try:
    for line in CONFIG_PATH.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("max_items:"):
            v = stripped.split(":", 1)[1].strip().split("#")[0].strip()
            if v.isdigit(): MAX_ITEMS = int(v)
        elif stripped.startswith("max_wait_seconds:"):
            v = stripped.split(":", 1)[1].strip().split("#")[0].strip()
            if v.isdigit(): MAX_WAIT_SECONDS = int(v)
        elif stripped.startswith("speaker_transition_ms:"):
            v = stripped.split(":", 1)[1].strip().split("#")[0].strip()
            if v.isdigit(): SPEAKER_TRANSITION_MS = int(v)
        # interrupt_threshold removed — preemption not implemented
except Exception:
    pass

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

_counter = 0

def _next_id() -> str:
    global _counter
    _counter += 1
    return f"vq-{int(time.time())}-{_counter}"


@dataclass(order=True)
class QueueItem:
    """Item in the priority queue. Higher priority = smaller sort key (plays first)."""
    sort_key: tuple = field(compare=True, repr=False)
    id: str = field(compare=False, default="")
    priority: int = field(compare=False, default=50)
    timestamp: float = field(compare=False, default=0.0)
    agent_id: str = field(compare=False, default="")
    pane_id: str = field(compare=False, default="")
    wav_path: str = field(compare=False, default="")
    volume: float = field(compare=False, default=0.8)
    category: str = field(compare=False, default="tts")

    @staticmethod
    def create(priority: int, agent_id: str, wav_path: str, volume: float,
               pane_id: str = "", category: str = "tts") -> "QueueItem":
        ts = time.time()
        item_id = _next_id()
        # Sort: negative priority (higher = first), then timestamp (earlier = first)
        return QueueItem(
            sort_key=(-priority, ts),
            id=item_id,
            priority=priority,
            timestamp=ts,
            agent_id=agent_id,
            pane_id=pane_id,
            wav_path=wav_path,
            volume=volume,
            category=category,
        )


# ---------------------------------------------------------------------------
# Queue manager
# ---------------------------------------------------------------------------

class VoiceQueue:
    def __init__(self):
        self.heap: list[QueueItem] = []
        self.current: QueueItem | None = None
        self.last_speaker: str = ""
        self.stats = {"processed": 0, "dropped": 0, "expired": 0}

    def enqueue(self, item: QueueItem) -> int:
        """Add item to queue. Returns position."""
        self._expire()
        if len(self.heap) >= MAX_ITEMS:
            # Drop lowest priority (last in heap = highest sort_key = lowest priority)
            if self.heap:
                dropped = heapq.nlargest(1, self.heap)[0]
                self.heap.remove(dropped)
                heapq.heapify(self.heap)
                self.stats["dropped"] += 1
        heapq.heappush(self.heap, item)
        return len(self.heap) - 1

    def get_next(self) -> QueueItem | None:
        """Get next item to play."""
        self._expire()
        if not self.heap:
            return None
        item = heapq.heappop(self.heap)
        self.current = item
        return item

    def complete(self, item_id: str) -> None:
        """Mark current item as done."""
        if self.current and self.current.id == item_id:
            self.last_speaker = self.current.agent_id
            self.stats["processed"] += 1
            self.current = None

    def needs_speaker_transition(self, item: QueueItem) -> bool:
        """Check if different agent is about to speak (needs 300ms pause).

        Returns False when agent_id is empty — unknown speakers don't trigger
        transitions. This prevents an infinite re-push loop when a single
        unnamed item is the only one in the queue.
        """
        if not item.agent_id or not self.last_speaker:
            return False
        return self.last_speaker != item.agent_id

    def _expire(self) -> None:
        """Remove items older than MAX_WAIT_SECONDS."""
        now = time.time()
        before = len(self.heap)
        self.heap = [i for i in self.heap if now - i.timestamp < MAX_WAIT_SECONDS]
        expired = before - len(self.heap)
        if expired:
            heapq.heapify(self.heap)
            self.stats["expired"] += expired


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

# Track previous indicator panes for cleanup
_prev_indicator_panes: list[str] = []

# Config cache — avoids re-reading config.yaml on every indicator update (~70 reads/s)
_config_cache: dict = {}
_config_cache_mtime: float = 0.0

_ICONS = {
    "star": "\U0001f31f", "mic": "\U0001f3a4", "speaker": "\U0001f50a",
    "mute": "\U0001f507", "bubble": "\U0001f4ac", "headphone": "\U0001f3a7",
    "wave": "\U0001f30a", "bell": "\U0001f514", "none": "",
}
_INDICATOR_DEFAULTS = {"speaking": "speaker", "listening": "mic", "queued": "bubble", "muted": "mute"}


def _load_config_cached() -> dict:
    """Load config.yaml with 1-second mtime cache. Returns parsed key-value dict."""
    global _config_cache, _config_cache_mtime
    try:
        mtime = CONFIG_PATH.stat().st_mtime
        if mtime == _config_cache_mtime and _config_cache:
            return _config_cache
        result = {}
        for line in CONFIG_PATH.read_text().splitlines():
            stripped = line.strip()
            if ":" in stripped and not stripped.startswith("#"):
                key, _, val = stripped.partition(":")
                result[key.strip()] = val.strip().split("#")[0].strip()
        _config_cache = result
        _config_cache_mtime = mtime
        return result
    except OSError:
        return _config_cache or {}


def _resolve_indicator(state: str) -> str:
    """Resolve a voice state to its emoji icon from config (cached)."""
    cfg = _load_config_cached()
    icon_name = cfg.get(state, _INDICATOR_DEFAULTS.get(state, ""))
    return _ICONS.get(icon_name, icon_name)


def _is_muted() -> bool:
    """Check if voice is muted via config (cached)."""
    return "true" in _load_config_cached().get("mute", "false").lower()


def _get_focused_pane() -> str:
    """Get the currently focused tmux pane ID."""
    try:
        if FOCUS_STATE_PATH.exists():
            return FOCUS_STATE_PATH.read_text().strip()
    except OSError:
        pass
    return ""


def _update_tmux_voice_indicator(speaking_pane_id: str | None, queue_pane_sizes: dict[str, int] | None = None) -> None:
    """Update tmux @claude_voice pane options for voice state indicators.

    Uses tmux pane options (like claude-tmux's @claude_state) instead of
    renaming windows. The window-status-format reads #{@claude_voice}.

    4 states by priority (highest wins):
      1. Speaking  -> set on the pane that is actively playing TTS
      2. Listening -> set on focused pane when mic is active (stt-active flag)
      3. Queued    -> set on panes with messages waiting to speak
      4. Muted     -> set on all voice-active panes when config mute=true
      (clear)     -> pane option unset when idle
    """
    global _prev_indicator_panes
    try:
        muted = _is_muted()
        stt_active = _stt_is_active()
        focused_pane = _get_focused_pane()

        panes_with_state: dict[str, str] = {}

        # Resolve icons from config (configurable via config.yaml indicators: section)
        icon_speaking = _resolve_indicator("speaking")
        icon_listening = _resolve_indicator("listening")
        icon_queued = _resolve_indicator("queued")
        icon_muted = _resolve_indicator("muted")

        # Base: all panes with active Claude sessions get "voice enabled" (mic)
        # Query tmux for all panes that have @claude_state set
        try:
            _result = subprocess.run(
                ["tmux", "list-panes", "-a", "-F", "#{pane_id}||#{@claude_state}"],
                capture_output=True, text=True, timeout=0.5,
            )
            for _line in _result.stdout.strip().split("\n"):
                _parts = _line.split("||")
                if len(_parts) == 2 and _parts[1].strip():  # has @claude_state = active session
                    _pid = _parts[0].strip()
                    if _pid and _pid != "_global":
                        panes_with_state[_pid] = icon_listening  # mic = voice enabled
        except Exception:
            pass

        # Override with higher-priority states (last write wins, so highest priority last)
        if muted:
            # Muted overrides everything
            for _pid in list(panes_with_state.keys()):
                panes_with_state[_pid] = icon_muted
        else:
            # Queued overrides mic baseline (written first, can be overwritten)
            if queue_pane_sizes:
                for pane_id, count in queue_pane_sizes.items():
                    if pane_id == "_global":
                        continue
                    if count > 0:
                        panes_with_state[pane_id] = icon_queued

            # Speaking wins over everything (written last = highest priority)
            if speaking_pane_id and speaking_pane_id != "_global":
                panes_with_state[speaking_pane_id] = icon_speaking

        # Set/clear @claude_voice on each known pane
        all_panes = set(list(panes_with_state.keys()) + _prev_indicator_panes)
        for pane_id in all_panes:
            if pane_id in panes_with_state:
                subprocess.run(
                    ["tmux", "set", "-p", "-t", pane_id, "@claude_voice", panes_with_state[pane_id]],
                    capture_output=True, timeout=0.2,
                )
            else:
                subprocess.run(
                    ["tmux", "set", "-pu", "-t", pane_id, "@claude_voice"],
                    capture_output=True, timeout=0.2,
                )
        _prev_indicator_panes = list(panes_with_state.keys())

        # Write state file for other consumers
        state = {
            "speaking_pane": speaking_pane_id,
            "stt_active": stt_active,
            "muted": muted,
            "focused_pane": focused_pane,
            "timestamp": time.time(),
        }
        if queue_pane_sizes:
            state["queued"] = {k: v for k, v in queue_pane_sizes.items() if v > 0}
        VOICE_STATE_PATH.write_text(json.dumps(state) + "\n")
    except Exception:
        pass  # Never crash the queue daemon for indicator updates


def _get_queue_pane_sizes(queue) -> dict[str, int]:
    """Get per-pane queue depths."""
    sizes: dict[str, int] = {}
    for item in queue.heap:
        pid = item.pane_id or "_global"
        sizes[pid] = sizes.get(pid, 0) + 1
    return sizes


EVENTS_PATH = Path("~/.claude/local/voice/voice-events.jsonl").expanduser()


def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    line = f"{ts} {msg}\n"
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(line)
    except OSError:
        pass
    sys.stderr.write(line)


def _log_event(event_type: str, **kwargs) -> None:
    """Write structured event to voice-events.jsonl for analytics and KOI sensor."""
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event_type, **kwargs}
    try:
        EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(EVENTS_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Audio Safety
# ---------------------------------------------------------------------------

def _measure_wav_peak(wav_path: str) -> float:
    """Measure peak amplitude of a WAV file. Returns 0.0-1.0."""
    import wave
    import struct
    try:
        with wave.open(wav_path, "r") as w:
            frames = w.readframes(min(w.getnframes(), 480000))  # First 10s max
            count = len(frames) // 2
            if count == 0:
                return 0.0
            samples = struct.unpack(f"<{count}h", frames)
            return max(abs(s) for s in samples) / 32767.0
    except Exception:
        return 0.95  # Assume high peak if measurement fails


def _check_channel_volumes() -> None:
    """Check and auto-fix WirePlumber channelVolumes corruption."""
    import re
    sp = Path("~/.local/state/wireplumber/stream-properties").expanduser()
    try:
        if not sp.exists():
            return
        text = sp.read_text()
        needs_fix = False
        for line in text.splitlines():
            if "Claude" in line or "pw-play" in line or "media.role:Music" in line or "claude-voice" in line:
                match = re.search(r'"channelVolumes":\[([^\]]+)\]', line)
                if match:
                    vols = [float(v.strip()) for v in match.group(1).split(",")]
                    if any(v > 2.0 for v in vols):
                        _log(f"channelVolumes corrupted: {vols} — auto-fixing")
                        needs_fix = True
        if needs_fix:
            lines = text.splitlines()
            out = []
            for line in lines:
                if ("Claude" in line or "pw-play" in line) and "channelVolumes" in line:
                    line = re.sub(
                        r'"channelVolumes":\[[^\]]+\]',
                        '"channelVolumes":[1.000000, 1.000000]',
                        line,
                    )
                out.append(line)
            sp.write_text("\n".join(out) + "\n")
            _log("channelVolumes auto-fixed in stream-properties")
    except Exception as e:
        _log(f"channelVolumes check failed: {e}")


def _safe_volume(wav_path: str, requested_volume: float) -> float:
    """Ensure playback volume won't clip. Returns clamped volume if needed."""
    peak = _measure_wav_peak(wav_path)
    if peak <= 0.0:
        return requested_volume
    max_safe = 0.95 / peak  # Leave 5% headroom
    if requested_volume > max_safe:
        _log(f"gain safety: clamped {requested_volume:.3f} → {max_safe:.3f} (peak={peak:.3f})")
        return max_safe
    return requested_volume


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------

def _play_wav(wav_path: str, volume: float) -> subprocess.Popen | None:
    """Fire-and-forget WAV playback.

    NOTE: No start_new_session — pw-play stays in daemon's process group so
    systemd kills it cleanly on daemon stop. This prevents orphaned playback.
    stderr is captured via PIPE so we can log failures on non-zero exit.
    """
    if not Path(wav_path).exists():
        _log(f"play: file missing {wav_path}")
        return None
    try:
        return subprocess.Popen(
            ["pw-play", f"--volume={volume:.3f}",
             "--target=claude-voice-sink",
             "-P", '{"application.name":"claude-voice"}',
             wav_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except Exception as e:
        _log(f"play: Popen failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

def _run_server() -> None:
    """Main server loop."""
    # Guard against concurrent startup via lock file (fixes race condition)
    import fcntl as _fcntl
    LOCK_PATH = PID_PATH.with_suffix(".lock")
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lockfd = open(str(LOCK_PATH), "w")
    try:
        _fcntl.flock(lockfd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        _log("queue daemon already running (lock held)")
        sys.exit(1)

    # Cleanup stale state from previous run (crash recovery)
    # Kill orphaned pw-play from previous daemon if flag has its PID
    try:
        if TTS_PLAYING_PATH.exists():
            _content = TTS_PLAYING_PATH.read_text().strip().split()
            if _content:
                _old_pid = int(_content[0])
                try:
                    os.kill(_old_pid, signal.SIGKILL)
                    _log(f"startup: killed orphaned pw-play pid {_old_pid}")
                except ProcessLookupError:
                    pass
    except (ValueError, OSError):
        pass
    _clear_flag(TTS_PLAYING_PATH)

    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()

    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    server.listen(10)
    server.settimeout(0.05)  # 50ms poll — fast playback completion detection

    PID_PATH.write_text(str(os.getpid()))
    _log(f"voice queue daemon ready on {SOCKET_PATH} (pid {os.getpid()})")

    global _health_playback_start, _health_last_advance, _health_queue_length, _health_is_playing

    queue = VoiceQueue()
    last_activity = time.monotonic()
    _last_socket_check = time.monotonic()
    playing_proc: subprocess.Popen | None = None
    playback_start_time: float = time.monotonic()  # Watchdog: when current playback began
    last_advance_time: float = time.monotonic()  # Health: when queue last advanced
    transition_until: float = 0.0  # Non-blocking speaker transition
    WATCHDOG_TIMEOUT = 300  # Kill stuck playback after 5min (long TTS can be 2-3min)
    indicator_tick: int = 0  # Counter for 500ms backup poll
    _health_last_advance = last_advance_time

    # Clear any stale tmux indicators from previous crashed daemon
    _update_tmux_voice_indicator(None, {})

    def _shutdown(sig=None, frame=None):
        _log("voice queue daemon shutting down")
        # Kill any active playback
        if playing_proc is not None:
            try:
                playing_proc.kill()
                playing_proc.wait(timeout=1)
            except Exception:
                pass
        # Clear all voice indicators on shutdown
        try:
            _update_tmux_voice_indicator(None, {})
        except Exception:
            pass
        # Clear flags
        _clear_flag(TTS_PLAYING_PATH)
        try:
            server.close()
            SOCKET_PATH.unlink(missing_ok=True)
            PID_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    while True:
        # Idle timeout (0 = disabled, e.g. under systemd)
        if IDLE_TIMEOUT > 0 and time.monotonic() - last_activity > IDLE_TIMEOUT:
            _log(f"idle timeout ({IDLE_TIMEOUT}s)")
            _shutdown()

        # Watchdog: kill stuck playback after WATCHDOG_TIMEOUT seconds
        # Ghost state guard: queue.current set but no playing process
        if playing_proc is None and queue.current is not None:
            _log(f"ghost: queue.current={queue.current.id} with no playing_proc — clearing")
            _log_event("ghost_clear", id=queue.current.id)
            queue.complete(queue.current.id)
            _clear_flag(TTS_PLAYING_PATH)
            _update_tmux_voice_indicator(None, _get_queue_pane_sizes(queue))

        if playing_proc is not None and time.monotonic() - playback_start_time > WATCHDOG_TIMEOUT:
            _log(f"watchdog: killing stuck playback (>{WATCHDOG_TIMEOUT}s)")
            _log_event("watchdog_kill", timeout=WATCHDOG_TIMEOUT)
            try:
                playing_proc.kill()
            except Exception:
                pass
            playing_proc = None
            if queue.current:
                queue.complete(queue.current.id)
            _clear_flag(TTS_PLAYING_PATH)
            _update_tmux_voice_indicator(None, _get_queue_pane_sizes(queue))
            last_advance_time = time.monotonic()
            _health_last_advance = last_advance_time
            _health_is_playing = False

        # Check if current playback finished
        if playing_proc is not None:
            ret = playing_proc.poll()
            if ret is not None:
                # Capture ID before complete() clears it
                item_id = queue.current.id if queue.current else "?"
                if queue.current:
                    queue.complete(queue.current.id)
                if ret != 0:
                    stderr_out = ""
                    try:
                        stderr_out = playing_proc.stderr.read(512).decode("utf-8", errors="replace").strip() if playing_proc.stderr else ""
                    except Exception:
                        pass
                    _log(f"playback failed: {item_id} exit={ret} stderr={stderr_out[:200]}")
                    _log_event("play_failed", id=item_id, exit_code=ret, stderr=stderr_out[:200])
                else:
                    _log(f"playback complete: {item_id}")
                    _log_event("play_complete", id=item_id)
                playing_proc = None
                # Clear TTS-playing flag so STT daemon resumes wake word detection
                _clear_flag(TTS_PLAYING_PATH)
                # Immediate indicator update on state change (not waiting for 500ms poll)
                _update_tmux_voice_indicator(None, _get_queue_pane_sizes(queue))
                last_advance_time = time.monotonic()
                _health_last_advance = last_advance_time
                _health_is_playing = False

        # If nothing playing, no transition pending, and user not speaking, advance queue
        stt_active = _stt_is_active()
        if playing_proc is None and queue.heap and not stt_active and time.monotonic() >= transition_until:
            item = queue.get_next()
            if item:
                # Non-blocking speaker transition
                if queue.needs_speaker_transition(item):
                    transition_until = time.monotonic() + (SPEAKER_TRANSITION_MS / 1000.0)
                    # Re-insert item — will be picked up after transition delay
                    heapq.heappush(queue.heap, item)
                    queue.current = None
                else:
                    # Compute gain chain at dequeue time (uses current config, not enqueue-time)
                    _cfg = _load_config_cached()
                    _chain = _compute_gain_chain(item.category, item.agent_id, _cfg)
                    _play_vol = _chain.get("final", item.volume)
                    # Safety gate: clamp volume if WAV peak × volume would clip
                    _play_vol = _safe_volume(item.wav_path, _play_vol)
                    _log(f"playing: {item.id} [{item.agent_id}] vol={_play_vol:.2f} pri={item.priority} {_chain.get('chain_str', '')}")
                    playing_proc = _play_wav(item.wav_path, _play_vol)
                    # Write pw-play PID to flag (not daemon PID) so health check can detect orphans
                    if playing_proc is not None:
                        try:
                            TTS_PLAYING_PATH.parent.mkdir(parents=True, exist_ok=True)
                            TTS_PLAYING_PATH.write_text(f"{playing_proc.pid} {time.time()}")
                        except OSError:
                            pass
                    playback_start_time = time.monotonic()
                    _health_playback_start = playback_start_time
                    _health_is_playing = playing_proc is not None
                    # Immediate indicator update on playback start (not waiting for 500ms poll)
                    _update_tmux_voice_indicator(item.pane_id, _get_queue_pane_sizes(queue))
                    if playing_proc is None:
                        queue.complete(item.id)
                        _clear_flag(TTS_PLAYING_PATH)
                        _update_tmux_voice_indicator(None, _get_queue_pane_sizes(queue))
                        _health_is_playing = False
                    last_advance_time = time.monotonic()
                    _health_last_advance = last_advance_time

        # Periodic checks (every 5s)
        if time.monotonic() - _last_socket_check > 5.0:
            _last_socket_check = time.monotonic()
            # Auto-fix WirePlumber channelVolumes corruption
            _check_channel_volumes()
            if not SOCKET_PATH.exists():
                _log("socket file missing — rebuilding")
                try:
                    server.close()
                except Exception:
                    pass
                try:
                    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    server.bind(str(SOCKET_PATH))
                    server.listen(10)
                    server.settimeout(0.05)
                    _log("socket rebuilt successfully")
                except Exception as rebuild_err:
                    _log(f"socket rebuild failed: {rebuild_err}")
                # Don't continue — let playback checks and watchdog still run

        # Accept new connections (50ms timeout — fast poll loop)
        try:
            conn, _ = server.accept()
        except socket.timeout:
            # Throttled indicator refresh: every 500ms (10 ticks at 50ms)
            # Backup poll — primary updates happen on state changes above
            indicator_tick += 1
            if indicator_tick >= 10:
                indicator_tick = 0
                current_speaker = queue.current.pane_id if queue.current else None
                _update_tmux_voice_indicator(current_speaker, _get_queue_pane_sizes(queue))
                _health_queue_length = len(queue.heap)
            continue
        except OSError as e:
            _log(f"accept error: {e} — rebuilding socket")
            # Rebuild the server socket (handles deleted socket file, etc.)
            try:
                server.close()
            except Exception:
                pass
            time.sleep(1)  # Brief pause before rebuild
            try:
                if SOCKET_PATH.exists():
                    SOCKET_PATH.unlink()
                server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                server.bind(str(SOCKET_PATH))
                server.listen(10)
                server.settimeout(0.05)
                _log("socket rebuilt successfully")
            except Exception as rebuild_err:
                _log(f"socket rebuild failed: {rebuild_err} — retrying in 5s")
                time.sleep(5)
            continue

        try:
            conn.settimeout(1.0)  # Prevent blocking on malformed clients
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    break

            if buf:
                request = json.loads(buf.split(b"\n")[0])
                response = _handle_request(queue, request)
                conn.sendall((json.dumps(response) + "\n").encode())
                last_activity = time.monotonic()
        except Exception as e:
            _log(f"connection error: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass


    # _process_queue removed — logic is now inline in the server loop
    # to avoid discarded return values and blocking sleep issues


def _handle_request(queue: VoiceQueue, request: dict) -> dict:
    """Handle one client request."""
    global _health_queue_length
    msg_type = request.get("type", "")

    if msg_type == "enqueue":
        item = QueueItem.create(
            priority=request.get("priority", 50),
            agent_id=request.get("agent_id", ""),
            wav_path=request.get("wav_path", ""),
            volume=request.get("volume", 0.8),
            pane_id=request.get("pane_id", ""),
            category=request.get("category", "tts"),
        )
        pos = queue.enqueue(item)
        _health_queue_length = len(queue.heap)
        _log(f"enqueued: {item.id} [{item.agent_id}] pane={item.pane_id} pri={item.priority} pos={pos}")
        _log_event("enqueue", id=item.id, agent_id=item.agent_id, pane_id=item.pane_id, priority=item.priority, category=item.category)
        # Immediate indicator update on enqueue (not waiting for 500ms poll)
        current_speaker = queue.current.pane_id if queue.current else None
        _update_tmux_voice_indicator(current_speaker, _get_queue_pane_sizes(queue))
        return {"type": "queued", "id": item.id, "position": pos}

    elif msg_type == "status":
        return {
            "type": "status",
            "queue_length": len(queue.heap),
            "is_playing": queue.current is not None,
            "stats": queue.stats,
            "health": _get_health(),
        }

    elif msg_type == "shutdown":
        _log("shutdown requested via IPC")
        return {"type": "shutdown_ack"}

    return {"type": "error", "message": f"unknown type: {msg_type}"}


# ---------------------------------------------------------------------------
# Control commands
# ---------------------------------------------------------------------------

def _check() -> None:
    if not SOCKET_PATH.exists():
        print("Queue daemon: not running (no socket)")
        sys.exit(1)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(str(SOCKET_PATH))
            s.sendall(b'{"type":"status"}\n')
            buf = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    break
            resp = json.loads(buf.split(b"\n")[0])
            health = resp.get("health", {})
            warnings = health.get("warnings", [])

            print(f"Queue daemon: running")
            print(f"  queue={resp.get('queue_length', '?')}, playing={resp.get('is_playing', '?')}")
            print(f"  stats={resp.get('stats', {})}")
            if health:
                ok = health.get("ok", True)
                print(f"  health={'OK' if ok else 'WARN'}")
                if health.get("playback_duration_s", 0) > 0:
                    print(f"  playback_duration={health['playback_duration_s']}s")
                if warnings:
                    for w in warnings:
                        print(f"  ⚠ {w}")
            if warnings:
                sys.exit(2)  # Unhealthy but reachable
    except Exception as e:
        print(f"Queue daemon: error ({e})")
        sys.exit(1)


def _stop() -> None:
    if PID_PATH.exists():
        try:
            pid = int(PID_PATH.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            print(f"Queue daemon stopped (pid {pid})")
        except (ValueError, ProcessLookupError):
            print("Queue daemon: stale PID file")
            PID_PATH.unlink(missing_ok=True)
    else:
        print("Queue daemon: not running")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--check" in sys.argv:
        _check()
    elif "--stop" in sys.argv:
        _stop()
    else:
        _run_server()
