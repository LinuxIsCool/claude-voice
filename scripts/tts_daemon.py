#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "kokoro>=0.9.4",
#   "soundfile",
#   "scipy",
#   "numpy",
#   "pyloudnorm",
#   "pip",
#   "en-core-web-sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl",
# ]
# ///
# Note: when run via uv run, the PEP 723 deps above handle spacy.
# When run directly (e.g. ~/.local/share/kokoro-env/bin/python3 tts_daemon.py),
# the kokoro-env venv already has kokoro + spacy installed.
"""TTS daemon — keeps Kokoro-82M loaded in GPU memory.

Listens on a Unix socket, synthesizes text on demand.
Exits after IDLE_TIMEOUT_MINUTES of inactivity.

Protocol (newline-delimited JSON):
  Request:  {"text": "Hello world", "voice": "am_onyx"}
  Response: {"path": "/path/to/cached.wav"}
     or:    {"error": "reason"}

Usage:
  uv run scripts/tts_daemon.py              # start daemon
  uv run scripts/tts_daemon.py --check      # check if running
  uv run scripts/tts_daemon.py --stop       # send stop signal
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SOCKET_PATH = Path("~/.claude/local/voice/daemon.sock").expanduser()
CACHE_DIR = Path("~/.claude/local/voice/cache/tts").expanduser()
PID_PATH = Path("~/.claude/local/voice/daemon.pid").expanduser()
LOG_PATH = Path("~/.claude/local/voice/daemon.log").expanduser()

# SYNC: lib/constants.py:TARGET_SAMPLE_RATE — must match
TARGET_SAMPLE_RATE = 48000
# Disable idle timeout under systemd (systemd manages lifecycle)
_UNDER_SYSTEMD = os.environ.get("INVOCATION_ID") is not None
IDLE_TIMEOUT_SECONDS = 0 if _UNDER_SYSTEMD else 30 * 60
DEFAULT_VOICE = "am_onyx"

# Read MAX_TEXT_LENGTH from config file — single source of truth is config.yaml
# Falls back to 15000 if config unreadable. This avoids the SYNC problem:
# changing the config automatically affects the daemon on next restart.
CONFIG_PATH = Path("~/.claude/local/voice/config.yaml").expanduser()
MAX_TEXT_LENGTH = 15000  # fallback default
try:
    for line in CONFIG_PATH.read_text().splitlines():
        if "response_max_chars" in line and ":" in line:
            val = line.split(":", 1)[1].strip().split("#")[0].strip()
            if val.isdigit():
                MAX_TEXT_LENGTH = int(val)
                break
except Exception:
    pass  # Use fallback


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# TTS synthesis (runs inside daemon, Kokoro already loaded)
# ---------------------------------------------------------------------------

# SYNC: lib/utils.py:cache_key — must match exactly
def _cache_key(text: str, voice: str) -> str:
    content = f"{voice}:{text}".encode("utf-8")
    return hashlib.sha256(content).hexdigest()[:16]


def _synthesize(pipe, text: str, voice: str) -> Path:
    """Synthesize text using already-loaded Kokoro pipeline.

    Returns path to cached WAV (48kHz stereo PCM_16).
    Raises on failure.
    """
    import numpy as np  # type: ignore[import]
    import scipy.signal  # type: ignore[import]
    import soundfile as sf  # type: ignore[import]

    key = _cache_key(text, voice)
    output_path = CACHE_DIR / f"{key}.wav"

    if output_path.exists():
        return output_path

    chunks = []
    for _, _, audio in pipe(text, voice=voice, speed=1.0):
        chunks.append(audio)

    if not chunks:
        raise RuntimeError("Kokoro returned no audio chunks")

    audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]

    # Upsample 24kHz → 48kHz for PipeWire
    audio_48k = scipy.signal.resample_poly(audio, 2, 1)
    stereo = np.column_stack([audio_48k, audio_48k])

    # ── Loudness normalization ──────────────────────────────────────
    # Kokoro outputs quiet audio (RMS -27 dBFS). We compress + normalize
    # to broadcast speech levels so downstream volume controls are simple
    # 0-1 attenuation. Without this, pw-play can't make it loud enough.
    #
    # LUFS normalize to -14 LUFS (broadcast speech standard) — louder at source,
    # so pw-play --volume stays at 1.0 (no clipping at ALSA int16 boundary).
    # Peak ceiling 0.89 (-1 dBFS) leaves headroom for minor pw-play gain adjustments.
    try:
        stereo = _loudness_normalize(
            stereo, TARGET_SAMPLE_RATE,
            target_lufs=-16.0,       # Yesterday's setting that sounded clear
            comp_threshold_db=0.0,   # No compression — preserves Kokoro's natural sound
            comp_ratio=1.0,          # 1:1 = disabled
            peak_ceiling=0.95,       # -0.4 dBFS — max fidelity
        )
    except Exception as e:
        _log(f"loudness normalization failed (using raw): {e}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), stereo, TARGET_SAMPLE_RATE, subtype="PCM_16")
    return output_path


def _loudness_normalize(
    audio: "np.ndarray", sample_rate: int,
    target_lufs: float = -10.0,
    comp_threshold_db: float = -18.0,
    comp_ratio: float = 10.0,
    peak_ceiling: float = 0.99,
) -> "np.ndarray":
    """Compress and LUFS-normalize audio to broadcast speech levels.

    Kokoro-82M outputs audio with RMS ~-27.6 dBFS and peaks at -2.5 to -7 dBFS.
    This high crest factor means peak normalization barely helps. We need:
    1. Compression to tame transient peaks and raise the body
    2. LUFS normalization to hit target integrated loudness
    3. Peak limiting to prevent clipping in the 16-bit output

    Args:
        audio: Float64 array, shape (samples, channels). Values in [-1, 1].
        sample_rate: Sample rate in Hz.
        target_lufs: Target integrated loudness in LUFS (default -10).
        comp_threshold_db: Compression threshold in dBFS (default -18).
        comp_ratio: Compression ratio above threshold (default 10:1).
        peak_ceiling: Maximum absolute sample value (default 0.99).

    Returns:
        Normalized audio array, same shape.
    """
    import numpy as np  # type: ignore[import]
    import pyloudnorm as pyln  # type: ignore[import]

    # Ensure float64 in [-1, 1] range
    f = audio.astype(np.float64)
    if np.max(np.abs(f)) > 1.0:
        f = f / 32767.0  # Was int16, convert

    # Step 1: Compression — tame peaks so normalization has room
    threshold = 10 ** (comp_threshold_db / 20)
    for ch in range(f.shape[1] if f.ndim > 1 else 1):
        channel = f[:, ch] if f.ndim > 1 else f
        a = np.abs(channel)
        mask = a > threshold
        if np.any(mask):
            excess = a[mask] - threshold
            channel[mask] = np.sign(channel[mask]) * (threshold + excess / comp_ratio)

    # Step 2: LUFS normalization
    meter = pyln.Meter(sample_rate)
    loudness = meter.integrated_loudness(f)
    if loudness > -70:  # Sanity check (silence = -inf)
        f = pyln.normalize.loudness(f, loudness, target_lufs)

    # Step 3: True-peak limit — scale down proportionally, never hard clip
    # Hard clip (np.clip) chops waveform tops → odd harmonics → audible distortion.
    # Proportional gain-back preserves waveform shape — just quieter if peaks exceed ceiling.
    current_peak = np.max(np.abs(f))
    if current_peak > peak_ceiling:
        f = f * (peak_ceiling / current_peak)

    return f


# ---------------------------------------------------------------------------
# Socket server
# ---------------------------------------------------------------------------

def _handle_request(pipe, data: bytes) -> dict:
    """Process one synthesis request. Returns response dict.

    If request has "enqueue": true, synthesis happens asynchronously in a thread.
    The daemon returns immediately with {"status": "accepted"}, then synthesizes
    and enqueues the result in the voice queue when done. The hook doesn't wait.
    """
    try:
        req = json.loads(data.decode("utf-8").strip())
        text = str(req.get("text", "")).strip()[:MAX_TEXT_LENGTH]
        voice = str(req.get("voice", DEFAULT_VOICE))
        should_enqueue = req.get("enqueue", False)
        volume = float(req.get("volume", 0.8))
        pane_id = str(req.get("pane_id", "_global"))
        agent_id = str(req.get("agent_id", ""))

        if not text:
            return {"error": "empty text"}

        if should_enqueue:
            # Async path: return immediately, synthesize + enqueue in background
            import threading

            # Compute the cache path so the client knows where to look
            key = _cache_key(text, voice)
            cache_path = str(CACHE_DIR / f"{key}.wav")

            def _synth_and_enqueue():
                try:
                    wav_path = _synthesize(pipe, text, voice)
                    _log(f"synthesized [{voice}] {text[:60]!r} → {wav_path.name}")
                    # Enqueue in voice queue for scheduled playback
                    _enqueue_in_voice_queue(str(wav_path), volume, pane_id, agent_id)
                except Exception as e:
                    _log(f"async synthesis error: {e}")

            t = threading.Thread(target=_synth_and_enqueue, daemon=True)
            t.start()
            return {"status": "accepted", "cache_path": cache_path}
        else:
            # Synchronous path: synthesize and return path
            wav_path = _synthesize(pipe, text, voice)
            _log(f"synthesized [{voice}] {text[:60]!r} → {wav_path.name}")
            return {"path": str(wav_path)}

    except Exception as e:
        _log(f"synthesis error: {e}")
        return {"error": str(e)}


def _enqueue_in_voice_queue(wav_path: str, volume: float, pane_id: str = "_global", agent_id: str = "") -> None:
    """Enqueue a synthesized WAV in the voice queue daemon for playback."""
    # SYNC: lib/constants.py:QUEUE_SOCKET
    queue_sock = Path("~/.claude/local/voice/queue.sock").expanduser()
    if not queue_sock.exists():
        # No queue daemon — play directly
        try:
            subprocess.Popen(
                ["pw-play", f"--volume={volume:.3f}", wav_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            _log(f"direct playback (no queue): {Path(wav_path).name}")
        except Exception:
            pass
        return

    try:
        request = json.dumps({
            "type": "enqueue",
            "wav_path": wav_path,
            "priority": 50,
            "agent_id": agent_id or os.environ.get("PERSONA_SLUG", ""),
            "volume": volume,
            "pane_id": pane_id,
        })
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(str(queue_sock))
            s.sendall((request + "\n").encode("utf-8"))
            buf = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"\n" in buf:
                    break
            resp = json.loads(buf.split(b"\n")[0])
            _log(f"enqueued in voice queue: {resp.get('id', '?')}")
    except Exception as e:
        _log(f"queue enqueue failed: {e}, playing directly")
        try:
            subprocess.Popen(
                ["pw-play", f"--volume={volume:.3f}", wav_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            pass


def _run_server(pipe) -> None:
    """Main server loop. Exits on SIGTERM or idle timeout."""
    # Guard against concurrent startup via PID file check
    if PID_PATH.exists():
        try:
            old_pid = int(PID_PATH.read_text().strip())
            os.kill(old_pid, 0)  # Check if process exists
            _log(f"daemon already running (pid {old_pid}), exiting")
            sys.exit(1)
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # Stale PID file — safe to proceed

    # Clean up stale socket
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()

    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    server.listen(5)
    server.settimeout(60)  # Check idle every 60s

    # Write PID file (after successful bind — avoids race)
    PID_PATH.write_text(str(os.getpid()))

    _log(f"TTS daemon ready on {SOCKET_PATH} (pid {os.getpid()})")

    last_request = time.monotonic()

    def _shutdown(sig, frame):  # noqa: ARG001 — signal handler signature required
        _log("TTS daemon shutting down")
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
        # Idle timeout check
        if IDLE_TIMEOUT_SECONDS > 0 and time.monotonic() - last_request > IDLE_TIMEOUT_SECONDS:
            _log(f"idle timeout ({IDLE_TIMEOUT_SECONDS}s), shutting down")
            _shutdown(None, None)

        try:
            conn, _ = server.accept()
        except socket.timeout:
            continue
        except OSError:
            break

        try:
            # Read request — accumulate full buffer, split on newline
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break
            data = data.split(b"\n")[0]  # Extract first complete line

            if data:
                response = _handle_request(pipe, data)
                conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
                last_request = time.monotonic()
        except Exception as e:
            _log(f"connection error: {e}")
        finally:
            conn.close()

    _shutdown(None, None)


# ---------------------------------------------------------------------------
# Control commands
# ---------------------------------------------------------------------------

def _check() -> None:
    """Check if daemon is running."""
    if not SOCKET_PATH.exists():
        print("daemon: not running (no socket)")
        sys.exit(1)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect(str(SOCKET_PATH))
        pid = PID_PATH.read_text().strip() if PID_PATH.exists() else "unknown"
        print(f"daemon: running (pid {pid})")
        sys.exit(0)
    except OSError:
        print("daemon: socket exists but not responding")
        sys.exit(1)


def _stop() -> None:
    """Stop the daemon."""
    if not PID_PATH.exists():
        print("daemon: not running")
        return
    pid = int(PID_PATH.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"daemon: stopped (pid {pid})")
    except ProcessLookupError:
        print(f"daemon: pid {pid} not found")
        PID_PATH.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="TTS daemon for claude-voice")
    parser.add_argument("--check", action="store_true", help="Check if running")
    parser.add_argument("--stop", action="store_true", help="Stop daemon")
    args = parser.parse_args()

    if args.check:
        _check()
        return
    if args.stop:
        _stop()
        return

    # Start daemon — load Kokoro once, then serve
    _log("loading Kokoro-82M...")
    t0 = time.monotonic()

    from kokoro import KPipeline  # type: ignore[import]
    pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")

    load_ms = int((time.monotonic() - t0) * 1000)
    _log(f"Kokoro loaded in {load_ms}ms")

    _run_server(pipe)


if __name__ == "__main__":
    main()
