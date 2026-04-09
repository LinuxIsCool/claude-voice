"""Wake word detection for claude-voice Phase 4.

Listens for "Legion" trigger word using openWakeWord. CPU-only, ~0% overhead.
When triggered, activates the STT pipeline.

The wake word model is a custom ONNX file trained on synthetic speech from
Kokoro-82M. See scripts/train_wake_word.py for training.

Usage:
    wake = WakeWordDetector()
    wake.on_wake = lambda score: print(f"Legion detected! confidence={score:.2f}")
    mic.register(wake.process_chunk)
"""
from __future__ import annotations

import numpy as np
from pathlib import Path
from typing import Callable, Optional

# Model path
MODELS_DIR = Path(__file__).resolve().parent.parent / "assets" / "models"
DEFAULT_MODEL = MODELS_DIR / "legion.onnx"

# Bundled models from openWakeWord (fallback if custom model not trained yet)
BUILTIN_MODELS = ["hey_jarvis"]


class WakeWordDetector:
    """Always-on wake word detector. Runs on CPU at ~0% overhead.

    Processes 80ms audio chunks (int16, 16kHz). When the wake word
    confidence exceeds the threshold, fires the on_wake callback.
    """

    def __init__(
        self,
        model_path: Optional[Path] = None,
        threshold: float = 0.5,
        cooldown_seconds: float = 2.0,
    ):
        """Initialize wake word detector.

        Args:
            model_path: Path to custom ONNX model. None = use default.
                        If custom model doesn't exist, falls back to
                        built-in "hey_jarvis" for testing.
            threshold: Confidence threshold (0.0-1.0) for trigger.
            cooldown_seconds: Minimum seconds between triggers (debounce).
        """
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self.on_wake: Optional[Callable[[float], None]] = None
        self._last_trigger: float = 0.0
        self._model = None
        self._model_name = "legion"

        # Resolve model path
        path = model_path or DEFAULT_MODEL
        if path.exists():
            self._model_paths = [str(path)]
        else:
            # Custom model not trained yet — use built-in for testing
            self._model_paths = None
            self._model_name = BUILTIN_MODELS[0]

    def _ensure_loaded(self) -> None:
        """Lazy-load the model on first use."""
        if self._model is not None:
            return
        from openwakeword.model import Model

        if self._model_paths:
            self._model = Model(wakeword_model_paths=self._model_paths)
        else:
            # Default models include hey_jarvis, alexa, etc.
            self._model = Model()

    def process_chunk(self, chunk_float32: np.ndarray) -> None:
        """Process one audio chunk. Call from mic callback.

        Args:
            chunk_float32: float32 mono audio, shape (N,), range [-1, 1].
        """
        import time

        self._ensure_loaded()

        import numpy as np
        # Clip to [-1, 1] before int16 conversion (high mic gain can exceed 1.0)
        clipped = np.clip(chunk_float32, -1.0, 1.0)
        chunk_int16 = (clipped * 32768).astype(np.int16)

        prediction = self._model.predict(chunk_int16)
        score = prediction.get(self._model_name, 0.0)

        if score > self.threshold:
            now = time.time()
            if now - self._last_trigger >= self.cooldown_seconds:
                self._last_trigger = now
                if self.on_wake:
                    self.on_wake(score)

    @property
    def is_loaded(self) -> bool:
        """Whether the model is loaded."""
        return self._model is not None

    @property
    def model_name(self) -> str:
        """Name of the active model."""
        return self._model_name
