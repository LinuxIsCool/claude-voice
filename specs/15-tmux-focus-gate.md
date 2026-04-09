---
title: "Spec 15: Spatial Volume Mixer"
status: ready
phase: "3.5"
layer: 0
created: 2026-03-27
updated: 2026-03-27
author: matt
effort: 45min
tags: [tmux, spatial-mixer, volume, cross-talk, tts, phase-3.5]
---

# Spec 15: Spatial Volume Mixer (Phase 3.5, Layer 0)

> **Updated 2026-03-27**: Redesigned from binary focus gate to continuous spatial
> volume mixer. The binary gate (`_is_focused_pane() -> bool`) is a degenerate
> case of this mixer (set `same_window: 0.0, same_session: 0.0`). The mixer is
> simpler to implement (no branching), more general (handles priority floors and
> ambient ducking), and more extensible (new features = new multipliers).
>
> Previous version (binary gate) committed as `f2cbe8a` for reference.

## Problem

When multiple Claude instances run in different tmux panes, ALL of them produce
TTS speech on Stop events. The speech from unfocused panes gets picked up by
STT transcription in the focused pane, contaminating user input. This is the
primary blocker for voice-driven development in a multi-agent environment.

## Solution

Replace the binary focus gate with a continuous spatial volume mixer.
One tmux subprocess call returns three booleans (`pane_active`, `window_active`,
`session_attached`) that map to four spatial states. Each state has a configurable
volume multiplier. Priority sets a floor — critical events override spatial silencing.

Volume flows through a pipeline: `effective = max(base × spatial, floor × base)`.
No branching, no separate code paths for earcons vs TTS. Everything plays at
the mixer's output volume. If that's 0.0, nothing plays. If it's 0.5, you hear
a quiet version.

Fails open: if tmux state can't be determined, full volume (safer than silence).

## Prerequisites

- `TMUX_PANE` env var (set by tmux, available in all hooks — verified)
- `tmux display-message -p -t $PANE '#{pane_active} #{window_active} #{session_attached}'` — one call, three values
- Latency budget: ~5ms for the subprocess call (verified on this machine)
- Priority field already exists in `theme.json` per sound slot (0, 1, or 2)

## Design

### New constants in `constants.py`

```python
FOCUS_STATE_PATH = VOICE_DATA_DIR / "focus-state"
"""Cache file for spatial state. Written by tmux hook (Layer 1).
If present, used instead of subprocess call for lower latency.
Contains: 'focused', 'same_window', 'same_session', or 'other_session'."""

DEFAULT_FOCUS_VOLUMES = {
    "focused": 1.0,
    "same_window": 0.5,
    "same_session": 0.2,
    "other_session": 0.0,
    "no_tmux": 1.0,
}
"""Default spatial volume multipliers. Configurable via tmux.focus_volumes."""

DEFAULT_PRIORITY_FLOORS = {
    "2": 0.8,   # notification/error/permission — always audible
    "1": 0.0,   # task_complete/commit/agent_return — follows spatial rules
    "0": 0.0,   # session_start/prompt_ack/compact — follows spatial rules
}
"""Priority volume floors. Higher-priority events override spatial silencing."""
```

### New functions in `router.py`

```python
def _get_focus_state() -> str:
    """Determine this pane's spatial relationship to the focused pane.

    Returns: "focused", "same_window", "same_session", "other_session", or "no_tmux".

    Three-tier resolution:
      1. No TMUX_PANE → not in tmux → "no_tmux" (full volume)
      2. Cached focus-state file exists → read directly (~0.1ms)
      3. Subprocess fallback → tmux display-message (~5ms)

    Fails open: if focus cannot be determined, returns "no_tmux" (full volume).
    """
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return "no_tmux"

    # Tier 1: check cached file (written by Layer 1 tmux hook)
    try:
        from constants import FOCUS_STATE_PATH
        if FOCUS_STATE_PATH.exists():
            return FOCUS_STATE_PATH.read_text().strip() or "no_tmux"
    except Exception:
        pass

    # Tier 2: ask tmux directly (one call, three booleans)
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane,
             "#{pane_active} #{window_active} #{session_attached}"],
            capture_output=True, text=True, timeout=1,
        )
        parts = result.stdout.strip().split()
        pane_active = parts[0] == "1" if len(parts) > 0 else False
        window_active = parts[1] == "1" if len(parts) > 1 else False
        session_attached = parts[2] == "1" if len(parts) > 2 else False

        if pane_active and window_active:
            return "focused"
        elif window_active:
            return "same_window"
        elif session_attached:
            return "same_session"
        else:
            return "other_session"
    except Exception:
        return "no_tmux"  # Fail open


def _effective_volume(base: float, focus_state: str, priority: int, config: dict) -> float:
    """Compute effective volume from spatial state and priority.

    Volume pipeline:
      spatial_vol = base × focus_multiplier
      floor_vol   = base × priority_floor
      effective   = max(spatial_vol, floor_vol)

    This replaces branching (if focused: play, else: skip) with multiplication.
    The binary gate is the config {same_window: 0, same_session: 0, other_session: 0}.
    """
    tmux_config = config.get("tmux", {})
    focus_volumes = tmux_config.get("focus_volumes", DEFAULT_FOCUS_VOLUMES)
    priority_floors = tmux_config.get("priority_floors", DEFAULT_PRIORITY_FLOORS)

    focus_mult = focus_volumes.get(focus_state, 1.0)
    spatial_vol = base * focus_mult

    floor_mult = priority_floors.get(str(priority), 0.0)
    floor_vol = base * floor_mult

    return min(1.0, max(spatial_vol, floor_vol))
```

### Integration into `_route_event_inner()`

The mixer runs ONCE per event. Both earcons and TTS use the mixed volume.

```python
    # 5b. Spatial volume mixing
    focus_state = _get_focus_state()

    # Look up priority from theme's semantic_sounds
    semantic = theme.get("semantic_sounds", {}).get(sound_token, {})
    priority = semantic.get("priority", 0)

    # Compute effective volumes for earcon and TTS
    earcon_vol = _effective_volume(effective_volume, focus_state, priority, config)
    tts_vol = _effective_volume(effective_volume, focus_state, priority, config)

    # 6. Fire earcon playback at mixed volume
    if earcon_vol > 0.0:
        play_sound(sound_path, volume=earcon_vol)

    # 7. TTS at mixed volume (skip synthesis entirely if volume is 0)
    tts_text = None
    tts_voice = None
    tts_config = config.get("tts", {})
    if event_name == "SessionStart" and tts_vol > 0.0:
        tts_text, tts_voice = _play_cached_greeting(config, theme, tts_vol)
    elif event_name == "Stop" and tts_config.get("enabled") and tts_config.get("response", True):
        if tts_vol > 0.0:
            tts_text, tts_voice = _speak_response(hook_data, tts_config, tts_vol)
```

**No branching.** No separate gate for earcons vs TTS. Both go through the same
volume pipeline. If the mixer outputs 0.0, we skip playback/synthesis. If it
outputs 0.5, we play at half volume. If it outputs 1.0, full volume.

The only optimization: skip TTS synthesis when `tts_vol == 0.0` to avoid wasting
Kokoro GPU cycles on inaudible speech. Earcons are cheap (file read + pw-play),
so even playing them at 0.0 is harmless, but we skip them too for cleanliness.

### Config

```yaml
tmux:
  focus_volumes:
    focused: 1.0        # Full volume — this is my active agent
    same_window: 0.5    # Half volume — I can see this agent in a split
    same_session: 0.2   # Whisper — I'm one keypress away
    other_session: 0.0  # Silent — fully background
  priority_floors:
    "2": 0.8            # errors/notifications always audible at 80%
    "1": 0.0            # normal events follow spatial rules
    "0": 0.0            # ambient events follow spatial rules
```

**The binary gate is a config preset:**
```yaml
# "Binary gate" mode — set all non-focused to 0
tmux:
  focus_volumes:
    focused: 1.0
    same_window: 0.0
    same_session: 0.0
    other_session: 0.0
```

**"Hear everything" mode:**
```yaml
tmux:
  focus_volumes:
    focused: 1.0
    same_window: 1.0
    same_session: 1.0
    other_session: 1.0
```

Users tune to taste. The code doesn't change.

### Logging enhancement

Add `focus_gated` field to `log_event()`:

```python
def log_event(
    event, session_id, *,
    ...,
    focus_gated: bool = False,  # NEW: was this event silenced by focus gate?
) -> None:
```

And add a `focused` column to the SQLite schema:

```sql
ALTER TABLE events ADD COLUMN focused INTEGER;
```

This enables future analytics: "How many events were silenced? Is focus gating too
aggressive?"

### New import in `router.py`

```python
import subprocess  # For _is_focused_pane() tmux call
```

## Config Schema (complete tmux section)

```yaml
tmux:
  focus_gate_speech: true     # Silence TTS for unfocused panes
  focus_gate_earcons: false   # Silence earcons for unfocused panes
  # Future (Layer 1):
  # focus_hook: true          # Install tmux after-select-pane hook
  # Future (Layer 3):
  # per_pane_sink: false      # Route audio to per-pane PipeWire sink
```

## Implementation Steps

### Step 1: Add constants (constants.py)

Add `FOCUSED_PANE_PATH` to constants.py.

### Step 2: Add config defaults (state.py)

Add `tmux` section to `DEFAULT_CONFIG`:
```python
"tmux": {
    "focus_gate_speech": True,
    "focus_gate_earcons": False,
},
```

### Step 3: Add `_is_focused_pane()` to router.py

The function as specified above. Three-tier: no-tmux → cached file → subprocess.

### Step 4: Wire into `_route_event_inner()`

Call `_is_focused_pane()` once per event (cache the result). Gate earcons
at Point A and TTS at Point B using the config keys.

### Step 5: Update log_event() signature (logger.py)

Add `focus_gated: bool = False` parameter. Add `focused` column to SQLite
schema. Include in both JSONL and SQLite writes.

### Step 6: Update CLAUDE.md

Document the `tmux` config section with both keys.

### Step 7: Sync to cache, test, commit

- Sync all changed files to plugin cache
- Test: run hook from focused pane (should play), run from unfocused pane (TTS should be silent)
- Commit: `feat(claude-voice): Phase 3.5 Layer 0 — tmux focus gate`

## Testing Plan

### Test 1: Focused pane plays normally
```bash
# In the focused tmux pane:
echo '{"session_id":"test","type":"Stop","data":{"last_assistant_message":"Focus test."}}' \
  | uv run hooks/voice_event.py Stop
# Expected: earcon plays + TTS speaks
```

### Test 2: Unfocused pane is TTS-silent
```bash
# Switch to a different pane, then in the unfocused pane:
echo '{"session_id":"test","type":"Stop","data":{"last_assistant_message":"Focus test."}}' \
  | uv run hooks/voice_event.py Stop
# Expected: earcon plays (focus_gate_earcons=false), TTS is silent
```

### Test 3: Non-tmux environment
```bash
# Outside tmux entirely:
TMUX_PANE="" echo '{"session_id":"test","type":"Stop","data":{"last_assistant_message":"Focus test."}}' \
  | uv run hooks/voice_event.py Stop
# Expected: earcon plays + TTS speaks (fail open)
```

### Test 4: Full earcon gating
```bash
# Set focus_gate_earcons: true in config, then from unfocused pane:
# Expected: complete silence (both earcon and TTS gated)
```

### Test 5: Latency verification
```bash
# Time the _is_focused_pane() call:
time tmux display-message -p -t $TMUX_PANE '#{pane_active}'
# Expected: <10ms
```

### Test 6: Log verification
```bash
# After running focused + unfocused tests:
sqlite3 ~/.claude/local/voice/voice.db \
  "SELECT event, focused FROM events ORDER BY id DESC LIMIT 5;"
# Expected: focused=1 for focused test, focused=0 for unfocused test
```

## Latency Budget

| Operation | Time | Notes |
|-----------|------|-------|
| `os.environ.get("TMUX_PANE")` | ~0.001ms | Dict lookup |
| `FOCUSED_PANE_PATH.exists()` | ~0.05ms | Stat call |
| `FOCUSED_PANE_PATH.read_text()` | ~0.1ms | File read (Layer 1 cache) |
| `subprocess.run(tmux ...)` | ~5ms | Subprocess + tmux query |
| **Total (with cache)** | **~0.15ms** | Layer 1 file present |
| **Total (subprocess)** | **~5ms** | Layer 1 file absent |

Both are well within the 150ms budget. The focus check is the first thing that
runs after config load, so if the pane is unfocused and TTS is gated, we skip
the expensive TTS path entirely — saving 90-700ms of synthesis time.

## Risk

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| tmux not installed | Low | None | `TMUX_PANE` unset → fail open |
| Subprocess hangs | Very Low | Low | 1s timeout, fail open |
| Wrong pane detected | Low | Low | File cache validated against live tmux |
| Focus file stale | Medium | Low | Subprocess fallback always available |
| Breaks non-tmux users | None | None | No TMUX_PANE → always plays |

## Files Modified

| File | Change |
|------|--------|
| `lib/constants.py` | Add `FOCUSED_PANE_PATH` |
| `lib/state.py` | Add `tmux` section to `DEFAULT_CONFIG` |
| `lib/router.py` | Add `_is_focused_pane()`, wire into `_route_event_inner()`, add `import subprocess` |
| `lib/logger.py` | Add `focus_gated` param to `log_event()`, add `focused` column to schema |
| `CLAUDE.md` | Document `tmux` config section |
| `~/.claude/local/voice/config.yaml` | Add `tmux` section |

## Success Criteria

- [ ] Unfocused panes produce NO TTS speech
- [ ] Focused pane plays TTS normally
- [ ] Earcons play from all panes by default
- [ ] `focus_gate_earcons: true` silences earcons from unfocused panes
- [ ] Non-tmux environments (no TMUX_PANE) are unaffected
- [ ] Focus gate adds <10ms to hook latency
- [ ] Events log `focused` state for analytics
- [ ] All existing tests still pass

## What This Unblocks

With Layer 0 complete, Shawn can use voice I/O in the focused pane without
contamination from other agents' TTS. This is the prerequisite for:

- **Layer 1** (tmux focus hook) — lowers latency from 5ms to 0.15ms
- **Layer 2** (STT-active flag) — system-wide TTS suppression during speech input
- **Phase 4** (STT + Conversation) — can't build STT without focus gate
- **Voice queue daemon** — scheduling who speaks when assumes focus awareness
