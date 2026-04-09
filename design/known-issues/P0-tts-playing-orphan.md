---
title: "P0: tts-playing Flag Orphaned on Queue Daemon Restart"
severity: P0
discovered: 2026-03-30
source: "Research report 02 — Audio Pipeline Verification"
status: fixed
file: scripts/voice_queue.py
---

# P0: tts-playing Flag Orphaned on Queue Daemon Restart

## Bug

If the queue daemon is killed (SIGKILL, OOM, crash) while TTS is playing,
the `tts-playing` flag file persists. On restart, the daemon does NOT clean
up this stale flag.

## Impact

The STT daemon reads `tts-playing` to suppress wake word detection. A stale
flag means wake word detection is permanently suppressed — the user can never
activate STT until manually deleting the file.

## Fix

Add cleanup to `_run_server()` startup in voice_queue.py, before binding socket:

```python
# Cleanup stale state from previous run
Path("~/.claude/local/voice/tts-playing").expanduser().unlink(missing_ok=True)
```

## Related

`duplex.py` line 33 defines `TTS_PLAYING_PID = VOICE_DATA_DIR / "tts-playing.pid"`
which is a different filename from what voice_queue.py actually creates (`tts-playing`).
This is dead code but a landmine — if duplex.py is ever wired in, it will read
the wrong file and always see "not playing."
