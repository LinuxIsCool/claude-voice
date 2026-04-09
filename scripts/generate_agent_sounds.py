# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy", "soundfile"]
# ///
"""Generate per-agent sound profiles for claude-voice.

Each agent gets 4 sounds: select, acknowledge, complete, error.
Each sound has a unique tonal signature derived from the agent's identity.

Usage:
    uv run scripts/generate_agent_sounds.py [--theme default]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import scipy.signal

THEMES_DIR = Path(__file__).resolve().parent.parent / "assets" / "themes"
SAMPLE_RATE = 48000  # SYNC: lib/constants.py:TARGET_SAMPLE_RATE

# Agent tonal signatures — each gets a base frequency and character
AGENTS = {
    "matt": {"base_freq": 880, "character": "military", "color": "square"},
    "darren": {"base_freq": 660, "character": "organic", "color": "sine"},
    "philipp": {"base_freq": 740, "character": "analytical", "color": "triangle"},
    "_default": {"base_freq": 523, "character": "neutral", "color": "sine"},
}

# Sound slot definitions
SLOTS = {
    "select": {"duration": 0.15, "envelope": "sharp", "interval": "fifth"},
    "acknowledge": {"duration": 0.2, "envelope": "smooth", "interval": "octave"},
    "complete": {"duration": 0.3, "envelope": "fade", "interval": "major_third"},
    "error": {"duration": 0.25, "envelope": "sharp", "interval": "minor_second"},
}

INTERVALS = {
    "fifth": 1.5,
    "octave": 2.0,
    "major_third": 1.25,
    "minor_second": 1.067,
}


def generate_tone(freq: float, duration: float, wave: str = "sine",
                  sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Generate a basic waveform."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    match wave:
        case "sine":
            return np.sin(2 * np.pi * freq * t)
        case "square":
            return scipy.signal.square(2 * np.pi * freq * t) * 0.5
        case "triangle":
            return scipy.signal.sawtooth(2 * np.pi * freq * t, width=0.5)
        case _:
            return np.sin(2 * np.pi * freq * t)


def apply_envelope(audio: np.ndarray, envelope: str) -> np.ndarray:
    """Apply amplitude envelope."""
    n = len(audio)
    match envelope:
        case "sharp":
            # Fast attack, quick decay
            env = np.exp(-np.linspace(0, 5, n))
        case "smooth":
            # Gentle fade in and out
            attack = np.linspace(0, 1, n // 4)
            sustain = np.ones(n // 2)
            release = np.linspace(1, 0, n - n // 4 - n // 2)
            env = np.concatenate([attack, sustain, release])
        case "fade":
            # Long fade out (completion feeling)
            env = np.linspace(1, 0, n) ** 0.5
        case _:
            env = np.ones(n)
    return audio * env


def generate_agent_sound(agent: dict, slot_def: dict) -> np.ndarray:
    """Generate a single agent sound."""
    freq = agent["base_freq"]
    interval = INTERVALS[slot_def["interval"]]
    duration = slot_def["duration"]
    wave = agent["color"]

    # Two-tone: base + interval
    tone1 = generate_tone(freq, duration, wave)
    tone2 = generate_tone(freq * interval, duration, wave) * 0.6
    mixed = (tone1 + tone2) * 0.5

    # Apply envelope
    shaped = apply_envelope(mixed, slot_def["envelope"])

    # Normalize to [-0.8, 0.8] to avoid clipping
    peak = np.abs(shaped).max()
    if peak > 0:
        shaped = shaped * (0.8 / peak)

    return shaped


def save_wav(audio: np.ndarray, path: Path, sample_rate: int = SAMPLE_RATE) -> None:
    """Save mono audio as 48kHz stereo WAV (PipeWire native)."""
    import soundfile as sf  # type: ignore

    stereo = np.column_stack([audio, audio])
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), stereo, sample_rate, subtype="PCM_16")


def main():
    parser = argparse.ArgumentParser(description="Generate agent sound profiles")
    parser.add_argument("--theme", default="default", help="Theme to generate for")
    args = parser.parse_args()

    theme_dir = THEMES_DIR / args.theme / "sounds" / "agents"
    theme_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for agent_name, agent_def in AGENTS.items():
        for slot_name, slot_def in SLOTS.items():
            audio = generate_agent_sound(agent_def, slot_def)
            filename = f"{agent_name}-{slot_name}.wav"
            path = theme_dir / filename
            save_wav(audio, path)
            count += 1
            print(f"  {filename} ({len(audio)/SAMPLE_RATE*1000:.0f}ms)")

    print(f"\nGenerated {count} agent sounds in {theme_dir}")


if __name__ == "__main__":
    main()
