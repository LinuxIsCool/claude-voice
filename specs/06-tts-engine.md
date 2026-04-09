---
title: "TTS Engine — ElevenLabs Cloud + Local GPU Dual Backend"
spec: "06"
status: draft
created: 2026-03-26
author: matt
tags: [claude-voice, tts, elevenlabs, kokoro, piper, gpu]
---

# 06 — TTS Engine

## 1. Overview

claude-voice provides text-to-speech via a dual-backend architecture: ElevenLabs (cloud, highest quality, lowest latency) and local GPU (Kokoro-82M primary, Piper fallback, zero cost). Routing follows claude-llms' 3-tier pattern: Local/Free, Fast/Cheap, Frontier/Quality. The TTS engine handles voice selection, parameter tuning, caching, and serialized playback via the fcntl queue from spec 05.

TTS is always best-effort. It must never block a hook, never crash a session, never interfere with Claude Code's primary text flow. Every TTS call is wrapped in a timeout, every failure falls through to the next tier, and total silence is an acceptable outcome. The system degrades gracefully from cloud-quality neural speech down through local GPU, local CPU, and finally silence — never an error.

The engine integrates with the Theme Engine (spec 02) for per-theme voice selection, greeting templates, and personality modifiers. It integrates with the Audio Playback Engine (spec 05) for the final step: playing the generated WAV file through `pw-play` with fcntl-based queue serialization to prevent overlapping speech from concurrent subagents.

---

## 2. Backend Comparison Matrix

| Backend | Location | Latency (TTFB) | Quality | Cost | GPU Required | Streaming | Voices | Output Format |
|---------|----------|-----------------|---------|------|-------------|-----------|--------|---------------|
| ElevenLabs Flash v2.5 | Cloud | ~75ms | Excellent | 0.5 credits/char | No | Yes (chunked) | 1000s (library + clone) | mp3, pcm_16000, pcm_24000, pcm_44100 |
| ElevenLabs Turbo v2.5 | Cloud | ~250ms | Excellent | 0.5 credits/char | No | Yes (chunked) | Same as Flash | Same |
| ElevenLabs Multilingual v2 | Cloud | ~400ms | Best (29 langs) | 1.0 credits/char | No | Yes (chunked) | Same as Flash | Same |
| ElevenLabs v3 | Cloud | ~300ms | Best (emotional) | 1.0 credits/char | No | Yes (chunked) | Same as Flash | Same |
| Kokoro-82M | Local GPU | ~200-500ms | Good | Free | Yes (RTX 4070) | No | ~10 built-in presets | WAV (configurable rate) |
| Piper TTS | Local CPU | ~100-300ms | Decent | Free | No | No | 100+ downloadable | WAV 22050Hz or 16000Hz |
| pyttsx3 | Local CPU | ~50ms | Basic | Free | No | No | System voices (espeak-ng) | Direct playback |

Notes on the matrix:

- **ElevenLabs Flash v2.5** (`eleven_flash_v2_5`): ElevenLabs recommends Flash over Turbo in all use cases — same quality, lower average latency. The `eleven_turbo_v2_5` model is functionally equivalent but with higher average latency.
- **ElevenLabs v3** (`eleven_v3`): Their newest model (2025). Most expressive, best emotional range, contextual understanding. Higher cost at 1.0 credits/char. Worth it for high-quality narration, not for quick status blurbs.
- **Kokoro-82M**: StyleTTS2 + ISTFTNet architecture. Won the TTS Spaces Arena against models 5-15x its size. 82M parameters, ~400MB VRAM. Apache 2.0.
- **Piper TTS**: VITS-based, ONNX runtime. Runs on CPU including Raspberry Pi. espeak-ng phonemizer under the hood. Apache 2.0.
- **pyttsx3**: Wraps espeak-ng (already installed at `/usr/bin/espeak-ng` v1.52.0). Robotic quality. Last resort only.

---

## 3. 3-Tier Routing Logic

Mirroring claude-llms' tiered model selection pattern:

```
Tier 1 (Local/Free):     Kokoro-82M → Piper → pyttsx3
Tier 2 (Fast/Cheap):     ElevenLabs Flash v2.5
Tier 3 (Frontier):       ElevenLabs v3 / Multilingual v2
```

### Routing Decision Table

| Condition | Tier | Backend | Rationale |
|-----------|------|---------|-----------|
| No `ELEVENLABS_API_KEY` set | 1 | Kokoro → Piper → pyttsx3 | Cannot reach cloud |
| `tts.backend: local` | 1 | Kokoro → Piper → pyttsx3 | Explicit local preference |
| `tts.backend: elevenlabs` | 2 | Flash v2.5 | Explicit cloud preference |
| `tts.backend: auto` (default) | 1 | Kokoro → Piper → pyttsx3 | Default is free/local |
| `tts.backend: auto` + local fails | 2 | Flash v2.5 | Automatic fallback |
| `tts.quality: best` | 3 | v3 / Multilingual v2 | Highest quality requested |
| `tts.quality: best` + no API key | 1 | Kokoro → Piper → pyttsx3 | Can't reach Tier 3 |
| Rate limited (429 from ElevenLabs) | 1 | Kokoro → Piper → pyttsx3 | Temporary fallback |
| GPU OOM on Kokoro | 1 | Piper → pyttsx3 | Skip GPU, use CPU |

### Routing Pseudocode

```python
def resolve_backend(config: dict, text: str) -> Backend:
    """Determine which TTS backend to use for this request."""
    backend_pref = config.get("tts", {}).get("backend", "auto")
    quality_pref = config.get("tts", {}).get("quality", "normal")
    has_api_key = bool(os.getenv("ELEVENLABS_API_KEY"))

    # Explicit cloud request
    if backend_pref == "elevenlabs":
        if not has_api_key:
            return resolve_local_chain()
        if quality_pref == "best":
            return ElevenLabsBackend(model="eleven_v3")
        return ElevenLabsBackend(model="eleven_flash_v2_5")

    # Explicit local request
    if backend_pref == "local":
        return resolve_local_chain()

    # Auto: try local first, fall back to cloud
    local = resolve_local_chain(probe_only=True)
    if local is not None:
        return local
    if has_api_key:
        return ElevenLabsBackend(model="eleven_flash_v2_5")
    return resolve_local_chain()  # will return pyttsx3 or None


def resolve_local_chain(probe_only: bool = False) -> Optional[Backend]:
    """Walk the local backend chain: Kokoro → Piper → pyttsx3."""
    if kokoro_available():
        return KokoroBackend()
    if piper_available():
        return PiperBackend()
    if pyttsx3_available():
        return Pyttsx3Backend()
    return None
```

### Backend Availability Probes

Each backend is probed once at module import time and the result cached:

| Backend | Probe Method | Cached As |
|---------|-------------|-----------|
| Kokoro | `import kokoro` succeeds + CUDA available | `_kokoro_available: bool` |
| Piper | `shutil.which("piper")` or `import piper` | `_piper_available: bool` |
| pyttsx3 | `import pyttsx3` succeeds | `_pyttsx3_available: bool` |
| ElevenLabs | `ELEVENLABS_API_KEY` env var is non-empty | `_elevenlabs_available: bool` |

---

## 4. Core API (`lib/tts.py`)

### Module Interface

```python
"""TTS engine for claude-voice.

Dual-backend text-to-speech with 3-tier routing:
  Tier 1: Kokoro-82M (GPU) → Piper (CPU) → pyttsx3 (espeak)
  Tier 2: ElevenLabs Flash v2.5 (cloud, fast)
  Tier 3: ElevenLabs v3 / Multilingual v2 (cloud, best)

Playback happens via lib/audio.play_sound() with queue mode (fcntl lock).
All public functions are safe to call from hook contexts — they never raise,
never block indefinitely, and degrade gracefully to silence.

Dependencies: httpx (for ElevenLabs), kokoro (optional), piper-tts (optional), pyttsx3 (optional)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CACHE_DIR = Path.home() / ".claude" / "local" / "voice" / "cache" / "tts"
"""Directory for cached TTS audio files."""

CACHE_TTL_DAYS = 30
"""Default cache retention in days."""

CACHE_MAX_MB = 500
"""Maximum cache size in megabytes before LRU eviction."""

SUBAGENT_MAX_WORDS = 20
"""Maximum words for subagent summary TTS."""

SUBAGENT_TIMEOUT_SECONDS = 10
"""Total time budget for subagent summary generation + TTS."""

TARGET_SAMPLE_RATE = 48000
"""Target sample rate matching PipeWire native quantum."""

TARGET_CHANNELS = 2
"""Stereo output matching default sink spec."""

TARGET_BIT_DEPTH = 16
"""16-bit signed integer PCM."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def speak(
    text: str,
    voice: str = "default",
    backend: str = "auto",
    speed: float = 1.0,
    emotion: str = "neutral",
    cache: bool = True,
) -> Optional[Path]:
    """Convert text to speech audio and play it.

    Returns path to generated WAV file (for caching), or None on failure.
    Playback happens via lib/audio.play_sound() with queue mode (fcntl lock).

    Parameters:
        text: The text to speak. Empty string returns None immediately.
        voice: Voice name or ID. "default" resolves via theme config.
        backend: "auto", "local", or "elevenlabs". Determines tier selection.
        speed: Speech rate multiplier (0.5 to 2.0).
        emotion: Emotional modifier key ("neutral", "success", "error", "alert",
                 "calm", "excited"). Applied as parameter adjustments per the
                 EMOTION_MODIFIERS table and theme personality_modifiers.
        cache: Whether to cache the generated audio. Set False for dynamic
               content (timestamps, session-specific text).

    Returns:
        Path to the WAV file (cached or freshly generated), or None if all
        backends failed or text was empty.

    Behavior:
        1. Check cache (if enabled) — cache hit returns immediately (~5ms)
        2. Resolve backend via 3-tier routing
        3. Generate audio via selected backend
        4. Resample to 48kHz 16-bit stereo WAV if needed
        5. Write to cache (if enabled)
        6. Play via lib/audio.play_sound(path, category="tts", mode="queue")
        7. Return path

    Failure modes:
        - All backends fail → returns None, logs warning, no sound
        - Timeout exceeded → returns None, partial cache file cleaned up
        - Cache write fails → audio still plays, caching disabled for this call
    """


def speak_async(text: str, **kwargs) -> None:
    """Fire-and-forget TTS — forks a subprocess to handle speech.

    Identical parameters to speak(). Spawns a detached child process via
    subprocess.Popen with start_new_session=True. The parent returns
    immediately. Used in hook contexts where the hook must exit fast.

    The child process:
        1. Acquires the fcntl TTS lock (waits up to 30s)
        2. Calls speak() synchronously
        3. Releases the lock
        4. Exits

    If the child cannot acquire the lock within 30s, it exits silently.
    If the child crashes, the lock file's PID-based stale detection handles
    cleanup on the next invocation (see spec 05, fcntl queue).
    """


def list_voices(backend: str = "all") -> list[dict]:
    """List available voices across all backends.

    Parameters:
        backend: "all", "elevenlabs", "kokoro", "piper", or "pyttsx3".

    Returns:
        List of dicts, each with:
            - id: str — backend-specific voice identifier
            - name: str — human-readable name
            - backend: str — which backend owns this voice
            - language: str — primary language code (e.g., "en")
            - gender: str — "male", "female", or "neutral"
            - preview_url: Optional[str] — URL or path to sample audio

    For ElevenLabs: queries /v1/voices API (cached for 1 hour).
    For Kokoro: returns the ~10 built-in presets.
    For Piper: scans installed models in ~/.local/share/piper-voices/.
    For pyttsx3: queries the system engine for available voices.
    """


def preview_voice(voice_id: str, sample_text: str = "Hello, I am your assistant.") -> None:
    """Play a voice sample.

    Determines which backend owns the voice_id, generates speech for the
    sample_text, and plays it immediately (bypasses cache, uses "interrupt"
    playback mode so previous preview stops).
    """


def get_backend_status() -> dict:
    """Health check all backends, return availability status.

    Returns:
        {
            "kokoro": {"available": true, "version": "0.9.2", "device": "cuda"},
            "piper": {"available": true, "version": "2.0.0", "models": 3},
            "pyttsx3": {"available": true, "engine": "espeak-ng"},
            "elevenlabs": {
                "available": true,
                "model": "eleven_flash_v2_5",
                "quota_remaining": 45000,
                "quota_total": 100000,
            },
            "active_tier": 1,
            "active_backend": "kokoro",
            "cache": {
                "entries": 142,
                "size_mb": 87.3,
                "hit_rate_pct": 34.2,
            }
        }
    """
```

### Internal Helper Functions

```python
def _generate_cache_key(text: str, voice: str, backend: str, speed: float, emotion: str) -> str:
    """SHA256 hash of all parameters that affect audio output."""
    payload = json.dumps({
        "text": text,
        "voice": voice,
        "backend": backend,
        "speed": speed,
        "emotion": emotion,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _cache_lookup(key: str) -> Optional[Path]:
    """Check if a cached WAV exists and is within TTL."""
    path = CACHE_DIR / f"{key}.wav"
    if not path.exists():
        return None
    age_days = (time.time() - path.stat().st_mtime) / 86400
    if age_days > CACHE_TTL_DAYS:
        path.unlink(missing_ok=True)
        return None
    # Touch the file to update access time (for LRU eviction)
    path.touch()
    return path


def _cache_store(key: str, audio_data: bytes) -> Path:
    """Write audio data to cache, enforce size limit."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{key}.wav"
    path.write_bytes(audio_data)
    _enforce_cache_limit()
    return path


def _enforce_cache_limit() -> None:
    """LRU eviction: delete oldest files until cache is under CACHE_MAX_MB."""
    total_bytes = sum(f.stat().st_size for f in CACHE_DIR.glob("*.wav"))
    if total_bytes <= CACHE_MAX_MB * 1024 * 1024:
        return
    # Sort by modification time (oldest first), delete until under limit
    files = sorted(CACHE_DIR.glob("*.wav"), key=lambda f: f.stat().st_mtime)
    for f in files:
        if total_bytes <= CACHE_MAX_MB * 1024 * 1024:
            break
        size = f.stat().st_size
        f.unlink(missing_ok=True)
        total_bytes -= size


def _resample_to_target(input_path: Path, output_path: Path) -> None:
    """Resample audio to 48kHz 16-bit stereo WAV using ffmpeg."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(input_path),
            "-ar", str(TARGET_SAMPLE_RATE),
            "-ac", str(TARGET_CHANNELS),
            "-sample_fmt", f"s{TARGET_BIT_DEPTH}",
            "-f", "wav",
            str(output_path),
        ],
        capture_output=True,
        timeout=10,
    )
```

---

## 5. ElevenLabs Integration

### 5a. Text-to-Speech API

**Endpoint**: `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}`

**Headers**:
```
xi-api-key: {ELEVENLABS_API_KEY}
Content-Type: application/json
Accept: audio/mpeg  (or audio/pcm for raw PCM)
```

**Request Body**:
```json
{
  "text": "Commander, authentication module is ready.",
  "model_id": "eleven_flash_v2_5",
  "voice_settings": {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.3,
    "use_speaker_boost": true
  },
  "output_format": "pcm_24000"
}
```

**Models available**:

| Model ID | Use Case | Latency | Languages | Credits/Char |
|----------|----------|---------|-----------|--------------|
| `eleven_flash_v2_5` | Real-time, status blurbs, subagent summaries | ~75ms TTFB | 32 | 0.5 |
| `eleven_turbo_v2_5` | Legacy — same as Flash but higher latency | ~250ms TTFB | 32 | 0.5 |
| `eleven_multilingual_v2` | Multi-language content | ~400ms TTFB | 29 | 1.0 |
| `eleven_v3` | Maximum expressiveness, long narration | ~300ms TTFB | 32+ | 1.0 |

**Recommendation**: Use `eleven_flash_v2_5` for all Tier 2 requests. Use `eleven_v3` for Tier 3 (quality: best). Never use `eleven_turbo_v2_5` — Flash supersedes it in all dimensions.

**Output format selection**:

| Format | Sample Rate | Size/sec | Quality | Use When |
|--------|------------|----------|---------|----------|
| `mp3_44100_128` | 44.1kHz | ~16KB/s | Good | Default for most APIs, needs decode |
| `pcm_16000` | 16kHz | 32KB/s | Low | Telephony only |
| `pcm_24000` | 24kHz | 48KB/s | Good | Good balance, needs resample to 48kHz |
| `pcm_44100` | 44.1kHz | 88KB/s | Best PCM | Best raw quality, needs resample to 48kHz |

**For claude-voice**: Request `pcm_24000` (good quality, reasonable bandwidth). Resample to 48kHz 16-bit stereo WAV via ffmpeg before playback and caching. This avoids MP3 decode overhead and matches the PipeWire native quantum.

### 5b. Streaming Text-to-Speech API

**Endpoint**: `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream`

Same request body as non-streaming. Response is chunked transfer encoding — audio bytes arrive as they are generated.

**Streaming workflow for claude-voice**:

```
1. Open HTTP connection with chunked transfer
2. Receive first audio chunk (~75ms after request)
3. Write chunk to temp file
4. Start pw-play on the temp file (pw-play handles growing files)
5. Continue receiving and appending chunks
6. On stream complete: rename temp file to cache path
```

**When to use streaming**: Text longer than approximately 50 words (roughly 300 characters). Below that threshold, full generation completes so quickly (~200ms) that the streaming overhead of managing chunked I/O is not worth the complexity.

**When NOT to stream**: Subagent summaries (always < 20 words), error messages (short), greeting blurbs (templated, often cached).

### 5c. Voice Library

ElevenLabs provides three voice acquisition methods:

**Pre-made voices** (1000+ in library):
- Browse via `GET /v1/voices` — returns all voices available to the account
- Each voice has: `voice_id`, `name`, `category`, `labels` (accent, age, gender, use_case)
- Shared voices from community library available via `GET /v1/shared-voices`

**Voice Design API** (`POST /v1/voice-generation/generate-voice`):
- Create voices from text descriptions: "A warm female voice with a slight British accent, age 30-40"
- Useful for generating theme-specific voices without manual cloning

**Voice Cloning**:
- Instant clone: `POST /v1/voice-generation/clone-voice` — clone from a single audio sample
- Professional clone: requires 30+ minutes of clean audio, highest quality
- For claude-voice: not needed initially. Pre-made voices cover all theme needs.

**Voice selection strategy for claude-voice**:
1. Each theme defines a `voice_id` in its `theme.json` `tts` section (see spec 02)
2. If `voice_id` is null, use the system default from `config.yaml`
3. The system default should be a general-purpose voice (e.g., "Rachel" — calm, professional)
4. Theme-specific voices chosen for character fit (see Voice Catalog, Section 8)

### 5d. Sound Effects API V2

**Endpoint**: `POST /v1/sound-generation`

**Request Body**:
```json
{
  "text": "A deep space radar ping echoing in a metallic command center",
  "duration_seconds": 2.0,
  "prompt_influence": 0.5
}
```

**Properties**:
- Generate sound effects from text prompts
- Duration: 0.5 to 22 seconds
- `prompt_influence`: 0.0 to 1.0 — how closely to follow the prompt vs. model creativity
- Output: MP3 audio
- Cost: credits based on duration

**For claude-voice**: This is a potential source for theme sound effects. Instead of manually designing earcons, we could generate theme-specific sounds dynamically:
- StarCraft: "Short sci-fi servo whir with radio click"
- Zelda: "Fairy chime with soft magical sparkle"
- Mario: "8-bit coin collect with bright metallic ring"

This is a Phase 2 capability. Initial implementation uses pre-designed WAV assets. The sound generation API becomes valuable when expanding the theme library or allowing user-created themes.

### 5e. Parameter Tuning Guide

**Stability** (0.0 to 1.0): Controls voice consistency. Higher = more monotone, lower = more expressive but potentially unstable.

**Similarity Boost** (0.0 to 1.0): How closely to match the original voice. Higher = more faithful, lower = more generic but potentially more natural.

**Style** (0.0 to 1.0): Controls emotional expressiveness. Higher = more dramatic, lower = more neutral. Only available on v2+ models.

**Speaker Boost** (boolean): Enhances voice clarity and reduces background artifacts. Small latency cost. Recommended for short-form TTS.

**Per-use-case tuning**:

| Use Case | Model | Stability | Similarity | Style | Speaker Boost | Rationale |
|----------|-------|-----------|------------|-------|--------------|-----------|
| Subagent summary | Flash v2.5 | 0.5 | 0.75 | 0.3 | true | Quick, clear, professional. Low style to avoid over-dramatizing status updates. |
| Error narration | Flash v2.5 | 0.7 | 0.75 | 0.5 | true | Higher stability for gravitas. Moderate style for urgency without panic. |
| Ambient narration | v3 | 0.3 | 0.5 | 0.7 | false | Low stability for natural variance. High style for atmospheric delivery. No boost — ambient should blend. |
| Brief reading | Flash v2.5 | 0.5 | 0.75 | 0.5 | true | Balanced across all axes. Default "read this aloud" profile. |
| Session greeting | Flash v2.5 | 0.4 | 0.75 | 0.6 | true | Slightly lower stability for warmth. Higher style for personality. Theme greeting template shapes content, voice params shape delivery. |
| Level-up announcement | v3 | 0.3 | 0.75 | 0.8 | true | Maximum expressiveness for celebratory moments. High style for excitement. |

---

## 6. Local GPU Backend (Kokoro-82M)

### Architecture

Kokoro-82M is a StyleTTS2 + ISTFTNet model. No diffusion, no encoder — decoder-only. This makes it exceptionally fast for its quality level. It won the TTS Spaces Arena on HuggingFace, outperforming XTTS-v2 (467M) and MetaVoice (1.2B).

### Hardware Profile (This Machine)

| Property | Value |
|----------|-------|
| GPU | NVIDIA RTX 4070 12GB VRAM |
| Kokoro VRAM usage | ~400-500MB |
| Remaining VRAM for other tasks | ~11.5GB |
| Inference speed | ~36x real-time on modern GPU |
| Typical sentence latency | 200-500ms |
| Can coexist with Whisper | Yes (large-v3-turbo uses ~3.2GB) |

### Installation

```bash
# Dedicated venv recommended (don't pollute whisperx-env)
uv venv ~/.local/share/kokoro-env
source ~/.local/share/kokoro-env/bin/activate.fish
uv pip install kokoro soundfile torch

# Or as a uv tool:
uv tool install kokoro
```

Model files download automatically on first use to `~/.cache/huggingface/hub/`.

### Voice Presets

Kokoro ships with approximately 10 built-in voice presets using a naming convention:

| Preset | Gender | Character | Notes |
|--------|--------|-----------|-------|
| `af_default` | Female | Default, neutral | General purpose |
| `af_bella` | Female | Warm, friendly | Good for Mario, casual themes |
| `af_sarah` | Female | Clear, professional | Good for Zelda, ethereal themes |
| `af_nicole` | Female | Soft, gentle | Good for Kingdom Hearts |
| `am_adam` | Male | Authoritative, deep | Good for StarCraft |
| `am_michael` | Male | Energetic, dynamic | Good for Smash Bros |
| `bf_emma` | Female (British) | Warm, composed | Good for Warcraft |
| `bm_george` | Male (British) | Measured, formal | Alternative for Warcraft |

Note: Exact preset names and availability may vary by Kokoro version. The `af_` prefix indicates American female, `am_` American male, `bf_` British female, `bm_` British male.

### Integration Pattern

```python
def _kokoro_synthesize(text: str, voice: str, speed: float) -> Optional[bytes]:
    """Generate speech via Kokoro-82M.

    Returns raw WAV bytes, or None on failure.
    """
    try:
        from kokoro import KPipeline

        pipeline = _get_kokoro_pipeline()  # cached singleton
        samples, sample_rate = pipeline(
            text,
            voice=voice,
            speed=speed,
        )
        # Convert to WAV bytes
        wav_bytes = _samples_to_wav(samples, sample_rate)
        return wav_bytes
    except ImportError:
        return None  # Kokoro not installed
    except RuntimeError as e:
        if "out of memory" in str(e).lower():
            # GPU OOM — fall through to Piper
            return None
        return None
    except Exception:
        return None  # Any other failure — silent
```

### Singleton Pattern

The Kokoro pipeline is expensive to initialize (model load, CUDA context). We use a module-level singleton:

```python
_kokoro_pipeline: Optional[Any] = None
_kokoro_lock = threading.Lock()

def _get_kokoro_pipeline():
    global _kokoro_pipeline
    if _kokoro_pipeline is None:
        with _kokoro_lock:
            if _kokoro_pipeline is None:
                from kokoro import KPipeline
                _kokoro_pipeline = KPipeline(lang_code="a")  # "a" = American English
    return _kokoro_pipeline
```

Note: In hook contexts where each invocation is a fresh process, the singleton provides no benefit — the model loads each time. For persistent daemon mode (future), the singleton becomes critical. The pattern is correct to include now.

### Limitations

- English-focused (American and British accents). Limited multilingual support compared to ElevenLabs.
- No streaming — full audio must generate before playback starts.
- Fewer voice options than ElevenLabs (~10 vs. 1000s).
- Requires CUDA-capable GPU for reasonable performance. CPU inference is possible but slow (~2-5x slower).
- First invocation in a process is slow (~2-3s) due to model loading. Subsequent calls are fast.

---

## 7. Piper TTS Fallback

### Role in the Stack

Piper is the CPU-based safety net. It runs when:
- Kokoro is not installed
- GPU is unavailable or OOM
- CUDA drivers are broken
- The user explicitly sets `tts.local.model: piper`

It requires no GPU, no API key, no network. If Piper and espeak-ng are installed, TTS works.

### Architecture

Piper uses a two-stage pipeline:
1. **Phonemization** via espeak-ng: text → IPA phonemes
2. **Neural vocoder** via VITS (ONNX runtime): phonemes → audio waveform

The ONNX runtime runs on CPU. On this machine (i7-13700F, 24 threads), inference is fast enough for real-time speech.

### Installation

```bash
# AUR package (recommended on CachyOS/Arch):
paru -S piper-tts-bin

# Or via pip:
uv pip install piper-tts

# Voice models download separately:
# Models stored in ~/.local/share/piper-voices/
# Download from https://github.com/rhasspy/piper/blob/master/VOICES.md
```

### Voice Models

Piper voices come in quality tiers:

| Quality | Description | Model Size | Latency (this CPU) |
|---------|-------------|------------|---------------------|
| `low` | Fastest, lowest quality | ~15MB | ~50ms |
| `medium` | Balanced | ~60MB | ~100-200ms |
| `high` | Best quality | ~100MB | ~200-300ms |

Recommended voices for claude-voice themes:

| Voice | Language | Quality | Gender | Size | Notes |
|-------|----------|---------|--------|------|-------|
| `en_US-amy-medium` | en-US | Medium | Female | ~60MB | Good general purpose, clear diction |
| `en_US-ryan-high` | en-US | High | Male | ~100MB | Authoritative, good for StarCraft/Smash |
| `en_US-lessac-medium` | en-US | Medium | Male | ~60MB | Warm, natural, good for Mario |
| `en_GB-alan-medium` | en-GB | Medium | Male | ~60MB | British, good for Warcraft |

### Integration Pattern

```python
def _piper_synthesize(text: str, model: str, speed: float) -> Optional[bytes]:
    """Generate speech via Piper TTS.

    Returns raw WAV bytes, or None on failure.
    Piper outputs 16-bit mono WAV at the model's native rate (usually 22050Hz).
    Caller is responsible for resampling to 48kHz stereo.
    """
    try:
        result = subprocess.run(
            [
                "piper",
                "--model", model,
                "--length-scale", str(1.0 / speed),  # Piper uses inverse scale
                "--output-raw",
            ],
            input=text.encode(),
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
```

### pyttsx3 Last Resort

If Piper is also unavailable, `pyttsx3` wraps the system's espeak-ng:

```python
def _pyttsx3_synthesize(text: str, speed: float) -> Optional[bytes]:
    """Generate speech via pyttsx3 (espeak-ng wrapper).

    Quality is robotic but functional. This is the absolute last resort.
    """
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", int(180 * speed))  # 180 WPM baseline

        # Save to temp file
        tmp = Path("/tmp/claude-voice-pyttsx3.wav")
        engine.save_to_file(text, str(tmp))
        engine.runAndWait()

        if tmp.exists():
            data = tmp.read_bytes()
            tmp.unlink(missing_ok=True)
            return data
        return None
    except Exception:
        return None
```

---

## 8. Voice Catalog

Per-theme voice assignments. Each theme maps to a specific voice per backend. The theme's `tts.voice_id` in `theme.json` holds the ElevenLabs voice ID. The local voice presets are resolved by the TTS engine based on theme slug.

| Theme | ElevenLabs Voice | ElevenLabs Voice ID | Kokoro Preset | Piper Model | Greeting Template |
|-------|-----------------|---------------------|---------------|-------------|-------------------|
| default | Rachel (calm, professional) | `21m00Tcm4TlvDq8ikWAM` | `af_default` | `en_US-amy-medium` | `"{summary}"` |
| starcraft | Adam (authoritative, commanding) | `pNInz6obpgDQGcFmaJgB` | `am_adam` | `en_US-ryan-high` | `"Commander, {summary}."` |
| warcraft | Antoni (warm, deliberate) | `ErXwobaYiN019PkySvjV` | `bf_emma` | `en_GB-alan-medium` | `"Work complete, my lord. {summary}."` |
| mario | Elli (cheerful, bright) | `MF3mGyEYCl7XYWbV9V6O` | `af_bella` | `en_US-lessac-medium` | `"Wahoo! {summary}!"` |
| zelda | Bella (ethereal, gentle) | `EXAVITQu4vr4xnSDxMaL` | `af_sarah` | `en_US-amy-medium` | `"Hey! Listen! {summary}."` |
| smash | Josh (energetic, punchy) | `TxGEqnHWrfWFTfGW9XjX` | `am_michael` | `en_US-ryan-high` | `"{summary} — GAME!"` |
| kingdom-hearts | Grace (warm, hopeful) | `oWAxZDx7w5VEj9dCyTzz` | `af_nicole` | `en_US-amy-medium` | `"{summary}. May your heart be your guiding key."` |

**Voice ID note**: The ElevenLabs voice IDs listed above are examples from the public voice library. Actual IDs should be verified against the user's ElevenLabs account. The `/voice setup` command (future) will allow browsing and selecting voices interactively.

**Template resolution**: The `{summary}` placeholder is replaced at runtime with a compressed summary of the event context. For SubagentStop, this is the AI-generated task blurb (< 20 words). For SessionStart, it is a context summary (branch, pending tasks). For Stop, it is a completion message.

---

## 9. Caching Strategy

### Cache Architecture

```
~/.claude/local/voice/cache/tts/
  {sha256_hash}.wav          # Cached TTS audio files
  _meta.json                 # Cache metadata (hit counts, total size)
```

### Cache Key Derivation

The cache key is a SHA256 hash of all parameters that affect audio output:

```python
key_input = json.dumps({
    "text": text,              # The spoken text
    "voice": voice,            # Voice ID or preset name
    "backend": backend,        # Which backend generated it
    "speed": speed,            # Speech rate
    "emotion": emotion,        # Emotional modifier
}, sort_keys=True)

cache_key = hashlib.sha256(key_input.encode()).hexdigest()
```

This means the same text spoken with different voices, speeds, or emotions produces different cache entries. Changing the backend also invalidates the cache (Kokoro and ElevenLabs produce different audio for the same text).

### Cache Behavior

| Scenario | Behavior |
|----------|----------|
| Cache hit (file exists, within TTL) | Skip TTS entirely, play cached file directly (~5ms) |
| Cache miss | Generate audio, store in cache, then play |
| Cache expired (beyond TTL) | Delete stale file, regenerate |
| Cache disabled (`cache: false` param) | Generate audio, do not store |
| Cache disabled (dynamic text) | Auto-detected: text containing timestamps, UUIDs, or session IDs skips cache |
| Cache disk full | Disable caching for this call, continue with live TTS, log warning |

### Cache Configuration

| Parameter | Default | Config Key | Description |
|-----------|---------|------------|-------------|
| TTL | 30 days | `tts.cache_ttl_days` | How long cached files persist |
| Max size | 500 MB | `tts.cache_max_mb` | LRU eviction threshold |
| Enabled | true | `tts.cache` | Master cache toggle |

### LRU Eviction

When total cache size exceeds `cache_max_mb`:
1. List all `.wav` files in cache directory
2. Sort by modification time (oldest first)
3. Delete oldest files until total size is under the limit
4. File access (cache hit) updates mtime via `touch()`, keeping frequently-used files fresh

### Cache Size Estimation

Typical TTS output sizes (48kHz, 16-bit, stereo):

| Text Length | Approx. Speech Duration | WAV Size |
|-------------|------------------------|----------|
| 10 words (subagent summary) | ~3 seconds | ~576 KB |
| 20 words (greeting) | ~6 seconds | ~1.15 MB |
| 50 words (brief reading) | ~15 seconds | ~2.88 MB |

At 500 MB cache limit: approximately 430 subagent summaries, or 250 medium-length utterances. With aggressive caching of repeated greetings and common phrases, this is more than sufficient for weeks of use.

### Dynamic Text Detection

Certain text patterns should bypass caching because they will never repeat:

```python
DYNAMIC_PATTERNS = [
    r"\d{4}-\d{2}-\d{2}",          # ISO dates
    r"\d{2}:\d{2}:\d{2}",          # Timestamps
    r"[0-9a-f]{8}-[0-9a-f]{4}",    # UUID fragments
    r"session[_-]?[a-z0-9]{6,}",   # Session IDs
]
```

If any pattern matches the input text, caching is skipped for that call regardless of the `cache` parameter.

---

## 10. Streaming TTS (ElevenLabs Only)

### Why Streaming Matters

For short text (< 50 words, typical for claude-voice), streaming adds complexity without meaningful latency improvement. But for longer narration — reading a file summary, explaining a diff, ambient storytelling — the difference is significant:

| Text Length | Non-Streaming Total | Streaming TTFB | Perceived Improvement |
|-------------|--------------------|-----------------|-----------------------|
| 10 words | ~200ms | ~100ms | Negligible |
| 50 words | ~500ms | ~100ms | Noticeable |
| 200 words | ~2000ms | ~100ms | Major (1.9s saved) |

### Streaming Implementation

```python
def _elevenlabs_stream(text: str, voice_id: str, params: dict) -> Optional[Path]:
    """Stream audio from ElevenLabs, start playback before generation completes.

    Returns path to the completed WAV file for caching.
    """
    import httpx

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    headers = {
        "xi-api-key": os.getenv("ELEVENLABS_API_KEY"),
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    body = {
        "text": text,
        "model_id": params.get("model", "eleven_flash_v2_5"),
        "voice_settings": {
            "stability": params.get("stability", 0.5),
            "similarity_boost": params.get("similarity_boost", 0.75),
            "style": params.get("style", 0.3),
            "use_speaker_boost": params.get("use_speaker_boost", True),
        },
        "output_format": "pcm_24000",
    }

    tmp_path = Path("/tmp") / f"claude-voice-stream-{os.getpid()}.raw"
    wav_path = CACHE_DIR / f"stream-{os.getpid()}.wav"
    playback_started = False

    with httpx.stream("POST", url, json=body, headers=headers, timeout=30.0) as response:
        if response.status_code != 200:
            return None

        with open(tmp_path, "wb") as f:
            for chunk in response.iter_bytes(chunk_size=4096):
                f.write(chunk)
                f.flush()

                # Start playback after first ~100ms of audio received
                if not playback_started and tmp_path.stat().st_size > 4800:
                    # 4800 bytes = ~100ms at 24kHz 16-bit mono
                    _start_streaming_playback(tmp_path)
                    playback_started = True

    # Convert completed raw PCM to WAV, resample to 48kHz
    _resample_to_target(tmp_path, wav_path)
    tmp_path.unlink(missing_ok=True)
    return wav_path
```

### Decision Logic: Stream vs. Full Generation

```python
STREAMING_WORD_THRESHOLD = 50

def _should_stream(text: str, backend: str) -> bool:
    """Determine whether to use streaming TTS."""
    if backend != "elevenlabs":
        return False  # Only ElevenLabs supports streaming
    word_count = len(text.split())
    return word_count > STREAMING_WORD_THRESHOLD
```

---

## 11. Subagent Summary TTS

This is the highest-value TTS integration — spoken feedback when a subagent completes its task. Based directly on Disler's `subagent_stop.py` + `task_summarizer.py` pattern.

### Event Flow

```
1. SubagentStop hook fires
   └── stdin: {"type": "SubagentStop", "data": {"session_id": "...", "transcript": [...]}}

2. Extract task description from transcript
   └── First user message in the subagent's transcript = the task assignment

3. Generate summary (< 20 words)
   └── Call claude -p with Haiku: "Summarize this task completion in under 20 words for TTS"
   └── Fallback: extract first sentence of the task assignment

4. Apply theme greeting template
   └── "Commander, {summary}."  (StarCraft)
   └── "Work complete, my lord. {summary}."  (Warcraft)

5. Speak via TTS with fcntl queue
   └── acquire_tts_lock(agent_id, timeout=30)
   └── speak(formatted_text, emotion="success")
   └── release_tts_lock(agent_id)

6. Total time budget: 10 seconds
   └── Summary generation: max 5s
   └── TTS generation + playback: max 5s
   └── If either exceeds budget: abort silently
```

### Summary Generation

```python
SUMMARY_SYSTEM_PROMPT = """You are a TTS summary generator. Given a task description,
produce a concise completion message under 20 words. Speak as if reporting task status
to a team lead. No code, no technical jargon, no markdown. Just a clean spoken sentence.

Examples:
- "Authentication module is ready with JWT support."
- "Database migration completed, three new tables created."
- "Fixed the race condition in the session handler."
"""

def _generate_subagent_summary(transcript: list[dict]) -> str:
    """Extract task and generate a <20 word TTS-friendly summary."""
    # Extract initial task from transcript
    task_text = ""
    for entry in transcript:
        if entry.get("type") == "user":
            task_text = entry.get("content", "")[:500]  # First user message
            break

    if not task_text:
        return "Task complete."

    try:
        result = subprocess.run(
            [
                "claude", "-p",
                f"{SUMMARY_SYSTEM_PROMPT}\n\nTask: {task_text}\n\nSummary:",
                "--model", "haiku",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        summary = result.stdout.strip()
        if summary and len(summary.split()) <= SUBAGENT_MAX_WORDS:
            return summary
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback: first sentence of task, truncated to 20 words
    words = task_text.split()[:SUBAGENT_MAX_WORDS]
    return " ".join(words) + "."
```

### fcntl Queue Integration

Multiple subagents can complete simultaneously (e.g., builder + validator in team pattern). The fcntl file lock from spec 05 serializes TTS so speeches do not overlap:

```
Lock file: ~/.claude/local/voice/data/tts.lock
Lock metadata: {"agent_id": "...", "timestamp": ..., "pid": ...}
Retry: exponential backoff 100ms → 1s cap
Timeout: 30s
Stale lock: PID liveness check before cleanup
```

Pattern in the SubagentStop hook:

```python
from lib.tts_queue import acquire_tts_lock, release_tts_lock, cleanup_stale_locks

cleanup_stale_locks(max_age_seconds=60)
agent_id = hook_data["data"].get("session_id", "unknown")

if acquire_tts_lock(agent_id, timeout=30):
    try:
        summary = _generate_subagent_summary(transcript)
        greeting = theme["tts"]["greeting_template"].format(summary=summary)
        speak(greeting, emotion="success")
    finally:
        release_tts_lock(agent_id)
```

### Failure Behavior

TTS is best-effort. The subagent hook must never block Claude Code:

| Failure | Response |
|---------|----------|
| Summary generation times out (> 5s) | Use fallback "Task complete." |
| TTS generation times out (> 5s) | Skip speech entirely |
| Lock acquisition times out (> 30s) | Skip speech entirely |
| Total time exceeds 10s | Abort silently |
| Any exception | Catch, log, continue |

---

## 12. Emotion Modifiers

Emotions affect TTS output through two mechanisms:
1. **Backend parameters** — stability, speed, pitch adjustments sent to the TTS engine
2. **Theme personality modifiers** — per-theme overrides from `theme.json` `tts.personality_modifiers`

### Base Emotion Table

```python
EMOTION_MODIFIERS = {
    "neutral": {
        "pitch_shift": 0,
        "speed": 1.0,
        "stability": 0.5,
        "style": 0.3,
    },
    "success": {
        "pitch_shift": 0,
        "speed": 1.0,
        "stability": 0.5,
        "style": 0.4,
    },
    "error": {
        "pitch_shift": -2,
        "speed": 0.9,
        "stability": 0.7,
        "style": 0.5,
    },
    "alert": {
        "pitch_shift": 2,
        "speed": 1.1,
        "stability": 0.6,
        "style": 0.4,
    },
    "calm": {
        "pitch_shift": 0,
        "speed": 0.95,
        "stability": 0.3,
        "style": 0.2,
    },
    "excited": {
        "pitch_shift": 3,
        "speed": 1.15,
        "stability": 0.4,
        "style": 0.7,
    },
}
```

### Event-to-Emotion Mapping

| Hook Event | Content | Emotion |
|------------|---------|---------|
| Stop | git commit detected | `excited` |
| Stop | error/failure detected | `error` |
| Stop | normal completion | `success` |
| SubagentStop | any | `success` |
| Notification | any | `alert` |
| SessionStart | any | `calm` |
| SessionEnd | any | `calm` |
| PreCompact | any | `neutral` |

### Parameter Merge Order

When computing final TTS parameters for a request:

```
1. Start with backend defaults (e.g., ElevenLabs: stability=0.5, similarity=0.75)
2. Apply base emotion modifiers from EMOTION_MODIFIERS table
3. Apply theme personality_modifiers from theme.json (overrides base emotions)
4. Apply any per-call overrides from speak() parameters
```

Theme personality modifiers from `theme.json` take precedence over the base emotion table. This allows StarCraft's "error" to sound different from Mario's "error" — both start from the same base but the theme can push parameters in character-appropriate directions.

### Pitch Shift Implementation

**ElevenLabs**: Does not support explicit pitch shift. The `stability` and `style` parameters approximate emotional range. Pitch shift values in the emotion table are ignored for ElevenLabs — the style parameter serves the same purpose.

**Kokoro**: Supports `speed` parameter. Pitch shift can be approximated by resampling the output audio (pitch up = resample higher then play at original rate). Not worth the complexity for V1 — Kokoro's natural prosody handles most emotional variation through the text itself.

**Piper**: Supports `--length-scale` (inverse speed). No pitch shift. Same approach as Kokoro — rely on natural prosody.

**pyttsx3**: Supports `rate` (WPM) and `pitch` properties directly via the espeak-ng engine.

---

## 13. Cost Optimization

### Character Budget Awareness

ElevenLabs charges per character. Every character of every TTS call counts. Optimization strategies:

**Cache aggressively**: Repeated phrases (theme greetings, common status messages) should hit cache on all but the first occurrence. The greeting template for a theme is mostly static — only the `{summary}` portion varies.

**Use Flash v2.5 by default**: At 0.5 credits/character, Flash is half the cost of Multilingual v2 or v3 (1.0 credits/char). Only escalate to higher-cost models when `quality: best` is explicitly requested.

**Default to Tier 1 (local)**: The default configuration routes all TTS through local backends. ElevenLabs is opt-in. Users who want cloud quality explicitly enable it.

**Summary compression**: Subagent summaries are capped at 20 words (~100 characters). At Flash pricing, each subagent completion costs approximately 50 credits — negligible.

### Rate Limiting

```python
RATE_LIMIT_WINDOW_SECONDS = 3600  # 1 hour
RATE_LIMIT_DEFAULT = 100           # Max calls per window

_elevenlabs_call_log: list[float] = []

def _check_rate_limit() -> bool:
    """Return True if we're within the rate limit."""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    _elevenlabs_call_log[:] = [t for t in _elevenlabs_call_log if t > cutoff]
    max_calls = int(os.getenv("CLAUDE_VOICE_RATE_LIMIT", RATE_LIMIT_DEFAULT))
    return len(_elevenlabs_call_log) < max_calls
```

### Batching

When multiple SubagentStop events fire within a short window (common in team-based patterns with builder + validator):

```python
BATCH_WINDOW_SECONDS = 2.0

def _should_batch(pending_summaries: list[str]) -> bool:
    """If multiple summaries arrived within the batch window, combine them."""
    return len(pending_summaries) > 1
```

If 3 subagents complete within 2 seconds, instead of 3 separate TTS calls:
- Combine: "Builder finished auth module. Validator confirmed. Tests all passing."
- One TTS call instead of three — saves 2/3 of the character cost.

### Usage Tracking

Character usage is tracked in `~/.claude/local/voice/data/state.db`:

```sql
CREATE TABLE IF NOT EXISTS tts_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    backend TEXT NOT NULL,           -- 'elevenlabs', 'kokoro', 'piper', 'pyttsx3'
    model TEXT,                      -- 'eleven_flash_v2_5', etc.
    characters INTEGER NOT NULL,    -- Character count of input text
    credits REAL,                    -- Estimated credits consumed (ElevenLabs only)
    latency_ms REAL,                -- Total generation time
    cached BOOLEAN NOT NULL DEFAULT 0,
    cache_key TEXT
);
```

The `get_backend_status()` function queries this table to report quota usage and cache hit rates.

---

## 14. Configuration Schema

The TTS section of `~/.claude/local/voice/config.yaml`:

```yaml
tts:
  # Backend selection: auto | local | elevenlabs
  # "auto" tries local first, falls back to cloud if local fails.
  # "local" never calls cloud APIs.
  # "elevenlabs" always uses cloud (falls to local if no API key).
  backend: auto

  # Quality tier: normal | best
  # "normal" uses Flash v2.5 (cloud) or Kokoro (local).
  # "best" uses v3/Multilingual v2 (cloud) or Kokoro (local, same as normal).
  quality: normal

  # Default voice name or ID. "default" resolves per theme.
  # Can be an ElevenLabs voice ID, Kokoro preset, or Piper model name.
  voice: default

  # Speech rate multiplier (0.5 to 2.0)
  speed: 1.0

  # Enable TTS audio caching
  cache: true

  # Cache time-to-live in days
  cache_ttl_days: 30

  # Maximum cache size in megabytes (LRU eviction above this)
  cache_max_mb: 500

  # ElevenLabs-specific configuration
  elevenlabs:
    # Default model for Tier 2 requests
    model: eleven_flash_v2_5

    # Voice settings (can be overridden per theme)
    stability: 0.5
    similarity_boost: 0.75
    style: 0.3
    use_speaker_boost: true

    # Maximum API calls per hour (0 = unlimited)
    rate_limit_per_hour: 100

  # Local backend configuration
  local:
    # Primary local model: kokoro | piper | pyttsx3
    model: kokoro

    # Kokoro voice preset (see Section 6 for available presets)
    kokoro_voice: af_default

    # Piper voice model name (must be installed)
    piper_model: en_US-amy-medium

  # Subagent completion TTS
  subagent:
    # Enable spoken summaries when subagents complete
    enabled: true

    # Maximum words in the generated summary
    max_words: 20

    # Total time budget for summary generation + TTS (seconds)
    timeout_seconds: 10

    # Batch window: combine summaries arriving within this window (seconds)
    batch_window_seconds: 2.0
```

### Environment Variable Overrides

Environment variables take precedence over config.yaml values:

| Environment Variable | Config Equivalent | Description |
|---------------------|-------------------|-------------|
| `ELEVENLABS_API_KEY` | (none — secrets not in config) | ElevenLabs API key |
| `CLAUDE_VOICE_TTS_BACKEND` | `tts.backend` | Backend selection |
| `CLAUDE_VOICE_TTS_QUALITY` | `tts.quality` | Quality tier |
| `CLAUDE_VOICE_TTS_VOICE` | `tts.voice` | Default voice |
| `CLAUDE_VOICE_TTS_SPEED` | `tts.speed` | Speech rate |
| `CLAUDE_VOICE_TTS_CACHE` | `tts.cache` | Cache toggle (0/1) |
| `CLAUDE_VOICE_RATE_LIMIT` | `tts.elevenlabs.rate_limit_per_hour` | Rate limit |

---

## 15. Error Handling

### Failure Cascade Table

Every failure has a defined recovery path. The system never raises exceptions to the caller. Every path terminates in either successful audio playback or graceful silence.

| Failure | Detection | Response | Fallback | User Impact |
|---------|-----------|----------|----------|-------------|
| No `ELEVENLABS_API_KEY` | `os.getenv()` returns None/empty | Route to Tier 1 | Kokoro → Piper → pyttsx3 | Local voice quality |
| ElevenLabs 401 (invalid key) | HTTP status code | Route to Tier 1, log warning | Kokoro → Piper → pyttsx3 | Local voice quality |
| ElevenLabs 429 (rate limited) | HTTP status code | Route to Tier 1, log warning | Kokoro → Piper → pyttsx3 | Temporary local quality |
| ElevenLabs 5xx (server error) | HTTP status code | Retry once after 1s, then Tier 1 | Kokoro → Piper → pyttsx3 | Brief delay, then local |
| ElevenLabs timeout (> 10s) | `httpx.TimeoutException` | Route to Tier 1 | Kokoro → Piper → pyttsx3 | Local voice quality |
| ElevenLabs quota exhausted | HTTP 402 or quota check | Route to Tier 1, log warning | Kokoro → Piper → pyttsx3 | Local voice quality |
| Network unreachable | `httpx.ConnectError` | Route to Tier 1 | Kokoro → Piper → pyttsx3 | Local voice quality |
| Kokoro not installed | `ImportError` | Fall to Piper | Piper → pyttsx3 | Lower quality local |
| Kokoro GPU OOM | `RuntimeError` with "out of memory" | Fall to Piper | Piper → pyttsx3 | CPU-based, slightly slower |
| Kokoro CUDA error | `RuntimeError` | Fall to Piper | Piper → pyttsx3 | CPU-based |
| Piper not installed | `FileNotFoundError` from `shutil.which()` | Fall to pyttsx3 | pyttsx3 | Robotic quality |
| Piper model missing | Non-zero exit code from piper CLI | Fall to pyttsx3 | pyttsx3 | Robotic quality |
| pyttsx3 fails | Any exception from pyttsx3 | TTS disabled | Silence | No spoken feedback |
| All backends fail | All attempts returned None | Return None, log warning | Silence | No spoken feedback |
| Cache write fails | `OSError` on file write | Continue without caching | Live TTS each time | Slightly higher latency on repeats |
| Cache disk full | `OSError` ENOSPC | Disable caching, log warning | Live TTS | No caching until space freed |
| ffmpeg not found (resample) | `FileNotFoundError` | Play unresampled audio | pw-play handles rate mismatch | PipeWire auto-resamples |
| pw-play fails | Non-zero exit from playback | Fall through playback chain | paplay → aplay → mpv | Different playback tool |
| All playback fails | All tools failed | Silence | None | No audio output |

### Error Logging

All TTS errors are logged to `~/.claude/local/voice/data/tts_errors.log` with timestamps. The log is rotated at 1MB. Errors are never printed to stdout (which would interfere with hook JSON output) or stderr (which would appear in Claude Code's UI).

```python
def _log_tts_error(backend: str, error: str, text_preview: str = "") -> None:
    """Append error to TTS error log. Never raises."""
    try:
        log_path = Path.home() / ".claude" / "local" / "voice" / "data" / "tts_errors.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        preview = text_preview[:80] if text_preview else ""
        entry = f"[{timestamp}] [{backend}] {error} | text: {preview}\n"
        with open(log_path, "a") as f:
            f.write(entry)
    except Exception:
        pass  # Logging failure must never propagate
```

---

## 16. Testing

### Backend Probe Script

`scripts/tts_test.py` — standalone test for all TTS backends:

```
Usage:
  uv run scripts/tts_test.py                    # Probe all backends, report status
  uv run scripts/tts_test.py --speak "Hello"    # Speak text through default backend
  uv run scripts/tts_test.py --voice "Rachel"   # Preview a specific voice
  uv run scripts/tts_test.py --backend kokoro   # Test a specific backend
  uv run scripts/tts_test.py --latency          # Measure latency per backend
  uv run scripts/tts_test.py --cache            # Verify cache hit/miss behavior
  uv run scripts/tts_test.py --concurrent       # Simulate 3 concurrent TTS calls
```

### Test Matrix

| Test | What It Verifies | Pass Criteria |
|------|-----------------|---------------|
| **Backend probe** | Each backend's availability detection | Correctly identifies installed/missing backends |
| **Tier routing** | 3-tier routing logic with various configs | Correct backend selected per config/env combination |
| **ElevenLabs API** | Cloud TTS generation (requires API key) | Audio file produced, correct format, < 2s total |
| **Kokoro generation** | Local GPU TTS generation | Audio file produced, < 1s for short text |
| **Piper generation** | Local CPU TTS generation | Audio file produced, < 500ms for short text |
| **pyttsx3 generation** | System espeak TTS | Audio file produced, < 200ms |
| **Fallback cascade** | Backend failure triggers next in chain | Simulated failures correctly cascade |
| **Cache write** | speak() with cache=True produces cache file | File exists at expected cache path |
| **Cache hit** | speak() same text twice | Second call < 10ms, returns same path |
| **Cache TTL** | Expired cache file is regenerated | File with old mtime is deleted and regenerated |
| **Cache LRU** | Exceeding cache_max_mb triggers eviction | Oldest files deleted, total under limit |
| **Dynamic text bypass** | Text with timestamps skips cache | No cache file created for timestamped text |
| **Voice catalog** | list_voices() returns voices per backend | Non-empty list with required fields |
| **Voice preview** | preview_voice() plays audio | Audio plays through speakers |
| **Emotion modifiers** | Different emotions produce different params | Parameter values match EMOTION_MODIFIERS table |
| **Theme voice resolution** | "default" voice resolves per active theme | Correct voice ID for starcraft vs. zelda |
| **Greeting template** | Template applies correctly | "{summary}" replaced, theme prefix present |
| **Subagent summary** | _generate_subagent_summary() produces < 20 words | Word count <= 20, meaningful content |
| **fcntl queue** | Concurrent speak_async() calls serialize | No overlapping audio, timestamps show sequential |
| **Concurrent subagents** | 3 SubagentStop TTS calls | All 3 play, none overlap, total < 30s |
| **Streaming** | Long text streams audio chunks | Playback starts before generation completes |
| **Rate limiting** | Exceeding rate limit triggers Tier 1 fallback | Cloud calls stop, local backend takes over |
| **Error resilience** | All backends disabled | speak() returns None, no exception, no crash |
| **Latency measurement** | Time from speak() to first audio | Kokoro < 500ms, Piper < 300ms, ElevenLabs < 500ms |
| **Output format** | Generated WAV matches target spec | 48kHz, 16-bit, stereo, little-endian |

### Latency Benchmarking

```python
def benchmark_backend(backend: str, text: str, iterations: int = 5) -> dict:
    """Measure TTS latency for a specific backend.

    Returns:
        {
            "backend": "kokoro",
            "text_length": 42,
            "iterations": 5,
            "latencies_ms": [234, 198, 201, 195, 203],
            "mean_ms": 206.2,
            "p50_ms": 201,
            "p95_ms": 234,
            "first_call_ms": 2341,  # Includes model load
        }
    """
```

The first call includes model loading overhead (significant for Kokoro). Subsequent calls show steady-state performance. Both metrics matter: first-call latency determines cold-start experience, steady-state determines sustained use experience.

---

## 17. Dependencies

### Required (stdlib)

These are always available — no installation needed:

- `hashlib`, `json`, `os`, `subprocess`, `threading`, `time`, `pathlib`, `shutil`, `weakref` — all Python stdlib

### Optional (per backend)

| Dependency | Backend | Install | Notes |
|------------|---------|---------|-------|
| `httpx` | ElevenLabs | `uv pip install httpx` | HTTP client with streaming support |
| `kokoro` | Kokoro-82M | `uv pip install kokoro` | Includes torch, CUDA deps |
| `piper-tts` | Piper | `uv pip install piper-tts` or AUR `piper-tts-bin` | Includes ONNX runtime |
| `pyttsx3` | pyttsx3 | `uv pip install pyttsx3` | Wraps espeak-ng |
| `soundfile` | Kokoro | `uv pip install soundfile` | WAV I/O for Kokoro output |
| `ffmpeg` | Resampling | System package (`pacman -S ffmpeg`) | Already installed on this machine |

### PEP 723 Inline Dependencies

Since hooks use `uv run` with PEP 723 script blocks, the TTS engine's dependencies are declared inline:

```python
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "httpx>=0.27",
# ]
# ///
```

Kokoro, Piper, and pyttsx3 are optional imports — their absence triggers fallback, not failure. Only `httpx` is declared as a hard dependency (for ElevenLabs), and even that is only used when the ElevenLabs backend is selected.

---

## 18. Cross-Reference

| Spec | Relationship | What This Spec Uses |
|------|-------------|---------------------|
| `01-plugin-scaffold.md` | Directory structure, config schema, data paths | `~/.claude/local/voice/config.yaml`, `~/.claude/local/voice/cache/tts/`, `~/.claude/local/voice/data/` |
| `02-theme-engine.md` | Theme voice assignments, greeting templates, personality modifiers | `theme.json` `tts` section: `voice_id`, `greeting_template`, `personality_modifiers` |
| `03-sound-synthesis.md` | Sound effect generation (WAV assets) | Future: ElevenLabs Sound Effects API as alternative synthesis source |
| `04-hook-architecture.md` | Hook events that trigger TTS, SubagentStop handler | Event classification, emotion mapping, non-blocking pattern |
| `05-audio-playback.md` | Final playback step, fcntl queue, volume, concurrency | `lib/audio.play_sound(path, category="tts", mode="queue")`, fcntl lock protocol |

### Data Flow Summary

```
Hook event (SubagentStop, Stop, Notification, SessionStart)
    → Event Router (spec 04) classifies event, determines emotion
    → TTS Engine (this spec) receives text + emotion
        → Cache check (Section 9)
        → Backend routing (Section 3)
        → Audio generation (Sections 5-7)
        → Resample to 48kHz WAV (Section 4)
        → Cache store (Section 9)
    → Audio Playback (spec 05) receives WAV path
        → fcntl lock acquisition (queue mode)
        → pw-play subprocess (fire-and-forget)
        → Lock release after playback
```
