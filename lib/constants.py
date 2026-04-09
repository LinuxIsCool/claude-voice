"""Canonical constants for claude-voice.

SINGLE SOURCE OF TRUTH for all shared values. Every other module that needs
these values imports from here. Never duplicate these elsewhere.

Scripts that cannot import (PEP 723 standalone) must declare their own copy
with a `# SYNC: lib/constants.py:<NAME>` comment pointing here.
"""
from pathlib import Path

# ── Audio ────────────────────────────────────────────────────────────
TARGET_SAMPLE_RATE = 48000
"""Target sample rate matching PipeWire native quantum (48kHz)."""

# ── TTS limits ───────────────────────────────────────────────────────
TTS_MAX_CHARS = 15000
"""Maximum text length for TTS synthesis and spoken response extraction.

Kokoro-82M chunks internally, so long text is fine.
15,000 chars ~ 3,000 words ~ 20 minutes of speech.
Code blocks are stripped before this limit applies.
"""

# ── Paths ────────────────────────────────────────────────────────────
VOICE_DATA_DIR = Path("~/.claude/local/voice").expanduser()
"""Root directory for all voice runtime data."""

CACHE_DIR = VOICE_DATA_DIR / "cache" / "tts"
"""Directory for cached TTS audio files (SHA256-keyed WAVs)."""

DEFAULT_VOICE = "am_onyx"
"""Default Kokoro voice preset. Single source of truth — imported by tts.py and tts_daemon.py."""

DAEMON_SOCKET = VOICE_DATA_DIR / "daemon.sock"
"""Unix socket for the TTS daemon (fast path)."""

QUEUE_SOCKET = VOICE_DATA_DIR / "queue.sock"
"""Unix socket for the voice queue daemon (turn-taking).
Note: The voice-arbiter also listens on this socket for backward compat."""

QUEUE_PID = VOICE_DATA_DIR / "queue.pid"
"""PID file for voice queue daemon (legacy)."""

ARBITER_SOCKET = VOICE_DATA_DIR / "arbiter.sock"
"""Unix socket for the voice-arbiter daemon (new orchestration layer)."""

ARBITER_PID = VOICE_DATA_DIR / "arbiter.pid"
"""PID file for voice-arbiter daemon."""

MODE_STATE_PATH = VOICE_DATA_DIR / "mode-state"
"""Persisted voice mode (ambient, focused, solo, silent, broadcast)."""

TTS_PLAYING_PATH = VOICE_DATA_DIR / "tts-playing"
"""Flag file: exists when TTS is actively playing. Set by arbiter, checked by STT."""

CONFIG_DIR = VOICE_DATA_DIR
"""Config directory (same as data dir for voice)."""

CONFIG_PATH = CONFIG_DIR / "config.yaml"
"""Runtime config file."""

HEARTBEAT_PATH = Path("~/.claude/local/health/voice-heartbeat").expanduser()
"""Health monitoring heartbeat file."""

KOKORO_ENV = Path("~/.local/share/kokoro-env/bin/python3").expanduser()
"""Kokoro venv Python binary."""

THEMES_DIR = Path(__file__).resolve().parent.parent / "assets" / "themes"
"""Root directory for all theme assets (sounds, theme.json files)."""

# ── Volume ──────────────────────────────────────────────────────────
DEFAULT_SYSTEM_GAIN = 3.5
"""Hardware calibration gain. Applied after all 0-1 user volumes.
Calibrated so master=0.7 sounds comfortable on HDMI-to-TV output.
Kokoro WAVs have RMS ~-27.6 dBFS, so amplification is needed."""

DEFAULT_MASTER_VOLUME = 0.7
"""Default master volume (0.0-1.0)."""

VOLUME_MIN = 0.0
"""Minimum for all user-facing volume controls."""

VOLUME_MAX = 1.0
"""Maximum for all user-facing volume controls."""

# ── Voice indicators ────────────────────────────────────────────────
INDICATOR_ICONS = {
    "star": "\U0001f31f",     # 🌟
    "mic": "\U0001f3a4",      # 🎤
    "speaker": "\U0001f50a",  # 🔊
    "mute": "\U0001f507",     # 🔇
    "bubble": "\U0001f4ac",   # 💬
    "headphone": "\U0001f3a7",# 🎧
    "wave": "\U0001f30a",     # 🌊
    "bell": "\U0001f514",     # 🔔
    "none": "",               # clear
}
"""Named emoji registry for voice state indicators.
Config uses names (e.g. 'star'), resolved to Unicode here."""

DEFAULT_INDICATORS = {
    "speaking": "star",
    "listening": "mic",
    "queued": "mic",
    "muted": "mute",
}
"""Default indicator name assignments. Overridable in config.yaml indicators section."""

# ── Spatial mixer ────────────────────────────────────────────────────
FOCUS_STATE_PATH = VOICE_DATA_DIR / "focus-state"
"""Cache file for spatial state. Written by tmux hook (Layer 1).
Contains the focused pane ID. Compared against TMUX_PANE to determine state."""

STT_ACTIVE_PATH = VOICE_DATA_DIR / "stt-active"
"""File flag: exists when STT is recording. All audio suppressed while present.
Created by Phase 4 STT engine. Checked by router.py volume pipeline."""

DEFAULT_FOCUS_VOLUMES = {
    "focused": 1.0,
    "same_window": 0.5,
    "same_session": 0.2,
    "other_session": 0.0,
    "no_tmux": 1.0,
}
"""Default spatial volume multipliers. Configurable via tmux.focus_volumes."""

DEFAULT_PRIORITY_FLOORS = {
    "2": 0.8,
    "1": 0.0,
    "0": 0.0,
}
"""Priority volume floors. Higher-priority events override spatial silencing."""

# ── STT ──────────────────────────────────────────────────────────────
STT_ENV = Path("~/.local/share/stt-env/bin/python3").expanduser()
"""STT environment Python binary (sounddevice, openwakeword, silero-vad)."""

WHISPER_ENV = Path("~/.local/share/whisperx-env/bin/python3").expanduser()
"""faster-whisper environment Python binary (for accuracy re-transcription)."""

STT_SAMPLE_RATE = 16000
"""Sample rate for all STT components (Parakeet, openWakeWord, Silero VAD)."""

MODELS_DIR = Path(__file__).resolve().parent.parent / "assets" / "models"
"""Directory for custom models (wake word ONNX, etc.)."""

TRANSCRIPT_PATH = VOICE_DATA_DIR / "last-transcript.txt"
"""Last STT transcript, written by stt_daemon for Claude Code to read."""

# ── Timeouts ─────────────────────────────────────────────────────────
SYNTHESIS_TIMEOUT_SECONDS = 10
"""Max seconds to wait for on-demand TTS synthesis."""

DAEMON_TIMEOUT_SECONDS = 120
"""Max seconds to wait for daemon socket response.
Long text (15K chars) can take 60+ seconds to synthesize.
The hook process must wait or the response is lost."""
