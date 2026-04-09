"""Speech-to-text engine for claude-voice Phase 4.

Two-tier STT:
  Tier 1 (streaming): parakeet_realtime_eou_120m-v1 — fast, EOU-aware, 160ms p50
  Tier 2 (accuracy):  faster-whisper large-v3-turbo — corrects final text, 2.8% WER

Integrates with:
  - Silero VAD for speech boundary detection
  - MicCapture for audio input
  - STT-active flag for TTS suppression during recording

Usage:
    stt = STTEngine()
    stt.on_transcript = lambda text: inject_prompt(text)
    stt.start_listening()  # Call when wake word fires
    # ... Silero VAD detects speech end ...
    stt.stop_listening()   # Transcribes and fires callback
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

from constants import STT_ACTIVE_PATH, WHISPER_ENV
from flags import write_flag, clear_flag

if TYPE_CHECKING:
    import numpy as np


class SileroVADWrapper:
    """Thin wrapper around Silero VAD for streaming use.

    Detects speech start and end from audio chunks.
    """

    def __init__(self, sample_rate: int = 16000):
        self.sample_rate = sample_rate
        self._model = None
        self._vad_iterator = None
        self.on_speech_start: Optional[Callable[[], None]] = None
        self.on_speech_end: Optional[Callable[[], None]] = None
        self._speech_active = False

    # Silero VAD requires exactly 512 samples at 16kHz (32ms chunks)
    SILERO_CHUNK = 512

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        from silero_vad import load_silero_vad, VADIterator
        self._model = load_silero_vad()
        self._vad_iterator = VADIterator(
            self._model,
            sampling_rate=self.sample_rate,
            threshold=0.5,
            min_silence_duration_ms=800,   # 800ms silence to confirm end
            speech_pad_ms=100,
        )
        self._carry = __import__('numpy').array([], dtype=__import__('numpy').float32)

    def process_chunk(self, chunk_float32) -> None:
        """Process audio chunk through VAD. Rechunks to 512 samples as required by Silero."""
        import numpy as np
        self._ensure_loaded()

        # Accumulate samples with carry buffer for non-512-aligned inputs
        combined = np.concatenate([self._carry, chunk_float32])
        i = 0
        while i + self.SILERO_CHUNK <= len(combined):
            sub = combined[i:i + self.SILERO_CHUNK]
            speech_dict = self._vad_iterator(sub)
            if speech_dict:
                if "start" in speech_dict and not self._speech_active:
                    self._speech_active = True
                    if self.on_speech_start:
                        self.on_speech_start()
                if "end" in speech_dict and self._speech_active:
                    self._speech_active = False
                    if self.on_speech_end:
                        self.on_speech_end()
            i += self.SILERO_CHUNK
        self._carry = combined[i:]

    def reset(self) -> None:
        """Reset VAD state for new utterance."""
        if self._vad_iterator:
            self._vad_iterator.reset_states()
        self._speech_active = False
        try:
            import numpy as np
            self._carry = np.array([], dtype=np.float32)
        except ImportError:
            self._carry = None  # numpy not available (testing outside stt-env)

    @property
    def is_speaking(self) -> bool:
        return self._speech_active


class STTEngine:
    """Two-tier speech-to-text engine.

    Tier 1: Fast streaming transcription (Parakeet or faster-whisper)
    Tier 2: Accuracy re-transcription (faster-whisper large-v3-turbo)
    """

    def __init__(self):
        self.on_transcript: Optional[Callable[[str], None]] = None
        self.on_partial: Optional[Callable[[str], None]] = None
        self.audio_buffer: list[np.ndarray] = []
        self.listening = False
        self.vad = SileroVADWrapper()
        self._stt_model = None

    def start_listening(self) -> None:
        """Begin recording. Called when wake word fires or PTT key pressed."""
        self.listening = True
        self.audio_buffer = []
        self.vad.reset()
        # Set STT-active flag — suppresses all TTS
        write_flag(STT_ACTIVE_PATH)

    def stop_listening(self) -> Optional[str]:
        """Stop recording, transcribe, return text. Clears STT-active flag."""
        import numpy as np

        self.listening = False
        clear_flag(STT_ACTIVE_PATH)

        if not self.audio_buffer:
            return None

        audio = np.concatenate(self.audio_buffer)
        self.audio_buffer = []

        # Transcribe
        text = self._transcribe(audio)
        if text and self.on_transcript:
            self.on_transcript(text)
        return text

    def buffer_audio(self, chunk) -> None:
        """Buffer audio while listening. Call from mic callback.

        Buffer is capped at ~60s to prevent unbounded growth.
        """
        if self.listening:
            self.audio_buffer.append(chunk)
            # Cap at ~60s of audio (750 chunks × 80ms)
            if len(self.audio_buffer) > 750:
                self.audio_buffer = self.audio_buffer[-750:]

    # Class-level model cache — loaded once, kept warm across transcriptions
    _whisper_model = None

    @classmethod
    def _get_whisper_model(cls):
        """Load faster-whisper model once, keep in VRAM for fast re-transcription."""
        if cls._whisper_model is not None:
            return cls._whisper_model
        try:
            from faster_whisper import WhisperModel
            cls._whisper_model = WhisperModel(
                "large-v3-turbo", device="cuda", compute_type="float16"
            )
        except (ImportError, Exception):
            return None
        return cls._whisper_model

    def _transcribe(self, audio) -> Optional[str]:
        """Transcribe audio buffer using faster-whisper.

        Fast path (~120ms): uses warm model loaded in this process.
        Slow fallback (~3-8s): subprocess with fresh model load.
        """
        import numpy as np

        if len(audio) < 1600:  # Less than 0.1s — too short
            return None

        # Write audio to temp WAV
        try:
            import soundfile as sf
        except ImportError:
            sf = None

        wav_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                wav_path = f.name
                if sf:
                    sf.write(f.name, audio, 16000, subtype="PCM_16")
                else:
                    # Fallback: write raw WAV manually
                    import wave
                    with wave.open(f.name, 'wb') as w:
                        w.setnchannels(1)
                        w.setsampwidth(2)
                        w.setframerate(16000)
                        w.writeframes((audio * 32768).astype(np.int16).tobytes())

            # Fast path: warm model in this process
            model = self._get_whisper_model()
            if model is not None:
                segments, info = model.transcribe(wav_path, beam_size=5, language="en")
                text = " ".join(seg.text.strip() for seg in segments)
                return text.strip() or None

            # Slow fallback: subprocess
            import subprocess
            from constants import WHISPER_ENV
            script = (
                "import warnings; warnings.filterwarnings('ignore'); "
                "from faster_whisper import WhisperModel; "
                "model = WhisperModel('large-v3-turbo', device='cuda', compute_type='float16'); "
                f"segments, info = model.transcribe('{wav_path}', beam_size=5, language='en'); "
                "text = ' '.join(seg.text.strip() for seg in segments); "
                "print(text)"
            )
            result = subprocess.run(
                [str(WHISPER_ENV), "-c", script],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return result.stdout.strip() or None
        except Exception:
            pass
        finally:
            if wav_path:
                Path(wav_path).unlink(missing_ok=True)

        return None
