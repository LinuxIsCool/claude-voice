#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["kokoro>=0.9.4", "soundfile", "scipy", "pip", "en-core-web-sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"]
# ///
"""Pre-generate TTS greetings for all themes.

Warms the TTS cache so that hook-time playback is instant (cache lookup only).
Run this after installing/updating themes:

    uv run scripts/tts_warmup.py
    uv run scripts/tts_warmup.py --theme starcraft
    uv run scripts/tts_warmup.py --voice af_heart --list-voices
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import soundfile as sf

warnings.filterwarnings("ignore")

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
THEMES_DIR = PLUGIN_ROOT / "assets" / "themes"
CACHE_DIR = Path("~/.claude/local/voice/cache/tts").expanduser()
# SYNC: lib/constants.py:TARGET_SAMPLE_RATE — must match
TARGET_SAMPLE_RATE = 48000

# Kokoro voice presets (as of v0.9.4)
KOKORO_VOICES = [
    "af_heart",      # American Female, warm
    "af_alloy",      # American Female, neutral
    "af_aoede",      # American Female, clear
    "af_bella",      # American Female, bright
    "af_jessica",    # American Female, conversational
    "af_nicole",     # American Female, professional
    "af_nova",       # American Female, energetic
    "af_river",      # American Female, calm
    "af_sarah",      # American Female, natural
    "af_sky",        # American Female, light
    "am_adam",       # American Male, warm
    "am_echo",       # American Male, deep
    "am_eric",       # American Male, clear
    "am_fenrir",     # American Male, strong
    "am_liam",       # American Male, neutral
    "am_michael",    # American Male, conversational
    "am_onyx",       # American Male, authoritative
    "am_puck",       # American Male, playful
    "am_santa",      # American Male, jolly
    "bf_alice",      # British Female
    "bf_lily",       # British Female
    "bf_emma",       # British Female
    "bm_daniel",     # British Male
    "bm_fable",      # British Male
    "bm_george",     # British Male
    "bm_lewis",      # British Male
]

# Theme-to-voice mapping (defaults, can be overridden in theme.json)
THEME_VOICES = {
    "default": "am_michael",
    "starcraft": "am_onyx",        # Authoritative military commander
    "warcraft": "am_fenrir",       # Strong fantasy warrior
    "mario": "am_puck",            # Playful, energetic
    "zelda": "af_river",           # Calm, mystical
    "smash": "am_echo",            # Deep, announcer-like
    "kingdom-hearts": "af_heart",  # Warm, emotional
}


# SYNC: lib/utils.py:cache_key — must match exactly
def cache_key(text: str, voice: str) -> str:
    content = f"{voice}:{text}".encode("utf-8")
    return hashlib.sha256(content).hexdigest()[:16]


def load_theme_json(theme_name: str) -> dict:
    path = THEMES_DIR / theme_name / "theme.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def get_greeting_text(theme_config: dict) -> str:
    greeting = (
        theme_config.get("tts", {}).get("greeting_template")
        or theme_config.get("meta", {}).get("greeting_template")
        or "{summary}"
    )
    return greeting.replace("{summary}", "session ready")


def synthesize_and_save(pipe, text: str, voice: str, output: Path) -> bool:
    """Synthesize text and save to WAV file."""
    try:
        t0 = time.time()
        chunks = []
        for _, _, audio in pipe(text, voice=voice, speed=1.0):
            chunks.append(audio)

        if not chunks:
            return False

        audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]

        # Upsample 24kHz mono → 48kHz stereo
        from scipy.signal import resample_poly
        audio_48k = resample_poly(audio, 2, 1)
        stereo = np.column_stack([audio_48k, audio_48k])

        sf.write(str(output), stereo, TARGET_SAMPLE_RATE, subtype="PCM_16")
        elapsed = time.time() - t0
        duration = len(audio) / 24000
        print(f"  ✓ {output.name} ({elapsed:.2f}s → {duration:.1f}s audio)")
        return True
    except Exception as e:
        print(f"  ✗ Failed: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Pre-generate TTS greetings for themes")
    parser.add_argument("--theme", default="all", help="Theme to warm up (default: all)")
    parser.add_argument("--voice", help="Override voice for all themes")
    parser.add_argument("--text", help="Custom text to synthesize")
    parser.add_argument("--list-voices", action="store_true", help="List available Kokoro voices")
    args = parser.parse_args()

    if args.list_voices:
        print("Available Kokoro voices:")
        for v in KOKORO_VOICES:
            default_for = [t for t, voice in THEME_VOICES.items() if voice == v]
            suffix = f" (default for: {', '.join(default_for)})" if default_for else ""
            print(f"  {v}{suffix}")
        return

    # Initialize Kokoro
    print("Loading Kokoro-82M...", flush=True)
    t0 = time.time()
    from kokoro import KPipeline
    pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    print(f"Ready in {time.time()-t0:.1f}s", flush=True)

    # Warm up with a dummy synthesis
    for _, _, _ in pipe("warmup", voice="af_heart", speed=1.0):
        break

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Determine themes to process
    if args.theme == "all":
        themes = sorted(d.name for d in THEMES_DIR.iterdir() if d.is_dir())
    else:
        themes = [args.theme]

    total_generated = 0
    total_cached = 0

    for theme_name in themes:
        theme_config = load_theme_json(theme_name)
        voice = args.voice or THEME_VOICES.get(theme_name, "am_michael")
        text = args.text or get_greeting_text(theme_config)

        key = cache_key(text, voice)
        output = CACHE_DIR / f"{key}.wav"

        print(f"\n[{theme_name}] voice={voice}")
        print(f'  Text: "{text}"')

        if output.exists() and not args.text:
            print(f"  → Cached: {output.name}")
            total_cached += 1
            continue

        if synthesize_and_save(pipe, text, voice, output):
            total_generated += 1
        else:
            print(f"  ✗ Failed to generate for {theme_name}")

    print(f"\nDone: {total_generated} generated, {total_cached} cached, {len(themes)} themes")


if __name__ == "__main__":
    main()
