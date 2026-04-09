---
title: "Phase 4: STT + Conversation — Complete Implementation Plan"
status: active
created: 2026-03-27
author: matt
session: matt:56
phase: "4"
depends_on: Phase 3.5 (all layers complete)
tags: [plan, phase-4, stt, conversation, parakeet, wake-word, aec, duplex]
---

# Phase 4: STT + Conversation

## Vision

Shawn speaks and the system hears. "Legion, what's the status?" → wake word
detected → speech transcribed → text injected into Claude Code → response
spoken back. The full loop: voice in, voice out.

---

## Architecture

```
Microphone (16kHz mono via sounddevice)
    │
    ▼
openWakeWord ("Legion" detector, CPU, ~0% overhead)
    │  [trigger detected]
    ▼
Silero VAD (voice activity detection, CPU, <1ms)
    │  [speech start → buffer audio]
    │  [speech end → flush buffer to STT]
    ▼
parakeet_realtime_eou_120m-v1 (streaming ASR, ~500MB VRAM, 160ms p50)
    │  [<EOU> token → utterance complete]
    ▼
faster-whisper large-v3-turbo (accuracy re-transcription, ~3.2GB VRAM)
    │  [corrected text]
    ▼
Claude Code prompt injection (via stdin or /voice API)
    │
    ▼
TTS response (existing Kokoro pipeline → voice queue → speech)
```

### Two-Tier STT Strategy

| Tier | Model | Params | VRAM | Latency | WER | Purpose |
|------|-------|--------|------|---------|-----|---------|
| 1 (streaming) | parakeet_realtime_eou_120m-v1 | 120M | ~500MB | 160ms p50 | 9.3% | Live transcription + EOU detection |
| 2 (accuracy) | faster-whisper large-v3-turbo | 809M | ~3.2GB | ~120ms | 2.8% | Re-transcribe final utterance for accuracy |

Tier 1 gives instant feedback (you see words appearing as you speak).
Tier 2 corrects the final result after EOU (better accuracy for the actual prompt).
faster-whisper is already installed in `whisperx-env` — no download needed.

### VRAM Budget (RTX 4070 12GB)

| Component | VRAM | Status |
|-----------|------|--------|
| Kokoro-82M TTS | ~555MB | Running (systemd) |
| parakeet_realtime_eou_120m | ~500-800MB | Phase 4 |
| faster-whisper large-v3-turbo | ~3.2GB | Phase 4 (lazy load) |
| Silero VAD | 0 (CPU) | Phase 4 |
| openWakeWord | 0 (CPU) | Phase 4 |
| **Total** | **~4.3-4.6GB** | 7+ GB headroom |

---

## Layers

### Layer 0: Audio Capture Pipeline (2 hours)

**Goal**: Continuous microphone capture feeding a processing pipeline.

**Files**:
| File | Action |
|------|--------|
| `lib/mic.py` | **CREATE** — sounddevice capture, ring buffer, callback routing |
| `lib/constants.py` | **MODIFY** — add STT paths and sample rate constants |

**Implementation**:

```python
# lib/mic.py
import sounddevice as sd
import numpy as np
from collections import deque

SAMPLE_RATE = 16000
CHUNK_MS = 80  # openWakeWord optimal
CHUNK_FRAMES = int(SAMPLE_RATE * CHUNK_MS / 1000)  # 1280

class MicCapture:
    def __init__(self, device=None):
        self.buffer = deque(maxlen=1000)  # ~80 seconds of audio
        self.callbacks = []  # registered consumers
        self.stream = None
        self.device = device  # None = default PipeWire input

    def start(self):
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype='float32',
            blocksize=CHUNK_FRAMES,
            callback=self._audio_callback,
            device=self.device,
        )
        self.stream.start()

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()

    def _audio_callback(self, indata, frames, time, status):
        chunk = indata[:, 0].copy()  # float32 mono
        self.buffer.append(chunk)
        for cb in self.callbacks:
            cb(chunk)

    def register(self, callback):
        self.callbacks.append(callback)
```

**AEC Integration** (optional, Layer 4):

If PipeWire AEC is configured, set `device` to the AEC Source node instead
of the raw microphone. This gives the pipeline cleaned audio with TTS bleed
removed.

### Layer 1: Wake Word Detection (2 hours)

**Goal**: "Legion" triggers the STT pipeline. Always-on, near-zero CPU.

**Files**:
| File | Action |
|------|--------|
| `lib/wake.py` | **CREATE** — openWakeWord integration |
| `scripts/train_wake_word.py` | **CREATE** — generate "Legion" training data + train model |
| `assets/models/legion.onnx` | **GENERATED** — custom wake word model (~200KB) |

**Training the "Legion" wake word**:

1. Generate 500+ synthetic utterances of "Legion" using Kokoro-82M (varied voices, speeds)
2. Generate negative examples (random speech, silence, similar words)
3. Train the openWakeWord MLP classifier on Google's frozen speech embedding backbone
4. Export as ONNX (~200KB)
5. Test false positive rate against 1 hour of ambient audio

```python
# lib/wake.py
from openwakeword.model import Model

class WakeWordDetector:
    def __init__(self, model_path="assets/models/legion.onnx", threshold=0.5):
        self.model = Model(wakeword_models=[model_path])
        self.threshold = threshold
        self.on_wake = None  # callback

    def process_chunk(self, audio_chunk_int16):
        prediction = self.model.predict(audio_chunk_int16)
        score = prediction.get("legion", 0)
        if score > self.threshold and self.on_wake:
            self.on_wake(score)
```

**Integration with mic.py**:

```python
mic = MicCapture()
wake = WakeWordDetector()
wake.on_wake = lambda score: activate_stt_pipeline()

def on_audio(chunk_float32):
    chunk_int16 = (chunk_float32 * 32768).astype(np.int16)
    wake.process_chunk(chunk_int16)

mic.register(on_audio)
mic.start()
```

### Layer 2: VAD + Streaming STT (3 hours)

**Goal**: After wake word, detect speech boundaries and transcribe in real-time.

**Files**:
| File | Action |
|------|--------|
| `lib/stt.py` | **CREATE** — Parakeet streaming + Silero VAD integration |
| `scripts/stt_daemon.py` | **CREATE** — long-running STT service (like TTS daemon) |

**Architecture**:

```
Wake word detected
    → touch stt-active (suppress all TTS)
    → Start Silero VAD monitoring
    → Start Parakeet streaming inference
    → Show partial transcripts (optional: via statusline)
    → <EOU> token detected
    → Stop VAD, remove stt-active
    → Flush audio buffer to faster-whisper for accuracy correction
    → Inject corrected text into Claude Code
```

**Silero VAD integration**:

```python
from silero_vad import load_silero_vad, VADIterator

model = load_silero_vad()  # ~2MB, loads in <100ms
vad = VADIterator(model, sampling_rate=16000)

def on_audio(chunk):
    speech_dict = vad(chunk)
    if speech_dict and 'start' in speech_dict:
        start_recording()
    if speech_dict and 'end' in speech_dict:
        stop_recording_and_transcribe()
```

**Parakeet streaming**:

```python
import nemo.collections.asr as nemo_asr

# Load once (lazy, on first STT activation)
asr = nemo_asr.models.EncDecRNNTBPEModel.from_pretrained(
    "nvidia/parakeet_realtime_eou_120m-v1"
)

# Chunked inference on buffered audio
output = asr.transcribe([audio_buffer_path])
text = output[0].text
# Check for <EOU> token
if "<EOU>" in text:
    finalize_utterance(text.replace("<EOU>", "").strip())
```

**STT-active flag integration**:

The flag at `~/.claude/local/voice/stt-active` was built in Phase 3.5 Layer 2.
When STT activates: `touch stt-active`. When STT deactivates: `rm stt-active`.
All TTS hooks already check this flag and suppress audio while it exists.

### Layer 3: Push-to-Talk Mode (1 hour)

**Goal**: Keybind-triggered recording as a simpler alternative to wake word.

**Files**:
| File | Action |
|------|--------|
| `lib/ptt.py` | **CREATE** — push-to-talk state machine |

**Implementation**:

Push-to-talk is simpler than wake word: user holds a key, audio records,
user releases, audio transcribes. No wake word detection, no VAD needed
(the key press IS the voice activity signal).

```python
class PushToTalk:
    def __init__(self, mic, stt):
        self.mic = mic
        self.stt = stt
        self.recording = False
        self.audio_buffer = []

    def start_recording(self):
        self.recording = True
        self.audio_buffer = []
        touch_stt_active()

    def stop_recording(self):
        self.recording = False
        remove_stt_active()
        audio = np.concatenate(self.audio_buffer)
        return self.stt.transcribe(audio)

    def on_audio(self, chunk):
        if self.recording:
            self.audio_buffer.append(chunk)
```

Integration with Claude Code's existing `/voice` keybind (spacebar hold)
or a custom keybind via the plugin's hook system.

### Layer 4: Acoustic Echo Cancellation (1 hour)

**Goal**: Remove TTS output from microphone input so STT doesn't transcribe Kokoro.

**Files**:
| File | Action |
|------|--------|
| `~/.config/pipewire/pipewire.conf.d/99-voice-aec.conf` | **CREATE** — PipeWire AEC module |
| `scripts/voice-aec-setup.sh` | **CREATE** — setup script (needs Shawn to run) |

**PipeWire config**:

```
context.modules = [
  { name = libpipewire-module-echo-cancel
    args = {
      capture.props = {
        node.name = "Voice AEC Capture"
      }
      source.props = {
        node.name = "Voice AEC Source"
        node.description = "Clean Mic for STT (echo cancelled)"
      }
      sink.props = {
        node.name = "Voice AEC Sink"
        node.description = "TTS Playback (cancellation reference)"
      }
      playback.props = {
        node.name = "Voice AEC Playback"
      }
    }
  }
]
```

**Wiring**:
- TTS daemon routes playback through "Voice AEC Sink" (so AEC has the reference signal)
- STT daemon reads from "Voice AEC Source" (cleaned mic, TTS bleed removed)
- `mic.py` device set to AEC Source node index

This is the hardware-level solution to cross-talk. Phase 3.5's STT-active flag
is the software-level solution. Both can coexist — belt and suspenders.

### Layer 5: Barge-In / Interrupt Handling (2 hours)

**Goal**: When the user speaks while TTS is playing, cancel TTS and start STT.

**Files**:
| File | Action |
|------|--------|
| `lib/duplex.py` | **CREATE** — interrupt detection and TTS cancellation |

**Architecture**:

```
TTS playing (via voice queue)
    │
User starts speaking (VAD detects speech)
    │
    ├── Cancel current TTS playback (kill pw-play PID)
    ├─�� Touch stt-active (suppress queued TTS)
    ├── Start STT recording
    │
    ▼
User finishes speaking (<EOU>)
    │
    ├── Remove stt-active
    ├── Transcribe + inject
    └── Queue resumes (next item plays)
```

This follows the Nvidia ACE pattern: cache reset on barge-in. The Parakeet
EOU model handles this natively — when it detects the user interrupting,
it emits `<EOU>` for the interrupted utterance and resets encoder state.

The voice queue daemon already supports `abort` semantics — a CRITICAL
priority item can preempt current playback. User speech is CRITICAL.

### Layer 6: STT Daemon + systemd (1 hour)

**Goal**: Long-running STT service managed by systemd, like TTS and queue daemons.

**Files**:
| File | Action |
|------|--------|
| `scripts/stt_daemon.py` | **CREATE** — STT service (Parakeet loaded, mic capture, wake word) |
| `~/.config/systemd/user/voice-stt.service` | **CREATE** — systemd unit |

The STT daemon keeps Parakeet and the wake word model loaded in memory.
It captures audio continuously, runs wake word detection, and on trigger
activates the full STT pipeline. Results are communicated to Claude Code
via file injection or the matrix IPC.

Add to `voice.target` so all three daemons start together:
```ini
Wants=voice-tts.service voice-queue.service voice-stt.service ...
```

---

## Environment Setup

### New Python environment

```bash
# Create dedicated STT env (NeMo has heavy deps, isolate from Kokoro env)
python3 -m venv ~/.local/share/stt-env
source ~/.local/share/stt-env/bin/activate
pip install nemo_toolkit['asr'] sounddevice openwakeword silero-vad numpy
```

Or install into existing `whisperx-env` if dep conflicts are minimal.

### Model downloads

```bash
# Parakeet EOU (streaming, ~500MB)
python3 -c "import nemo.collections.asr as asr; asr.models.EncDecRNNTBPEModel.from_pretrained('nvidia/parakeet_realtime_eou_120m-v1')"

# faster-whisper large-v3-turbo — already downloaded in whisperx-env
```

### Wake word training

```bash
cd plugins/claude-voice
uv run scripts/train_wake_word.py  # Generates legion.onnx from synthetic speech
```

---

## Implementation Schedule

| Session | Layers | Effort | Deliverable |
|---------|--------|--------|-------------|
| 1 | Layer 0 + Layer 1 | 4h | Mic capture + wake word ("Legion") |
| 2 | Layer 2 | 3h | VAD + streaming STT (Parakeet + faster-whisper) |
| 3 | Layer 3 + Layer 4 | 2h | Push-to-talk + PipeWire AEC |
| 4 | Layer 5 + Layer 6 | 3h | Barge-in + STT daemon + systemd |

**Total: ~12 hours across 4 sessions.**

---

## Exit Criteria

- [ ] "Legion, what's the status?" → transcribed and injected as prompt within 500ms
- [ ] Push-to-talk: hold key → speak → release → text appears within 300ms
- [ ] Barge-in: speaking during TTS → TTS cancels → STT captures speech
- [ ] AEC: Kokoro TTS output does not appear in STT transcription
- [ ] Wake word false positive rate < 1 per hour
- [ ] STT accuracy: >95% on conversational English (faster-whisper re-transcription)
- [ ] VRAM: all models coexist on RTX 4070 12GB
- [ ] STT daemon managed by systemd (auto-start, auto-restart)
- [ ] stt-active flag correctly suppresses all TTS during recording

---

## Dependency Map

```
Phase 3.5 (complete)
    │
    ├── STT-active flag (Layer 2) ──── Phase 4 sets/clears this
    ├── Voice queue abort ──────────── Phase 4 barge-in uses this
    ├── Spatial mixer ──────────────── AEC source replaces raw mic
    └── systemd services ──────────── Phase 4 adds voice-stt.service
```

---

## Risk Register

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| NeMo install conflicts with Kokoro env | Medium | Medium | Separate stt-env venv |
| Parakeet VRAM + Kokoro VRAM too much | Low | High | Lazy load Parakeet only when wake word fires |
| Wake word false positives | Medium | Low | Tune threshold, test against ambient audio |
| AEC quality insufficient | Medium | Medium | STT-active flag as software backup |
| Barge-in feels laggy | Medium | Medium | Cancel TTS immediately on VAD speech start |
| Claude Code doesn't accept injected prompts | Medium | High | Test injection method early (Layer 2) |

---

## What This Unlocks

| Phase | What Phase 4 Enables |
|-------|---------------------|
| 5 (Personality) | Voice-activated persona switching ("Legion, be Darren") |
| 6 (Integration) | Voice-driven rhythms commands, observatory queries |
| 7 (Autonomy) | "Fix the login bug" → intent parse → dispatch → narrate |

Phase 4 turns the terminal from "Legion talks to you" into "you talk to Legion."
Phase 7 makes it "you talk, Legion acts."
