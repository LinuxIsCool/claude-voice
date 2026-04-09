"""TTS engine for claude-voice.

Dual-backend text-to-speech with tiered routing:
  Tier 1 (fast): TTS daemon via Unix socket — keeps Kokoro warm, ~90ms
  Tier 2 (slow): Subprocess synthesis — cold Kokoro load, ~8s
  Cache: SHA256-keyed WAVs, looked up before any synthesis

Playback happens via lib/audio.play_sound() with fire-and-forget pattern.
All public functions are safe to call from hook contexts — they never raise,
never block indefinitely, and degrade gracefully to silence.

Key functions:
  get_cached(text, voice)      — instant cache lookup, no synthesis
  speak_via_daemon(text, voice) — fast path via daemon socket
  synthesize(text, voice)      — slow path via subprocess
  speak(text, voice)           — full pipeline: cache → daemon → subprocess
  extract_speakable(text)      — strip markdown, extract speakable prose
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Optional

from constants import (
    CACHE_DIR,
    DAEMON_SOCKET,
    DAEMON_TIMEOUT_SECONDS,
    DEFAULT_VOICE,
    KOKORO_ENV,
    SYNTHESIS_TIMEOUT_SECONDS,
    TARGET_SAMPLE_RATE,
    TTS_MAX_CHARS,
)
from utils import cache_key

# ---------------------------------------------------------------------------
# Public re-exports (for consumers that import from tts)
# ---------------------------------------------------------------------------

MAX_TEXT_LENGTH = TTS_MAX_CHARS
SPEAKABLE_MAX_CHARS = TTS_MAX_CHARS

# Markdown patterns to strip before speaking
# re.DOTALL only needed for code fences (``` ... ```). Using [\s\S] for that
# pattern specifically so DOTALL doesn't affect bold/italic matching.
_MD_STRIP = re.compile(
    r"```[\s\S]*?```|"      # fenced code blocks (cross-line)
    r"`[^`]+`|"             # inline code
    r"#{1,6}\s+|"           # headings
    r"\*{1,2}([^*]+)\*{1,2}|"  # bold/italic → keep inner text
    r"^\s*[-*+]\s+|"        # list bullets
    r"^\s*\d+\.\s+|"        # numbered lists
    r"\|[^\n]+\|",          # table rows
    re.MULTILINE,
)


def _cached_path(text: str, voice: str) -> Path:
    """Return the expected cache path for a text + voice combo."""
    key = cache_key(text, voice)
    return CACHE_DIR / f"{key}.wav"


def get_cached(text: str, voice: str = DEFAULT_VOICE) -> Optional[Path]:
    """Look up pre-generated TTS audio from cache.

    Returns path to WAV file if cached, None otherwise.
    This is the fast path — no synthesis, no GPU, just a file lookup.
    """
    path = _cached_path(text, voice)
    if path.exists():
        return path
    return None


def synthesize(
    text: str,
    voice: str = DEFAULT_VOICE,
    output_path: Optional[Path] = None,
    timeout: float = SYNTHESIS_TIMEOUT_SECONDS,
) -> Optional[Path]:
    """Synthesize text to speech using Kokoro-82M.

    Runs Kokoro in a subprocess to avoid loading PyTorch into the hook
    process (which would add 5s+ to every hook invocation).

    Returns path to WAV file on success, None on failure.
    The result is automatically cached for future lookups.

    Args:
        text: Text to synthesize (max 500 chars).
        voice: Kokoro voice preset (default: af_heart).
        output_path: Override output path. If None, uses cache dir.
        timeout: Max seconds to wait for synthesis.
    """
    if not text or not text.strip():
        return None

    text = text[:MAX_TEXT_LENGTH].strip()

    # Check cache first
    cached = get_cached(text, voice)
    if cached is not None:
        return cached

    # Verify Kokoro is available
    if not KOKORO_ENV.exists():
        return None

    # Determine output path
    if output_path is None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        output_path = _cached_path(text, voice)

    # Run synthesis in subprocess via env vars (no string interpolation into code)
    # This avoids importing PyTorch/Kokoro into the hook process
    synth_script = '''
import warnings
warnings.filterwarnings("ignore")
import os, sys
from kokoro import KPipeline
import soundfile as sf
import numpy as np

pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
text = os.environ["TTS_TEXT"]
voice = os.environ["TTS_VOICE"]
output = os.environ["TTS_OUTPUT"]
sample_rate = int(os.environ.get("TTS_SAMPLE_RATE", "48000"))

chunks = []
for _, _, audio in pipe(text, voice=voice, speed=1.0):
    chunks.append(audio)

if not chunks:
    sys.exit(1)

audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]

import scipy.signal
audio_48k = scipy.signal.resample_poly(audio, 2, 1)
stereo = np.column_stack([audio_48k, audio_48k])
sf.write(output, stereo, sample_rate, subtype="PCM_16")
'''

    try:
        result = subprocess.run(
            [str(KOKORO_ENV), "-c", synth_script],
            capture_output=True,
            timeout=timeout,
            env={
                **os.environ,
                "PYTHONWARNINGS": "ignore",
                "TTS_TEXT": text,
                "TTS_VOICE": voice,
                "TTS_OUTPUT": str(output_path),
                "TTS_SAMPLE_RATE": str(TARGET_SAMPLE_RATE),
            },
        )
        if result.returncode == 0 and output_path.exists():
            return output_path
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        pass

    return None


def speak(
    text: str,
    voice: str = DEFAULT_VOICE,
    volume: float = 0.8,
) -> Optional[Path]:
    """High-level TTS: synthesize and play.

    First checks the cache. If not cached, attempts on-demand synthesis.
    Playback is fire-and-forget via pw-play.
    For the fast daemon path, use speak_via_daemon() instead.

    Returns the WAV path on success, None on failure.
    Never raises. Degrades to silence.
    """
    try:
        # Try cache first (instant)
        wav = get_cached(text, voice)
        if wav is None:
            # On-demand synthesis (slow — 8s cold, 0.1s warm)
            wav = synthesize(text, voice)
        if wav is None:
            return None

        # Play via audio module
        from audio import play_sound

        play_sound(wav, volume=volume)
        return wav
    except Exception:
        return None


def warmup_greeting(theme_config: dict, voice: str = DEFAULT_VOICE) -> Optional[Path]:
    """Pre-generate the greeting for a theme.

    Call this at setup time (not in the hook path) to populate the cache.
    Returns the cached WAV path.
    """
    greeting = (
        theme_config.get("tts", {}).get("greeting_template")
        or theme_config.get("meta", {}).get("greeting_template")
        or "{summary}"
    )
    # Use a generic summary for pre-warming
    text = greeting.replace("{summary}", "session ready")
    return synthesize(text, voice)


def extract_speakable(text: str, max_chars: int = SPEAKABLE_MAX_CHARS) -> Optional[str]:
    """Extract speakable content from a markdown response.

    Strips markdown formatting and code blocks, returning the full prose
    content up to max_chars. Finds a clean sentence boundary for truncation
    so the spoken output doesn't end mid-thought.

    The goal is to read the FULL meaningful output — not just the first
    sentence. If the assistant wrote 3 paragraphs of explanation, the user
    should hear all of it (up to the configured limit).

    Args:
        text: Raw assistant response (may contain markdown).
        max_chars: Maximum characters in the returned excerpt.

    Returns:
        Clean speakable string (≤ max_chars), or None.
    """
    if not text or not text.strip():
        return None

    # Skip code-only responses (> 60% of content is inside code fences)
    code_blocks = re.findall(r"```.*?```", text, re.DOTALL)
    code_chars = sum(len(b) for b in code_blocks)
    if len(text) > 20 and code_chars / len(text) > 0.6:
        return None

    # Strip markdown — bold/italic keeps inner text via group 1
    def _replace_md(m: re.Match) -> str:
        return m.group(1) if m.lastindex and m.group(1) else " "

    clean = _MD_STRIP.sub(_replace_md, text)

    # Collapse whitespace
    clean = re.sub(r"\s+", " ", clean).strip()

    if not clean:
        return None

    # If content fits within limit, return it all
    if len(clean) <= max_chars:
        return clean.strip() or None

    # Truncate at a sentence boundary within max_chars
    excerpt = clean[:max_chars]

    # Search backwards for sentence-ending punctuation
    best_pos = -1
    for end_char in (".", "!", "?"):
        pos = excerpt.rfind(end_char)
        if pos > 30:  # At least 30 chars to be a real sentence
            best_pos = max(best_pos, pos)

    if best_pos > 30:
        return excerpt[: best_pos + 1].strip()

    # No sentence boundary — use word boundary with ellipsis
    word_boundary = excerpt.rfind(" ")
    if word_boundary > 30:
        return excerpt[:word_boundary].rstrip() + "..."

    return excerpt.rstrip() + "..."


def speak_via_daemon(
    text: str,
    voice: str = DEFAULT_VOICE,
    volume: float = 0.8,
) -> Optional[Path]:
    """Synthesize and play text via the TTS daemon (fast path).

    The daemon keeps Kokoro loaded in GPU memory, so synthesis is ~90ms
    instead of ~8s cold-start. If the daemon is not running, falls back
    to the subprocess path via speak().

    Returns the WAV path on success, None on failure.
    Never raises. Degrades silently.
    """
    if not text or not text.strip():
        return None

    # Fast path: check cache — if hit, enqueue or play directly (no synthesis)
    cached = get_cached(text, voice)
    if cached is not None:
        try:
            from queue_client import enqueue_speech
            result = enqueue_speech(str(cached), priority=1, volume=volume)
            if result is not None:
                return cached
        except Exception:
            pass
        try:
            from audio import play_sound
            play_sound(cached, volume=volume)
        except Exception:
            pass
        return cached

    # Async path: tell the TTS daemon to synthesize AND enqueue in one fire-and-forget call.
    # The hook process does NOT wait for synthesis to finish.
    # The daemon synthesizes, then enqueues the result in the voice queue.
    if DAEMON_SOCKET.exists():
        try:
            # Pass pane_id so the queue daemon knows which tmux pane this came from
            import os as _os
            _pane_id = _os.environ.get("TMUX_PANE", "")
            if not _pane_id:
                try:
                    from queue_client import _detect_tmux_pane
                    _pane_id = _detect_tmux_pane()
                except Exception:
                    _pane_id = "_global"
            request = json.dumps({
                "text": text[:MAX_TEXT_LENGTH],
                "voice": voice,
                "enqueue": True,
                "volume": volume,
                "pane_id": _pane_id,
                "agent_id": _os.environ.get("PERSONA_SLUG", ""),
            })
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(2)  # Short timeout — we just need to send, not wait
                s.connect(str(DAEMON_SOCKET))
                s.sendall((request + "\n").encode("utf-8"))
                # Read ack (daemon confirms it received the request)
                buf = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    if b"\n" in buf:
                        break
                response = json.loads(buf.split(b"\n")[0])
                if response.get("status") == "accepted":
                    return Path(response.get("cache_path", ""))  # Path where WAV will be
        except Exception:
            pass

    # Fallback: synchronous synthesis + direct play (no daemon, no queue)
    wav_path = synthesize(text, voice)
    if wav_path:
        try:
            from audio import play_sound
            play_sound(wav_path, volume=volume)
        except Exception:
            pass
    return wav_path


def list_cached() -> list[Path]:
    """List all cached TTS audio files."""
    if not CACHE_DIR.exists():
        return []
    return sorted(CACHE_DIR.glob("*.wav"))


def clear_cache() -> int:
    """Clear the TTS cache. Returns number of files removed."""
    files = list_cached()
    for f in files:
        try:
            f.unlink()
        except OSError:
            pass
    return len(files)
