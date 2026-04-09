---
title: "ADR-002: File Flags as Inter-Process Communication"
date: 2026-03-27
status: accepted
author: matt
---

# ADR-002: File Flags as Inter-Process Communication

## Context

Voice has multiple processes that need to coordinate: hook processes (transient),
TTS daemon, queue daemon, STT daemon, ambient loop. They need to signal states
like "user is speaking" and "system is playing TTS."

## Decision

Use file flags (touch/unlink) in `~/.claude/local/voice/` for state coordination.
`stt-active` exists when user speaks. `tts-playing` exists when TTS plays.

## Rationale

- Matches Legion convention — filesystem IS the IPC throughout the system
- No additional dependencies (no message queue, no shared memory)
- Debuggable — `ls -la ~/.claude/local/voice/` shows all state
- Any process can read without coupling to the writer
- Any tool can create/delete flags for manual control

## Risks

- Stale flags on process crash (mitigated by cleanup on startup)
- No atomicity guarantees (mitigated by check order in consumers)
- Polling latency (50ms in queue daemon — acceptable for audio timing)

## Alternatives Considered

- **Unix signals** (SIGUSR1/SIGUSR2): Requires PID knowledge, tight coupling
- **Named pipes**: Blocking, complex lifecycle
- **Shared memory**: Overkill, portability concerns
- **D-Bus**: Heavy dependency for simple boolean state
