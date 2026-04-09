---
title: "Phase 3.5 Remaining Layers (1-6) — Implementation Plan"
status: active
created: 2026-03-27
updated: 2026-03-27
author: matt
session: matt:56 (75265449)
phase: "3.5"
baseline_commit: 8c99778
layer_0_commit: 00266e6
depends_on: plans/phase-3.5-multi-agent-audio.md
tags: [plan, phase-3.5, implementation, layers-1-6]
---

# Phase 3.5 Remaining Layers (1-6)

Layer 0 (spatial volume mixer) is shipped and reviewed. Six layers remain.
This plan covers all of them with exact files, code, tests, and sequencing.

## Status

| Layer | Name | Status | Commit |
|-------|------|--------|--------|
| 0 | Spatial Volume Mixer | ✅ DONE | 00266e6, 4fc4a0e |
| 1 | Cached Spatial State | Ready to build | — |
| 2 | STT Multiplier | Ready to build | — |
| 3 | Per-Pane Audio Routing | Ready to build | — |
| 4 | Agent Sound Profiles | Ready to build | — |
| 5 | Ambient Engine | Ready to build | — |
| 6 | Voice Queue Daemon | Ready to build | — |

---

## Layer 1: Cached Spatial State (1 hour)

**Goal**: Replace the 5ms tmux subprocess call with a 0.1ms file read.

**Why**: The mixer calls `_get_focus_state()` on every hook event. 33 plugins fire on SessionStart. 5ms × N events adds up. A cached file makes it negligible.

### Files

| File | Action |
|------|--------|
| `~/.claude/local/scripts/voice-focus-change.sh` | **CREATE** — tmux hook script |
| `lib/router.py` | **MODIFY** — `_get_focus_state()` already checks file (Tier 1), just needs the file to exist |
| `~/.tmux.conf` or claude-tmux hook | **MODIFY** — install tmux hooks |

### Implementation

**voice-focus-change.sh** (~10 lines):
```bash
#!/bin/bash
# Called by tmux after-select-pane / after-select-window hooks.
# Writes the focused pane ID so voice hooks can compare without subprocess.
PANE_ID="${1:-}"
[ -z "$PANE_ID" ] && exit 0
STATE_DIR="$HOME/.claude/local/voice"
mkdir -p "$STATE_DIR"
echo "$PANE_ID" > "$STATE_DIR/focus-state"
```

**tmux hooks** (add to `~/.tmux.conf` or via claude-tmux):
```tmux
set-hook -g after-select-pane "run-shell -b '~/.claude/local/scripts/voice-focus-change.sh #{pane_id}'"
set-hook -g after-select-window "run-shell -b '~/.claude/local/scripts/voice-focus-change.sh #{pane_id}'"
```

**router.py _get_focus_state() update**: The function already has Tier 1 (cached file check). Currently it only checks if the file matches THIS pane (→ "focused") and falls through to subprocess otherwise. Enhance: when file doesn't match, we still know we're NOT focused but need subprocess for same_window vs same_session vs other_session. No code change needed — the current logic already does this correctly.

### Tests

1. `tmux select-pane -t %55` → file contains `%55` within 100ms
2. Switch window → file updates to new active pane
3. `_get_focus_state()` reads file first (verify via latency: <1ms vs ~5ms)
4. Delete file → falls back to subprocess (no error)

### Commit message
```
feat(claude-voice): Phase 3.5 Layer 1 — cached spatial state via tmux hooks
```

---

## Layer 2: STT Multiplier (30 min)

**Goal**: All TTS suppressed while user is speaking. File-based contract for Phase 4.

**Why**: When STT is active (Phase 4), Kokoro TTS output must not play — it would be picked up by the microphone. This layer creates the suppression mechanism. Phase 4 will set the flag; this layer reads it.

### Files

| File | Action |
|------|--------|
| `lib/constants.py` | **MODIFY** — add `STT_ACTIVE_PATH` |
| `lib/router.py` | **MODIFY** — add STT multiplier to volume pipeline |

### Implementation

**constants.py**:
```python
STT_ACTIVE_PATH = VOICE_DATA_DIR / "stt-active"
"""File flag: exists when STT is recording. All TTS suppressed while present."""
```

**router.py** — add to `_route_event_inner()` after spatial mixing:
```python
    # 5c. STT suppression — mute all TTS while user is speaking
    from constants import STT_ACTIVE_PATH
    stt_active = STT_ACTIVE_PATH.exists()
    if stt_active:
        mixed_vol = 0.0  # Suppress everything while user speaks
```

This is deliberately simple. One file existence check (~0.05ms). No config needed — when STT is active, silence is always correct.

### Tests

1. `touch ~/.claude/local/voice/stt-active` → next hook produces no audio
2. `rm ~/.claude/local/voice/stt-active` → audio resumes
3. Priority 2 events are ALSO suppressed (STT overrides priority floors — you don't want error sounds in the mic)

### Commit message
```
feat(claude-voice): Phase 3.5 Layer 2 — STT-active volume suppression
```

---

## Layer 3: Per-Pane Audio Routing (2 hours)

**Goal**: Route audio to specific PipeWire sinks per pane.

**Why**: Independent from the mixer. Mixer controls HOW LOUD, routing controls WHERE. Different panes can output to different sinks (headphones, HDMI, Bluetooth).

### Files

| File | Action |
|------|--------|
| `lib/audio.py` | **MODIFY** — add `sink` parameter, `--target` support |
| `lib/router.py` | **MODIFY** — read sink from config/tmux pane option, pass to play_sound |
| `lib/state.py` | **MODIFY** — add `audio` section to DEFAULT_CONFIG |
| `config.yaml` | **MODIFY** — add `audio.sink` |

### Implementation

**audio.py** — add `sink` param to `play_sound()` and `_build_args()`:
```python
def play_sound(path: Path, volume: float = 1.0, sink: str = "") -> Optional[Popen]:
    ...

def _build_args(name, path, sound, volume, sink=""):
    match name:
        case "pw-play":
            args = [path, f"--volume={volume:.3f}"]
            if sink:
                args.append(f"--target={sink}")
            args.append(str(sound))
            return args
        ...
```

**router.py** — resolve sink per pane:
```python
    # 5d. Audio sink routing
    audio_config = config.get("audio", {})
    sink = audio_config.get("sink", "")

    # Per-pane override via tmux pane option
    if not sink and os.environ.get("TMUX_PANE"):
        try:
            result = subprocess.run(
                ["tmux", "show-option", "-pv", "@claude_audio_sink"],
                capture_output=True, text=True, timeout=1,
            )
            pane_sink = result.stdout.strip()
            if pane_sink:
                sink = pane_sink
        except Exception:
            pass

    # Pass sink to all playback calls
    play_sound(sound_path, volume=mixed_vol, sink=sink)
```

**Config**:
```yaml
audio:
  sink: ""  # default sink. Set to "hdmi-output-0", "bluetooth", etc.
```

### Tests

1. Default sink: plays normally (empty string = system default)
2. Config `audio.sink: "null"` → pw-play uses `--target=null`
3. tmux pane option: `tmux set-option -p @claude_audio_sink "headphones"` → routes to headphones
4. Pane option overrides config

### Commit message
```
feat(claude-voice): Phase 3.5 Layer 3 — per-pane PipeWire audio routing
```

---

## Layer 4: Agent Sound Profiles — RTS Model (3 hours)

**Goal**: Each persona has a sonic identity. Navigate to a pane → hear that agent's "select" sound. Warcraft 3 unit sound model.

**Why**: In a multi-agent tmux environment, SOUND tells you which agent you're interacting with before you read the screen. Each agent needs a distinctive audio signature.

### Files

| File | Action |
|------|--------|
| `lib/agents.py` | **CREATE** — agent profile resolution |
| `assets/themes/*/theme.json` | **MODIFY** — add `agent_sounds` section |
| `scripts/generate_agent_sounds.py` | **CREATE** — synthesize per-agent WAVs |
| `~/.claude/local/scripts/voice-focus-change.sh` | **MODIFY** — play select sound on focus |

### Sound Taxonomy

| Slot | Trigger | WC3 Equivalent | Description |
|------|---------|----------------|-------------|
| `select` | Pane gains focus | "What?" | Agent acknowledges your attention |
| `acknowledge` | SubagentStart | "Yes, my lord" | Agent accepts task |
| `complete` | SubagentStop | "Work complete" | Agent reports results |
| `error` | PostToolUseFailure | "We're under attack!" | Something went wrong |

### Theme.json Addition

```json
{
  "agent_sounds": {
    "matt": {
      "select": "sounds/agents/matt-select.wav",
      "acknowledge": "sounds/agents/matt-ack.wav",
      "complete": "sounds/agents/matt-done.wav",
      "error": "sounds/agents/matt-error.wav"
    },
    "darren": {
      "select": "sounds/agents/darren-select.wav"
    },
    "_default": {
      "select": "sounds/agents/default-select.wav",
      "acknowledge": "sounds/agents/default-ack.wav",
      "complete": "sounds/agents/default-done.wav",
      "error": "sounds/agents/default-error.wav"
    }
  }
}
```

### agents.py (~60 lines)

```python
def resolve_agent_sound(persona: str, slot: str, theme: dict) -> Path | None:
    """Resolve persona + slot to a WAV path from theme's agent_sounds."""
    agent_sounds = theme.get("agent_sounds", {})
    profile = agent_sounds.get(persona, agent_sounds.get("_default", {}))
    wav_name = profile.get(slot)
    if not wav_name:
        return None
    theme_slug = theme.get("meta", {}).get("slug", "default")
    path = THEMES_DIR / theme_slug / wav_name
    return path if path.exists() else None
```

### Integration

**SubagentStart**: play `acknowledge` sound for the agent persona
**SubagentStop**: play `complete` sound
**tmux focus change**: `voice-focus-change.sh` plays `select` sound via `pw-play`

### Sound Generation

`scripts/generate_agent_sounds.py` — numpy/scipy synthesis, same pipeline as existing `generate_sounds.py`. Each agent gets a unique tonal signature:
- matt: military ping (square wave, short, sharp)
- darren: organic chime (sine, warm, resonant)
- _default: neutral click (triangle wave, clean)

4 slots × 3 agents + _default = ~16 WAVs per theme. Generate for default theme first, others inherit via deep merge.

### Tests

1. Navigate to matt pane → matt's select sound plays
2. Navigate to unknown agent → _default select sound
3. SubagentStart → acknowledge sound
4. SubagentStop → complete sound
5. Theme switch → agent sounds follow theme

### Commit message
```
feat(claude-voice): Phase 3.5 Layer 4 — RTS agent sound profiles
```

---

## Layer 5: Ambient Engine (2 hours)

**Goal**: Background drone when subagents are running. Volume scales with agent count. Ducks during TTS.

**Why**: The ambient drone provides a continuous "system is working" signal — like hearing a computer's fans. When subagents stop, silence signals "done." This is the atmospheric layer of the spatial audio environment.

### Files

| File | Action |
|------|--------|
| `lib/ambient.py` | **CREATE** — loop management, PID tracking |
| `lib/constants.py` | **MODIFY** — add ambient paths |
| `lib/router.py` | **MODIFY** — start/stop ambient on SubagentStart/Stop |

### Implementation

**ambient.py** (~80 lines):
```python
class AmbientEngine:
    PID_FILE = VOICE_DATA_DIR / "ambient.pid"
    COUNT_FILE = VOICE_DATA_DIR / "ambient-count"

    def start_loop(self, wav_path: Path, volume: float = 0.3):
        if self.is_running():
            return  # Already playing
        proc = Popen(["pw-play", "--loop", f"--volume={volume:.3f}", str(wav_path)],
                     stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)
        self.PID_FILE.write_text(str(proc.pid))

    def stop_loop(self):
        if not self.PID_FILE.exists():
            return
        try:
            pid = int(self.PID_FILE.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except (ValueError, ProcessLookupError):
            pass
        self.PID_FILE.unlink(missing_ok=True)

    def is_running(self) -> bool:
        if not self.PID_FILE.exists():
            return False
        try:
            pid = int(self.PID_FILE.read_text().strip())
            os.kill(pid, 0)  # Check existence
            return True
        except (ValueError, ProcessLookupError):
            self.PID_FILE.unlink(missing_ok=True)
            return False

    def increment_agents(self):
        count = self._read_count() + 1
        self.COUNT_FILE.write_text(str(count))
        return count

    def decrement_agents(self) -> int:
        count = max(0, self._read_count() - 1)
        self.COUNT_FILE.write_text(str(count))
        return count

    def _read_count(self) -> int:
        try:
            return int(self.COUNT_FILE.read_text().strip())
        except (FileNotFoundError, ValueError):
            return 0
```

### Router Integration

```python
# In _route_event_inner():
if event_name == "SubagentStart":
    ambient = AmbientEngine()
    count = ambient.increment_agents()
    if not ambient.is_running():
        # Resolve ambient WAV from theme
        ambient_path = resolve_ambient_sound(theme)
        if ambient_path:
            ambient_vol = config.get("categories", {}).get("ambient", 0.3) * mixed_vol
            ambient.start_loop(ambient_path, volume=ambient_vol)

elif event_name == "SubagentStop":
    ambient = AmbientEngine()
    count = ambient.decrement_agents()
    if count == 0:
        ambient.stop_loop()

elif event_name == "SessionEnd":
    AmbientEngine().stop_loop()  # Always cleanup
```

### Tests

1. SubagentStart → ambient starts (if not already running)
2. SubagentStop (last agent) → ambient stops
3. SubagentStop (other agents still running) → ambient continues
4. SessionEnd → ambient stops regardless
5. Orphan PID (process died) → detected, cleaned up
6. No ambient WAV in theme → silently skipped

### Commit message
```
feat(claude-voice): Phase 3.5 Layer 5 — ambient engine with agent count scaling
```

---

## Layer 6: Voice Queue Daemon (3 hours)

**Goal**: Agents take turns speaking. Never two voices at once.

**Why**: The mixer controls volume, the queue controls timing. Two agents at 50% speaking simultaneously is still cacophony. The queue serializes speech with priority scheduling and speaker transitions.

### Files

| File | Action |
|------|--------|
| `scripts/voice_queue.py` | **CREATE** — queue daemon (~200 lines) |
| `lib/queue_client.py` | **CREATE** — enqueue function (~80 lines) |
| `lib/router.py` | **MODIFY** — route TTS through queue when available |
| `lib/constants.py` | **MODIFY** — add queue paths and defaults |
| `config.yaml` | **MODIFY** — add `queue` section |

### Design (from spec 18, derived from POC)

**Daemon** (`scripts/voice_queue.py`):
- `heapq` priority queue with `QueueItem.__lt__` (higher priority first, then FIFO)
- Unix socket at `~/.claude/local/voice/queue.sock`
- Newline-delimited JSON protocol
- Speaker transition: 300ms pause between different agents
- Expiration: items older than 30s dropped
- Interruption: priority ≥ 2 can preempt current speaker
- Idle timeout: 30 min (same as TTS daemon)
- PID file at `~/.claude/local/voice/queue.pid`

**Client** (`lib/queue_client.py`):
- `enqueue_speech(wav_path, priority, agent_id, volume)` → dict or None
- Returns None if daemon not running → caller plays directly (graceful degradation)
- One-shot connection per enqueue (hook processes are short-lived)

**Router integration**:
- After TTS synthesis returns a WAV path, try `enqueue_speech()`
- If enqueue succeeds: daemon handles playback timing
- If enqueue fails (daemon not running): `play_sound()` directly (current behavior)
- Earcons BYPASS the queue — they're <300ms, overlap risk is minimal

### Protocol

```
Client: {"type":"enqueue","wav_path":"/path.wav","priority":1,"agent_id":"matt","volume":0.8}
Daemon: {"type":"queued","id":"vq-1711...","position":0}
  ... daemon waits for current speech to finish ...
  ... 300ms speaker transition if different agent ...
Daemon: {"type":"play_now","id":"vq-1711...","wav_path":"/path.wav","volume":0.8}
  ... client (or daemon) plays WAV ...
Client: {"type":"playback_complete","id":"vq-1711...","duration_ms":2800}
```

### Config

```yaml
queue:
  enabled: true
  max_items: 50
  max_wait_seconds: 30
  speaker_transition_ms: 300
  interrupt_threshold: 2
```

### Tests

1. Two Stop events fire simultaneously → sequential, not overlapping
2. Error during speech → CRITICAL interrupts current speaker
3. 10 subagents complete → first few speak, rest expire
4. Different agents → audible 300ms pause
5. Same agent → no pause
6. Queue daemon not running → direct playback (graceful)
7. Kill queue daemon mid-speech → current playback completes normally

### Commit message
```
feat(claude-voice): Phase 3.5 Layer 6 — voice queue daemon (turn-taking)
```

---

## Implementation Schedule

| Session | Layers | Effort | Key Deliverable |
|---------|--------|--------|-----------------|
| Next | 1 + 2 | 1.5h | Cached state + STT multiplier (fast, foundational) |
| After | 3 | 2h | Per-pane routing (independent, testable alone) |
| After | 4 + 5 | 5h | Agent sounds + ambient (creative + mechanical) |
| After | 6 | 3h | Queue daemon (capstone — serializes all speech) |

**Total remaining: ~11.5 hours across 4 sessions.**

Each session: implement → sync cache → test → code review → fix → commit → journal.

---

## Dependency Graph

```
Layer 0 ✅ (spatial mixer)
  │
  ├── Layer 1 (cached state) ──── speeds up Layer 0
  │
  ├── Layer 2 (STT multiplier) ── temporal gate, additive
  │
  ├── Layer 3 (per-pane routing) ─ independent, WHERE not WHEN/HOW LOUD
  │
  ├── Layer 4 (agent sounds) ──── reads PERSONA_SLUG, plays from theme
  │
  ├── Layer 5 (ambient engine) ── SubagentStart/Stop triggers
  │
  └── Layer 6 (voice queue) ───── depends on all above for priority + volume
                                   but degrades gracefully without them
```

Layers 1-5 are independent of each other — can be built in any order.
Layer 6 benefits from all others but works without them (graceful degradation).

---

## Complete Exit Criteria (Phase 3.5)

- [x] Focused pane plays at full volume
- [x] Same-window panes play at 50% (configurable)
- [x] Same-session panes play at 20% (configurable)
- [x] Other-session panes are silent unless priority >= 2
- [x] Error/notification from ANY pane plays at 80% (priority floor)
- [x] All events log focus_state and effective_volume
- [x] All 7 themes still work
- [ ] Focus state cached in file for <1ms reads (Layer 1)
- [ ] STT-active flag suppresses all audio (Layer 2)
- [ ] Per-pane audio routing via `--target` (Layer 3)
- [ ] Agent select/acknowledge/complete sounds per persona (Layer 4)
- [ ] Ambient drone during subagent activity (Layer 5)
- [ ] Agents take turns speaking — never two voices at once (Layer 6)
- [ ] Speaker transitions have 300ms pause (Layer 6)
- [ ] Stale queued speech expires after 30s (Layer 6)
- [ ] Latency: earcon p95 < 150ms, TTS p95 < 700ms

---

## Risk Register

| Risk | Layer | Mitigation |
|------|-------|------------|
| tmux hook not installed → no cached file | 1 | Subprocess fallback works (current behavior) |
| STT flag left behind (orphaned) → permanent silence | 2 | Phase 4 STT engine cleans up on exit. SessionEnd hook removes flag. |
| pw-play --target wrong sink → no audio | 3 | Config validation on startup. Fallback to default sink. |
| Agent sound WAVs missing → silent navigation | 4 | `_default` profile as fallback. Generate all on install. |
| Ambient PID orphaned → loop plays forever | 5 | SessionEnd cleanup. PID liveness check on every SubagentStart. |
| Queue daemon not running → overlapping speech | 6 | Falls back to direct playback. TTS daemon still works independently. |
| Queue daemon + TTS daemon = 2 long-running processes | 6 | Both have idle timeouts. systemd services (task-103) for lifecycle. |
