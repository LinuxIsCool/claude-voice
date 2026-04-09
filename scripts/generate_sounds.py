# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "scipy"]
# ///
"""
Generate WAV earcon files for claude-voice themes.

Each sound is synthesized from scratch using numpy + scipy. Variants apply slight
randomized variation (pitch, duration, filter cutoff) to avoid repetitive fatigue.

Supports 7 themes: default, starcraft, warcraft, mario, zelda, smash, kingdom-hearts.
Each theme has 12 events x 3 variants = 36 WAV files (except ambient = 1 variant).

Usage:
    uv run scripts/generate_sounds.py                       # all themes
    uv run scripts/generate_sounds.py --theme starcraft     # one theme
    uv run scripts/generate_sounds.py --list-themes         # show available
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

import numpy as np
from scipy import signal as sp_signal
from scipy.io import wavfile

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLE_RATE = 48_000
BIT_DEPTH = 16
PEAK_DB = -3.0  # normalize target
PEAK_LINEAR = 10 ** (PEAK_DB / 20.0)  # 0.707

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_TEMPLATE = "assets/themes/{theme}/sounds"

# Variant offsets: pitch (cents), duration (fraction), filter (Hz)
VARIANT_OFFSETS = [
    {"pitch_cents": 0, "dur_scale": 1.0, "filter_hz": 0},
    {"pitch_cents": +50, "dur_scale": 0.90, "filter_hz": +500},
    {"pitch_cents": -50, "dur_scale": 1.10, "filter_hz": -500},
]

# ---------------------------------------------------------------------------
# DSP Primitives
# ---------------------------------------------------------------------------


def cents_to_ratio(cents: float) -> float:
    """Convert pitch offset in cents to frequency multiplier."""
    return 2 ** (cents / 1200.0)


def make_time(duration_s: float) -> np.ndarray:
    """Create a time array for the given duration at SAMPLE_RATE."""
    n_samples = int(SAMPLE_RATE * duration_s)
    return np.linspace(0, duration_s, n_samples, endpoint=False)


def sine(freq: float, t: np.ndarray) -> np.ndarray:
    """Pure sine oscillator."""
    return np.sin(2 * np.pi * freq * t)


def sawtooth(freq: float, t: np.ndarray) -> np.ndarray:
    """Sawtooth oscillator (scipy)."""
    return sp_signal.sawtooth(2 * np.pi * freq * t)


def triangle(freq: float, t: np.ndarray) -> np.ndarray:
    """Triangle oscillator."""
    return sp_signal.sawtooth(2 * np.pi * freq * t, width=0.5)


def square(freq: float, t: np.ndarray) -> np.ndarray:
    """Square wave oscillator."""
    return np.sign(np.sin(2 * np.pi * freq * t))


def white_noise(n: int, seed: int = 42) -> np.ndarray:
    """Generate white noise samples."""
    return np.random.default_rng(seed).normal(0, 1, n)


def fm_synth(carrier: float, mod_freq: float, mod_index: float | np.ndarray, t: np.ndarray) -> np.ndarray:
    """FM synthesis: carrier modulated by mod_freq with given index."""
    return np.sin(2 * np.pi * carrier * t + mod_index * np.sin(2 * np.pi * mod_freq * t))


def sweep(f_start: float, f_end: float, t: np.ndarray) -> np.ndarray:
    """Linear frequency sweep from f_start to f_end over t."""
    return sp_signal.chirp(t, f_start, t[-1], f_end, method="linear")


def adsr_envelope(
    n_samples: int,
    attack_ms: float = 10,
    decay_ms: float = 30,
    sustain_level: float = 0.8,
    release_ms: float = 30,
) -> np.ndarray:
    """
    Generate an ADSR envelope. The sustain portion fills whatever remains
    after attack + decay + release.
    """
    a = int(SAMPLE_RATE * attack_ms / 1000)
    d = int(SAMPLE_RATE * decay_ms / 1000)
    r = int(SAMPLE_RATE * release_ms / 1000)
    s = max(0, n_samples - a - d - r)

    env = np.concatenate([
        np.linspace(0, 1, a, endpoint=False),                # attack
        np.linspace(1, sustain_level, d, endpoint=False),     # decay
        np.full(s, sustain_level),                            # sustain
        np.linspace(sustain_level, 0, r, endpoint=True),      # release
    ])
    # Trim or pad to exact length
    if len(env) > n_samples:
        env = env[:n_samples]
    elif len(env) < n_samples:
        env = np.pad(env, (0, n_samples - len(env)))
    return env


def simple_reverb(sig: np.ndarray, decay: float = 0.3, length_ms: float = 80) -> np.ndarray:
    """Convolve with an exponentially decaying noise burst for light room reverb."""
    n_ir = int(SAMPLE_RATE * length_ms / 1000)
    ir = np.random.default_rng(42).normal(0, 1, n_ir)
    ir *= np.exp(-np.linspace(0, 5, n_ir)) * decay
    ir[0] = 1.0  # direct signal
    wet = np.convolve(sig, ir, mode="full")[: len(sig)]
    return wet


def lowpass(sig: np.ndarray, cutoff_hz: float) -> np.ndarray:
    """Simple Butterworth lowpass filter."""
    cutoff_hz = max(100, min(cutoff_hz, SAMPLE_RATE / 2 - 100))
    sos = sp_signal.butter(2, cutoff_hz, btype="low", fs=SAMPLE_RATE, output="sos")
    return sp_signal.sosfilt(sos, sig)


def highpass(sig: np.ndarray, cutoff_hz: float) -> np.ndarray:
    """Butterworth highpass filter."""
    cutoff_hz = max(100, min(cutoff_hz, SAMPLE_RATE / 2 - 100))
    sos = sp_signal.butter(2, cutoff_hz, btype="high", fs=SAMPLE_RATE, output="sos")
    return sp_signal.sosfilt(sos, sig)


def bandpass(sig: np.ndarray, low: float, high: float) -> np.ndarray:
    """Butterworth bandpass filter."""
    low = max(100, min(low, SAMPLE_RATE / 2 - 200))
    high = max(low + 50, min(high, SAMPLE_RATE / 2 - 100))
    sos = sp_signal.butter(2, [low, high], btype="band", fs=SAMPLE_RATE, output="sos")
    return sp_signal.sosfilt(sos, sig)


def normalize(sig: np.ndarray) -> np.ndarray:
    """Peak-normalize to PEAK_LINEAR (-3dB)."""
    peak = np.abs(sig).max()
    if peak < 1e-10:
        return sig
    return sig / peak * PEAK_LINEAR


def to_stereo_16bit(sig: np.ndarray) -> np.ndarray:
    """Convert mono float signal to stereo 16-bit int array."""
    sig = np.clip(sig, -1.0, 1.0)
    int_sig = (sig * 32767).astype(np.int16)
    return np.column_stack([int_sig, int_sig])


# ---------------------------------------------------------------------------
# Default Theme Generators (renamed from gen_*)
# ---------------------------------------------------------------------------


def gen_default_session_start(duration_s: float = 0.6, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Ascending sweep 400->1200Hz with warmth and reverb."""
    t = make_time(duration_s)
    f_lo = 400 * pitch_ratio
    f_hi = 1200 * pitch_ratio

    sig = sweep(f_lo, f_hi, t) * 0.7
    sig += sine(f_lo * 0.5, t) * 0.2
    sig += sine(f_hi * 1.5, t) * 0.1 * np.linspace(0, 1, len(t))

    env = adsr_envelope(len(t), attack_ms=80, decay_ms=100, sustain_level=0.7, release_ms=120)
    sig *= env
    sig = lowpass(sig, 3000 + filter_offset)
    sig = simple_reverb(sig, decay=0.25, length_ms=100)
    return normalize(sig)


def gen_default_session_end(duration_s: float = 0.4, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Descending sweep 800->400Hz with gentle fadeout."""
    t = make_time(duration_s)
    f_hi = 800 * pitch_ratio
    f_lo = 400 * pitch_ratio

    sig = sweep(f_hi, f_lo, t) * 0.6
    sig += sine(f_lo, t) * 0.3
    sig += sine(f_hi * 2, t) * 0.1 * np.exp(-t * 8)

    env = adsr_envelope(len(t), attack_ms=20, decay_ms=60, sustain_level=0.5, release_ms=150)
    sig *= env
    sig = lowpass(sig, 2500 + filter_offset)
    sig = simple_reverb(sig, decay=0.2, length_ms=60)
    return normalize(sig)


def gen_default_task_complete(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Two-note major 3rd (C5 + E5), bright and satisfying."""
    t = make_time(duration_s)
    f1 = 523 * pitch_ratio
    f2 = 659 * pitch_ratio

    half = len(t) // 2
    sig = np.zeros(len(t))
    sig[:half] += sine(f1, t[:half]) * 0.6
    sig[:half] += sine(f1 * 2, t[:half]) * 0.15
    overlap_start = int(half * 0.85)
    sig[overlap_start:] += sine(f2, t[: len(t) - overlap_start]) * 0.6
    sig[overlap_start:] += sine(f2 * 2, t[: len(t) - overlap_start]) * 0.15

    env = adsr_envelope(len(t), attack_ms=8, decay_ms=40, sustain_level=0.6, release_ms=60)
    sig *= env
    sig = lowpass(sig, 4000 + filter_offset)
    sig = simple_reverb(sig, decay=0.15, length_ms=50)
    return normalize(sig)


def gen_default_prompt_ack(duration_s: float = 0.15, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Soft 600Hz click/tick, almost subliminal."""
    t = make_time(duration_s)
    freq = 600 * pitch_ratio

    sig = sine(freq, t) * 0.4
    sig += sine(freq * 3, t) * 0.15 * np.exp(-t * 60)
    click_len = min(int(SAMPLE_RATE * 0.005), len(t))
    noise_click = np.random.default_rng(7).normal(0, 0.1, click_len)
    sig[:click_len] += noise_click * np.exp(-np.linspace(0, 10, click_len))

    env = adsr_envelope(len(t), attack_ms=3, decay_ms=20, sustain_level=0.3, release_ms=40)
    sig *= env
    sig = lowpass(sig, 2000 + filter_offset)
    return normalize(sig)


def gen_default_agent_deploy(duration_s: float = 0.25, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Rising sweep 500->1000Hz with slight sawtooth character."""
    t = make_time(duration_s)
    f_lo = 500 * pitch_ratio
    f_hi = 1000 * pitch_ratio

    sig = sweep(f_lo, f_hi, t) * 0.5
    sig += sawtooth(f_lo * 0.98, t) * 0.15 * np.linspace(0.3, 0.8, len(t))
    sig += sine(f_hi * 1.5, t) * 0.1 * np.linspace(0, 1, len(t))

    env = adsr_envelope(len(t), attack_ms=10, decay_ms=30, sustain_level=0.7, release_ms=50)
    sig *= env
    sig = lowpass(sig, 3500 + filter_offset)
    sig = simple_reverb(sig, decay=0.1, length_ms=40)
    return normalize(sig)


def gen_default_agent_return(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Falling chime 1000->600Hz, bell-like with FM character."""
    t = make_time(duration_s)
    f_hi = 1000 * pitch_ratio
    f_lo = 600 * pitch_ratio

    modulator = sine(f_hi * 1.4, t) * 200 * np.exp(-t * 6)
    carrier_freq = np.linspace(f_hi, f_lo, len(t)) + modulator
    phase = np.cumsum(carrier_freq / SAMPLE_RATE) * 2 * np.pi
    sig = np.sin(phase) * 0.6

    sig += sine(f_hi * 2, t) * 0.2 * np.exp(-t * 10)

    env = adsr_envelope(len(t), attack_ms=5, decay_ms=50, sustain_level=0.5, release_ms=80)
    sig *= env
    sig = lowpass(sig, 4000 + filter_offset)
    sig = simple_reverb(sig, decay=0.2, length_ms=70)
    return normalize(sig)


def gen_default_error(duration_s: float = 0.25, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Dissonant minor 2nd (300Hz + 317Hz), buzz character."""
    t = make_time(duration_s)
    f1 = 300 * pitch_ratio
    f2 = 317 * pitch_ratio

    sig = sine(f1, t) * 0.4
    sig += sine(f2, t) * 0.4
    sig += sawtooth(f1 * 0.5, t) * 0.1
    sig += sine(f1 * 3.1, t) * 0.08 * np.exp(-t * 8)

    env = adsr_envelope(len(t), attack_ms=5, decay_ms=30, sustain_level=0.7, release_ms=40)
    sig *= env
    sig = lowpass(sig, 2500 + filter_offset)
    return normalize(sig)


def gen_default_notification(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Two-tone alert ping: 1000Hz then 1500Hz."""
    half_dur = duration_s / 2
    t1 = make_time(half_dur)
    t2 = make_time(half_dur)
    f1 = 1000 * pitch_ratio
    f2 = 1500 * pitch_ratio

    note1 = sine(f1, t1) * 0.5 + sine(f1 * 2, t1) * 0.15
    note2 = sine(f2, t2) * 0.5 + sine(f2 * 2, t2) * 0.15

    env1 = adsr_envelope(len(t1), attack_ms=5, decay_ms=20, sustain_level=0.7, release_ms=30)
    env2 = adsr_envelope(len(t2), attack_ms=5, decay_ms=20, sustain_level=0.7, release_ms=30)

    sig = np.concatenate([note1 * env1, note2 * env2])
    sig = lowpass(sig, 5000 + filter_offset)
    sig = simple_reverb(sig, decay=0.1, length_ms=30)
    return normalize(sig)


def gen_default_commit(duration_s: float = 0.5, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Ascending arpeggio C4->E4->G4->C5, fanfare feel."""
    freqs = [262, 330, 392, 523]
    freqs = [f * pitch_ratio for f in freqs]
    note_dur = duration_s / len(freqs)

    parts = []
    for freq in freqs:
        t_note = make_time(note_dur)
        note = sine(freq, t_note) * 0.5
        note += sine(freq * 2, t_note) * 0.15
        note += triangle(freq * 3, t_note) * 0.05
        env = adsr_envelope(len(t_note), attack_ms=8, decay_ms=15, sustain_level=0.65, release_ms=25)
        parts.append(note * env)

    sig = np.concatenate(parts)
    sig = lowpass(sig, 4500 + filter_offset)
    sig = simple_reverb(sig, decay=0.15, length_ms=60)
    return normalize(sig)


def gen_default_permission(duration_s: float = 0.2, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Sharp 1500Hz ping, bright, attention-demanding."""
    t = make_time(duration_s)
    freq = 1500 * pitch_ratio

    sig = sine(freq, t) * 0.6
    sig += sine(freq * 2, t) * 0.2 * np.exp(-t * 15)
    sig += sine(freq * 3, t) * 0.15 * np.exp(-t * 25)

    env = adsr_envelope(len(t), attack_ms=3, decay_ms=25, sustain_level=0.5, release_ms=40)
    sig *= env
    sig = lowpass(sig, 6000 + filter_offset)
    sig = simple_reverb(sig, decay=0.1, length_ms=30)
    return normalize(sig)


def gen_default_compact(duration_s: float = 0.2, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Compressed pulse: 800Hz with fast tremolo (AM at 20Hz)."""
    t = make_time(duration_s)
    freq = 800 * pitch_ratio
    tremolo_rate = 20

    carrier = sine(freq, t)
    tremolo = 0.5 + 0.5 * sine(tremolo_rate, t)
    sig = carrier * tremolo * 0.6

    sig += sine(freq * 0.5, t) * 0.15

    env = adsr_envelope(len(t), attack_ms=5, decay_ms=20, sustain_level=0.7, release_ms=30)
    sig *= env
    sig = lowpass(sig, 3000 + filter_offset)
    return normalize(sig)


def gen_default_ambient(duration_s: float = 3.0, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Low 200-400Hz drone, smooth, looping-friendly with fade in/out."""
    t = make_time(duration_s)
    f_lo = 200 * pitch_ratio
    f_hi = 400 * pitch_ratio

    sig = sine(f_lo, t) * 0.3
    sig += sine(f_lo * 1.5, t) * 0.15
    sig += sine(f_hi, t) * 0.1
    sig += triangle(f_lo * 0.5, t) * 0.1

    lfo = 0.8 + 0.2 * sine(0.5, t)
    sig *= lfo

    fade_len = int(SAMPLE_RATE * 0.5)
    fade_in = np.linspace(0, 1, fade_len)
    fade_out = np.linspace(1, 0, fade_len)
    sig[:fade_len] *= fade_in
    sig[-fade_len:] *= fade_out

    sig = lowpass(sig, 800 + filter_offset)
    sig = simple_reverb(sig, decay=0.3, length_ms=150)
    return normalize(sig)


# ---------------------------------------------------------------------------
# StarCraft Theme — Digital military. Square waves, radio chirps, metallic FM.
# ---------------------------------------------------------------------------


def gen_starcraft_session_start(duration_s: float = 0.6, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Terran boot sequence, ascending digital sweep 1000->3000Hz."""
    t = make_time(duration_s)
    f_lo = 1000 * pitch_ratio
    f_hi = 3000 * pitch_ratio

    sig = sweep(f_lo, f_hi, t) * 0.5
    sig += square(f_lo * 0.5, t) * 0.2 * np.linspace(0.2, 0.8, len(t))
    sig += fm_synth(f_hi, f_lo * 0.3, 2.0, t) * 0.15 * np.linspace(0, 1, len(t))

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=50, sustain_level=0.8, release_ms=80)
    sig *= env
    sig = lowpass(sig, 6000 + filter_offset)
    sig = simple_reverb(sig, decay=0.1, length_ms=30)
    return normalize(sig)


def gen_starcraft_session_end(duration_s: float = 0.4, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Power-down descending square 2000->500Hz."""
    t = make_time(duration_s)
    f_hi = 2000 * pitch_ratio
    f_lo = 500 * pitch_ratio

    freq_env = np.linspace(f_hi, f_lo, len(t))
    phase = np.cumsum(freq_env / SAMPLE_RATE) * 2 * np.pi
    sig = np.sign(np.sin(phase)) * 0.5
    sig += sine(f_lo * 0.5, t) * 0.15 * np.linspace(1, 0.2, len(t))

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=40, sustain_level=0.7, release_ms=100)
    sig *= env
    sig = lowpass(sig, 4000 + filter_offset)
    sig = simple_reverb(sig, decay=0.08, length_ms=20)
    return normalize(sig)


def gen_starcraft_task_complete(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """SCV ready — two-note digital major 3rd at 1200Hz."""
    t = make_time(duration_s)
    f1 = 1200 * pitch_ratio
    f2 = f1 * (5 / 4)  # major 3rd

    half = len(t) // 2
    sig = np.zeros(len(t))
    sig[:half] += square(f1, t[:half]) * 0.5
    sig[:half] += sine(f1 * 2, t[:half]) * 0.1
    sig[half:] += square(f2, t[:len(t) - half]) * 0.5
    sig[half:] += sine(f2 * 2, t[:len(t) - half]) * 0.1

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=20, sustain_level=0.7, release_ms=40)
    sig *= env
    sig = lowpass(sig, 5000 + filter_offset)
    sig = simple_reverb(sig, decay=0.08, length_ms=20)
    return normalize(sig)


def gen_starcraft_prompt_ack(duration_s: float = 0.15, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Short radio click, square burst 2000Hz."""
    t = make_time(duration_s)
    freq = 2000 * pitch_ratio

    sig = square(freq, t) * 0.5
    sig += white_noise(len(t), seed=11) * 0.08

    env = adsr_envelope(len(t), attack_ms=1, decay_ms=10, sustain_level=0.3, release_ms=20)
    sig *= env
    sig = bandpass(sig, 1500 + filter_offset, 4000 + filter_offset)
    return normalize(sig)


def gen_starcraft_agent_deploy(duration_s: float = 0.25, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Rising digital whoosh 800->4000Hz with FM character."""
    t = make_time(duration_s)
    f_lo = 800 * pitch_ratio
    f_hi = 4000 * pitch_ratio

    sig = sweep(f_lo, f_hi, t) * 0.4
    sig += fm_synth(f_lo * 2, f_lo * 0.5, 3.0, t) * 0.3 * np.linspace(0.5, 1, len(t))
    sig += white_noise(len(t), seed=22) * 0.05 * np.linspace(0, 0.5, len(t))

    env = adsr_envelope(len(t), attack_ms=1, decay_ms=20, sustain_level=0.8, release_ms=40)
    sig *= env
    sig = highpass(sig, 600 + filter_offset)
    sig = simple_reverb(sig, decay=0.05, length_ms=15)
    return normalize(sig)


def gen_starcraft_agent_return(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Descending scanner sweep 3000->800Hz."""
    t = make_time(duration_s)
    f_hi = 3000 * pitch_ratio
    f_lo = 800 * pitch_ratio

    sig = sweep(f_hi, f_lo, t) * 0.4
    sig += square(f_hi * 0.5, t) * 0.15 * np.linspace(1, 0.2, len(t))
    sig += fm_synth(f_lo, f_lo * 0.3, 1.5, t) * 0.2 * np.exp(-t * 5)

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=30, sustain_level=0.6, release_ms=60)
    sig *= env
    sig = lowpass(sig, 5000 + filter_offset)
    sig = simple_reverb(sig, decay=0.08, length_ms=25)
    return normalize(sig)


def gen_starcraft_error(duration_s: float = 0.25, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Alarm buzz, alternating 400/500Hz square waves."""
    t = make_time(duration_s)
    f1 = 400 * pitch_ratio
    f2 = 500 * pitch_ratio

    # Alternate between two frequencies at ~10Hz rate
    alt = (np.sin(2 * np.pi * 10 * t) > 0).astype(float)
    sig = square(f1, t) * alt * 0.4
    sig += square(f2, t) * (1 - alt) * 0.4
    sig += white_noise(len(t), seed=33) * 0.05

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=15, sustain_level=0.8, release_ms=30)
    sig *= env
    sig = lowpass(sig, 3000 + filter_offset)
    return normalize(sig)


def gen_starcraft_notification(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Radar ping, sharp 3000Hz sine with metallic reverb."""
    t = make_time(duration_s)
    freq = 3000 * pitch_ratio

    sig = sine(freq, t) * 0.5 * np.exp(-t * 12)
    sig += fm_synth(freq * 0.5, freq * 0.1, 2.0, t) * 0.15 * np.exp(-t * 8)
    sig += sine(freq * 2, t) * 0.1 * np.exp(-t * 20)

    env = adsr_envelope(len(t), attack_ms=1, decay_ms=30, sustain_level=0.3, release_ms=60)
    sig *= env
    sig = highpass(sig, 1000 + filter_offset)
    sig = simple_reverb(sig, decay=0.15, length_ms=40)
    return normalize(sig)


def gen_starcraft_commit(duration_s: float = 0.5, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Checkpoint achieved, ascending 4-note digital arpeggio."""
    freqs = [1000, 1250, 1500, 2000]
    freqs = [f * pitch_ratio for f in freqs]
    note_dur = duration_s / len(freqs)

    parts = []
    for freq in freqs:
        t_note = make_time(note_dur)
        note = square(freq, t_note) * 0.4
        note += sine(freq * 2, t_note) * 0.15
        env = adsr_envelope(len(t_note), attack_ms=2, decay_ms=10, sustain_level=0.7, release_ms=20)
        parts.append(note * env)

    sig = np.concatenate(parts)
    sig = lowpass(sig, 5000 + filter_offset)
    sig = simple_reverb(sig, decay=0.08, length_ms=20)
    return normalize(sig)


def gen_starcraft_permission(duration_s: float = 0.2, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Alert klaxon, sharp 2500Hz with fast tremolo."""
    t = make_time(duration_s)
    freq = 2500 * pitch_ratio

    tremolo = 0.5 + 0.5 * square(15, t)
    sig = square(freq, t) * tremolo * 0.5
    sig += sine(freq * 0.5, t) * 0.1

    env = adsr_envelope(len(t), attack_ms=1, decay_ms=15, sustain_level=0.8, release_ms=25)
    sig *= env
    sig = lowpass(sig, 5000 + filter_offset)
    return normalize(sig)


def gen_starcraft_compact(duration_s: float = 0.2, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Data compression sound, fast downward FM sweep."""
    t = make_time(duration_s)
    carrier = 3000 * pitch_ratio

    sig = fm_synth(carrier, 800, 5.0 * np.linspace(1, 0, len(t)), t) * 0.5
    sig += sweep(carrier, 800 * pitch_ratio, t) * 0.2

    env = adsr_envelope(len(t), attack_ms=1, decay_ms=20, sustain_level=0.6, release_ms=30)
    sig *= env
    sig = lowpass(sig, 5000 + filter_offset)
    return normalize(sig)


def gen_starcraft_ambient(duration_s: float = 3.0, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Low digital hum, 200Hz square + 100Hz sub."""
    t = make_time(duration_s)
    f1 = 200 * pitch_ratio
    f2 = 100 * pitch_ratio

    sig = square(f1, t) * 0.15
    sig += sine(f2, t) * 0.2
    sig += white_noise(len(t), seed=44) * 0.03

    lfo = 0.8 + 0.2 * sine(0.3, t)
    sig *= lfo

    fade_len = int(SAMPLE_RATE * 0.5)
    sig[:fade_len] *= np.linspace(0, 1, fade_len)
    sig[-fade_len:] *= np.linspace(1, 0, fade_len)

    sig = lowpass(sig, 600 + filter_offset)
    sig = simple_reverb(sig, decay=0.1, length_ms=40)
    return normalize(sig)


# ---------------------------------------------------------------------------
# Warcraft Theme — Fantasy organic. War drums, horn brass, wooden impacts.
# ---------------------------------------------------------------------------


def gen_warcraft_session_start(duration_s: float = 0.6, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """War horn call, rising triangle 200->600Hz with long reverb."""
    t = make_time(duration_s)
    f_lo = 200 * pitch_ratio
    f_hi = 600 * pitch_ratio

    sig = sweep(f_lo, f_hi, t) * 0.3
    # Triangle horn character
    freq_env = np.linspace(f_lo, f_hi, len(t))
    phase = np.cumsum(freq_env / SAMPLE_RATE) * 2 * np.pi
    sig += sp_signal.sawtooth(phase, width=0.5) * 0.4
    sig += sine(f_lo * 0.5, t) * 0.15

    env = adsr_envelope(len(t), attack_ms=40, decay_ms=80, sustain_level=0.7, release_ms=200)
    sig *= env
    sig = lowpass(sig, 1500 + filter_offset)
    sig = simple_reverb(sig, decay=0.4, length_ms=250)
    return normalize(sig)


def gen_warcraft_session_end(duration_s: float = 0.4, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Fading embers, descending sine 400->100Hz with heavy reverb."""
    t = make_time(duration_s)
    f_hi = 400 * pitch_ratio
    f_lo = 100 * pitch_ratio

    sig = sweep(f_hi, f_lo, t) * 0.4
    sig += sine(f_lo, t) * 0.3 * np.exp(-t * 3)
    sig += triangle(f_hi * 0.5, t) * 0.1 * np.exp(-t * 5)

    env = adsr_envelope(len(t), attack_ms=15, decay_ms=60, sustain_level=0.5, release_ms=200)
    sig *= env
    sig = lowpass(sig, 800 + filter_offset)
    sig = simple_reverb(sig, decay=0.45, length_ms=300)
    return normalize(sig)


def gen_warcraft_task_complete(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Anvil strike, bright impact + metallic ring 800Hz."""
    t = make_time(duration_s)
    freq = 800 * pitch_ratio

    # Impact transient
    impact = white_noise(len(t), seed=55) * 0.3 * np.exp(-t * 40)
    # Metallic ring
    ring = sine(freq, t) * 0.4 * np.exp(-t * 6)
    ring += sine(freq * 2.4, t) * 0.15 * np.exp(-t * 8)
    ring += sine(freq * 3.7, t) * 0.08 * np.exp(-t * 12)

    sig = impact + ring
    env = adsr_envelope(len(t), attack_ms=2, decay_ms=40, sustain_level=0.4, release_ms=100)
    sig *= env
    sig = lowpass(sig, 2000 + filter_offset)
    sig = simple_reverb(sig, decay=0.35, length_ms=200)
    return normalize(sig)


def gen_warcraft_prompt_ack(duration_s: float = 0.15, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Wooden click/tap at 300Hz."""
    t = make_time(duration_s)
    freq = 300 * pitch_ratio

    sig = sine(freq, t) * 0.4 * np.exp(-t * 30)
    sig += white_noise(len(t), seed=66) * 0.15 * np.exp(-t * 50)
    sig += sine(freq * 2.3, t) * 0.1 * np.exp(-t * 40)

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=15, sustain_level=0.2, release_ms=30)
    sig *= env
    sig = bandpass(sig, 200 + filter_offset, 800 + filter_offset)
    sig = simple_reverb(sig, decay=0.2, length_ms=80)
    return normalize(sig)


def gen_warcraft_agent_deploy(duration_s: float = 0.25, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """March drum, low sine 120Hz impact + triangle horn accent."""
    t = make_time(duration_s)
    f_drum = 120 * pitch_ratio
    f_horn = 400 * pitch_ratio

    # Drum impact
    sig = sine(f_drum, t) * 0.5 * np.exp(-t * 10)
    sig += white_noise(len(t), seed=77) * 0.15 * np.exp(-t * 30)
    # Horn accent in second half
    half = len(t) // 2
    horn_t = t[:len(t) - half]
    horn = triangle(f_horn, horn_t) * 0.3
    horn_env = adsr_envelope(len(horn_t), attack_ms=20, decay_ms=30, sustain_level=0.5, release_ms=40)
    horn *= horn_env
    sig[half:] += horn

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=40, sustain_level=0.5, release_ms=60)
    sig *= env
    sig = lowpass(sig, 1200 + filter_offset)
    sig = simple_reverb(sig, decay=0.3, length_ms=150)
    return normalize(sig)


def gen_warcraft_agent_return(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Shield bash, mid impact 400Hz with long decay."""
    t = make_time(duration_s)
    freq = 400 * pitch_ratio

    sig = sine(freq, t) * 0.4 * np.exp(-t * 5)
    sig += white_noise(len(t), seed=88) * 0.2 * np.exp(-t * 25)
    sig += sine(freq * 1.5, t) * 0.15 * np.exp(-t * 8)
    sig += sine(freq * 0.5, t) * 0.2 * np.exp(-t * 3)

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=50, sustain_level=0.4, release_ms=120)
    sig *= env
    sig = lowpass(sig, 1500 + filter_offset)
    sig = simple_reverb(sig, decay=0.35, length_ms=200)
    return normalize(sig)


def gen_warcraft_error(duration_s: float = 0.25, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Dark rumble, dissonant low sines 100/107Hz."""
    t = make_time(duration_s)
    f1 = 100 * pitch_ratio
    f2 = 107 * pitch_ratio

    sig = sine(f1, t) * 0.5
    sig += sine(f2, t) * 0.4
    sig += sine(f1 * 3, t) * 0.08
    sig += white_noise(len(t), seed=99) * 0.05

    env = adsr_envelope(len(t), attack_ms=5, decay_ms=40, sustain_level=0.7, release_ms=60)
    sig *= env
    sig = lowpass(sig, 600 + filter_offset)
    sig = simple_reverb(sig, decay=0.3, length_ms=150)
    return normalize(sig)


def gen_warcraft_notification(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Bell tower, triangle 600Hz with very long reverb."""
    t = make_time(duration_s)
    freq = 600 * pitch_ratio

    sig = triangle(freq, t) * 0.5 * np.exp(-t * 4)
    sig += sine(freq * 2, t) * 0.2 * np.exp(-t * 6)
    sig += sine(freq * 3, t) * 0.1 * np.exp(-t * 10)

    env = adsr_envelope(len(t), attack_ms=3, decay_ms=40, sustain_level=0.4, release_ms=120)
    sig *= env
    sig = lowpass(sig, 2000 + filter_offset)
    sig = simple_reverb(sig, decay=0.5, length_ms=350)
    return normalize(sig)


def gen_warcraft_commit(duration_s: float = 0.5, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Forge complete, rising brass arpeggio 200->400->600->800Hz."""
    freqs = [200, 400, 600, 800]
    freqs = [f * pitch_ratio for f in freqs]
    note_dur = duration_s / len(freqs)

    parts = []
    for freq in freqs:
        t_note = make_time(note_dur)
        note = triangle(freq, t_note) * 0.4
        note += sine(freq * 0.5, t_note) * 0.2
        note += sine(freq * 1.5, t_note) * 0.1
        env = adsr_envelope(len(t_note), attack_ms=15, decay_ms=20, sustain_level=0.6, release_ms=30)
        parts.append(note * env)

    sig = np.concatenate(parts)
    sig = lowpass(sig, 2000 + filter_offset)
    sig = simple_reverb(sig, decay=0.4, length_ms=250)
    return normalize(sig)


def gen_warcraft_permission(duration_s: float = 0.2, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Battle horn, sharp triangle 500Hz blast."""
    t = make_time(duration_s)
    freq = 500 * pitch_ratio

    sig = triangle(freq, t) * 0.6
    sig += sine(freq * 2, t) * 0.15
    sig += sine(freq * 0.5, t) * 0.2

    env = adsr_envelope(len(t), attack_ms=5, decay_ms=20, sustain_level=0.7, release_ms=40)
    sig *= env
    sig = lowpass(sig, 1500 + filter_offset)
    sig = simple_reverb(sig, decay=0.3, length_ms=150)
    return normalize(sig)


def gen_warcraft_compact(duration_s: float = 0.2, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Scroll roll, noise burst with bandpass 200-800Hz."""
    t = make_time(duration_s)
    base_freq = 500 * pitch_ratio

    sig = white_noise(len(t), seed=111) * 0.5
    sig += sine(base_freq, t) * 0.1  # tonal anchor
    sig *= np.linspace(1, 0.3, len(t))

    env = adsr_envelope(len(t), attack_ms=3, decay_ms=30, sustain_level=0.5, release_ms=40)
    sig *= env
    sig = bandpass(sig, 200 + filter_offset, 800 + filter_offset)
    sig = simple_reverb(sig, decay=0.25, length_ms=120)
    return normalize(sig)


def gen_warcraft_ambient(duration_s: float = 3.0, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Cavern wind, filtered noise 100-400Hz with slow LFO."""
    t = make_time(duration_s)
    f_lo = 100 * pitch_ratio
    f_hi = 400 * pitch_ratio

    sig = white_noise(len(t), seed=122) * 0.3
    sig += sine(f_lo, t) * 0.15
    sig += sine(f_lo * 1.5, t) * 0.08

    lfo = 0.6 + 0.4 * sine(0.25, t)
    sig *= lfo

    fade_len = int(SAMPLE_RATE * 0.5)
    sig[:fade_len] *= np.linspace(0, 1, fade_len)
    sig[-fade_len:] *= np.linspace(1, 0, fade_len)

    sig = bandpass(sig, f_lo + filter_offset, f_hi + filter_offset)
    sig = simple_reverb(sig, decay=0.5, length_ms=350)
    return normalize(sig)


# ---------------------------------------------------------------------------
# Mario Theme — Cheerful chiptune. Square waves, triangle bells, bouncy.
# ---------------------------------------------------------------------------


def gen_mario_session_start(duration_s: float = 0.6, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Level start jingle, ascending square 400->800->1200->1600Hz."""
    freqs = [400, 800, 1200, 1600]
    freqs = [f * pitch_ratio for f in freqs]
    note_dur = duration_s / len(freqs)

    parts = []
    for freq in freqs:
        t_note = make_time(note_dur)
        note = square(freq, t_note) * 0.45
        note += triangle(freq * 2, t_note) * 0.1
        env = adsr_envelope(len(t_note), attack_ms=3, decay_ms=10, sustain_level=0.6, release_ms=15)
        parts.append(note * env)

    sig = np.concatenate(parts)
    sig = lowpass(sig, 4000 + filter_offset)
    sig = simple_reverb(sig, decay=0.05, length_ms=20)
    return normalize(sig)


def gen_mario_session_end(duration_s: float = 0.4, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Pipe travel, descending square sweep 800->200Hz."""
    t = make_time(duration_s)
    f_hi = 800 * pitch_ratio
    f_lo = 200 * pitch_ratio

    freq_env = np.linspace(f_hi, f_lo, len(t))
    phase = np.cumsum(freq_env / SAMPLE_RATE) * 2 * np.pi
    sig = np.sign(np.sin(phase)) * 0.5
    sig += sine(f_lo, t) * 0.15

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=30, sustain_level=0.6, release_ms=40)
    sig *= env
    sig = lowpass(sig, 2000 + filter_offset)
    sig = simple_reverb(sig, decay=0.05, length_ms=15)
    return normalize(sig)


def gen_mario_task_complete(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Coin collect, two bright square pings 988/1319Hz (B5/E6)."""
    t = make_time(duration_s)
    f1 = 988 * pitch_ratio   # B5
    f2 = 1319 * pitch_ratio  # E6

    half = len(t) // 2
    sig = np.zeros(len(t))
    sig[:half] += square(f1, t[:half]) * 0.45
    sig[:half] += sine(f1 * 2, t[:half]) * 0.08
    sig[half:] += square(f2, t[:len(t) - half]) * 0.45
    sig[half:] += sine(f2 * 2, t[:len(t) - half]) * 0.08

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=15, sustain_level=0.5, release_ms=25)
    sig *= env
    sig = lowpass(sig, 4000 + filter_offset)
    sig = simple_reverb(sig, decay=0.04, length_ms=15)
    return normalize(sig)


def gen_mario_prompt_ack(duration_s: float = 0.15, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Small hop, short square 600Hz pip."""
    t = make_time(duration_s)
    freq = 600 * pitch_ratio

    sig = square(freq, t) * 0.4
    sig += sine(freq * 2, t) * 0.08 * np.exp(-t * 40)

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=10, sustain_level=0.3, release_ms=20)
    sig *= env
    sig = lowpass(sig, 2500 + filter_offset)
    return normalize(sig)


def gen_mario_agent_deploy(duration_s: float = 0.25, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Power-up ascending sweep 400->2000Hz."""
    t = make_time(duration_s)
    f_lo = 400 * pitch_ratio
    f_hi = 2000 * pitch_ratio

    freq_env = np.linspace(f_lo, f_hi, len(t))
    phase = np.cumsum(freq_env / SAMPLE_RATE) * 2 * np.pi
    sig = np.sign(np.sin(phase)) * 0.45
    sig += triangle(f_lo, t) * 0.1 * np.linspace(1, 0, len(t))

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=15, sustain_level=0.7, release_ms=30)
    sig *= env
    sig = lowpass(sig, 4000 + filter_offset)
    sig = simple_reverb(sig, decay=0.04, length_ms=15)
    return normalize(sig)


def gen_mario_agent_return(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Star power end, descending chime 1500->600Hz."""
    t = make_time(duration_s)
    f_hi = 1500 * pitch_ratio
    f_lo = 600 * pitch_ratio

    freq_env = np.linspace(f_hi, f_lo, len(t))
    phase = np.cumsum(freq_env / SAMPLE_RATE) * 2 * np.pi
    sig = np.sign(np.sin(phase)) * 0.35
    sig += triangle(f_hi, t) * 0.2 * np.exp(-t * 5)

    env = adsr_envelope(len(t), attack_ms=3, decay_ms=25, sustain_level=0.5, release_ms=40)
    sig *= env
    sig = lowpass(sig, 3500 + filter_offset)
    sig = simple_reverb(sig, decay=0.05, length_ms=20)
    return normalize(sig)


def gen_mario_error(duration_s: float = 0.25, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Hit damage, descending square 500->200Hz with buzz."""
    t = make_time(duration_s)
    f_hi = 500 * pitch_ratio
    f_lo = 200 * pitch_ratio

    freq_env = np.linspace(f_hi, f_lo, len(t))
    phase = np.cumsum(freq_env / SAMPLE_RATE) * 2 * np.pi
    sig = np.sign(np.sin(phase)) * 0.4
    # Buzz overlay
    sig += square(f_lo * 0.5, t) * 0.15

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=20, sustain_level=0.6, release_ms=30)
    sig *= env
    sig = lowpass(sig, 2000 + filter_offset)
    return normalize(sig)


def gen_mario_notification(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """1-UP, ascending arpeggio 660/880/1047Hz (E5/A5/C6)."""
    freqs = [660, 880, 1047]
    freqs = [f * pitch_ratio for f in freqs]
    note_dur = duration_s / len(freqs)

    parts = []
    for freq in freqs:
        t_note = make_time(note_dur)
        note = square(freq, t_note) * 0.4
        note += sine(freq * 2, t_note) * 0.1
        env = adsr_envelope(len(t_note), attack_ms=2, decay_ms=10, sustain_level=0.5, release_ms=20)
        parts.append(note * env)

    sig = np.concatenate(parts)
    sig = lowpass(sig, 3500 + filter_offset)
    sig = simple_reverb(sig, decay=0.04, length_ms=15)
    return normalize(sig)


def gen_mario_commit(duration_s: float = 0.5, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Flagpole, descending victory arpeggio 1047->880->660->523Hz."""
    freqs = [1047, 880, 660, 523]
    freqs = [f * pitch_ratio for f in freqs]
    note_dur = duration_s / len(freqs)

    parts = []
    for freq in freqs:
        t_note = make_time(note_dur)
        note = square(freq, t_note) * 0.4
        note += triangle(freq * 0.5, t_note) * 0.15
        env = adsr_envelope(len(t_note), attack_ms=3, decay_ms=12, sustain_level=0.6, release_ms=20)
        parts.append(note * env)

    sig = np.concatenate(parts)
    sig = lowpass(sig, 3500 + filter_offset)
    sig = simple_reverb(sig, decay=0.06, length_ms=25)
    return normalize(sig)


def gen_mario_permission(duration_s: float = 0.2, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Question block, short bright square 1000Hz with bounce."""
    t = make_time(duration_s)
    freq = 1000 * pitch_ratio

    # Quick pitch bounce: up then down
    freq_env = freq * (1 + 0.3 * np.exp(-t * 20))
    phase = np.cumsum(freq_env / SAMPLE_RATE) * 2 * np.pi
    sig = np.sign(np.sin(phase)) * 0.45

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=15, sustain_level=0.5, release_ms=25)
    sig *= env
    sig = lowpass(sig, 3000 + filter_offset)
    return normalize(sig)


def gen_mario_compact(duration_s: float = 0.2, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Shrink, fast descending sweep 1200->400Hz."""
    t = make_time(duration_s)
    f_hi = 1200 * pitch_ratio
    f_lo = 400 * pitch_ratio

    freq_env = np.linspace(f_hi, f_lo, len(t))
    phase = np.cumsum(freq_env / SAMPLE_RATE) * 2 * np.pi
    sig = np.sign(np.sin(phase)) * 0.45

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=15, sustain_level=0.5, release_ms=25)
    sig *= env
    sig = lowpass(sig, 3000 + filter_offset)
    return normalize(sig)


def gen_mario_ambient(duration_s: float = 3.0, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Underground theme, square bass 200Hz + triangle 150Hz."""
    t = make_time(duration_s)
    f1 = 200 * pitch_ratio
    f2 = 150 * pitch_ratio

    sig = square(f1, t) * 0.2
    sig += triangle(f2, t) * 0.15
    sig += sine(f1 * 0.5, t) * 0.1

    # Staccato pulse at 4Hz
    pulse = (sine(4, t) > 0.3).astype(float) * 0.7 + 0.3
    sig *= pulse

    fade_len = int(SAMPLE_RATE * 0.5)
    sig[:fade_len] *= np.linspace(0, 1, fade_len)
    sig[-fade_len:] *= np.linspace(1, 0, fade_len)

    sig = lowpass(sig, 800 + filter_offset)
    sig = simple_reverb(sig, decay=0.05, length_ms=20)
    return normalize(sig)


# ---------------------------------------------------------------------------
# Zelda Theme — Mystical melodic. Harp, ocarina, FM bells, sparkle.
# ---------------------------------------------------------------------------


def gen_zelda_session_start(duration_s: float = 0.6, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Fairy fountain opening, ascending FM bells 800->1200->1600Hz with sparkle noise."""
    freqs = [800, 1200, 1600]
    freqs = [f * pitch_ratio for f in freqs]
    note_dur = duration_s / len(freqs)

    parts = []
    for freq in freqs:
        t_note = make_time(note_dur)
        note = fm_synth(freq, freq * 1.4, 2.5, t_note) * 0.4
        note += sine(freq * 2, t_note) * 0.1 * np.exp(-t_note * 8)
        # Sparkle noise
        sparkle = white_noise(len(t_note), seed=int(freq)) * 0.04 * np.exp(-t_note * 15)
        note += sparkle
        env = adsr_envelope(len(t_note), attack_ms=10, decay_ms=30, sustain_level=0.5, release_ms=50)
        parts.append(note * env)

    sig = np.concatenate(parts)
    sig = lowpass(sig, 5000 + filter_offset)
    sig = simple_reverb(sig, decay=0.35, length_ms=200)
    return normalize(sig)


def gen_zelda_session_end(duration_s: float = 0.4, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Ocarina farewell, descending sine melody 1200->800->600Hz."""
    freqs = [1200, 800, 600]
    freqs = [f * pitch_ratio for f in freqs]
    note_dur = duration_s / len(freqs)

    parts = []
    for freq in freqs:
        t_note = make_time(note_dur)
        # Ocarina = pure sine with slight vibrato
        vibrato = sine(5, t_note) * freq * 0.01
        note = sine(freq, t_note) * 0.5
        note += sine(freq + vibrato, t_note) * 0.1
        env = adsr_envelope(len(t_note), attack_ms=15, decay_ms=25, sustain_level=0.6, release_ms=40)
        parts.append(note * env)

    sig = np.concatenate(parts)
    sig = lowpass(sig, 3000 + filter_offset)
    sig = simple_reverb(sig, decay=0.35, length_ms=200)
    return normalize(sig)


def gen_zelda_task_complete(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Secret found, rising 4th interval 600->800Hz with shimmer."""
    t = make_time(duration_s)
    f1 = 600 * pitch_ratio
    f2 = 800 * pitch_ratio

    half = len(t) // 2
    sig = np.zeros(len(t))
    # Triangle harp for first note
    sig[:half] += triangle(f1, t[:half]) * 0.4
    sig[:half] += sine(f1 * 2, t[:half]) * 0.15 * np.exp(-t[:half] * 8)
    # FM bell shimmer for second
    t2 = t[:len(t) - half]
    sig[half:] += fm_synth(f2, f2 * 1.4, 2.0, t2) * 0.4
    sig[half:] += white_noise(len(t2), seed=133) * 0.03 * np.exp(-t2 * 12)

    env = adsr_envelope(len(t), attack_ms=8, decay_ms=30, sustain_level=0.5, release_ms=60)
    sig *= env
    sig = lowpass(sig, 4000 + filter_offset)
    sig = simple_reverb(sig, decay=0.3, length_ms=180)
    return normalize(sig)


def gen_zelda_prompt_ack(duration_s: float = 0.15, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Soft menu select, triangle 1000Hz quick ping."""
    t = make_time(duration_s)
    freq = 1000 * pitch_ratio

    sig = triangle(freq, t) * 0.4 * np.exp(-t * 20)
    sig += sine(freq * 2, t) * 0.08 * np.exp(-t * 30)

    env = adsr_envelope(len(t), attack_ms=3, decay_ms=12, sustain_level=0.3, release_ms=25)
    sig *= env
    sig = lowpass(sig, 3000 + filter_offset)
    sig = simple_reverb(sig, decay=0.15, length_ms=60)
    return normalize(sig)


def gen_zelda_agent_deploy(duration_s: float = 0.25, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Navi 'hey!', rising FM bell 1200->2000Hz."""
    t = make_time(duration_s)
    f_lo = 1200 * pitch_ratio
    f_hi = 2000 * pitch_ratio

    freq_env = np.linspace(f_lo, f_hi, len(t))
    sig = fm_synth(f_lo, f_lo * 0.7, 3.0, t) * 0.4
    # Rising shimmer
    phase = np.cumsum(freq_env / SAMPLE_RATE) * 2 * np.pi
    sig += np.sin(phase) * 0.25
    sig += white_noise(len(t), seed=144) * 0.03 * np.exp(-t * 10)

    env = adsr_envelope(len(t), attack_ms=5, decay_ms=20, sustain_level=0.6, release_ms=40)
    sig *= env
    sig = lowpass(sig, 5000 + filter_offset)
    sig = simple_reverb(sig, decay=0.25, length_ms=120)
    return normalize(sig)


def gen_zelda_agent_return(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Item get jingle, ascending triangle arpeggio."""
    freqs = [600, 800, 1000, 1200]
    freqs = [f * pitch_ratio for f in freqs]
    note_dur = duration_s / len(freqs)

    parts = []
    for freq in freqs:
        t_note = make_time(note_dur)
        note = triangle(freq, t_note) * 0.4
        note += fm_synth(freq * 2, freq, 1.5, t_note) * 0.1 * np.exp(-t_note * 10)
        env = adsr_envelope(len(t_note), attack_ms=5, decay_ms=15, sustain_level=0.5, release_ms=25)
        parts.append(note * env)

    sig = np.concatenate(parts)
    sig = lowpass(sig, 4000 + filter_offset)
    sig = simple_reverb(sig, decay=0.3, length_ms=160)
    return normalize(sig)


def gen_zelda_error(duration_s: float = 0.25, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Guardian alert, dissonant FM 700/741Hz."""
    t = make_time(duration_s)
    f1 = 700 * pitch_ratio
    f2 = 741 * pitch_ratio

    sig = fm_synth(f1, f1 * 0.5, 4.0, t) * 0.35
    sig += fm_synth(f2, f2 * 0.5, 4.0, t) * 0.35
    sig += white_noise(len(t), seed=155) * 0.05

    env = adsr_envelope(len(t), attack_ms=3, decay_ms=25, sustain_level=0.7, release_ms=40)
    sig *= env
    sig = lowpass(sig, 3000 + filter_offset)
    return normalize(sig)


def gen_zelda_notification(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Owl hoot, sine 600->500Hz with vibrato."""
    t = make_time(duration_s)
    f_hi = 600 * pitch_ratio
    f_lo = 500 * pitch_ratio

    freq_env = np.linspace(f_hi, f_lo, len(t))
    vibrato = sine(6, t) * 15
    phase = np.cumsum((freq_env + vibrato) / SAMPLE_RATE) * 2 * np.pi
    sig = np.sin(phase) * 0.5
    sig += sine(f_lo * 0.5, t) * 0.15

    env = adsr_envelope(len(t), attack_ms=20, decay_ms=40, sustain_level=0.5, release_ms=80)
    sig *= env
    sig = lowpass(sig, 2000 + filter_offset)
    sig = simple_reverb(sig, decay=0.3, length_ms=150)
    return normalize(sig)


def gen_zelda_commit(duration_s: float = 0.5, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Chest open, dramatic ascending arpeggio + sparkle."""
    # Pentatonic: D E G A B
    freqs = [587, 659, 784, 880, 988]
    freqs = [f * pitch_ratio for f in freqs]
    note_dur = duration_s / len(freqs)

    parts = []
    for freq in freqs:
        t_note = make_time(note_dur)
        note = triangle(freq, t_note) * 0.35
        note += fm_synth(freq * 2, freq, 2.0, t_note) * 0.15
        sparkle = white_noise(len(t_note), seed=int(freq) % 256) * 0.03 * np.exp(-t_note * 12)
        note += sparkle
        env = adsr_envelope(len(t_note), attack_ms=8, decay_ms=15, sustain_level=0.5, release_ms=25)
        parts.append(note * env)

    sig = np.concatenate(parts)
    sig = lowpass(sig, 4500 + filter_offset)
    sig = simple_reverb(sig, decay=0.35, length_ms=200)
    return normalize(sig)


def gen_zelda_permission(duration_s: float = 0.2, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Fairy sparkle, high FM bell 2000Hz with noise."""
    t = make_time(duration_s)
    freq = 2000 * pitch_ratio

    sig = fm_synth(freq, freq * 1.4, 2.5, t) * 0.4 * np.exp(-t * 8)
    sig += white_noise(len(t), seed=166) * 0.06 * np.exp(-t * 15)
    sig += sine(freq * 0.5, t) * 0.1 * np.exp(-t * 10)

    env = adsr_envelope(len(t), attack_ms=3, decay_ms=20, sustain_level=0.4, release_ms=35)
    sig *= env
    sig = highpass(sig, 800 + filter_offset)
    sig = simple_reverb(sig, decay=0.3, length_ms=150)
    return normalize(sig)


def gen_zelda_compact(duration_s: float = 0.2, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Time shift, reverse-ish sweep 1500->800Hz."""
    t = make_time(duration_s)
    f_hi = 1500 * pitch_ratio
    f_lo = 800 * pitch_ratio

    sig = sweep(f_hi, f_lo, t) * 0.3
    sig += fm_synth(f_hi, f_lo * 0.5, 3.0, t) * 0.25 * np.linspace(1, 0.3, len(t))

    # Reverse-style envelope: fade in fast, sustain, quick drop
    env = adsr_envelope(len(t), attack_ms=40, decay_ms=20, sustain_level=0.7, release_ms=15)
    sig *= env
    sig = lowpass(sig, 4000 + filter_offset)
    sig = simple_reverb(sig, decay=0.25, length_ms=120)
    return normalize(sig)


def gen_zelda_ambient(duration_s: float = 3.0, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Lost woods vibe, pentatonic sine layers 400/600/800Hz."""
    t = make_time(duration_s)
    f1 = 400 * pitch_ratio
    f2 = 600 * pitch_ratio
    f3 = 800 * pitch_ratio

    sig = sine(f1, t) * 0.2
    sig += sine(f2, t) * 0.15
    sig += sine(f3, t) * 0.1

    # Gentle beating between layers
    lfo1 = 0.7 + 0.3 * sine(0.4, t)
    lfo2 = 0.7 + 0.3 * sine(0.6, t + 0.5)
    sig *= (lfo1 + lfo2) * 0.5

    fade_len = int(SAMPLE_RATE * 0.5)
    sig[:fade_len] *= np.linspace(0, 1, fade_len)
    sig[-fade_len:] *= np.linspace(1, 0, fade_len)

    sig = lowpass(sig, 1500 + filter_offset)
    sig = simple_reverb(sig, decay=0.4, length_ms=250)
    return normalize(sig)


# ---------------------------------------------------------------------------
# Smash Bros Theme — Competitive punchy. Impact hits, crowd noise, arena energy.
# ---------------------------------------------------------------------------


def gen_smash_session_start(duration_s: float = 0.6, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Character select, ascending sweep + crowd noise burst."""
    t = make_time(duration_s)
    f_lo = 500 * pitch_ratio
    f_hi = 2500 * pitch_ratio

    sig = sweep(f_lo, f_hi, t) * 0.35
    sig += sine(f_lo, t) * 0.2 * np.linspace(1, 0.3, len(t))
    # Crowd burst
    crowd = white_noise(len(t), seed=177) * 0.15
    crowd_env = np.exp(-t * 3) * np.linspace(0, 1, len(t))
    sig += crowd * crowd_env
    sig += fm_synth(f_hi * 0.5, 200, 2.0, t) * 0.1 * np.linspace(0, 1, len(t))

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=50, sustain_level=0.7, release_ms=80)
    sig *= env
    sig = lowpass(sig, 5000 + filter_offset)
    sig = simple_reverb(sig, decay=0.15, length_ms=60)
    return normalize(sig)


def gen_smash_session_end(duration_s: float = 0.4, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Game set, descending sweep + crowd fade."""
    t = make_time(duration_s)
    f_hi = 2000 * pitch_ratio
    f_lo = 500 * pitch_ratio

    sig = sweep(f_hi, f_lo, t) * 0.35
    sig += sine(f_lo, t) * 0.2 * np.exp(-t * 4)
    crowd = white_noise(len(t), seed=188) * 0.12 * np.linspace(1, 0, len(t))
    sig += crowd

    env = adsr_envelope(len(t), attack_ms=3, decay_ms=40, sustain_level=0.5, release_ms=100)
    sig *= env
    sig = lowpass(sig, 4000 + filter_offset)
    sig = simple_reverb(sig, decay=0.15, length_ms=60)
    return normalize(sig)


def gen_smash_task_complete(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """KO hit, sharp impact (noise transient + sine 800Hz)."""
    t = make_time(duration_s)
    freq = 800 * pitch_ratio

    # Hard transient
    impact = white_noise(len(t), seed=199) * 0.4 * np.exp(-t * 40)
    body = sine(freq, t) * 0.4 * np.exp(-t * 6)
    body += sine(freq * 0.5, t) * 0.2 * np.exp(-t * 4)
    # FM crack
    crack = fm_synth(freq * 2, freq, 5.0, t) * 0.15 * np.exp(-t * 25)

    sig = impact + body + crack
    env = adsr_envelope(len(t), attack_ms=1, decay_ms=30, sustain_level=0.4, release_ms=60)
    sig *= env
    sig = lowpass(sig, 4000 + filter_offset)
    sig = simple_reverb(sig, decay=0.12, length_ms=50)
    return normalize(sig)


def gen_smash_prompt_ack(duration_s: float = 0.15, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Menu tick, short click 1200Hz."""
    t = make_time(duration_s)
    freq = 1200 * pitch_ratio

    sig = sine(freq, t) * 0.4 * np.exp(-t * 30)
    sig += white_noise(len(t), seed=200) * 0.08 * np.exp(-t * 50)

    env = adsr_envelope(len(t), attack_ms=1, decay_ms=10, sustain_level=0.2, release_ms=20)
    sig *= env
    sig = lowpass(sig, 4000 + filter_offset)
    return normalize(sig)


def gen_smash_agent_deploy(duration_s: float = 0.25, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Launch star, rising whoosh with noise."""
    t = make_time(duration_s)
    f_lo = 600 * pitch_ratio
    f_hi = 3000 * pitch_ratio

    sig = sweep(f_lo, f_hi, t) * 0.35
    sig += white_noise(len(t), seed=211) * 0.15 * np.linspace(0.2, 0.8, len(t))
    sig += sine(f_lo, t) * 0.15 * np.linspace(1, 0, len(t))

    env = adsr_envelope(len(t), attack_ms=1, decay_ms=20, sustain_level=0.8, release_ms=35)
    sig *= env
    sig = highpass(sig, 400 + filter_offset)
    sig = simple_reverb(sig, decay=0.1, length_ms=35)
    return normalize(sig)


def gen_smash_agent_return(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Landing impact, low thud 200Hz + mid crack."""
    t = make_time(duration_s)
    f_low = 200 * pitch_ratio
    f_mid = 600 * pitch_ratio

    sig = sine(f_low, t) * 0.5 * np.exp(-t * 8)
    sig += white_noise(len(t), seed=222) * 0.2 * np.exp(-t * 30)
    sig += sine(f_mid, t) * 0.2 * np.exp(-t * 15)

    env = adsr_envelope(len(t), attack_ms=1, decay_ms=40, sustain_level=0.3, release_ms=70)
    sig *= env
    sig = lowpass(sig, 3000 + filter_offset)
    sig = simple_reverb(sig, decay=0.12, length_ms=50)
    return normalize(sig)


def gen_smash_error(duration_s: float = 0.25, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Shield break, dissonant shatter noise + low 300Hz."""
    t = make_time(duration_s)
    freq = 300 * pitch_ratio

    sig = white_noise(len(t), seed=233) * 0.3 * np.exp(-t * 15)
    sig += sine(freq, t) * 0.3
    sig += sine(freq * 1.07, t) * 0.2  # dissonance
    sig += fm_synth(freq * 3, freq, 6.0, t) * 0.1 * np.exp(-t * 20)

    env = adsr_envelope(len(t), attack_ms=1, decay_ms=25, sustain_level=0.5, release_ms=40)
    sig *= env
    sig = lowpass(sig, 3000 + filter_offset)
    return normalize(sig)


def gen_smash_notification(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Announcer ping, bright 1500Hz with presence."""
    t = make_time(duration_s)
    freq = 1500 * pitch_ratio

    sig = sine(freq, t) * 0.5 * np.exp(-t * 8)
    sig += sine(freq * 2, t) * 0.2 * np.exp(-t * 12)
    sig += white_noise(len(t), seed=244) * 0.05 * np.exp(-t * 20)

    env = adsr_envelope(len(t), attack_ms=1, decay_ms=30, sustain_level=0.4, release_ms=60)
    sig *= env
    sig = lowpass(sig, 5000 + filter_offset)
    sig = simple_reverb(sig, decay=0.12, length_ms=50)
    return normalize(sig)


def gen_smash_commit(duration_s: float = 0.5, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Victory, ascending fanfare arpeggio with crowd."""
    freqs = [523, 659, 784, 1047]
    freqs = [f * pitch_ratio for f in freqs]
    note_dur = duration_s / len(freqs)

    parts = []
    for i, freq in enumerate(freqs):
        t_note = make_time(note_dur)
        note = sine(freq, t_note) * 0.4
        note += sine(freq * 2, t_note) * 0.15
        # Increasing crowd
        crowd = white_noise(len(t_note), seed=255 + i) * 0.05 * (i + 1) / len(freqs)
        note += crowd
        env = adsr_envelope(len(t_note), attack_ms=2, decay_ms=15, sustain_level=0.6, release_ms=25)
        parts.append(note * env)

    sig = np.concatenate(parts)
    sig = lowpass(sig, 5000 + filter_offset)
    sig = simple_reverb(sig, decay=0.15, length_ms=60)
    return normalize(sig)


def gen_smash_permission(duration_s: float = 0.2, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Final smash alert, dramatic 2000Hz with tremolo."""
    t = make_time(duration_s)
    freq = 2000 * pitch_ratio

    tremolo = 0.5 + 0.5 * sine(12, t)
    sig = sine(freq, t) * tremolo * 0.45
    sig += fm_synth(freq * 0.5, 200, 3.0, t) * 0.15

    env = adsr_envelope(len(t), attack_ms=1, decay_ms=15, sustain_level=0.7, release_ms=25)
    sig *= env
    sig = lowpass(sig, 5000 + filter_offset)
    return normalize(sig)


def gen_smash_compact(duration_s: float = 0.2, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Dodge roll, quick whoosh noise sweep."""
    t = make_time(duration_s)
    _ = pitch_ratio  # noise-based, pitch applied via filter

    sig = white_noise(len(t), seed=266) * 0.35
    # Swept filter effect via modulated bandpass center
    sig_lp = lowpass(sig, 3000 + filter_offset)
    sig_hp = highpass(sig_lp, 500 + filter_offset)
    sig = sig_hp * np.concatenate([np.linspace(0.3, 1, len(t) // 2), np.linspace(1, 0.3, len(t) - len(t) // 2)])

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=20, sustain_level=0.6, release_ms=25)
    sig *= env
    return normalize(sig)


def gen_smash_ambient(duration_s: float = 3.0, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Stadium ambience, filtered crowd noise."""
    t = make_time(duration_s)

    crowd = white_noise(len(t), seed=277) * 0.25
    # Sub rumble
    crowd += sine(80 * pitch_ratio, t) * 0.1
    crowd += sine(120 * pitch_ratio, t) * 0.06

    lfo = 0.7 + 0.3 * sine(0.3, t)
    crowd *= lfo

    fade_len = int(SAMPLE_RATE * 0.5)
    crowd[:fade_len] *= np.linspace(0, 1, fade_len)
    crowd[-fade_len:] *= np.linspace(1, 0, fade_len)

    crowd = bandpass(crowd, 200 + filter_offset, 2000 + filter_offset)
    crowd = simple_reverb(crowd, decay=0.15, length_ms=80)
    return normalize(crowd)


# ---------------------------------------------------------------------------
# Kingdom Hearts Theme — Orchestral emotional. Triangle strings, sine choir, FM piano.
# ---------------------------------------------------------------------------


def gen_kingdom_hearts_session_start(duration_s: float = 0.6, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Save point chime, ascending FM piano 247->311->370->494Hz with choir pad."""
    freqs = [247, 311, 370, 494]  # B3, D#4, F#4, B4
    freqs = [f * pitch_ratio for f in freqs]
    note_dur = duration_s / len(freqs)

    parts = []
    for freq in freqs:
        t_note = make_time(note_dur)
        # FM piano
        note = fm_synth(freq, freq * 1.5, 2.0, t_note) * 0.35
        # Choir pad (sine layer)
        note += sine(freq, t_note) * 0.2
        note += sine(freq * 2, t_note) * 0.08
        env = adsr_envelope(len(t_note), attack_ms=12, decay_ms=25, sustain_level=0.6, release_ms=40)
        parts.append(note * env)

    sig = np.concatenate(parts)
    sig = lowpass(sig, 3000 + filter_offset)
    sig = simple_reverb(sig, decay=0.4, length_ms=300)
    return normalize(sig)


def gen_kingdom_hearts_session_end(duration_s: float = 0.4, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Sanctuary melody fragment, descending sine 494->370->311->247Hz."""
    freqs = [494, 370, 311, 247]  # B4, F#4, D#4, B3
    freqs = [f * pitch_ratio for f in freqs]
    note_dur = duration_s / len(freqs)

    parts = []
    for freq in freqs:
        t_note = make_time(note_dur)
        note = sine(freq, t_note) * 0.4
        note += sine(freq * 2, t_note) * 0.1
        note += triangle(freq * 0.5, t_note) * 0.1
        env = adsr_envelope(len(t_note), attack_ms=15, decay_ms=20, sustain_level=0.5, release_ms=35)
        parts.append(note * env)

    sig = np.concatenate(parts)
    sig = lowpass(sig, 2500 + filter_offset)
    sig = simple_reverb(sig, decay=0.45, length_ms=350)
    return normalize(sig)


def gen_kingdom_hearts_task_complete(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Keyblade strike, bright FM bell 800Hz with sparkle."""
    t = make_time(duration_s)
    freq = 800 * pitch_ratio

    sig = fm_synth(freq, freq * 1.4, 3.0, t) * 0.4 * np.exp(-t * 5)
    sig += sine(freq * 2, t) * 0.15 * np.exp(-t * 8)
    sig += white_noise(len(t), seed=288) * 0.04 * np.exp(-t * 15)
    sig += sine(freq * 3, t) * 0.08 * np.exp(-t * 12)

    env = adsr_envelope(len(t), attack_ms=2, decay_ms=35, sustain_level=0.4, release_ms=80)
    sig *= env
    sig = lowpass(sig, 4000 + filter_offset)
    sig = simple_reverb(sig, decay=0.35, length_ms=200)
    return normalize(sig)


def gen_kingdom_hearts_prompt_ack(duration_s: float = 0.15, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Menu navigate, soft sine 500Hz pip."""
    t = make_time(duration_s)
    freq = 500 * pitch_ratio

    sig = sine(freq, t) * 0.35 * np.exp(-t * 15)
    sig += sine(freq * 2, t) * 0.08 * np.exp(-t * 25)

    env = adsr_envelope(len(t), attack_ms=5, decay_ms=12, sustain_level=0.3, release_ms=25)
    sig *= env
    sig = lowpass(sig, 2000 + filter_offset)
    sig = simple_reverb(sig, decay=0.2, length_ms=80)
    return normalize(sig)


def gen_kingdom_hearts_agent_deploy(duration_s: float = 0.25, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Gummi ship launch, rising triangle sweep 300->1200Hz."""
    t = make_time(duration_s)
    f_lo = 300 * pitch_ratio
    f_hi = 1200 * pitch_ratio

    freq_env = np.linspace(f_lo, f_hi, len(t))
    phase = np.cumsum(freq_env / SAMPLE_RATE) * 2 * np.pi
    sig = sp_signal.sawtooth(phase, width=0.5) * 0.35  # triangle sweep
    sig += sine(f_lo, t) * 0.2 * np.linspace(1, 0, len(t))
    sig += fm_synth(f_hi, f_lo, 1.5, t) * 0.1 * np.linspace(0, 1, len(t))

    env = adsr_envelope(len(t), attack_ms=8, decay_ms=25, sustain_level=0.7, release_ms=40)
    sig *= env
    sig = lowpass(sig, 3500 + filter_offset)
    sig = simple_reverb(sig, decay=0.3, length_ms=150)
    return normalize(sig)


def gen_kingdom_hearts_agent_return(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """World warp arrival, descending shimmer."""
    t = make_time(duration_s)
    f_hi = 1200 * pitch_ratio
    f_lo = 400 * pitch_ratio

    freq_env = np.linspace(f_hi, f_lo, len(t))
    phase = np.cumsum(freq_env / SAMPLE_RATE) * 2 * np.pi
    sig = np.sin(phase) * 0.35
    sig += fm_synth(f_hi, f_lo * 0.5, 2.0, t) * 0.2 * np.exp(-t * 4)
    sig += white_noise(len(t), seed=299) * 0.03 * np.exp(-t * 10)

    env = adsr_envelope(len(t), attack_ms=5, decay_ms=40, sustain_level=0.5, release_ms=80)
    sig *= env
    sig = lowpass(sig, 3500 + filter_offset)
    sig = simple_reverb(sig, decay=0.4, length_ms=250)
    return normalize(sig)


def gen_kingdom_hearts_error(duration_s: float = 0.25, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Darkness pulse, low dissonant FM 200/213Hz."""
    t = make_time(duration_s)
    f1 = 200 * pitch_ratio
    f2 = 213 * pitch_ratio

    sig = fm_synth(f1, f1 * 0.5, 4.0, t) * 0.4
    sig += fm_synth(f2, f2 * 0.5, 4.0, t) * 0.35
    sig += sine(f1 * 0.5, t) * 0.1

    env = adsr_envelope(len(t), attack_ms=5, decay_ms=35, sustain_level=0.6, release_ms=50)
    sig *= env
    sig = lowpass(sig, 1000 + filter_offset)
    sig = simple_reverb(sig, decay=0.35, length_ms=200)
    return normalize(sig)


def gen_kingdom_hearts_notification(duration_s: float = 0.3, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Kairi's chime, gentle FM bell 600Hz with long decay."""
    t = make_time(duration_s)
    freq = 600 * pitch_ratio

    sig = fm_synth(freq, freq * 1.4, 2.0, t) * 0.4 * np.exp(-t * 4)
    sig += sine(freq * 2, t) * 0.15 * np.exp(-t * 6)
    sig += sine(freq * 0.5, t) * 0.1

    env = adsr_envelope(len(t), attack_ms=8, decay_ms=35, sustain_level=0.4, release_ms=100)
    sig *= env
    sig = lowpass(sig, 3000 + filter_offset)
    sig = simple_reverb(sig, decay=0.45, length_ms=300)
    return normalize(sig)


def gen_kingdom_hearts_commit(duration_s: float = 0.5, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Level up, ascending arpeggio 247->311->370->494->741Hz."""
    freqs = [247, 311, 370, 494, 741]  # B major ascending
    freqs = [f * pitch_ratio for f in freqs]
    note_dur = duration_s / len(freqs)

    parts = []
    for freq in freqs:
        t_note = make_time(note_dur)
        note = fm_synth(freq, freq * 1.5, 2.0, t_note) * 0.3
        note += sine(freq, t_note) * 0.2
        note += triangle(freq * 0.5, t_note) * 0.1
        env = adsr_envelope(len(t_note), attack_ms=10, decay_ms=15, sustain_level=0.55, release_ms=25)
        parts.append(note * env)

    sig = np.concatenate(parts)
    sig = lowpass(sig, 3500 + filter_offset)
    sig = simple_reverb(sig, decay=0.4, length_ms=300)
    return normalize(sig)


def gen_kingdom_hearts_permission(duration_s: float = 0.2, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Trinity mark, bright triangle 800Hz pulse."""
    t = make_time(duration_s)
    freq = 800 * pitch_ratio

    sig = triangle(freq, t) * 0.5
    sig += sine(freq * 2, t) * 0.15 * np.exp(-t * 10)
    sig += sine(freq * 0.5, t) * 0.1

    env = adsr_envelope(len(t), attack_ms=3, decay_ms=20, sustain_level=0.5, release_ms=35)
    sig *= env
    sig = lowpass(sig, 3000 + filter_offset)
    sig = simple_reverb(sig, decay=0.3, length_ms=150)
    return normalize(sig)


def gen_kingdom_hearts_compact(duration_s: float = 0.2, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Time compression, reverse sweep + FM wobble."""
    t = make_time(duration_s)
    f_hi = 1200 * pitch_ratio
    f_lo = 400 * pitch_ratio

    sig = sweep(f_hi, f_lo, t) * 0.3
    # FM wobble
    wobble_idx = 3.0 * np.linspace(1, 0.2, len(t))
    sig += fm_synth(f_lo, f_lo * 0.5, wobble_idx, t) * 0.25

    # Reverse-ish envelope
    env = adsr_envelope(len(t), attack_ms=35, decay_ms=20, sustain_level=0.6, release_ms=20)
    sig *= env
    sig = lowpass(sig, 3000 + filter_offset)
    sig = simple_reverb(sig, decay=0.3, length_ms=150)
    return normalize(sig)


def gen_kingdom_hearts_ambient(duration_s: float = 3.0, pitch_ratio: float = 1.0, filter_offset: float = 0) -> np.ndarray:
    """Dearly beloved pad, B major chord (247/311/370Hz) sine layers."""
    t = make_time(duration_s)
    f1 = 247 * pitch_ratio  # B3
    f2 = 311 * pitch_ratio  # D#4
    f3 = 370 * pitch_ratio  # F#4

    sig = sine(f1, t) * 0.25
    sig += sine(f2, t) * 0.2
    sig += sine(f3, t) * 0.15
    # Octave shimmer
    sig += sine(f1 * 2, t) * 0.05
    sig += sine(f2 * 2, t) * 0.04

    lfo = 0.8 + 0.2 * sine(0.35, t)
    sig *= lfo

    fade_len = int(SAMPLE_RATE * 0.5)
    sig[:fade_len] *= np.linspace(0, 1, fade_len)
    sig[-fade_len:] *= np.linspace(1, 0, fade_len)

    sig = lowpass(sig, 1200 + filter_offset)
    sig = simple_reverb(sig, decay=0.5, length_ms=400)
    return normalize(sig)


# ---------------------------------------------------------------------------
# Theme Registry
# ---------------------------------------------------------------------------

THEME_REGISTRY: dict[str, dict[str, tuple[Callable, float, int]]] = {
    "default": {
        "session-start": (gen_default_session_start, 0.6, 3),
        "session-end": (gen_default_session_end, 0.4, 3),
        "task-complete": (gen_default_task_complete, 0.3, 3),
        "prompt-ack": (gen_default_prompt_ack, 0.15, 3),
        "agent-deploy": (gen_default_agent_deploy, 0.25, 3),
        "agent-return": (gen_default_agent_return, 0.3, 3),
        "error": (gen_default_error, 0.25, 3),
        "notification": (gen_default_notification, 0.3, 3),
        "commit": (gen_default_commit, 0.5, 3),
        "permission": (gen_default_permission, 0.2, 3),
        "compact": (gen_default_compact, 0.2, 3),
        "ambient": (gen_default_ambient, 3.0, 1),
    },
    "starcraft": {
        "session-start": (gen_starcraft_session_start, 0.6, 3),
        "session-end": (gen_starcraft_session_end, 0.4, 3),
        "task-complete": (gen_starcraft_task_complete, 0.3, 3),
        "prompt-ack": (gen_starcraft_prompt_ack, 0.15, 3),
        "agent-deploy": (gen_starcraft_agent_deploy, 0.25, 3),
        "agent-return": (gen_starcraft_agent_return, 0.3, 3),
        "error": (gen_starcraft_error, 0.25, 3),
        "notification": (gen_starcraft_notification, 0.3, 3),
        "commit": (gen_starcraft_commit, 0.5, 3),
        "permission": (gen_starcraft_permission, 0.2, 3),
        "compact": (gen_starcraft_compact, 0.2, 3),
        "ambient": (gen_starcraft_ambient, 3.0, 1),
    },
    "warcraft": {
        "session-start": (gen_warcraft_session_start, 0.6, 3),
        "session-end": (gen_warcraft_session_end, 0.4, 3),
        "task-complete": (gen_warcraft_task_complete, 0.3, 3),
        "prompt-ack": (gen_warcraft_prompt_ack, 0.15, 3),
        "agent-deploy": (gen_warcraft_agent_deploy, 0.25, 3),
        "agent-return": (gen_warcraft_agent_return, 0.3, 3),
        "error": (gen_warcraft_error, 0.25, 3),
        "notification": (gen_warcraft_notification, 0.3, 3),
        "commit": (gen_warcraft_commit, 0.5, 3),
        "permission": (gen_warcraft_permission, 0.2, 3),
        "compact": (gen_warcraft_compact, 0.2, 3),
        "ambient": (gen_warcraft_ambient, 3.0, 1),
    },
    "mario": {
        "session-start": (gen_mario_session_start, 0.6, 3),
        "session-end": (gen_mario_session_end, 0.4, 3),
        "task-complete": (gen_mario_task_complete, 0.3, 3),
        "prompt-ack": (gen_mario_prompt_ack, 0.15, 3),
        "agent-deploy": (gen_mario_agent_deploy, 0.25, 3),
        "agent-return": (gen_mario_agent_return, 0.3, 3),
        "error": (gen_mario_error, 0.25, 3),
        "notification": (gen_mario_notification, 0.3, 3),
        "commit": (gen_mario_commit, 0.5, 3),
        "permission": (gen_mario_permission, 0.2, 3),
        "compact": (gen_mario_compact, 0.2, 3),
        "ambient": (gen_mario_ambient, 3.0, 1),
    },
    "zelda": {
        "session-start": (gen_zelda_session_start, 0.6, 3),
        "session-end": (gen_zelda_session_end, 0.4, 3),
        "task-complete": (gen_zelda_task_complete, 0.3, 3),
        "prompt-ack": (gen_zelda_prompt_ack, 0.15, 3),
        "agent-deploy": (gen_zelda_agent_deploy, 0.25, 3),
        "agent-return": (gen_zelda_agent_return, 0.3, 3),
        "error": (gen_zelda_error, 0.25, 3),
        "notification": (gen_zelda_notification, 0.3, 3),
        "commit": (gen_zelda_commit, 0.5, 3),
        "permission": (gen_zelda_permission, 0.2, 3),
        "compact": (gen_zelda_compact, 0.2, 3),
        "ambient": (gen_zelda_ambient, 3.0, 1),
    },
    "smash": {
        "session-start": (gen_smash_session_start, 0.6, 3),
        "session-end": (gen_smash_session_end, 0.4, 3),
        "task-complete": (gen_smash_task_complete, 0.3, 3),
        "prompt-ack": (gen_smash_prompt_ack, 0.15, 3),
        "agent-deploy": (gen_smash_agent_deploy, 0.25, 3),
        "agent-return": (gen_smash_agent_return, 0.3, 3),
        "error": (gen_smash_error, 0.25, 3),
        "notification": (gen_smash_notification, 0.3, 3),
        "commit": (gen_smash_commit, 0.5, 3),
        "permission": (gen_smash_permission, 0.2, 3),
        "compact": (gen_smash_compact, 0.2, 3),
        "ambient": (gen_smash_ambient, 3.0, 1),
    },
    "kingdom-hearts": {
        "session-start": (gen_kingdom_hearts_session_start, 0.6, 3),
        "session-end": (gen_kingdom_hearts_session_end, 0.4, 3),
        "task-complete": (gen_kingdom_hearts_task_complete, 0.3, 3),
        "prompt-ack": (gen_kingdom_hearts_prompt_ack, 0.15, 3),
        "agent-deploy": (gen_kingdom_hearts_agent_deploy, 0.25, 3),
        "agent-return": (gen_kingdom_hearts_agent_return, 0.3, 3),
        "error": (gen_kingdom_hearts_error, 0.25, 3),
        "notification": (gen_kingdom_hearts_notification, 0.3, 3),
        "commit": (gen_kingdom_hearts_commit, 0.5, 3),
        "permission": (gen_kingdom_hearts_permission, 0.2, 3),
        "compact": (gen_kingdom_hearts_compact, 0.2, 3),
        "ambient": (gen_kingdom_hearts_ambient, 3.0, 1),
    },
}

ALL_THEME_NAMES = sorted(THEME_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def generate_theme(theme_name: str, output_dir: Path) -> list[dict]:
    """Generate all sounds for a single theme, return metadata for summary."""
    recipes = THEME_REGISTRY[theme_name]
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for event_name, (gen_fn, base_dur, n_variants) in recipes.items():
        variant_files = []
        for v in range(n_variants):
            offsets = VARIANT_OFFSETS[v] if v < len(VARIANT_OFFSETS) else VARIANT_OFFSETS[0]
            pitch_ratio = cents_to_ratio(offsets["pitch_cents"])
            dur = base_dur * offsets["dur_scale"]
            filt = offsets["filter_hz"]

            sig = gen_fn(duration_s=dur, pitch_ratio=pitch_ratio, filter_offset=filt)
            stereo = to_stereo_16bit(sig)

            if n_variants == 1:
                filename = f"{event_name}-loop.wav"
            else:
                filename = f"{event_name}-{v + 1:02d}.wav"

            filepath = output_dir / filename
            wavfile.write(str(filepath), SAMPLE_RATE, stereo)
            file_size = filepath.stat().st_size
            variant_files.append((filename, file_size))

        results.append({
            "event": event_name,
            "variants": variant_files,
            "duration_ms": int(base_dur * 1000),
        })

    return results


def print_summary(theme_name: str, results: list[dict]) -> tuple[int, int]:
    """Print a formatted summary table."""
    total_files = 0
    total_bytes = 0

    print(f"\n  Theme: {theme_name}")
    print(f"  {'Event':<20} {'Variants':<8} {'Duration':<10} {'Files & Sizes'}")
    print("  " + "-" * 78)

    for r in results:
        sizes = ", ".join(f"{name} ({sz // 1024}KB)" for name, sz in r["variants"])
        n = len(r["variants"])
        total_files += n
        total_bytes += sum(sz for _, sz in r["variants"])
        print(f"  {r['event']:<20} {n:<8} {r['duration_ms']:<10}ms {sizes}")

    print("  " + "-" * 78)
    print(f"  Total: {total_files} files, {total_bytes // 1024}KB")
    return total_files, total_bytes


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate claude-voice earcon sounds")
    parser.add_argument("--theme", default="all", help="Theme name or 'all' (default: 'all')")
    parser.add_argument("--output-dir", type=Path, default=None, help="Override output directory (single theme only)")
    parser.add_argument("--list-themes", action="store_true", help="List available theme names and exit")
    args = parser.parse_args()

    if args.list_themes:
        print("Available themes:")
        for name in ALL_THEME_NAMES:
            print(f"  {name}")
        sys.exit(0)

    if args.theme == "all":
        themes_to_generate = ALL_THEME_NAMES
    elif args.theme in THEME_REGISTRY:
        themes_to_generate = [args.theme]
    else:
        print(f"Unknown theme: '{args.theme}'")
        print(f"Available: {', '.join(ALL_THEME_NAMES)}")
        sys.exit(1)

    if args.output_dir and len(themes_to_generate) > 1:
        print("--output-dir can only be used with a single --theme, not 'all'")
        sys.exit(1)

    grand_total_files = 0
    grand_total_bytes = 0

    for theme_name in themes_to_generate:
        if args.output_dir:
            output_dir = args.output_dir
        else:
            output_dir = PLUGIN_ROOT / DEFAULT_OUTPUT_TEMPLATE.format(theme=theme_name)

        print(f"\nGenerating sounds for theme '{theme_name}'")
        print(f"Output: {output_dir}")

        results = generate_theme(theme_name, output_dir)
        files, bytes_ = print_summary(theme_name, results)
        grand_total_files += files
        grand_total_bytes += bytes_

    if len(themes_to_generate) > 1:
        print(f"\n{'=' * 40}")
        print(f"Grand total: {len(themes_to_generate)} themes, {grand_total_files} files, {grand_total_bytes // 1024}KB")

    print("\nDone.")


if __name__ == "__main__":
    main()
