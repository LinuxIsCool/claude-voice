"""Duplex conversation handling for claude-voice Phase 4.

Manages the interaction between TTS output and STT input:
- When user speaks during TTS → cancel TTS, start STT (barge-in)
- When STT finishes → resume TTS queue
- Coordinates stt-active flag with voice queue abort

Usage:
    duplex = DuplexManager(mic, stt, vad)
    duplex.start()  # Begins monitoring for barge-in
"""
from __future__ import annotations

import os
import signal
from pathlib import Path
from typing import Optional

from constants import STT_ACTIVE_PATH, VOICE_DATA_DIR
from flags import write_flag


class DuplexManager:
    """Manages barge-in: user speech interrupts agent TTS.

    When VAD detects user speech while TTS is playing:
    1. Kill the current pw-play process (cancel TTS)
    2. Set stt-active flag (suppress queued TTS)
    3. Activate STT recording
    4. On speech end: clear flag, transcribe, inject, resume queue
    """

    QUEUE_PID = VOICE_DATA_DIR / "queue.pid"
    TTS_PLAYING = VOICE_DATA_DIR / "tts-playing"

    def __init__(self, stt_engine):
        self.stt = stt_engine
        self.barge_in_active = False

    def on_speech_detected_during_tts(self) -> None:
        """Called when VAD detects speech while TTS is playing.

        This is the barge-in trigger.
        """
        if self.barge_in_active:
            return

        self.barge_in_active = True

        # 1. Cancel current TTS playback
        self._cancel_current_playback()

        # 2. Start STT recording (sets stt-active flag)
        self.stt.start_listening()

    def on_speech_ended(self) -> Optional[str]:
        """Called when VAD detects end of user speech after barge-in.

        Returns transcribed text.
        """
        if not self.barge_in_active:
            return None

        self.barge_in_active = False

        # Transcribe and return
        return self.stt.stop_listening()

    def _cancel_current_playback(self) -> None:
        """Kill the currently playing pw-play process.

        The voice queue daemon tracks the playing process. We can signal
        it to abort, or directly kill the pw-play PID if we can find it.
        """
        # Method 1: Find and kill pw-play processes playing voice cache WAVs
        try:
            import subprocess
            result = subprocess.run(
                ["pgrep", "-f", "pw-play.*voice/cache"],
                capture_output=True, text=True, timeout=1,
            )
            for pid_str in result.stdout.strip().split("\n"):
                if pid_str.strip():
                    try:
                        os.kill(int(pid_str.strip()), signal.SIGTERM)
                    except (ValueError, ProcessLookupError):
                        pass
        except Exception:
            pass

        # Method 2: Write stt-active flag to prevent queue from starting next item
        write_flag(STT_ACTIVE_PATH)
