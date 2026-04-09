---
title: "P1: SessionStart Greeting Bypasses Queue"
severity: P1
discovered: 2026-03-30
source: "Research report 02 — Audio Pipeline Verification"
status: fixed
file: lib/router.py
function: _play_cached_greeting()
---

# P1: SessionStart Greeting Bypasses Queue

## Bug

`_play_cached_greeting()` calls `play_sound()` directly instead of routing
through the queue daemon. Two consequences:

1. The `tts-playing` flag is NOT set during greeting playback
2. If the user says the wake word during the greeting, STT activates over it

## Impact

Minor until STT is fully operational. Becomes P0 when wake word detection
is always-on.

## Fix

Route greeting through `queue_client.enqueue_speech()` instead of direct
`play_sound()`. The greeting WAV is already cached, so it's just a path change.
