---
title: "STT Engine — faster-whisper, VAD & Streaming Transcription"
spec: "07"
status: draft
created: 2026-03-26
author: matt
tags: [claude-voice, stt, whisper, vad, transcription]
---

# 07 — STT Engine

## 1. Overview

claude-voice's STT engine converts speech to text for Claude Code input. Primary backend: faster-whisper with the large-v3-turbo model (already installed on this machine). The engine handles voice activity detection (VAD), audio capture, transcription, and injection into Claude Code's input stream. This is the "voice input" half of claude-voice (paired with TTS output defined in spec 06).

The pipeline is entirely local. No audio leaves this machine. faster-whisper runs on the RTX 4070 GPU with CUDA, delivering transcription latency well under perceptible delay for interactive use. Three listening modes — push-to-talk, continuous (VAD-gated), and manual record — cover the full range from precise command input to hands-free dictation.

Why build this when Claude Code has native `/voice`? Because the native implementation sends audio to Anthropic's cloud servers, offers no offline mode, no customization, no local-first privacy, and no integration with our own tooling. We need a pipeline we own.

---

## 2. System Inventory

What is already available on this machine — no installation required for the core path:

| Component | Status | Location / Version |
|-----------|--------|--------------------|
| **faster-whisper** | Installed | `~/.local/share/whisperx-env/` — v1.2.1, CTranslate2 4.7.1 |
| **large-v3-turbo model** | Downloaded | `~/.cache/huggingface/hub/` — ~1.6GB, CT2 format |
| **PyTorch + CUDA** | Installed | torch 2.8.0, CUDA 12.x libs in whisperx-env |
| **Silero VAD** | Installed | Via whisperx (`whisperx.vads.Silero`), ~2MB JIT model |
| **pyannote.audio** | Installed | v4.0.4 in whisperx-env (diarization, not needed for real-time) |
| **GPU** | Available | RTX 4070 12GB VRAM, CUDA capable |
| **Microphone** | System default | PipeWire 1.6.2 capture, `pw-record` available |
| **PipeWire** | Running | v1.6.2, capture + playback, `pw-record` and `pw-play` in PATH |
| **Claude Code /voice** | Exists | `voiceEnabled: true` in settings, cloud-based, push-to-talk via Space |

What needs installation for optional/enhanced features:

| Component | Purpose | Install |
|-----------|---------|---------|
| **RealtimeSTT** | Streaming transcription with integrated VAD | `pip install RealtimeSTT` in whisperx-env |
| **sounddevice** | Python audio capture (alternative to pw-record) | `pip install sounddevice` |
| **ydotool** | Wayland keyboard injection for text input | `paru -S ydotool` (AUR) |

---

## 3. Backend Comparison

### 3.1 Model Size vs Performance (faster-whisper on RTX 4070)

| Model | Params | VRAM (fp16) | Latency (5s utterance) | WER (LibriSpeech) | Notes |
|-------|--------|-------------|------------------------|--------------------|----|
| **large-v3-turbo** | 809M | ~3.2 GB | **~120-150ms** | ~2.8% | **PRIMARY — best speed/quality ratio** |
| large-v3 | 1.54B | ~5.8 GB | ~500-800ms | ~2.7% | Marginal quality gain, 4x slower |
| distil-large-v3 | 756M | ~3.0 GB | ~120-200ms | ~2.9% | Distilled, slightly worse on short clips |
| medium | 769M | ~2.8 GB | ~200-350ms | ~3.0% | Good CPU fallback candidate |
| small | 244M | ~1.0 GB | ~100-150ms | ~3.4% | Lightweight, acceptable quality |
| tiny | 39M | ~0.4 GB | ~50-80ms | ~5.7% | Fast partial results (RealtimeSTT two-phase) |

### 3.2 Framework Comparison

| Framework | Approach | Streaming | VAD | GPU | Fits Our Stack |
|-----------|----------|-----------|-----|-----|----------------|
| **faster-whisper** | CTranslate2 inference | Via chunking | Built-in (Silero) | CUDA fp16 | Already installed, primary |
| **RealtimeSTT** | Wraps faster-whisper | Native (two-phase) | Silero integrated | Same as model | Best for live mic input |
| **WhisperX** | Wraps faster-whisper + alignment | Batch only | Silero pre-process | CUDA | Already installed, batch/meeting use |
| **whisper.cpp** | GGML C++ inference | HTTP server mode | Partial | CUDA/CPU | Alternative if Python is unwanted |
| **OpenAI Whisper API** | Cloud | No | No | N/A | Rejected — cloud, adds latency, costs money |

### 3.3 Decision

**faster-whisper large-v3-turbo** is the PRIMARY backend. It is already installed, already downloaded, delivers ~120-150ms transcription for a 5-second utterance on this GPU, and sits at the quality sweet spot (2.8% WER — within 0.1% of large-v3 at 4x the speed).

For live microphone input with streaming feedback, **RealtimeSTT** wraps faster-whisper and adds integrated Silero VAD, two-phase transcription (tiny for partials, turbo for finals), and a clean callback API. It is the recommended framework layer on top of faster-whisper for the `continuous` and `push_to_talk` modes.

For offline batch transcription of recordings, **WhisperX** adds word-level timestamps and speaker diarization — use it via the claude-recordings plugin, not through this STT engine.

---

## 4. Core API (`lib/stt.py`)

The STT engine exposes four public functions and one session class. All functions are synchronous from the caller's perspective except `transcribe_stream`, which yields segments as they arrive.

```python
"""STT engine — faster-whisper transcription with VAD and streaming support.

This module is the speech-to-text core of claude-voice. It manages:
- Model loading (lazy, with GPU/CPU fallback)
- Audio file transcription
- Live microphone streaming transcription
- Listening session lifecycle (push-to-talk, continuous, manual)

All processing is local. No audio leaves the machine.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Optional

import numpy as np


# ── Model Management ────────────────────────────────────────────────────────

_model = None
_model_name: str = ""
_model_device: str = ""

def _get_model(
    model_name: str = "large-v3-turbo",
    device: str = "auto",
    compute_type: str = "float16",
) -> "WhisperModel":
    """Lazy-load the Whisper model. Reuses cached instance if config unchanged.

    Device resolution:
        "auto" → "cuda" if torch.cuda.is_available() else "cpu"
        "cuda" → CUDA (fails hard if unavailable)
        "cpu"  → CPU with int8 quantization for speed

    If CUDA OOM occurs, falls back to CPU with medium model.
    """
    global _model, _model_name, _model_device

    if device == "auto":
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"

    # CPU gets int8 for speed; GPU gets float16
    if device == "cpu":
        compute_type = "int8"

    if _model is not None and _model_name == model_name and _model_device == device:
        return _model

    from faster_whisper import WhisperModel

    try:
        _model = WhisperModel(
            model_name,
            device=device,
            compute_type=compute_type,
            cpu_threads=4,
            num_workers=1,
        )
        _model_name = model_name
        _model_device = device
    except Exception:
        # CUDA OOM or model not found — fall back
        if device == "cuda":
            _model = WhisperModel(
                "medium",  # smaller model for CPU
                device="cpu",
                compute_type="int8",
                cpu_threads=8,
                num_workers=1,
            )
            _model_name = "medium"
            _model_device = "cpu"
        else:
            raise

    return _model


def unload_model() -> None:
    """Explicitly unload the Whisper model to free VRAM.

    Called by the idle timer (5 minutes of no STT activity by default)
    or when TTS needs GPU headroom.
    """
    global _model, _model_name, _model_device
    if _model is not None:
        del _model
        _model = None
        _model_name = ""
        _model_device = ""
        # Force CUDA memory release
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


# ── File Transcription ──────────────────────────────────────────────────────

def transcribe(
    audio_path: Path,
    language: str = "en",
    model_name: str = "large-v3-turbo",
    device: str = "auto",
) -> str:
    """Transcribe an audio file to text.

    Args:
        audio_path: Path to audio file (WAV, MP3, FLAC, OGG, etc. —
                    anything ffmpeg can decode).
        language: ISO 639-1 language code (default: "en").
        model_name: Whisper model name.
        device: Inference device ("auto", "cuda", "cpu").

    Returns:
        Transcribed text as a single string. Empty string if no speech detected.

    Raises:
        FileNotFoundError: If audio_path does not exist.
        RuntimeError: If transcription fails after fallback attempts.
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    model = _get_model(model_name, device)

    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=5,
        best_of=5,
        temperature=0.0,
        condition_on_previous_text=True,
        vad_filter=True,
        vad_parameters=dict(
            threshold=0.5,
            min_silence_duration_ms=500,
            speech_pad_ms=300,
        ),
    )

    text_parts = []
    for segment in segments:
        text_parts.append(segment.text.strip())

    return " ".join(text_parts)


# ── Streaming Transcription ─────────────────────────────────────────────────

def transcribe_stream(
    audio_stream: Iterator[np.ndarray],
    language: str = "en",
    model_name: str = "large-v3-turbo",
    device: str = "auto",
    sample_rate: int = 16000,
) -> Iterator[str]:
    """Stream transcription from an audio chunk iterator.

    Accumulates audio in a buffer. When the buffer exceeds a threshold
    (default: 3 seconds of audio), transcribes the buffer and yields
    the result. The buffer is then cleared.

    For true streaming with partial results, use RealtimeSTT via
    start_listening() instead.

    Args:
        audio_stream: Iterator yielding numpy float32 arrays of audio samples.
        language: ISO 639-1 language code.
        model_name: Whisper model name.
        device: Inference device.
        sample_rate: Sample rate of incoming audio (default: 16000).

    Yields:
        Text segments as they are transcribed.
    """
    CHUNK_DURATION_S = 3.0
    chunk_threshold = int(sample_rate * CHUNK_DURATION_S)

    model = _get_model(model_name, device)
    buffer = np.array([], dtype=np.float32)

    for chunk in audio_stream:
        buffer = np.concatenate([buffer, chunk.astype(np.float32)])

        if len(buffer) >= chunk_threshold:
            segments, _ = model.transcribe(
                buffer,
                language=language,
                beam_size=1,  # greedy for speed in streaming
                temperature=0.0,
                vad_filter=True,
            )
            text = " ".join(seg.text.strip() for seg in segments)
            if text:
                yield text
            buffer = np.array([], dtype=np.float32)

    # Flush remaining buffer
    if len(buffer) > sample_rate // 2:  # at least 0.5s of audio
        segments, _ = model.transcribe(
            buffer,
            language=language,
            beam_size=1,
            temperature=0.0,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments)
        if text:
            yield text


# ── Listening Sessions ──────────────────────────────────────────────────────

@dataclass
class ListeningSession:
    """Handle for an active listening session.

    Created by start_listening(), controlled by the caller.
    Manages the lifecycle of a single voice input interaction.
    """
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    mode: str = "push_to_talk"
    start_time: float = field(default_factory=time.time)
    is_recording: bool = False
    is_paused: bool = False
    language: str = "en"
    _audio_buffer: list = field(default_factory=list, repr=False)
    _partial_text: str = field(default="", repr=False)
    _callback: Optional[Callable[[str], None]] = field(default=None, repr=False)
    _cancelled: bool = field(default=False, repr=False)

    def pause(self) -> None:
        """Pause recording. Audio during pause is discarded."""
        self.is_paused = True
        self.is_recording = False

    def resume(self) -> None:
        """Resume recording after pause."""
        self.is_paused = False
        self.is_recording = True

    def cancel(self) -> None:
        """Cancel the session. Discard all audio, no transcription."""
        self._cancelled = True
        self.is_recording = False
        self._audio_buffer.clear()
        self._partial_text = ""

    def get_partial(self) -> str:
        """Return the current partial transcription.

        In push_to_talk mode, this is empty until release.
        In continuous mode, this updates as VAD segments complete.
        """
        return self._partial_text

    @property
    def duration(self) -> float:
        """Seconds since session started."""
        return time.time() - self.start_time

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled


def start_listening(
    mode: str = "push_to_talk",
    callback: Optional[Callable[[str], None]] = None,
    vad_threshold: float = 0.5,
    silence_ms: int = 500,
    min_speech_ms: int = 200,
    pre_speech_ms: int = 300,
    language: str = "en",
    model_name: str = "large-v3-turbo",
    device: str = "auto",
) -> ListeningSession:
    """Start a listening session.

    This function returns immediately with a ListeningSession handle.
    Audio capture and VAD run in a background thread. Transcription
    results are delivered via the callback (if provided) or retrieved
    via stop_listening().

    Modes:
        push_to_talk: Record while key held. Transcribe on release
                      (caller signals release by calling stop_listening).
                      No VAD — all captured audio is transcribed.

        continuous:   VAD monitors microphone continuously. Speech detected
                      → start buffering. Silence detected (silence_ms) →
                      transcribe → deliver via callback → restart listening.
                      Runs until stop_listening() is called.

        manual:       Record from start_listening() until stop_listening().
                      No VAD, no auto-trigger. Full recording transcribed
                      at once. Best for long dictation.

    Args:
        mode: Listening mode ("push_to_talk", "continuous", "manual").
        callback: Function called with transcribed text. Required for
                  continuous mode (otherwise text is lost between segments).
        vad_threshold: Silero VAD speech probability threshold (0.0–1.0).
        silence_ms: Milliseconds of silence before triggering transcription.
        min_speech_ms: Minimum speech duration to accept (filters clicks/pops).
        pre_speech_ms: Pre-speech buffer to capture utterance onset.
        language: ISO 639-1 language code.
        model_name: Whisper model name.
        device: Inference device.

    Returns:
        ListeningSession handle for controlling the session.
    """
    session = ListeningSession(
        mode=mode,
        language=language,
        is_recording=True,
        _callback=callback,
    )

    # Ensure model is loaded before starting capture (avoid latency spike on first utterance)
    _get_model(model_name, device)

    # Audio capture runs in a background thread.
    # Implementation detail: uses either sounddevice (preferred) or pw-record subprocess.
    # See §5 for capture pipeline details.
    import threading

    def _capture_loop():
        """Background thread: capture audio, run VAD, trigger transcription."""
        _run_capture_loop(
            session=session,
            vad_threshold=vad_threshold,
            silence_ms=silence_ms,
            min_speech_ms=min_speech_ms,
            pre_speech_ms=pre_speech_ms,
            model_name=model_name,
            device=device,
        )

    thread = threading.Thread(target=_capture_loop, daemon=True, name=f"stt-{session.session_id}")
    thread.start()

    return session


def stop_listening(session: ListeningSession) -> str:
    """Stop listening and return final transcription.

    For push_to_talk and manual modes, this triggers transcription
    of the accumulated audio buffer. For continuous mode, this stops
    the VAD loop and returns any remaining partial transcription.

    Args:
        session: The ListeningSession handle from start_listening().

    Returns:
        Final transcribed text. Empty string if cancelled or no speech detected.
    """
    session.is_recording = False

    if session.is_cancelled:
        return ""

    if not session._audio_buffer:
        return ""

    # Concatenate all buffered audio
    audio = np.concatenate(session._audio_buffer).astype(np.float32)
    session._audio_buffer.clear()

    if len(audio) < 16000 * 0.2:  # less than 200ms — likely noise
        return ""

    # Transcribe the full buffer
    model = _get_model()
    segments, _ = model.transcribe(
        audio,
        language=session.language,
        beam_size=5,
        best_of=5,
        temperature=0.0,
        condition_on_previous_text=True,
        vad_filter=True,
        vad_parameters=dict(
            threshold=0.5,
            min_silence_duration_ms=500,
            speech_pad_ms=300,
        ),
    )

    text = " ".join(seg.text.strip() for seg in segments)

    # Filter filler words if the entire transcription is just filler
    FILLER_ONLY = {"um", "uh", "hmm", "ah", "oh", "er", "mm"}
    words = text.lower().split()
    if words and all(w.strip(".,!?") in FILLER_ONLY for w in words):
        return ""

    return text
```

---

## 5. Audio Capture Pipeline

### 5.1 Pipeline Diagram

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────┐
│ Microphone  │────▶│ PipeWire Capture  │────▶│  Preprocessor │────▶│  VAD Filter  │────▶│  Buffer  │
│ (system     │     │ (sounddevice or   │     │  resample     │     │  (Silero)    │     │  (numpy  │
│  default)   │     │  pw-record)       │     │  normalize    │     │  speech/     │     │  array)  │
└─────────────┘     └──────────────────┘     │  noise gate   │     │  silence     │     └────┬─────┘
                                              └──────────────┘     │  detection   │          │
                                                                   └──────────────┘          │
                                                                          │                  │
                                                                          │ Silence           │ Push-to-talk
                                                                          │ detected          │ key released
                                                                          ▼                  ▼
                                                                   ┌──────────────┐   ┌──────────────┐
                                                                   │ Flush buffer │   │ Flush buffer │
                                                                   └──────┬───────┘   └──────┬───────┘
                                                                          │                  │
                                                                          ▼                  ▼
                                                                   ┌───────────────────────────────┐
                                                                   │        faster-whisper          │
                                                                   │   large-v3-turbo, CUDA fp16   │
                                                                   │   beam_size=5, temperature=0  │
                                                                   └──────────────┬────────────────┘
                                                                                  │
                                                                                  ▼
                                                                   ┌───────────────────────────────┐
                                                                   │        Post-Processing        │
                                                                   │   filler word filter          │
                                                                   │   whitespace normalization    │
                                                                   └──────────────┬────────────────┘
                                                                                  │
                                                                                  ▼
                                                                   ┌───────────────────────────────┐
                                                                   │     Claude Code Input         │
                                                                   │   callback / clipboard /      │
                                                                   │   ydotool injection           │
                                                                   └───────────────────────────────┘
```

### 5.2 Capture Method

Two capture backends, selected at runtime based on availability:

**Primary: sounddevice (Python)**

```python
import sounddevice as sd

CAPTURE_RATE = 16000       # Whisper's native sample rate
CAPTURE_CHANNELS = 1       # Mono
CAPTURE_DTYPE = "float32"  # Whisper expects float32 in [-1, 1]
CHUNK_DURATION_MS = 30     # 30ms chunks — matches Silero VAD window size
CHUNK_SAMPLES = int(CAPTURE_RATE * CHUNK_DURATION_MS / 1000)  # 480 samples

def _capture_audio_sounddevice():
    """Generator: yields 30ms audio chunks from system microphone."""
    with sd.InputStream(
        samplerate=CAPTURE_RATE,
        channels=CAPTURE_CHANNELS,
        dtype=CAPTURE_DTYPE,
        blocksize=CHUNK_SAMPLES,
    ) as stream:
        while True:
            chunk, overflowed = stream.read(CHUNK_SAMPLES)
            if overflowed:
                pass  # Log but don't fail — occasional overflows are normal
            yield chunk.flatten()
```

**Fallback: pw-record (subprocess)**

```python
import subprocess

def _capture_audio_pwrecord():
    """Generator: yields audio chunks via pw-record subprocess."""
    proc = subprocess.Popen(
        [
            "pw-record",
            "--format", "f32",
            "--rate", "16000",
            "--channels", "1",
            "-",  # stdout
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    BYTES_PER_CHUNK = CHUNK_SAMPLES * 4  # float32 = 4 bytes
    try:
        while True:
            data = proc.stdout.read(BYTES_PER_CHUNK)
            if not data:
                break
            chunk = np.frombuffer(data, dtype=np.float32)
            yield chunk
    finally:
        proc.terminate()
        proc.wait()
```

**Selection logic:**

```python
def _get_capture_backend():
    """Return the best available capture backend."""
    try:
        import sounddevice
        # Verify a device exists
        sounddevice.query_devices(kind="input")
        return "sounddevice"
    except Exception:
        pass

    import shutil
    if shutil.which("pw-record"):
        return "pw-record"

    return None  # No capture available — STT disabled
```

### 5.3 Audio Format

Whisper expects a specific input format. All captured audio is normalized to this before transcription:

| Parameter | Value | Reason |
|-----------|-------|--------|
| Sample rate | 16,000 Hz | Whisper's training sample rate |
| Channels | 1 (mono) | Whisper is mono-only |
| Bit depth | 32-bit float | Native numpy/torch format |
| Range | [-1.0, 1.0] | Normalized float |
| Encoding | PCM (raw) | No compression for in-memory buffers |
| File format | WAV 16-bit PCM | For disk-saved recordings (file transcription) |

---

## 6. Voice Activity Detection (VAD)

### 6.1 Backend Comparison

| VAD Backend | Latency per chunk | Accuracy | GPU Required | Model Size | Status |
|-------------|-------------------|----------|--------------|------------|--------|
| **Silero VAD** | < 1ms (30ms window) | Excellent — best open-source | No (CPU ONNX) | ~2 MB | Already installed (whisperx-env) |
| webrtcvad | ~0.5ms | Good — high false positive on music/noise | No | N/A (C lib) | Available via pip |
| Energy-based (RMS) | ~0.01ms | Basic — fails on low-volume speech, noisy environments | No | 0 (numpy) | Built-in, no install |
| faster-whisper built-in | N/A (applied during transcription) | Good | Yes (runs with model) | 0 (part of whisper) | Already installed |
| pyannote VAD | ~5ms | Excellent — best for multi-speaker | Optional | ~20 MB | Installed (whisperx-env), overkill for single-speaker |

### 6.2 Decision

**Silero VAD** is the primary VAD backend. It is already installed, runs in < 1ms per 30ms audio chunk on a single CPU thread, and is significantly more accurate than webrtcvad or energy-based detection. It handles accented speech, background noise, and music bleed gracefully.

**Energy-based** (numpy RMS threshold) is the fallback if Silero fails to load. It works for quiet environments but will false-trigger on noise.

**faster-whisper's built-in VAD** (also Silero under the hood) is used during transcription to filter silence from the audio buffer before decoding. This is complementary to the real-time VAD — the real-time VAD decides *when* to stop recording, the transcription VAD filters *what* gets decoded.

### 6.3 VAD Parameters

```python
@dataclass
class VADConfig:
    """Voice activity detection parameters."""

    # Speech probability threshold. Silero outputs 0.0–1.0 per chunk.
    # Higher = more conservative (fewer false triggers, may miss quiet speech).
    # Lower = more sensitive (catches whispers, but triggers on rustling/breathing).
    threshold: float = 0.5

    # Milliseconds of continuous silence before declaring end-of-speech.
    # 500ms is the sweet spot — enough to handle natural pauses within sentences,
    # short enough to feel responsive.
    silence_duration_ms: int = 500

    # Minimum speech duration to accept. Anything shorter is discarded
    # as a click, pop, cough, or keyboard sound.
    min_speech_duration_ms: int = 200

    # Pre-speech buffer. Audio before the VAD triggers is kept in a rolling
    # buffer so the start of the utterance isn't clipped. 300ms captures
    # the onset consonant of most words.
    pre_speech_buffer_ms: int = 300
```

### 6.4 VAD State Machine

```
                    ┌─────────────┐
                    │   IDLE      │ ◀──── initial state
                    │ (monitoring)│
                    └──────┬──────┘
                           │ speech probability > threshold
                           ▼
                    ┌─────────────┐
                    │  SPEECH     │
                    │ (recording) │ ◀──── pre_speech_buffer attached
                    └──────┬──────┘
                           │ speech probability < threshold
                           ▼
                    ┌──────────────┐
                    │  TRAILING   │
                    │ (silence    │ ──── counting silence_duration_ms
                    │  countdown) │
                    └──────┬──────┘
                      │         │
    speech resumes    │         │ silence_duration_ms exceeded
    (< silence_ms)    │         │
                      ▼         ▼
               ┌──────────┐  ┌──────────────┐
               │  SPEECH   │  │  COMMITTED   │
               │ (continue) │  │ (flush →     │
               └──────────┘  │  transcribe)  │
                              └──────┬───────┘
                                     │
                                     ▼
                              ┌─────────────┐
                              │   IDLE      │ ◀──── restart for continuous mode
                              └─────────────┘
```

### 6.5 Silero VAD Integration

```python
import collections

class SileroVAD:
    """Silero VAD wrapper for real-time speech detection."""

    def __init__(self, config: VADConfig):
        self.config = config
        self._model = None
        self._state = "idle"  # idle | speech | trailing
        self._silence_chunks = 0
        self._speech_chunks = 0

        # Pre-speech ring buffer: stores last N ms of audio
        pre_speech_chunks = int(config.pre_speech_buffer_ms / 30)  # 30ms per chunk
        self._pre_speech_buffer = collections.deque(maxlen=max(pre_speech_chunks, 1))

        # Silence threshold in chunks (30ms each)
        self._silence_threshold = int(config.silence_duration_ms / 30)
        self._min_speech_chunks = int(config.min_speech_duration_ms / 30)

    def _load_model(self):
        """Load Silero VAD model (lazy)."""
        if self._model is not None:
            return
        import torch
        model, utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
        )
        self._model = model

    def process_chunk(self, audio_chunk: np.ndarray) -> dict:
        """Process a 30ms audio chunk. Returns state transition info.

        Args:
            audio_chunk: float32 numpy array, 480 samples at 16kHz.

        Returns:
            {
                "state": "idle" | "speech" | "trailing" | "committed",
                "speech_probability": float,
                "should_transcribe": bool,
                "pre_speech_audio": Optional[np.ndarray],  # on speech start
            }
        """
        self._load_model()
        import torch

        tensor = torch.from_numpy(audio_chunk).float()
        prob = self._model(tensor, 16000).item()

        result = {
            "state": self._state,
            "speech_probability": prob,
            "should_transcribe": False,
            "pre_speech_audio": None,
        }

        if self._state == "idle":
            self._pre_speech_buffer.append(audio_chunk.copy())
            if prob >= self.config.threshold:
                self._state = "speech"
                self._speech_chunks = 1
                self._silence_chunks = 0
                # Attach pre-speech buffer
                result["pre_speech_audio"] = np.concatenate(list(self._pre_speech_buffer))
                self._pre_speech_buffer.clear()
                result["state"] = "speech"

        elif self._state == "speech":
            if prob >= self.config.threshold:
                self._speech_chunks += 1
                self._silence_chunks = 0
            else:
                self._state = "trailing"
                self._silence_chunks = 1
                result["state"] = "trailing"

        elif self._state == "trailing":
            if prob >= self.config.threshold:
                # Speech resumed — false alarm on silence
                self._state = "speech"
                self._speech_chunks += self._silence_chunks + 1
                self._silence_chunks = 0
                result["state"] = "speech"
            else:
                self._silence_chunks += 1
                if self._silence_chunks >= self._silence_threshold:
                    # Silence confirmed — commit if speech was long enough
                    if self._speech_chunks >= self._min_speech_chunks:
                        result["should_transcribe"] = True
                        result["state"] = "committed"
                    # Reset to idle
                    self._state = "idle"
                    self._speech_chunks = 0
                    self._silence_chunks = 0

        return result
```

---

## 7. Transcription Modes

### 7.1 Push-to-Talk (Default)

The simplest and most reliable mode. User holds a key to record, releases to transcribe.

**Flow:**

```
1. User presses & holds hotkey (default: Space via Claude Code /voice, or configurable key)
2. ListeningSession created, is_recording = True
3. Audio capture starts → chunks flow into session._audio_buffer
4. No VAD needed — all captured audio is speech (user is intentionally speaking)
5. User releases hotkey
6. stop_listening(session) called
7. Buffer concatenated → preprocessed → faster-whisper transcribes
8. Text returned → injected into Claude Code input
```

**Latency budget:**

| Step | Duration | Notes |
|------|----------|-------|
| Key release event | 0 ms | Trigger point |
| Buffer concatenation | ~1 ms | numpy concatenate |
| Audio preprocessing | ~5 ms | Normalize, noise gate |
| Whisper transcription | ~120-150 ms | 5s utterance, large-v3-turbo, CUDA fp16 |
| Post-processing | ~1 ms | Filler filter, whitespace normalization |
| Text injection | ~5 ms | Clipboard or ydotool |
| **Total** | **~132-162 ms** | Well under 200ms perceptible threshold |

**Advantages:** Zero false activations. Lowest latency (no silence detection overhead). Mirrors Claude Code's native /voice behavior.

**Disadvantages:** Requires a hand on the keyboard. Not hands-free.

### 7.2 Continuous Listening

VAD-gated hands-free mode. The microphone is always active. Silero VAD detects speech onset and offset.

**Flow:**

```
1. start_listening(mode="continuous", callback=on_text)
2. Silero VAD monitors microphone continuously (< 1ms per 30ms chunk)
3. Speech detected (probability > threshold)
   → Pre-speech buffer attached
   → Begin accumulating audio
4. Speech continues → buffer grows
5. Silence detected (> silence_ms of quiet)
   → VAD state machine transitions to "committed"
   → Buffer flushed to faster-whisper
   → Transcribed text delivered to callback
   → VAD resets to "idle", monitoring resumes
6. Repeat until stop_listening() called
```

**Latency budget:**

| Step | Duration | Notes |
|------|----------|-------|
| End of speech | 0 ms | User stops talking |
| VAD silence confirmation | ~500 ms | silence_duration_ms setting |
| Buffer concatenation | ~1 ms | |
| Audio preprocessing | ~5 ms | |
| Whisper transcription | ~120-150 ms | |
| Post-processing | ~1 ms | |
| Text injection | ~5 ms | |
| **Total** | **~632-662 ms** | Dominated by silence detection window |

The 500ms silence window is the main latency cost. Reducing it below 300ms causes mid-sentence splits. Increasing it above 800ms feels sluggish. 500ms is the standard trade-off used by RealtimeSTT, Google Assistant, and Amazon Alexa.

**Advantages:** Hands-free. Natural conversation flow. Can dictate while doing other things.

**Disadvantages:** False activations from ambient noise (TV, other people, keyboard sounds). Higher continuous CPU load from VAD (negligible on this machine). The 500ms silence window adds perceptible delay.

### 7.3 Manual Record

Record until explicit stop. No VAD, no auto-detection. Everything between start and stop is captured and transcribed.

**Flow:**

```
1. start_listening(mode="manual")
2. All audio captured, no VAD filtering
3. stop_listening() → full recording transcribed at once
4. Text returned
```

**Use cases:**
- Long dictation (multi-paragraph text)
- Meeting note capture
- Recording a thought without worrying about pauses triggering early transcription

**Advantages:** No risk of mid-sentence splitting. Captures everything including pauses and "um"s (useful for verbatim transcription).

**Disadvantages:** No partial results. Must wait for full transcription after stop. Longer recordings = longer transcription time (still fast — 10 minutes of audio transcribes in ~20 seconds on GPU).

---

## 8. Claude Code Integration

### 8.1 Integration Paths

Two viable approaches for injecting transcribed text into Claude Code's input:

**Path A: Enhance Native /voice**

Claude Code's built-in `/voice` feature uses cloud STT. If the API supports custom STT backend registration, claude-voice registers as a local provider:

```
User holds Space → Claude Code captures audio → routes to claude-voice STT → transcribed text → input buffer
```

Status: Unknown. Claude Code's voice API is not documented for plugin extension. This path requires investigation.

**Path B: Independent Capture + Injection**

claude-voice captures audio independently and injects transcribed text into the terminal:

```
User triggers recording (hotkey/VAD) → claude-voice captures → transcribes → injects text via:
  Option 1: ydotool type "transcribed text"  (Wayland keyboard injection)
  Option 2: wl-copy "text" && wl-paste       (clipboard paste)
  Option 3: Write to a file, Claude Code reads via hook
```

Status: This is the reliable, fully-controlled path. Works regardless of Claude Code's internal API.

### 8.2 Recommended Integration (Path B)

**Clipboard injection** is the most robust method on Wayland:

```python
import subprocess

def inject_text(text: str) -> None:
    """Inject transcribed text into the focused terminal via clipboard.

    Uses wl-copy to set clipboard contents, then ydotool to simulate
    Ctrl+Shift+V (terminal paste).
    """
    if not text:
        return

    # Set clipboard
    proc = subprocess.run(
        ["wl-copy", "--", text],
        capture_output=True,
        timeout=2,
    )
    if proc.returncode != 0:
        return

    # Simulate paste in terminal (Ctrl+Shift+V)
    subprocess.run(
        ["ydotool", "key", "29:1", "42:1", "47:1", "47:0", "42:0", "29:0"],
        capture_output=True,
        timeout=2,
    )
```

**Alternative: direct ydotool typing** (simpler but slower for long text):

```python
def inject_text_type(text: str) -> None:
    """Inject text by simulating keystrokes."""
    subprocess.run(
        ["ydotool", "type", "--", text],
        capture_output=True,
        timeout=5,
    )
```

**Alternative: callback-based** (for MCP server integration in a future phase):

```python
def inject_text_callback(text: str, callback: Callable[[str], None]) -> None:
    """Deliver text to a registered callback (e.g., MCP tool response)."""
    callback(text)
```

### 8.3 Hotkey Binding

The default push-to-talk key is Space (matching Claude Code's native /voice). This creates a conflict when typing. Resolution strategies:

| Strategy | Key | Pros | Cons |
|----------|-----|------|------|
| **Space hold** (default) | Space (hold > 300ms) | Matches native /voice | Conflicts with typing spaces |
| **Modifier + key** | Ctrl+Space | No typing conflict | Two-hand activation |
| **Dedicated key** | F13 / side mouse button | Zero conflict | Requires extra hardware or remapping |
| **Voice-activated** | None (continuous mode) | Hands-free | False activations |

For v0.1: defer hotkey binding to Claude Code's native mechanism when using /voice, or use Ctrl+Space for independent operation. The hotkey is configured in `~/.claude/local/voice/config.yaml` under `stt.hotkey`.

---

## 9. Whisper Configuration

### 9.1 Model Loading

```python
from faster_whisper import WhisperModel

model = WhisperModel(
    "large-v3-turbo",       # Model name — resolved from HuggingFace cache or downloaded
    device="cuda",           # GPU inference
    compute_type="float16",  # Half precision — best speed/VRAM balance on RTX 4070
    cpu_threads=4,           # Thread count for CPU operations (preprocessing, etc.)
    num_workers=1,           # Single inference worker (no batching for real-time use)
)
```

### 9.2 Transcription Parameters

```python
segments, info = model.transcribe(
    audio,                              # Path or numpy array
    language="en",                      # Language hint — avoids auto-detection latency
    beam_size=5,                        # Beam search width — 5 is standard
    best_of=5,                          # Sample N candidates, keep best — 5 is standard
    temperature=0.0,                    # Greedy decoding (deterministic, fastest)
    condition_on_previous_text=True,    # Use previous segment as context (reduces repetition)
    vad_filter=True,                    # Apply Silero VAD during transcription
    vad_parameters=dict(
        threshold=0.5,                  # Speech detection threshold
        min_silence_duration_ms=500,    # Silence to split segments
        speech_pad_ms=300,              # Padding around speech segments
    ),
    word_timestamps=False,              # Disable for speed (enable for subtitle use)
    initial_prompt=None,                # Optional context prompt for domain-specific terms
)
```

### 9.3 Performance Tuning

For **lowest latency** (push-to-talk, short utterances):

```python
# Speed-optimized: ~100ms for 5s utterance
segments, info = model.transcribe(
    audio,
    language="en",
    beam_size=1,           # Greedy — no beam search overhead
    best_of=1,             # No sampling
    temperature=0.0,       # Deterministic
    vad_filter=True,       # Still filter silence
    word_timestamps=False, # Skip timestamp computation
)
```

For **highest accuracy** (manual mode, long recordings):

```python
# Quality-optimized: ~200ms for 5s utterance (still fast)
segments, info = model.transcribe(
    audio,
    language="en",
    beam_size=5,
    best_of=5,
    temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],  # Temperature fallback
    condition_on_previous_text=True,
    vad_filter=True,
    word_timestamps=True,  # Full timestamps for review
    initial_prompt="Technical conversation about software development, Linux, Python, Claude Code.",
)
```

---

## 10. Audio Preprocessing

All captured audio passes through preprocessing before reaching Whisper. This improves transcription quality by normalizing volume, removing noise, and ensuring the correct format.

```python
import numpy as np

# Named constants — no magic numbers
TARGET_SAMPLE_RATE = 16000        # Whisper's native rate
NOISE_GATE_RMS_THRESHOLD = 0.001  # ~-60 dB — below this is silence/noise
PEAK_NORMALIZE_TARGET = 0.7       # -3 dB headroom (prevents clipping artifacts)
MIN_AUDIO_DURATION_S = 0.2        # 200ms — shorter is noise, not speech


def preprocess_audio(
    audio: np.ndarray,
    source_rate: int = 48000,
    noise_gate_db: float = -40.0,
    normalize: bool = True,
) -> np.ndarray:
    """Preprocess captured audio for Whisper transcription.

    Pipeline:
    1. Convert stereo to mono (if needed)
    2. Resample to 16kHz (if needed)
    3. Apply noise gate (discard if below threshold)
    4. Peak normalize to -3dB headroom

    Args:
        audio: Raw audio as numpy array (any dtype, any channels).
        source_rate: Sample rate of the input audio.
        noise_gate_db: Noise gate threshold in dB (default: -40 dB).
        normalize: Whether to peak-normalize the audio.

    Returns:
        Preprocessed float32 numpy array at 16kHz mono.
        Empty array if audio is below noise gate.
    """
    # Ensure float32
    if audio.dtype != np.float32:
        if np.issubdtype(audio.dtype, np.integer):
            info = np.iinfo(audio.dtype)
            audio = audio.astype(np.float32) / max(abs(info.min), abs(info.max))
        else:
            audio = audio.astype(np.float32)

    # Step 1: Convert to mono
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    # Step 2: Resample if needed
    if source_rate != TARGET_SAMPLE_RATE:
        from scipy.signal import resample

        num_samples = int(len(audio) * TARGET_SAMPLE_RATE / source_rate)
        audio = resample(audio, num_samples)

    # Step 3: Noise gate
    noise_gate_linear = 10 ** (noise_gate_db / 20)  # Convert dB to linear
    rms = np.sqrt(np.mean(audio ** 2))
    if rms < noise_gate_linear:
        return np.array([], dtype=np.float32)

    # Step 4: Peak normalize
    if normalize:
        peak = np.abs(audio).max()
        if peak > 0:
            audio = audio / peak * PEAK_NORMALIZE_TARGET

    return audio.astype(np.float32)
```

---

## 11. GPU Resource Management

### 11.1 VRAM Budget

The RTX 4070 has 12,282 MiB (12 GB) of VRAM. Budget allocation:

| Component | VRAM | When Loaded | Lifetime |
|-----------|------|-------------|----------|
| **faster-whisper large-v3-turbo** (fp16) | ~3.2 GB | On first STT request | Unloaded after 5 min idle |
| **Kokoro-82M TTS** (fp16) | ~400 MB | On first TTS request | Unloaded after 5 min idle |
| **Silero VAD** | ~50 MB | On first VAD call | Kept resident (tiny) |
| **CUDA context overhead** | ~300 MB | On first CUDA operation | Permanent |
| **Available for other work** | ~8 GB | Always | System/other models |

**Total peak STT+TTS**: ~3.95 GB of 12 GB. Comfortable headroom.

### 11.2 Lazy Loading Strategy

Models are loaded on first use and unloaded after an idle timeout:

```python
import threading
import time

MODEL_IDLE_TIMEOUT_S = 300  # 5 minutes — configurable

_idle_timer: Optional[threading.Timer] = None

def _reset_idle_timer():
    """Reset the model unload timer. Called after every STT operation."""
    global _idle_timer
    if _idle_timer is not None:
        _idle_timer.cancel()
    _idle_timer = threading.Timer(MODEL_IDLE_TIMEOUT_S, unload_model)
    _idle_timer.daemon = True
    _idle_timer.start()
```

### 11.3 STT/TTS Contention

If both STT and TTS are active simultaneously (user speaks while TTS is playing — barge-in):

1. **STT takes priority** — the user is speaking, so listen.
2. TTS playback is paused or ducked (volume reduced).
3. STT completes → TTS can resume or is cancelled (depending on whether the user's utterance supersedes the TTS output).

This contention is rare in practice (push-to-talk means the user explicitly starts recording, which implies they want to be heard, not to hear). Continuous mode might trigger it if background TTS is playing.

---

## 12. Configuration Schema

Full STT configuration as it appears in `~/.claude/local/voice/config.yaml`:

```yaml
# ── Speech-to-Text ─────────────────────────────────────────────────────────
stt:
  # Backend engine
  backend: whisper             # whisper — faster-whisper direct
                               # realtime — RealtimeSTT (streaming, two-phase)

  # Whisper model
  model: large-v3-turbo        # tiny | small | medium | large-v3 | large-v3-turbo | distil-large-v3
  language: en                 # ISO 639-1 language code
  device: auto                 # auto | cpu | cuda
                               # auto: CUDA if available, else CPU

  # Listening mode
  mode: push_to_talk           # push_to_talk | continuous | manual

  # Voice activity detection
  vad:
    backend: silero             # silero | energy
                                # silero: Silero VAD (~2MB model, excellent accuracy)
                                # energy: numpy RMS threshold (no model, basic accuracy)
    threshold: 0.5              # Speech detection probability threshold (0.0–1.0)
                                # Higher = fewer false triggers, may miss quiet speech
                                # Lower = catches whispers, may trigger on noise
    silence_ms: 500             # Silence duration (ms) before end-of-speech commit
                                # 300 = responsive but splits sentences
                                # 500 = balanced (recommended)
                                # 800 = tolerates long pauses, feels sluggish
    min_speech_ms: 200          # Minimum speech duration (ms) to accept
                                # Filters clicks, pops, keyboard sounds, coughs
    pre_speech_ms: 300          # Pre-speech audio buffer (ms)
                                # Captures the onset of the first word

  # Audio preprocessing
  preprocessing:
    noise_gate_db: -40          # Noise gate threshold in dB
                                # Audio below this RMS is treated as silence
    normalize: true             # Peak-normalize audio before transcription
                                # Improves accuracy for quiet speakers

  # Hotkey (for independent operation outside Claude Code /voice)
  hotkey: ctrl+space            # Push-to-talk activation key
                                # space — conflicts with typing
                                # ctrl+space — no conflict, two-hand
                                # f13 — dedicated key, zero conflict

  # Model management
  idle_timeout_s: 300           # Seconds before unloading model from VRAM (0 = never unload)

  # Post-processing
  filter_filler: true           # Discard transcriptions that are only filler words (um, uh, hmm)
```

### 12.1 Validation Rules

| Key | Type | Valid Range | Default |
|-----|------|-------------|---------|
| `stt.backend` | string | `"whisper"`, `"realtime"` | `"whisper"` |
| `stt.model` | string | Any valid Whisper model name | `"large-v3-turbo"` |
| `stt.language` | string | ISO 639-1 code | `"en"` |
| `stt.device` | string | `"auto"`, `"cpu"`, `"cuda"` | `"auto"` |
| `stt.mode` | string | `"push_to_talk"`, `"continuous"`, `"manual"` | `"push_to_talk"` |
| `stt.vad.backend` | string | `"silero"`, `"energy"` | `"silero"` |
| `stt.vad.threshold` | float | 0.0–1.0 | `0.5` |
| `stt.vad.silence_ms` | int | 100–5000 | `500` |
| `stt.vad.min_speech_ms` | int | 50–2000 | `200` |
| `stt.vad.pre_speech_ms` | int | 0–1000 | `300` |
| `stt.preprocessing.noise_gate_db` | float | -80.0–0.0 | `-40.0` |
| `stt.preprocessing.normalize` | bool | true/false | `true` |
| `stt.hotkey` | string | Key combo string | `"ctrl+space"` |
| `stt.idle_timeout_s` | int | 0–3600 | `300` |
| `stt.filter_filler` | bool | true/false | `true` |

---

## 13. Latency Analysis

### 13.1 Push-to-Talk (Optimal Path)

End-to-end from key release to text available in Claude Code input:

```
Key release event                    0 ms
├─ Buffer concatenation              1 ms
├─ Audio preprocessing               5 ms   (normalize, noise gate)
├─ Whisper transcription           130 ms   (5s utterance, large-v3-turbo, CUDA fp16, beam=5)
├─ Post-processing                   1 ms   (filler filter, whitespace)
├─ Text injection (clipboard)        5 ms   (wl-copy + ydotool paste)
└─ Total                          ~142 ms
```

For a speed-optimized path (beam=1, greedy):

```
Key release event                    0 ms
├─ Buffer concatenation              1 ms
├─ Audio preprocessing               5 ms
├─ Whisper transcription            80 ms   (beam=1, no sampling)
├─ Post-processing                   1 ms
├─ Text injection                    5 ms
└─ Total                           ~92 ms
```

### 13.2 Continuous Listening (VAD-Gated)

End-to-end from user stops speaking to text available:

```
User stops speaking                  0 ms
├─ VAD silence detection           500 ms   (silence_ms setting — the dominant cost)
├─ Buffer concatenation              1 ms
├─ Audio preprocessing               5 ms
├─ Whisper transcription           130 ms
├─ Post-processing                   1 ms
├─ Text injection                    5 ms
└─ Total                          ~642 ms
```

### 13.3 Cold Start (First Transcription)

The first transcription in a session incurs model loading overhead:

```
Trigger event                        0 ms
├─ Model loading (CUDA init)       800 ms   (one-time, includes CUDA context creation)
├─ Model loading (weights)         600 ms   (large-v3-turbo from HuggingFace cache)
├─ CTranslate2 optimization        200 ms   (graph optimization for GPU)
├─ First transcription            150 ms
└─ Total (first call)           ~1750 ms
```

Mitigation: `start_listening()` preloads the model in its setup phase. By the time the user finishes their first utterance, the model is ready.

### 13.4 Scaling with Utterance Length

Transcription time scales roughly linearly with audio duration. RTF (real-time factor) for large-v3-turbo on RTX 4070 is approximately 30-40x realtime:

| Utterance Length | Transcription Time | Total (push-to-talk) |
|------------------|-------------------|----------------------|
| 1 second | ~30 ms | ~42 ms |
| 3 seconds | ~80 ms | ~92 ms |
| 5 seconds | ~130 ms | ~142 ms |
| 10 seconds | ~260 ms | ~272 ms |
| 30 seconds | ~780 ms | ~792 ms |
| 1 minute | ~1,500 ms | ~1,512 ms |
| 5 minutes | ~7,500 ms | ~7,512 ms |

Even a 5-minute manual recording transcribes in under 8 seconds. For the typical push-to-talk utterance (3-10 seconds), transcription is imperceptible.

---

## 14. Error Handling

Every failure mode must degrade gracefully. STT is a convenience feature — its failures must never break Claude Code's hook pipeline, crash the session, or leave resources (threads, subprocesses, GPU memory) leaked.

| Failure | Detection | Response | User Impact |
|---------|-----------|----------|-------------|
| **No microphone** | `sounddevice.query_devices(kind="input")` raises, `pw-record` exits non-zero | STT disabled. Log warning once. | Voice input unavailable, typing still works |
| **GPU OOM** | `torch.cuda.OutOfMemoryError` during model load or transcription | Unload model, retry with `device="cpu"` and `model="medium"` | Slower transcription (~2-3x), still functional |
| **Whisper model not found** | `FileNotFoundError` from HuggingFace cache miss | Prompt user to download. STT disabled until model available. | One-time setup step |
| **CUDA not available** | `torch.cuda.is_available()` returns False | Use CPU with int8 quantization, downgrade to medium model | Slower but functional |
| **PipeWire capture fails** | `pw-record` exits non-zero, sounddevice raises | Try ALSA fallback (`arecord`). If both fail, STT disabled. | May need PipeWire restart |
| **Empty transcription** | Whisper returns no segments or empty text | Skip injection. No-op. | User re-speaks. Normal for very short/quiet input. |
| **Transcription is only filler** | All words match filler set (um, uh, hmm, ah, oh, er, mm) | Filter and discard. No injection. | Prevents noise in Claude Code input |
| **Background thread crash** | Exception in capture loop | Log traceback. Set `session.is_recording = False`. | Session ends, user retries |
| **sounddevice import fails** | ImportError | Fall back to pw-record subprocess capture | No user-facing difference |
| **Silero VAD load fails** | Exception during torch.hub.load | Fall back to energy-based VAD | Less accurate speech detection |
| **ydotool not available** | `shutil.which("ydotool")` returns None | Fall back to wl-copy only (user pastes manually) | Minor inconvenience |
| **wl-copy not available** | `shutil.which("wl-copy")` returns None | Print transcription to stdout, user copies | Degraded but functional |
| **Model download interrupted** | Partial file in HuggingFace cache | Delete partial cache, retry on next load | One-time retry |

### 14.1 Error Recovery Pattern

```python
def _safe_transcribe(audio: np.ndarray, language: str = "en") -> str:
    """Transcribe with full error recovery chain.

    Never raises. Returns empty string on any failure.
    """
    try:
        model = _get_model()  # handles CUDA → CPU fallback internally
        segments, _ = model.transcribe(
            audio,
            language=language,
            beam_size=5,
            temperature=0.0,
            vad_filter=True,
        )
        return " ".join(seg.text.strip() for seg in segments)
    except Exception as exc:
        # Log but don't crash
        _log_error(f"STT transcription failed: {exc}")

        # If it was a CUDA error, try CPU
        if "cuda" in str(exc).lower() or "out of memory" in str(exc).lower():
            try:
                unload_model()
                model = _get_model(model_name="medium", device="cpu")
                segments, _ = model.transcribe(
                    audio,
                    language=language,
                    beam_size=1,
                    temperature=0.0,
                )
                return " ".join(seg.text.strip() for seg in segments)
            except Exception as fallback_exc:
                _log_error(f"STT CPU fallback also failed: {fallback_exc}")

        return ""
```

---

## 15. Privacy and Security

### 15.1 Guarantees

- **100% local processing.** All audio stays on this machine. faster-whisper runs on the local GPU. No audio is sent to any cloud service, API, or external server.
- **No persistent audio storage.** Audio buffers exist in memory only during the active listening session. When `stop_listening()` returns (or the session is cancelled), the buffer is cleared. No audio files are written to disk unless the user explicitly requests it.
- **Transcriptions are ephemeral.** Transcribed text is injected into Claude Code's input and then discarded from the STT engine's memory. The text enters Claude Code's normal conversation flow (which has its own logging via claude-logging), but the STT engine does not maintain a separate transcript log.
- **No wake word daemon by default.** The always-listening ambient mode (Phase 3) requires explicit opt-in. Push-to-talk (default) only activates the microphone when the user holds the key.

### 15.2 Optional Logging

For debugging or review, transcriptions can be logged to a session file:

```yaml
# In config.yaml
stt:
  log_transcriptions: false   # Set to true to log all transcriptions
```

When enabled, transcriptions are appended to `~/.claude/local/voice/logs/stt-YYYY-MM-DD.jsonl`:

```json
{"timestamp": "2026-03-26T14:30:22Z", "session_id": "a1b2c3d4e5f6", "mode": "push_to_talk", "duration_s": 4.2, "text": "show me the git log for the last week", "latency_ms": 138}
```

This log contains text only, never audio. It can be deleted at any time.

### 15.3 Microphone Access

The STT engine accesses the system default input device via PipeWire. On this system (CachyOS/KDE Plasma 6), PipeWire manages all audio routing. The user can:

- Mute the microphone at the system level (KDE audio settings)
- Select which input device is used (PipeWire device routing)
- Monitor which applications are accessing the microphone (PipeWire's pw-top or KDE's audio panel)

The STT engine does not bypass any system audio policies.

---

## 16. Testing

### 16.1 Test Scripts

All test scripts live at `${CLAUDE_PLUGIN_ROOT}/scripts/` and are runnable via `uv run`.

**Capture test — verify microphone access:**

```bash
# Record 5 seconds from default mic, play it back
uv run scripts/stt_test.py --capture 5

# Expected: records audio, plays it back via pw-play, prints RMS levels per second
# Failure: "No input device found" or silence (RMS < 0.001)
```

**File transcription test — verify Whisper is working:**

```bash
# Transcribe a WAV file
uv run scripts/stt_test.py --file /path/to/sample.wav

# Expected: prints transcribed text, model info, latency
# Failure: model not found, CUDA error, empty transcription
```

**Latency benchmark — measure end-to-end performance:**

```bash
# Generate test utterances of varying lengths, measure transcription time
uv run scripts/stt_test.py --benchmark

# Expected output:
#   1s audio: 32ms transcription (31x realtime)
#   3s audio: 85ms transcription (35x realtime)
#   5s audio: 142ms transcription (35x realtime)
#   10s audio: 271ms transcription (37x realtime)
```

**VAD test — verify speech detection accuracy:**

```bash
# Play silence → speech → silence, verify VAD segmentation
uv run scripts/stt_test.py --vad

# Expected: reports speech onset/offset times, chunk count, false trigger count
```

**GPU test — verify CUDA acceleration:**

```bash
# Check CUDA availability and model loading
uv run scripts/stt_test.py --gpu

# Expected output:
#   CUDA available: True
#   Device: NVIDIA GeForce RTX 4070
#   VRAM total: 12282 MiB
#   VRAM used: 342 MiB (before model load)
#   Model: large-v3-turbo (float16)
#   VRAM used: 3542 MiB (after model load)
#   Test transcription: OK (138ms)
```

**Integration test — full push-to-talk flow:**

```bash
# Simulate push-to-talk: record for 3 seconds, transcribe, print result
uv run scripts/stt_test.py --push-to-talk 3

# Expected: records 3s, transcribes, prints text and total latency
```

### 16.2 Automated Checks

The following can be verified without a microphone (using pre-recorded audio):

1. **Model loading**: Load model, verify device (cuda/cpu), verify compute type.
2. **File transcription**: Transcribe a known audio file, compare output to expected text.
3. **Preprocessing**: Feed known audio through `preprocess_audio()`, verify output format and normalization.
4. **VAD state machine**: Feed synthetic audio (silence + sine wave + silence) through `SileroVAD.process_chunk()`, verify state transitions.
5. **Error recovery**: Force CUDA OOM (load huge model), verify CPU fallback engages.
6. **Filler filtering**: Transcribe audio containing only "um" and "uh", verify empty string returned.

---

## 17. Capture Loop Implementation

The private `_run_capture_loop` function is the core of all three listening modes. It runs in a background daemon thread spawned by `start_listening()`.

```python
def _run_capture_loop(
    session: ListeningSession,
    vad_threshold: float,
    silence_ms: int,
    min_speech_ms: int,
    pre_speech_ms: int,
    model_name: str,
    device: str,
) -> None:
    """Background capture loop. Runs until session.is_recording is False.

    For push_to_talk / manual:
        Captures all audio into session._audio_buffer.
        No VAD — everything is buffered. Transcription happens in stop_listening().

    For continuous:
        Runs VAD on every chunk. When speech ends (committed), transcribes
        the accumulated segment and delivers via callback. Then resets and
        continues monitoring.
    """
    backend = _get_capture_backend()
    if backend is None:
        _log_error("No audio capture backend available. STT disabled.")
        session.is_recording = False
        return

    if backend == "sounddevice":
        capture = _capture_audio_sounddevice()
    else:
        capture = _capture_audio_pwrecord()

    if session.mode in ("push_to_talk", "manual"):
        # Simple: buffer everything until stop_listening()
        for chunk in capture:
            if not session.is_recording:
                break
            if session.is_paused:
                continue
            session._audio_buffer.append(chunk)
        return

    # Continuous mode: VAD-gated
    vad_config = VADConfig(
        threshold=vad_threshold,
        silence_duration_ms=silence_ms,
        min_speech_duration_ms=min_speech_ms,
        pre_speech_buffer_ms=pre_speech_ms,
    )

    try:
        vad = SileroVAD(vad_config)
    except Exception:
        # Silero failed — fall back to energy-based VAD
        vad = EnergyVAD(vad_config)

    speech_buffer = []

    for chunk in capture:
        if not session.is_recording:
            break
        if session.is_paused:
            continue

        result = vad.process_chunk(chunk)

        if result["state"] == "speech" or result["state"] == "trailing":
            # Attach pre-speech buffer on speech start
            if result.get("pre_speech_audio") is not None:
                speech_buffer.append(result["pre_speech_audio"])
            speech_buffer.append(chunk)

        if result["should_transcribe"] and speech_buffer:
            # End of speech — transcribe this segment
            audio = np.concatenate(speech_buffer).astype(np.float32)
            speech_buffer.clear()

            text = _safe_transcribe(audio, language=session.language)

            if text and session._callback:
                session._callback(text)
            elif text:
                session._partial_text = text  # Store for get_partial()
```

---

## 18. Energy-Based VAD Fallback

When Silero VAD is unavailable (torch not installed, model download fails), the energy-based fallback uses simple RMS thresholding:

```python
class EnergyVAD:
    """Fallback VAD using audio energy (RMS) thresholding.

    Less accurate than Silero — triggers on any loud noise, misses
    quiet speech. Suitable for quiet environments only.
    """

    SPEECH_RMS_THRESHOLD = 0.02   # Empirical threshold for speech energy
    SILENCE_RMS_THRESHOLD = 0.005 # Below this is definitely silence

    def __init__(self, config: VADConfig):
        self.config = config
        self._state = "idle"
        self._silence_chunks = 0
        self._speech_chunks = 0
        self._silence_threshold = int(config.silence_duration_ms / 30)
        self._min_speech_chunks = int(config.min_speech_duration_ms / 30)
        self._pre_speech_buffer = collections.deque(
            maxlen=max(int(config.pre_speech_buffer_ms / 30), 1)
        )

    def process_chunk(self, audio_chunk: np.ndarray) -> dict:
        """Process a 30ms chunk using RMS energy detection."""
        rms = np.sqrt(np.mean(audio_chunk ** 2))
        is_speech = rms > self.SPEECH_RMS_THRESHOLD

        result = {
            "state": self._state,
            "speech_probability": min(rms / self.SPEECH_RMS_THRESHOLD, 1.0),
            "should_transcribe": False,
            "pre_speech_audio": None,
        }

        # Same state machine as SileroVAD
        if self._state == "idle":
            self._pre_speech_buffer.append(audio_chunk.copy())
            if is_speech:
                self._state = "speech"
                self._speech_chunks = 1
                self._silence_chunks = 0
                result["pre_speech_audio"] = np.concatenate(list(self._pre_speech_buffer))
                self._pre_speech_buffer.clear()
                result["state"] = "speech"

        elif self._state == "speech":
            if is_speech:
                self._speech_chunks += 1
            else:
                self._state = "trailing"
                self._silence_chunks = 1
                result["state"] = "trailing"

        elif self._state == "trailing":
            if is_speech:
                self._state = "speech"
                self._speech_chunks += self._silence_chunks + 1
                self._silence_chunks = 0
                result["state"] = "speech"
            else:
                self._silence_chunks += 1
                if self._silence_chunks >= self._silence_threshold:
                    if self._speech_chunks >= self._min_speech_chunks:
                        result["should_transcribe"] = True
                        result["state"] = "committed"
                    self._state = "idle"
                    self._speech_chunks = 0
                    self._silence_chunks = 0

        return result
```

---

## 19. RealtimeSTT Integration (Alternative Backend)

When `stt.backend` is set to `"realtime"`, the engine uses RealtimeSTT instead of direct faster-whisper. RealtimeSTT provides native streaming with partial results and a polished two-phase transcription pipeline.

```python
def _start_realtime_session(
    session: ListeningSession,
    vad_threshold: float,
    silence_ms: float,
) -> None:
    """Start a RealtimeSTT-backed listening session.

    RealtimeSTT handles mic capture, VAD, and transcription internally.
    We configure it and provide callbacks.
    """
    from RealtimeSTT import AudioToTextRecorder

    def on_text(text: str):
        """Called when RealtimeSTT commits a final transcription."""
        if not text or not text.strip():
            return
        session._partial_text = text.strip()
        if session._callback:
            session._callback(text.strip())

    def on_partial(text: str):
        """Called with intermediate results (fast model partial output)."""
        session._partial_text = text.strip() if text else ""

    recorder = AudioToTextRecorder(
        model="large-v3-turbo",
        language=session.language,
        use_microphone=True,
        spinner=False,
        device="cuda",
        compute_type="float16",

        # VAD
        silero_sensitivity=vad_threshold,
        post_speech_silence_duration=silence_ms / 1000,  # RealtimeSTT uses seconds

        # Two-phase: tiny for fast partials, turbo for accurate finals
        realtime_model_type="tiny",
        realtime_processing_pause=0.1,  # 100ms between partial updates

        # Callbacks
        on_realtime_transcription_update=on_partial,
    )

    # RealtimeSTT blocks on recorder.text() — run in the capture thread
    while session.is_recording and not session.is_cancelled:
        text = recorder.text(on_text)
        if text and session._callback:
            session._callback(text)

    recorder.shutdown()
```

**When to use RealtimeSTT vs direct faster-whisper:**

| Criterion | Direct faster-whisper | RealtimeSTT |
|-----------|----------------------|-------------|
| Push-to-talk | Preferred (simpler, lower overhead) | Works but overkill |
| Continuous listening | Requires manual VAD + capture loop | Preferred (built-in VAD + streaming) |
| Partial results (live feedback) | Not available | Native (two-phase: tiny + turbo) |
| Dependencies | Only faster-whisper | RealtimeSTT + faster-whisper + sounddevice |
| Cold start | Faster (one model) | Slower (two models: tiny + turbo) |
| Customization | Full control over every parameter | Configuration through RealtimeSTT API |

---

## 20. Open Questions

These are unresolved design decisions that will be settled during implementation or through user feedback:

1. **Replace or augment Claude Code's native /voice?**
   The native /voice uses cloud STT. claude-voice provides local STT. Should they coexist (user chooses per-session), or should claude-voice replace /voice entirely? Current lean: coexist — use claude-voice for local/private, native /voice for simplicity when cloud is acceptable.

2. **Hotkey conflict resolution.**
   Space as push-to-talk conflicts with typing. Claude Code's native /voice solves this with hold detection (brief vs long press). If claude-voice operates independently, Ctrl+Space avoids the conflict but requires two hands. A dedicated key (mouse button, F-key) is cleanest but requires hardware/remapping. Decision deferred to user preference after first prototype.

3. **Auto-punctuation behavior.**
   Whisper naturally produces punctuated text ("Hello, how are you?"). Should we pass this through as-is, or strip punctuation for raw dictation? Current lean: pass through as-is — Whisper's punctuation is accurate and saves the user from manually adding it.

4. **Multi-speaker diarization.**
   WhisperX (already installed) supports speaker diarization via pyannote.audio. Useful for meeting transcription but adds latency and complexity for real-time use. Diarization should be available in manual mode for long recordings, but not in push-to-talk or continuous modes.

5. **Streaming partial display.**
   In continuous mode with RealtimeSTT, partial transcriptions arrive before the utterance is complete. Should these be displayed somewhere (status bar, overlay) to give the user feedback that their speech is being recognized? This is a UX question that depends on the terminal integration approach.

6. **Language auto-detection.**
   Setting `language="en"` skips auto-detection and improves speed. But Shawn may occasionally speak French or other languages. Should we auto-detect (slower) or default to English with manual override? Current lean: default English, `stt.language: auto` available for multilingual use.

7. **Barge-in behavior.**
   When the user speaks while TTS is playing (interrupting the assistant), should we: (a) immediately stop TTS and transcribe, (b) duck TTS volume and transcribe, or (c) ignore the speech until TTS finishes? Current lean: (a) immediate stop — the user's intent to speak should always take priority.

---

## 21. References

- [Claude Code voice mode (Mar 2026)](https://techcrunch.com/2026/03/03/claude-code-rolls-out-a-voice-mode-capability/) — native /voice feature
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — CTranslate2-based Whisper inference
- [RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) — streaming transcription library
- [WhisperX](https://github.com/m-bain/whisperX) — alignment + diarization wrapper
- [Silero VAD](https://github.com/snakers4/silero-vad) — voice activity detection
- [VoiceMode MCP](https://github.com/mbailey/voicemode) — reference MCP voice integration
- [Whisper large-v3-turbo benchmarks](https://huggingface.co/deepdml/faster-whisper-large-v3-turbo-ct2/discussions/3) — RTF measurements
- [05-stt-tts-state-of-art.md](/home/shawn/.claude/local/research/2026/03/25/voice/05-stt-tts-state-of-art.md) — Legion research document
