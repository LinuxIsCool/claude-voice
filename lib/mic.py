"""Microphone capture for claude-voice Phase 4.

Continuous audio capture via sounddevice (PipeWire native on CachyOS).
Feeds audio chunks to registered callbacks: wake word detector, VAD, STT.

The capture runs on its own thread (via sounddevice's PortAudio callback).
Consumers register via mic.register(callback) and receive numpy float32 chunks.

Usage:
    mic = MicCapture()
    mic.register(on_audio)
    mic.start()
    # ... mic.stop() on cleanup
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# STT constants
STT_SAMPLE_RATE = 16000
"""Sample rate for STT pipeline (Parakeet, openWakeWord, Silero VAD all use 16kHz)."""

CHUNK_MS = 80
"""Chunk size in milliseconds. 80ms is optimal for openWakeWord."""

CHUNK_FRAMES = int(STT_SAMPLE_RATE * CHUNK_MS / 1000)
"""Number of frames per chunk (1280 at 16kHz/80ms)."""

# Type alias for audio callbacks (Any instead of np.ndarray to avoid numpy import)
AudioCallback = Callable


class MicCapture:
    """Continuous microphone capture with callback routing.

    Audio is captured at 16kHz mono float32 in 80ms chunks.
    Registered callbacks receive each chunk as a numpy array.
    """

    def __init__(self, device: Optional[int] = None):
        """Initialize mic capture.

        Args:
            device: sounddevice device index. None = system default (PipeWire).
                    Set to AEC Source index for echo-cancelled input.
        """
        self.device = device
        self.stream = None
        self.callbacks: list[AudioCallback] = []
        self.buffer: deque[np.ndarray] = deque(maxlen=1000)  # ~80s of audio
        self.running = False

    def start(self) -> None:
        """Start capturing audio from the microphone."""
        if self.running:
            return

        import sounddevice as sd

        self.stream = sd.InputStream(
            samplerate=STT_SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=CHUNK_FRAMES,
            callback=self._audio_callback,
            device=self.device,
        )
        self.stream.start()
        self.running = True

    def stop(self) -> None:
        """Stop capturing audio."""
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.running = False

    def register(self, callback: AudioCallback) -> None:
        """Register a callback to receive audio chunks.

        Callback signature: fn(chunk: np.ndarray) → None
        chunk is float32 mono, shape (CHUNK_FRAMES,), range [-1.0, 1.0].
        """
        self.callbacks.append(callback)

    def unregister(self, callback: AudioCallback) -> None:
        """Remove a previously registered callback."""
        try:
            self.callbacks.remove(callback)
        except ValueError:
            pass

    def get_recent_audio(self, seconds: float = 5.0) -> np.ndarray:
        """Get the last N seconds of captured audio as a contiguous array.

        Useful for re-transcription: after EOU, grab the buffered audio
        and feed it to faster-whisper for accuracy correction.
        """
        chunks_needed = int(seconds * 1000 / CHUNK_MS)
        recent = list(self.buffer)[-chunks_needed:]
        if not recent:
            return np.array([], dtype=np.float32)
        return np.concatenate(recent)

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        """PortAudio callback — runs on audio thread. Must be fast."""
        chunk = indata[:, 0].copy()  # float32 mono, shape (CHUNK_FRAMES,)
        self.buffer.append(chunk)
        for cb in self.callbacks:
            try:
                cb(chunk)
            except Exception:
                pass  # Never crash the audio thread
