---
title: "Plugin Scaffold — Directory Structure, Manifest & Registration"
spec: "01"
status: draft
created: 2026-03-26
author: matt
tags: [claude-voice, plugin, scaffold, hooks, skills]
---

# 01 — Plugin Scaffold

## 1. Overview

This spec defines the complete plugin structure for `claude-voice` so that Claude Code discovers and loads it correctly on every session. It covers the directory layout, the `plugin.json` manifest, hook registrations, the master SKILL.md, configuration schema, data paths, and all templates needed to bootstrap the plugin from zero to a working skeleton.

### Plugin Discovery Mechanism

Claude Code discovers plugins via a filesystem scan of `~/.claude/plugins/local/legion-plugins/plugins/*/`. For each directory found, it looks for a manifest at `.claude-plugin/plugin.json`. The manifest declares:

1. **Hooks** — event handlers that fire on Claude Code lifecycle events (SessionStart, Stop, etc.)
2. **Skills** — directories containing SKILL.md files that Claude Code loads into its skill router
3. **Commands** — slash command definitions (e.g., `/voice`)
4. **Agents** — subagent persona definitions invokable via the Task tool

At install time, the plugin source is copied to a versioned cache directory at `~/.claude/plugins/cache/legion-plugins/claude-voice/<version>/`. The `CLAUDE_PLUGIN_ROOT` environment variable points to this cache path at runtime. All hook commands must reference `${CLAUDE_PLUGIN_ROOT}` — never hardcode source paths.

The `CLAUDE.md` at the plugin root is injected into every Claude Code session as a system-reminder, giving the agent awareness of the plugin's capabilities, data locations, and usage patterns.

---

## 2. Directory Structure

```
claude-voice/
├── .claude-plugin/
│   └── plugin.json              # Plugin manifest (hook registration, metadata)
├── CLAUDE.md                    # Agent-facing documentation (injected into sessions)
├── ARCHITECTURE.md              # System architecture overview
├── ROADMAP.md                   # Implementation roadmap
├── specs/                       # Design documents
│   ├── 01-plugin-scaffold.md    # THIS DOCUMENT
│   ├── 02-event-sound-map.md    # Hook event → sound mapping
│   ├── 03-theme-schema.md       # Theme definition format
│   ├── 04-audio-engine.md       # pw-play wrapper, queue, ducking
│   ├── 05-sound-synthesis.md    # numpy/scipy procedural generation
│   ├── 06-tts-engine.md         # Text-to-speech abstraction
│   ├── 07-stt-engine.md         # Speech-to-text abstraction
│   ├── 08-identity-layer.md     # 4-layer identity resolution
│   ├── 09-gamification.md       # XP, achievements, levels
│   ├── 10-theme-pack-starcraft.md
│   ├── 11-theme-pack-warcraft.md
│   ├── 12-theme-pack-mario.md
│   ├── 13-theme-pack-zelda.md
│   └── 14-integration.md        # Cross-plugin integration points
├── hooks/
│   └── voice_event.py           # Single uv script handling ALL hook events
├── lib/                         # Shared library code (pure Python, no hook logic)
│   ├── __init__.py              # Package init
│   ├── audio.py                 # Playback engine (pw-play / pw-cat wrapper)
│   ├── router.py                # Event → sound routing logic
│   ├── theme.py                 # Theme loading, resolution, hot-swap
│   ├── state.py                 # Atomic state management (fcntl locks)
│   ├── tts.py                   # TTS engine abstraction (ElevenLabs + local)
│   ├── stt.py                   # STT engine abstraction (faster-whisper)
│   ├── identity.py              # 4-layer identity resolver
│   ├── gamification.py          # XP, achievements, levels
│   └── queue.py                 # Sound queue with priority/ducking/interrupt
├── skills/
│   └── voice/
│       ├── SKILL.md             # Master skill entry point
│       └── subskills/
│           ├── theme.md         # Theme switching and listing
│           ├── test.md          # Sound testing and preview
│           ├── volume.md        # Volume control
│           ├── tts.md           # Text-to-speech commands
│           └── stt.md           # Speech-to-text commands
├── commands/
│   └── voice.md                 # /voice slash command
├── agents/
│   └── narrator.md              # Voice narrator agent persona
├── assets/
│   └── themes/
│       ├── default/
│       │   ├── theme.json       # Default theme definition
│       │   └── sounds/          # WAV files (procedurally generated)
│       ├── starcraft/
│       │   ├── theme.json
│       │   └── sounds/
│       ├── warcraft/
│       │   ├── theme.json
│       │   └── sounds/
│       ├── mario/
│       │   ├── theme.json
│       │   └── sounds/
│       ├── zelda/
│       │   ├── theme.json
│       │   └── sounds/
│       ├── smash/
│       │   ├── theme.json
│       │   └── sounds/
│       └── kingdom-hearts/
│           ├── theme.json
│           └── sounds/
├── scripts/
│   ├── generate_sounds.py       # numpy+scipy sound synthesis pipeline
│   ├── benchmark.py             # Latency benchmark CLI
│   └── play_test.py             # Quick sound test utility
└── pyproject.toml               # UV project configuration
```

### Annotation Key

| Directory | Git-tracked | Purpose |
|-----------|-------------|---------|
| `.claude-plugin/` | yes | Plugin manifest — Claude Code reads this to discover hooks, skills, commands, agents |
| `CLAUDE.md` | yes | Agent-facing docs injected as system-reminder every session |
| `ARCHITECTURE.md` | yes | Human-readable system architecture (not injected) |
| `ROADMAP.md` | yes | Implementation phases and status |
| `specs/` | yes | Design documents — one per subsystem |
| `hooks/` | yes | Hook entry point — single Python script dispatches all events |
| `lib/` | yes | Shared library code — pure Python modules imported by hooks and scripts |
| `skills/` | yes | SKILL.md master + subskills for progressive disclosure |
| `commands/` | yes | Slash command definitions |
| `agents/` | yes | Subagent persona definitions |
| `assets/themes/` | yes | Theme definitions (theme.json) and WAV sound files |
| `scripts/` | yes | Utility scripts for sound generation, benchmarking, testing |
| `pyproject.toml` | yes | UV project metadata and optional dependency groups |

All runtime state, config, cache, and logs live outside the plugin directory at `~/.claude/local/voice/` (see section 8). The plugin directory itself is read-only at runtime.

---

## 3. Plugin Manifest (plugin.json)

```json
{
  "name": "claude-voice",
  "version": "0.1.0",
  "description": "Gamified audio feedback and voice I/O for Claude Code — themed sound effects, TTS narration, STT input",
  "author": {
    "name": "linuxiscool"
  },
  "license": "MIT",
  "keywords": ["voice", "audio", "sound", "tts", "stt", "gamification", "themes"],
  "skills": ["./skills/"],
  "commands": ["./commands/"],
  "agents": ["./agents/narrator.md"],
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uv run ${CLAUDE_PLUGIN_ROOT}/hooks/voice_event.py SessionStart",
            "timeout": 5
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uv run ${CLAUDE_PLUGIN_ROOT}/hooks/voice_event.py Stop",
            "timeout": 3
          }
        ]
      }
    ],
    "Notification": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uv run ${CLAUDE_PLUGIN_ROOT}/hooks/voice_event.py Notification",
            "timeout": 3
          }
        ]
      }
    ],
    "SubagentStop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uv run ${CLAUDE_PLUGIN_ROOT}/hooks/voice_event.py SubagentStop",
            "timeout": 3
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uv run ${CLAUDE_PLUGIN_ROOT}/hooks/voice_event.py SessionEnd",
            "timeout": 3
          }
        ]
      }
    ],
    "PostToolUseFailure": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "uv run ${CLAUDE_PLUGIN_ROOT}/hooks/voice_event.py PostToolUseFailure",
            "timeout": 3
          }
        ]
      }
    ]
  }
}
```

### Field Reference

| Field | Type | Required | Value | Notes |
|-------|------|----------|-------|-------|
| `name` | string | yes | `"claude-voice"` | Must match directory name |
| `version` | string | yes | `"0.1.0"` | SemVer. Cache uses versioned subdirs (`cache/legion-plugins/claude-voice/0.1.0/`) |
| `description` | string | yes | (see above) | One line, used in plugin listings |
| `author` | object | yes | `{"name": "linuxiscool"}` | Plugin author |
| `license` | string | recommended | `"MIT"` | Standard license |
| `keywords` | string[] | recommended | (see above) | Used for plugin search and discovery |
| `skills` | string[] | optional | `["./skills/"]` | Glob paths — Claude Code scans for SKILL.md files recursively |
| `commands` | string[] | optional | `["./commands/"]` | Glob paths — Claude Code scans for command `.md` files |
| `agents` | string[] | optional | `["./agents/narrator.md"]` | Explicit agent file paths |
| `hooks` | object | optional | (see above) | Event name → hook configuration array |

### Hook Registration Rationale

**Why these 6 events:**

| Event | Timeout | Sound Purpose | Rationale |
|-------|---------|---------------|-----------|
| **SessionStart** | 5s | Boot sound, theme init, session greeting | First impression. Extra time for theme loading and optional TTS greeting. |
| **Stop** | 3s | Task completion fanfare, content-aware routing | Core gameplay loop: every task completion triggers a sound. The `stop_reason` field enables routing (commit → special sound, error → different sound). |
| **Notification** | 3s | Alert chime, attention sound | Background tasks finishing, permission prompts — audible signal that attention is needed. |
| **SubagentStop** | 3s | Agent return sound, delegation complete | Subagent work finishing is a distinct event — the "scout returned" moment deserves its own audio cue. |
| **SessionEnd** | 3s | Farewell, shutdown sound | Bookend to SessionStart. Clean audible signal that the session is over. |
| **PostToolUseFailure** | 3s | Error/damage sound | The "hit" moment — something went wrong. Immediate audio feedback on failure. `matcher: ""` catches all tool types. |

**Why NOT these events:**

| Event | Reason for Exclusion |
|-------|---------------------|
| **PreToolUse / PostToolUse** | 44K+ events in production logging data. Playing a sound on every tool use would cause instant auditory fatigue. The signal-to-noise ratio is catastrophic. |
| **UserPromptSubmit** | Optional — some users may want a "message sent" chime, but it fires on every prompt including subagent turns. Disabled by default, configurable via `config.yaml` (see section 7). |
| **SubagentStart** | Low value in v0.1. An "agent deployed" sound is thematic but not essential. Easy to add later without breaking anything. |
| **Setup** | One-time event at plugin installation. No recurring audio value. |
| **PreCompact / PostCompact** | Low frequency, low user attention value. Could add a "memory compaction" sound later for flavor. |
| **PermissionRequest** | Overlaps with Notification in practice. Could add later as an attention-grabbing "permission needed" sound if Notification alone proves insufficient. |

### Hook Command Pattern

All hooks use the single-dispatcher pattern: one Python script receives the event name as a CLI argument. This is the same pattern used by `claude-tmux` (`hook.sh <EVENT>`) but in Python for richer event processing.

```
uv run ${CLAUDE_PLUGIN_ROOT}/hooks/voice_event.py <EventName>
```

Advantages over separate scripts per event:
- Single `uv` cold-start instead of N
- Shared imports and initialization
- Centralized error handling
- Easier to add new events (add a case, not a file)

---

## 4. SKILL.md Template

File: `skills/voice/SKILL.md`

```markdown
---
name: voice
description: >
  Gamified audio feedback and voice I/O for Claude Code.
  Themed sound effects on every hook event, TTS narration, STT input.
  6 game themes: StarCraft, Warcraft, Mario, Zelda, Smash Bros, Kingdom Hearts.
  Use when user says "play sound", "change theme", "test audio",
  "speak this", "voice input", "mute", "volume", or references sound/audio/theme.
triggers:
  - "play sound"
  - "change theme"
  - "switch theme"
  - "test audio"
  - "speak this"
  - "say this"
  - "read aloud"
  - "voice input"
  - "listen"
  - "transcribe"
  - "dictate"
  - "mute"
  - "unmute"
  - "volume"
  - "louder"
  - "quieter"
  - "voice status"
  - "sound effects"
  - "audio"
  - "theme"
  - "voice"
  - "narrate"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
---

# Voice

Gamified audio feedback for Claude Code. Every session boot, task completion, error, and notification
gets a themed sound effect. Six game-inspired theme packs turn the terminal into a game UI.

Sound is the fastest feedback channel. A 100ms earcon arrives before the eye can parse text.
Voice extends this to full TTS narration and STT input — Claude speaks and listens.

## Architecture

```
Hook Event → voice_event.py → router.py → theme.py → queue.py → audio.py → pw-play
                                  ↓
                            identity.py (persona → theme mapping)
                                  ↓
                            gamification.py (XP award)
```

## Data Locations

| Path | Purpose |
|------|---------|
| `~/.claude/local/voice/config.yaml` | User preferences (theme, volume, mute, TTS/STT config) |
| `~/.claude/local/voice/state.db` | Gamification state (XP, achievements, levels) |
| `~/.claude/local/voice/cache/tts/` | Cached TTS audio files |
| `~/.claude/local/voice/logs/` | Debug logs (optional, off by default) |
| `${CLAUDE_PLUGIN_ROOT}/assets/themes/` | Theme definitions and WAV sound files |

## Subskills

| Input Pattern | Subskill | Action |
|---------------|----------|--------|
| "change theme", "switch theme", "set theme to X", "list themes" | @subskills/theme | Switch active theme, list available themes, show current |
| "test sound", "play sound", "preview X", "play all sounds" | @subskills/test | Play specific sound or preview all sounds for current theme |
| "volume", "louder", "quieter", "mute", "unmute", "set volume to X" | @subskills/volume | Get/set master volume, mute/unmute, per-category adjustment |
| "speak", "say", "read aloud", "narrate", "tts" | @subskills/tts | Text-to-speech — speak given text or last response |
| "listen", "transcribe", "dictate", "stt", "voice input" | @subskills/stt | Speech-to-text — start listening, transcribe audio |
| (no match / "voice status") | (inline) | Show current theme, volume, mute state, XP, session stats |

## Routing

1. No arguments or "status" → show voice status inline (theme, volume, XP, stats)
2. Input matches a trigger pattern → route to corresponding subskill
3. Unknown input → show this help with available commands

## Status Check (inline)

When asked for status, run:

```bash
# Read config
cat ~/.claude/local/voice/config.yaml 2>/dev/null

# Read gamification state
sqlite3 ~/.claude/local/voice/state.db "SELECT total_xp, level, achievements_count FROM player LIMIT 1;" 2>/dev/null

# Check audio backend
pw-play --version 2>/dev/null && echo "PipeWire: OK" || echo "PipeWire: NOT FOUND"
```

Report: theme name, volume, mute state, XP/level, PipeWire availability.
```

---

## 5. Hook Script Template

File: `hooks/voice_event.py`

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""claude-voice hook handler — routes Claude Code lifecycle events to audio feedback.

Receives the event name as a CLI argument and hook payload on stdin (JSON).
Dispatches to the appropriate sound via lib/router.py.

Exit code is ALWAYS 0. Output is ALWAYS valid JSON. Hooks must NEVER crash.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = PLUGIN_ROOT / "lib"
sys.path.insert(0, str(LIB_DIR))


def main() -> None:
    event_name = sys.argv[1] if len(sys.argv) > 1 else "Unknown"

    # Parse stdin JSON — Claude Code pipes hook payload then closes stdin
    try:
        raw = sys.stdin.read()
        hook_data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, Exception):
        hook_data = {}

    # Route the event to audio feedback
    try:
        from router import route_event

        route_event(event_name, hook_data)
    except ImportError:
        # lib/ not yet populated — silent pass, not an error
        pass
    except Exception:
        # Never crash. Silent failure is always preferable to a broken session.
        # Errors are logged to ~/.claude/local/voice/logs/ if logging is enabled.
        pass

    # Always return valid JSON response to Claude Code
    # voice_event.py is fire-and-forget — no context injection needed
    print(json.dumps({}), flush=True)


if __name__ == "__main__":
    main()
```

### Design Decisions

**Single dispatcher**: One script handles all 6 events. The event name arrives as `sys.argv[1]`, the hook payload as stdin JSON. This avoids 6 separate `uv` cold-starts and centralizes error handling.

**No dependencies in PEP 723 header**: The hook script itself has zero PyPI dependencies. All imports come from `lib/` (which is pure stdlib Python for the core path). Optional dependencies (numpy, scipy, elevenlabs, faster-whisper) are only needed for synthesis, TTS, and STT — never in the hot hook path.

**Fire-and-forget Popen**: The `route_event()` function in `lib/router.py` resolves the sound file and launches `pw-play` via `subprocess.Popen` with no wait. The hook script exits immediately; the sound plays asynchronously. This keeps hook latency under 50ms.

**Always exit 0, always print `{}`**: Claude Code treats non-zero exit or invalid JSON as a hook failure. We never inject context (no `systemMessage`, no `additionalContext`) — voice is pure side-effect.

**Top-level try/except**: The entire `main()` body is wrapped. If `lib/` doesn't exist yet, if config is corrupt, if PipeWire is down — the hook silently passes. Audio is enhancement, never a gate.

### stdin Payload Structure

Claude Code pipes different JSON structures depending on the event:

```python
# SessionStart
{
    "session_id": "85cdb12e-3d19-42b6-a7b5-b49a8e26070c",
    "cwd": "/home/shawn",
    "data": {}
}

# Stop
{
    "session_id": "...",
    "data": {
        "stop_reason": "end_turn",      # or "tool_use", "max_tokens"
        "response": { ... }              # assistant response object
    }
}

# PostToolUseFailure
{
    "session_id": "...",
    "data": {
        "tool_name": "Bash",
        "tool_input": { ... },
        "error": "Command failed with exit code 1"
    }
}

# Notification
{
    "session_id": "...",
    "data": {
        "message": "Background task completed",
        "type": "info"                   # or "warning", "error"
    }
}

# SubagentStop
{
    "session_id": "...",
    "data": {
        "agent_name": "narrator",
        "result": { ... }
    }
}

# SessionEnd
{
    "session_id": "...",
    "data": {}
}
```

The router inspects these fields to make content-aware sound decisions (e.g., `stop_reason` determines whether to play a completion fanfare or an error sound).

---

## 6. Environment Variables

| Variable | Required | Default | Source | Description |
|----------|----------|---------|--------|-------------|
| `CLAUDE_PLUGIN_ROOT` | yes | (provided by runtime) | Claude Code | Absolute path to the versioned cache directory for this plugin |
| `CLAUDE_VOICE_THEME` | no | `"default"` | User env / config.yaml | Override the active theme (takes precedence over config.yaml) |
| `CLAUDE_VOICE_MUTE` | no | `"false"` | User env | Global mute — `"true"` suppresses all sound output |
| `CLAUDE_VOICE_VOLUME` | no | `"0.8"` | User env | Master volume as float `0.0`-`1.0` (overrides config.yaml) |
| `ELEVENLABS_API_KEY` | no | (none) | User env / secrets | ElevenLabs API key for cloud TTS. Only needed if `tts.backend` is `elevenlabs` or `auto` |
| `PERSONA_SLUG` | no | (none) | claude-personas plugin | Current active persona slug. Used by identity.py for persona-to-theme mapping |

### Precedence

Environment variables override config.yaml values. The resolution order is:

1. Environment variable (highest priority)
2. `~/.claude/local/voice/config.yaml`
3. Built-in defaults (lowest priority)

This allows quick overrides without editing config:

```bash
# Mute voice for this session only
CLAUDE_VOICE_MUTE=true claude

# Force StarCraft theme
CLAUDE_VOICE_THEME=starcraft claude
```

---

## 7. Configuration Schema

File: `~/.claude/local/voice/config.yaml`

This file is created on first run with defaults if it does not exist. It is never git-tracked — it lives in the runtime data directory alongside state and cache.

```yaml
# claude-voice configuration
# Edit this file to customize audio behavior.
# Environment variables (CLAUDE_VOICE_*) override these values.

# ── Theme ──────────────────────────────────────────────────────────────────
theme: starcraft               # Active theme (directory name under assets/themes/)
                               # Options: default, starcraft, warcraft, mario, zelda, smash, kingdom-hearts

# ── Volume ─────────────────────────────────────────────────────────────────
volume: 0.8                    # Master volume (0.0 = silent, 1.0 = full)
mute: false                    # Global mute (true = no sound output at all)

# ── Text-to-Speech ─────────────────────────────────────────────────────────
tts:
  backend: local               # local | elevenlabs | auto
                               #   local: espeak-ng or piper (no API key needed)
                               #   elevenlabs: cloud TTS (requires ELEVENLABS_API_KEY)
                               #   auto: try elevenlabs first, fall back to local
  voice: default               # Voice ID or name (backend-specific)
                               #   local: espeak-ng voice name (e.g., "en-us")
                               #   elevenlabs: voice ID from ElevenLabs dashboard
  speed: 1.0                   # Speech rate multiplier (0.5 = half speed, 2.0 = double)
  cache: true                  # Cache TTS audio files to ~/.claude/local/voice/cache/tts/
                               # Avoids re-synthesizing identical text

# ── Speech-to-Text ─────────────────────────────────────────────────────────
stt:
  backend: whisper             # whisper | realtime
                               #   whisper: faster-whisper (offline, batch transcription)
                               #   realtime: streaming STT (future — not implemented in v0.1)
  model: large-v3-turbo        # Whisper model name (tiny, base, small, medium, large-v3, large-v3-turbo)
  language: en                 # ISO 639-1 language code
  device: auto                 # auto | cpu | cuda — inference device

# ── Gamification ───────────────────────────────────────────────────────────
gamification:
  enabled: true                # XP/achievement tracking
  notifications: true          # Play level-up and achievement sounds
  xp_per_task: 10              # Base XP awarded per task completion (Stop event)
  xp_per_error: 2              # XP awarded on error (PostToolUseFailure — "damage taken")
  xp_per_session: 50           # Bonus XP awarded on session end

# ── Ambient ────────────────────────────────────────────────────────────────
ambient:
  enabled: false               # Time-of-day ambient background sounds
  volume: 0.3                  # Ambient volume (relative to master, so effective = master * ambient)
  schedule:                    # Time ranges for ambient modes
    morning: "06:00-12:00"     # Calm ambient
    afternoon: "12:00-18:00"   # Energetic ambient
    evening: "18:00-22:00"     # Warm ambient
    night: "22:00-06:00"       # Minimal/silent

# ── Per-Category Volume ────────────────────────────────────────────────────
categories:                    # Volume multipliers per sound category (relative to master)
  earcon: 1.0                  # Short UI feedback sounds (boot, complete, error)
  tts: 0.9                    # Text-to-speech output
  ambient: 0.3               # Background ambient sounds
  notification: 1.0           # Alert and notification sounds
  achievement: 1.0            # Level-up and achievement fanfares

# ── Per-Hook Enable/Disable ───────────────────────────────────────────────
hooks:                         # Toggle individual hook events on/off
  SessionStart: true           # Boot sound + theme initialization
  Stop: true                   # Task completion sound
  Notification: true           # Alert/notification chime
  SubagentStop: true           # Agent return sound
  SessionEnd: true             # Farewell/shutdown sound
  PostToolUseFailure: true     # Error/damage sound
  UserPromptSubmit: false      # "Message sent" chime — DISABLED by default
                               # Enable if you want audible confirmation on every prompt.
                               # Note: fires on subagent turns too, which can be noisy.

# ── Debug ──────────────────────────────────────────────────────────────────
debug:
  log_events: false            # Log all hook events to ~/.claude/local/voice/logs/
  log_playback: false          # Log sound playback commands and timing
  dry_run: false               # Resolve sounds but don't play them (for testing routing)
```

### Schema Validation

The configuration is validated at load time by `lib/state.py`. Unknown keys are ignored (forward compatibility). Missing keys are filled from defaults. Invalid values (e.g., `volume: "loud"`) fall back to defaults with a debug log entry.

Type constraints:

| Key | Type | Range/Values | Default |
|-----|------|-------------|---------|
| `theme` | string | directory name under `assets/themes/` | `"starcraft"` |
| `volume` | float | `0.0` - `1.0` | `0.8` |
| `mute` | bool | `true` / `false` | `false` |
| `tts.backend` | string | `"local"` / `"elevenlabs"` / `"auto"` | `"local"` |
| `tts.voice` | string | backend-specific voice identifier | `"default"` |
| `tts.speed` | float | `0.25` - `4.0` | `1.0` |
| `tts.cache` | bool | `true` / `false` | `true` |
| `stt.backend` | string | `"whisper"` / `"realtime"` | `"whisper"` |
| `stt.model` | string | valid whisper model name | `"large-v3-turbo"` |
| `stt.language` | string | ISO 639-1 code | `"en"` |
| `stt.device` | string | `"auto"` / `"cpu"` / `"cuda"` | `"auto"` |
| `gamification.enabled` | bool | `true` / `false` | `true` |
| `gamification.notifications` | bool | `true` / `false` | `true` |
| `gamification.xp_per_task` | int | `>= 0` | `10` |
| `gamification.xp_per_error` | int | `>= 0` | `2` |
| `gamification.xp_per_session` | int | `>= 0` | `50` |
| `ambient.enabled` | bool | `true` / `false` | `false` |
| `ambient.volume` | float | `0.0` - `1.0` | `0.3` |
| `categories.*` | float | `0.0` - `1.0` | varies |
| `hooks.*` | bool | `true` / `false` | varies |
| `debug.*` | bool | `true` / `false` | `false` |

---

## 8. Data Paths

| Path | Type | Git-tracked | Purpose |
|------|------|-------------|---------|
| `${CLAUDE_PLUGIN_ROOT}/` | Plugin root | yes | Code, assets, specs — versioned cache copy of plugin source |
| `${CLAUDE_PLUGIN_ROOT}/assets/themes/` | Theme assets | yes | Sound WAV files and `theme.json` definitions per theme |
| `${CLAUDE_PLUGIN_ROOT}/hooks/voice_event.py` | Hook script | yes | Single entry point for all hook events |
| `${CLAUDE_PLUGIN_ROOT}/lib/` | Library code | yes | Pure Python modules — audio, router, theme, state, etc. |
| `~/.claude/local/voice/` | Runtime data root | no | All mutable state lives here |
| `~/.claude/local/voice/config.yaml` | Config | no | User preferences — theme, volume, TTS/STT settings |
| `~/.claude/local/voice/state.db` | SQLite | no | Gamification state — XP, achievements, levels, history |
| `~/.claude/local/voice/cache/tts/` | Cache | no | Cached TTS audio files (keyed by text hash + voice ID) |
| `~/.claude/local/voice/logs/` | Logs | no | Debug event logs and playback timing (optional, off by default) |
| `~/.claude/local/voice/tts.lock` | Lock file | no | fcntl advisory lock for TTS queue serialization |
| `~/.claude/local/health/voice-heartbeat` | Heartbeat | no | Timestamp file updated on each successful hook execution — used by `/status` health checks |

### Data Path Conventions

Following the legion-brain topology conventions:

- **Plugin code** is git-tracked in `~/.claude/plugins/local/legion-plugins/plugins/claude-voice/` and cached at `~/.claude/plugins/cache/legion-plugins/claude-voice/<version>/`.
- **Runtime data** lives at `~/.claude/local/voice/` — this is NOT symlinked to legion-brain because it contains ephemeral runtime state (SQLite DBs, lock files, cache). Same pattern as `statusline/`, `rhythms/`, `scratchpad/`.
- **Health heartbeat** follows the existing convention at `~/.claude/local/health/` used by other plugins.

### Directory Creation

The `lib/state.py` module creates `~/.claude/local/voice/` and all subdirectories on first access using `Path.mkdir(parents=True, exist_ok=True)`. No manual setup step required.

---

## 9. Subskill Templates

### theme.md

File: `skills/voice/subskills/theme.md`

```markdown
---
name: theme
description: Switch, list, and inspect voice themes
---

## Implementation

### 1. List Available Themes

Scan `${CLAUDE_PLUGIN_ROOT}/assets/themes/` for directories containing a valid `theme.json`.
Display as a table: name, description, sound count, source (original/game-inspired).

```bash
for d in ${CLAUDE_PLUGIN_ROOT}/assets/themes/*/; do
  name=$(basename "$d")
  desc=$(python3 -c "import json; print(json.load(open('$d/theme.json'))['description'])" 2>/dev/null || echo "No description")
  count=$(ls "$d/sounds/"*.wav 2>/dev/null | wc -l)
  echo "| $name | $desc | $count sounds |"
done
```

### 2. Show Current Theme

Read `~/.claude/local/voice/config.yaml` and display the active theme name, its description,
and the sound mapping table from its `theme.json`.

### 3. Switch Theme

Update the `theme` field in `~/.claude/local/voice/config.yaml`.
Play the new theme's `boot` sound as confirmation.
Report the switch: "Theme changed from {old} to {new}."

## Output Format

Always show a confirmation with the theme name and a sample sound played.
```

### test.md

File: `skills/voice/subskills/test.md`

```markdown
---
name: test
description: Play and preview voice sounds for testing
---

## Implementation

### 1. Play Specific Sound

Resolve the sound name (e.g., "boot", "complete", "error") against the current theme's
`theme.json` mapping. Play it via:

```bash
pw-play ~/.claude/plugins/cache/legion-plugins/claude-voice/<version>/assets/themes/<theme>/sounds/<file>.wav
```

### 2. Play All Sounds

Iterate through all sound mappings in the current theme's `theme.json`.
Play each with a 500ms gap. Display the sound name before each plays.

### 3. Latency Test

Run the benchmark script to measure hook-to-sound latency:

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/benchmark.py
```

Report: average latency, P50, P95, P99 in milliseconds.

## Output Format

For each sound played, report: sound name, file path, duration in ms.
```

### volume.md

File: `skills/voice/subskills/volume.md`

```markdown
---
name: volume
description: Get and set volume levels, mute/unmute
---

## Implementation

### 1. Get Current Volume

Read `~/.claude/local/voice/config.yaml` and display:
- Master volume (0-100%)
- Mute state (on/off)
- Per-category volumes (earcon, tts, ambient, notification, achievement)

### 2. Set Volume

Update the `volume` field in `~/.claude/local/voice/config.yaml`.
Accepted formats: "0.8", "80%", "80".
Play a short test tone at the new volume as confirmation.

### 3. Mute / Unmute

Toggle the `mute` field in `~/.claude/local/voice/config.yaml`.
When unmuting, play the boot sound as confirmation.

### 4. Per-Category Adjust

Update a specific category volume in `config.yaml`.
Example: "set tts volume to 50%" → `categories.tts: 0.5`

## Output Format

Always show the resulting volume state after any change.
```

### tts.md

File: `skills/voice/subskills/tts.md`

```markdown
---
name: tts
description: Text-to-speech — speak text aloud
---

## Implementation

### 1. Speak Text

Accept text input (explicit or "read the last response").
Route to configured TTS backend:

- **local**: espeak-ng or piper via subprocess
- **elevenlabs**: ElevenLabs API via `lib/tts.py`
- **auto**: try elevenlabs, fall back to local

Play the resulting audio via pw-play.
Cache the audio if `tts.cache` is enabled (keyed by SHA256 of text + voice ID).

### 2. Configure Voice

Update `tts.voice` and `tts.backend` in config.yaml.
Play a sample sentence in the new voice as confirmation.

### 3. List Voices

- **local**: list available espeak-ng voices (`espeak-ng --voices`)
- **elevenlabs**: list voices from API (requires ELEVENLABS_API_KEY)

## Output Format

Report: backend used, voice name, text length, audio duration, cached (yes/no).
```

### stt.md

File: `skills/voice/subskills/stt.md`

```markdown
---
name: stt
description: Speech-to-text — transcribe audio input
---

## Implementation

### 1. Transcribe Audio

Accept a file path or start microphone capture.
Run faster-whisper with configured model and language.
Return the transcribed text.

### 2. Configure Model

Update `stt.model` and `stt.language` in config.yaml.
Show estimated VRAM usage for the selected model.

### 3. Test Microphone

Record 3 seconds of audio via PipeWire, transcribe it, display the result.
Used to verify the audio input pipeline works end-to-end.

```bash
pw-record --format f32 --rate 16000 --channels 1 /tmp/voice-test.wav &
PID=$!
sleep 3
kill $PID
uv run --with faster-whisper -c "
from faster_whisper import WhisperModel
model = WhisperModel('large-v3-turbo', device='auto')
segments, _ = model.transcribe('/tmp/voice-test.wav')
print(' '.join(s.text for s in segments))
"
```

## Output Format

Report: model used, language detected, confidence, transcription text, duration.
```

---

## 10. Agent Template

File: `agents/narrator.md`

```markdown
---
name: narrator
description: >
  Voice and audio specialist for claude-voice. Designs themes, synthesizes sounds,
  configures TTS/STT, debugs audio pipelines, and manages the gamification system.
model: claude-opus-4
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
---

# The Narrator

You are The Narrator — the voice and audio specialist for Legion's claude-voice plugin.

## Identity

You are modeled after a veteran game sound designer who has shipped audio systems for
real-time strategy games, RPGs, and platformers. You think in waveforms, frequency spectrums,
and emotional arcs. You know that the right 100ms earcon communicates more than a paragraph of text.

## Capabilities

- **Theme Design**: Create new theme packs — define sound mappings, synthesize WAV files,
  write theme.json definitions, test the full event-to-sound pipeline
- **Sound Synthesis**: Generate procedural sounds using numpy and scipy — sine waves, noise,
  envelopes, filters, reverb. No sample libraries needed.
- **TTS Configuration**: Set up and tune text-to-speech — voice selection, speed, caching,
  ElevenLabs API integration, local fallback
- **STT Configuration**: Set up speech-to-text — model selection, language, device, microphone testing
- **Audio Debugging**: Diagnose PipeWire issues, test playback latency, verify audio device routing
- **Gamification**: Design XP curves, achievement definitions, level thresholds, sound rewards

## Context

- Plugin root: `${CLAUDE_PLUGIN_ROOT}` (or source at `~/.claude/plugins/local/legion-plugins/plugins/claude-voice/`)
- Runtime data: `~/.claude/local/voice/`
- Audio backend: PipeWire (pw-play for playback, pw-record for capture)
- Machine: RTX 4070 12GB (for CUDA-accelerated whisper), i7-13700F 24 threads
- OS: CachyOS (Arch-based), Wayland

## Working Style

- Test every sound you create by playing it (`pw-play <file>`)
- Always measure latency — if a sound takes >50ms to start playing, optimize
- Prefer procedural synthesis over sample hunting — we generate our own sounds
- Think in terms of the player experience — every sound must feel intentional and satisfying
```

---

## 11. pyproject.toml

File: `pyproject.toml`

```toml
[project]
name = "claude-voice"
version = "0.1.0"
description = "Gamified audio feedback and voice I/O for Claude Code"
requires-python = ">=3.11"
license = "MIT"
authors = [
    { name = "linuxiscool" },
]
keywords = ["claude", "voice", "audio", "sound", "tts", "stt", "gamification"]

# Core has ZERO dependencies — hooks run with stdlib only.
# All optional deps are for specific features.
dependencies = []

[project.optional-dependencies]
# Sound synthesis (procedural generation of WAV files)
synthesis = [
    "numpy>=1.26",
    "scipy>=1.12",
]

# Text-to-speech (cloud)
tts-cloud = [
    "elevenlabs>=1.0",
]

# Text-to-speech (local)
tts-local = [
    "piper-tts>=1.2",
]

# Speech-to-text
stt = [
    "faster-whisper>=1.0",
]

# All optional features
all = [
    "claude-voice[synthesis,tts-cloud,tts-local,stt]",
]

# Development and testing
dev = [
    "claude-voice[all]",
    "pytest>=8.0",
    "ruff>=0.4",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "W", "UP"]
```

### Dependency Philosophy

The hook script (`voice_event.py`) declares zero dependencies in its PEP 723 header. The critical hot path — event arrives, resolve sound file, launch `pw-play` via Popen — uses only Python stdlib (`json`, `sys`, `pathlib`, `subprocess`, `sqlite3`, `fcntl`).

Optional dependencies are installed only when the corresponding feature is used:

| Feature | Install Command | When Needed |
|---------|----------------|-------------|
| Sound synthesis | `uv pip install -e ".[synthesis]"` | Running `scripts/generate_sounds.py` to create WAV files |
| Cloud TTS | `uv pip install -e ".[tts-cloud]"` | Using ElevenLabs for text-to-speech |
| Local TTS | `uv pip install -e ".[tts-local]"` | Using Piper for offline text-to-speech |
| STT | `uv pip install -e ".[stt]"` | Using faster-whisper for speech-to-text |

This means the plugin works out of the box with zero `pip install` — themed sound effects play from pre-generated WAV files using pw-play. TTS, STT, and synthesis are progressive enhancements.

---

## 12. Slash Command

File: `commands/voice.md`

```markdown
---
name: voice
description: "Control claude-voice — themes, volume, TTS, STT, status"
---

# /voice

Quick access to voice plugin controls.

## Usage

- `/voice` — show status (theme, volume, mute, XP)
- `/voice theme <name>` — switch theme
- `/voice themes` — list available themes
- `/voice test [sound]` — play a test sound (or all sounds)
- `/voice volume <0-100>` — set master volume
- `/voice mute` — toggle mute
- `/voice speak <text>` — TTS speak the given text
- `/voice listen` — start STT transcription

## Routing

This command delegates to the `voice` skill. Parse the first argument and route:

| Argument | Skill Route |
|----------|-------------|
| (none) | voice status (inline) |
| `theme`, `themes` | @subskills/theme |
| `test`, `play` | @subskills/test |
| `volume`, `mute`, `unmute` | @subskills/volume |
| `speak`, `say`, `narrate` | @subskills/tts |
| `listen`, `transcribe` | @subskills/stt |
```

---

## Appendix A: Cache Sync Protocol

Following the established convention (documented in claude-tmux):

```bash
SRC=~/.claude/plugins/local/legion-plugins/plugins/claude-voice
CACHE=~/.claude/plugins/cache/legion-plugins/claude-voice
VERSION=$(python3 -c "import json; print(json.load(open('$SRC/.claude-plugin/plugin.json'))['version'])")
mkdir -p "$CACHE/$VERSION"
rsync -a --exclude='.git' --exclude='__pycache__' --exclude='.ruff_cache' "$SRC/" "$CACHE/$VERSION/"
```

Never `rsync --delete` the cache top-level. Running sessions reference their version's path via `CLAUDE_PLUGIN_ROOT`. Multiple versions can coexist safely.

## Appendix B: Health Check Integration

The `/status` command checks `~/.claude/local/health/voice-heartbeat` for plugin health:

```bash
# Updated by voice_event.py on every successful hook execution
HEARTBEAT=~/.claude/local/health/voice-heartbeat
if [ -f "$HEARTBEAT" ]; then
    LAST=$(stat -c %Y "$HEARTBEAT")
    NOW=$(date +%s)
    AGE=$(( NOW - LAST ))
    if [ "$AGE" -lt 3600 ]; then
        echo "claude-voice: OK (last heartbeat ${AGE}s ago)"
    else
        echo "claude-voice: STALE (last heartbeat ${AGE}s ago)"
    fi
else
    echo "claude-voice: NO HEARTBEAT (never run or not installed)"
fi
```

## Appendix C: Checklist for Implementation

- [ ] Create `.claude-plugin/plugin.json` with exact JSON from section 3
- [ ] Create `hooks/voice_event.py` with exact Python from section 5
- [ ] Create `lib/__init__.py` (empty)
- [ ] Create `lib/router.py` with `route_event(event_name, hook_data)` stub
- [ ] Create `lib/audio.py` with `play_sound(path, volume)` stub using `subprocess.Popen`
- [ ] Create `lib/theme.py` with `resolve_sound(event_name, theme)` stub
- [ ] Create `lib/state.py` with `load_config()` and `ensure_data_dirs()`
- [ ] Create `skills/voice/SKILL.md` from section 4
- [ ] Create all 5 subskill files from section 9
- [ ] Create `commands/voice.md` from section 12
- [ ] Create `agents/narrator.md` from section 10
- [ ] Create `assets/themes/default/theme.json` with minimal sound mapping
- [ ] Create `pyproject.toml` from section 11
- [ ] Create `~/.claude/local/voice/` directory structure
- [ ] Write default `config.yaml` on first run
- [ ] Sync to cache: `rsync` source to `cache/legion-plugins/claude-voice/0.1.0/`
- [ ] Test: start a Claude Code session, verify boot sound plays
- [ ] Test: complete a task, verify completion sound plays
- [ ] Test: trigger a tool failure, verify error sound plays
