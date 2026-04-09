---
title: "P0: Queue Daemon Ignores stt-active Flag"
severity: P0
discovered: 2026-03-30
source: "Research report 03 — State Machine Analysis"
status: fixed
file: scripts/voice_queue.py
line: 281
---

# P0: Queue Daemon Ignores stt-active Flag

## Bug

`voice_queue.py` line 281 advances the queue without checking if `stt-active` exists.
The comment in `duplex.py` line 91 says "Touch stt-active to prevent queue from
starting next item" — this claim is FALSE.

## Impact

When STT is active (user speaking), queued TTS items will still play, contaminating
the microphone input. This defeats the entire purpose of the stt-active flag.

## Fix

Add one line to the dequeue condition in voice_queue.py:

```python
# Line 281, change:
if playing_proc is None and queue.heap and time.monotonic() >= transition_until:

# To:
stt_active = Path("~/.claude/local/voice/stt-active").expanduser().exists()
if playing_proc is None and queue.heap and not stt_active and time.monotonic() >= transition_until:
```

## Testing

1. Touch `~/.claude/local/voice/stt-active`
2. Trigger a Stop event with TTS
3. Verify no playback occurs
4. Remove stt-active
5. Verify queued item plays
