---
title: "claude-voice — State Machine"
created: 2026-03-30
updated: 2026-03-30
author: matt
status: verified
audit: 2026-03-30 (report 03 — state machine analysis)
tags: [claude-voice, state-machine, verification]
note: >
  This document captures what the 2026-03-30 audit found. It is necessarily
  incomplete — the full vision for voice state management spans phases not yet
  designed. Future sessions should extend this, not treat it as final.
---

# claude-voice — State Machine

## State Flags (File-Based IPC)

All coordination between processes uses files in `~/.claude/local/voice/`.
This follows the Legion convention: the filesystem IS the IPC.

### Flag Inventory

| Flag | Path | Semantics | Creator | Consumer | Cleanup |
|------|------|-----------|---------|----------|---------|
| `stt-active` | `~/.claude/local/voice/stt-active` | User is currently speaking — suppress all audio | stt_daemon.py, duplex.py | router.py (line 181) | stt_daemon.py on speech end, finally block on exit |
| `tts-playing` | `~/.claude/local/voice/tts-playing` | System is currently speaking — suppress wake word | voice_queue.py (line 293) | stt_daemon.py wake word loop | voice_queue.py on playback complete (line 278) |
| `focus-state` | `~/.claude/local/voice/focus-state` | Focused pane ID for spatial mixing | tmux hook (**NOT INSTALLED**) | router.py Tier 1 cache (line 39) | Overwritten on each focus change |
| `ambient-count` | `~/.claude/local/voice/ambient-count` | Number of active subagents (for ambient loop) | ambient.py | ambient.py | Should reset on SessionStart — **doesn't** |
| `ambient.pid` | `~/.claude/local/voice/ambient.pid` | PID of ambient pw-play loop process | ambient.py | ambient.py | Should clean on SessionEnd — **buggy** |

### State Transition Diagram

```
                    ┌─────────────┐
                    │    IDLE     │
                    │  (normal)   │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
              v            v            v
     ┌──────────────┐ ┌─────────┐ ┌──────────────┐
     │ TTS_PLAYING  │ │ STT_    │ │ MUTED        │
     │ (queue is    │ │ ACTIVE  │ │ (config or   │
     │  playing)    │ │ (user   │ │  env var)    │
     │              │ │  speaks)│ │              │
     └──────┬───────┘ └────┬────┘ └──────────────┘
            │              │
            │   BARGE-IN   │
            └──────┬───────┘
                   v
          ┌──────────────┐
          │ BARGE_IN     │
          │ (tts killed, │
          │  stt starts) │
          │ IN-PROCESS   │
          │ ONLY         │
          └──────────────┘
```

### Transition Rules

| From | Trigger | To | Actions |
|------|---------|-----|---------|
| IDLE | Hook event | IDLE | Volume pipeline runs, earcon/TTS dispatched |
| IDLE | Queue plays TTS | TTS_PLAYING | `tts-playing` flag created |
| TTS_PLAYING | Playback complete | IDLE | `tts-playing` flag deleted |
| IDLE | Wake word detected | STT_ACTIVE | `stt-active` flag created |
| STT_ACTIVE | Speech ends (VAD) | IDLE | `stt-active` flag deleted, transcript written |
| TTS_PLAYING | Speech detected (VAD) | BARGE_IN | pw-play killed, `stt-active` created |
| BARGE_IN | Speech ends | IDLE | Transcript written, `stt-active` deleted |
| Any | Mute toggle | MUTED | `config.yaml` updated |
| MUTED | Unmute toggle | IDLE | `config.yaml` updated |

## Known Race Conditions

### RC-1: stt-active + tts-playing coexistence (50ms window)

During barge-in, `duplex.py` creates `stt-active` before the queue daemon's 50ms
poll loop detects and clears `tts-playing`. Both flags exist simultaneously for
up to 50ms. **Mitigated**: router.py checks `stt-active` AFTER volume computation,
so the stt-active check wins.

### RC-2: Queue daemon ignores stt-active (CONFIRMED BUG)

The voice queue daemon's advance loop at line 281 has NO check for `stt-active`.
Comment in `duplex.py` line 91 says "Touch stt-active to prevent queue from starting
next item" — **this is false**. Items will dequeue and play during STT.

**Fix**: Add `stt_active = STT_ACTIVE_PATH.exists()` check before dequeue.

### RC-3: Concurrent hook events

Two hooks firing simultaneously (e.g., SubagentStop + Stop) both run volume pipeline
independently. Both may call `play_sound()` — producing overlapping earcons. This is
by design for earcons (short, low overlap risk). TTS goes through the queue, preventing
speech overlap.

## Failure Modes (Stale State)

| Scenario | Stale Flag | Effect | Mitigation |
|----------|-----------|--------|------------|
| stt_daemon crashes during recording | `stt-active` persists | Permanent audio mute | `finally` block in stt_daemon.py cleans up. SIGKILL: startup cleanup at line 76 |
| voice_queue crashes during playback | `tts-playing` persists | Wake word permanently suppressed | **NO MITIGATION** — P0 bug. Fix: clear on startup |
| tts_daemon crashes | `daemon.sock` persists | New TTS requests fail (connection refused) | **NO MITIGATION** — stale socket. Fix: unlink on startup |
| ambient loop crashes | `ambient.pid` is dead, `ambient-count` > 0 | No ambient + can't restart | **LIVE BUG** — fix: validate PID on access |
| tmux hook fails | `focus-state` is stale | Wrong pane gets full volume | Fallback to subprocess query (5ms) |

## Recommendations

1. **Every daemon should clear its flag files on startup** (defensive cleanup)
2. **Flag files should include PID or timestamp** (enables staleness detection)
3. **Queue daemon must check stt-active** (one-line fix, P0)
4. **Consider inotify over polling** for stt-active watch (lower latency, same reliability)
5. **Ambient state should reset on SessionStart** (prevents count drift)
