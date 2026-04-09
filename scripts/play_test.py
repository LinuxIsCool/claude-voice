# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy"]
# ///
"""
Audio backend latency tester for claude-voice.

Tests available audio playback backends (pw-play, paplay, aplay, mpv) by:
1. Generating a short 440Hz test tone WAV
2. Timing each backend's playback startup
3. Optionally testing with an actual theme sound

Usage:
    uv run scripts/play_test.py
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLE_RATE = 48_000
TEST_DURATION_S = 0.2
TEST_FREQ_HZ = 440
PEAK_LINEAR = 0.707  # -3dB

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
THEME_SOUNDS_DIR = PLUGIN_ROOT / "assets" / "themes" / "default" / "sounds"

BACKENDS = [
    {"name": "pw-play", "cmd": ["pw-play"], "desc": "PipeWire native"},
    {"name": "paplay", "cmd": ["paplay"], "desc": "PulseAudio"},
    {"name": "aplay", "cmd": ["aplay"], "desc": "ALSA"},
    {"name": "mpv", "cmd": ["mpv", "--no-video", "--really-quiet"], "desc": "mpv (fallback)"},
]

# ---------------------------------------------------------------------------
# WAV generation (numpy only, no scipy dependency)
# ---------------------------------------------------------------------------


def generate_test_wav(filepath: Path) -> None:
    """Generate a 440Hz sine wave WAV: 48kHz, 16-bit, stereo."""
    n_samples = int(SAMPLE_RATE * TEST_DURATION_S)
    t = np.linspace(0, TEST_DURATION_S, n_samples, endpoint=False)

    # Sine with ADSR to avoid clicks
    sig = np.sin(2 * np.pi * TEST_FREQ_HZ * t)

    # Simple fade in/out (5ms each)
    fade_len = int(SAMPLE_RATE * 0.005)
    sig[:fade_len] *= np.linspace(0, 1, fade_len)
    sig[-fade_len:] *= np.linspace(1, 0, fade_len)

    sig = sig / np.abs(sig).max() * PEAK_LINEAR
    int_sig = (sig * 32767).astype(np.int16)
    stereo = np.column_stack([int_sig, int_sig])

    # Write WAV manually (avoid scipy dependency)
    data = stereo.tobytes()
    n_channels = 2
    sample_width = 2  # 16-bit
    byte_rate = SAMPLE_RATE * n_channels * sample_width
    block_align = n_channels * sample_width
    data_size = len(data)
    file_size = 36 + data_size

    with open(filepath, "wb") as f:
        f.write(b"RIFF")
        f.write(struct.pack("<I", file_size))
        f.write(b"WAVE")
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))  # chunk size
        f.write(struct.pack("<H", 1))   # PCM
        f.write(struct.pack("<H", n_channels))
        f.write(struct.pack("<I", SAMPLE_RATE))
        f.write(struct.pack("<I", byte_rate))
        f.write(struct.pack("<H", block_align))
        f.write(struct.pack("<H", sample_width * 8))
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(data)


def test_backend(backend: dict, wav_path: Path) -> dict:
    """Test a single audio backend. Returns result dict."""
    name = backend["name"]
    binary = backend["cmd"][0]

    # Check if binary exists
    if not shutil.which(binary):
        return {"name": name, "desc": backend["desc"], "available": False,
                "latency_ms": None, "status": "NOT FOUND"}

    cmd = backend["cmd"] + [str(wav_path)]

    try:
        t_start = time.perf_counter()
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        t_launched = time.perf_counter()
        launch_latency_ms = (t_launched - t_start) * 1000

        # Wait for playback to finish (timeout 5s)
        proc.wait(timeout=5)
        t_done = time.perf_counter()
        total_ms = (t_done - t_start) * 1000

        if proc.returncode == 0:
            return {"name": name, "desc": backend["desc"], "available": True,
                    "latency_ms": launch_latency_ms, "total_ms": total_ms,
                    "status": "PASS"}
        else:
            return {"name": name, "desc": backend["desc"], "available": True,
                    "latency_ms": launch_latency_ms, "total_ms": total_ms,
                    "status": f"FAIL (rc={proc.returncode})"}

    except subprocess.TimeoutExpired:
        proc.kill()
        return {"name": name, "desc": backend["desc"], "available": True,
                "latency_ms": None, "status": "TIMEOUT"}
    except Exception as e:
        return {"name": name, "desc": backend["desc"], "available": True,
                "latency_ms": None, "status": f"ERROR: {e}"}


def find_theme_sound() -> Path | None:
    """Find an actual theme sound to test with."""
    if not THEME_SOUNDS_DIR.exists():
        return None
    wavs = sorted(THEME_SOUNDS_DIR.glob("*.wav"))
    # Pick a short one (prompt-ack is 150ms)
    for w in wavs:
        if "prompt-ack" in w.name:
            return w
    return wavs[0] if wavs else None


def print_results(results: list[dict], label: str) -> None:
    """Print formatted results table."""
    print(f"\n  {label}")
    print(f"  {'Backend':<12} {'Description':<20} {'Launch (ms)':<14} {'Total (ms)':<14} {'Status'}")
    print(f"  {'-' * 72}")
    for r in results:
        latency = f"{r['latency_ms']:.1f}" if r.get("latency_ms") is not None else "-"
        total = f"{r.get('total_ms', 0):.1f}" if r.get("total_ms") else "-"
        print(f"  {r['name']:<12} {r['desc']:<20} {latency:<14} {total:<14} {r['status']}")


def main() -> None:
    print("claude-voice Audio Backend Test")
    print("=" * 40)

    with tempfile.TemporaryDirectory() as tmpdir:
        test_wav = Path(tmpdir) / "test_440hz.wav"
        generate_test_wav(test_wav)
        print(f"\nGenerated test tone: {test_wav} ({test_wav.stat().st_size} bytes)")

        # Test with synthetic tone
        results_synth = []
        for backend in BACKENDS:
            result = test_backend(backend, test_wav)
            results_synth.append(result)

        print_results(results_synth, "Synthetic 440Hz tone (200ms)")

        # Test with actual theme sound
        theme_wav = find_theme_sound()
        if theme_wav:
            print(f"\n  Theme sound: {theme_wav.name}")
            results_theme = []
            for backend in BACKENDS:
                if backend["cmd"][0] and shutil.which(backend["cmd"][0]):
                    result = test_backend(backend, theme_wav)
                    results_theme.append(result)
            print_results(results_theme, f"Theme sound: {theme_wav.name}")
        else:
            print("\n  No theme sounds found — skipping theme test")

    # Summary
    passing = [r for r in results_synth if r["status"] == "PASS"]
    print(f"\n  Available backends: {len(passing)}/{len(BACKENDS)}")
    if passing:
        best = min(passing, key=lambda r: r["latency_ms"] or 999)
        print(f"  Recommended: {best['name']} (launch latency: {best['latency_ms']:.1f}ms)")
    else:
        print("  WARNING: No working audio backends found!")

    print()


if __name__ == "__main__":
    main()
