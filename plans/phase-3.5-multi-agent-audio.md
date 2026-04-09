---
title: "Phase 3.5: Multi-Agent Audio — Complete Implementation Plan"
status: active
created: 2026-03-27
author: matt
session: matt:57
phase: "3.5"
spec: specs/15-tmux-focus-gate.md
commit_baseline: f29dfe5
tags: [plan, phase-3.5, spatial-mixer, multi-agent, implementation]
---

# Phase 3.5: Multi-Agent Audio — Complete Implementation Plan

## Overview

Phase 3.5 adds spatial audio intelligence to claude-voice. The core abstraction
is a **volume mixer** that computes effective volume from spatial state (which tmux
pane), event priority (how important), and temporal state (is the user speaking).

Six layers, each additive. Each leaves a working system. Total: ~9.5 hours.

---

## Entry Criteria (all met)

- [x] Phase 1 complete (scaffold, hooks, pw-play, default theme)
- [x] Phase 2 complete (7 themes, hot-swap, variant randomization)
- [x] Phase 3 TTS operational (Kokoro-82M daemon, 689ms warm synthesis)
- [x] DRY refactor complete (constants.py, utils.py — commit 681a39c)
- [x] Spec 15 written (spatial mixer design — commit f29dfe5)
- [x] Research complete (5 research files, 5 scratchpad thoughts)

---

## Layer 0: Spatial Volume Mixer (45 min)

**Goal**: Volume follows cognitive distance. Focused pane at 100%, same-window at 50%, same-session at 20%, other-session at 0%. Priority floors let critical events break through.

### Files to modify

| File | Change |
|------|--------|
| `lib/constants.py` | Add `FOCUS_STATE_PATH`, `DEFAULT_FOCUS_VOLUMES`, `DEFAULT_PRIORITY_FLOORS` |
| `lib/state.py` | Add `tmux` section to `DEFAULT_CONFIG` |
| `lib/router.py` | Add `import subprocess`, `_get_focus_state()`, `_effective_volume()`, wire into `_route_event_inner()` |
| `lib/logger.py` | Add `focus_state` param to `log_event()`, add column to SQLite schema |
| `CLAUDE.md` | Document `tmux` config section |
| `config.yaml` | Add `tmux` section with `focus_volumes` and `priority_floors` |

### Implementation steps

**Step 1: constants.py** — add 3 new constants:
```python
FOCUS_STATE_PATH = VOICE_DATA_DIR / "focus-state"

DEFAULT_FOCUS_VOLUMES = {
    "focused": 1.0,
    "same_window": 0.5,
    "same_session": 0.2,
    "other_session": 0.0,
    "no_tmux": 1.0,
}

DEFAULT_PRIORITY_FLOORS = {
    "2": 0.8,
    "1": 0.0,
    "0": 0.0,
}
```

**Step 2: state.py** — add tmux section to DEFAULT_CONFIG:
```python
from constants import DEFAULT_FOCUS_VOLUMES, DEFAULT_PRIORITY_FLOORS

DEFAULT_CONFIG = {
    ...,
    "tmux": {
        "focus_volumes": dict(DEFAULT_FOCUS_VOLUMES),
        "priority_floors": dict(DEFAULT_PRIORITY_FLOORS),
    },
}
```

**Step 3: router.py** — add two functions and wire them in:

`_get_focus_state()`:
- Check `TMUX_PANE` env var (no tmux → "no_tmux")
- Check cached `FOCUS_STATE_PATH` file (Layer 1 compat, ~0.1ms)
- Subprocess fallback: `tmux display-message -p -t $PANE '#{pane_active} #{window_active} #{session_attached}'` (~5ms)
- Parse three booleans → four states
- Fail open → "no_tmux"

`_effective_volume()`:
- Look up `focus_volumes[focus_state]` from config
- Look up `priority_floors[str(priority)]` from config
- Return `min(1.0, max(base * focus_mult, base * floor_mult))`

Wire into `_route_event_inner()`:
- After config load and theme resolve, call `_get_focus_state()` once
- Get priority from `theme["semantic_sounds"][sound_token]["priority"]`
- Compute `earcon_vol` and `tts_vol` via `_effective_volume()`
- Pass `earcon_vol` to `play_sound()`
- Pass `tts_vol` to greeting/response functions
- Skip TTS synthesis entirely when `tts_vol == 0.0`

**Step 4: logger.py** — add `focus_state` to log records:
- Add `focus_state: Optional[str] = None` param to `log_event()`
- Add to JSONL record
- Add `focused TEXT` column to SQLite events table (ALTER TABLE if exists, add to CREATE TABLE)

**Step 5: config.yaml** — add tmux section:
```yaml
tmux:
  focus_volumes:
    focused: 1.0
    same_window: 0.5
    same_session: 0.2
    other_session: 0.0
  priority_floors:
    "2": 0.8
    "1": 0.0
    "0": 0.0
```

**Step 6: CLAUDE.md** — document the tmux config section.

**Step 7: Sync to cache, test, commit.**

### Tests

1. **Focused pane**: hook fires → earcon at 100% + TTS speaks → verify in SQLite `focus_state='focused'`
2. **Unfocused same-window**: hook fires → earcon at 50% + TTS at 50% → verify `focus_state='same_window'`
3. **Unfocused same-session**: hook fires → earcon at 20% + TTS at 20%
4. **Error from background**: PostToolUseFailure from unfocused pane → earcon at 80% (priority floor)
5. **No tmux**: unset TMUX_PANE → plays at 100% (fail open)
6. **Latency**: `time tmux display-message -p -t $TMUX_PANE '#{pane_active} #{window_active} #{session_attached}'` < 10ms

### Exit criteria

- [ ] 4-level spatial state detection working
- [ ] Volume scales with spatial distance
- [ ] Priority floors override spatial silencing for critical events
- [ ] `focus_state` logged in every event record
- [ ] TTS synthesis skipped when effective volume is 0.0
- [ ] Non-tmux environments unaffected

---

## Layer 1: Cached Spatial State (1 hour)

**Goal**: Replace the 5ms subprocess call with a 0.1ms file read.

### Files to create/modify

| File | Change |
|------|--------|
| `~/.claude/local/scripts/voice-focus-change.sh` | NEW — writes spatial state on pane focus |
| `~/.tmux.conf` or tmux plugin hook | Add `after-select-pane` and `after-select-window` hooks |
| `lib/router.py` | `_get_focus_state()` already checks file first (built into Layer 0) |

### Implementation

Script `voice-focus-change.sh`:
```bash
#!/bin/bash
# Called by tmux after-select-pane / after-select-window hooks.
# Writes the focused pane's state so voice hooks can read it without subprocess.
PANE_ID="$1"
STATE_FILE="$HOME/.claude/local/voice/focus-state"
echo "$PANE_ID" > "$STATE_FILE"
```

tmux hooks:
```tmux
set-hook -g after-select-pane "run-shell -b '$HOME/.claude/local/scripts/voice-focus-change.sh #{pane_id}'"
set-hook -g after-select-window "run-shell -b '$HOME/.claude/local/scripts/voice-focus-change.sh #{pane_id}'"
```

Update `_get_focus_state()` to compare this pane's `TMUX_PANE` against the cached
focused pane ID. If they match → "focused". If not → subprocess fallback to
determine same_window/same_session/other_session.

### Tests

1. Switch panes → file updates within 100ms
2. Hook reads file → returns correct focus state
3. File missing → falls back to subprocess (no error)

---

## Layer 2: STT Multiplier (30 min)

**Goal**: Suppress ALL TTS while the user is speaking.

### Implementation

- When STT starts: `touch ~/.claude/local/voice/stt-active`
- When STT stops: `rm ~/.claude/local/voice/stt-active`
- In `_effective_volume()`, add: `stt_mult = 0.0 if stt_active_path.exists() else 1.0`
- Volume pipeline becomes: `max(base × focus_mult × stt_mult, base × floor_mult × stt_mult)`

Note: STT doesn't exist yet (Phase 4). This layer creates the FILE-BASED CONTRACT
that Phase 4 will honor. Layer 2 is the receiver; Phase 4 is the sender.

---

## Layer 3: Per-Pane Audio Routing (2 hours)

**Goal**: Route audio to specific PipeWire sinks per pane.

### Implementation

- `audio.py`: add `sink` param to `play_sound()`, pass `--target <sink>` to `pw-play`
- Config: `audio.sink: default`
- Per-pane override: tmux option `@claude_audio_sink`, read via `tmux show-option -pv @claude_audio_sink`
- `PIPEWIRE_NODE` env var override (set at pane launch time)

This is independent of the volume mixer — routing is WHERE, mixer is HOW LOUD.

### Tests

1. Default sink plays normally
2. Custom sink routes to specified target
3. Per-pane override via tmux option works

---

## Layer 4: Agent Sound Profiles — RTS Model (3 hours)

**Goal**: Each persona has a unique sound identity. Navigate to a pane → hear that agent's "select" sound.

### Files to create/modify

| File | Change |
|------|--------|
| `lib/agents.py` | NEW — agent profile resolution (persona slug → sound set) |
| `assets/themes/*/theme.json` | Add `agent_sounds` section |
| `scripts/generate_agent_sounds.py` | NEW — synthesize per-agent sounds |

### Sound taxonomy (from WC3 research)

| Slot | Event | Description |
|------|-------|-------------|
| `select` | Pane gains focus | "What?" — agent acknowledges your attention |
| `acknowledge` | SubagentStart | "Yes." — agent accepts a task |
| `complete` | SubagentStop | "Work complete." — agent reports results |
| `error` | PostToolUseFailure | "We're under attack!" — something went wrong |

### Implementation

1. Create `lib/agents.py` with `resolve_agent_sounds(persona_slug, theme)` → dict of slot → wav_path
2. Add `agent_sounds` to theme.json with per-persona overrides and `_default` fallback
3. Generate 4 sounds × ~5 agent profiles = ~20 new WAV files per theme
4. Wire `select` sound into the tmux `after-select-pane` hook (Layer 1 script plays it)
5. Wire `acknowledge` and `complete` into SubagentStart/SubagentStop routing

### Tests

1. Navigate to matt pane → matt's select sound plays
2. Navigate to darren pane → darren's select sound plays
3. Unknown agent → default select sound
4. SubagentStart → acknowledge sound for the spawned agent type

---

## Layer 5: Ambient Engine (2 hours)

**Goal**: Background drone when subagents are running. Volume scales with agent count. Ducks during TTS.

### Files to create

| File | Change |
|------|--------|
| `lib/ambient.py` | NEW — loop management, PID tracking, volume control |

### Implementation

```python
class AmbientEngine:
    PID_FILE = VOICE_DATA_DIR / "ambient.pid"

    def start_loop(self, wav_path: Path, volume: float = 0.3):
        proc = Popen(["pw-play", "--loop", f"--volume={volume}", str(wav_path)],
                     start_new_session=True)
        self.PID_FILE.write_text(str(proc.pid))

    def stop_loop(self):
        if self.PID_FILE.exists():
            pid = int(self.PID_FILE.read_text())
            os.kill(pid, signal.SIGTERM)
            self.PID_FILE.unlink()

    def is_running(self) -> bool:
        # PID liveness check

    def set_volume(self, volume: float):
        # wpctl set-volume --pid <PID> <volume>
```

Integration:
- SubagentStart: if no ambient loop running, start. Increment agent count file.
- SubagentStop: decrement agent count. If zero, stop loop.
- Agent count → ambient intensity: 1 agent = 0.1, 2 = 0.2, 3+ = 0.3
- TTS speech: duck ambient to 0.05 during synthesis, restore after. This is one more multiplier in the volume pipeline.
- SessionEnd: always stop ambient loop (cleanup).

### Tests

1. SubagentStart → ambient starts
2. SubagentStop (last agent) → ambient stops
3. TTS fires → ambient ducks to near-silence, restores after
4. SessionEnd → ambient cleaned up regardless of agent count
5. Orphan PID detection (process died but PID file remains)

---

## Layer 6: Voice Queue Daemon (3 hours)

**Goal**: Agents take turns speaking. Never two voices at once. Priority scheduling, speaker transition pauses, expiration for stale speech.

**Why this is Phase 3.5, not Phase 6**: Right now, two agents completing simultaneously both speak simultaneously — at their spatially-mixed volumes. The mixer answers "how loud?" but not "when?" Both questions must be answered for usable multi-agent voice.

**Prior art**: The TypeScript POC at `~/.claude/local/dock/repos/LinuxIsCool/claude-plugins-public/plugins/voice/src/coordination/` designed this completely (6 files, ~700 lines). We port the design to Python, simplified.

### Architecture (from POC, adapted)

```
┌─────────────────────────────────────┐
│  Voice Queue Daemon                  │
│  Unix socket: ~/.claude/local/voice/queue.sock  │
│                                      │
│  QueueManager:                       │
│    - Priority heap (heapq)           │
│    - Max 50 items, LRU eviction      │
│    - Per-item expiration (30s)       │
│    - Interruption policy per priority│
│    - Speaker transition (300ms)      │
│                                      │
│  IPCServer:                          │
│    - Newline-delimited JSON          │
│    - play_now / abort signals        │
│    - Tracks currently-playing client │
└─────────────────────────────────────┘
        ↑                    ↓
   enqueue()           play_now(item)
   playback_complete   abort(reason)
        ↑                    ↓
┌─────────────────────────────────────┐
│  Claude Instance (hook process)      │
│  router.py → speak_via_daemon()      │
│  Instead of playing directly:        │
│    1. Synthesize WAV via TTS daemon  │
│    2. Enqueue to queue daemon        │
│    3. Wait for play_now signal       │
│    4. Play WAV via pw-play           │
│    5. Send playback_complete         │
└─────────────────────────────────────┘
```

### Priority Mapping (from theme.json to queue)

| theme.json priority | Queue Priority | Events | Behavior |
|---------------------|---------------|--------|----------|
| 2 | CRITICAL (100) | error, notification, permission | Interrupts current speaker |
| 1 | NORMAL (50) | task_complete, commit, agent_return | Waits in queue |
| 0 | LOW (20) | session_start, prompt_ack, compact | Waits, dropped if queue full |
| (TTS) | NORMAL (50) | Stop TTS response | Waits, expires after 30s |

### Files to create

| File | Purpose | Lines (est.) |
|------|---------|-------------|
| `scripts/voice_queue.py` | Queue daemon — heapq + Unix socket server | ~200 |
| `lib/queue_client.py` | Client library — enqueue, wait for play_now | ~80 |

### IPC Protocol (simplified from POC)

Client → Daemon:
```json
{"type": "enqueue", "text": "...", "priority": 50, "agent_id": "matt", "wav_path": "/path/to.wav"}
{"type": "playback_complete", "id": "vq-123", "duration_ms": 2800}
{"type": "playback_failed", "id": "vq-123", "error": "pw-play not found"}
{"type": "status"}
```

Daemon → Client:
```json
{"type": "queued", "id": "vq-123", "position": 0}
{"type": "play_now", "id": "vq-123", "wav_path": "/path/to.wav", "volume": 0.8}
{"type": "abort", "id": "vq-123", "reason": "preempted by CRITICAL"}
{"type": "status", "queue_length": 3, "is_playing": true}
```

### Key Simplifications from POC

| POC (TypeScript) | Our Version (Python) | Why |
|------------------|---------------------|-----|
| EventEmitter class | Simple callback dict | No npm dependency |
| QueueConfig file | Read from config.yaml | DRY — same config file |
| 5 priority levels | 3 levels (maps from theme.json 0/1/2) | Sufficient for now |
| Full VoiceConfig in QueueItem | Just wav_path + volume | Synthesis happens BEFORE enqueue |
| Persistent connections | One-shot per event | Hook processes are short-lived |

### Integration with Existing Pipeline

The queue inserts between TTS synthesis and playback:

**Before (current)**:
```
router.py → speak_via_daemon(text) → daemon synthesizes → daemon plays → done
```

**After (with queue)**:
```
router.py → speak_via_daemon(text) → TTS daemon synthesizes → returns WAV path
         → queue_client.enqueue(wav_path, priority, volume)
         → queue daemon: wait for turn
         → queue daemon: play_now → client plays WAV via pw-play
         → client: playback_complete
```

The TTS daemon ONLY synthesizes. The queue daemon ONLY schedules. pw-play ONLY plays. Clean separation.

### Speaker Transitions

When a different agent speaks after the previous one, the daemon waits 300ms before sending `play_now`. This creates natural conversational rhythm — you can hear when the speaker changes. Same agent back-to-back? No pause.

### Expiration

Items older than 30s in the queue are dropped. If 5 agents complete tasks simultaneously, only the first 2-3 speak. The rest expire — their context is stale anyway. Configurable via `queue.max_wait_seconds` in config.yaml.

### Config

```yaml
queue:
  enabled: true
  max_items: 50
  max_wait_seconds: 30
  speaker_transition_ms: 300
  interrupt_threshold: 2     # priority >= 2 can interrupt
  socket: ~/.claude/local/voice/queue.sock
```

### Tests

1. Two Stop events fire simultaneously → first speaks, second waits, then speaks
2. Error during speech → CRITICAL interrupts, current speech aborted
3. 10 subagents complete at once → first 3-4 speak, rest expire
4. Different agents → 300ms pause between speakers
5. Same agent → no pause
6. Queue status returns accurate counts
7. Daemon idle timeout → clean shutdown

---

## Implementation Schedule

| Session | Layers | Effort | Deliverable |
|---------|--------|--------|-------------|
| Evening 1 | Layer 0 ✅ + Layer 1 | 1h 45m | Spatial mixer + cached state file |
| Evening 2 | Layer 2 + Layer 3 | 2h 30m | STT multiplier + per-pane routing |
| Evening 3 | Layer 4 + Layer 5 | 5h | Agent sound profiles + ambient engine |
| Evening 4 | Layer 6 | 3h | Voice queue daemon + client integration |

Each evening ends with: sync cache → test → commit → journal entry.

---

## Commit Sequence

```
1. feat(claude-voice): Phase 3.5 Layer 0 — spatial volume mixer ✅ (00266e6)
2. feat(claude-voice): Phase 3.5 Layer 1 — cached spatial state via tmux hooks
3. feat(claude-voice): Phase 3.5 Layer 2 — STT-active volume multiplier
4. feat(claude-voice): Phase 3.5 Layer 3 — per-pane PipeWire audio routing
5. feat(claude-voice): Phase 3.5 Layer 4 — RTS agent sound profiles
6. feat(claude-voice): Phase 3.5 Layer 5 — ambient engine with agent count scaling
7. feat(claude-voice): Phase 3.5 Layer 6 — voice queue daemon (turn-taking)
```

---

## Phase 3.5 Exit Criteria

- [x] Focused pane plays at full volume
- [x] Same-window panes play at 50% (configurable)
- [x] Same-session panes play at 20% (configurable)
- [x] Other-session panes are silent unless priority >= 2
- [x] Error/notification from ANY pane plays at 80% (priority floor)
- [x] All events log focus_state and effective_volume
- [x] All 7 themes still work (no regressions)
- [ ] STT-active flag suppresses all TTS (file contract for Phase 4)
- [ ] Per-pane audio routing via `--target` works
- [ ] Navigating to an agent pane plays that agent's "select" sound
- [ ] Ambient drone plays during subagent activity, stops at zero agents
- [ ] Ambient ducks during TTS speech
- [ ] **Agents take turns speaking — never two voices at once**
- [ ] **Speaker transitions have 300ms pause**
- [ ] **Stale queued speech expires after 30s**
- [ ] Latency: earcon p95 < 150ms, TTS p95 < 700ms (unchanged)

---

## What This Unblocks

| Phase | What Phase 3.5 Provides |
|-------|------------------------|
| 4 (STT) | Focus mixer prevents cross-talk. STT-active file contract ready. Queue prevents TTS during STT. |
| 5 (Personality) | Agent sound profiles provide per-persona sonic identity. Priority floors ready for gamification. |
| 6 (Integration) | Ambient engine ready for rhythms bridge. Focus state logged for analytics. Queue ready for matrix cross-agent coordination. |
| 7 (Autonomy) | Mixer + queue handle real-time turn-taking. Dynamic intent queue evolves from static queue. |

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| tmux subprocess adds latency | Layer 1 caches state in file (~0.1ms) |
| Ambient loop orphan processes | PID liveness check + SessionEnd cleanup |
| Config complexity | Sensible defaults. Binary gate is a config preset. |
| Agent sound generation time | Batch generate all sounds in one script run |
| Breaking existing behavior | Volume mixer at 100% everywhere = current behavior exactly |
| Queue daemon not running | Graceful fallback: play immediately without queue (current behavior) |
| Queue daemon adds latency | Enqueue is fire-and-forget for earcons; only TTS waits for play_now |
