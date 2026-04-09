---
title: "ElevenLabs Deep Dive — Full API Surface, Models, Voices & Sound Effects"
spec: "13"
status: draft
created: 2026-03-26
author: matt
tags: [claude-voice, elevenlabs, tts, sound-effects, voice-cloning, api]
---

# 13 — ElevenLabs Deep Dive

## 1. Overview

ElevenLabs is claude-voice's cloud TTS provider — Tier 2 (Fast/Cheap) and Tier 3 (Frontier/Quality) in the 3-tier routing architecture defined in spec 06. This spec documents the complete API surface relevant to our use case: text-to-speech, sound effects generation, voice design, voice cloning, and voice library management.

The goal is to maximize what we get from the ElevenLabs subscription. claude-voice's earcons are generated locally via numpy synthesis (spec 03) — free, offline, zero latency. ElevenLabs fills a different niche: high-quality neural speech for greetings, narration, summaries, and optionally, dynamically generated sound effects that exceed what numpy can produce. The cloud backend is always optional; every ElevenLabs call has a local fallback path (Kokoro → Piper → pyttsx3 → silence), and total silence is an acceptable outcome.

This spec is a reference document. It catalogs what ElevenLabs offers so that implementation decisions in spec 06 (TTS engine) and spec 02 (theme engine) are grounded in the actual API surface rather than assumptions.

---

## 2. API Authentication

### 2.1 API Key Storage

The API key is loaded from two locations, checked in order:

1. **Environment variable**: `ELEVENLABS_API_KEY` — takes precedence if set
2. **Secrets file**: `~/.claude/local/secrets/elevenlabs-api.env` — sourced if env var is absent

The secrets file format:

```bash
# ~/.claude/local/secrets/elevenlabs-api.env
ELEVENLABS_API_KEY=sk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Loading logic in `lib/tts.py`:

```python
def _load_api_key() -> Optional[str]:
    """Load ElevenLabs API key from env or secrets file."""
    key = os.environ.get("ELEVENLABS_API_KEY")
    if key:
        return key
    secrets_path = Path.home() / ".claude" / "local" / "secrets" / "elevenlabs-api.env"
    if secrets_path.exists():
        for line in secrets_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "ELEVENLABS_API_KEY":
                return v.strip()
    return None
```

### 2.2 Base URL

```
https://api.elevenlabs.io
```

All endpoints are versioned under `/v1/`.

### 2.3 Required Headers

Every request includes:

| Header | Value | Purpose |
|--------|-------|---------|
| `xi-api-key` | `{ELEVENLABS_API_KEY}` | Authentication |
| `Content-Type` | `application/json` | Request body format |
| `Accept` | Varies by endpoint | Response format (audio MIME type or JSON) |

### 2.4 Rate Limits by Plan Tier

Rate limits are expressed as **concurrent requests** (simultaneous parallel connections), not requests-per-second:

| Plan | Monthly Credits | Concurrent Requests | Voice Cloning | Voice Library Access |
|------|----------------|---------------------|---------------|---------------------|
| Free | 10,000 chars | 2 | Instant only | No |
| Starter | 30,000 chars | 3 | Instant only | Yes |
| Creator | 100,000 chars | 5 | Instant + Professional | Yes |
| Pro | 500,000 chars | 10 | Instant + Professional | Yes |
| Scale | 2,000,000 chars | 15 | Instant + Professional | Yes |
| Business | Custom | 15+ | All | Yes |

When concurrent limit is exceeded, the API returns HTTP 429 with body `{"detail": {"status": "too_many_concurrent_requests"}}`. claude-voice handles this by falling to local TTS (see section 10).

### 2.5 Python SDK

```bash
uv pip install elevenlabs
```

The official SDK (`elevenlabs` on PyPI) wraps all REST endpoints. Current version as of March 2026: `2.39.x`. The SDK provides typed request/response models and handles streaming automatically. We use `httpx` directly for maximum control over timeouts and streaming, but the SDK is documented here as the canonical reference.

---

## 3. Text-to-Speech Models

### 3.1 Complete Model Comparison

| Model | Model ID | TTFB | Full Gen (20 words) | Languages | Max Chars | Cost | Best For |
|-------|----------|------|---------------------|-----------|-----------|------|----------|
| **Flash v2.5** | `eleven_flash_v2_5` | ~75ms | ~200ms | 32 | 40,000 | 0.5 credits/char | Real-time, low latency, interactive |
| **Turbo v2.5** | `eleven_turbo_v2_5` | ~250ms | ~400ms | 32 | 40,000 | 0.5 credits/char | Legacy — Flash supersedes this |
| **Multilingual v2** | `eleven_multilingual_v2` | ~400ms | ~600ms | 29 | 10,000 | 1.0 credits/char | Long-form, audiobook, stable |
| **Eleven v3** | `eleven_v3` | ~300ms | ~500ms | 74 | 5,000 | 1.0 credits/char | Expressive, emotional, contextual |
| **English v1** | `eleven_monolingual_v1` | ~300ms | ~400ms | 1 (English) | 10,000 | 0.5 credits/char | Legacy English-only |

### 3.2 Per-Model Detail

#### Flash v2.5 (`eleven_flash_v2_5`)

ElevenLabs' recommended model for all latency-sensitive use cases. Replaced Turbo v2.5 as the default "fast" model. Same quality tier as Turbo, lower average latency.

- **TTFB**: ~75ms (measured, not theoretical — this is from request sent to first audio byte received)
- **Full generation for 20 words (~100 chars)**: ~200ms total
- **Languages**: 32 — English, Spanish, French, German, Italian, Portuguese, Polish, Hindi, Arabic, Japanese, Korean, Mandarin, Dutch, Turkish, Swedish, Indonesian, Filipino, Malay, Romanian, Ukrainian, Greek, Czech, Danish, Finnish, Bulgarian, Croatian, Slovak, Tamil, and more
- **Character limit**: 40,000 per request (generous — claude-voice never approaches this)
- **Streaming**: Full support (chunked HTTP and WebSocket)
- **Output formats**: All (MP3, PCM, Opus, μ-law, A-law)
- **Voice settings sensitivity**: Responds well to stability and similarity_boost. Style parameter adds latency with diminishing returns — keep at 0.0 for speed.
- **claude-voice role**: Default cloud backend. Tier 2 (Fast/Cheap). Used for greetings, status narration, subagent summaries.

#### Turbo v2.5 (`eleven_turbo_v2_5`)

Functionally equivalent to Flash v2.5 but with higher average latency. ElevenLabs documentation explicitly recommends Flash over Turbo in all cases. Kept here for completeness.

- **claude-voice role**: Not used. Flash v2.5 is strictly better.

#### Multilingual v2 (`eleven_multilingual_v2`)

The workhorse for long-form, high-stability generation. Most predictable output across long text passages. Does not exhibit the drift or inconsistency that Flash sometimes shows on multi-paragraph text.

- **TTFB**: ~400ms
- **Languages**: 29
- **Character limit**: 10,000 per request
- **Cost**: 2x Flash (1.0 credits/char vs 0.5)
- **Strengths**: Stability on long-form. Consistent prosody across paragraphs. Best for audiobook-style narration.
- **Weaknesses**: Higher latency. Fewer languages than v3.
- **claude-voice role**: Available via `tts.quality: best` config. Tier 3. Rarely triggered in typical hook usage since our text is short.

#### Eleven v3 (`eleven_v3`)

ElevenLabs' newest model (2025). Most expressive, broadest language support, deepest emotional range. Understands conversational context — adjusts prosody based on what the text says, not just how it's marked up.

- **TTFB**: ~300ms
- **Languages**: 74 (broadest of any model)
- **Character limit**: 5,000 per request (lowest limit — designed for focused, expressive generation, not bulk)
- **Cost**: 1.0 credits/char
- **Strengths**: Emotional range. Contextual prosody. Best for character voices, dramatic narration, dialogue.
- **Weaknesses**: Not designed for real-time/interactive use. Higher latency than Flash. Lowest character limit.
- **claude-voice role**: Premium option for themes that demand expressiveness (e.g., a cinematic theme). Not default.

#### English v1 (`eleven_monolingual_v1`)

Legacy model. English only. Lower quality than all v2/v3 models. No reason to use unless debugging compatibility issues.

- **claude-voice role**: Not used.

### 3.3 Model Selection Logic for claude-voice

```python
MODEL_SELECTION = {
    "speed":   "eleven_flash_v2_5",    # Default. 75ms TTFB. Cheapest.
    "quality": "eleven_multilingual_v2", # Stable long-form. 2x cost.
    "expressive": "eleven_v3",          # Emotional, contextual. 2x cost.
}

def select_model(quality_pref: str, text_length: int) -> str:
    """Select ElevenLabs model based on preference and text length."""
    if quality_pref == "best":
        if text_length > 5000:
            return MODEL_SELECTION["quality"]  # v3 can't handle >5000 chars
        return MODEL_SELECTION["expressive"]
    return MODEL_SELECTION["speed"]
```

---

## 4. TTS API Endpoints

### 4a. Standard TTS — `POST /v1/text-to-speech/{voice_id}`

The primary endpoint. Generates audio for the full text and returns it as a single response.

**Request**:

```http
POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}
Headers:
  xi-api-key: {API_KEY}
  Content-Type: application/json
  Accept: audio/pcm

Body:
{
  "text": "Commander, authentication module is ready.",
  "model_id": "eleven_flash_v2_5",
  "voice_settings": {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,
    "use_speaker_boost": true
  },
  "output_format": "pcm_24000"
}
```

**Response**: Raw audio bytes in the requested format. Content-Type header indicates the format (`audio/pcm`, `audio/mpeg`, etc.).

**For claude-voice**: Request `pcm_24000` (or `pcm_44100` on Pro+ plans), resample to 48kHz stereo WAV via ffmpeg, play through `pw-play`. The standard endpoint is preferred over streaming for short text (< 200 chars) because:
- Fewer round trips
- Simpler error handling (single HTTP response)
- Flash v2.5 returns full audio in ~200ms for 20 words — fast enough for our use case

**Implementation**:

```python
import httpx
import struct
from pathlib import Path

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io/v1"
ELEVENLABS_TTS_TIMEOUT_SECONDS = 10

def _elevenlabs_tts(
    text: str,
    voice_id: str,
    model_id: str = "eleven_flash_v2_5",
    output_format: str = "pcm_24000",
    voice_settings: Optional[dict] = None,
) -> Optional[bytes]:
    """Call ElevenLabs TTS API, return raw audio bytes or None on failure."""
    api_key = _load_api_key()
    if not api_key:
        return None

    url = f"{ELEVENLABS_BASE_URL}/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    body = {
        "text": text,
        "model_id": model_id,
        "output_format": output_format,
    }
    if voice_settings:
        body["voice_settings"] = voice_settings

    try:
        response = httpx.post(
            url,
            headers=headers,
            json=body,
            timeout=ELEVENLABS_TTS_TIMEOUT_SECONDS,
        )
        if response.status_code == 200:
            return response.content
        # Log non-200 status for debugging
        _log_api_error(response.status_code, response.text)
        return None
    except httpx.TimeoutException:
        return None
    except httpx.RequestError:
        return None
```

### 4b. Streaming TTS — `POST /v1/text-to-speech/{voice_id}/stream`

Returns audio via chunked transfer encoding. The client receives audio chunks as they are generated, enabling playback to start before the full audio is ready.

**Request**: Identical body to standard TTS.

**Response**: Chunked HTTP response. Each chunk is a segment of audio bytes in the requested format.

**When to use**: Text longer than ~200 characters (roughly 40+ words). The latency advantage of streaming grows with text length — for short text, the overhead of managing chunks is not worth it.

**Implementation pattern**:

```python
def _elevenlabs_tts_stream(
    text: str,
    voice_id: str,
    output_path: Path,
    model_id: str = "eleven_flash_v2_5",
    output_format: str = "pcm_24000",
    voice_settings: Optional[dict] = None,
) -> Optional[Path]:
    """Stream ElevenLabs TTS to a file. Returns path on success."""
    api_key = _load_api_key()
    if not api_key:
        return None

    url = f"{ELEVENLABS_BASE_URL}/text-to-speech/{voice_id}/stream"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    body = {
        "text": text,
        "model_id": model_id,
        "output_format": output_format,
    }
    if voice_settings:
        body["voice_settings"] = voice_settings

    try:
        with httpx.stream(
            "POST", url,
            headers=headers,
            json=body,
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0),
        ) as response:
            if response.status_code != 200:
                _log_api_error(response.status_code, "streaming")
                return None
            with open(output_path, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=4096):
                    f.write(chunk)
        return output_path
    except (httpx.TimeoutException, httpx.RequestError):
        # Clean up partial file
        output_path.unlink(missing_ok=True)
        return None
```

### 4c. WebSocket Streaming — `wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input`

Bidirectional WebSocket for real-time, token-by-token text input with audio output. The client sends text chunks as they become available (e.g., from an LLM streaming response), and the server returns audio chunks as they are generated.

**Connection URL**:

```
wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?model_id={model_id}&output_format={format}
```

**Protocol**:

1. Client opens WebSocket connection
2. Client sends initial config message:
   ```json
   {
     "text": " ",
     "voice_settings": {
       "stability": 0.5,
       "similarity_boost": 0.75
     },
     "xi_api_key": "{API_KEY}",
     "generation_config": {
       "chunk_length_schedule": [120, 160, 250, 290]
     }
   }
   ```
3. Client sends text chunks as they arrive:
   ```json
   {"text": "Commander, "}
   {"text": "the module "}
   {"text": "is ready."}
   ```
4. Client sends end-of-input signal:
   ```json
   {"text": ""}
   ```
5. Server sends audio chunks as base64-encoded strings:
   ```json
   {
     "audio": "base64_encoded_audio_bytes...",
     "isFinal": false,
     "alignment": {...}
   }
   ```

**`chunk_length_schedule`**: Controls how aggressively the server buffers text before generating audio. Lower values = lower latency but potentially choppier output. The schedule defines character thresholds for successive chunks — the server generates audio after accumulating that many characters. Default `[120, 160, 250, 290]` means: first chunk at 120 chars, second at 160, etc.

**claude-voice role**: Not used in initial implementation. The WebSocket API is designed for conversational AI where text is generated token-by-token. claude-voice receives complete text from hook events, so the standard and streaming HTTP endpoints are sufficient. Future use case: if claude-voice ever needs to narrate LLM output in real-time as it streams, WebSocket becomes relevant.

---

## 5. Voice Settings — Parameter Deep Dive

### 5.1 Parameter Reference

| Parameter | Type | Range | Default | Effect |
|-----------|------|-------|---------|--------|
| `stability` | float | 0.0 – 1.0 | 0.5 | Controls voice consistency across generations. Higher = more predictable, lower = more expressive with wider pitch variation. At 0.0, the voice is highly dramatic and variable. At 1.0, it is flat and monotone. |
| `similarity_boost` | float | 0.0 – 1.0 | 0.75 | How closely the output matches the original voice sample. Higher = more faithful to the voice, lower = more generic. Very high values (>0.9) can introduce artifacts. |
| `style` | float | 0.0 – 1.0 | 0.0 | Amplifies the voice's natural expressiveness. **Increases latency** — ElevenLabs runs an additional style transfer pass. At 0.0, no style enhancement (fastest). At 1.0, maximum expressiveness (slowest). Only supported on Multilingual v2 and v3. |
| `use_speaker_boost` | bool | true/false | true | Post-processing that enhances speaker clarity and reduces background artifacts. Slight latency increase. Recommended always-on. |

### 5.2 Parameter Interaction Matrix

| stability | similarity_boost | Result |
|-----------|-----------------|--------|
| High (0.7+) | High (0.8+) | Consistent, faithful, slightly robotic. Good for military/technical narration. |
| High (0.7+) | Low (0.3) | Consistent but generic. Not useful. |
| Mid (0.4-0.6) | Mid (0.6-0.8) | Natural, balanced. The sweet spot for most use cases. |
| Low (0.2-0.3) | High (0.8+) | Expressive and faithful to the voice. Best for character voices. |
| Low (0.2-0.3) | Low (0.3) | Chaotic, inconsistent. Not useful. |

### 5.3 Theme-Specific Voice Settings

Each theme in claude-voice maps to a specific voice personality. The voice settings should reflect the theme's sonic DNA:

| Theme | stability | similarity_boost | style | use_speaker_boost | Rationale |
|-------|-----------|-----------------|-------|-------------------|-----------|
| **default** | 0.50 | 0.75 | 0.0 | true | Neutral, balanced, no latency penalty from style |
| **starcraft** | 0.70 | 0.80 | 0.0 | true | Military precision — consistent, authoritative, no variability |
| **mario** | 0.30 | 0.70 | 0.3 | true | Playful, expressive, bouncy. Style adds character. |
| **zelda** | 0.45 | 0.75 | 0.2 | true | Mystical, slightly ethereal. Moderate expression. |
| **warcraft** | 0.55 | 0.80 | 0.2 | true | Commanding but with warmth. Medieval gravitas. |
| **metroid** | 0.65 | 0.75 | 0.0 | true | Cold, precise, AI-like. Minimal variation. |
| **halo** | 0.60 | 0.80 | 0.1 | true | Professional military, slight warmth (Cortana-esque). |
| **pokemon** | 0.35 | 0.70 | 0.3 | true | Cheerful, energetic, high expression. |

### 5.4 Emotion Modifier Overlays

The `speak()` function accepts an `emotion` parameter that applies delta adjustments on top of the theme's base settings:

| Emotion | stability Δ | similarity_boost Δ | style Δ | Use Case |
|---------|------------|--------------------|---------| ---------|
| `neutral` | +0.00 | +0.00 | +0.0 | Default, no adjustment |
| `success` | -0.05 | +0.00 | +0.1 | Task completion, commits — slightly more expressive |
| `error` | +0.10 | +0.05 | +0.0 | Error narration — more consistent, urgent |
| `alert` | +0.05 | +0.05 | +0.0 | Warnings — slightly tighter |
| `calm` | +0.10 | +0.00 | -0.1 | Session end — steadier, quieter |
| `excited` | -0.10 | +0.00 | +0.2 | Achievement, milestone — more expressive |

Applied as:

```python
def _apply_emotion_overlay(base_settings: dict, emotion: str) -> dict:
    """Apply emotion delta to base voice settings. Clamp all values to [0.0, 1.0]."""
    deltas = EMOTION_MODIFIERS.get(emotion, EMOTION_MODIFIERS["neutral"])
    return {
        "stability": max(0.0, min(1.0, base_settings["stability"] + deltas["stability"])),
        "similarity_boost": max(0.0, min(1.0, base_settings["similarity_boost"] + deltas["similarity_boost"])),
        "style": max(0.0, min(1.0, base_settings.get("style", 0.0) + deltas["style"])),
        "use_speaker_boost": base_settings.get("use_speaker_boost", True),
    }
```

---

## 6. Sound Effects API V2

### 6.1 Overview

ElevenLabs' Sound Effects API generates audio from text descriptions. Version 2 (September 2025) brought significant improvements: 30-second generation (up from 22s), seamless looping, 48kHz professional output, and improved prompt adherence.

This is a potential alternative to claude-voice's numpy-based earcon synthesis (spec 03). The trade-off is clear: ElevenLabs SFX produces dramatically higher quality audio, but costs money and requires network access. Local numpy synthesis is free, offline, instant, and deterministic.

### 6.2 Endpoint

```http
POST https://api.elevenlabs.io/v1/sound-generation

Headers:
  xi-api-key: {API_KEY}
  Content-Type: application/json

Body:
{
  "text": "8-bit coin collect sound effect, bright and cheerful",
  "model_id": "eleven_text_to_sound_v2",
  "duration_seconds": 1.5,
  "prompt_influence": 0.5,
  "output_format": "mp3_44100_128"
}
```

### 6.3 Request Parameters

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `text` | string | 1-500 chars | Required | Natural language description of the desired sound |
| `model_id` | string | — | `eleven_text_to_sound_v2` | Only v2 model available |
| `duration_seconds` | float | 0.5 – 30.0 | Auto | Desired duration. If omitted, model chooses based on prompt. |
| `prompt_influence` | float | 0.0 – 1.0 | 0.3 | How strictly to follow the text prompt vs. creative latitude |
| `output_format` | string | — | `mp3_44100_128` | Same format options as TTS (see section 8) |

### 6.4 Looping Support

The v2 model supports seamless looping — the generated audio's end blends smoothly into its beginning. This is only available with `eleven_text_to_sound_v2`. Useful for ambient backgrounds.

To request a looping sound, include the word "loop" or "looping" in the prompt, or the API detects ambient/atmospheric prompts automatically.

### 6.5 claude-voice Sound Effect Prompts

Example prompts for generating theme-appropriate sounds:

#### StarCraft Theme

| Semantic Token | Prompt | Duration | Notes |
|----------------|--------|----------|-------|
| `session_start` | "sci-fi computer boot sequence, servo motors engaging, digital HUD powering up, metallic, military" | 1.5s | Terran command center boot |
| `task_complete` | "sci-fi tactical computer confirmation beep, crisp military acknowledgment tone, short" | 0.5s | Mission objective complete |
| `error` | "sci-fi alarm klaxon, short urgent warning, metallic reverb, digital" | 0.8s | System alert |
| `commit` | "sci-fi data transmission complete, digital relay confirmed, radio chirp" | 0.6s | Data committed to command |
| `session_end` | "sci-fi computer shutdown sequence, systems powering down, servo motors disengaging" | 1.2s | Terran command center shutdown |

#### Mario Theme

| Semantic Token | Prompt | Duration | Notes |
|----------------|--------|----------|-------|
| `session_start` | "8-bit video game level start jingle, cheerful, bright, retro Nintendo style" | 1.0s | World entry |
| `task_complete` | "8-bit coin collect sound effect, bright and cheerful, classic video game" | 0.3s | Coin collect |
| `error` | "8-bit video game damage sound, descending tone, retro, short" | 0.4s | Hit/damage |
| `commit` | "8-bit video game power-up collected, ascending triumphant tones, retro" | 0.5s | Power-up |
| `session_end` | "8-bit video game level complete, short fanfare, cheerful, retro Nintendo" | 1.2s | Level clear |

#### Zelda Theme

| Semantic Token | Prompt | Duration | Notes |
|----------------|--------|----------|-------|
| `session_start` | "orchestral fantasy adventure game menu opening, harp and strings, mystical, ethereal" | 1.5s | Title screen |
| `task_complete` | "fantasy game puzzle solved jingle, ascending harp notes, magical sparkle" | 0.5s | Puzzle complete |
| `error` | "fantasy game alert, low horn warning, ominous, short" | 0.6s | Danger |
| `commit` | "orchestral treasure chest opening fanfare, triumphant brass, magical sparkle" | 1.0s | Treasure acquired |
| `session_end` | "fantasy game save point melody, gentle harp, serene, peaceful" | 1.2s | Save and rest |

#### Warcraft Theme

| Semantic Token | Prompt | Duration | Notes |
|----------------|--------|----------|-------|
| `session_start` | "medieval castle horn fanfare, deep brass, stone hall reverb, commanding" | 1.5s | Alliance horn |
| `task_complete` | "medieval anvil strike with reverb, blacksmith completion, metallic ring" | 0.5s | Craft complete |
| `error` | "medieval war drum alert, deep urgent beat, ominous" | 0.6s | Warning drums |
| `commit` | "medieval scroll sealing sound, wax stamp, parchment rustle, ceremonial" | 0.8s | Decree sealed |
| `session_end` | "medieval tavern door closing, creaking wood, muffled warmth fading" | 1.0s | Rest at the inn |

### 6.6 Cost Structure

Sound effects are billed **per generation**, not per character. Each generation costs credits based on the output duration:

- Short effects (0.5–2s): ~10-20 credits per generation
- Medium effects (2–10s): ~20-50 credits per generation
- Long effects (10–30s): ~50-100 credits per generation

(Exact credit costs vary by plan tier and are subject to change. Check the billing dashboard for current rates.)

### 6.7 Trade-off Analysis: Cloud SFX vs. Local Numpy Synthesis

| Dimension | ElevenLabs SFX | Local Numpy (spec 03) |
|-----------|---------------|----------------------|
| **Quality** | Professional, 48kHz, realistic timbres | Good, 48kHz, synthesized timbres |
| **Cost** | Credits per generation | Free |
| **Latency** | 500ms–3s (network + generation) | < 50ms |
| **Offline** | No | Yes |
| **Determinism** | Non-deterministic (different each time) | Deterministic (same params = same output) |
| **Variety** | Infinite (describe anything) | Limited to synthesis parameters |
| **Cacheability** | Must pre-generate and cache | Generated on the fly, or cached |
| **Theme fidelity** | Excellent prompt adherence to theme descriptions | Good but constrained by synthesis math |

**Recommendation**: Use ElevenLabs SFX for **one-time theme asset generation** — generate the variant pools at theme install time, cache them as WAV files, and serve them locally thereafter. This gives us the quality of cloud generation with the speed and reliability of local playback. The numpy synthesizer remains the **runtime fallback** when cached cloud assets are unavailable.

---

## 7. Voice Library Management

### 7.1 Pre-Made Voices

ElevenLabs hosts 10,000+ community and professionally designed voices. These are browsable and searchable via the API.

#### List All Voices

```http
GET https://api.elevenlabs.io/v1/voices

Headers:
  xi-api-key: {API_KEY}
```

**Response** (abridged):

```json
{
  "voices": [
    {
      "voice_id": "pNInz6obpgDQGcFmaJgB",
      "name": "Adam",
      "category": "premade",
      "labels": {
        "accent": "american",
        "description": "deep",
        "age": "middle aged",
        "gender": "male",
        "use_case": "narration"
      },
      "preview_url": "https://storage.googleapis.com/.../adam_preview.mp3",
      "available_for_tiers": ["free", "starter", "creator", "pro", "scale"],
      "settings": {
        "stability": 0.5,
        "similarity_boost": 0.75
      }
    }
  ]
}
```

#### Search Voice Library

```http
GET https://api.elevenlabs.io/v1/shared-voices?page_size=20&gender=male&age=middle_aged&accent=american&use_case=narration

Headers:
  xi-api-key: {API_KEY}
```

Filter parameters:
- `gender`: male, female, neutral
- `age`: young, middle_aged, old
- `accent`: american, british, australian, indian, african, etc.
- `language`: ISO language code
- `use_case`: narration, conversational, characters, news
- `search`: free-text search across voice names and descriptions
- `page_size`: results per page (default 30, max 100)
- `page`: page number for pagination

#### Get Voice Preview

```http
GET https://api.elevenlabs.io/v1/voices/{voice_id}

Headers:
  xi-api-key: {API_KEY}
```

Returns full voice metadata including `preview_url` — a direct link to a short audio sample.

### 7.2 Voice Design — Create from Text Description

Generate entirely new voices by describing desired characteristics. No audio samples needed.

```http
POST https://api.elevenlabs.io/v1/voice-generation/generate-voice

Headers:
  xi-api-key: {API_KEY}
  Content-Type: application/json

Body:
{
  "gender": "male",
  "age": "middle_aged",
  "accent": "american",
  "accent_strength": 1.5,
  "text": "I am a gruff military commander with decades of experience in tactical operations. My voice is authoritative and commanding."
}
```

**Response**: Returns generated `voice_id` and preview audio. The voice is saved to your voice library and can be used in subsequent TTS requests.

**Parameters**:

| Parameter | Type | Description |
|-----------|------|-------------|
| `gender` | string | "male", "female", "neutral" |
| `age` | string | "young", "middle_aged", "old" |
| `accent` | string | Target accent ("american", "british", "australian", etc.) |
| `accent_strength` | float | 0.3 – 2.0. How strong the accent is. |
| `text` | string | Description of the voice AND the sample text to generate. |

**claude-voice use case**: Generate custom theme voices that exactly match the sonic DNA:
- StarCraft: "gruff military commander, authoritative, crisp radio quality"
- Zelda: "gentle mystical sage, elderly, wise, warm"
- Mario: "cheerful young guide, bright, energetic, slightly cartoonish"
- Warcraft: "deep-voiced medieval herald, commanding, stone hall resonance"

### 7.3 Instant Voice Cloning (IVC)

Create a voice clone from one or more audio samples. Near-instant processing.

```http
POST https://api.elevenlabs.io/v1/voices/add

Headers:
  xi-api-key: {API_KEY}
  Content-Type: multipart/form-data

Body (multipart):
  name: "Shawn Personal"
  description: "Cloned from personal recordings"
  labels: {"use_case": "personal_narration", "accent": "canadian"}
  files: [audio_sample_1.mp3, audio_sample_2.mp3]
```

**Requirements**:
- **Minimum**: 1 audio sample
- **Recommended**: 3-5 samples, each 30s–2min, totaling 3-10 minutes
- **Formats**: MP3, WAV, M4A, FLAC, OGG
- **Quality tips**: Clean recording, minimal background noise, consistent microphone distance, varied intonation (read diverse text, not monotone)

**Response**:

```json
{
  "voice_id": "new_cloned_voice_id"
}
```

The cloned voice is immediately available for TTS requests.

**claude-voice use case**: Clone Shawn's voice from existing recordings (OBS recordings in `~/`, meeting recordings via claude-recordings). Use the cloned voice for personal narration — session summaries, reminders, and alerts in Shawn's own voice.

### 7.4 Professional Voice Cloning (PVC)

Higher-quality cloning that requires more audio input and takes longer to process.

- **Minimum**: 30 minutes of clean audio
- **Processing time**: Hours (not instant)
- **Availability**: Creator plan and above
- **Quality**: Significantly better than IVC, especially for emotional range and consistency

Not prioritized for claude-voice initial implementation. IVC is sufficient for personal narration. PVC can be explored later if IVC quality is inadequate.

### 7.5 Voice Management Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /v1/voices` | GET | List all voices in your library |
| `GET /v1/voices/{voice_id}` | GET | Get voice details and metadata |
| `DELETE /v1/voices/{voice_id}` | DELETE | Remove a voice from your library |
| `POST /v1/voices/{voice_id}/edit` | POST | Edit voice name, description, labels |
| `GET /v1/voices/{voice_id}/settings` | GET | Get voice's default settings |
| `POST /v1/voices/{voice_id}/settings/edit` | POST | Update voice's default settings |

---

## 8. Output Formats

### 8.1 Complete Format Reference

Format IDs follow the pattern: `{codec}_{sample_rate}` or `{codec}_{sample_rate}_{bitrate}`.

| Format ID | Codec | Sample Rate | Bitrate | Quality | File Size (10s) | Min Plan |
|-----------|-------|-------------|---------|---------|-----------------|----------|
| `mp3_22050_32` | MP3 | 22.05kHz | 32kbps | Low | ~40KB | Free |
| `mp3_44100_32` | MP3 | 44.1kHz | 32kbps | Low | ~40KB | Free |
| `mp3_44100_64` | MP3 | 44.1kHz | 64kbps | Medium | ~80KB | Free |
| `mp3_44100_96` | MP3 | 44.1kHz | 96kbps | Good | ~120KB | Free |
| `mp3_44100_128` | MP3 | 44.1kHz | 128kbps | Good | ~160KB | Free |
| `mp3_44100_192` | MP3 | 44.1kHz | 192kbps | High | ~240KB | Creator |
| `pcm_16000` | PCM (s16le) | 16kHz | Raw | Medium | ~320KB | Free |
| `pcm_22050` | PCM (s16le) | 22.05kHz | Raw | Medium | ~441KB | Free |
| `pcm_24000` | PCM (s16le) | 24kHz | Raw | Good | ~480KB | Free |
| `pcm_44100` | PCM (s16le) | 44.1kHz | Raw | Best | ~882KB | Pro |
| `pcm_48000` | PCM (s16le) | 48kHz | Raw | Best | ~960KB | Pro |
| `ulaw_8000` | μ-law | 8kHz | — | Phone | ~80KB | Free |
| `alaw_8000` | A-law | 8kHz | — | Phone | ~80KB | Free |
| `opus_48000_32` | Opus | 48kHz | 32kbps | Good | ~40KB | Free |
| `opus_48000_64` | Opus | 48kHz | 64kbps | Good | ~80KB | Free |
| `opus_48000_96` | Opus | 48kHz | 96kbps | High | ~120KB | Free |
| `opus_48000_128` | Opus | 48kHz | 128kbps | High | ~160KB | Free |
| `opus_48000_192` | Opus | 48kHz | 192kbps | Best | ~240KB | Creator |

### 8.2 Recommended Format for claude-voice

**Primary**: `pcm_24000` — raw 24kHz 16-bit signed little-endian PCM.

Rationale:
- Available on all plan tiers (Free and above)
- Raw PCM has zero decoding overhead
- 24kHz is high enough quality for speech intelligibility and naturalness
- Resample to 48kHz (PipeWire native) via ffmpeg — exact 2x ratio means clean integer resampling
- No lossy compression artifacts

**Alternative (Pro+ plans)**: `pcm_44100` or `pcm_48000` — if available on the plan, requesting 48kHz directly eliminates the resampling step entirely.

**For caching**: `mp3_44100_128` — good quality, 5x smaller than PCM. Cache stored audio as MP3, decode to PCM at playback time. Storage savings matter when CACHE_MAX_MB is 500.

### 8.3 PCM-to-WAV Conversion

ElevenLabs PCM output is headerless raw audio. To play via `pw-play`, wrap in a WAV header:

```python
import struct
from pathlib import Path

WAV_HEADER_SIZE = 44

def _pcm_to_wav(
    pcm_data: bytes,
    output_path: Path,
    sample_rate: int = 24000,
    channels: int = 1,
    bit_depth: int = 16,
) -> Path:
    """Wrap raw PCM bytes in a WAV header."""
    byte_rate = sample_rate * channels * (bit_depth // 8)
    block_align = channels * (bit_depth // 8)
    data_size = len(pcm_data)
    file_size = data_size + WAV_HEADER_SIZE - 8

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", file_size, b"WAVE",
        b"fmt ", 16,               # fmt chunk size
        1,                         # PCM format
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bit_depth,
        b"data", data_size,
    )

    output_path.write_bytes(header + pcm_data)
    return output_path
```

After wrapping, resample to 48kHz stereo via `_resample_to_target()` (spec 06) before playback.

---

## 9. Cost Management

### 9.1 Current Pricing (March 2026)

| Plan | Monthly Price | Characters Included | Overage Rate | Annual Discount |
|------|--------------|---------------------|--------------|-----------------|
| Free | $0 | 10,000 | N/A (hard cap) | — |
| Starter | $5/mo | 30,000 | ~$0.30/1K chars | ~20% |
| Creator | $22/mo | 100,000 | ~$0.24/1K chars | ~20% |
| Pro | $99/mo | 500,000 | ~$0.24/1K chars | ~20% |
| Scale | $330/mo | 2,000,000 | ~$0.18/1K chars | ~20% |

Note: Flash models cost 0.5 credits per character. Multilingual v2 and v3 cost 1.0 credits per character. When using Flash, your effective character budget is 2x the credit count.

### 9.2 Character Counting

ElevenLabs counts characters as follows:
- Every character in the `text` field counts, including spaces and punctuation
- SSML tags (if used) do NOT count — only the text content inside tags
- Empty strings cost 0 characters
- Minimum billable unit: 1 character

### 9.3 Cost Per Typical claude-voice Use Case

| Use Case | Typical Text | Chars | Model | Credits Used | Cost at Pro ($99/mo) |
|----------|-------------|-------|-------|-------------|---------------------|
| Session greeting | "Commander, authentication module is ready." | 46 | Flash v2.5 | 23 | ~$0.005 |
| SubagentStop summary | "Research agent completed: found 12 relevant papers." | 52 | Flash v2.5 | 26 | ~$0.006 |
| Error narration | "Build failed. Check logs." | 26 | Flash v2.5 | 13 | ~$0.003 |
| Commit narration | "Committed: fix routing bug in auth module" | 43 | Flash v2.5 | 21.5 | ~$0.005 |
| Session end | "Session complete. 47 minutes, 23 tool uses." | 46 | Flash v2.5 | 23 | ~$0.005 |

### 9.4 Monthly Budget Estimate

Assuming a typical day with 3-5 Claude Code sessions, each triggering 5-10 TTS events:

| Scenario | Sessions/Day | TTS Events/Session | Avg Chars/Event | Daily Chars | Monthly Chars | % of Pro Quota |
|----------|-----------:|------------------:|----------------:|-----------:|--------------:|---------------:|
| Light use | 3 | 5 | 50 | 750 | 22,500 | 2.25% |
| Medium use | 5 | 8 | 60 | 2,400 | 72,000 | 7.2% |
| Heavy use | 8 | 12 | 70 | 6,720 | 201,600 | 20.2% |

Even heavy use consumes only ~20% of the Pro plan's quota when using Flash v2.5. claude-voice is not a significant cost driver.

### 9.5 Cost Optimization Strategies

1. **Cache aggressively**: Common phrases (greetings, session start/end, error messages) are highly cacheable. A cache hit costs zero characters. The LRU cache (spec 06, CACHE_MAX_MB=500) stores WAV files keyed by text+voice+settings hash.

2. **Use Flash v2.5 exclusively**: 0.5 credits/char vs. 1.0 for v3/Multilingual. Same quality for short text.

3. **Pre-generate theme greetings**: At theme install time, generate all greeting variants and cache them. A theme with 10 greeting templates × 3 variants = 30 pre-cached phrases. One-time cost, permanent benefit.

4. **Text truncation for TTS**: The `SUBAGENT_MAX_WORDS = 20` constant (spec 06) ensures subagent summaries are concise. 20 words ≈ 100 characters ≈ 50 credits. No runaway costs from verbose summaries.

5. **Default to local**: The `tts.backend: auto` config defaults to local TTS (Kokoro/Piper). Cloud is a fallback, not the default. Users must explicitly opt into `tts.backend: elevenlabs` for cloud-first behavior.

6. **Monitor quota**: `get_backend_status()` returns `quota_remaining` from `GET /v1/user/subscription`. Alert the user (via TTS, appropriately) when quota drops below 20%.

### 9.6 Quota Monitoring

```http
GET https://api.elevenlabs.io/v1/user/subscription

Headers:
  xi-api-key: {API_KEY}
```

**Response** (relevant fields):

```json
{
  "tier": "pro",
  "character_count": 145230,
  "character_limit": 500000,
  "next_character_count_reset_unix": 1711929600,
  "voice_limit": 100,
  "can_use_instant_voice_cloning": true,
  "can_use_professional_voice_cloning": true
}
```

`character_count` is characters consumed this billing period. `character_limit` is the quota. Remaining = limit - count.

---

## 10. Error Handling

### 10.1 HTTP Error Reference

| HTTP Code | Error Body | Meaning | claude-voice Response |
|-----------|-----------|---------|----------------------|
| 200 | — | Success | Process audio |
| 400 | `{"detail": {"message": "..."}}` | Bad request — invalid parameters, empty text, unsupported format | Log error, fall to local TTS |
| 401 | `{"detail": {"message": "invalid_api_key"}}` | API key invalid, expired, or revoked | Log error, disable ElevenLabs for session, fall to local TTS |
| 403 | `{"detail": {"message": "..."}}` | Feature not available on plan (e.g., PCM 48kHz on Free) | Log, retry with lower format, fall to local TTS |
| 422 | `{"detail": {"message": "..."}}` | Validation error — text too long, invalid voice_id, invalid model_id | Log error, fall to local TTS |
| 429 | `{"detail": {"status": "too_many_concurrent_requests"}}` | Concurrent request limit exceeded | Exponential backoff (1 retry), then fall to local TTS |
| 429 | `{"detail": {"status": "quota_exceeded"}}` | Monthly character quota depleted | Disable ElevenLabs for remainder of billing period, fall to local TTS, warn user |
| 429 | `{"detail": {"status": "system_busy"}}` | ElevenLabs infrastructure overloaded | Backoff (1 retry at 2s), then fall to local TTS |
| 500 | — | Internal server error | Fall to local TTS immediately |
| 502/503 | — | Gateway/service unavailable | Fall to local TTS immediately |

### 10.2 Error Handling Implementation

```python
RETRIABLE_CODES = {429}
NON_RETRIABLE_CODES = {400, 401, 403, 422}

def _handle_api_response(response: httpx.Response) -> Optional[bytes]:
    """Handle ElevenLabs API response. Returns audio bytes or None."""
    if response.status_code == 200:
        return response.content

    status = response.status_code

    if status == 401:
        # Invalid API key — disable for this session
        global _elevenlabs_disabled
        _elevenlabs_disabled = True
        _log("ElevenLabs API key invalid. Disabled for session.")
        return None

    if status == 429:
        detail = _parse_error_detail(response)
        if detail.get("status") == "quota_exceeded":
            _elevenlabs_disabled = True
            _log("ElevenLabs quota exceeded. Disabled until reset.")
            return None
        # Concurrent limit or system busy — one retry with backoff
        return None  # Caller handles retry logic

    if status in NON_RETRIABLE_CODES:
        _log(f"ElevenLabs API error {status}: {response.text[:200]}")
        return None

    # 5xx — server error, no retry
    _log(f"ElevenLabs server error {status}")
    return None
```

### 10.3 Fallback Chain

When ElevenLabs fails for any reason:

```
ElevenLabs (cloud) → Kokoro-82M (local GPU) → Piper (local CPU) → pyttsx3 (espeak-ng) → silence
```

Every step in this chain is independently failable. The total time budget for all fallback attempts is `SUBAGENT_TIMEOUT_SECONDS = 10`. If the chain exhausts the budget, the result is silence — which is always acceptable.

---

## 11. SDK Integration

### 11.1 Official Python SDK Usage

The `elevenlabs` Python SDK provides a typed, ergonomic interface over the REST API. While claude-voice uses `httpx` directly for maximum control, the SDK is the canonical reference for API behavior.

**Installation**:

```bash
uv pip install elevenlabs
```

**Basic TTS (synchronous)**:

```python
from elevenlabs.client import ElevenLabs

client = ElevenLabs(api_key="sk_...")

# Standard generation — returns an iterator of audio bytes
audio_iterator = client.text_to_speech.convert(
    text="Commander, authentication module is ready.",
    voice_id="pNInz6obpgDQGcFmaJgB",  # Adam
    model_id="eleven_flash_v2_5",
    output_format="pcm_24000",
    voice_settings={
        "stability": 0.5,
        "similarity_boost": 0.75,
        "style": 0.0,
        "use_speaker_boost": True,
    },
)

# Collect all bytes
audio_bytes = b"".join(audio_iterator)

# Write to WAV (add header), resample to 48kHz, play via pw-play
wav_path = _pcm_to_wav(audio_bytes, Path("/tmp/tts_output.wav"), sample_rate=24000)
_resample_to_target(wav_path, Path("/tmp/tts_output_48k.wav"))
```

**Streaming TTS**:

```python
from elevenlabs import stream as play_stream
from elevenlabs.client import ElevenLabs

client = ElevenLabs(api_key="sk_...")

audio_stream = client.text_to_speech.stream(
    text="Commander, the research agent has completed its analysis.",
    voice_id="pNInz6obpgDQGcFmaJgB",
    model_id="eleven_flash_v2_5",
    output_format="pcm_24000",
)

# Option 1: Play directly via SDK's built-in player (uses mpv/ffplay)
play_stream(audio_stream)

# Option 2: Write chunks to file for pw-play integration
with open("/tmp/stream_output.pcm", "wb") as f:
    for chunk in audio_stream:
        if isinstance(chunk, bytes):
            f.write(chunk)
```

**Sound Effects Generation**:

```python
audio_iterator = client.text_to_sound_effects.convert(
    text="8-bit coin collect sound effect, bright and cheerful",
    duration_seconds=1.0,
    prompt_influence=0.5,
)

sfx_bytes = b"".join(audio_iterator)
```

**Voice Cloning**:

```python
voice = client.clone(
    name="Shawn Personal",
    description="Cloned from personal recordings",
    files=[
        "/path/to/sample1.mp3",
        "/path/to/sample2.mp3",
        "/path/to/sample3.mp3",
    ],
)
# voice.voice_id is now usable in TTS requests
```

**List Voices**:

```python
response = client.voices.get_all()
for voice in response.voices:
    print(f"{voice.voice_id}: {voice.name} ({voice.labels})")
```

**Check Subscription**:

```python
sub = client.user.get_subscription()
remaining = sub.character_limit - sub.character_count
print(f"Characters remaining: {remaining}/{sub.character_limit}")
```

### 11.2 Why claude-voice Uses httpx Instead of the SDK

The SDK is excellent for exploration and prototyping. For production hook code, we use `httpx` directly because:

1. **Dependency minimization**: `httpx` is already a dependency for other HTTP calls. The SDK adds `pydantic`, `httpx` (same), and several sub-dependencies. In hook contexts where import time matters, fewer deps = faster cold start.

2. **Timeout control**: `httpx.Timeout` allows per-phase timeouts (connect, read, write, pool). The SDK's timeout handling is coarser.

3. **Error handling**: We need raw HTTP status codes and response bodies for the fallback chain. The SDK raises exceptions that must be caught and inspected — an extra layer of indirection.

4. **Streaming control**: For chunk-by-chunk file writing (4b), raw `httpx.stream()` is simpler than the SDK's iterator wrapping.

The SDK remains the reference implementation. When ElevenLabs changes their API, the SDK's changelog is the authoritative source.

---

## 12. Theme-Specific Voice Curation

### 12.1 Voice Selection Criteria

Each theme should have a primary voice that matches its sonic DNA. Selection criteria:

1. **Timbre match**: The voice should "sound like it belongs" in the theme's world
2. **Gender/age alignment**: Match the expected character archetype
3. **Accent fit**: American English is default; theme-specific accents where they add character
4. **Consistency across lengths**: The voice should sound natural on both 5-word alerts and 30-word summaries
5. **Parameter responsiveness**: The voice should respond well to stability/similarity_boost adjustments without artifacts

### 12.2 Recommended Voices Per Theme

These recommendations are based on ElevenLabs' pre-made voice library. Voice IDs are stable and persistent.

#### Default Theme

| Voice | Voice ID | Gender | Age | Accent | Rationale |
|-------|----------|--------|-----|--------|-----------|
| **Adam** (primary) | `pNInz6obpgDQGcFmaJgB` | Male | Middle-aged | American | Neutral, clear, professional. The "NPR narrator" voice. Works for everything. |
| **Rachel** (alt) | `21m00Tcm4TlvDq8ikWAM` | Female | Young | American | Warm, conversational. Good alternative for users who prefer a female voice. |

#### StarCraft Theme

| Voice | Voice ID | Gender | Age | Accent | Rationale |
|-------|----------|--------|-----|--------|-----------|
| **Arnold** (primary) | `VR6AewLTigWG4xSOukaG` | Male | Middle-aged | American | Deep, authoritative, crisp. Adjutant-style military briefing voice. |
| **Custom (designed)** | TBD | Male | Middle-aged | American | Voice Design: "gruff military commander, authoritative, slight radio crackle quality" |

Voice settings: stability 0.70, similarity_boost 0.80, style 0.0. High stability for military precision.

#### Mario Theme

| Voice | Voice ID | Gender | Age | Accent | Rationale |
|-------|----------|--------|-----|--------|-----------|
| **Josh** (primary) | `TxGEqnHWrfWFTfGW9XjX` | Male | Young | American | Bright, energetic, slightly nasal. Toad-adjacent enthusiasm. |
| **Custom (designed)** | TBD | Male | Young | Italian-American | Voice Design: "cheerful young guide, bright and bouncy, slight Italian accent, video game announcer energy" |

Voice settings: stability 0.30, similarity_boost 0.70, style 0.3. Low stability for expressive, playful variation.

#### Zelda Theme

| Voice | Voice ID | Gender | Age | Accent | Rationale |
|-------|----------|--------|-----|--------|-----------|
| **Clyde** (primary) | `2EiwWnXFnvU5JabPnv8n` | Male | Old | British | Warm, wise, measured. Old sage narrating an ancient tale. |
| **Custom (designed)** | TBD | Male | Old | British | Voice Design: "elderly mystical sage, wise and gentle, slight ethereal reverb, Gandalf-adjacent warmth" |

Voice settings: stability 0.45, similarity_boost 0.75, style 0.2. Moderate expression for mystical gravitas.

#### Warcraft Theme

| Voice | Voice ID | Gender | Age | Accent | Rationale |
|-------|----------|--------|-----|--------|-----------|
| **Adam** (primary) | `pNInz6obpgDQGcFmaJgB` | Male | Middle-aged | American | Deep, commanding. Works for Alliance herald narration. |
| **Custom (designed)** | TBD | Male | Middle-aged | British | Voice Design: "deep-voiced medieval herald, commanding presence, stone hall resonance, Shakespearean training" |

Voice settings: stability 0.55, similarity_boost 0.80, style 0.2. Balanced command with warmth.

#### Metroid Theme

| Voice | Voice ID | Gender | Age | Accent | Rationale |
|-------|----------|--------|-----|--------|-----------|
| **Dorothy** (primary) | `ThT5KcBeYPX3keUQqHPh` | Female | Young | American | Calm, precise, AI-like. Ship computer (Aurora Unit) voice. |
| **Custom (designed)** | TBD | Female | Middle-aged | Neutral | Voice Design: "cold AI ship computer, precise and clinical, minimal emotion, slight digital processing quality" |

Voice settings: stability 0.65, similarity_boost 0.75, style 0.0. High stability for cold precision.

#### Halo Theme

| Voice | Voice ID | Gender | Age | Accent | Rationale |
|-------|----------|--------|-----|--------|-----------|
| **Rachel** (primary) | `21m00Tcm4TlvDq8ikWAM` | Female | Young | American | Warm, intelligent, slightly playful. Cortana-esque AI companion. |
| **Custom (designed)** | TBD | Female | Young | American | Voice Design: "intelligent AI companion, warm but professional, military context, slight playfulness, Cortana-inspired" |

Voice settings: stability 0.60, similarity_boost 0.80, style 0.1. Professional warmth with slight personality.

#### Pokemon Theme

| Voice | Voice ID | Gender | Age | Accent | Rationale |
|-------|----------|--------|-----|--------|-----------|
| **Josh** (primary) | `TxGEqnHWrfWFTfGW9XjX` | Male | Young | American | Energetic, bright, encouraging. Professor's assistant energy. |
| **Custom (designed)** | TBD | Male/Female | Young | American | Voice Design: "cheerful Pokemon professor assistant, encouraging and bright, adventure companion energy" |

Voice settings: stability 0.35, similarity_boost 0.70, style 0.3. High expression for enthusiasm.

### 12.3 Voice Curation Workflow

1. **Browse**: Use `list_voices()` to search the library with theme-appropriate filters
2. **Preview**: Use `preview_voice()` with theme-representative text to audition candidates
3. **Test settings**: Generate test audio with theme-specific voice settings and emotion overlays
4. **Record voice_id**: Store the chosen voice_id in the theme's `theme.json` under `tts.voice_id`
5. **Optional — design custom**: If no pre-made voice fits, use Voice Design to create one
6. **Optional — clone**: For personal narration, clone Shawn's voice from recordings

---

## 13. Open Questions

### 13.1 Should we use Sound Effects API for dynamic earcon generation instead of numpy synthesis?

**Arguments for**:
- Dramatically higher quality — real timbres vs. synthesized waveforms
- Theme fidelity — describe the exact sound you want in natural language
- Variation — generate multiple unique variants per prompt easily
- 48kHz native output matches PipeWire quantum

**Arguments against**:
- Network dependency — earcons fire on every hook event, cannot tolerate latency
- Cost — even small costs add up over thousands of hook events per month
- Non-deterministic — same prompt produces different output each time
- Latency — 500ms–3s generation time vs. < 50ms for numpy

**Recommended approach**: Hybrid. Use ElevenLabs SFX for **one-time offline asset generation** during theme setup. Generate all earcon variants, save as WAV files in the theme's `sounds/` directory, and serve them locally forever after. Numpy synthesis remains the runtime fallback for any missing or corrupted cached assets. This gives cloud quality with local reliability.

### 13.2 Should we pre-generate and cache all theme TTS greetings at install time?

**Arguments for**:
- Zero latency on first session — greeting plays from cache, no cloud round-trip
- Deterministic — the greeting always sounds the same (familiar, not jarring)
- Cost control — one-time generation, no per-session cost

**Arguments against**:
- Stale greetings — if greeting templates change, cache must be invalidated
- Storage — 30 greetings × 3 variants × ~50KB each = ~4.5MB per theme. Manageable.
- Less dynamic — can't incorporate session-specific context (time of day, project name)

**Recommended approach**: Pre-generate a base set of generic greetings at theme install time. Generate session-specific greetings (with time, project context) on the fly and cache them as they're generated. The cache naturally fills over time with the most common contexts.

### 13.3 Voice cloning: worth the effort for personal narration?

**Arguments for**:
- Highly personal — hearing your own voice as the system narrator is a unique experience
- Recordings exist — OBS recordings in `~/`, meeting recordings via claude-recordings
- IVC is instant — upload samples, get a voice_id, done

**Arguments against**:
- Uncanny valley risk — hearing a slightly-off version of your own voice is unsettling
- Quality dependency — IVC quality depends on recording quality and variety
- Privacy — voice data is uploaded to ElevenLabs servers

**Recommended approach**: Try it. IVC is low-cost and instant. Upload 3-5 clean recording segments, generate a test, and evaluate subjectively. If it's good, add it as a voice option. If it's uncanny, abandon it. No sunk cost.

### 13.4 Should we support multiple ElevenLabs voices per theme?

**Arguments for**:
- Different events could have different voices (e.g., error = stern voice, success = warm voice)
- Richer character — a theme could have a "cast" (primary narrator + alert voice + companion)
- More immersive — StarCraft could have Adjutant for status + Mengsk for achievements

**Arguments against**:
- Complexity — voice routing logic becomes more complex
- Cost — more voices = more unique cache entries = more generations before cache is warm
- Coherence — too many voices dilutes the theme's identity

**Recommended approach**: Support it in the schema (allow `tts.voices` as a map of `event_type → voice_id`), but default to a single primary voice per theme. Let power users define per-event voices if they want. The theme.json schema already supports this via the `tts.personality_modifiers` structure in spec 02 — extend it to include voice_id overrides per emotion/event.

### 13.5 WebSocket streaming — when does it become relevant?

The WebSocket API (`stream-input`) is designed for token-by-token text streaming. claude-voice currently receives complete text from hook events — the text is known in full before TTS begins. WebSocket adds value only when:

- claude-voice narrates LLM output as it streams (real-time narration of Claude's response)
- claude-voice implements a conversational loop (STT → LLM → TTS in real-time)

Neither is in scope for initial implementation. Revisit when spec 07 (STT engine) reaches implementation stage.

---

## 14. References

- [ElevenLabs API Documentation](https://elevenlabs.io/docs/overview/intro)
- [ElevenLabs Models Overview](https://elevenlabs.io/docs/overview/models)
- [ElevenLabs API Pricing](https://elevenlabs.io/pricing/api)
- [ElevenLabs Python SDK (GitHub)](https://github.com/elevenlabs/elevenlabs-python)
- [ElevenLabs Sound Effects Documentation](https://elevenlabs.io/docs/overview/capabilities/sound-effects)
- [ElevenLabs Voice Cloning Overview](https://elevenlabs.io/docs/eleven-creative/voices/voice-cloning)
- [ElevenLabs Streaming Documentation](https://elevenlabs.io/docs/api-reference/streaming)
- [ElevenLabs Voice Library](https://elevenlabs.io/docs/eleven-creative/voices/voice-library)
- [ElevenLabs Supported Output Formats](https://help.elevenlabs.io/hc/en-us/articles/15754340124305-What-audio-formats-do-you-support)
- [ElevenLabs Rate Limits](https://help.elevenlabs.io/hc/en-us/articles/14312733311761-How-many-Text-to-Speech-requests-can-I-make-and-can-I-increase-it)
- [Eleven v3 Review](https://metapress.com/eleven-v3-review-a-premium-ai-voice-model-built-for-performance-not-just-narration/)
- [ElevenLabs Pricing Breakdown 2026 (Flexprice)](https://flexprice.io/blog/elevenlabs-pricing-breakdown)
