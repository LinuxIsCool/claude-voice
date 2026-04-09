---
title: "Theme Engine — Schema, Sonic DNA & Hot-Swap"
spec: "02"
status: draft
created: 2026-03-26
author: matt
tags: [claude-voice, theme, sound-design, DRY]
---

# 02 — Theme Engine

## 1. Overview

The theme engine is claude-voice's single source of truth for sound identity. It mirrors claude-tmux's `theme.json` pattern: semantic tokens map to states, which map to hook events, which resolve to concrete sound files. Every sound decision flows through `theme.json`. No hardcoded sound paths, no hardcoded durations, no hardcoded frequencies anywhere else in the system.

The mapping chain:

```
hook event → hook_to_sound → semantic sound token → variant pool → WAV file path
```

This is the audio analog of claude-tmux's visual chain:

```
hook event → hook_to_state → state → semantic color → hex value
```

Both plugins consume the same 14 hook event types from Claude Code. Both use `theme.json` as their single source of truth. Both support hot-swap without restart. The patterns are identical — only the output modality differs (color/glyph vs. sound/speech).

A theme defines:
- **Sonic DNA** — the timbral identity shared by all sounds in the theme (instruments, frequency range, reverb character, emotional tone)
- **12 semantic sound tokens** — abstract event names (`task_complete`, `error`, `commit`) with variant pools, durations, priorities, and playback modes
- **Hook-to-sound mapping** — which hook events map to which semantic tokens
- **Content-aware overrides** — regex patterns on Stop hook output that reroute to different tokens
- **TTS personality** — greeting templates and voice modifiers per emotional state
- **Visual sync** — accent color and paired tmux theme for audio-visual coherence

---

## 2. Design Principles

### 2.1 ONE file defines a theme's entire sonic identity

`theme.json` is the theme. There is no second file. No sidecar. No environment variables that modify behavior. If you want to know what a theme sounds like, read its `theme.json`. If you want to change what a theme sounds like, edit its `theme.json`.

### 2.2 Semantic sound tokens, not filenames

Code references `"task_complete"`, never `"fanfare-03.wav"`. The mapping from semantic token to concrete file lives exclusively in `theme.json`. Library code (`lib/theme.py`, `lib/router.py`, `lib/playback.py`) is completely theme-agnostic — it operates on tokens, paths, priorities, and playback modes. Swap the theme, and the same code produces entirely different sounds.

### 2.3 All theme-specific values live in theme.json

Durations, frequency ranges, priority levels, variant counts, TTS greeting templates, playback modes, content-aware regex patterns — all of it lives in `theme.json`. The `lib/` code contains zero theme-specific constants. This is the DRY principle applied ruthlessly: one change, one file, one place.

### 2.4 Theme inheritance

Every theme extends `default/theme.json`. A game theme only needs to override what differs from the default. If StarCraft doesn't specify a `compact` sound, it inherits the default's. Deep merge for nested objects, full replacement for variant arrays. This keeps game themes focused on their personality rather than boilerplate.

### 2.5 Variant pools for anti-habituation

Each semantic sound token has 3-7 WAV variants. The Sound Router picks one at random on each event. Research (Wwise/FMOD game audio middleware, Blizzard's acknowledgment pattern) shows that fewer than 3 variants causes rapid habituation — the brain stops noticing the sound. More than 7 dilutes sonic identity — the variants stop feeling like "the same event." The sweet spot is 3-7.

### 2.6 Hot-swap requires zero restarts

Changing the active theme (via `/voice theme starcraft`, config.yaml edit, or `CLAUDE_VOICE_THEME` env var) takes effect on the very next hook event. No daemon to restart, no cache to invalidate, no state to rebuild. Each hook invocation reads `config.yaml` fresh, loads the appropriate `theme.json`, and resolves the sound. The filesystem is the IPC.

---

## 3. theme.json Schema

### 3.1 Complete Example (StarCraft)

```json
{
  "meta": {
    "name": "StarCraft",
    "slug": "starcraft",
    "version": "1.0.0",
    "description": "Terran command interface — military precision, servo whirring, sci-fi HUD",
    "author": "claude-voice",
    "sonic_dna": {
      "frequency_range": "200-6000Hz",
      "instrument_palette": ["synth_lead", "digital_percussion", "metallic_impact", "radio_chirp", "servo_motor"],
      "cultural_reference": "Blizzard Entertainment StarCraft (1998), Terran faction UI",
      "emotional_tone": "military_precision",
      "reverb": "metallic_chamber"
    }
  },
  "semantic_sounds": {
    "session_start": {
      "variants": ["boot-01.wav", "boot-02.wav", "boot-03.wav"],
      "duration_ms": 600,
      "priority": 0,
      "mode": "overlap",
      "category": "earcon"
    },
    "session_end": {
      "variants": ["shutdown-01.wav", "shutdown-02.wav", "shutdown-03.wav"],
      "duration_ms": 400,
      "priority": 0,
      "mode": "overlap",
      "category": "earcon"
    },
    "prompt_ack": {
      "variants": ["ack-01.wav", "ack-02.wav", "ack-03.wav", "ack-04.wav", "ack-05.wav"],
      "duration_ms": 150,
      "priority": 0,
      "mode": "debounce",
      "category": "earcon"
    },
    "task_complete": {
      "variants": ["complete-01.wav", "complete-02.wav", "complete-03.wav", "complete-04.wav", "complete-05.wav"],
      "duration_ms": 300,
      "priority": 1,
      "mode": "overlap",
      "category": "earcon"
    },
    "agent_deploy": {
      "variants": ["deploy-01.wav", "deploy-02.wav", "deploy-03.wav"],
      "duration_ms": 250,
      "priority": 0,
      "mode": "overlap",
      "category": "earcon"
    },
    "agent_return": {
      "variants": ["return-01.wav", "return-02.wav", "return-03.wav"],
      "duration_ms": 300,
      "priority": 1,
      "mode": "overlap",
      "category": "earcon"
    },
    "error": {
      "variants": ["error-01.wav", "error-02.wav", "error-03.wav"],
      "duration_ms": 250,
      "priority": 2,
      "mode": "interrupt",
      "category": "notification"
    },
    "notification": {
      "variants": ["alert-01.wav", "alert-02.wav", "alert-03.wav"],
      "duration_ms": 300,
      "priority": 2,
      "mode": "interrupt",
      "category": "notification"
    },
    "commit": {
      "variants": ["levelup-01.wav", "levelup-02.wav", "levelup-03.wav"],
      "duration_ms": 500,
      "priority": 1,
      "mode": "overlap",
      "category": "earcon"
    },
    "permission": {
      "variants": ["attention-01.wav", "attention-02.wav", "attention-03.wav"],
      "duration_ms": 200,
      "priority": 2,
      "mode": "interrupt",
      "category": "notification"
    },
    "compact": {
      "variants": ["compress-01.wav", "compress-02.wav", "compress-03.wav"],
      "duration_ms": 200,
      "priority": 0,
      "mode": "overlap",
      "category": "earcon"
    },
    "ambient": {
      "variants": ["ambient-loop.wav"],
      "duration_ms": -1,
      "priority": 0,
      "mode": "loop",
      "category": "ambient"
    }
  },
  "hook_to_sound": {
    "SessionStart": "session_start",
    "SessionEnd": "session_end",
    "UserPromptSubmit": "prompt_ack",
    "Stop": "task_complete",
    "SubagentStart": "agent_deploy",
    "SubagentStop": "agent_return",
    "PostToolUseFailure": "error",
    "Notification": "notification",
    "PermissionRequest": "permission",
    "PreCompact": "compact"
  },
  "content_aware_overrides": {
    "Stop": {
      "patterns": {
        "git commit|committed|Created commit": "commit",
        "error|Error|ERROR|failed|Failed|FAILED|exception|Exception": "error",
        "test.*pass|tests passed|All.*pass": "task_complete"
      }
    }
  },
  "tts": {
    "voice_id": null,
    "greeting_template": "Commander, {summary}.",
    "personality_modifiers": {
      "success": { "pitch_shift": 0, "speed": 1.0 },
      "error": { "pitch_shift": -2, "speed": 0.9 },
      "alert": { "pitch_shift": 2, "speed": 1.1 }
    }
  },
  "visual_sync": {
    "tmux_theme": "default",
    "accent_color": "#00ff00"
  }
}
```

### 3.2 Field Reference

#### `meta` (object, required)

Top-level metadata identifying the theme.

| Field | Type | Required | Default | Constraints | Description |
|-------|------|----------|---------|-------------|-------------|
| `meta.name` | string | yes | — | 1-64 chars, human-readable | Display name shown in theme listings and status bar. Example: `"StarCraft"`. |
| `meta.slug` | string | yes | — | lowercase, alphanumeric + hyphens, must match directory name | Machine identifier used in config.yaml, env vars, and filesystem paths. Example: `"starcraft"`. Must match the directory name under `assets/themes/`. |
| `meta.version` | string | yes | `"1.0.0"` | SemVer format `MAJOR.MINOR.PATCH` | Theme version. Increment MAJOR for breaking schema changes, MINOR for new sounds, PATCH for variant additions or metadata tweaks. |
| `meta.description` | string | yes | — | 1-256 chars | One-line description of the theme's sonic personality. Shown in `/voice theme list` output. |
| `meta.author` | string | recommended | `"claude-voice"` | Free text | Theme author. All built-in themes use `"claude-voice"`. Community themes use the contributor's name. |
| `meta.sonic_dna` | object | recommended | `{}` | — | The theme's timbral identity. Not used by code — exists for documentation, synthesis guidance, and future theme-generation tools. |
| `meta.sonic_dna.frequency_range` | string | optional | `"200-4000Hz"` | Format: `"<low>-<high>Hz"` | The frequency band where most of the theme's sonic energy lives. Guides synthesis scripts and helps distinguish themes at a glance. |
| `meta.sonic_dna.instrument_palette` | string[] | optional | `[]` | — | List of timbral descriptors for the synthesis pipeline. Not filenames — conceptual labels like `"synth_lead"`, `"war_drum"`, `"chiptune_square"`. |
| `meta.sonic_dna.cultural_reference` | string | optional | `""` | Free text | The game, era, or franchise this theme evokes. Exists for human context, not code. Example: `"Blizzard Entertainment StarCraft (1998), Terran faction UI"`. |
| `meta.sonic_dna.emotional_tone` | string | optional | `""` | Free text | One-word or short-phrase emotional descriptor. Example: `"military_precision"`, `"whimsical_adventure"`, `"competitive_energy"`. |
| `meta.sonic_dna.reverb` | string | optional | `""` | Free text | The reverb character of the sonic space. Example: `"metallic_chamber"`, `"stone_cathedral"`, `"open_arena"`. Guides synthesis reverb parameters. |

#### `semantic_sounds` (object, required)

Maps each of the 12 semantic sound tokens to its configuration. Keys are the token names (see Section 4 for the full reference).

| Field | Type | Required | Default | Constraints | Description |
|-------|------|----------|---------|-------------|-------------|
| `semantic_sounds.<token>` | object | required (per token) | inherited from default theme | — | Configuration for one semantic sound token. Tokens not present in a game theme are inherited from `default/theme.json`. |
| `semantic_sounds.<token>.variants` | string[] | required | — | 1-10 entries, each a valid filename | Array of WAV filenames relative to the theme's `sounds/` directory. The Sound Router picks one at random on each event. Minimum 1 (for sounds that should never vary, like `permission`). Recommended 3-7 for frequently-heard events. |
| `semantic_sounds.<token>.duration_ms` | integer | required | — | -1 or 50-2000 | Expected duration of the sound in milliseconds. Used for debounce calculations and queue scheduling, NOT for truncating playback. The WAV file plays to completion regardless. `-1` means looping/indefinite (used only for `ambient`). |
| `semantic_sounds.<token>.priority` | integer | required | `0` | 0, 1, or 2 | Priority level for playback conflict resolution. `0` = low (yield to everything), `1` = normal (queue behind high), `2` = high (interrupt low/normal). See playback modes below. |
| `semantic_sounds.<token>.mode` | string | required | `"overlap"` | One of: `"overlap"`, `"debounce"`, `"interrupt"`, `"loop"` | Playback mode governing concurrent sound behavior. See Section 3.3. |
| `semantic_sounds.<token>.category` | string | recommended | `"earcon"` | One of: `"earcon"`, `"notification"`, `"ambient"` | Functional category for volume group assignment. `earcon` = brief interface sounds (affected by earcon volume). `notification` = attention-demanding sounds (affected by notification volume). `ambient` = background soundscapes (affected by ambient volume). |
| `semantic_sounds.<token>.weights` | number[] | optional | uniform distribution | Same length as `variants`, each >= 0, sum > 0 | Weighted random selection. If present, variant `i` is selected with probability `weights[i] / sum(weights)`. Allows themes to make certain variants more or less common. |

#### `hook_to_sound` (object, required)

Maps Claude Code hook event names to semantic sound tokens.

| Field | Type | Required | Default | Constraints | Description |
|-------|------|----------|---------|-------------|-------------|
| `hook_to_sound.<HookEvent>` | string | per-event | inherited from default | Must be a key in `semantic_sounds` | The semantic sound token to play when this hook event fires. Example: `"SessionStart": "session_start"`. The full set of supported hook events is listed below. |

Supported hook events and their default mappings:

| Hook Event | Default Sound Token | Notes |
|------------|-------------------|-------|
| `SessionStart` | `session_start` | Fires once when Claude Code session initializes |
| `SessionEnd` | `session_end` | Fires when session closes |
| `UserPromptSubmit` | `prompt_ack` | Fires on every user message (disabled by default in config.yaml) |
| `Stop` | `task_complete` | Fires when Claude finishes a response (subject to content-aware overrides) |
| `SubagentStart` | `agent_deploy` | Fires when a subagent is spawned |
| `SubagentStop` | `agent_return` | Fires when a subagent completes |
| `PostToolUseFailure` | `error` | Fires when a tool use fails |
| `Notification` | `notification` | Fires on system notifications |
| `PermissionRequest` | `permission` | Fires when Claude requests user permission |
| `PreCompact` | `compact` | Fires before context compaction |

Hook events NOT mapped (by design):
- `PreToolUse` / `PostToolUse` — 44K+ events in production. Pure noise.
- `Setup` — one-time install event. No recurring audio value.
- `PostCompact` — `PreCompact` already signals the event.

#### `content_aware_overrides` (object, optional)

Regex-based rerouting of specific hook events to different sound tokens based on the content of the assistant's output.

| Field | Type | Required | Default | Constraints | Description |
|-------|------|----------|---------|-------------|-------------|
| `content_aware_overrides.<HookEvent>` | object | optional | `{}` | Hook event name as key | Container for pattern overrides on a specific hook event. Currently only `Stop` is meaningfully content-aware (it receives `last_assistant_message`). |
| `content_aware_overrides.<HookEvent>.patterns` | object | optional | `{}` | Keys are regex patterns, values are sound token names | Ordered map of regex patterns to sound tokens. First matching pattern wins. If no pattern matches, the default `hook_to_sound` mapping is used. Patterns are matched against `last_assistant_message` from the hook payload. |

Pattern matching rules:
1. Patterns are evaluated top-to-bottom (JSON key order, which is insertion order in Python 3.7+).
2. Each pattern is compiled as a Python `re.search()` regex (case-sensitive by default).
3. First match wins — subsequent patterns are not evaluated.
4. If no pattern matches, the default `hook_to_sound` mapping is used.
5. The target sound token must exist in `semantic_sounds` (either in this theme or inherited from default).

#### `tts` (object, optional)

Text-to-speech configuration for this theme.

| Field | Type | Required | Default | Constraints | Description |
|-------|------|----------|---------|-------------|-------------|
| `tts.voice_id` | string or null | optional | `null` | Provider-specific voice ID, or null for system default | The TTS voice to use when this theme is active. `null` means use the system default voice from `config.yaml`. Example: `"pNInz6obpgDQGcFmaJgB"` (ElevenLabs voice ID). |
| `tts.greeting_template` | string | optional | `"{summary}"` | Must contain `{summary}` placeholder | Template for the session-start greeting spoken via TTS. The `{summary}` placeholder is replaced with a compressed (<20 word) summary of the session context. Each theme gives this greeting character. |
| `tts.personality_modifiers` | object | optional | `{}` | Keys are emotional states | Per-emotion voice adjustments applied during TTS synthesis. |
| `tts.personality_modifiers.<state>.pitch_shift` | number | optional | `0` | -12 to +12 (semitones) | Pitch adjustment in semitones. Negative = deeper, positive = higher. Applied to the TTS output. |
| `tts.personality_modifiers.<state>.speed` | number | optional | `1.0` | 0.5 to 2.0 | Playback speed multiplier. <1.0 = slower, >1.0 = faster. Applied to the TTS output. |

Recognized emotional states for personality modifiers:
- `success` — task completed successfully, commit, test pass
- `error` — tool failure, exception, build fail
- `alert` — notification, permission request, urgent attention

#### `visual_sync` (object, optional)

Coordinates with claude-tmux for audio-visual coherence.

| Field | Type | Required | Default | Constraints | Description |
|-------|------|----------|---------|-------------|-------------|
| `visual_sync.tmux_theme` | string | optional | `"default"` | Must match a claude-tmux theme slug | The claude-tmux theme that pairs with this audio theme. Allows automatic visual theme switching when the audio theme changes. |
| `visual_sync.accent_color` | string | optional | `"#a6e3a1"` | CSS hex color | The theme's signature color. Used by claude-statusline to tint the audio indicator. Provides a quick visual signal of which theme is active. |

### 3.3 Playback Modes

Each semantic sound token declares a `mode` that governs its behavior when concurrent sounds are in play.

| Mode | Behavior | Use Case |
|------|----------|----------|
| `overlap` | Plays immediately regardless of other sounds. Multiple overlapping sounds mix naturally via PipeWire. | Most earcons. Task completion, agent deploy/return, session start/end. Quick sounds that don't fight for attention. |
| `debounce` | Suppressed if the same token fired within the last `duration_ms`. Prevents machine-gun repetition from rapid events. | `prompt_ack` — if the user pastes 5 prompts in quick succession, only the first plays. |
| `interrupt` | Kills any currently-playing lower-priority sound before playing. Ensures this sound is heard clearly. | `error`, `notification`, `permission` — high-priority sounds that demand attention and must not be buried under ambient or earcon layers. |
| `loop` | Plays continuously, restarting when the WAV ends. Managed as a long-lived `pw-play` process rather than a one-shot. | `ambient` — background soundscape. Only one loop plays at a time. A new loop replaces the old one. |

Priority + mode interaction matrix:

| Incoming Priority | Current Playing Priority | Incoming Mode | Result |
|-------------------|------------------------|---------------|--------|
| 0 (low) | 0 (low) | overlap | Both play simultaneously |
| 0 (low) | 2 (high) | overlap | Incoming plays (overlap ignores priority) |
| 1 (normal) | 0 (low) | overlap | Both play simultaneously |
| 2 (high) | 0 (low) | interrupt | Current killed, incoming plays |
| 2 (high) | 1 (normal) | interrupt | Current killed, incoming plays |
| 2 (high) | 2 (high) | interrupt | Current killed, incoming plays |
| 0 (low) | any | debounce | Suppressed if same token within duration_ms |

---

## 4. Semantic Sound Token Reference

The 12 canonical semantic sound tokens that every theme must define (or inherit from default):

| # | Token | Description | Duration Range | Priority | Mode | Category | Recommended Variants | Hook Source |
|---|-------|-------------|---------------|----------|------|----------|---------------------|-------------|
| 1 | `session_start` | Session initialization. The theme's signature intro. Sets the sonic identity for the session. Longest permitted earcon. | 400-800ms | 0 (low) | overlap | earcon | 3-5 | `SessionStart` |
| 2 | `session_end` | Session shutdown. Gentle resolution — the complementary inverse of `session_start`. Lower pitch, quieter. | 300-500ms | 0 (low) | overlap | earcon | 3 | `SessionEnd` |
| 3 | `prompt_ack` | User prompt received. Brief acknowledgment confirming input was accepted. Must be under 200ms or it becomes oppressive. Disabled by default. | 80-200ms | 0 (low) | debounce | earcon | 5-7 | `UserPromptSubmit` |
| 4 | `task_complete` | Claude finished a response. The workhorse sound, heard most often. Satisfying but not celebratory. Most variants to combat habituation. | 200-400ms | 1 (normal) | overlap | earcon | 5-7 | `Stop` |
| 5 | `agent_deploy` | Subagent spawned. Outward energy — a unit leaving the base. Slightly lower volume than `task_complete`. | 150-300ms | 0 (low) | overlap | earcon | 3-5 | `SubagentStart` |
| 6 | `agent_return` | Subagent completed. Inward energy — a unit reporting back. Slightly more prominent than `agent_deploy`. | 200-400ms | 1 (normal) | overlap | earcon | 3-5 | `SubagentStop` |
| 7 | `error` | Tool failure. Sharp, dissonant, attention-grabbing. Must be distinct from every other token. | 150-300ms | 2 (high) | interrupt | notification | 3-5 | `PostToolUseFailure` |
| 8 | `notification` | System notification. Brighter than `error`, more urgent than `task_complete`. Comm-channel-open feeling. | 200-400ms | 2 (high) | interrupt | notification | 3-5 | `Notification` |
| 9 | `commit` | Git commit detected (via content-aware routing). Level-up fanfare — more celebratory than `task_complete`. Small achievement moment. | 300-600ms | 1 (normal) | overlap | earcon | 3-5 | `Stop` (content-aware) |
| 10 | `permission` | Claude requests user permission. Sharp attention snap — unmistakable. Fewest variants for maximum recognition consistency. | 100-200ms | 2 (high) | interrupt | notification | 3 | `PermissionRequest` |
| 11 | `compact` | Context window compaction imminent. Informational, low-priority. Mechanical/compression character. | 150-300ms | 0 (low) | overlap | earcon | 3 | `PreCompact` |
| 12 | `ambient` | Background soundscape driven by time-of-day via claude-rhythms. Not an earcon — a continuous loop. Lowest priority, yields to all other sounds. | looping (-1) | 0 (low) | loop | ambient | per-phase | Rhythms Bridge |

Total sound inventory per theme: 12 tokens x 3-7 variants = 36-84 WAV files.

---

## 5. Theme Resolution Algorithm

When a hook fires, `lib/theme.py` resolves it to a concrete WAV file path through this deterministic sequence:

```
Step 1: Determine active theme
    ├── Check CLAUDE_VOICE_THEME environment variable
    ├── If not set, check persona_themes mapping (see Section 9)
    ├── If no persona match, read theme from ~/.claude/local/voice/config.yaml
    └── If config missing, fall back to "default"

Step 2: Load theme.json
    ├── Path: ${CLAUDE_PLUGIN_ROOT}/assets/themes/{slug}/theme.json
    ├── Parse JSON
    └── If file missing or invalid, fall back to default/theme.json

Step 3: Apply inheritance (merge with default)
    ├── Load default/theme.json as base
    ├── Deep merge: theme values override default values
    ├── For semantic_sounds.<token>: if token exists in theme, use entire token object
    │   (variant arrays are REPLACED, not merged)
    ├── For semantic_sounds.<token>: if token missing in theme, use default's token
    ├── For hook_to_sound: theme entries override, missing entries inherit
    ├── For content_aware_overrides: theme entries override, missing entries inherit
    ├── For tts: theme entries override, missing entries inherit
    └── For visual_sync: theme entries override, missing entries inherit

Step 4: Map hook event to semantic sound token
    ├── Look up event name in hook_to_sound (e.g., "Stop" → "task_complete")
    └── If event not in hook_to_sound, return None (no sound plays)

Step 5: Check content-aware overrides
    ├── If event has content_aware_overrides entry
    ├── Extract last_assistant_message from hook payload
    ├── Iterate patterns in order
    ├── re.search(pattern, message) for each
    ├── First match: reroute to the override's sound token
    └── No match: keep original sound token from Step 4

Step 6: Resolve semantic sound token to configuration
    ├── Look up token in merged semantic_sounds
    └── If token not found, return None (no sound plays)

Step 7: Select variant
    ├── Read variants array from token config
    ├── If weights present: random.choices(variants, weights=weights, k=1)[0]
    ├── If no weights: random.choice(variants)
    ├── Optional no-repeat: if selected == _last_variant[token], re-roll once
    └── Store selected variant in _last_variant[token] for next check

Step 8: Construct file path
    └── ${CLAUDE_PLUGIN_ROOT}/assets/themes/{slug}/sounds/{variant_filename}

Step 9: Return resolution result
    └── (path, priority, mode, duration_ms, category)
        or None if any step resolved to nothing
```

### Resolution Function Signature

```python
@dataclass
class SoundResolution:
    path: Path
    priority: int           # 0, 1, or 2
    mode: str               # "overlap", "debounce", "interrupt", "loop"
    duration_ms: int         # -1 for loops
    category: str           # "earcon", "notification", "ambient"
    token: str              # semantic token name for logging
    variant: str            # selected variant filename for logging

def resolve_sound(
    event_type: str,
    hook_data: dict,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> SoundResolution | None:
    """
    Resolve a hook event to a concrete sound file.
    Returns None if no sound should play (muted, no mapping, missing file).
    """
```

---

## 6. Theme Inheritance

### The Principle

`default/theme.json` is the complete, fully-populated base theme. Every field has a value. Every semantic sound token has variants. Every hook event has a mapping. A game theme only overrides what makes it distinctive.

### Merge Rules

| Field Type | Merge Strategy | Rationale |
|-----------|---------------|-----------|
| Scalar (`meta.name`, `meta.slug`, etc.) | Replace | A theme's name is its own, not merged with default's. |
| `semantic_sounds.<token>` (whole object) | Replace if present, inherit if absent | If StarCraft defines `task_complete`, StarCraft's entire `task_complete` object is used (variants, duration, priority, mode, category). If StarCraft doesn't define `compact`, default's `compact` object is used whole. Partial overrides within a token are NOT supported — this prevents confusing half-default, half-custom configurations. |
| `semantic_sounds.<token>.variants` (array) | Replace entirely (never merge) | A theme's variants are its own sounds. Appending default variants to a game theme's pool would contaminate the sonic identity. StarCraft's `error` sounds nothing like default's `error` — they must not be mixed. |
| `hook_to_sound` (object) | Shallow merge (theme keys override, missing keys inherit) | Most themes use the same hook-to-sound mapping. A theme can reroute a specific hook to a different token without redefining the entire mapping. |
| `content_aware_overrides` (object) | Shallow merge at the hook level | If StarCraft defines overrides for `Stop`, those replace default's `Stop` overrides entirely. If StarCraft doesn't define overrides for a hook, default's overrides are inherited. |
| `tts` (object) | Deep merge (per-field) | A theme might override `greeting_template` but inherit `personality_modifiers`. |
| `visual_sync` (object) | Deep merge (per-field) | A theme might override `accent_color` but inherit `tmux_theme`. |
| `meta.sonic_dna` (object) | Replace entirely | Sonic DNA is the theme's identity — there is no meaningful merge with default's DNA. |

### Inheritance Example

Given `default/theme.json`:
```json
{
  "semantic_sounds": {
    "task_complete": {
      "variants": ["complete-01.wav", "complete-02.wav", "complete-03.wav"],
      "duration_ms": 300,
      "priority": 1,
      "mode": "overlap",
      "category": "earcon"
    },
    "compact": {
      "variants": ["compress-01.wav", "compress-02.wav"],
      "duration_ms": 200,
      "priority": 0,
      "mode": "overlap",
      "category": "earcon"
    }
  }
}
```

And `starcraft/theme.json`:
```json
{
  "semantic_sounds": {
    "task_complete": {
      "variants": ["sc-complete-01.wav", "sc-complete-02.wav", "sc-complete-03.wav", "sc-complete-04.wav", "sc-complete-05.wav"],
      "duration_ms": 350,
      "priority": 1,
      "mode": "overlap",
      "category": "earcon"
    }
  }
}
```

Merged result:
- `task_complete`: StarCraft's definition (5 variants, 350ms) — fully replaced.
- `compact`: default's definition (2 variants, 200ms) — inherited because StarCraft didn't define it.

### Pseudo-code

```python
def merge_themes(default: dict, theme: dict) -> dict:
    merged = copy.deepcopy(default)

    # Replace scalars in meta
    if "meta" in theme:
        merged["meta"].update(theme["meta"])
        # sonic_dna replaces entirely
        if "sonic_dna" in theme.get("meta", {}):
            merged["meta"]["sonic_dna"] = theme["meta"]["sonic_dna"]

    # semantic_sounds: replace whole tokens, inherit missing
    if "semantic_sounds" in theme:
        for token, config in theme["semantic_sounds"].items():
            merged["semantic_sounds"][token] = config

    # hook_to_sound: shallow merge
    if "hook_to_sound" in theme:
        merged["hook_to_sound"].update(theme["hook_to_sound"])

    # content_aware_overrides: per-hook replace
    if "content_aware_overrides" in theme:
        for hook, overrides in theme["content_aware_overrides"].items():
            merged["content_aware_overrides"][hook] = overrides

    # tts: deep merge
    if "tts" in theme:
        merged.setdefault("tts", {})
        merged["tts"].update(theme["tts"])

    # visual_sync: deep merge
    if "visual_sync" in theme:
        merged.setdefault("visual_sync", {})
        merged["visual_sync"].update(theme["visual_sync"])

    return merged
```

---

## 7. Six Theme Profiles

### 7.1 StarCraft (starcraft)

**Cultural Reference**: Blizzard Entertainment StarCraft (1998) — Terran faction. The command interface of a deep-space military outpost. Every sound carries the weight of a military decision.

**Sonic DNA**:
- **Frequency range**: 200-6000Hz — mid-heavy with metallic upper harmonics
- **Instrument palette**: Synth lead, digital percussion, metallic impacts, radio chirps, servo motors, distorted guitar stabs
- **Emotional tone**: Military precision — controlled urgency, competence, authority
- **Reverb**: Metallic chamber — the inside of a battlecruiser bridge. Short decay, hard reflections.

**Per-Event Sound Descriptions**:

| Token | StarCraft Sound | Duration |
|-------|----------------|----------|
| `session_start` | Terran UI boot sequence — ascending digital sweep with system-online confirmation tone. Screen flickering to life. Adjutant coming online. Three servo whirs building to a solid confirmation beep. | 600ms |
| `session_end` | Systems powering down — descending digital sweep, complementary inverse of boot. Reactor cooling. Screens dimming. A single low-register confirmation tone. | 400ms |
| `prompt_ack` | SCV acknowledgment beep — short, crisp, confirming. Like a unit responding to a select command. Single digital chirp with slight metallic ring. | 150ms |
| `task_complete` | "Job's done" completion chime — rising two-note resolution in the terran UI register. Satisfying but not celebratory. The workhorse sound. Clean digital tones, minor metallic undertone. | 300ms |
| `agent_deploy` | "Carrier has arrived" deployment sweep — a unit leaving the base. Outward energy. Thruster-ignition whoosh compressed into a short digital burst. Directional, forward-moving. | 250ms |
| `agent_return` | Incoming transmission chirp — the scanner pings before the report arrives. Two quick ascending beeps then a brief data-received confirmation. Inward, returning energy. | 300ms |
| `error` | "Under attack" alert — sharp, dissonant, attention-grabbing without being alarming. Two fast descending tones with a hard metallic attack. Red-spectrum timbre. Unmistakable. | 250ms |
| `notification` | Alert klaxon — comm-channel-open ping. Brighter and higher than error. A single bright tone with a quick echo, like a radar blip demanding attention. | 300ms |
| `commit` | Level-up fanfare — ascending triumphant three-note sequence with reverb tail. More celebratory than task_complete. The "nuclear launch" gravitas scaled to a checkpoint moment. | 500ms |
| `permission` | Attention snap — high-pitched sharp digital ping. Two fast staccato tones, like a priority comm channel forcing open. Maximum clarity, minimum ambiguity. | 200ms |
| `compact` | Data compression crunch — digital squeeze sound. Rapid descending pitch sweep with bitcrusher texture. Memory banks reorganizing. Quick and mechanical. | 200ms |
| `ambient` | Deep-space bridge hum — low-frequency steady drone with occasional distant radar pings and servo whispers. Barely perceptible. The sound of a command center at rest. | looping |

**TTS Greeting Template**: `"Commander, {summary}."`

**Visual Sync**: Accent color `#00ff00` (terran green), tmux theme `default`.

---

### 7.2 Warcraft (warcraft)

**Cultural Reference**: Blizzard Entertainment Warcraft III (2002) and World of Warcraft (2004) — Alliance/Horde hybrid. Medieval fantasy with campfire warmth. Peons building, drums in the distance, spells crackling.

**Sonic DNA**:
- **Frequency range**: 100-4000Hz — low-heavy, warm, organic
- **Instrument palette**: War drums, horn brass, wooden impacts, ambient nature, anvil strikes, spell chimes, plucked strings
- **Emotional tone**: Industrious warmth — "work work" energy, loyal service, pride in completion
- **Reverb**: Stone great hall — medium decay, warm reflections. A castle interior with a roaring fireplace.

**Per-Event Sound Descriptions**:

| Token | Warcraft Sound | Duration |
|-------|---------------|----------|
| `session_start` | Orc drum intro — three deep war drum hits with building intensity, culminating in a horn call. The Horde ready for work. Town Hall selection sound feel. | 700ms |
| `session_end` | Campfire dying down — crackling fire sound descending, a single low horn note fading. The feast is over, the keep sleeps. | 400ms |
| `prompt_ack` | Peon "work work" click — a short wooden tap, like an axe hitting a tree trunk. Minimal, reliable, the sound of a unit accepting a task. | 120ms |
| `task_complete` | "Job's done" chime — a warm two-note ascending interval on a struck bell. The peon's pride. Organic metal, not digital. Warm overtones, satisfying resonance. | 350ms |
| `agent_deploy` | War horn — a short, bold brass blast. A scout being sent from the keep. Outward energy, purposeful, heroic without being grandiose. | 250ms |
| `agent_return` | "Something need doing?" return chirp — wooden gate creak followed by a brief plucked-string confirmation. The scout returning with news. | 300ms |
| `error` | Low health warning — a deep, urgent double-pulse. Like the heartbeat warning when a hero is critically wounded. Unmistakably bad, but organic, not electronic. | 250ms |
| `notification` | Quest notification chime — bright crystal bell tone, higher than the completion chime. A new quest is available. The "!" above an NPC's head, in sound. | 350ms |
| `commit` | Level-up fanfare — ascending brass and strings, a miniature version of the WoW level-up flash. Triumphant, warm, earned. Three notes ascending to a held chord. | 500ms |
| `permission` | Spell-ready ping — a quick arcane sparkle. Bright, magical, attention-pulling. Like a spell finishing its cooldown. | 180ms |
| `compact` | Hammer on anvil — a single metallic strike with a brief ring. The blacksmith compressing raw material. Physical, satisfying. | 200ms |
| `ambient` | Forest camp ambience — distant crickets, occasional owl, very low crackling fire, wind through trees. Ashenvale at night. Barely there, deeply atmospheric. | looping |

**TTS Greeting Template**: `"Work complete, my lord. {summary}."`

**Visual Sync**: Accent color `#c7a247` (Horde gold), tmux theme `default`.

---

### 7.3 Mario (mario)

**Cultural Reference**: Nintendo Super Mario Bros. (1985) and Super Mario World (1990). Mushroom Kingdom. Pure joy, bright colors, instant gratification. The most recognizable sound design in gaming history.

**Sonic DNA**:
- **Frequency range**: 300-8000Hz — high-energy, bright, occupying the upper registers
- **Instrument palette**: Chiptune square waves, bright triangle waves, coin tinks, bounce springs, pipe wooshes, star sparkles, 8-bit percussion
- **Emotional tone**: Unbridled cheerfulness — playful, energetic, rewarding, addictive
- **Reverb**: None / dry — 8-bit sounds are characteristically dry. Pure waveforms, no spatial treatment.

**Per-Event Sound Descriptions**:

| Token | Mario Sound | Duration |
|-------|------------|----------|
| `session_start` | World 1-1 intro — a bright ascending arpeggio of square wave tones, like the opening bars of the overworld theme condensed into a single flourish. "Let's-a go!" energy. Bouncy, major-key. | 600ms |
| `session_end` | Course clear snippet — a quick descending resolution, the final bars of the flagpole fanfare compressed. Satisfying, conclusive, victorious. | 400ms |
| `prompt_ack` | Coin collect — the iconic two-note ascending 8-bit tone. Bright, instant, deeply satisfying. The most recognizable earcon in gaming. Short, crisp, dopamine-triggering. | 100ms |
| `task_complete` | Power-up mushroom — ascending chromatic run culminating in a brief held tone. Growth energy. Something good happened, and you're bigger now. More substantial than coin, less grand than star. | 300ms |
| `agent_deploy` | Pipe warp entry — a descending chromatic whoosh. Going down the pipe into a sub-world. Quick, directional, purpose-driven. | 200ms |
| `agent_return` | Pipe warp exit — an ascending chromatic whoosh. Complementary inverse of entry. Coming back up from the sub-world with what you found. | 250ms |
| `error` | Damage shrink — the descending, deflating tone of Mario getting hit. Loss of power-up. Quick, unmistakable, not pleasant but not alarming. A setback, not a catastrophe. | 200ms |
| `notification` | ? Block hit — the hollow bounce of hitting a question block from below. Something is here, pay attention. Percussive with a bright tonal tail. | 250ms |
| `commit` | 1-UP extra life — the iconic ascending five-note jingle. Rare, celebratory, unmistakable. The most rewarding sound in 8-bit gaming. Reserved for meaningful checkpoints. | 500ms |
| `permission` | Pause menu — the sharp, clean pause sound. Gameplay stops, attention required. Crisp, immediate, breaking the flow deliberately. | 150ms |
| `compact` | Block break — the percussive crunch of Mario breaking a brick block. Quick, satisfying, physical. Things being compressed and cleared. | 180ms |
| `ambient` | Underwater theme hints — very soft, slow, gentle arpeggios reminiscent of the underwater levels. Dreamy, floaty, barely present. Chiptune lullaby. | looping |

**TTS Greeting Template**: `"Wahoo! {summary}!"`

**Visual Sync**: Accent color `#e04040` (Mario red), tmux theme `default`.

---

### 7.4 Legend of Zelda (zelda)

**Cultural Reference**: Nintendo The Legend of Zelda: Ocarina of Time (1998) and A Link to the Past (1991). Hyrule. Discovery, wonder, reverence. Every sound carries the weight of ancient magic and hidden secrets.

**Sonic DNA**:
- **Frequency range**: 150-6000Hz — wide, melodic, crystalline upper harmonics with warm low-end support
- **Instrument palette**: Harp arpeggios, ocarina melody lines, crystalline chimes, orchestral swells, fairy sparkles, wooden chest thuds, triumphant brass
- **Emotional tone**: Reverent discovery — the sense that every action uncovers something sacred. Wonder, patience, ancient wisdom.
- **Reverb**: Stone temple — long decay, warm and spacious. The inside of a Hylian dungeon or the Forest Temple. Sounds linger and shimmer.

**Per-Event Sound Descriptions**:

| Token | Zelda Sound | Duration |
|-------|------------|----------|
| `session_start` | Fairy fountain hint — gentle harp arpeggios ascending through a chord, with crystalline chime accents. The fairy fountain theme compressed to a single phrase. Ethereal, welcoming, sacred. | 700ms |
| `session_end` | Save point — the gentle confirmation harp strum of saving the game. Complete, peaceful, a journey paused but not finished. A single held chord fading with reverb. | 400ms |
| `prompt_ack` | Rupee collect — the bright, tight ascending two-note chime of collecting a green rupee. Clean, simple, rewarding. One of the most efficient earcons ever designed. | 100ms |
| `task_complete` | Small item get — a compressed version of the "da-da-da-DAAA" item fanfare. Three ascending notes resolving to a held fourth. Triumphant but restrained. The discovery of something useful. | 350ms |
| `agent_deploy` | Fairy deploy — a sparkle-trail sound, like Navi launching from Link's side. Tinkling, bright, outward-moving. A companion going ahead to scout. | 250ms |
| `agent_return` | Navi return — complementary inward sparkle, with a brief chime landing. The fairy returning with information. "Hey! Listen!" energy without the actual voice. | 300ms |
| `error` | Heart-beep alarm — the urgent, insistent beeping of low health. Rhythmic, persistent, unmistakable. A single cycle of the low-health pattern — two fast beeps at a tense pitch. | 250ms |
| `notification` | Secret found — the iconic ascending discovery jingle. Something hidden has been revealed. Bright, exciting, wonder-inducing. The "puzzle solved" feeling compressed to a notification. | 400ms |
| `commit` | Chest open fanfare — the full "da-da-da-DAAA" ascending sequence. Link holds the item above his head. The most triumphant moment in the Zelda vocabulary. Extended, earned, celebrated. | 600ms |
| `permission` | Hey! Listen! — a bright, sharp fairy ping. Urgent but magical, not mechanical. Navi demanding attention. High-frequency sparkle with a cut-off that demands a response. | 150ms |
| `compact` | Book of Mudora page turn — a papery flutter followed by a soft chime. Ancient knowledge being reorganized. Scholarly, mystical, quiet. | 200ms |
| `ambient` | Lost Woods hints — very soft, distant melodic fragments. Pentatonic, gently rhythmic, the forest breathing. Occasional bird call, water drip. Deep Hyrule at rest. | looping |

**TTS Greeting Template**: `"Hey! Listen! {summary}."`

**Visual Sync**: Accent color `#00b050` (Hylian green), tmux theme `default`.

---

### 7.5 Super Smash Bros (smash)

**Cultural Reference**: Nintendo Super Smash Bros. Melee (2001) and Super Smash Bros. Ultimate (2018). The crossover arena. Every sound is designed for maximum impact and competitive clarity. Announcer-driven, crowd-reactive, punchy.

**Sonic DNA**:
- **Frequency range**: 100-8000Hz — full spectrum, emphasis on hard transients and low-end impacts
- **Instrument palette**: Impact hits, announcer stabs, crowd roar, electric guitar stings, explosion bursts, whoosh sweeps, menu blips, stadium reverb
- **Emotional tone**: Competitive exhilaration — adrenaline, showmanship, clutch moments, arena energy
- **Reverb**: Open arena — large, diffuse, the sound of a stadium. Wide stereo, crowd ambience baked into the decay.

**Per-Event Sound Descriptions**:

| Token | Smash Bros Sound | Duration |
|-------|-----------------|----------|
| `session_start` | "READY?" — the announcer countdown energy compressed to a single dramatic beat. A deep impact hit followed by an ascending sweep, like the camera zooming into the stage. Stadium energy building. | 600ms |
| `session_end` | "GAME!" resolution — the dramatic final hit sound followed by the slow-motion freeze-frame tone. Decisive, conclusive, the match is over. A big impact followed by a sustained ring. | 500ms |
| `prompt_ack` | Menu cursor move — the clean, precise blip of navigating the character select screen. Tight, responsive, no wasted energy. Pure UI feedback at its most refined. | 100ms |
| `task_complete` | Smash attack hit — a satisfying medium-weight impact with a brief electric crackling. Something connected. Not the final blow, but a solid hit. Punchy, energetic. | 300ms |
| `agent_deploy` | Character select confirm — the decisive stamp of locking in a fighter. A quick electric guitar stab with reverb. "This is my choice." Committed, purposeful. | 250ms |
| `agent_return` | Stock respawn platform — the whoosh of the floating platform delivering a respawned fighter. Descending, arriving, back-in-the-game energy. | 300ms |
| `error` | Self-destruct/SD — the disappointing plummet of falling off the stage. A descending whoosh followed by a low distant explosion. A stock lost, a life spent. Clearly bad, quickly over. | 250ms |
| `notification` | Home-run bat charge — the rising energy of a fully-charged smash attack about to connect. Tension building, something is about to happen. Ascending electrical whine. | 350ms |
| `commit` | KO slam — the full-power knockout. A massive impact hit followed by the distant star-KO twinkle. Maximum energy. The most satisfying sound in the Smash vocabulary. | 500ms |
| `permission` | Final Smash ready — the bright, urgent alert that a final smash is available. Crackling energy, can't be ignored. High-stakes, limited-time. Act now. | 200ms |
| `compact` | Shield break — the crystalline shattering sound of a broken shield. Something protective collapsed but will regenerate. Dramatic but not fatal. | 200ms |
| `ambient` | Stadium crowd murmur — very low background crowd noise with occasional distant cheers. The arena between matches. Energy potential, not energy spent. Breath before the next fight. | looping |

**TTS Greeting Template**: `"{summary} — GAME!"`

**Visual Sync**: Accent color `#ff3333` (Smash red), tmux theme `default`.

---

### 7.6 Kingdom Hearts (kingdom-hearts)

**Cultural Reference**: Square Enix / Disney Kingdom Hearts (2002) and Kingdom Hearts II (2005). The intersection of Final Fantasy orchestral grandeur and Disney magic. Save points glow, keyblades ring, hearts are the currency of meaning.

**Sonic DNA**:
- **Frequency range**: 80-6000Hz — full orchestral range, emphasis on strings and piano in the mid-range
- **Instrument palette**: String ensemble, choir pads, piano arpeggios, keyblade metallic ring, save-point crystalline chimes, orchestral brass swells, harp glissando, bell tree
- **Emotional tone**: Emotional grandeur — every moment matters, every action has weight. Tenderness beneath strength. The bittersweet beauty of connections between worlds.
- **Reverb**: Cathedral — long, warm, enveloping. The inside of a massive Disney castle or the hollow of a world's heart. Sound floats and shimmers.

**Per-Event Sound Descriptions**:

| Token | Kingdom Hearts Sound | Duration |
|-------|---------------------|----------|
| `session_start` | Save point activation — the gentle, shimmering crystalline cascade of stepping onto a save point. Ascending piano arpeggios with string pad support. Safety, readiness, a breath before the journey. The signature KH "glowing circle" sound. | 800ms |
| `session_end` | Dearly Beloved hint — a single piano phrase echoing the opening bars of the series' theme. Tender, reflective, bittersweet. A journey paused, not ended. The sound of looking up at the stars from Destiny Islands. | 500ms |
| `prompt_ack` | MP orb collect — a bright, quick crystalline chime. Like absorbing a small orb of magical energy. Efficient, clean, the smallest unit of magical feedback. | 120ms |
| `task_complete` | Ability learned — an ascending orchestral swell compressed to a single phrase. Strings and brass lifting, then resolving. Growth achieved, capability expanded. Warm, proud, earned. | 350ms |
| `agent_deploy` | Keyblade summon — the distinctive metallic ring and whoosh of a keyblade materializing in hand. Bright, purposeful, commitment to action. A weapon-companion being called forth. | 250ms |
| `agent_return` | Keyblade dismiss — the complementary reverse. Metallic shimmer fading, the keyblade returning to light. Task complete, weapon at rest. Gentle, resolving. | 300ms |
| `error` | HP critical — the urgent, rhythmic low-health warning. A pulsing, tense orchestral stab. Danger is present, but not despair — a keyblade wielder fights through. Dark but not hopeless. | 250ms |
| `notification` | Journal updated — the bright, warm chime of a new journal entry. Knowledge gained, story progressed. Higher than task_complete, brighter than session_start. Information worth having. | 350ms |
| `commit` | Victory fanfare — the triumphant ascending orchestral sequence at battle end. Brass, strings, and timpani building to a resolved major chord. The most celebratory sound in the KH vocabulary. Hard-earned, deeply satisfying. | 600ms |
| `permission` | Command menu select — the precise, bright menu navigation tone of accessing a critical ability. Triangle button energy. Important choice incoming, focus required. | 180ms |
| `compact` | Memory orb compression — a soft, shimmering sound of light being gathered and compressed. Like memories being crystallized into a single orb. Magical, painless, preserving. | 200ms |
| `ambient` | Destiny Islands dusk — extremely soft ocean waves, distant seagulls, the barest hint of a music-box Dearly Beloved fragment carried on wind. The liminal space between worlds. Peaceful, melancholy, infinite. | looping |

**TTS Greeting Template**: `"{summary}. May your heart be your guiding key."`

**Visual Sync**: Accent color `#7b68ee` (keyblade purple/blue), tmux theme `default`.

---

## 8. Hot-Swap Mechanism

### How Theme Changes Work

Theme switching requires zero process restarts. The mechanism is trivially simple because claude-voice has no persistent daemon — each hook invocation is a fresh Python process.

```
1. User runs:  /voice theme starcraft
   (or edits ~/.claude/local/voice/config.yaml directly)
   (or sets CLAUDE_VOICE_THEME=starcraft in environment)

2. Skill handler writes to config.yaml:
   theme: starcraft

3. Next hook event fires (any event — Stop, Notification, etc.)

4. voice_event.py reads config.yaml:
   config = yaml.safe_load(open(config_path))
   theme_slug = os.environ.get("CLAUDE_VOICE_THEME") or config.get("theme", "default")

5. voice_event.py loads the new theme:
   theme_path = plugin_root / "assets" / "themes" / theme_slug / "theme.json"
   theme = json.loads(theme_path.read_text())

6. Sound resolution proceeds with the new theme.
   The user hears StarCraft sounds from this point forward.
```

### Why This Works

- **No daemon**: Each hook invocation is a new Python process. There is no long-running process holding a stale theme in memory.
- **No cache**: The theme is loaded from disk on every invocation. This costs ~2ms for YAML parse + ~1ms for JSON parse = ~3ms total. Acceptable within the 150ms timing budget.
- **No IPC**: Config is a file. Theme is a file. Reading files is the IPC. This matches the legion convention used by claude-tmux, claude-statusline, and claude-matrix.
- **No coordination**: No need to signal "theme changed" to anyone. The next hook invocation naturally picks up the new config.

### Transition Sound

When the theme changes, the next hook event plays a sound from the NEW theme. There is no special "theme transition" sound — the first sound the user hears from the new theme IS the transition. This is intentional: it immediately establishes the new sonic identity rather than wasting time on a meta-sound.

If the theme is changed via `/voice theme starcraft` (a skill command, not a hook event), the skill can optionally trigger a `session_start` preview sound from the new theme as confirmation.

### Config Read Performance

| Operation | Time | Notes |
|-----------|------|-------|
| `open()` + `yaml.safe_load()` on config.yaml (~20 lines) | <2ms | PyYAML is slow for large files but config.yaml is tiny |
| `open()` + `json.loads()` on theme.json (~80 lines) | <1ms | JSON parse is fast, file is small |
| `open()` + `json.loads()` on default/theme.json (inheritance) | <1ms | Only loaded if game theme needs fallback fields |
| **Total theme resolution** | **<4ms** | Well within the 150ms budget |

### Asset Preloading

NOT done. WAV files are 5-15KB each. The OS page cache handles repeat access naturally. Preloading would add complexity (a persistent process to manage the preload cache) for negligible latency improvement. The `pw-play` command reads the WAV from disk — on second access, it's already in page cache.

### Theme Validation

On first load of a theme in a session, the resolver optionally validates that all referenced WAV files exist:

```python
def validate_theme(theme: dict, theme_dir: Path) -> list[str]:
    """Return list of missing sound files. Empty list = valid."""
    missing = []
    for token, config in theme["semantic_sounds"].items():
        for variant in config["variants"]:
            path = theme_dir / "sounds" / variant
            if not path.exists():
                missing.append(f"{token}: {variant}")
    return missing
```

Missing files are logged as warnings but do not crash the hook. A missing variant means that specific variant is skipped during random selection. A token with ALL variants missing means no sound plays for that event — equivalent to the mute theme for that token.

---

## 9. Persona-to-Theme Mapping

### Integration with claude-personas

Each persona in the legion system has a character — and characters have preferred sonic environments. Matt (chief of staff) operates in a StarCraft command interface. Philipp (data observatory) works in the Zelda discovery space. Darren (KOI protocol) commands from Warcraft's organic warmth.

### Mapping Location

The persona-to-theme mapping lives in `~/.claude/local/voice/config.yaml`, NOT in persona character YAML files. Voice owns this mapping. Personas are a read-only input, not a configuration target.

```yaml
# ~/.claude/local/voice/config.yaml
theme: starcraft
volume: 80
muted: false

persona_themes:
  matt: starcraft
  philipp: zelda
  darren: warcraft
  carolanne: kingdom-hearts
  dan: smash
  alucek: mario
```

### Resolution Order

The active theme is determined by this priority chain (highest priority first):

```
1. CLAUDE_VOICE_THEME environment variable
   ├── Explicitly set by user or script
   └── Overrides everything — the "I know what I want" escape hatch

2. Persona mapping (config.yaml → persona_themes)
   ├── Read PERSONA_SLUG from environment (set by claude-personas)
   ├── Look up in persona_themes mapping
   └── If match found, use that theme

3. Config file (config.yaml → theme)
   ├── The default setting when no env var or persona is active
   └── Persists across sessions until changed

4. Hardcoded fallback: "default"
   └── If config.yaml is missing or unparseable
```

### Why Voice Owns the Mapping

Three reasons:

1. **Separation of concerns**: claude-personas defines who a persona IS (name, archetype, personality traits). claude-voice defines how a persona SOUNDS. Mixing these creates a coupling that makes both harder to change.

2. **Single config file**: All voice-related configuration lives in one place (`config.yaml`). The user doesn't need to edit persona YAML files to change their audio experience.

3. **Fallback safety**: If claude-personas isn't installed or a persona has no mapping, voice gracefully falls back to the config file theme. No error, no missing data, no silent failure.

---

## 10. Variant Selection

### Anti-Habituation Algorithm

The core insight from game audio middleware (Wwise, FMOD) and Blizzard's acknowledgment pattern: the human auditory system habituates rapidly to exact repeats. After 3-5 identical repetitions, the brain stops consciously registering the sound. Variation sustains perception.

### Selection Modes

**Uniform random** (default):

```python
variant = random.choice(config["variants"])
```

Every variant has equal probability. Simple, effective, sufficient for most themes.

**Weighted random** (optional):

```python
variant = random.choices(
    config["variants"],
    weights=config["weights"],
    k=1
)[0]
```

When `weights` is present in the token config, variants are selected with proportional probability. Use case: making one "signature" variant more common while keeping others as occasional surprises.

Example:
```json
{
  "variants": ["complete-01.wav", "complete-02.wav", "complete-03.wav", "complete-04.wav", "complete-05.wav"],
  "weights": [3, 2, 2, 1, 1]
}
```

`complete-01.wav` plays ~33% of the time (3/9), while `complete-04.wav` and `complete-05.wav` play ~11% each (1/9). This creates a recognizable "default" sound with occasional variation.

**No-repeat constraint** (optional):

```python
# Track last variant per token (in-memory, resets each hook invocation)
# For cross-invocation tracking, use state.json
_last_variant: dict[str, str] = {}

def select_variant(token: str, variants: list[str], weights: list[float] | None = None) -> str:
    if len(variants) == 1:
        return variants[0]

    if weights:
        selected = random.choices(variants, weights=weights, k=1)[0]
    else:
        selected = random.choice(variants)

    # No-repeat: if same as last, re-roll once
    if selected == _last_variant.get(token) and len(variants) > 1:
        if weights:
            selected = random.choices(variants, weights=weights, k=1)[0]
        else:
            selected = random.choice(variants)

    _last_variant[token] = selected
    return selected
```

The no-repeat constraint re-rolls ONCE if the selected variant matches the previous play. It does not guarantee no repeats (the re-roll might land on the same variant again with small pools). This is intentional — a hard no-repeat guarantee with a pool of 3 would create predictable patterns, which is worse than occasional repeats.

### Why 3-7 Variants

| Count | Effect | Verdict |
|-------|--------|---------|
| 1 | Identical every time. Rapid habituation within a session. | Too few — acceptable only for `permission` (where recognition > novelty) |
| 2 | Binary alternation becomes predictable within minutes. | Too few |
| 3 | Minimum viable variety. Noticeable rotation but manageable. | Minimum for low-frequency events |
| 4-5 | Good balance of variety and sonic identity. Recommended for most events. | Sweet spot |
| 6-7 | Rich variety. Each play feels fresh. | Good for high-frequency events like `task_complete` |
| 8+ | Individual variants stop being recognized as "the same event." Sonic identity dilutes. | Too many — the variants lose coherence |

Recommended variant counts by token:

| Token | Frequency | Recommended Variants |
|-------|-----------|---------------------|
| `session_start` | Once per session | 3-5 |
| `session_end` | Once per session | 3 |
| `prompt_ack` | Every user message (if enabled) | 5-7 |
| `task_complete` | Every Claude response | 5-7 |
| `agent_deploy` | Per subagent spawn | 3-5 |
| `agent_return` | Per subagent completion | 3-5 |
| `error` | Per tool failure | 3-5 |
| `notification` | Per system notification | 3-5 |
| `commit` | Per git commit | 3-5 |
| `permission` | Per permission request | 3 |
| `compact` | Per context compaction | 3 |
| `ambient` | Continuous loop | 1 per time-of-day phase |

---

## 11. Content-Aware Overrides

### The Problem

The `Stop` hook fires when Claude finishes ANY response. But not all responses are equal. A git commit is a checkpoint worth celebrating. An error response should trigger an alert, not a success chime. A test pass deserves a different sound than a generic task completion. The default `hook_to_sound` mapping (`Stop` -> `task_complete`) is a lowest-common-denominator — correct but uninformative.

### The Solution

Content-aware overrides inspect the `last_assistant_message` field in the Stop hook payload and reroute to a more specific sound token based on regex pattern matching.

### How It Works

```
Stop hook fires
  ├── hook_data["data"]["last_assistant_message"] = "Created commit abc1234..."
  ├── Enter content_aware_overrides["Stop"]["patterns"]
  ├── Pattern 1: "git commit|committed|Created commit" → re.search() → MATCH
  ├── Reroute: "task_complete" → "commit"
  └── Play: commit sound (level-up fanfare) instead of task_complete
```

### Pattern Evaluation Rules

1. **Patterns are regex** — compiled with Python `re.search()`. This enables flexible matching: `git commit|committed|Created commit` matches any of three phrases.

2. **First match wins** — patterns are evaluated in the order they appear in the JSON object (insertion order, guaranteed in Python 3.7+). Once a pattern matches, no further patterns are checked.

3. **Case-sensitive by default** — the patterns include explicit case variants (`error|Error|ERROR`) rather than using case-insensitive matching. This is deliberate: themes can match specific casing patterns (e.g., `ERROR` in stack traces but not `error` in normal prose).

4. **Fall-through to default** — if no pattern matches, the original `hook_to_sound` mapping is used (typically `task_complete` for `Stop`).

5. **Patterns are theme-specific** — each theme can define its own content-aware patterns. A theme might recognize additional patterns relevant to its personality. The default theme provides a baseline set that game themes can override or extend (via the inheritance rules in Section 6).

### Default Content-Aware Patterns

```json
{
  "Stop": {
    "patterns": {
      "git commit|committed|Created commit": "commit",
      "error|Error|ERROR|failed|Failed|FAILED|exception|Exception": "error",
      "test.*pass|tests passed|All.*pass": "task_complete"
    }
  }
}
```

| Pattern | Target Token | Matches |
|---------|-------------|---------|
| `git commit\|committed\|Created commit` | `commit` | Git commit messages in assistant output |
| `error\|Error\|ERROR\|failed\|Failed\|FAILED\|exception\|Exception` | `error` | Error reports, failure messages, exceptions |
| `test.*pass\|tests passed\|All.*pass` | `task_complete` | Test suite pass reports (remains task_complete, not promoted to commit) |

### Extensibility

Themes can add their own patterns for domain-specific events:

```json
{
  "Stop": {
    "patterns": {
      "git commit|committed|Created commit": "commit",
      "git push|pushed to|Pull request": "commit",
      "deploy|deployed|deployment": "commit",
      "error|Error|ERROR|failed|Failed|FAILED": "error",
      "warning|Warning|WARN|deprecated": "notification"
    }
  }
}
```

This StarCraft variant promotes `git push` and deployments to `commit` sounds (level-up fanfare) and routes warnings to `notification` sounds (alert klaxon). The thematic context is: in StarCraft, deployments are high-value operations. In Mario, they might not warrant special treatment.

---

## 12. Mute Theme

### The Silence Escape Hatch

Sound must NEVER be mandatory. The mute theme exists as a first-class citizen, not an afterthought. Two paths to silence, both always available:

### Path 1: The Mute Theme

```json
{
  "meta": {
    "name": "Mute",
    "slug": "mute",
    "version": "1.0.0",
    "description": "Silence. No sounds play for any event.",
    "author": "claude-voice",
    "sonic_dna": {}
  },
  "semantic_sounds": {},
  "hook_to_sound": {},
  "content_aware_overrides": {},
  "tts": {
    "voice_id": null,
    "greeting_template": "",
    "personality_modifiers": {}
  },
  "visual_sync": {
    "tmux_theme": "default",
    "accent_color": "#6c7086"
  }
}
```

Empty `semantic_sounds` means every resolution attempt returns `None`. No sound plays. This is the belt.

Activate: `/voice theme mute` or set `theme: mute` in config.yaml.

### Path 2: The Mute Toggle

```yaml
# config.yaml
theme: starcraft    # preserved — when unmuted, StarCraft resumes
muted: true         # overrides theme — silence
```

When `muted: true`, the sound resolution function returns `None` before even loading `theme.json`. This is the suspenders.

Activate: `/voice mute` or set `muted: true` in config.yaml.

### Path 3: Volume Zero

```yaml
# config.yaml
theme: starcraft
volume: 0           # resolution proceeds but playback is silent
```

Volume 0 allows the resolution pipeline to run (useful for logging and gamification) but passes volume 0 to `pw-play`, resulting in silence. This preserves all behavior except audible output.

### Resolution Check Order

```python
def should_play_sound(config: dict) -> bool:
    if config.get("muted", False):
        return False
    if config.get("volume", 80) <= 0:
        return False
    if config.get("theme", "default") == "mute":
        return False
    return True
```

All three paths are checked. Any one of them results in silence. The user picks whichever mental model makes sense to them.

---

## 13. Integration with Other Specs

### References to Other Specs

| Spec | What This Spec References | What That Spec Provides |
|------|--------------------------|------------------------|
| `specs/01-plugin-scaffold.md` | Directory structure (`assets/themes/{slug}/`), config schema (`~/.claude/local/voice/config.yaml`), hook registration (which events fire `voice_event.py`) | The filesystem layout that theme.json lives within. The config.yaml schema that holds theme selection, volume, mute state. |
| `specs/03-sound-event-routing.md` (planned) | The Event Router that reads `hook_to_sound` and `content_aware_overrides` from theme.json and produces a semantic sound token | The detailed routing logic, edge cases, and fallback behavior for event classification. This spec defines the schema; that spec defines the algorithm. |
| `specs/04-audio-playback.md` | The playback engine that receives `(path, priority, mode, duration_ms)` from theme resolution and plays the sound | Playback modes (`overlap`, `debounce`, `interrupt`, `loop`), volume control, concurrent playback coordination, the `pw-play` fallback chain. This spec defines what modes exist; that spec defines how they behave. |
| `specs/05-sound-synthesis.md` (planned) | The synthesis pipeline that generates WAV variants based on `sonic_dna` and per-event descriptions in this spec | How the sound descriptions in Section 7 become actual WAV files. numpy/scipy waveform generation, envelope design, per-theme instrument patches. |
| `specs/08-gamification.md` (planned) | Gamification engine that may trigger bonus sounds (level-up, achievement unlock) that route through theme resolution | Additional semantic sound tokens beyond the core 12 (e.g., `level_up`, `achievement`) that would be added to theme.json. |

### Relationship to claude-tmux theme.json

This spec is modeled on claude-tmux's `theme.json` (at `~/.claude/plugins/local/legion-plugins/plugins/claude-tmux/theme.json`). The parallel:

| claude-tmux | claude-voice | Pattern |
|-------------|-------------|---------|
| `semantic_colors` | `semantic_sounds` | Named tokens mapping to concrete values |
| `states` | (implicit in sound token definitions) | Intermediate semantic layer |
| `hook_to_state` | `hook_to_sound` | Hook event to semantic token mapping |
| `personas` | (via config.yaml persona_themes) | Persona identity integration |
| Single file defines all visual behavior | Single file defines all sonic behavior | DRY principle |

Both files serve the same architectural role in their respective plugins: the single source of truth that all code reads from and no code writes to at runtime.

---

## 14. Open Questions

### Q1: Should themes support ambient sound loops?

Currently spec'd as the `ambient` token with `"mode": "loop"` and `"duration_ms": -1`. This requires a long-lived `pw-play` process managed separately from one-shot earcons. The complexity cost is non-trivial: tracking the loop process PID, handling theme switches (kill old loop, start new), managing volume separately, integrating with claude-rhythms for time-of-day transitions.

**Recommendation**: Defer to Phase 2. The core value proposition is earcons (event sounds), not ambient. Ship the 11 one-shot tokens first. Add ambient when the earcon system is proven.

### Q2: Should persona-to-theme mapping live in config.yaml or a separate file?

Currently spec'd in config.yaml under `persona_themes`. Alternative: a dedicated `persona-themes.yaml` file.

**Recommendation**: Keep in config.yaml. The mapping is 6-8 lines. A separate file adds filesystem complexity for no organizational benefit. config.yaml is already the one file the user edits for voice settings.

### Q3: Should content-aware patterns be regex or simple substring match?

Currently spec'd as regex (Python `re.search()`). Alternative: simple `in` substring matching.

**Recommendation**: Start with regex. The cost is negligible (patterns are short, `re.search` on a message is <1ms). The benefit is significant: `test.*pass` matches "test suite passed" and "tests all passed" and "testing passed" — substring matching would need three separate entries. Regex is a strict superset of substring matching, so nothing is lost.

### Q4: Should there be a "random" theme that picks a different theme each session?

A meta-theme that selects randomly from the installed game themes on `SessionStart`, then maintains that selection for the rest of the session.

**Recommendation**: Yes, but as a simple config option, not a theme:

```yaml
# config.yaml
theme: random   # special value: picks from [starcraft, warcraft, mario, zelda, smash, kingdom-hearts]
```

The resolver handles `"random"` as a special case: on `SessionStart`, it picks a random game theme, writes it to a session-scoped state file, and uses that theme for all subsequent events in the session. Low complexity, high delight.

### Q5: Should themes define custom XP multipliers?

Some themes might reward different behaviors more heavily (StarCraft rewards agent deployments, Zelda rewards exploration/research, Mario rewards rapid task completion). This creates an incentive to choose themes based on playstyle rather than just aesthetics.

**Recommendation**: Defer to gamification spec. The theme engine should remain focused on sound identity. XP multipliers can reference theme slugs from the gamification system without polluting theme.json.

### Q6: Should volume normalization be enforced at the theme level?

The research recommends -14 LUFS for notification audio. Should theme.json declare a target LUFS, and should the synthesis pipeline enforce it?

**Recommendation**: Yes. Add to `meta.sonic_dna`:

```json
"target_lufs": -14
```

The synthesis pipeline normalizes all generated WAVs to this target. Manual/community themes are validated against it on load (warning, not error). This prevents one theme from being dramatically louder than another during hot-swap.
