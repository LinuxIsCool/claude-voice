#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Voice arbiter daemon — central voice orchestration for Legion's multi-agent tmux environment.

Replaces voice_queue.py with:
  - Per-pane priority queues (messages stay with their pane)
  - 5 voice modes: Ambient, Focused, Solo, Silent, Broadcast
  - 5 gate types: Focus, STT, TTS, Priority, Cooldown
  - SIGSTOP/SIGCONT for TTS pause/resume (not kill-and-restart)
  - Virtual voice pattern: never discard, make virtual, re-promote on focus change
  - Human supremacy: Shawn's mic ALWAYS preempts

State machine per message: CREATED → GATED → QUEUED → PLAYING → PAUSED → COMPLETE

Protocol (wire-compatible with existing queue_client.py):
  Request:  {"type": "enqueue", "wav_path": "...", "priority": 50, "agent_id": "...", "volume": 0.8}
  Response: {"type": "queued", "id": "...", "position": 0}

New commands:
  {"type": "mode", "mode": "focused"}        → switch voice mode
  {"type": "focus", "pane_id": "%42"}         → update focused pane
  {"type": "status"}                          → full arbiter status
  {"type": "drain", "pane_id": "%42"}         → play all queued for pane
  {"type": "skip"}                            → skip current playback
  {"type": "shutdown"}                        → graceful stop

Usage:
    uv run scripts/voice_arbiter.py              # start daemon
    uv run scripts/voice_arbiter.py --check      # check if running
    uv run scripts/voice_arbiter.py --stop       # send stop signal
    uv run scripts/voice_arbiter.py --mode focused  # switch mode

Design: voice-queue-architecture/report.md + unified-synthesis.md + first-principles journal
"""
from __future__ import annotations

import asyncio
import enum
import heapq
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Add lib/ to path for volume and state imports
# ---------------------------------------------------------------------------
_LIB_DIR = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

# ---------------------------------------------------------------------------
# Config paths (SYNC with lib/constants.py)
# ---------------------------------------------------------------------------

VOICE_DIR = Path("~/.claude/local/voice").expanduser()
ARBITER_SOCKET = VOICE_DIR / "arbiter.sock"
ARBITER_PID = VOICE_DIR / "arbiter.pid"
ARBITER_LOG = VOICE_DIR / "arbiter.log"
# Keep backward compat — also listen on old queue.sock for existing clients
LEGACY_SOCKET = VOICE_DIR / "queue.sock"

CONFIG_PATH = VOICE_DIR / "config.yaml"
FOCUS_STATE_PATH = VOICE_DIR / "focus-state"
STT_ACTIVE_PATH = VOICE_DIR / "stt-active"
TTS_PLAYING_PATH = VOICE_DIR / "tts-playing"
MODE_STATE_PATH = VOICE_DIR / "mode-state"
SPEAKING_NOW_PATH = VOICE_DIR / "speaking-now.json"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    line = f"{ts} [arbiter] {msg}\n"
    try:
        ARBITER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(ARBITER_LOG, "a") as f:
            f.write(line)
    except OSError:
        pass
    sys.stderr.write(line)


# ---------------------------------------------------------------------------
# Enums & Data Types
# ---------------------------------------------------------------------------

class Mode(enum.Enum):
    """Voice modes — named policies that determine which gates are active."""
    AMBIENT = "ambient"       # All panes speak. STT + TTS gates only.
    FOCUSED = "focused"       # Only active pane speaks. Others queue silently.
    SOLO = "solo"             # Active pane + immediate preemption on mic.
    SILENT = "silent"         # No TTS at all. Everything queues.
    BROADCAST = "broadcast"   # Only matt-prime speaks. All others route through matt.


class MessageState(enum.Enum):
    QUEUED = "queued"
    PLAYING = "playing"
    PAUSED = "paused"
    COMPLETE = "complete"
    VIRTUAL = "virtual"       # Tracked but not playing (game audio pattern)


class Priority(enum.IntEnum):
    """Message priority levels. Higher = more important."""
    BACKGROUND = 10
    ROUTINE = 20
    NORMAL = 50
    IMPORTANT = 80
    CRITICAL = 100


# ---------------------------------------------------------------------------
# Voice Message
# ---------------------------------------------------------------------------

_counter = 0

def _next_id() -> str:
    global _counter
    _counter += 1
    return f"va-{int(time.time())}-{_counter}"


@dataclass
class VoiceMessage:
    """A single voice message in the arbiter system."""
    id: str
    pane_id: str              # tmux pane ID (e.g., "%42")
    agent_id: str             # persona slug
    wav_path: str             # path to WAV file
    priority: int             # 0-100, higher = more important
    volume: float             # effective volume (already mixed)
    timestamp: float          # when created
    state: MessageState = MessageState.QUEUED
    text: str = ""            # original text (for dedup/logging)
    category: str = "tts"     # sound category for gain chain lookup

    # For heapq: sort by (-priority, timestamp) so higher priority + earlier = first
    def __lt__(self, other: "VoiceMessage") -> bool:
        return (-self.priority, self.timestamp) < (-other.priority, other.timestamp)

    @staticmethod
    def create(
        pane_id: str, agent_id: str, wav_path: str,
        priority: int = 50, volume: float = 0.8, text: str = "",
        category: str = "tts",
    ) -> "VoiceMessage":
        return VoiceMessage(
            id=_next_id(),
            pane_id=pane_id or "_global",
            agent_id=agent_id,
            wav_path=wav_path,
            priority=priority,
            volume=volume,
            timestamp=time.time(),
            text=text,
            category=category,
        )


# ---------------------------------------------------------------------------
# Gate Engine
# ---------------------------------------------------------------------------

class GateEngine:
    """Evaluates whether a queued message may play.

    Gates compose via AND — all active gates must pass.
    Mode determines which gates are active.
    """

    def __init__(self, arbiter: "VoiceArbiter"):
        self.arbiter = arbiter

    def evaluate(self, msg: VoiceMessage) -> bool:
        """Return True if all active gates pass for this message."""
        # Virtual messages must not play — they need explicit promotion first
        if msg.state == MessageState.VIRTUAL:
            return False

        mode = self.arbiter.mode

        # Silent mode: nothing passes
        if mode == Mode.SILENT:
            return False

        # Gate 1: TTS gate — is something already playing?
        if self.arbiter.playing_msg is not None:
            return False

        # Gate 2: STT gate — is Shawn speaking?
        if self._stt_active():
            return False

        # Gate 3: Focus gate (Focused, Solo, Broadcast modes)
        if mode in (Mode.FOCUSED, Mode.SOLO, Mode.BROADCAST):
            if not self._focus_passes(msg):
                # Exception: critical priority bypasses focus gate
                if msg.priority < Priority.CRITICAL:
                    return False

        # Gate 4: Broadcast identity gate
        if mode == Mode.BROADCAST:
            # Only matt-prime (or messages with agent_id containing "matt") speak
            if "matt" not in msg.agent_id.lower() and msg.priority < Priority.CRITICAL:
                return False

        # Gate 5: Cooldown gate
        if not self._cooldown_passes():
            return False

        return True

    def _stt_active(self) -> bool:
        """Check if Shawn is currently speaking."""
        try:
            if STT_ACTIVE_PATH.exists():
                # Check staleness (>120s = stale flag)
                age = time.time() - STT_ACTIVE_PATH.stat().st_mtime
                return age < 120
        except OSError:
            pass
        return False

    def _focus_passes(self, msg: VoiceMessage) -> bool:
        """Check if message's pane is the focused pane."""
        focused = self.arbiter.focused_pane
        if not focused:
            return True  # No focus info = pass (fail open)
        # _global pane messages always pass focus gate
        if msg.pane_id == "_global":
            return True
        return msg.pane_id == focused

    def _cooldown_passes(self) -> bool:
        """Check if enough time has passed since last playback."""
        if self.arbiter.last_complete_time == 0:
            return True
        elapsed = time.time() - self.arbiter.last_complete_time
        cooldown = self.arbiter.cooldown_ms / 1000.0
        return elapsed >= cooldown


# ---------------------------------------------------------------------------
# Per-Pane Queue
# ---------------------------------------------------------------------------

class PaneQueue:
    """Priority queue for a single tmux pane."""

    def __init__(self, pane_id: str):
        self.pane_id = pane_id
        self.heap: list[VoiceMessage] = []
        self.stats = {"enqueued": 0, "played": 0, "expired": 0, "virtual": 0}
        self.max_items = 50
        self.max_age_seconds = 300  # 5 minutes (configurable)

    def enqueue(self, msg: VoiceMessage) -> int:
        """Add message to queue. Returns position."""
        self._expire()
        # Overflow: drop lowest priority
        if len(self.heap) >= self.max_items:
            dropped = heapq.nlargest(1, self.heap)
            if dropped:
                self.heap.remove(dropped[0])
                heapq.heapify(self.heap)
        heapq.heappush(self.heap, msg)
        self.stats["enqueued"] += 1
        return len(self.heap) - 1

    def peek(self) -> Optional[VoiceMessage]:
        """Look at next message without removing."""
        self._expire()
        return self.heap[0] if self.heap else None

    def pop(self) -> Optional[VoiceMessage]:
        """Remove and return highest-priority message."""
        self._expire()
        if not self.heap:
            return None
        msg = heapq.heappop(self.heap)
        self.stats["played"] += 1
        return msg

    def virtualize_all(self) -> int:
        """Mark all messages as virtual (tracked, not playing)."""
        count = 0
        for msg in self.heap:
            if msg.state == MessageState.QUEUED:
                msg.state = MessageState.VIRTUAL
                count += 1
        self.stats["virtual"] += count
        return count

    def promote_all(self) -> int:
        """Promote all virtual messages back to queued."""
        count = 0
        for msg in self.heap:
            if msg.state == MessageState.VIRTUAL:
                msg.state = MessageState.QUEUED
                count += 1
        return count

    def _expire(self) -> None:
        """Remove messages older than max_age_seconds."""
        now = time.time()
        before = len(self.heap)
        self.heap = [m for m in self.heap if now - m.timestamp < self.max_age_seconds]
        expired = before - len(self.heap)
        if expired:
            heapq.heapify(self.heap)
            self.stats["expired"] += expired

    @property
    def length(self) -> int:
        return len(self.heap)


# ---------------------------------------------------------------------------
# Voice Arbiter (core daemon)
# ---------------------------------------------------------------------------

class VoiceArbiter:
    """Central voice orchestration daemon.

    Manages per-pane queues, gate evaluation, mode switching,
    and TTS playback coordination.
    """

    def __init__(self):
        # Mode
        self.mode: Mode = Mode.AMBIENT
        self._load_mode()

        # Focus
        self.focused_pane: str = ""
        self._load_focus()

        # Queues: pane_id -> PaneQueue
        self.panes: dict[str, PaneQueue] = {}

        # Playback state
        self.playing_msg: Optional[VoiceMessage] = None
        self.playing_proc: Optional[asyncio.subprocess.Process] = None
        self.last_speaker: str = ""
        self.last_complete_time: float = 0
        self.cooldown_ms: int = 300  # configurable

        # Speaker transition pause
        self.transition_until: float = 0

        # Gate engine
        self.gates = GateEngine(self)

        # Stats
        self.stats = {
            "total_enqueued": 0,
            "total_played": 0,
            "total_paused": 0,
            "total_skipped": 0,
            "mode_switches": 0,
            "focus_changes": 0,
            "stt_preemptions": 0,
            "started_at": time.time(),
        }

        # Load config
        self._load_config()

    def _load_config(self) -> None:
        """Load arbiter config from config.yaml.

        Called at init (before any panes exist) and sets daemon-level
        config. Per-pane max_age is applied in get_pane_queue() at
        pane creation time, reading the same config value.
        """
        self._config_max_age = 300  # default 5 min
        try:
            text = CONFIG_PATH.read_text()
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("max_wait_seconds:"):
                    v = stripped.split(":", 1)[1].strip().split("#")[0].strip()
                    if v.isdigit():
                        self._config_max_age = int(v)
                elif stripped.startswith("speaker_transition_ms:"):
                    v = stripped.split(":", 1)[1].strip().split("#")[0].strip()
                    if v.isdigit():
                        self.cooldown_ms = int(v)
        except Exception:
            pass

    def _load_mode(self) -> None:
        """Load persisted mode state."""
        try:
            if MODE_STATE_PATH.exists():
                mode_str = MODE_STATE_PATH.read_text().strip().lower()
                self.mode = Mode(mode_str)
        except (ValueError, OSError):
            self.mode = Mode.AMBIENT

    def _save_mode(self) -> None:
        """Persist mode state."""
        try:
            MODE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            MODE_STATE_PATH.write_text(self.mode.value)
        except OSError:
            pass

    def _load_focus(self) -> None:
        """Load current focus state from file."""
        try:
            if FOCUS_STATE_PATH.exists():
                self.focused_pane = FOCUS_STATE_PATH.read_text().strip()
        except OSError:
            pass

    def get_pane_queue(self, pane_id: str) -> PaneQueue:
        """Get or create a pane queue."""
        if pane_id not in self.panes:
            pq = PaneQueue(pane_id)
            pq.max_age_seconds = self._config_max_age
            self.panes[pane_id] = pq
        return self.panes[pane_id]

    def enqueue(self, msg: VoiceMessage) -> int:
        """Enqueue a voice message into its pane's queue."""
        pq = self.get_pane_queue(msg.pane_id)
        pos = pq.enqueue(msg)
        self.stats["total_enqueued"] += 1
        _log(f"enqueued: {msg.id} pane={msg.pane_id} [{msg.agent_id}] pri={msg.priority} pos={pos}")
        # Update tmux indicators — show queue count on this pane
        self.update_tmux_indicators()
        return pos

    def set_mode(self, new_mode: Mode) -> str:
        """Switch voice mode. Returns status message."""
        old = self.mode
        self.mode = new_mode
        self.stats["mode_switches"] += 1
        self._save_mode()

        # Mode transition effects
        if new_mode == Mode.SILENT:
            total = 0
            for pq in self.panes.values():
                total += pq.virtualize_all()
            _log(f"mode: {old.value} -> {new_mode.value} (virtualized {total} messages)")

        elif old == Mode.SILENT:
            total = 0
            for pq in self.panes.values():
                total += pq.promote_all()
            _log(f"mode: {old.value} -> {new_mode.value} (promoted {total} virtual messages)")

        elif new_mode == Mode.AMBIENT and old == Mode.FOCUSED:
            total = 0
            for pq in self.panes.values():
                total += pq.promote_all()
            _log(f"mode: {old.value} -> {new_mode.value} (promoted {total} virtual messages)")

        else:
            _log(f"mode: {old.value} -> {new_mode.value}")

        return f"mode switched: {old.value} -> {new_mode.value}"

    def set_focus(self, pane_id: str) -> str:
        """Update focused pane. Triggers queue re-evaluation."""
        old = self.focused_pane
        self.focused_pane = pane_id
        self.stats["focus_changes"] += 1

        # Write focus state file for other components
        try:
            FOCUS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            FOCUS_STATE_PATH.write_text(pane_id)
        except OSError:
            pass

        # In focused mode: virtualize old pane, promote new pane
        if self.mode in (Mode.FOCUSED, Mode.SOLO, Mode.BROADCAST):
            if old and old in self.panes:
                self.panes[old].virtualize_all()
            if pane_id in self.panes:
                self.panes[pane_id].promote_all()

        _log(f"focus: {old or '(none)'} -> {pane_id}")
        return f"focus changed: {old or '(none)'} -> {pane_id}"

    async def try_advance(self) -> bool:
        """Try to start playing the next eligible message.

        Returns True if playback started.
        """
        if self.playing_msg is not None:
            return False

        if time.time() < self.transition_until:
            return False

        # Determine which panes to consider based on mode
        if self.mode in (Mode.FOCUSED, Mode.SOLO):
            candidates = self._gather_focused_candidates()
        elif self.mode == Mode.BROADCAST:
            candidates = self._gather_broadcast_candidates()
        elif self.mode == Mode.SILENT:
            return False
        else:
            candidates = self._gather_all_candidates()

        if not candidates:
            return False

        candidates.sort(key=lambda m: (-m.priority, m.timestamp))

        for msg in candidates:
            if self.gates.evaluate(msg):
                # Speaker transition delay — check BEFORE removing from heap.
                # Leave msg in place; transition_until gates the whole function
                # on the next tick, preventing livelock.
                if self.last_speaker and self.last_speaker != msg.agent_id:
                    self.transition_until = time.time() + (self.cooldown_ms / 1000.0)
                    return False

                # Remove from heap and play
                pq = self.get_pane_queue(msg.pane_id)
                if msg in pq.heap:
                    pq.heap.remove(msg)
                    heapq.heapify(pq.heap)

                await self._start_playback(msg)
                return True

        return False

    def _gather_all_candidates(self) -> list[VoiceMessage]:
        candidates = []
        for pq in self.panes.values():
            msg = pq.peek()
            if msg and msg.state in (MessageState.QUEUED, MessageState.VIRTUAL):
                candidates.append(msg)
        return candidates

    def _gather_focused_candidates(self) -> list[VoiceMessage]:
        candidates = []
        for pane_id, pq in self.panes.items():
            msg = pq.peek()
            if not msg or msg.state not in (MessageState.QUEUED, MessageState.VIRTUAL):
                continue
            if pane_id == self.focused_pane or pane_id == "_global":
                candidates.append(msg)
            elif msg.priority >= Priority.CRITICAL:
                candidates.append(msg)
        return candidates

    def _gather_broadcast_candidates(self) -> list[VoiceMessage]:
        candidates = []
        for pq in self.panes.values():
            msg = pq.peek()
            if not msg or msg.state not in (MessageState.QUEUED, MessageState.VIRTUAL):
                continue
            if "matt" in msg.agent_id.lower() or msg.priority >= Priority.CRITICAL:
                candidates.append(msg)
        return candidates

    async def _start_playback(self, msg: VoiceMessage) -> None:
        """Start playing a voice message via pw-play.

        Volume is calculated HERE at playback time (not enqueue time)
        using the four-stage gain chain. Config is hot-reloaded so
        F9/F10 changes take effect on the next playback.
        """
        if not Path(msg.wav_path).exists():
            _log(f"skip (missing): {msg.id} {msg.wav_path}")
            self.stats["total_skipped"] += 1
            return

        msg.state = MessageState.PLAYING
        self.playing_msg = msg
        self.stats["total_played"] += 1

        self._write_flag(TTS_PLAYING_PATH)

        # Calculate volume at playback time via gain chain
        try:
            from volume import compute_gain_chain, policy_vol_for_mode
            from state import load_config
            config = load_config()
            policy = policy_vol_for_mode(
                self.mode.value, msg.pane_id, self.focused_pane, msg.agent_id
            )
            chain = compute_gain_chain(msg.category, msg.agent_id, config, policy)
            play_vol = chain["final"]
            _log(f"playing: {msg.id} pane={msg.pane_id} [{msg.agent_id}] {chain['chain_str']}")
        except Exception as e:
            # Fallback: use legacy volume from enqueue request
            play_vol = msg.volume
            _log(f"playing: {msg.id} pane={msg.pane_id} [{msg.agent_id}] vol={play_vol:.2f} (fallback: {e})")

        try:
            self.playing_proc = await asyncio.create_subprocess_exec(
                "pw-play", f"--volume={play_vol:.3f}",
                "--target=claude-voice-sink",
                "-P", '{"application.name":"claude-voice"}',
                msg.wav_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )  # Same sink/flags as voice_queue._play_wav for consistent gain staging
            self._write_speaking_now(msg.pane_id)
            # Update tmux indicators — show speaking icon on this pane
            self.update_tmux_indicators()
        except Exception as e:
            _log(f"playback error: {e}")
            self._complete_current()

    async def check_playback(self) -> None:
        """Check if current playback finished."""
        if self.playing_proc is None:
            return
        if self.playing_proc.returncode is not None:
            self._complete_current()

    async def pause_for_stt(self) -> None:
        """Pause current TTS because Shawn started speaking.

        Uses SIGSTOP for true pause. Note: SIGSTOP holds the PipeWire stream
        open, which may cause buffer xruns on other audio sources. If the
        pause exceeds PAUSE_TIMEOUT_SECONDS, we kill instead of resuming
        to avoid prolonged PipeWire stalls.
        """
        if self.playing_proc is None or self.playing_msg is None:
            return

        pid = self.playing_proc.pid
        if pid is None:
            return

        try:
            os.kill(pid, signal.SIGSTOP)
            self.playing_msg.state = MessageState.PAUSED
            self._pause_started = time.time()
            self.stats["stt_preemptions"] += 1
            _log(f"paused (STT): {self.playing_msg.id} pid={pid}")
        except ProcessLookupError:
            self._complete_current()
        except OSError as e:
            _log(f"SIGSTOP failed: {e}")

    async def resume_from_stt(self) -> None:
        """Resume paused TTS after Shawn stops speaking. Uses SIGCONT.

        If the pause exceeded 10 seconds, kills instead of resuming
        to avoid prolonged PipeWire stream stalls.
        """
        if self.playing_proc is None or self.playing_msg is None:
            return
        if self.playing_msg.state != MessageState.PAUSED:
            return

        pid = self.playing_proc.pid
        if pid is None:
            return

        # If paused too long, kill instead of resume (PipeWire health)
        pause_duration = time.time() - getattr(self, "_pause_started", time.time())
        if pause_duration > 10.0:
            _log(f"pause too long ({pause_duration:.1f}s), killing: {self.playing_msg.id} pid={pid}")
            try:
                os.kill(pid, signal.SIGCONT)  # Must SIGCONT before SIGTERM
                self.playing_proc.terminate()
            except ProcessLookupError:
                pass
            self._complete_current()
            return

        try:
            os.kill(pid, signal.SIGCONT)
            self.playing_msg.state = MessageState.PLAYING
            _log(f"resumed: {self.playing_msg.id} pid={pid}")
        except ProcessLookupError:
            self._complete_current()
        except OSError as e:
            _log(f"SIGCONT failed: {e}")

    async def skip_current(self) -> str:
        """Skip current playback.

        Guards against double-completion race: the main loop may call
        _complete_current() while we're awaiting process.wait().
        We capture the message ref and only complete if it hasn't changed.
        """
        if self.playing_proc is None:
            return "nothing playing"

        # Capture refs before await (main loop may complete during wait)
        target_msg = self.playing_msg
        target_proc = self.playing_proc
        msg_id = target_msg.id if target_msg else "?"

        try:
            target_proc.terminate()
            await asyncio.wait_for(target_proc.wait(), timeout=2)
        except Exception:
            try:
                target_proc.kill()
            except Exception:
                pass

        # Only complete if main loop hasn't already done it
        if self.playing_msg is target_msg and target_msg is not None:
            self._complete_current()
            self.stats["total_skipped"] += 1

        _log(f"skipped: {msg_id}")
        return f"skipped: {msg_id}"

    def _complete_current(self) -> None:
        """Mark current playback as complete."""
        if self.playing_msg:
            self.last_speaker = self.playing_msg.agent_id
            self.playing_msg.state = MessageState.COMPLETE
            self.playing_msg = None
        self.playing_proc = None
        self.last_complete_time = time.time()
        self._clear_flag(TTS_PLAYING_PATH)
        self._write_speaking_now(None)
        # Update tmux indicators — clear speaking icon, update queue counts
        self.update_tmux_indicators()

    def _write_speaking_now(self, pane_id: str | None) -> None:
        """Write speaking-now.json so indicator pollers and other consumers stay in sync."""
        try:
            import json as _json
            SPEAKING_NOW_PATH.parent.mkdir(parents=True, exist_ok=True)
            SPEAKING_NOW_PATH.write_text(_json.dumps({
                "speaking_pane": pane_id,
                "timestamp": time.time(),
            }))
        except Exception:
            pass

    def prune_stale_panes(self) -> int:
        """Remove empty pane queues that have had no activity for max_age_seconds."""
        stale = []
        for pane_id, pq in self.panes.items():
            if pq.length == 0 and pane_id != self.focused_pane:
                # Check if last enqueue was long ago via stats
                if pq.stats["enqueued"] > 0:
                    stale.append(pane_id)
        for pane_id in stale:
            del self.panes[pane_id]
        if stale:
            _log(f"pruned {len(stale)} stale pane queues: {stale}")
        return len(stale)

    def get_status(self) -> dict:
        """Full arbiter status including volume info."""
        pane_status = {}
        for pane_id, pq in self.panes.items():
            pane_status[pane_id] = {
                "queue_length": pq.length,
                "stats": pq.stats,
            }

        # Load volume info
        volume_info = {}
        try:
            from state import load_config
            config = load_config()
            volume_info = {
                "master": config.get("volume", 0.7),
                "system_gain": config.get("system_gain", 3.5),
                "agent_volumes": config.get("agent_volumes", {}),
                "categories": config.get("categories", {}),
            }
        except Exception:
            pass

        return {
            "type": "status",
            "mode": self.mode.value,
            "focused_pane": self.focused_pane,
            "is_playing": self.playing_msg is not None,
            "current": {
                "id": self.playing_msg.id,
                "pane": self.playing_msg.pane_id,
                "agent": self.playing_msg.agent_id,
                "state": self.playing_msg.state.value,
            } if self.playing_msg else None,
            "panes": pane_status,
            "total_queued": sum(pq.length for pq in self.panes.values()),
            "volume": volume_info,
            "stats": self.stats,
            "uptime_seconds": int(time.time() - self.stats["started_at"]),
        }

    # ── Tmux voice indicators ──────────────────────────────────────────
    # Uses @claude_voice pane option (like @claude_state in claude-tmux).
    # Format string reads #{@claude_voice} to show icons in status bar.

    def update_tmux_indicators(self) -> None:
        """Update tmux pane options and state file for voice indicators.

        Sets @claude_voice on each pane:
          - Playing pane gets the speaking icon
          - Panes with queued messages get the queue icon with count
          - Empty panes get cleared

        Also writes speaking-now.json for other consumers.
        """
        try:
            self._update_tmux_indicators_inner()
        except Exception:
            pass  # Never crash for indicator updates

    def _is_muted(self) -> bool:
        """Check if voice is muted via config."""
        try:
            text = CONFIG_PATH.read_text()
            for line in text.splitlines():
                if line.strip().startswith("mute:"):
                    return "true" in line.lower()
        except OSError:
            pass
        return False

    @staticmethod
    def _resolve_indicator(state: str) -> str:
        """Resolve a voice state name to its emoji from config.

        Reads config.yaml indicators section, falls back to defaults.
        Icon names map to emoji via a built-in registry.
        """
        _ICONS = {
            "star": "\U0001f31f", "mic": "\U0001f3a4", "speaker": "\U0001f50a",
            "mute": "\U0001f507", "bubble": "\U0001f4ac", "headphone": "\U0001f3a7",
            "wave": "\U0001f30a", "bell": "\U0001f514", "none": "",
        }
        _DEFAULTS = {"speaking": "star", "listening": "mic", "queued": "mic", "muted": "mute"}
        icon_name = _DEFAULTS.get(state, "")
        try:
            for line in CONFIG_PATH.read_text().splitlines():
                stripped = line.strip()
                if stripped.startswith(f"{state}:"):
                    icon_name = stripped.split(":", 1)[1].strip().split("#")[0].strip()
                    break
        except OSError:
            pass
        return _ICONS.get(icon_name, icon_name)

    def _update_tmux_indicators_inner(self) -> None:
        """Update tmux @claude_voice pane options with 4-state voice indicators.

        Priority (highest wins):
          1. Speaking  (actively playing TTS)
          2. Listening (mic active on focused pane)
          3. Queued    (messages waiting)
          4. Muted     (config mute=true, replaces all above)
        """
        import subprocess as _sp

        speaking_pane = self.playing_msg.pane_id if self.playing_msg else None
        muted = self._is_muted()
        stt_active = self.gates._stt_active()
        focused = self.focused_pane

        panes_with_state: dict[str, str] = {}

        # Resolve icons from config (configurable via config.yaml indicators: section)
        icon_speaking = self._resolve_indicator("speaking")
        icon_listening = self._resolve_indicator("listening")
        icon_queued = self._resolve_indicator("queued")
        icon_muted = self._resolve_indicator("muted")

        # Base: all panes with active Claude sessions get "voice enabled" (mic)
        try:
            import subprocess as _sp2
            _result = _sp2.run(
                ["tmux", "list-panes", "-a", "-F", "#{pane_id}||#{@claude_state}"],
                capture_output=True, text=True, timeout=0.5,
            )
            for _line in _result.stdout.strip().split("\n"):
                _parts = _line.split("||")
                if len(_parts) == 2 and _parts[1].strip():
                    _pid = _parts[0].strip()
                    if _pid and _pid != "_global":
                        panes_with_state[_pid] = icon_listening  # mic = voice enabled
        except Exception:
            pass

        # Override with higher-priority states
        if muted:
            for _pid in list(panes_with_state.keys()):
                panes_with_state[_pid] = icon_muted
        else:
            # Priority 1: Speaking (highest) overrides mic
            if speaking_pane and speaking_pane != "_global":
                panes_with_state[speaking_pane] = icon_speaking

            # Priority 2: Queued overrides mic (but NOT if that pane is already speaking)
            for pane_id, pq in self.panes.items():
                if pane_id == "_global":
                    continue
                if pane_id == speaking_pane:
                    continue  # speaking takes priority — don't clobber with queued
                if pq.length > 0:
                    panes_with_state[pane_id] = icon_queued

        # Set/clear @claude_voice on each known pane
        for pane_id in set(list(panes_with_state.keys()) + list(getattr(self, "_prev_indicator_panes", []))):
            if pane_id in panes_with_state:
                _sp.run(
                    ["tmux", "set", "-p", "-t", pane_id, "@claude_voice", panes_with_state[pane_id]],
                    capture_output=True, timeout=0.2,
                )
            else:
                _sp.run(
                    ["tmux", "set", "-pu", "-t", pane_id, "@claude_voice"],
                    capture_output=True, timeout=0.2,
                )

        self._prev_indicator_panes = list(panes_with_state.keys())

        # Write state file for other consumers
        try:
            state = {
                "speaking_pane": speaking_pane,
                "speaking_agent": self.playing_msg.agent_id if self.playing_msg else "",
                "stt_active": stt_active,
                "muted": muted,
                "mode": self.mode.value,
                "focused_pane": focused,
                "timestamp": time.time(),
                "queued": {pid: pq.length for pid, pq in self.panes.items() if pq.length > 0},
            }
            SPEAKING_NOW_PATH.write_text(json.dumps(state) + "\n")
        except OSError:
            pass

    # ── Flag file helpers ────────────────────────────────────────────

    @staticmethod
    def _write_flag(path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(os.getpid()))
        except OSError:
            pass

    @staticmethod
    def _clear_flag(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Async Server
# ---------------------------------------------------------------------------

class ArbiterServer:
    """Async Unix socket server for the voice arbiter."""

    def __init__(self):
        self.arbiter = VoiceArbiter()
        self._running = False
        self._stt_was_active = False

    async def start(self) -> None:
        """Start the arbiter server."""
        VOICE_DIR.mkdir(parents=True, exist_ok=True)

        # Cleanup stale state
        self.arbiter._clear_flag(TTS_PLAYING_PATH)
        for sock_path in (ARBITER_SOCKET, LEGACY_SOCKET):
            if sock_path.exists():
                sock_path.unlink()

        ARBITER_PID.write_text(str(os.getpid()))
        self._running = True

        server_new = await asyncio.start_unix_server(
            self._handle_client, path=str(ARBITER_SOCKET)
        )
        server_legacy = await asyncio.start_unix_server(
            self._handle_client, path=str(LEGACY_SOCKET)
        )

        _log(f"voice arbiter ready (pid {os.getpid()}) mode={self.arbiter.mode.value}")
        _log(f"  sockets: {ARBITER_SOCKET}, {LEGACY_SOCKET}")

        # Setup signal handlers now that we have a running loop
        _setup_signals(self)

        try:
            async with server_new, server_legacy:
                await self._main_loop()
        finally:
            self._shutdown()

    async def _main_loop(self) -> None:
        """Main event loop — 50ms tick for playback monitoring."""
        _prune_counter = 0
        while self._running:
            # Check if current playback finished
            if self.arbiter.playing_proc is not None:
                ret = self.arbiter.playing_proc.returncode
                if ret is not None:
                    msg_id = self.arbiter.playing_msg.id if self.arbiter.playing_msg else "?"
                    self.arbiter._complete_current()
                    _log(f"playback complete: {msg_id}")

            # Monitor STT state
            stt_now = self.arbiter.gates._stt_active()
            if stt_now and not self._stt_was_active:
                await self.arbiter.pause_for_stt()
            elif not stt_now and self._stt_was_active:
                await self.arbiter.resume_from_stt()
            self._stt_was_active = stt_now

            # Try to advance queue
            if not stt_now:
                await self.arbiter.try_advance()

            # Focus state polling (tmux hook writes this, poll as backup)
            try:
                if FOCUS_STATE_PATH.exists():
                    new_focus = FOCUS_STATE_PATH.read_text().strip()
                    if new_focus != self.arbiter.focused_pane:
                        self.arbiter.set_focus(new_focus)
            except OSError:
                pass

            # Prune stale pane queues every 60s (1200 ticks at 50ms)
            _prune_counter += 1
            if _prune_counter >= 1200:
                _prune_counter = 0
                self.arbiter.prune_stale_panes()

            # Throttled indicator refresh every 500ms (10 ticks)
            # Updates mic-active and muted states between playback events
            if _prune_counter % 10 == 0:
                self.arbiter.update_tmux_indicators()

            await asyncio.sleep(0.05)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle one client connection."""
        try:
            data = await asyncio.wait_for(reader.readline(), timeout=5)
            if not data:
                return

            request = json.loads(data.decode("utf-8"))
            response = await self._dispatch(request)

            writer.write((json.dumps(response) + "\n").encode("utf-8"))
            await writer.drain()
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            try:
                err = json.dumps({"type": "error", "message": str(e)})
                writer.write((err + "\n").encode("utf-8"))
                await writer.drain()
            except Exception:
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _dispatch(self, request: dict) -> dict:
        """Route a client request to the appropriate handler."""
        msg_type = request.get("type", "")

        if msg_type == "enqueue":
            return self._handle_enqueue(request)
        elif msg_type == "mode":
            return self._handle_mode(request)
        elif msg_type == "focus":
            return self._handle_focus(request)
        elif msg_type == "status":
            return self.arbiter.get_status()
        elif msg_type == "drain":
            return self._handle_drain(request)
        elif msg_type == "skip":
            result = await self.arbiter.skip_current()
            return {"type": "skip_ack", "message": result}
        elif msg_type == "agent_volume":
            return self._handle_agent_volume(request)
        elif msg_type == "shutdown":
            _log("shutdown requested via IPC")
            self._running = False
            return {"type": "shutdown_ack"}
        else:
            return {"type": "error", "message": f"unknown type: {msg_type}"}

    def _handle_enqueue(self, request: dict) -> dict:
        """Handle enqueue request — wire-compatible with queue_client.py."""
        pane_id = request.get("pane_id", "") or "_global"

        msg = VoiceMessage.create(
            pane_id=pane_id,
            agent_id=request.get("agent_id", ""),
            wav_path=request.get("wav_path", ""),
            priority=request.get("priority", 50),
            volume=request.get("volume", 0.8),
            text=request.get("text", ""),
            category=request.get("category", "tts"),
        )
        pos = self.arbiter.enqueue(msg)
        return {"type": "queued", "id": msg.id, "position": pos}

    def _handle_mode(self, request: dict) -> dict:
        """Handle mode switch request."""
        mode_str = request.get("mode", "").lower()
        try:
            new_mode = Mode(mode_str)
        except ValueError:
            valid = [m.value for m in Mode]
            return {"type": "error", "message": f"invalid mode: {mode_str}. Valid: {valid}"}

        result = self.arbiter.set_mode(new_mode)
        return {"type": "mode_ack", "message": result, "mode": new_mode.value}

    def _handle_focus(self, request: dict) -> dict:
        """Handle focus change notification."""
        pane_id = request.get("pane_id", "")
        if not pane_id:
            return {"type": "error", "message": "pane_id required"}
        result = self.arbiter.set_focus(pane_id)
        return {"type": "focus_ack", "message": result}

    def _handle_agent_volume(self, request: dict) -> dict:
        """Handle per-agent volume change. Writes to config.yaml."""
        agent_id = request.get("agent_id", "")
        if not agent_id:
            return {"type": "error", "message": "agent_id required"}
        vol = max(0.0, min(1.0, float(request.get("volume", 1.0))))
        try:
            from state import load_config, save_config
            config = load_config()
            if "agent_volumes" not in config or not isinstance(config["agent_volumes"], dict):
                config["agent_volumes"] = {"_default": 1.0}
            if vol >= 1.0:
                # Remove override (default is 1.0)
                config["agent_volumes"].pop(agent_id, None)
            else:
                config["agent_volumes"][agent_id] = vol
            save_config(config)
            _log(f"agent_volume: {agent_id} = {vol:.2f}")
            return {"type": "agent_volume_ack", "agent_id": agent_id, "volume": vol}
        except Exception as e:
            return {"type": "error", "message": f"agent_volume failed: {e}"}

    def _handle_drain(self, request: dict) -> dict:
        """Handle drain request — play all queued messages for a pane."""
        pane_id = request.get("pane_id", "")
        if not pane_id:
            return {"type": "error", "message": "pane_id required"}
        pq = self.arbiter.panes.get(pane_id)
        if not pq:
            return {"type": "drain_ack", "count": 0}
        promoted = pq.promote_all()
        return {"type": "drain_ack", "count": promoted + pq.length, "pane_id": pane_id}

    def _shutdown(self) -> None:
        """Cleanup on shutdown."""
        _log("voice arbiter shutting down")
        try:
            ARBITER_SOCKET.unlink(missing_ok=True)
            LEGACY_SOCKET.unlink(missing_ok=True)
            ARBITER_PID.unlink(missing_ok=True)
            self.arbiter._clear_flag(TTS_PLAYING_PATH)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------

def _setup_signals(server: ArbiterServer) -> None:
    """Setup signal handlers for graceful shutdown."""
    loop = asyncio.get_running_loop()

    def handle_shutdown(sig):
        _log(f"received signal {sig}")
        server._running = False

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_shutdown, sig)


# ---------------------------------------------------------------------------
# CLI Commands
# ---------------------------------------------------------------------------

def _check() -> None:
    """Check if arbiter is running."""
    sock_path = ARBITER_SOCKET if ARBITER_SOCKET.exists() else LEGACY_SOCKET
    if not sock_path.exists():
        print("Voice arbiter: not running (no socket)")
        sys.exit(1)
    try:
        import socket as _socket
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(str(sock_path))
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
            mode = resp.get("mode", "?")
            queued = resp.get("total_queued", "?")
            playing = resp.get("is_playing", False)
            panes = len(resp.get("panes", {}))
            uptime = resp.get("uptime_seconds", 0)
            print(f"Voice arbiter: running (mode={mode}, queued={queued}, "
                  f"playing={playing}, panes={panes}, uptime={uptime}s)")
    except Exception as e:
        print(f"Voice arbiter: error ({e})")
        sys.exit(1)


def _stop() -> None:
    """Stop the arbiter."""
    if ARBITER_PID.exists():
        try:
            pid = int(ARBITER_PID.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            print(f"Voice arbiter stopped (pid {pid})")
        except (ValueError, ProcessLookupError):
            print("Voice arbiter: stale PID file")
            ARBITER_PID.unlink(missing_ok=True)
    else:
        print("Voice arbiter: not running")


def _set_mode(mode_str: str) -> None:
    """Switch arbiter mode via CLI."""
    sock_path = ARBITER_SOCKET if ARBITER_SOCKET.exists() else LEGACY_SOCKET
    if not sock_path.exists():
        print("Voice arbiter: not running")
        sys.exit(1)
    try:
        import socket as _socket
        request = json.dumps({"type": "mode", "mode": mode_str})
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(str(sock_path))
            s.sendall((request + "\n").encode())
            buf = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    break
            resp = json.loads(buf.split(b"\n")[0])
            print(resp.get("message", resp))
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if "--check" in sys.argv:
        _check()
    elif "--stop" in sys.argv:
        _stop()
    elif "--mode" in sys.argv:
        idx = sys.argv.index("--mode")
        if idx + 1 < len(sys.argv):
            _set_mode(sys.argv[idx + 1])
        else:
            print("Usage: voice_arbiter.py --mode <ambient|focused|solo|silent|broadcast>")
            sys.exit(1)
    else:
        # Start daemon with flock guard
        import fcntl
        LOCK_PATH = ARBITER_PID.with_suffix(".lock")
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Hold lock for process lifetime (NOT in a with block — must survive asyncio.run)
        lockfd = open(str(LOCK_PATH), "w")
        try:
            fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            _log("arbiter already running (lock held)")
            print("Voice arbiter: already running")
            lockfd.close()
            sys.exit(1)

        try:
            server = ArbiterServer()
            asyncio.run(server.start())
        finally:
            lockfd.close()


if __name__ == "__main__":
    main()
