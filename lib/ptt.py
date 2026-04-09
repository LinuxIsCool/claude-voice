"""Push-to-talk for claude-voice Phase 4.

Simple alternative to wake word: user holds a key, audio records,
user releases, audio transcribes. No wake word detection needed.

Integrates with MicCapture and STTEngine.

Usage:
    ptt = PushToTalk(mic, stt)
    ptt.start()   # Key pressed
    # ... user speaks ...
    text = ptt.stop()   # Key released → transcribe
"""
from __future__ import annotations

from typing import Optional


class PushToTalk:
    """Push-to-talk recording state machine.

    start() begins buffering audio and sets stt-active flag.
    stop() transcribes the buffer and clears the flag.
    """

    def __init__(self, stt_engine):
        """Initialize with an STTEngine instance for transcription."""
        self.stt = stt_engine
        self.recording = False

    def start(self) -> None:
        """Start recording. Called when PTT key is pressed."""
        if self.recording:
            return
        self.recording = True
        self.stt.start_listening()

    def stop(self) -> Optional[str]:
        """Stop recording and transcribe. Called when PTT key is released.

        Returns transcribed text, or None if too short / failed.
        """
        if not self.recording:
            return None
        self.recording = False
        return self.stt.stop_listening()

    @property
    def is_recording(self) -> bool:
        return self.recording
