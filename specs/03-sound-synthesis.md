---
title: "Sound Synthesis — numpy+scipy Recipes, Asset Generation & Variant Pipeline"
spec: "03"
status: draft
created: 2026-03-26
author: matt
tags: [claude-voice, synthesis, numpy, scipy, sound-design, assets]
---

# 03 — Sound Synthesis

## 1. Overview

All claude-voice sound assets are synthesized programmatically using numpy and scipy. Zero licensing risk. Full control over every parameter. Every waveform is original, reproducible, and git-tracked.

The synthesis pipeline generates WAV files that live in `assets/themes/{theme}/sounds/`. The build script (`scripts/generate_sounds.py`) is a PEP 723 single-file script invoked via `uv run`. It produces all sounds for all themes in a single deterministic pass — same random seed, same output every time. Total asset footprint is under 5MB across all 7 themes (default + 6 game themes).

Why synthesis over downloads:
- **Legal certainty**: Every waveform is computed from first principles. No samples, no licenses, no attribution chains.
- **Full parameter control**: Every frequency, envelope, and filter is a named constant in the script. Tweaking a theme means changing numbers, not swapping files.
- **Reproducibility**: `generate_sounds.py` is a pure function from parameters to WAV files. Delete all assets and regenerate identically.
- **Variant generation**: Automated pitch/timing/filter transforms produce 3-7 variants per event from a single base recipe.
- **Size efficiency**: Synthesized earcons at 100-800ms duration are 10-150KB each as WAV. The entire asset set fits comfortably in a git repo.

---

## 2. Output Format

Every generated file must match the audio playback engine spec (spec 05):

| Property | Value | Rationale |
|----------|-------|-----------|
| Container | WAV (RIFF/WAVE) | Universal support, zero decode overhead |
| Encoding | Uncompressed PCM | No decoder startup cost, direct passthrough to PipeWire |
| Sample rate | 48000 Hz | Matches PipeWire native quantum — zero resampling |
| Bit depth | 16-bit signed integer (`s16le`) | Sufficient dynamic range for earcons, half the size of `f32le` |
| Channels | Stereo (2 channels) | Matches default sink spec (`float32le 2ch 48000Hz`) |
| Byte order | Little-endian | x86-64 native, no byte-swapping |
| LUFS target | -14 LUFS | Standard for notification audio normalization |

File writing uses `scipy.io.wavfile.write()` for stereo output:

```python
import numpy as np
from scipy.io import wavfile

SAMPLE_RATE = 48000
BIT_DEPTH = 16
MAX_AMP = 32767  # 2^15 - 1, peak for int16

def write_wav(path: str, signal_left: np.ndarray, signal_right: np.ndarray):
    """Write a stereo WAV file from two mono float arrays in [-1.0, 1.0]."""
    stereo = np.column_stack([
        (np.clip(signal_left, -1.0, 1.0) * MAX_AMP).astype(np.int16),
        (np.clip(signal_right, -1.0, 1.0) * MAX_AMP).astype(np.int16),
    ])
    wavfile.write(path, SAMPLE_RATE, stereo)
```

Alternatively, the `wave` stdlib module works without scipy at write time:

```python
import wave
import struct

def write_wav_stdlib(path: str, left: np.ndarray, right: np.ndarray):
    """Write stereo WAV using only stdlib wave module."""
    interleaved = np.empty(len(left) * 2, dtype=np.int16)
    interleaved[0::2] = (np.clip(left, -1.0, 1.0) * MAX_AMP).astype(np.int16)
    interleaved[1::2] = (np.clip(right, -1.0, 1.0) * MAX_AMP).astype(np.int16)
    with wave.open(str(path), 'w') as f:
        f.setnchannels(2)
        f.setsampwidth(2)  # 16-bit = 2 bytes
        f.setframerate(SAMPLE_RATE)
        f.writeframes(interleaved.tobytes())
```

---

## 3. Synthesis Primitives

These are the building blocks used across all theme recipes. Every sound in every theme is composed from combinations of these primitives.

### 3a. Sine Wave

The foundation. Pure tone with no harmonics. Used for chimes, bells, clean tones.

```python
def sine(freq: float, duration: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Pure sine wave oscillator. Returns float array in [-1, 1]."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    return np.sin(2 * np.pi * freq * t)
```

### 3b. Square Wave

Hard-clipped sine. Rich in odd harmonics (3rd, 5th, 7th...). The NES/chiptune character. Used heavily in Mario theme, digital beeps.

```python
def square(freq: float, duration: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Square wave via sign of sine. Odd harmonics only."""
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    return np.sign(np.sin(2 * np.pi * freq * t))
```

Band-limited variant (less aliasing, better for higher frequencies):

```python
def square_bl(freq: float, duration: float, harmonics: int = 15) -> np.ndarray:
    """Band-limited square wave via additive synthesis of odd harmonics."""
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    signal = np.zeros_like(t)
    for k in range(1, harmonics + 1, 2):
        if k * freq > SAMPLE_RATE / 2:
            break  # Nyquist limit
        signal += (1.0 / k) * np.sin(2 * np.pi * k * freq * t)
    return signal / np.max(np.abs(signal))  # normalize
```

### 3c. Sawtooth Wave

All harmonics (odd + even). Buzzy, aggressive, brass-like. Used for Warcraft horns, StarCraft servo motors.

```python
from scipy.signal import sawtooth as _sawtooth

def sawtooth(freq: float, duration: float) -> np.ndarray:
    """Sawtooth wave. All harmonics. Buzzy/brass character."""
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    return _sawtooth(2 * np.pi * freq * t)
```

### 3d. Triangle Wave

Odd harmonics like square, but with 1/k^2 rolloff. Softer, rounder. NES bass channel. Zelda undertones.

```python
from scipy.signal import sawtooth as _sawtooth

def triangle(freq: float, duration: float) -> np.ndarray:
    """Triangle wave. Odd harmonics with steep rolloff. Soft character."""
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    return _sawtooth(2 * np.pi * freq * t, width=0.5)
```

### 3e. Noise

White noise: uniform random samples. Used for radio static (StarCraft), impact transients, sword draws, explosions.

```python
def white_noise(duration: float) -> np.ndarray:
    """White noise. Uniform distribution in [-1, 1]."""
    return np.random.uniform(-1, 1, int(SAMPLE_RATE * duration))

def pink_noise(duration: float) -> np.ndarray:
    """Pink noise (1/f). Warmer than white. Good for ambient textures."""
    n = int(SAMPLE_RATE * duration)
    white = np.random.randn(n)
    # Voss-McCartney approximation
    pink = np.zeros(n)
    num_rows = 16
    rows = np.zeros(num_rows)
    running_sum = 0
    for i in range(n):
        index = 0
        val = i
        while val & 1 == 0 and index < num_rows:
            running_sum -= rows[index]
            rows[index] = np.random.randn()
            running_sum += rows[index]
            val >>= 1
            index += 1
        pink[i] = running_sum + white[i]
    return pink / np.max(np.abs(pink))
```

### 3f. ADSR Envelope

Attack/Decay/Sustain/Release amplitude shaping. Every sound passes through an envelope to avoid clicks and shape character.

```python
def adsr(length: int, attack: float, decay: float, sustain_level: float,
         release: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """
    Generate ADSR amplitude envelope.

    Args:
        length: total number of samples
        attack: attack time in seconds
        decay: decay time in seconds
        sustain_level: sustain amplitude (0.0 to 1.0)
        release: release time in seconds
        sample_rate: samples per second

    Returns:
        Amplitude envelope array of shape (length,) with values in [0, 1]
    """
    a_samples = int(attack * sample_rate)
    d_samples = int(decay * sample_rate)
    r_samples = int(release * sample_rate)
    s_samples = max(0, length - a_samples - d_samples - r_samples)

    attack_curve = np.linspace(0.0, 1.0, a_samples)
    decay_curve = np.linspace(1.0, sustain_level, d_samples)
    sustain_curve = np.full(s_samples, sustain_level)
    release_curve = np.linspace(sustain_level, 0.0, r_samples)

    envelope = np.concatenate([attack_curve, decay_curve, sustain_curve, release_curve])

    # Pad or truncate to exact length
    if len(envelope) < length:
        envelope = np.pad(envelope, (0, length - len(envelope)))
    return envelope[:length]
```

Simpler exponential decay variant (used more often for earcons):

```python
def exp_decay(duration: float, decay_rate: float = 3.0) -> np.ndarray:
    """Exponential decay envelope. decay_rate controls speed (higher = faster)."""
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    return np.exp(-decay_rate * t)
```

### 3g. Frequency Sweep (Chirp)

Linear or exponential frequency sweep. Used for sci-fi effects, power-ups, alerts.

```python
def sweep(f_start: float, f_end: float, duration: float,
          decay: float = 2.0, mode: str = 'linear') -> np.ndarray:
    """
    Frequency sweep from f_start to f_end Hz.

    mode: 'linear' for constant rate, 'exponential' for multiplicative
    decay: exponential amplitude decay rate
    """
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0, duration, n, endpoint=False)

    if mode == 'linear':
        freq = np.linspace(f_start, f_end, n)
    elif mode == 'exponential':
        freq = f_start * (f_end / f_start) ** (t / duration)
    else:
        raise ValueError(f"Unknown sweep mode: {mode}")

    phase = np.cumsum(2 * np.pi * freq / SAMPLE_RATE)
    env = np.exp(-decay * t)
    return np.sin(phase) * env
```

### 3h. Amplitude Modulation (Tremolo)

Multiplies the signal amplitude by a low-frequency oscillator. Creates pulsing, wavering effects.

```python
def tremolo(signal: np.ndarray, mod_freq: float = 5.0,
            depth: float = 0.5) -> np.ndarray:
    """
    Apply amplitude modulation (tremolo) to a signal.

    mod_freq: modulation frequency in Hz (typically 3-12 Hz)
    depth: modulation depth 0.0 (none) to 1.0 (full)
    """
    t = np.linspace(0, len(signal) / SAMPLE_RATE, len(signal), endpoint=False)
    modulator = 1.0 - depth + depth * np.sin(2 * np.pi * mod_freq * t)
    return signal * modulator
```

### 3i. Frequency Modulation (FM Synthesis)

Modulating the frequency of a carrier with another oscillator. Produces metallic, bell-like, or complex timbres. Essential for Kingdom Hearts crystalline chimes and Zelda fairy sparkles.

```python
def fm_synth(carrier_freq: float, mod_freq: float, mod_index: float,
             duration: float) -> np.ndarray:
    """
    FM synthesis: carrier modulated by modulator.

    carrier_freq: fundamental frequency
    mod_freq: modulator frequency (try ratios like 1:1, 1:2, 1:3)
    mod_index: modulation depth (0=pure sine, 5+=very metallic)
    """
    t = np.linspace(0, duration, int(SAMPLE_RATE * duration), endpoint=False)
    modulator = mod_index * np.sin(2 * np.pi * mod_freq * t)
    return np.sin(2 * np.pi * carrier_freq * t + modulator)
```

### 3j. Filtering

Butterworth filters via scipy for shaping frequency content. Low-pass removes harshness, high-pass removes rumble, band-pass isolates character.

```python
from scipy.signal import butter, sosfilt

def lowpass(signal: np.ndarray, cutoff: float, order: int = 4) -> np.ndarray:
    """Apply Butterworth low-pass filter."""
    sos = butter(order, cutoff, btype='low', fs=SAMPLE_RATE, output='sos')
    return sosfilt(sos, signal)

def highpass(signal: np.ndarray, cutoff: float, order: int = 4) -> np.ndarray:
    """Apply Butterworth high-pass filter."""
    sos = butter(order, cutoff, btype='high', fs=SAMPLE_RATE, output='sos')
    return sosfilt(sos, signal)

def bandpass(signal: np.ndarray, low: float, high: float,
             order: int = 4) -> np.ndarray:
    """Apply Butterworth band-pass filter."""
    sos = butter(order, [low, high], btype='band', fs=SAMPLE_RATE, output='sos')
    return sosfilt(sos, signal)
```

### 3k. Reverb (Simple Convolution)

Convolves the signal with a short exponentially decaying noise burst. Creates a sense of space — large hall for Zelda/Kingdom Hearts, tight room for StarCraft.

```python
def reverb(signal: np.ndarray, room_size: float = 0.3,
           decay: float = 4.0, wet: float = 0.3) -> np.ndarray:
    """
    Simple convolution reverb using exponentially decaying noise impulse.

    room_size: impulse response duration in seconds (0.1=small, 0.5=cathedral)
    decay: decay rate of impulse (higher=drier)
    wet: mix ratio (0.0=dry, 1.0=fully wet)
    """
    ir_len = int(SAMPLE_RATE * room_size)
    t = np.linspace(0, room_size, ir_len, endpoint=False)
    impulse = np.random.randn(ir_len) * np.exp(-decay * t)
    impulse /= np.max(np.abs(impulse))
    wet_signal = np.convolve(signal, impulse, mode='full')[:len(signal)]
    return (1.0 - wet) * signal + wet * wet_signal
```

### 3l. Stereo Panning

Positions a mono signal in the stereo field. Uses constant-power panning law to avoid volume dips at center.

```python
def pan(signal: np.ndarray, position: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """
    Constant-power stereo panning.

    position: -1.0 (full left) to +1.0 (full right), 0.0 = center
    Returns (left, right) channel arrays.
    """
    angle = (position + 1.0) * np.pi / 4  # 0 to pi/2
    left = signal * np.cos(angle)
    right = signal * np.sin(angle)
    return left, right
```

### 3m. Layering and Mixing

Combines multiple signals with gain control and peak normalization.

```python
def mix(*signals_and_gains: tuple[np.ndarray, float]) -> np.ndarray:
    """
    Mix multiple signals with individual gain values.

    Args: tuples of (signal, gain) where gain is linear (1.0 = unity)
    Returns: mixed signal normalized to prevent clipping
    """
    max_len = max(len(s) for s, g in signals_and_gains)
    mixed = np.zeros(max_len)
    for signal, gain in signals_and_gains:
        padded = np.pad(signal, (0, max_len - len(signal)))
        mixed += padded * gain
    peak = np.max(np.abs(mixed))
    if peak > 0:
        mixed /= peak
    return mixed

def seq(*signals: np.ndarray) -> np.ndarray:
    """Concatenate signals sequentially (no overlap)."""
    return np.concatenate(signals)

def overlay(base: np.ndarray, overlay_sig: np.ndarray,
            offset_samples: int = 0, gain: float = 0.5) -> np.ndarray:
    """Overlay a signal onto a base at a given sample offset."""
    result = base.copy()
    end = min(offset_samples + len(overlay_sig), len(result))
    actual_len = end - offset_samples
    result[offset_samples:end] += overlay_sig[:actual_len] * gain
    return result
```

### 3n. Silence

Explicit silence for spacing between notes in sequences.

```python
def silence(duration: float) -> np.ndarray:
    """Generate silence of given duration."""
    return np.zeros(int(SAMPLE_RATE * duration))
```

---

## 4. Psychoacoustic Guidelines

From game audio UX research (report 04) and earcon design literature. These constraints govern all theme recipes.

### 4.1 Emotional-to-Acoustic Mapping

| Emotional Target | Frequency Range | Interval | Attack | Duration | Example Use |
|-----------------|-----------------|----------|--------|----------|-------------|
| Success/reward | 500-1500 Hz | Major 3rd (4 semitones) | Medium (10-30ms) | 200-400ms | task_complete |
| Error/danger | 200-800 Hz | Minor 2nd (1) or tritone (6) | Hard (<5ms) | 150-300ms | error |
| Alert/attention | 1000-3000 Hz | Perfect 5th (7 semitones) | Sharp (<10ms) | 100-200ms | notification |
| Completion/calm | 300-800 Hz | Octave (12) or 5th (7) | Soft (30-80ms) | 300-600ms | session_end |
| Neutral/ack | 500-1000 Hz | Unison (0) | Soft (5-15ms) | 50-150ms | prompt_ack |
| Achievement | 400-2000 Hz | Ascending arpeggio | Medium-soft (15-40ms) | 400-800ms | commit |
| Urgency | 1500-3000 Hz | Minor 2nd (1) or tritone (6) | Very sharp (<3ms) | 100-200ms | permission |
| Deployment | 400-1200 Hz | Perfect 4th (5) ascending | Medium (10-20ms) | 200-300ms | agent_deploy |
| Return/arrival | 600-1400 Hz | Perfect 4th (5) descending | Soft (20-40ms) | 200-400ms | agent_return |
| Compression | 800-2000 Hz | Chromatic descent | Medium (10-20ms) | 200-300ms | compact |

### 4.2 Key Constraints

- **Optimal earcon core**: 700-2000 Hz for the primary frequency content of every notification sound. This is the human ear's peak sensitivity band for alert perception.
- **Avoid below 200 Hz**: Lost on laptop speakers, small monitors, and HDMI audio paths. Sub-200 Hz content is only for bass reinforcement on full-range speakers — never as the sole frequency content.
- **Avoid above 4000 Hz**: Fatiguing with repetition. Brief transient content above 4kHz is acceptable for attack character, but sustained energy above 4kHz causes ear fatigue over long sessions.
- **8-12 distinct sounds maximum per theme**: The earcon vocabulary ceiling. Beyond 12 sonically distinct sounds, users cannot reliably distinguish which event triggered which sound. claude-voice uses exactly 12 semantic events — every one must be perceptually unique within a theme.
- **Sub-150ms perceived latency**: The brain links cause and effect within 150ms. Hook processing (50ms) plus pw-play startup (30-50ms) must total under 150ms. Sound generation happens offline — this constraint applies only to playback.
- **3-7 variants per event**: Identical repeats cause auditory habituation within 10-20 occurrences. Random variant selection from a pool keeps the brain engaged. Variants share timbre and emotional tone but differ in pitch, timing, and ornamental detail.
- **Frequent events under 200ms**: `prompt_ack` fires on every user message. It must be under 200ms or it becomes oppressive. `task_complete` fires on every Claude response — 200-400ms is the ceiling.

### 4.3 Duration Budgets by Event

| Event | Minimum | Target | Maximum | Frequency | Rationale |
|-------|---------|--------|---------|-----------|-----------|
| session_start | 400ms | 600ms | 800ms | Once per session | Boot jingle. Longest earcon. Sets sonic identity. |
| session_end | 200ms | 350ms | 500ms | Once per session | Logout. Gentle, complementary to boot. |
| prompt_ack | 50ms | 100ms | 200ms | Every user message | Must be ultra-brief. Disabled by default. |
| task_complete | 150ms | 250ms | 400ms | Every Claude response | Workhorse sound. Most variants needed. |
| agent_deploy | 150ms | 200ms | 300ms | Each subagent spawn | Outward dispatch. Brief. |
| agent_return | 150ms | 250ms | 400ms | Each subagent return | Inward arrival. Slightly longer. |
| error | 100ms | 200ms | 300ms | Tool failures | Sharp, attention-grabbing. |
| notification | 150ms | 300ms | 400ms | System notifications | Alert. Bright. |
| commit | 250ms | 400ms | 600ms | Git commits | Achievement moment. Can be longer. |
| permission | 80ms | 150ms | 200ms | Permission requests | Sharp attention snap. |
| compact | 150ms | 200ms | 300ms | Context compaction | Informational crunch. |
| ambient | 15s | 30s | 60s | Continuous loop | Background layer. Separate concern. |

---

## 5. Theme Synthesis Recipes

Each theme defines a complete set of 12 sound events. The recipes below specify exact synthesis parameters for the base variant of each event. Variant generation (section 6) creates additional variants from each base.

### Musical Constants

```python
# MIDI note to frequency
def midi_to_freq(note: int) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)

# Named note frequencies (Hz) - middle octave and surroundings
C3 = 130.81;  D3 = 146.83;  E3 = 164.81;  F3 = 174.61;  G3 = 196.00;  A3 = 220.00;  B3 = 246.94
C4 = 261.63;  D4 = 293.66;  E4 = 329.63;  F4 = 349.23;  G4 = 392.00;  A4 = 440.00;  B4 = 493.88
C5 = 523.25;  D5 = 587.33;  E5 = 659.25;  F5 = 698.46;  G5 = 783.99;  A5 = 880.00;  B5 = 987.77
C6 = 1046.50; D6 = 1174.66; E6 = 1318.51

# Sharps/flats
Cs4 = 277.18;  Eb4 = 311.13;  Fs4 = 369.99;  Gs4 = 415.30;  Bb4 = 466.16
Cs5 = 554.37;  Eb5 = 622.25;  Fs5 = 739.99;  Gs5 = 830.61;  Ab4 = 415.30
Gs3 = 207.65;  Ab5 = 830.61;  Fs3 = 185.00;  Bb3 = 233.08

# Interval ratios (for tuning)
MINOR_2ND = 16 / 15       # 1 semitone — tension
MAJOR_3RD = 5 / 4         # 4 semitones — happy, bright
PERFECT_4TH = 4 / 3       # 5 semitones — open, neutral
TRITONE = 2 ** 0.5        # 6 semitones — ominous
PERFECT_5TH = 3 / 2       # 7 semitones — strong, stable
OCTAVE = 2 / 1            # 12 semitones — resolution
```

---

### 5.1 Default Theme (default)

**Sonic DNA**: Clean, neutral, professional. Pure sine tones with soft envelopes. No cultural reference — the fallback theme that works in any context. Think macOS notification sounds: understated, clear, never distracting.

**Frequency range**: 500-2000 Hz
**Instrument palette**: Pure sine, gentle FM bells
**Emotional tone**: Calm, focused, unobtrusive

| Event | Technique | Frequencies | Duration | Envelope | Character Notes |
|-------|-----------|-------------|----------|----------|-----------------|
| session_start | Ascending 3-note sine arpeggio | C5 (523), E5 (659), G5 (784) | 500ms | ADSR: A=30ms, D=50ms, S=0.6, R=150ms per note | Clean major triad. Warm. "System ready." Each note 120ms with 20ms gap. |
| session_end | Descending 2-note sine | G4 (392), C4 (262) | 350ms | ADSR: A=40ms, D=30ms, S=0.5, R=180ms | Perfect 5th descending to root. Gentle resolution. |
| prompt_ack | Single soft sine ping | A4 (880) | 100ms | Exp decay, rate=8.0 | Just a soft tick. Barely there. Confirms receipt. |
| task_complete | Rising 2-note sine chime | E5 (659), G5 (784) | 250ms | ADSR: A=10ms, D=30ms, S=0.7, R=100ms | Minor 3rd interval. Pleasant, not celebratory. |
| agent_deploy | Ascending sine sweep | 400 -> 800 Hz | 200ms | Exp decay, rate=3.0 | Smooth upward glide. "Dispatched." |
| agent_return | Descending sine sweep | 800 -> 500 Hz | 250ms | ADSR: A=5ms, D=30ms, S=0.6, R=150ms | Complementary to deploy. "Returned." |
| error | Two-note dissonant sine | E4 (330) + F4 (349) simultaneous | 200ms | ADSR: A=3ms, D=20ms, S=0.4, R=100ms | Minor 2nd interval. Tense, clear warning. |
| notification | FM bell ping | Carrier 1000 Hz, mod 1500 Hz, index 2.0 | 300ms | Exp decay, rate=4.0 | Metallic bell. Bright. Attention-getting. |
| commit | Ascending 4-note arpeggio | C5, E5, G5, C6 (523, 659, 784, 1047) | 400ms | Each note: A=10ms, D=20ms, S=0.7, R=60ms | Major triad + octave. Small celebration. |
| permission | Sharp high ping | B5 (988) | 120ms | Exp decay, rate=12.0 | Very fast attack, fast decay. "Look here." |
| compact | Descending chromatic 3-note | D5 (587), Cs5 (554), C5 (523) | 200ms | Each note: A=5ms, D=10ms, S=0.5, R=40ms | Chromatic descent. "Compressing." |
| ambient | Slow sine pad with gentle FM | 300 Hz carrier, 0.1 Hz mod, index 0.3 | 30s loop | Constant level, crossfade ends | Near-subliminal. Warm drone. Volume at 20% of earcons. |

---

### 5.2 StarCraft Theme (starcraft)

**Sonic DNA**: Terran military command. Digital, precise, slightly mechanical. Synth bass, servo motors, scanner sweeps, radio static bursts, HUD beeps. The Adjutant's domain — cold blue light, command center displays, nuclear launch buttons.

**Frequency range**: 200-4000 Hz
**Instrument palette**: Sine + square (digital beeps), sawtooth (servo), white noise (radio static), FM synthesis (scanner)
**Emotional tone**: Military precision, controlled urgency, command authority

| Event | Technique | Frequencies | Duration | Envelope | Character Notes |
|-------|-----------|-------------|----------|----------|-----------------|
| session_start | Boot sequence: ascending digital sweep + 3 confirmation beeps + radio static burst | Sweep 200->2000 Hz (300ms), then 3 square beeps at 800/1000/1200 Hz (60ms each, 30ms gaps), static burst 50ms | 650ms | Sweep: exp decay rate=1.5. Beeps: ADSR A=3ms, D=10ms, S=0.8, R=15ms. Static: exp decay rate=10.0 | "Command center online." The sweep is the power-up, beeps are system checks, static is comms activation. Low-pass filter at 3000 Hz on sweep for warmth. |
| session_end | Power-down: descending sweep + 2 fading beeps | Sweep 1500->200 Hz (250ms), then 2 square beeps at 800/600 Hz (50ms each), each quieter | 400ms | Sweep: exp decay rate=2.0. Beeps: exp decay rate=6.0, second beep at 50% volume | "Systems offline." Inverse of boot. Feels like shutting down terminals. |
| prompt_ack | SCV acknowledgment: crisp double beep | Two square wave beeps at 1000 Hz, 60ms each, 40ms gap | 160ms | ADSR: A=2ms, D=5ms, S=0.9, R=10ms per beep | "Yes sir." Short, military, confirmatory. High sustain for digital crispness. |
| task_complete | Mission objective complete: ascending 3-note digital fanfare | Square wave: C5 (523), E5 (659), G5 (784), 80ms each, 15ms gaps | 270ms | ADSR: A=3ms, D=15ms, S=0.7, R=30ms per note | "Objective achieved." Major triad but in digital square wave. Satisfying without being celebratory. |
| agent_deploy | Unit deployed: rising blip arpeggio | Square wave: 400, 600, 800, 1000 Hz, 40ms each, 10ms gaps | 200ms | ADSR: A=2ms, D=5ms, S=0.8, R=8ms per blip | "Carrier has arrived" energy scaled down. Quick ascending digital staircase. |
| agent_return | Unit reporting: incoming transmission chirp + confirmation | FM chirp (carrier 1200, mod 800, index 3.0) 80ms, then sine ping at 1400 Hz 100ms | 200ms | Chirp: exp decay rate=8.0. Ping: ADSR A=5ms, D=20ms, S=0.5, R=80ms | "Reporting in." The chirp is the radio crackle, the ping is the data received. |
| error | Shield hit: descending buzz + low thump | Sawtooth sweep 600->200 Hz (150ms) with distortion (clip at 0.7), noise burst at 150 Hz (50ms) | 220ms | Sweep: exp decay rate=3.0. Thump: exp decay rate=15.0 | "Under attack." Dissonant, metallic, grabs attention. Clip the sawtooth for crunch. |
| notification | Nuclear alert: two-tone klaxon | Alternating square beeps: 1500 Hz (80ms) then 1000 Hz (80ms), repeated once | 350ms | ADSR: A=2ms, D=5ms, S=0.9, R=5ms per tone | "Nuclear launch detected" scaled to notification. The alternating pitch is the alert pattern. High priority. |
| commit | Build complete: low confirm + high ping | Sawtooth tone at 440 Hz (100ms) + sine ping at 880 Hz (150ms) overlaid, then square beep at 1200 Hz (80ms) | 400ms | Low: ADSR A=5ms, D=30ms, S=0.6, R=50ms. Ping: exp decay 2.0. Beep: exp decay 8.0 | "Construction complete." The sawtooth is the mechanical thunk, sine is the digital confirmation, beep is the HUD update. |
| permission | Attention snap: sharp high square beep | Square wave at 2000 Hz, single hit | 100ms | ADSR: A=1ms, D=5ms, S=0.8, R=15ms | Sharp. Unmistakable. "Awaiting orders." Cuts through anything. |
| compact | Data compression: descending digital crunch | 3 rapid square notes descending: 1200, 900, 600 Hz (40ms each, 5ms gaps) with noise undertone | 140ms | Notes: ADSR A=2ms, D=5ms, S=0.7, R=8ms. Noise: exp decay 12.0 at 20% gain | "Compressing data." Quick digital crunch. |
| ambient | Command center hum: filtered noise + slow scanner pulse | Band-passed noise (200-800 Hz) at low volume, with slow sine pulse at 0.5 Hz modulating 300 Hz carrier | 30s loop | Constant. Tremolo depth=0.2 at 0.5 Hz on carrier | Quiet background hum of electronics. Should feel like sitting at a terminal in the command center. Volume 15% of earcons. |

**Variant strategy**: 4 variants per event. Pitch shift ±50 cents on beep frequencies. Radio static intensity variation ±30%. Timing gaps ±15ms.

---

### 5.3 Warcraft Theme (warcraft)

**Sonic DNA**: Medieval fantasy. Hammer on anvil, spell chimes, horn calls, wood and metal impacts. The forge, the war room, the campfire. Warcraft II/III era: rich, organic, warm — the Horde's earthy tones mixed with Alliance brass.

**Frequency range**: 100-3500 Hz
**Instrument palette**: Sawtooth (brass horns), FM synthesis (bells/anvil), noise bursts (impacts/foley), sine (spell tones), triangle (lute pluck)
**Emotional tone**: Epic warmth, fantasy grandeur, rustic heroism

| Event | Technique | Frequencies | Duration | Envelope | Character Notes |
|-------|-----------|-------------|----------|----------|-----------------|
| session_start | Castle gate + war horn: low rumble sweep + brass horn call with reverb | Noise rumble LP at 200 Hz (200ms), then sawtooth horn at 350 Hz (300ms) with reverb (room=0.4, wet=0.4) | 600ms | Rumble: exp decay 2.0. Horn: ADSR A=50ms, D=40ms, S=0.7, R=150ms | "The gates are open." Low rumble is the gate mechanism, horn announces readiness. Reverb gives castle courtyard space. |
| session_end | Embers dying: descending horn + low bell | Sawtooth at 300 Hz descending to 200 Hz over 250ms, then FM bell (carrier 250, mod 375, index 1.5) 150ms | 450ms | Horn: exp decay 1.5. Bell: ADSR A=20ms, D=30ms, S=0.3, R=150ms | "The fires grow low." Settling, peaceful. The bell is a distant tower bell. |
| prompt_ack | Peon "Work work": short sawtooth tap | Single sawtooth tone at 440 Hz, bright, staccato | 120ms | ADSR: A=5ms, D=15ms, S=0.6, R=30ms | "Ready to serve." Quick brass-like tap. Not melodic, just an acknowledgment. |
| task_complete | "Job's done": hammer strike on anvil + bright chime | Noise transient 20ms (anvil strike), then FM bell (carrier 1200, mod 1800, index 2.0) 200ms | 280ms | Strike: exp decay 20.0. Bell: ADSR A=5ms, D=30ms, S=0.4, R=150ms | The sharp noise hit is the hammer, the FM ring is the anvil resonance. Satisfying metallic clang. |
| agent_deploy | War horn dispatch: ascending brass figure | Sawtooth 2-note: D4 (294) then A4 (440), 80ms each, 15ms gap | 200ms | ADSR: A=15ms, D=20ms, S=0.7, R=30ms per note | "To battle!" Rising perfect 5th on brass. Feels like sending troops. |
| agent_return | Scout returns: descending horn + wood thud | Sawtooth: A4 (440) to D4 (294) 80ms each, 15ms gap, then noise LP at 300 Hz 40ms | 240ms | Horn: ADSR A=10ms, D=20ms, S=0.6, R=30ms. Thud: exp decay 15.0 | "Report, soldier." Descending horn mirrors deploy. Thud is boots arriving. |
| error | Spell fizzle: descending whoosh + sparkle decay | Sweep 800->200 Hz sine (120ms), then high sine scatter (random 1500-3000 Hz pings, 5 pings over 100ms) | 250ms | Sweep: exp decay 3.0. Pings: each exp decay 10.0, random gain 0.3-0.7 | "That spell failed." The whoosh is the misfired magic, sparkles are the dissipating energy. |
| notification | Signal fire: bright horn stab + echo | Sawtooth at 700 Hz (80ms), then same with reverb (room=0.3, wet=0.5) at 50% volume (100ms) | 300ms | Horn: ADSR A=5ms, D=15ms, S=0.8, R=20ms. Echo: same with exp decay 2.0 | "A signal fire burns!" Sharp horn with deliberate echo, like a call across the valley. |
| commit | Anvil forging: 3 hammer strikes + rising chord | 3 noise transients (20ms each, 60ms apart), then sawtooth chord C4+E4+G4 (262/330/392 Hz, 200ms) | 450ms | Strikes: exp decay 20.0, increasing volume (0.5, 0.7, 0.9). Chord: ADSR A=20ms, D=30ms, S=0.6, R=100ms | "A mighty weapon forged!" Three hammer blows building to a triumphant chord. The craft is complete. |
| permission | War drum attention: sharp percussive hit | Noise burst LP at 400 Hz + sine at 500 Hz simultaneous | 100ms | ADSR: A=2ms, D=10ms, S=0.3, R=40ms | "Attend!" Single war drum thud. Low, percussive, impossible to miss. |
| compact | Scroll rolling: descending filtered noise | Noise band-passed 600-2000 Hz, with frequency descending via moving filter | 200ms | Exp decay 3.0 with filter sweep from 2000 to 600 Hz over duration | "Compacting the scrolls." Papery rustling sound via filtered noise. |
| ambient | Campfire + distant forest: filtered noise + slow crackle | Pink noise LP at 400 Hz (wind), random pops (noise bursts, 10ms, random interval 0.5-2s) | 30s loop | Constant wind at 10% volume, pops at random 20-40% volume | Sitting by the fire in the barracks. Warm, organic, low. Volume 15% of earcons. |

**Variant strategy**: 4 variants per event. Horn pitch ±60 cents (wider for organic feel). Anvil ring frequency ±200 Hz. Impact timing ±20ms. Reverb wet ±15%.

---

### 5.4 Mario Theme (mario)

**Sonic DNA**: Mushroom Kingdom chiptune. Square waves, bright arpeggios, bouncy rhythms. The NES sound chip had 2 square wave channels, 1 triangle channel, and 1 noise channel — this is the entire palette. Musical key of C major. Everything is staccato, upbeat, and colorful.

**Frequency range**: 300-4000 Hz
**Instrument palette**: Square wave (melody, everything), triangle wave (bass), noise (percussion), band-limited square (higher notes)
**Emotional tone**: Cheerful, bouncy, bright, playful

| Event | Technique | Frequencies | Duration | Envelope | Character Notes |
|-------|-----------|-------------|----------|----------|-----------------|
| session_start | World 1-1 style: 4-note ascending arpeggio | Square wave: C4 (262), E4 (330), G4 (392), C5 (523), 80ms each, 20ms gaps | 400ms | ADSR: A=3ms, D=10ms, S=0.8, R=20ms per note | "Let's-a go!" Major chord arpeggio in pure square wave. Bouncy. Each note pops cleanly. |
| session_end | Flagpole slide: descending glissando | Square sweep C5 (523) -> C4 (262) linear, 200ms, then triangle thud at C3 (131) 80ms | 300ms | Sweep: exp decay 2.0. Thud: exp decay 8.0 | "Course clear!" Sliding down the flagpole. The thud is landing at the bottom. |
| prompt_ack | Coin collect: two-note ascending blip | Square wave: B5 (988) 60ms, then E6 (1319) 80ms, 10ms gap | 160ms | ADSR: A=2ms, D=8ms, S=0.7, R=15ms per note | The iconic coin sound. B to E (perfect 4th up). Crisp, bright, universally satisfying. |
| task_complete | Power star jingle: ascending 3-note | Square: C5 (523), E5 (659), G5 (784), 60ms each, 10ms gaps | 210ms | ADSR: A=2ms, D=8ms, S=0.8, R=15ms per note | Like a mini power-up collection. Major triad, quick, cheerful. |
| agent_deploy | Pipe warp: descending square glissando + pop | Square sweep 800->300 Hz (120ms), then square pop at 500 Hz (40ms) | 180ms | Sweep: exp decay 3.0. Pop: ADSR A=2ms, D=5ms, S=0.6, R=10ms | "Going down the pipe." The sweep is entering, the pop is arriving. |
| agent_return | Pipe emerge: ascending square glissando + pop | Square sweep 300->800 Hz (120ms), then square pop at 700 Hz (40ms) | 180ms | Sweep: linear, exp decay 2.5. Pop: ADSR A=2ms, D=5ms, S=0.7, R=10ms | Inverse of deploy. Coming back up from the pipe. |
| error | Damage shrink: rapid descending spiral | Square: C5, B4, Bb4, A4, Ab4 (523, 494, 466, 440, 415), 40ms each, 5ms gaps | 220ms | ADSR: A=2ms, D=5ms, S=0.6, R=8ms per note, each note at decreasing volume (1.0, 0.85, 0.7, 0.55, 0.4) | "Ouch!" Chromatic descent with diminishing volume. Mario taking damage. |
| notification | Block hit: percussive thump + note | Noise burst 15ms LP at 300 Hz, then square at 660 Hz 80ms | 120ms | Thump: exp decay 20.0. Note: ADSR A=3ms, D=10ms, S=0.6, R=30ms | Hitting a ? block. The thump is the block, the note is what comes out. |
| commit | 1-UP mushroom: ascending 6-note jingle | Square: C5, E5, G5, C6, E6, G5 (523, 659, 784, 1047, 1319, 784), 50ms each, 10ms gaps | 360ms | ADSR: A=2ms, D=8ms, S=0.7, R=12ms per note | "1-UP!" Ascending chromatic-ish arpeggio. Celebratory. The highest note then drops back down for resolution. |
| permission | Pause menu: single high bleep | Square at 880 Hz, single staccato hit | 80ms | ADSR: A=2ms, D=5ms, S=0.8, R=10ms | "!" Pause/attention. Very short, very sharp. |
| compact | Block compress: 3 descending thuds | Noise bursts LP at 400 Hz: 3 hits at decreasing pitch (400, 300, 200 Hz filter), 30ms each, 20ms gaps | 150ms | Exp decay 15.0 per hit, decreasing volume (0.8, 0.6, 0.4) | Like blocks compressing into each other. Bouncy rhythm. |
| ambient | Underground level: triangle bass pulse + echo drops | Triangle wave at C3 (131) pulsing at 2 Hz (on 100ms, off 400ms), with random square pings at 1000-2000 Hz every 2-4s | 30s loop | Bass: constant. Pings: exp decay 6.0, random volume 0.1-0.3 | Underground level ambience. Sparse, echoey, mysterious. Volume 20% of earcons. |

**Variant strategy**: 4 variants per event. Pitch shift ±30 cents (tighter for chiptune accuracy). Tempo variation ±8% on note durations. Occasional octave shift on final note of sequences.

---

### 5.5 Legend of Zelda Theme (zelda)

**Sonic DNA**: Hyrule. Ocarina notes, harp arpeggios, fairy sparkles, treasure fanfares. Sine waves with reverb-like decay, majestic intervals. Key of A minor with major resolution. The silence between notes is as important as the notes themselves — Zelda audio breathes.

**Frequency range**: 150-4000 Hz
**Instrument palette**: Sine (ocarina, melody), FM synthesis (harp, fairy sparkle), triangle (bass undertone), filtered noise (wind, sparkle texture)
**Emotional tone**: Mystical, melodic, reverent, wonder

| Event | Technique | Frequencies | Duration | Envelope | Character Notes |
|-------|-----------|-------------|----------|----------|-----------------|
| session_start | Fairy fountain hint: harp arpeggio with sparkle overlay | FM harp (carrier at each note freq, mod=freq*1.5, index=1.0): Am arpeggio A3 (220), C4 (262), E4 (330), A4 (440), 100ms each, 30ms gaps. HP noise sparkle overlay 2000-4000 Hz. Reverb room=0.4, wet=0.35 | 650ms | Each note: ADSR A=10ms, D=30ms, S=0.5, R=80ms. Sparkle: exp decay 2.0, gain=0.15 | "The fairy fountain theme in miniature." Am arpeggio ascending. The reverb gives it that cavernous, magical space. Sparkle is subtle, like fairy dust. |
| session_end | Temple bell: low octave + 5th resolving | Sine chord A3 (220) + E4 (330) held 200ms, then resolving to A3 (220) alone for 200ms. Reverb room=0.5, wet=0.4 | 450ms | Chord: ADSR A=40ms, D=30ms, S=0.6, R=150ms. Resolution: exp decay 1.5 | "The temple rests." Open 5th chord settling to root. Serene, ancient, final. |
| prompt_ack | "Hey! Listen!" ping: rising attention tone | Sine sweep 440->880 Hz (80ms) then hold 880 Hz (40ms) | 130ms | Sweep: linear amp from 0.3 to 1.0. Hold: exp decay 5.0 | Navi's attention call. Rising octave sweep then a brief hold. Not annoying (unlike the original). |
| task_complete | Secret discovered: the iconic 4-note jingle | Sine: Gs4 (415), A4 (440), B4 (494), E5 (659), 80ms each, 15ms gaps. Reverb room=0.3, wet=0.25 | 400ms | ADSR: A=8ms, D=20ms, S=0.6, R=60ms per note | "You found a secret!" The classic ascending figure. Last note (E5) is a leap — this gives it the "revelation" character. |
| agent_deploy | Companion sent: fairy sparkle dispatch | Rapid FM pings (carrier 1800-2400 Hz, mod=carrier*2, index=1.5), 5 pings descending in pitch over 180ms, HP noise texture underneath | 220ms | Each ping: 30ms, exp decay 8.0. Noise: exp decay 4.0, gain=0.1 | "Go forth, fairy." Descending sparkle cascade. The fairy flies outward. Light and magical. |
| agent_return | Companion returns: ascending sparkle arrival | Same FM sparkle as deploy but ascending in pitch (1400->2200 Hz), 5 pings over 180ms | 220ms | Same as deploy but ascending pitch order | Inverse of deploy. The fairy returns with information. Ascending = arriving. |
| error | Game over tone: descending minor arpeggio | Sine: E4 (330), C4 (262), A3 (220), 100ms each, 20ms gaps. Reverb room=0.3, wet=0.3 | 380ms | ADSR: A=5ms, D=30ms, S=0.4, R=80ms per note, decreasing volume (0.9, 0.7, 0.5) | Am arpeggio descending. Each note dimmer. Not harsh — melancholy. Zelda errors feel sad, not alarming. |
| notification | Item get ping: bright attention chime | FM bell (carrier 1000, mod 1500, index 2.5) 80ms, then sine at 1200 Hz 100ms. Reverb room=0.2, wet=0.2 | 200ms | Bell: exp decay 6.0. Sine: ADSR A=5ms, D=20ms, S=0.5, R=60ms | "Something important!" Bright, crystalline. Like finding a rupee or key item. |
| commit | Chest open fanfare: ascending 4-note triumph | Sine: A4 (440), Cs5 (554), E5 (659), A5 (880), first 3 notes 80ms each with 10ms gaps, final note 180ms held. Reverb room=0.3, wet=0.3 | 460ms | First 3: ADSR A=8ms, D=15ms, S=0.7, R=15ms. Final: ADSR A=10ms, D=40ms, S=0.6, R=120ms | "Da-da-da-DAAAA!" A major arpeggio. The held final note is the chest opening and light pouring out. |
| permission | Navi urgent: sharp ping pair | Sine at 2000 Hz (50ms), gap 30ms, sine at 2200 Hz (50ms) | 140ms | Exp decay 10.0 per ping | "HEY!" Two sharp pings. Higher pitch than other sounds. Impossible to ignore. |
| compact | Scroll seal: descending filtered sweep | Sine sweep 1000->400 Hz (150ms) with band-pass narrowing (2000->500 Hz bandwidth) | 180ms | Exp decay 2.5 | "Sealing the scroll." The narrowing bandwidth gives a "closing" feeling. |
| ambient | Lost Woods: filtered wind + distant ocarina | Pink noise LP at 600 Hz (wind). Every 4-6s, a single sine note from pentatonic scale (A3, C4, D4, E4, G4) with reverb room=0.5, wet=0.5, at 15% volume | 30s loop | Wind: constant at 8% vol. Notes: ADSR A=80ms, D=50ms, S=0.3, R=300ms | Gentle forest wind with occasional distant melody. Sparse. Each note hangs in the air. The silence between notes defines the character. |

**Variant strategy**: 4 variants per event. Pitch shift ±40 cents. Reverb wet ±20%. Sparkle ping count ±1. Timing gaps ±25ms (Zelda breathes, so timing variation is wider).

---

### 5.6 Super Smash Bros Theme (smash)

**Sonic DNA**: Arena energy. Impact sounds, crowd roar, announcer-style stingers, character select tones. The FGC tournament stage — bright lights, big hits, dramatic pauses. Melee and Ultimate era: tight, responsive, punchy. Everything has WEIGHT.

**Frequency range**: 100-6000 Hz
**Instrument palette**: Noise (impacts, crowd), sine+square (stingers), FM synthesis (electric guitar stabs), sweep generators (whooshes), layered transients
**Emotional tone**: Competitive, punchy, dramatic, high-energy

| Event | Technique | Frequencies | Duration | Envelope | Character Notes |
|-------|-----------|-------------|----------|----------|-----------------|
| session_start | Character select: rising sweep + confirmation hit | Sweep 150->1200 Hz (250ms, sine), gap 30ms, then layered impact: noise burst + sine at 600 Hz + FM stinger (carrier 800, mod 1200, index 3.0) 120ms. Reverb room=0.2, wet=0.2 | 450ms | Sweep: exp decay 0.8. Impact: ADSR A=2ms, D=15ms, S=0.5, R=80ms | "Choose your fighter." The sweep builds tension, the impact is the selection lock-in. Heavy, authoritative. |
| session_end | Results screen: descending resolution | FM chord (carriers 600+900 Hz, mods at 1.5x, index 2.0) held 200ms, then descending to 400+600 Hz over 200ms | 450ms | ADSR: A=20ms, D=30ms, S=0.5, R=150ms | "Game set." Rich, settling. The match is over. |
| prompt_ack | Menu select: crisp UI blip | Sine at 880 Hz, single clean hit | 80ms | Exp decay 8.0 | Clean, precise. Menu cursor confirmation. No personality, just function. |
| task_complete | KO confirm: deep impact + crowd hint | Noise burst (50ms) LP at 300 Hz (the hit), then sine ring at 400 Hz (100ms), HP noise at 3000-5000 Hz at 15% gain (crowd swell, 100ms) | 280ms | Hit: exp decay 15.0. Ring: exp decay 3.0. Crowd: ADSR A=30ms, D=20ms, S=0.3, R=80ms | "That's a KO." The deep thump, the resonant ring, the distant crowd reaction. Punchy and satisfying. |
| agent_deploy | Fighter selected: selection chord hit | Simultaneous sine+square at C5 (523) + E5 (659), sharp attack | 200ms | ADSR: A=3ms, D=20ms, S=0.5, R=100ms | "Player 2 enters." Parallel 3rd interval. Clean and decisive. |
| agent_return | Victory pose: triumphant 2-note stinger | Square: G5 (784) 60ms then C6 (1047) 100ms, 10ms gap | 180ms | ADSR: A=3ms, D=10ms, S=0.7, R=40ms per note | Perfect 4th ascending. Victorious. "The fighter returns." |
| error | Stock lost: descending whoosh + explosion | Sweep 600->50 Hz exponential (200ms), then noise burst LP at 200 Hz (60ms) with distortion (clip at 0.6) | 280ms | Sweep: exp decay 0.8. Burst: exp decay 10.0 | "SD!" The descending whoosh is the fall, the thump is the blast zone. Dramatic. |
| notification | Announcer alert: bright stinger + echo | FM stinger (carrier 1500, mod 2000, index 2.5) 80ms, then same with reverb (room=0.3, wet=0.5) at 40% volume 120ms | 250ms | Stinger: exp decay 6.0. Echo: exp decay 3.0 | "Attention!" Bright, metallic, arena PA system energy. The echo gives it arena scale. |
| commit | "GAME!" stinger: dramatic resolution chord | Layered: sine at 400 Hz + square at 800 Hz + FM bell (carrier 600, mod 900, index 2.0), held with reverb room=0.3, wet=0.3 | 500ms | ADSR: A=5ms, D=40ms, S=0.4, R=200ms | "GAME!" The most dramatic sound in the set. Full, resonant, final. A moment of silence should follow. |
| permission | Ready stance: breath-like attention tone | Sine at 320 Hz with tremolo (mod_freq=8, depth=0.3), rising to 500 Hz over duration | 150ms | ADSR: A=15ms, D=10ms, S=0.7, R=40ms | "Ready..." Tense, anticipatory. The tremolo gives it a vibrating energy. |
| compact | Shield shrink: descending filtered buzz | Square sweep 1500->500 Hz (150ms) with LP filter tracking (following pitch) | 170ms | Exp decay 3.0 | Like a shield getting smaller. Mechanical, tight. |
| ambient | Tournament venue: low crowd murmur + occasional impact echoes | Pink noise LP at 800 Hz (crowd). Every 3-5s, a random noise impact (50ms) with reverb room=0.4, wet=0.6 at 10% volume (distant match) | 30s loop | Crowd: constant at 8% vol. Impacts: exp decay 8.0, random volume 0.05-0.15 | You're backstage at EVO. Distant matches echo. The crowd is a warm drone. Volume 15% of earcons. |

**Variant strategy**: 4 variants per event. Impact noise seed variation. Sweep endpoint ±100 Hz. Ring frequency ±80 Hz. Crowd swell duration ±30ms.

---

### 5.7 Kingdom Hearts Theme (kingdom-hearts)

**Sonic DNA**: Disney orchestral magic. Keyblade swishes, save-point chimes, heartless dark tones, light piano. The intersection of JRPG systems and Disney warmth. PS2 era: lush, emotional, reverb-heavy. Everything sparkles. Key center around B major / G# minor.

**Frequency range**: 80-5000 Hz
**Instrument palette**: Sine (piano-like clean tones), FM synthesis (crystalline chimes, keyblade ring), filtered noise (magic effects), layered sine chords (orchestral pads), reverb-heavy everything
**Emotional tone**: Orchestral, emotional, epic, warm, magical

| Event | Technique | Frequencies | Duration | Envelope | Character Notes |
|-------|-----------|-------------|----------|----------|-----------------|
| session_start | Save point: ascending crystal chime sequence with warm reverb | FM chimes (carrier at note, mod=note*2, index=1.8): B4 (494), Ds5 (622), Fs5 (740), B5 (988), 100ms each, 40ms gaps. Reverb room=0.5, wet=0.4. Sine pad B3 (247) underneath at 20% | 700ms | Chimes: ADSR A=8ms, D=25ms, S=0.5, R=100ms. Pad: ADSR A=80ms, D=50ms, S=0.4, R=200ms | "Save point reached." B major arpeggio in crystalline FM tones. The warm pad underneath gives it that Kingdom Hearts emotional weight. Long reverb = cathedral feel. |
| session_end | Sanctuary fade: descending warm chord | Sine chord: B4 (494) + Fs4 (370) + Ds4 (311) held 200ms, fading. Reverb room=0.5, wet=0.45 | 500ms | ADSR: A=50ms, D=40ms, S=0.5, R=250ms | "Rest now." B major triad descending in volume. Warm, embracing, peaceful. Like the end of a Dearly Beloved reprise. |
| prompt_ack | MP orb collect: warm bright ping | Sine at C6 (1047) with gentle FM shimmer (mod=C6*1.5, index=0.5) | 110ms | ADSR: A=5ms, D=15ms, S=0.5, R=40ms | "Acknowledged." Warm but brief. Like collecting a small green orb. |
| task_complete | Keyblade strike: metallic whoosh + bright resonance | Sweep 500->2000 Hz sine (60ms, the swing), then FM ring (carrier 1760, mod 2640, index 2.0) 150ms (the impact ring). Reverb room=0.25, wet=0.3 | 250ms | Swing: exp decay 5.0. Ring: ADSR A=3ms, D=30ms, S=0.4, R=120ms | "Strike!" The whoosh is the keyblade arc, the FM ring is the magical metal impact. Bright and satisfying. |
| agent_deploy | Donald/Goofy deployed: warm two-tone send-off | Sine: E5 (659) 80ms, then Gs5 (831) 120ms, 15ms gap. Reverb room=0.3, wet=0.3 | 230ms | ADSR: A=10ms, D=20ms, S=0.6, R=80ms per note | "Go, friends!" Major 3rd ascending. Warm and supportive. The party member is off on their mission. |
| agent_return | Party member returns: descending warm arrival | Sine: Gs5 (831) 80ms, then E5 (659) 120ms with reverb room=0.3, wet=0.3 | 230ms | ADSR: A=10ms, D=20ms, S=0.6, R=80ms per note | "Welcome back." Inverse of deploy. Settling, reassuring. |
| error | Heartless encounter: dark descending tone with distortion | Sawtooth sweep 800->200 Hz (150ms) with LP filter at 1000 Hz, slight distortion (clip at 0.75). Low sine pulse at 100 Hz underneath (80ms) | 250ms | Sweep: exp decay 2.5. Pulse: exp decay 4.0 | "Heartless!" Dark, threatening. The sawtooth distortion is the darkness. The low pulse is the heartbeat. Not harsh — ominous. |
| notification | Light notification: crystalline bell + sparkle | FM bell (carrier 1200, mod 1800, index 2.0) 100ms, then HP noise sparkle (2000-5000 Hz) 80ms at 15% gain | 200ms | Bell: ADSR A=5ms, D=20ms, S=0.4, R=80ms. Sparkle: exp decay 8.0 | "A light calls to you." Bright, magical, attention-worthy. |
| commit | Level-up: orchestral flourish ascending | Layered sine pad: Fs4+B4+Ds5 (370/494/622) 100ms, stepping up to B4+Ds5+Fs5 (494/622/740) 100ms, to Ds5+Fs5+B5 (622/740/988) 200ms. Reverb room=0.4, wet=0.35 | 500ms | Each chord: ADSR A=20ms, D=25ms, S=0.6, R=60ms. Final: S=0.5, R=150ms | "Level up!" Ascending parallel triads. Each step brighter. The final chord held longer with reverb bloom. Emotional peak. |
| permission | Heart's call: sharp crystalline ping pair | FM ping (carrier 1500, mod 2250, index 1.5) x2, 60ms each, 40ms gap | 170ms | Exp decay 8.0 per ping | "Your heart is calling." Sharp but not harsh. Crystalline. Impossible to ignore. |
| compact | Memory compression: descending chime cascade | FM chimes: B5 (988), Fs5 (740), Ds5 (622), B4 (494), 40ms each, 10ms gaps | 210ms | Each: ADSR A=5ms, D=10ms, S=0.5, R=20ms, decreasing volume (0.9, 0.7, 0.5, 0.3) | "Memories reorganizing." Descending B major. Each note fainter. Like memories settling into place. |
| ambient | Destiny Islands: gentle waves + wind chime | Pink noise LP at 500 Hz modulated by 0.1 Hz sine (wave rhythm). Random FM chime (carrier 800-1600 Hz, mod=carrier*2, index=1.0) every 5-8s, reverb room=0.5, wet=0.5 | 30s loop | Waves: constant at 10% vol, amplitude modulated. Chimes: ADSR A=15ms, D=30ms, S=0.3, R=200ms at 8% vol | Waves on the shore, distant wind chimes. Peaceful, nostalgic, warm. Volume 15% of earcons. |

**Variant strategy**: 4 variants per event. FM modulation index ±0.3 (changes timbre color). Reverb wet ±15%. Chord voicing inversion (root vs 1st vs 2nd). Attack time ±30% (harder or softer onset).

---

## 6. Variant Generation

Variants prevent auditory habituation. Each sound event gets 3-7 variants generated from the base recipe by applying controlled random transforms. Variant 01 is always the base recipe unchanged. Variants 02+ apply combinations of the transforms below.

### 6.1 Transform Table

| Technique | Parameter | Range | Effect | Implementation |
|-----------|-----------|-------|--------|----------------|
| Pitch shift | Frequency multiplier | ±50 cents (0.9715x to 1.0293x) | Subtle frequency variation. Same melody, slightly different key | `freq * 2**(cents/1200)` applied to all note frequencies in recipe |
| Time stretch | Duration multiplier | ±10% (0.9x to 1.1x) | Slightly faster/slower. Changes feel, not identity | Multiply all duration values. Resample to maintain pitch if needed, or just adjust note lengths |
| Filter cutoff | LP cutoff shift | ±500 Hz | Brighter or darker overall timbre | Add offset to any LP filter cutoff in recipe |
| Attack variation | Attack time multiplier | ±20% | Snappier (shorter attack) or softer (longer attack) onset | Multiply all ADSR attack values |
| Reverb amount | Wet mix shift | ±30% (relative) | More spacious or more intimate | Add offset to reverb wet parameter, clamped to [0.0, 0.8] |
| Pan position | Stereo position | ±0.15 from center | Slight stereo variation. Sounds come from slightly different directions | Pan primitive with position ±0.15 |
| Gain variation | Overall amplitude | ±2 dB (0.794x to 1.259x) | Louder or softer. Simulates natural performance variation | Multiply final signal, re-normalize if clipping |

### 6.2 Variant Generation Algorithm

```python
import hashlib

VARIANT_SEED_SALT = "claude-voice-v1"

def generate_variants(theme: str, event: str, base_recipe: callable,
                      count: int, rng_seed: int = 42) -> list[np.ndarray]:
    """
    Generate N variants of a sound event.

    Variant 01 is always the unmodified base recipe.
    Variants 02-N apply random combinations of transforms.

    The seed is deterministic: same theme+event+seed = same variants every time.
    """
    variants = []

    # Variant 01: base recipe, no transforms
    variants.append(base_recipe())

    for i in range(1, count):
        # Deterministic seed per variant
        seed_str = f"{VARIANT_SEED_SALT}:{theme}:{event}:{rng_seed}:{i}"
        seed = int(hashlib.sha256(seed_str.encode()).hexdigest()[:8], 16)
        rng = np.random.RandomState(seed)

        # Select which transforms to apply (2-4 per variant)
        num_transforms = rng.randint(2, 5)
        transform_indices = rng.choice(7, size=num_transforms, replace=False)

        # Generate base then apply transforms
        signal = base_recipe()

        for idx in transform_indices:
            if idx == 0:  # pitch shift
                cents = rng.uniform(-50, 50)
                signal = pitch_shift(signal, cents)
            elif idx == 1:  # time stretch
                factor = rng.uniform(0.9, 1.1)
                signal = time_stretch(signal, factor)
            elif idx == 2:  # filter cutoff
                offset = rng.uniform(-500, 500)
                signal = lowpass(signal, max(1000, 3000 + offset))
            elif idx == 3:  # attack variation
                # Re-generate with modified attack (requires recipe params)
                pass  # Handled at recipe level
            elif idx == 4:  # reverb
                wet_offset = rng.uniform(-0.1, 0.1)
                signal = reverb(signal, wet=max(0, min(0.8, 0.3 + wet_offset)))
            elif idx == 5:  # pan
                pos = rng.uniform(-0.15, 0.15)
                # Applied at stereo stage
            elif idx == 6:  # gain
                db_offset = rng.uniform(-2, 2)
                signal = signal * (10 ** (db_offset / 20))

        variants.append(signal)

    return variants
```

### 6.3 Variant Count per Theme

| Theme | Variants per Event | Rationale |
|-------|-------------------|-----------|
| default | 3 | Minimal theme, less personality to vary |
| starcraft | 4 | Digital precision allows moderate variation |
| warcraft | 4 | Organic warmth benefits from variation |
| mario | 4 | Chiptune clarity means variants stay recognizable |
| zelda | 4 | Reverb variation keeps it fresh |
| smash | 4 | Impact variation is naturally satisfying |
| kingdom-hearts | 4 | FM timbre variation is expressive |

---

## 7. Build Pipeline

### 7.1 Script Specification

The generator script `scripts/generate_sounds.py` is a PEP 723 single-file script:

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=2.0", "scipy>=1.12"]
# ///
```

### 7.2 CLI Interface

```
Usage:
  uv run scripts/generate_sounds.py                         # Generate all themes, all events
  uv run scripts/generate_sounds.py --theme starcraft        # Generate one theme
  uv run scripts/generate_sounds.py --theme starcraft mario  # Generate multiple themes
  uv run scripts/generate_sounds.py --event error            # Generate one event across all themes
  uv run scripts/generate_sounds.py --event error commit     # Generate multiple events
  uv run scripts/generate_sounds.py --validate               # Verify all assets meet spec
  uv run scripts/generate_sounds.py --clean                  # Remove all generated assets
  uv run scripts/generate_sounds.py --seed 42                # Specify random seed (default: 42)
  uv run scripts/generate_sounds.py --variants 5             # Override variant count
  uv run scripts/generate_sounds.py --dry-run                # Print what would be generated
```

Arguments:

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--theme` | str (repeatable) | all | Generate only specified theme(s) |
| `--event` | str (repeatable) | all | Generate only specified event(s) |
| `--validate` | flag | false | Validate existing assets instead of generating |
| `--clean` | flag | false | Remove all generated WAV files |
| `--seed` | int | 42 | Master random seed for deterministic output |
| `--variants` | int | per-theme default | Override variant count for all events |
| `--dry-run` | flag | false | Print planned output without generating |
| `--output-dir` | path | `assets/themes/` | Output directory root |

### 7.3 Pipeline Steps

```
1. Parse CLI args
   ├── Determine theme filter (all or specific)
   ├── Determine event filter (all or specific)
   └── Load master seed

2. For each theme in scope:
   ├── Load theme sonic DNA parameters
   ├── Create output directory: assets/themes/{theme}/sounds/
   │
   ├── 3. For each event in scope:
   │   ├── Execute base recipe function
   │   ├── Generate N variants via transform pipeline
   │   │
   │   ├── 4. For each variant:
   │   │   ├── Apply stereo panning (center for base, slight variation for others)
   │   │   ├── Normalize to -14 LUFS target
   │   │   ├── Convert float64 -> int16
   │   │   ├── Write WAV: assets/themes/{theme}/sounds/{event}-{NN}.wav
   │   │   └── Validate: format, duration, peak, RMS, file size
   │   │
   │   └── Report: event name, variant count, duration range, file sizes
   │
   └── Report: theme totals

5. Final validation pass (if --validate):
   ├── Check every expected file exists
   ├── Verify WAV headers (48kHz, 16-bit, stereo)
   ├── Check duration within ±20% of target
   ├── Check peak amplitude (no clipping)
   ├── Check RMS level (-20dB to -6dB)
   ├── Check file size (<200KB per earcon)
   └── Check stereo balance (L/R RMS within 6dB)

6. Summary report:
   ├── Table: theme | event | variants | sizes | durations
   ├── Total files generated
   ├── Total size on disk
   └── Any validation failures
```

### 7.4 Output Directory Structure

```
assets/themes/
├── default/
│   └── sounds/
│       ├── session-start-01.wav
│       ├── session-start-02.wav
│       ├── session-start-03.wav
│       ├── session-end-01.wav
│       ├── ...
│       ├── compact-03.wav
│       └── ambient-loop.wav
├── starcraft/
│   └── sounds/
│       ├── session-start-01.wav
│       ├── session-start-02.wav
│       ├── session-start-03.wav
│       ├── session-start-04.wav
│       ├── ...
│       ├── compact-04.wav
│       └── ambient-loop.wav
├── warcraft/
│   └── sounds/
│       └── (same pattern, 4 variants per event)
├── mario/
│   └── sounds/
│       └── (same pattern, 4 variants per event)
├── zelda/
│   └── sounds/
│       └── (same pattern, 4 variants per event)
├── smash/
│   └── sounds/
│       └── (same pattern, 4 variants per event)
└── kingdom-hearts/
    └── sounds/
        └── (same pattern, 4 variants per event)
```

### 7.5 LUFS Normalization

All sounds target -14 LUFS (Loudness Units relative to Full Scale). This is the standard for notification audio — loud enough to hear clearly, quiet enough to not startle.

```python
def measure_lufs(signal: np.ndarray, sample_rate: int = SAMPLE_RATE) -> float:
    """
    Simplified LUFS measurement (ITU-R BS.1770 approximation).
    For earcons <1s, RMS-based approximation is sufficient.
    """
    rms = np.sqrt(np.mean(signal ** 2))
    if rms == 0:
        return -np.inf
    return 20 * np.log10(rms)

def normalize_lufs(signal: np.ndarray, target_lufs: float = -14.0) -> np.ndarray:
    """Normalize signal to target LUFS level."""
    current = measure_lufs(signal)
    if current == -np.inf:
        return signal
    gain_db = target_lufs - current
    gain_linear = 10 ** (gain_db / 20)
    normalized = signal * gain_linear
    # Prevent clipping
    peak = np.max(np.abs(normalized))
    if peak > 0.98:
        normalized = normalized * (0.98 / peak)
    return normalized
```

---

## 8. Asset Inventory

Expected output after a full `uv run scripts/generate_sounds.py` run:

| Theme | Earcon Events | Variants/Event | Ambient Loops | Total Files | Est. Size |
|-------|--------------|----------------|---------------|-------------|-----------|
| default | 11 | 3 | 1 | 34 | ~400KB |
| starcraft | 11 | 4 | 1 | 45 | ~550KB |
| warcraft | 11 | 4 | 1 | 45 | ~550KB |
| mario | 11 | 4 | 1 | 45 | ~450KB |
| zelda | 11 | 4 | 1 | 45 | ~500KB |
| smash | 11 | 4 | 1 | 45 | ~550KB |
| kingdom-hearts | 11 | 4 | 1 | 45 | ~550KB |
| **Total** | **77** | — | **7** | **304** | **~3.55MB** |

Notes:
- 11 earcon events (session_start through compact) + 1 ambient loop = 12 sound entries per theme
- Ambient loops are generated as a single file (`ambient-loop.wav`), not variants — the looping nature provides its own variation
- File size estimates assume 48kHz 16-bit stereo WAV: ~192KB/s. A 300ms earcon = ~58KB. Actual sizes vary with silence trimming.

Size budget breakdown:
- Earcon (100-800ms): 19-154 KB per file
- Ambient loop (30s): ~5.8 MB per file raw — but ambient loops use lower effective bandwidth (sparse content, lots of near-silence), so practical sizes are similar after LUFS normalization brings quiet sections near the noise floor

If ambient loops push total size beyond 5MB, they can be generated on first run and stored in `~/.claude/local/voice/cache/ambient/` instead of git-tracked in assets. This keeps the git repo lean.

---

## 9. Quality Gates

Every generated sound must pass all quality gates before being written to disk. A failing gate is a generation error, not a warning.

### 9.1 Gate Table

| Gate | Check | Threshold | Failure Action |
|------|-------|-----------|----------------|
| **Format** | WAV header: 48000 Hz, 16-bit, 2 channels | Exact match | Regenerate (bug in writer) |
| **Duration** | Measured duration vs. target from theme recipe | Within ±20% of target | Log warning, adjust recipe |
| **Peak amplitude** | `max(abs(signal))` | 0.0 < peak <= 1.0 (no clipping, no silence) | Normalize or investigate |
| **RMS level** | `20 * log10(rms)` | -20 dB to -6 dB | Normalize to -14 LUFS |
| **File size** | `os.path.getsize()` | Earcon: <200 KB. Ambient: <6 MB | Check duration, investigate |
| **Stereo balance** | `abs(rms_left_dB - rms_right_dB)` | <6 dB (unless intentionally panned) | Check pan parameters |
| **DC offset** | `abs(mean(signal))` | <0.01 | Apply DC removal: `signal - mean(signal)` |
| **Leading/trailing silence** | Samples below -60 dB at start/end | <50ms of silence at start, <100ms at end | Trim excess silence |

### 9.2 Validation Script Mode

`uv run scripts/generate_sounds.py --validate` runs all gates against existing assets without regenerating:

```
Validating assets/themes/starcraft/sounds/...
  session-start-01.wav  OK  (48kHz 16-bit stereo, 647ms, -13.8 LUFS, 124KB)
  session-start-02.wav  OK  (48kHz 16-bit stereo, 658ms, -14.1 LUFS, 126KB)
  session-start-03.wav  OK  (48kHz 16-bit stereo, 635ms, -13.6 LUFS, 122KB)
  session-start-04.wav  OK  (48kHz 16-bit stereo, 669ms, -14.3 LUFS, 128KB)
  ...
  error-01.wav          OK  (48kHz 16-bit stereo, 218ms, -12.9 LUFS, 42KB)

Summary:
  7 themes, 304 files
  0 failures, 0 warnings
  Total size: 3.42 MB
```

---

## 10. Naming Convention

### 10.1 File Naming

```
assets/themes/{theme}/sounds/{event}-{NN}.wav
```

- **theme**: kebab-case directory name matching theme.json `name` field
- **event**: kebab-case matching the semantic token from the event router
- **NN**: zero-padded 2-digit variant number, starting at 01
- **Ambient loops**: `ambient-loop.wav` (single file, no variant number)

### 10.2 Examples

```
assets/themes/starcraft/sounds/session-start-01.wav
assets/themes/starcraft/sounds/session-start-02.wav
assets/themes/starcraft/sounds/session-start-03.wav
assets/themes/starcraft/sounds/session-start-04.wav
assets/themes/starcraft/sounds/task-complete-01.wav
assets/themes/starcraft/sounds/task-complete-02.wav
assets/themes/starcraft/sounds/task-complete-03.wav
assets/themes/starcraft/sounds/task-complete-04.wav
assets/themes/starcraft/sounds/error-01.wav
assets/themes/starcraft/sounds/error-02.wav
assets/themes/starcraft/sounds/error-03.wav
assets/themes/starcraft/sounds/error-04.wav
assets/themes/starcraft/sounds/ambient-loop.wav

assets/themes/mario/sounds/prompt-ack-01.wav
assets/themes/mario/sounds/prompt-ack-02.wav
assets/themes/mario/sounds/prompt-ack-03.wav
assets/themes/mario/sounds/prompt-ack-04.wav
assets/themes/mario/sounds/commit-01.wav

assets/themes/zelda/sounds/notification-01.wav
assets/themes/kingdom-hearts/sounds/session-start-01.wav
assets/themes/default/sounds/task-complete-01.wav
```

### 10.3 Semantic Token to Filename Mapping

| Semantic Token | Filename Stem |
|---------------|---------------|
| session_start | session-start |
| session_end | session-end |
| prompt_ack | prompt-ack |
| task_complete | task-complete |
| agent_deploy | agent-deploy |
| agent_return | agent-return |
| error | error |
| notification | notification |
| commit | commit |
| permission | permission |
| compact | compact |
| ambient | ambient-loop |

The token-to-filename conversion is: replace underscores with hyphens. The Sound Router handles this mapping.

---

## 11. Reference: Musical Intervals for Synthesis

### 11.1 Interval Table

| Interval | Semitones | Frequency Ratio | Cents | Emotional Quality | Primary Use In |
|----------|-----------|-----------------|-------|-------------------|----------------|
| Unison | 0 | 1:1 | 0 | Neutral, reinforcement | prompt_ack (doubled tone) |
| Minor 2nd | 1 | 16:15 | 100 | Tension, dissonance, unease | error (simultaneous pair) |
| Major 2nd | 2 | 9:8 | 200 | Stepping, movement | compact (chromatic descent) |
| Minor 3rd | 3 | 6:5 | 300 | Sad, melancholy, gentle | session_end (some themes) |
| Major 3rd | 4 | 5:4 | 400 | Happy, bright, warm | task_complete, agent_deploy |
| Perfect 4th | 5 | 4:3 | 500 | Open, neutral, medieval | warcraft horns, mario coin (B->E) |
| Tritone | 6 | sqrt(2):1 | 600 | Ominous, unstable, demonic | error variant, heartless |
| Perfect 5th | 7 | 3:2 | 700 | Strong, stable, heroic | notification, zelda fanfare |
| Minor 6th | 8 | 8:5 | 800 | Bittersweet, yearning | kingdom-hearts emotion |
| Major 6th | 9 | 5:3 | 900 | Warm, gentle resolution | session_end resolution |
| Minor 7th | 10 | 9:5 | 1000 | Tension seeking resolution | pre-commit buildup |
| Major 7th | 11 | 15:8 | 1100 | Bright tension, jazz | sparkle accents |
| Octave | 12 | 2:1 | 1200 | Resolution, completeness | session_start cap, zelda navi |

### 11.2 MIDI Note Reference

```python
def midi_to_freq(note: int) -> float:
    """Convert MIDI note number to frequency in Hz. A4 = MIDI 69 = 440 Hz."""
    return 440.0 * 2 ** ((note - 69) / 12)

def freq_to_midi(freq: float) -> int:
    """Convert frequency to nearest MIDI note number."""
    return round(69 + 12 * np.log2(freq / 440.0))

# Common MIDI note numbers:
# C4 = 60, D4 = 62, E4 = 64, F4 = 65, G4 = 67, A4 = 69, B4 = 71
# C5 = 72, D5 = 74, E5 = 76, F5 = 77, G5 = 79, A5 = 81, B5 = 83
```

### 11.3 Pitch Shift by Cents

```python
def cents_to_ratio(cents: float) -> float:
    """Convert cents offset to frequency multiplier. 100 cents = 1 semitone."""
    return 2 ** (cents / 1200)

# Examples:
# +50 cents  -> 1.02930  (slightly sharp)
# -50 cents  -> 0.97153  (slightly flat)
# +100 cents -> 1.05946  (one semitone up)
# -100 cents -> 0.94387  (one semitone down)
```

---

## 12. Open Questions

1. **Ambient loop format**: Should ambient loops use OGG/Opus for smaller file size? The playback spec (05) mandates WAV for earcons, but ambient loops at 30s duration are ~5.8MB each as WAV (7 themes = ~40MB). OGG at quality 4 would reduce this to ~200KB each. `pw-play` supports OGG natively via `libsndfile`. Recommendation: generate ambient loops as OGG, or move them to `~/.claude/local/voice/cache/ambient/` outside the git-tracked assets.

2. **Preview mode**: Should `generate_sounds.py` include a `--preview` flag that plays each sound after generation via `pw-play`? Useful for rapid iteration during theme development. Low implementation cost — just add a `subprocess.Popen(['pw-play', path])` after each write.

3. **Variant count configurability**: Should variant count be per-event in theme.json (e.g., `prompt_ack: 7` variants because it fires most, `session_start: 3` because it fires once), or fixed at generation time via `--variants`? Per-event counts optimize the habituation profile but add complexity. Recommendation: support both — theme.json can specify per-event overrides, `--variants` provides a global default.

4. **User WAV overrides**: Should the sound router check for user-provided WAV files before falling back to generated assets? Pattern: if `~/.claude/local/voice/overrides/{theme}/{event}-01.wav` exists, use it instead. This lets users drop in their own sounds without modifying the plugin. Low implementation cost in the sound router.

5. **Pitch shift implementation**: True pitch shifting (preserving duration while changing frequency) requires resampling or phase vocoder. For earcons, the simpler approach of shifting all recipe frequencies and regenerating is cleaner than post-processing. Should the variant pipeline shift frequencies at the recipe level (more accurate) or apply a post-process pitch shift (simpler code, potential artifacts)?

6. **Ambient loop crossfade**: Ambient loops need seamless looping. Should the generator apply a crossfade to the loop boundaries (fade last 500ms into first 500ms), or should the playback engine handle looping with crossfade? Generator-side is simpler. Playback-side is more flexible.

7. **Deterministic seeding vs. fresh generation**: The current spec uses seed 42 for fully deterministic output. Should there be a `--randomize` mode that generates fresh variants each run? This could be useful for "refreshing" the sound set periodically. The risk is that git diffs become noisy (every WAV changes on regeneration).
