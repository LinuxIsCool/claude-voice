---
title: "claude-voice — Volume Pipeline"
created: 2026-03-30
updated: 2026-03-30
author: matt
status: verified
tags: [claude-voice, volume, spatial-mixer, pipeline]
note: >
  Verified against router.py source on 2026-03-30. The math is correct.
  This doc traces every multiplication from config to speaker.
---

# claude-voice — Volume Pipeline

## The Pipeline

Three orthogonal filters compose via multiplication:

```
effective = max(base x spatial_mult, base x priority_floor)
if stt_active: effective = 0.0
```

Where:
- `base = clamp(master x category, 0.0, 1.0)`
- `spatial_mult = focus_volumes[focus_state]`
- `priority_floor = priority_floors[str(event_priority)]`

## Step-by-Step Trace

### Step 1: Master Volume

Source: `config.yaml` field `volume` or `$CLAUDE_VOICE_VOLUME` env var.

```python
master_volume = float(
    os.environ.get("CLAUDE_VOICE_VOLUME") or config.get("volume", 0.8)
)
```

Range: 0.0 to 1.0. Default: 0.8.

### Step 2: Category Volume

Each sound belongs to a category (earcon, notification, ambient).
Category multiplier comes from `config.yaml` field `categories`.

```python
category = get_sound_category(theme, sound_token)  # "earcon" | "notification" | "ambient"
category_volume = config.get("categories", {}).get(category, 1.0)
```

Default categories: `earcon: 1.0, notification: 1.0, ambient: 0.3`.

### Step 3: Base Volume

```python
base_volume = clamp(master_volume * category_volume, 0.0, 1.0)
```

Example: master=0.8, category=0.3 (ambient) -> base=0.24.

### Step 4: Spatial Focus State

Determined by `_get_focus_state()`:
1. No `$TMUX_PANE` -> "no_tmux" (full volume)
2. `focus-state` file matches `$TMUX_PANE` -> "focused"
3. Subprocess `tmux display-message` -> "focused" | "same_window" | "same_session" | "other_session"

### Step 5: Spatial Multiplier

```python
focus_mult = config["tmux"]["focus_volumes"].get(focus_state, 1.0)
spatial_vol = base_volume * focus_mult
```

Default focus_volumes:
```yaml
focused: 1.0
same_window: 0.5
same_session: 0.2
other_session: 0.0
no_tmux: 1.0
```

**Current config** (2026-03-30): ALL set to 1.0 (hear-everything mode).

### Step 6: Priority Floor

Events have priority 0, 1, or 2 (from theme.json `semantic_sounds`).

```python
floor_mult = config["tmux"]["priority_floors"].get(str(priority), 0.0)
floor_vol = base_volume * floor_mult
```

Default priority_floors:
```yaml
"2": 0.8    # errors/notifications always audible
"1": 0.0    # normal events follow spatial rules
"0": 0.0    # ambient events follow spatial rules
```

### Step 7: Effective Volume

```python
effective = clamp(max(spatial_vol, floor_vol), 0.0, 1.0)
```

Priority floor ensures critical events (p=2) are heard even from unfocused panes.

### Step 8: STT Suppression

Absolute override — if user is speaking, silence everything:

```python
if STT_ACTIVE_PATH.exists():
    effective = 0.0
```

### Step 9: Playback Decision

```python
if effective > 0.0:
    play_sound(sound_path, volume=effective)
```

TTS synthesis is also skipped when `effective == 0.0` to save GPU cycles.

## Worked Examples

### Example 1: Focused pane, normal task complete

```
master=0.8, category=earcon(1.0), base=0.8
focus_state=focused, spatial_mult=1.0, spatial_vol=0.8
priority=1, floor_mult=0.0, floor_vol=0.0
effective = max(0.8, 0.0) = 0.8
```

### Example 2: Background pane, error event (with designed defaults)

```
master=0.8, category=notification(1.0), base=0.8
focus_state=other_session, spatial_mult=0.0, spatial_vol=0.0
priority=2, floor_mult=0.8, floor_vol=0.64
effective = max(0.0, 0.64) = 0.64
```

Error is still heard at 64% volume even from a fully backgrounded session.

### Example 3: Same window split, ambient sound (with designed defaults)

```
master=0.8, category=ambient(0.3), base=0.24
focus_state=same_window, spatial_mult=0.5, spatial_vol=0.12
priority=0, floor_mult=0.0, floor_vol=0.0
effective = max(0.12, 0.0) = 0.12
```

### Example 4: Any pane, STT active

```
(any computation above)
stt_active=True -> effective=0.0
```

## Preset Configurations

| Preset | focused | same_window | same_session | other_session | Effect |
|--------|---------|-------------|--------------|---------------|--------|
| focus-only | 1.0 | 0.0 | 0.0 | 0.0 | Only active pane speaks |
| spatial | 1.0 | 0.5 | 0.2 | 0.0 | Graduated by cognitive distance |
| hear-all | 1.0 | 1.0 | 1.0 | 1.0 | Everything full volume |
| meeting | 0.1* | 0.0 | 0.0 | 0.0 | Near-silent (* also lowers master) |
