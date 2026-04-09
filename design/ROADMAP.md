---
title: "claude-voice — Roadmap (2026-03-30)"
created: 2026-03-30
updated: 2026-03-30
author: matt
status: active
audit: 2026-03-30 (5-agent research, 5 verification reports, design directory)
tags: [claude-voice, roadmap, plan]
note: >
  This roadmap builds on what was verified on 2026-03-30. It describes
  the path forward from current state. Earlier roadmaps (ROADMAP.md,
  ROADMAP2.md in root) are historical. This is the active plan.

  Future sessions should update this file, not create new roadmap files.
  The design/ directory is the living source of truth.
---

# claude-voice — Roadmap

## Principles

Every phase follows the same pattern: **fix, harden, extend**.
Each phase is self-contained — if we stop after any phase, the system is better than before.

| Principle | Application |
|-----------|-------------|
| **Reliability first** | Fix known bugs before adding features |
| **Parsimony** | Smallest change that solves the problem |
| **Self-similarity** | Same patterns at every scale (file-as-IPC, fail-open, config-driven) |
| **Completeness** | Every phase leaves the system in a working, documented, tested state |
| **Simplicity** | One mechanism per concern. No redundant systems |
| **Clarity** | If you can't explain it in one sentence, redesign it |

## Current State Summary

**What works**: 10 hook events -> earcons (7 themes, 259 WAVs). Kokoro-82M TTS via daemon.
Voice queue daemon with priority scheduling and speaker transitions. Spatial volume mixer
(infrastructure live, config set to hear-all). Dual JSONL+SQLite event logging. 49/49 tests pass.

**What's broken**: 2 P0 bugs (queue ignores stt-active, tts-playing orphan on crash).
1 live P1 (ambient state drift). Stale root ARCHITECTURE.md. STT daemon has no systemd unit.
DuplexManager never instantiated.

**What's designed but unbuilt**: ElevenLabs integration, identity resolver (persona -> voice),
gamification, rhythms integration, wake word training, full duplex conversation.

---

## Phase 0: Stabilize (2 hours)

> Fix every known bug. Leave the system more reliable than we found it.

### 0.1 — P0 Bug Fixes (45min)

| Item | File | Change | Lines |
|------|------|--------|-------|
| Queue checks stt-active | `scripts/voice_queue.py:281` | Add `stt_active = Path(...).exists()` guard before dequeue | +2 |
| Clear tts-playing on startup | `scripts/voice_queue.py` | `Path("tts-playing").unlink(missing_ok=True)` in `_run_server()` | +1 |
| Clear stale sockets on startup | `scripts/voice_queue.py`, `scripts/tts_daemon.py` | `SOCKET_PATH.unlink(missing_ok=True)` before bind | +1 each |
| Fix duplex.py naming | `lib/duplex.py:33` | `tts-playing.pid` -> `tts-playing` | 1 line fix |

### 0.2 — P1 Bug Fixes (30min)

| Item | File | Change |
|------|------|--------|
| Ambient state reset on SessionStart | `lib/router.py` or `lib/ambient.py` | Call `cleanup()` on SessionStart, validate PID before trusting count |
| Greeting through queue | `lib/router.py:_play_cached_greeting()` | Route via `enqueue_speech()` instead of direct `play_sound()` |

### 0.3 — Documentation Sync (30min)

| Item | Change |
|------|--------|
| Root ARCHITECTURE.md | Add "SUPERSEDED by design/ARCHITECTURE.md" header |
| Spec status fields | Update specs 01-06, 15, 18 from "draft"/"ready" to "implemented" |
| Spec 07 | Mark as "stale — code uses Parakeet, not faster-whisper" |

### 0.4 — Daemon Resilience (15min)

| Item | Change |
|------|--------|
| voice.target | Create systemd target grouping TTS + queue, enable for graphical-session |
| PipeWire dependency | Add `After=pipewire.service` to voice-tts.service |

**Exit criteria**: All P0/P1 bugs fixed. All tests pass. Daemons survive reboot.
System is strictly more reliable than before Phase 0.

---

## Phase 1: Focus Isolation (3 hours)

> Shawn's two user stories: spatial focus + STT-aware queuing.

### 1.1 — Focus Presets (45min)

Add preset subcommand to voice skill:

```
/voice preset focus-only    → focused:1.0, all others:0.0
/voice preset spatial       → focused:1.0, same_window:0.5, same_session:0.2, other_session:0.0
/voice preset hear-all      → all 1.0 (current default)
/voice preset meeting       → volume:0.1, tts.enabled:false, all non-focused:0.0
/voice preset restore       → undo last preset change
```

Implementation: read config.yaml, write new focus_volumes (and optionally volume/tts.enabled),
store previous values in `~/.claude/local/voice/.preset-backup.yaml` for restore.

### 1.2 — Tmux Focus Hook (45min)

Install tmux hooks that write focused pane ID for real-time spatial state:

```bash
set-hook -g after-select-pane    'run-shell -b "echo #{pane_id} > ~/.claude/local/voice/focus-state"'
set-hook -g after-select-window  'run-shell -b "echo #{pane_id} > ~/.claude/local/voice/focus-state"'
set-hook -g client-session-changed 'run-shell -b "echo #{pane_id} > ~/.claude/local/voice/focus-state"'
```

Owned by claude-tmux. Consumed by claude-voice router.py (already reads the file).

### 1.3 — Queue Hold During STT (45min)

Add stt-active check to voice_queue.py main loop (builds on 0.1 fix):

```python
stt_active = Path("~/.claude/local/voice/stt-active").expanduser().exists()
if playing_proc is None and queue.heap and not stt_active and time.monotonic() >= transition_until:
    # dequeue and play
```

Items accumulate during STT. When flag clears, queue drains in priority order.
Items still expire at `max_wait_seconds` — stale speech is worse than no speech.

### 1.4 — STT Systemd Unit (45min)

Create `voice-stt.service`:
- ExecStart with stt-env Python
- After=voice-queue.service
- Sets stt-active during recording
- Clears stt-active on stop/crash (ExecStopPost)
- Add to voice.target

**Exit criteria**: `/voice preset focus-only` silences all non-active panes.
Queue holds items during STT. STT daemon survives reboot.

---

## Phase 2: Hardening (3 hours)

> Make what exists bulletproof. Zero new features.

### 2.1 — Stale Flag Protection (1hr)

Every flag file gets a staleness check:

| Flag | Protection |
|------|------------|
| `stt-active` | Include timestamp. Consumers ignore if > 60s old |
| `tts-playing` | Include PID. Consumers check PID alive before trusting |
| `focus-state` | Include timestamp. Fall through to subprocess if > 5s old |
| `ambient-count` | Include PID. Reset if PID dead |

Format: `PID TIMESTAMP` on first line. Consumers: `read line, check PID alive, check age`.

### 2.2 — Daemon Health Monitoring (1hr)

- Health check script: verifies sockets exist, daemons respond to `{"type":"status"}`, no stale flags
- Integrate with existing `~/.claude/local/health/` infrastructure
- Desktop notification on failure (via `notify-send`)
- Heartbeat file per daemon (already exists for voice overall, extend to per-daemon)

### 2.3 — Test Coverage (1hr)

| Gap | Test |
|-----|------|
| Volume pipeline with spatial mixing | Parametric test: all 5 focus states x 3 priorities |
| Queue hold during stt-active | Integration test: enqueue, touch stt-active, verify no dequeue |
| Stale flag detection | Unit test: old timestamp -> ignored |
| Daemon socket reconnection | Test: kill daemon, verify fallback to direct play |
| Config preset round-trip | Test: apply preset, verify config, restore, verify original |

**Exit criteria**: 70+ tests. All state transitions tested. Health monitoring active.

---

## Phase 3: Identity (4 hours)

> Each persona gets a distinct voice. The terminal becomes a room of recognizable speakers.

### 3.1 — Persona-Voice Config (1hr)

Add to config.yaml:

```yaml
personas:
  matt:
    voice: am_onyx
    pitch_shift: 0
  philipp:
    voice: af_heart
    pitch_shift: -2
  darren:
    voice: am_adam
    pitch_shift: 0
  _default:
    voice: am_onyx
```

Router reads `$PERSONA_SLUG`, looks up voice config, passes to TTS daemon.

### 3.2 — Identity Resolver (2hr)

`lib/identity.py` — the designed-but-unbuilt spec 08:

Resolution chain: `session override -> $PERSONA_SLUG -> agent type -> _default`

Returns: `{voice, pitch_shift, theme_override, greeting_template}`

Wire into `router.py` for TTS voice selection and `_play_cached_greeting()` for
persona-specific greetings.

### 3.3 — Agent Sound Profiles (1hr)

Extend `agents.py` with per-persona earcon variants:
- Matt: clean professional tones
- Darren: slightly warmer, lower frequency
- Philipp: data-oriented, precise clicks

Generate via `generate_agent_sounds.py` with persona parameters.

**Exit criteria**: Each persona speaks with a recognizable voice. Greetings are personalized.
Agent dispatch/return sounds differentiate who is working.

---

## Phase 4: Knowledge Integration (3 hours)

> Voice events enter the knowledge layer. What was heard becomes searchable.

### 4.1 — Voice Events to KOI (2hr)

Create `VoiceSensor` in legion-koi (same pattern as ChangelogSensor, JournalSensor):
- Polls `~/.claude/local/voice/events/*.jsonl`
- Creates bundles in `legion.claude-voice` namespace
- Fields: event type, theme, volume, focus_state, tts_text, persona, timestamp

1,922 existing events + all future events become searchable, feed rhythms briefs,
visible in Philipp's dashboards.

### 4.2 — Voice in Rhythms Briefs (30min)

Add voice summary to morning/evening brief investigators:
- "Yesterday: 47 voice events, 12 TTS responses spoken, 3 agents active"
- "Most active persona by voice: matt (23 events)"

### 4.3 — Statusline Integration (30min)

Show voice state in tmux statusline:
- Current preset (focus-only / spatial / hear-all)
- Mute indicator
- STT active indicator
- Current theme name

**Exit criteria**: Voice events in KOI. Voice summary in briefs. Voice state visible in statusline.

---

## Phase 5: Conversation (8 hours)

> The terminal listens. Push-to-talk first, always-on later.

### 5.1 — Push-to-Talk (3hr)

- Keybinding: configurable hotkey (default: F12 or tmux prefix + v)
- Hold key: start recording (stt-active flag set, queue holds)
- Release key: stop recording, transcribe, inject into Claude Code stdin
- Visual indicator: tmux statusline shows recording state

### 5.2 — AEC Configuration (2hr)

PipeWire echo cancellation — prevent TTS from being picked up by microphone:
- Create AEC virtual sink/source via PipeWire config
- Route STT capture through AEC source
- Route TTS playback through AEC sink reference
- Script at `~/.claude/local/scripts/setup-voice-aec.sh`

### 5.3 — Wake Word (1.5hr)

- Train custom "Legion" wake word ONNX model (openWakeWord toolkit)
- Install as `assets/models/legion.onnx`
- STT daemon loads custom model, falls back to "hey_jarvis"

### 5.4 — Wire DuplexManager (1.5hr)

Connect `lib/duplex.py` to `stt_daemon.py`:
- Barge-in detection via VAD during TTS playback
- TTS cancellation (kill pw-play)
- Transition from TTS to STT recording

**Exit criteria**: User can speak to Legion via push-to-talk or wake word.
AEC prevents feedback loops. Barge-in works.

---

## Phase 6: Polish (4 hours)

> Everything that makes the system delightful rather than merely functional.

### 6.1 — ElevenLabs Integration (2hr)

Implement spec 13 — cloud TTS for high-quality narration:
- Kokoro handles short utterances (< 50 words) — free, fast
- ElevenLabs handles long narration — expressive, emotional
- Auto-selection based on text length and config

### 6.2 — Gamification (1hr)

Implement spec 09 — XP, levels, achievements:
- XP per event type (commit = 10, task_complete = 5, error = -2)
- Level curve: `floor(k * sqrt(XP))`
- Level-up sound on threshold cross
- Achievement tracking in SQLite

### 6.3 — Rhythms Integration (1hr)

Implement spec 10:
- Morning brief spoken aloud via TTS
- Time-of-day ambient soundscapes
- Meeting-mode auto-trigger from calendar events

**Exit criteria**: Cloud TTS available for quality-sensitive narration.
Gamification provides positive reinforcement. Rhythms drive ambient audio.

---

## Phase 7: Fleet (2 hours)

> Voice works on any machine in the Tailscale mesh.

### 7.1 — Portable Configuration (1hr)

- Voice config in legion-brain (synced via git)
- Machine-specific overrides (GPU model, audio device)
- Kokoro model download script for new machines

### 7.2 — Remote Voice (1hr)

- Forward TTS output over Tailscale to local speakers
- Remote STT: speak into local mic, transcribe on GPU machine
- PipeWire network streaming between fleet machines

**Exit criteria**: Voice works on E15 (thin client mode, GPU on Legion).

---

## Timeline Estimate

| Phase | Effort | Cumulative | Description |
|-------|--------|-----------|-------------|
| 0 | 2hr | 2hr | Stabilize — fix all known bugs |
| 1 | 3hr | 5hr | Focus isolation — spatial + STT-aware |
| 2 | 3hr | 8hr | Hardening — bulletproof state management |
| 3 | 4hr | 12hr | Identity — persona voices |
| 4 | 3hr | 15hr | Knowledge — voice events in KOI |
| 5 | 8hr | 23hr | Conversation — PTT + wake word + duplex |
| 6 | 4hr | 27hr | Polish — ElevenLabs, gamification, rhythms |
| 7 | 2hr | 29hr | Fleet — portable across machines |

## Dependencies

```
Phase 0 ──> Phase 1 ──> Phase 2
                │
                └──> Phase 3 (independent of 2)
                │
                └──> Phase 4 (independent of 2, 3)
                         │
                         └──> Phase 5 (requires 1.3 queue hold + 2.1 stale protection)
                                  │
                                  └──> Phase 6 (requires 3 identity + 5 conversation)
                                           │
                                           └──> Phase 7 (requires everything)
```

Phases 3 and 4 can run in parallel after Phase 1.
Phase 5 depends on Phase 1 (queue hold) and Phase 2 (stale flag protection).
Phase 6 depends on Phase 3 (identity) and Phase 5 (conversation).
Phase 7 depends on everything stabilized.

## Invariants (Hold Across All Phases)

1. Hooks never crash. Exit 0 always.
2. Fail open. Sound over silence, except during STT.
3. File-as-IPC. No new daemon protocols.
4. Config-driven. Behavior changes via YAML, not code.
5. Every commit leaves the system working.
6. Every phase has its own test coverage.
7. design/ directory updated before code changes.
8. Known issues tracked in design/known-issues/.
