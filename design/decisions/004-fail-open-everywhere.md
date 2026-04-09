---
title: "ADR-004: Fail Open Everywhere"
date: 2026-03-26
status: accepted
author: matt
---

# ADR-004: Fail Open Everywhere

## Context

Voice hooks run in the Claude Code pipeline. A crash or hang blocks the user's
terminal for the hook timeout duration (up to 10 seconds).

## Decision

Every failure mode defaults to "play audio normally" rather than "be silent."
Every hook exits 0. Every exception is caught. Every missing dependency is skipped.

## Rationale

- False positive (unexpected sound) is annoying but harmless
- False negative (blocked terminal) is catastrophic for workflow
- Silence from a bug is hard to debug ("is it broken or just muted?")
- Sound from a bug is immediately noticeable and diagnosable

## Implementation

- `voice_event.py`: outer try/except catches everything, always prints `{}`
- `router.py`: `route_event()` wraps `_route_event_inner()` in try/except
- `_get_focus_state()`: returns "no_tmux" (full volume) on any error
- `queue_client.py`: returns None on socket error -> caller plays directly
- `tts.py`: speak_via_daemon falls back to subprocess on socket error

## Exception: STT Suppression

`stt-active` flag causes `effective = 0.0` — this is fail-CLOSED for audio
during speech input. This is correct: playing audio over the user's microphone
is worse than silence.
