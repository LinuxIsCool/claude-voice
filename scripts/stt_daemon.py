# /// script
# requires-python = ">=3.11"
# dependencies = ["sounddevice", "openwakeword", "silero-vad", "numpy"]
# ///
"""STT daemon for claude-voice Phase 4.

Long-running service that:
1. Captures microphone audio continuously (16kHz mono)
2. Runs wake word detection ("Legion" or fallback "hey_jarvis")
3. On trigger: activates VAD + STT pipeline
4. Transcribes speech and writes result to a file for Claude Code to read

Managed by systemd: voice-stt.service

Usage:
    # Start (normally via systemd)
    uv run scripts/stt_daemon.py

    # Stop
    uv run scripts/stt_daemon.py --stop

    # Check
    uv run scripts/stt_daemon.py --check
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PID_PATH = Path("~/.claude/local/voice/stt.pid").expanduser()
LOG_PATH = Path("~/.claude/local/voice/stt.log").expanduser()
STT_ACTIVE_PATH = Path("~/.claude/local/voice/stt-active").expanduser()
TRANSCRIPT_PATH = Path("~/.claude/local/voice/last-transcript.txt").expanduser()
IDLE_TIMEOUT = 0  # 0 = no timeout (always listening)


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
# Main daemon
# ---------------------------------------------------------------------------

def _run_daemon() -> None:
    """Main STT daemon loop."""
    import numpy as np

    # Guard against concurrent startup
    if PID_PATH.exists():
        try:
            old_pid = int(PID_PATH.read_text().strip())
            os.kill(old_pid, 0)
            _log(f"STT daemon already running (pid {old_pid})")
            sys.exit(1)
        except (ValueError, ProcessLookupError, PermissionError):
            pass

    # Import clear_flag for use throughout this function
    _lib_path = str(Path(__file__).resolve().parent.parent / "lib")
    if _lib_path not in sys.path:
        sys.path.insert(0, _lib_path)
    try:
        from flags import clear_flag as _clear_flag
    except ImportError:
        def _clear_flag(path):
            path.unlink(missing_ok=True)

    # Clear any orphaned stt-active flag from prior crash (SIGKILL, OOM, etc.)
    _clear_flag(STT_ACTIVE_PATH)

    PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    PID_PATH.write_text(str(os.getpid()))

    def _shutdown(sig=None, frame=None):
        _log("STT daemon shutting down")
        _clear_flag(STT_ACTIVE_PATH)
        PID_PATH.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Ensure CUDA libs are available (borrow from whisperx-env if needed)
    cuda_lib_dirs = [
        os.path.expanduser("~/.local/share/whisperx-env/lib/python3.12/site-packages/nvidia/cublas/lib"),
        os.path.expanduser("~/.local/share/whisperx-env/lib/python3.12/site-packages/nvidia/cudnn/lib"),
    ]
    existing = os.environ.get("LD_LIBRARY_PATH", "")
    cuda_paths = ":".join(d for d in cuda_lib_dirs if os.path.isdir(d))
    if cuda_paths:
        os.environ["LD_LIBRARY_PATH"] = f"{cuda_paths}:{existing}" if existing else cuda_paths

    _log(f"STT daemon starting (pid {os.getpid()})")

    # Import and initialize components
    from mic import MicCapture
    from wake import WakeWordDetector
    from stt import STTEngine, SileroVADWrapper

    # Find AEC source device for echo-cancelled mic input
    aec_device = None
    try:
        import sounddevice as _sd
        for i, d in enumerate(_sd.query_devices()):
            if "AEC" in d.get("name", "") and d["max_input_channels"] > 0:
                aec_device = i
                _log(f"AEC source detected — device {i}: {d['name']}")
                break
        if aec_device is None:
            _log("No AEC source found — using default mic (TTS may bleed into STT)")
    except Exception as e:
        _log(f"AEC device detection failed: {e}")

    mic = MicCapture(device=aec_device)
    wake = WakeWordDetector()
    stt = STTEngine()
    vad = SileroVADWrapper()

    # State (protected by _state_lock for thread safety)
    import threading
    _state_lock = threading.Lock()
    listening = False
    listen_start_time = 0.0
    LISTEN_TIMEOUT = 30.0  # Max seconds to listen before auto-stop
    SILENCE_TIMEOUT = 2.5  # Seconds of silence after speech to trigger end
    last_speech_time = 0.0
    heard_speech = False

    def _finalize():
        """Transcribe buffered audio and write result. Thread-safe."""
        nonlocal listening, heard_speech
        with _state_lock:
            if not listening:
                return  # Already finalized by another thread
            listening = False
            heard_speech = False
        # Audio feedback: stop-listening beep
        try:
            _stop_beep = Path(__file__).resolve().parent.parent / "assets" / "themes" / "default" / "sounds" / "session-end-01.wav"
            if _stop_beep.exists():
                subprocess.Popen(
                    ["pw-play", "--volume=0.5", str(_stop_beep)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except Exception:
            pass
        buf_len = len(stt.audio_buffer)
        _log(f"finalizing transcription... ({buf_len} chunks in buffer)")
        text = stt.stop_listening()
        vad.reset()
        if text:
            _log(f"transcript: {text}")
            TRANSCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
            TRANSCRIPT_PATH.write_text(text)
        else:
            _log("no speech detected")

    def on_wake(score: float):
        nonlocal listening, listen_start_time, heard_speech, last_speech_time
        if listening:
            return
        _log(f"wake word detected (confidence={score:.2f})")
        # Audio feedback: play notification sound so user knows we heard the wake word
        try:
            _beep_path = Path(__file__).resolve().parent.parent / "assets" / "themes" / "default" / "sounds" / "notification-01.wav"
            if _beep_path.exists():
                subprocess.Popen(
                    ["pw-play", "--volume=0.5", str(_beep_path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
        except Exception:
            pass
        listening = True
        heard_speech = False
        listen_start_time = time.time()
        last_speech_time = time.time()
        stt.start_listening()
        vad.reset()
        _log("STT listening... (speak now, 15s timeout)")

    def on_speech_start():
        nonlocal heard_speech, last_speech_time
        if listening:
            heard_speech = True
            last_speech_time = time.time()
            _log("speech detected")

    def on_speech_end():
        if not listening:
            return
        if heard_speech:
            _log("speech end detected")
            _finalize()

    def on_audio(chunk: np.ndarray):
        nonlocal listening, last_speech_time
        # Always listen for wake word — even during TTS playback
        # (user can interrupt agent speech by saying "hey Jarvis")
        if not listening:
            wake.process_chunk(chunk)
            return

        # Buffer audio and run VAD when listening
        stt.buffer_audio(chunk)
        vad.process_chunk(chunk)

        # Track when we last heard speech (for silence timeout)
        if vad.is_speaking:
            last_speech_time = time.time()

        # Timeout checks (run every chunk = every 80ms)
        now = time.time()
        elapsed = now - listen_start_time
        if elapsed > LISTEN_TIMEOUT:
            _log(f"listen timeout ({LISTEN_TIMEOUT}s, elapsed={elapsed:.1f}s) — finalizing")
            _finalize()
            return
        if heard_speech and now - last_speech_time > SILENCE_TIMEOUT:
            _log(f"silence timeout ({SILENCE_TIMEOUT}s after last speech) — finalizing")
            _finalize()
            return

    # Wire callbacks
    wake.on_wake = on_wake
    vad.on_speech_start = on_speech_start
    vad.on_speech_end = on_speech_end
    mic.register(on_audio)

    # Start capture
    mic.start()
    _log(f"STT daemon ready — listening for wake word '{wake.model_name}'")

    # Partial transcript path — write here for real-time display
    PARTIAL_PATH = Path("~/.claude/local/voice/partial-transcript.txt").expanduser()
    last_partial_time = 0.0
    PARTIAL_INTERVAL = 2.0  # Seconds between partial transcriptions

    # Main loop — check timeouts and run partial transcription
    try:
        while True:
            time.sleep(0.5)
            if listening:
                now = time.time()
                elapsed = now - listen_start_time

                # Periodic partial transcription for real-time display
                if heard_speech and now - last_partial_time >= PARTIAL_INTERVAL and stt.audio_buffer:
                    last_partial_time = now
                    try:
                        import numpy as _np
                        partial_audio = _np.concatenate(stt.audio_buffer)
                        partial_text = stt._transcribe(partial_audio)
                        if partial_text:
                            PARTIAL_PATH.write_text(partial_text)
                            _log(f"[partial] {partial_text[:80]}")
                    except Exception as e:
                        _log(f"[partial] error: {e}")

                if elapsed > LISTEN_TIMEOUT:
                    _log(f"[main] listen timeout ({elapsed:.1f}s) — finalizing")
                    _finalize()
                    PARTIAL_PATH.unlink(missing_ok=True)
                elif heard_speech and now - last_speech_time > SILENCE_TIMEOUT:
                    _log(f"[main] silence timeout — finalizing")
                    _finalize()
                    PARTIAL_PATH.unlink(missing_ok=True)
    except KeyboardInterrupt:
        pass
    finally:
        mic.stop()
        _shutdown()


# ---------------------------------------------------------------------------
# Control commands
# ---------------------------------------------------------------------------

def _check() -> None:
    if not PID_PATH.exists():
        print("STT daemon: not running")
        sys.exit(1)
    try:
        pid = int(PID_PATH.read_text().strip())
        os.kill(pid, 0)
        print(f"STT daemon: running (pid {pid})")
    except (ValueError, ProcessLookupError):
        print("STT daemon: stale PID file")
        sys.exit(1)


def _stop() -> None:
    if PID_PATH.exists():
        try:
            pid = int(PID_PATH.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            print(f"STT daemon stopped (pid {pid})")
        except (ValueError, ProcessLookupError):
            print("STT daemon: stale PID file")
            PID_PATH.unlink(missing_ok=True)
    else:
        print("STT daemon: not running")


if __name__ == "__main__":
    if "--check" in sys.argv:
        _check()
    elif "--stop" in sys.argv:
        _stop()
    else:
        _run_daemon()
