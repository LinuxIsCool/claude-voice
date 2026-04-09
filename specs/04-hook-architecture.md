---
title: "Hook Architecture — Event Routing, Content Detection & State Machine"
spec: "04"
status: draft
created: 2026-03-26
author: matt
tags: [claude-voice, hooks, events, routing, state-machine]
---

# 04 — Hook Architecture

## 1. Overview

claude-voice's hook handler is a single Python UV script (`hooks/voice_event.py`) that receives Claude Code hook events via stdin JSON, routes them through the theme engine to select sounds, and fires playback via `subprocess.Popen`. The handler must be **stateless across invocations** (each hook call is a fresh process), **fast** (<150ms wall time), and **crash-proof** (never exit non-zero, never block).

The handler is registered in `plugin.json` for 6 hook events (SessionStart, Stop, Notification, SubagentStop, SessionEnd, PostToolUseFailure). Each registration points to the same script with the event name passed as a CLI argument:

```
uv run ${CLAUDE_PLUGIN_ROOT}/hooks/voice_event.py <EventName>
```

Claude Code pipes the hook payload as JSON to stdin, waits for the process to exit, and reads stdout for optional response JSON. The handler must always print `{}` to stdout and exit with code 0. Audio playback happens via a detached `subprocess.Popen` that outlives the hook process — the hook never waits for sound to finish.

### Why a Single Script

The single-dispatcher pattern (one script, event name as argv) is preferred over separate scripts per event:

- **One UV cold-start** instead of N (UV caches the interpreter + dependencies after first run)
- **Shared imports and initialization** — json, sys, pathlib parsed once
- **Centralized error handling** — one top-level try/except wraps everything
- **Easier extensibility** — adding a new event means adding a case branch, not a new file + plugin.json entry

This matches the `claude-logging` pattern (`log_event.py -e <EventName>`) proven across 31K+ events in production.

---

## 2. Hook Event Reference

Complete table of all 14 Claude Code hook event types, with their registration status in claude-voice:

| Event | Registered | Timeout | Payload Fields | Sound Token | Rationale |
|-------|------------|---------|----------------|-------------|-----------|
| `SessionStart` | **Yes** | 5s | `session_id`, `cwd`, `model`, `source`, `agent_type`, `transcript_path` | `session_start` | Boot sound, initialize theme. Extra timeout for theme loading + optional TTS greeting. |
| `SessionEnd` | **Yes** | 3s | `session_id`, `reason`, `transcript_path` | `session_end` | Farewell sound. Bookend to SessionStart. Clean audible signal that the session is over. |
| `UserPromptSubmit` | **No** (opt-in) | — | `session_id`, `prompt` (string or content blocks), `permission_mode` | `prompt_ack` | Fires on every prompt including subagent turns. Too frequent for default. Configurable via `config.yaml`. |
| `Stop` | **Yes** | 3s | `session_id`, `last_assistant_message`, `stop_hook_active`, `transcript_path` | `task_complete` (or content-aware override) | Core gameplay loop. The most important event — fires when Claude finishes responding. Content-aware parsing inspects `last_assistant_message` for git commits, errors, etc. |
| `SubagentStart` | **No** (deferred) | — | `session_id`, `agent_id`, `agent_type` | `agent_deploy` | Low priority in v0.1. "Agent deployed" is thematic but not essential. Easy to add later. |
| `SubagentStop` | **Yes** | 3s | `session_id`, `agent_id`, `agent_type`, `agent_transcript_path`, `last_assistant_message`, `stop_hook_active` | `agent_return` | Agent completion deserves its own audio cue — the "scout returned" moment. |
| `PreToolUse` | **No** (never) | — | `session_id`, `tool_name`, `tool_input`, `tool_use_id`, `agent_id` | — | 44K+ events in production. Playing a sound on every tool use causes instant auditory fatigue. |
| `PostToolUse` | **No** (never) | — | `session_id`, `tool_name`, `tool_input`, `tool_response`, `tool_use_id` | — | 42K+ events. Same problem as PreToolUse. |
| `PostToolUseFailure` | **Yes** | 3s | `session_id`, `tool_name`, `tool_input`, `error`, `is_interrupt`, `tool_use_id` | `error` | Error/damage sound. Immediate audio feedback on failure. `matcher: ""` catches all tool types. |
| `PermissionRequest` | **No** (deferred) | — | `session_id`, `tool_name`, `tool_input`, `permission_suggestions` | `permission` | Overlaps with Notification in practice. Add in v0.2 if Notification alone proves insufficient. |
| `Notification` | **Yes** | 3s | `session_id`, `message`, `notification_type` | `notification` | Alert sound when Claude needs user attention. Permission prompts, background task completions. |
| `PreCompact` | **No** (deferred) | — | `session_id`, `trigger`, `custom_instructions` | `compact` | Low frequency (30 lifetime events). Could add a "memory compaction" sound later for flavor. |
| `PostCompact` | **No** (never) | — | `session_id`, `trigger`, `compact_summary` | — | Redundant with PreCompact. The summary is interesting data but not worth a separate sound. |
| `Setup` | **No** (never) | — | (minimal) | — | One-time event at plugin installation. No recurring audio value. |

### Full Stdin Payload Examples (All 6 Registered Events)

**SessionStart:**

```json
{
  "session_id": "f7756714-b2f8-4ded-94a2-2465768c0470",
  "transcript_path": "/home/shawn/.claude/projects/-home-shawn/f7756714-b2f8-4ded-94a2-2465768c0470.jsonl",
  "cwd": "/home/shawn",
  "permission_mode": "default",
  "agent_type": "claude-personas:matt",
  "hook_event_name": "SessionStart",
  "source": "startup",
  "model": "claude-opus-4-6"
}
```

Key fields: `source` can be `"startup"` (fresh session), `"compact"` (after context compaction), or `"clear"` (after context clear). `model` is the model slug. `agent_type` carries the active persona.

**Stop:**

```json
{
  "session_id": "8c2dbf03-2d76-4935-a683-4734cae8c31c",
  "transcript_path": "/home/shawn/.claude/projects/-home-shawn/8c2dbf03-2d76-4935-a683-4734cae8c31c.jsonl",
  "cwd": "/home/shawn",
  "permission_mode": "default",
  "hook_event_name": "Stop",
  "stop_hook_active": false,
  "last_assistant_message": "I've committed the changes:\n\nCreated commit abc1234: Fix login validation\n\n3 files changed, 42 insertions(+), 8 deletions(-)"
}
```

Key fields: `stop_hook_active` (bool) guards against infinite hook chains. `last_assistant_message` is the full text of Claude's last response — the primary input for content-aware routing.

**SubagentStop:**

```json
{
  "session_id": "113ebbfc-9a4e-4d2a-b3f1-8e2d9c7a6b5d",
  "transcript_path": "/home/shawn/.claude/projects/-home-shawn/113ebbfc-9a4e-4d2a-b3f1-8e2d9c7a6b5d.jsonl",
  "cwd": "/home/shawn",
  "permission_mode": "default",
  "agent_id": "a7450863f27b3de78",
  "agent_type": "Explore",
  "hook_event_name": "SubagentStop",
  "stop_hook_active": false,
  "agent_transcript_path": "/home/shawn/.claude/projects/-home-shawn/113ebbfc-9a4e-4d2a-b3f1-8e2d9c7a6b5d/subagents/agent-a7450863f27b3de78.jsonl",
  "last_assistant_message": "Perfect. Now I have all the information I need. The codebase uses Django 5.1 with DRF, PostgreSQL with pgvector, and Graphiti for the knowledge graph."
}
```

Key fields: `agent_id` (short hex), `agent_type` (agent name/role), `agent_transcript_path` (subagent's own JSONL).

**Notification:**

```json
{
  "session_id": "d78a58e7-3c1b-4f9a-a2e8-1b5d9f0c3a7e",
  "transcript_path": "/home/shawn/.claude/projects/-home-shawn/d78a58e7-3c1b-4f9a-a2e8-1b5d9f0c3a7e.jsonl",
  "cwd": "/home/shawn",
  "agent_type": "claude-personas:matt",
  "hook_event_name": "Notification",
  "message": "Claude needs your permission to use Write",
  "notification_type": "permission_prompt"
}
```

Key fields: `message` (human-readable alert text), `notification_type` (known values: `"permission_prompt"`).

**PostToolUseFailure:**

```json
{
  "session_id": "d78a58e7-3c1b-4f9a-a2e8-1b5d9f0c3a7e",
  "transcript_path": "/home/shawn/.claude/projects/-home-shawn/d78a58e7-3c1b-4f9a-a2e8-1b5d9f0c3a7e.jsonl",
  "cwd": "/home/shawn",
  "permission_mode": "bypassPermissions",
  "agent_id": "ac6129f9c52a90f7d",
  "agent_type": "general-purpose",
  "hook_event_name": "PostToolUseFailure",
  "tool_name": "WebFetch",
  "tool_input": {"url": "https://example.com/api/data", "prompt": "Extract the pricing table"},
  "tool_use_id": "toolu_01YJ8qA5D7ymisYyZ8xNzMCk",
  "error": "Request failed with status code 403",
  "is_interrupt": false
}
```

Key fields: `error` (string — the failure message), `is_interrupt` (bool — true if user hit Escape), `tool_name` (which tool failed).

**SessionEnd:**

```json
{
  "session_id": "8c2dbf03-2d76-4935-a683-4734cae8c31c",
  "transcript_path": "/home/shawn/.claude/projects/-home-shawn/8c2dbf03-2d76-4935-a683-4734cae8c31c.jsonl",
  "cwd": "/home/shawn",
  "hook_event_name": "SessionEnd",
  "reason": "other"
}
```

Key fields: `reason` (known values: `"other"`, possibly `"timeout"`, `"crash"`).

---

## 3. Event Router (`lib/router.py`)

The event router is the core routing logic that transforms a raw hook event into a sound playback action. It is called by `voice_event.py` after parsing stdin.

### Interface

```python
"""Event router — maps hook events to sound playback.

This module is the single decision point for all hook-to-sound routing.
It loads config, resolves theme, applies content-aware overrides, selects
the sound variant, calculates volume, and fires playback. All error
handling is internal — this function never raises.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Optional

# Lazy imports — only loaded when route_event() is actually called.
# This keeps startup fast if the lib directory doesn't exist yet
# (e.g., first install before UV resolves dependencies).


def route_event(event_name: str, hook_data: dict) -> None:
    """Route a hook event to the appropriate sound.

    This is the main entry point called by voice_event.py.
    It orchestrates the full routing pipeline:

    1. Guard: check stop_hook_active (prevent infinite loops)
    2. Guard: check if event is enabled in config
    3. Load active theme
    4. Map event_name -> sound token via direct lookup or content-aware parsing
    5. Apply content-aware overrides (for Stop and SubagentStop events)
    6. Resolve sound token -> file path via theme
    7. Select random variant from the sound directory
    8. Calculate effective volume (master * category)
    9. Fire playback via lib/audio.play_sound()
    10. Update session state (sound count, last event)
    11. Update gamification state (if enabled)
    12. Write health heartbeat

    Args:
        event_name: The hook event name (e.g., "Stop", "SessionStart")
        hook_data: The full JSON payload from stdin

    Returns:
        None. All effects are side effects (sound playback, state writes).
        Never raises — all errors are caught and logged internally.
    """
    try:
        _route_event_inner(event_name, hook_data)
    except Exception:
        pass  # Prime directive: never crash


def _route_event_inner(event_name: str, hook_data: dict) -> None:
    """Inner routing logic. May raise — caller wraps in try/except."""

    # --- Step 1: Guard against hook-triggered stops ---
    # When stop_hook_active is True, another Stop hook already fired this
    # turn and injected a new prompt. Playing sound here would double-trigger.
    if hook_data.get("stop_hook_active"):
        return

    # --- Step 2: Check if event is enabled in config ---
    from lib.config import load_config
    config = load_config()
    if not config.get("hooks", {}).get(event_name, True):
        return  # Event disabled by user

    # --- Step 3: Load active theme ---
    from lib.theme import load_theme
    theme_name = config.get("theme", "default")
    theme = load_theme(theme_name)

    # --- Step 4 & 5: Map event to sound token ---
    sound_token = _resolve_sound_token(event_name, hook_data, theme, config)
    if not sound_token:
        return  # No sound mapped for this event

    # --- Step 6: Resolve sound token -> file path ---
    from lib.theme import resolve_sound_path
    sound_dir = resolve_sound_path(theme, sound_token)
    if not sound_dir or not sound_dir.exists():
        return  # Missing sound directory, skip silently

    # --- Step 7: Select random variant ---
    import random
    variants = list(sound_dir.glob("*.wav"))
    if not variants:
        return  # No WAV files in directory
    chosen = random.choice(variants)

    # --- Step 8: Calculate effective volume ---
    master_volume = config.get("volume", 80) / 100.0  # 0-100 -> 0.0-1.0
    category = _get_sound_category(sound_token)
    category_volume = config.get("category_volumes", {}).get(category, 1.0)
    effective_volume = master_volume * category_volume
    # Clamp to [0.0, 1.0]
    effective_volume = max(0.0, min(1.0, effective_volume))

    if effective_volume <= 0.0:
        return  # Muted

    # --- Step 9: Fire playback ---
    from lib.audio import play_sound
    play_sound(str(chosen), volume=effective_volume)

    # --- Step 10: Update session state ---
    session_id = hook_data.get("session_id", "")
    if session_id:
        _update_session_state(session_id, event_name, sound_token, theme_name)

    # --- Step 11: Update gamification state ---
    if config.get("gamification", {}).get("enabled", False):
        _update_gamification(session_id, event_name, sound_token, hook_data)

    # --- Step 12: Write health heartbeat ---
    _write_heartbeat()
```

### Sound Token Resolution

```python
# Direct hook-to-sound mapping for non-content-aware events
HOOK_TO_SOUND = {
    "SessionStart":       "session_start",
    "SessionEnd":         "session_end",
    "UserPromptSubmit":   "prompt_ack",
    "SubagentStart":      "agent_deploy",
    "SubagentStop":       "agent_return",
    "PostToolUseFailure": "error",
    "Notification":       "notification",
    "PermissionRequest":  "permission",
    "PreCompact":         "compact",
}


def _resolve_sound_token(
    event_name: str,
    hook_data: dict,
    theme: dict,
    config: dict,
) -> Optional[str]:
    """Resolve the sound token for a hook event.

    For most events this is a direct lookup in HOOK_TO_SOUND.
    For Stop and SubagentStop, content-aware parsing inspects
    last_assistant_message to select a contextual sound.

    Resolution order:
    1. Content-aware override (Stop/SubagentStop only)
    2. Theme-specific hook_to_sound overrides (theme.json)
    3. Built-in HOOK_TO_SOUND table
    4. None (no sound for this event)
    """
    # Content-aware events get special treatment
    if event_name in ("Stop", "SubagentStop"):
        token = _content_aware_resolve(hook_data, theme, config)
        if token:
            return token

    # Theme-specific overrides
    theme_overrides = theme.get("hook_to_sound", {})
    if event_name in theme_overrides:
        return theme_overrides[event_name]

    # Built-in default mapping
    return HOOK_TO_SOUND.get(event_name)
```

### Sound Categories

Sound tokens are grouped into categories for volume control:

```python
SOUND_CATEGORIES = {
    "feedback": [
        "session_start", "session_end", "task_complete",
        "prompt_ack", "commit",
    ],
    "alert": [
        "error", "notification", "permission",
    ],
    "agent": [
        "agent_deploy", "agent_return",
    ],
    "ambient": [
        "compact", "level_up", "achievement",
    ],
}

# Reverse lookup: token -> category
_TOKEN_TO_CATEGORY = {}
for cat, tokens in SOUND_CATEGORIES.items():
    for t in tokens:
        _TOKEN_TO_CATEGORY[t] = cat


def _get_sound_category(sound_token: str) -> str:
    """Get the volume category for a sound token."""
    return _TOKEN_TO_CATEGORY.get(sound_token, "feedback")
```

### Error Handling Per Step

| Step | Failure Mode | Response |
|------|-------------|----------|
| 1. stop_hook_active guard | N/A (dict.get with default) | Return silently |
| 2. Config load | Missing config.yaml, YAML parse error | Use hardcoded defaults (theme: "default", volume: 80) |
| 3. Theme load | Missing theme.json, JSON parse error | Fall back to "default" theme. If default also missing, return silently. |
| 4-5. Token resolution | No matching pattern, missing mapping | Return None -> no sound played |
| 6. Path resolution | Directory doesn't exist | Return silently (log warning if logging available) |
| 7. Variant selection | Empty directory (no WAV files) | Return silently |
| 8. Volume calculation | Invalid config values | Clamp to [0.0, 1.0] |
| 9. Playback | Popen failure, missing pw-play binary | Return None from play_sound(), continue |
| 10. Session state | File write failure, permission error | Skip, non-critical |
| 11. Gamification | SQLite error, schema mismatch | Skip, non-critical |
| 12. Heartbeat | File write failure | Skip, non-critical |

---

## 4. Content-Aware Routing

The Stop hook is the most important event for sound playback. It fires when Claude finishes responding, and the `last_assistant_message` field contains the full text of what Claude just said. By analyzing this text, we can select contextually appropriate sounds instead of a generic completion chime.

### Pattern Table

```python
import re
from typing import Optional

# Ordered list of (compiled_regex, sound_token) pairs.
# First match wins. Order matters — more specific patterns first.
CONTENT_PATTERNS = [
    # Git operations — most distinctive sound
    (re.compile(
        r"(?i)"
        r"(?:git commit|committed|Created commit|"
        r"pushed to|merged|pull request|PR #\d+|"
        r"cherry.pick|rebased|git push)"
    ), "commit"),

    # Errors and failures — damage/alert sound
    (re.compile(
        r"(?i)"
        r"(?:error|Error|ERROR|"
        r"failed|Failed|FAILED|"
        r"exception|Exception|"
        r"traceback|Traceback|"
        r"panic:|PANIC:|"
        r"fatal:|FATAL:)"
    ), "error"),

    # Test results — completion with emphasis
    (re.compile(
        r"(?i)"
        r"(?:tests? passed|tests? pass|"
        r"All \d+ tests|"
        r"\d+ passed|"
        r"PASSED|"
        r"test suite.*(?:pass|success))"
    ), "task_complete"),

    # Warnings — softer alert
    (re.compile(
        r"(?i)"
        r"(?:warning|Warning|WARN|"
        r"deprecated|Deprecated|"
        r"DEPRECATED)"
    ), "notification"),

    # Package management — installation complete
    (re.compile(
        r"(?i)"
        r"(?:installed|upgraded|"
        r"updated.*package|"
        r"Successfully installed|"
        r"added \d+ packages)"
    ), "task_complete"),

    # File operations — creation/write complete
    (re.compile(
        r"(?i)"
        r"(?:wrote.*file|created.*file|"
        r"File written|Written to|"
        r"Saved to|saved.*to)"
    ), "task_complete"),
]


def _content_aware_resolve(
    hook_data: dict,
    theme: dict,
    config: dict,
) -> Optional[str]:
    """Analyze last_assistant_message to select a contextual sound token.

    Resolution order:
    1. Theme-specific content_aware_overrides (theme.json)
    2. Built-in CONTENT_PATTERNS (above)
    3. Return None (caller falls back to default Stop sound)

    Args:
        hook_data: Full hook payload (needs last_assistant_message)
        theme: Loaded theme dict
        config: Loaded config dict

    Returns:
        Sound token string, or None if no pattern matched.
    """
    message = hook_data.get("last_assistant_message", "")
    if not message:
        return None

    # 1. Theme-specific overrides (allow themes to define custom patterns)
    theme_overrides = theme.get("content_aware_overrides", [])
    for override in theme_overrides:
        pattern = override.get("pattern", "")
        token = override.get("sound_token", "")
        if pattern and token:
            try:
                if re.search(pattern, message):
                    return token
            except re.error:
                continue  # Invalid regex in theme, skip

    # 2. Built-in patterns
    for regex, token in CONTENT_PATTERNS:
        if regex.search(message):
            return token

    # 3. No match — caller uses default
    return None
```

### Theme-Specific Content Overrides

Themes can define their own content-aware patterns in `theme.json`. This allows the StarCraft theme to play a "nuclear launch" sound when a destructive operation is detected, or the Zelda theme to play a "treasure chest" sound when a new file is created.

```json
{
  "name": "starcraft",
  "content_aware_overrides": [
    {
      "pattern": "(?i)deleted|removed|destroyed|dropped",
      "sound_token": "warning",
      "comment": "Destructive operations get the warning klaxon"
    },
    {
      "pattern": "(?i)deployed|launched|started.*service",
      "sound_token": "agent_deploy",
      "comment": "Service deployments sound like unit production"
    }
  ]
}
```

### The stop_hook_active Guard

**CRITICAL**: The `stop_hook_active` field prevents infinite hook loops. When True, it means another Stop hook has already fired this turn and injected a new prompt (causing Claude to respond again, which fires another Stop). Without this guard, a Stop hook that injects feedback would create an infinite cycle.

```python
# This check MUST be the very first thing in route_event():
if hook_data.get("stop_hook_active"):
    return  # Don't play sound for hook-triggered stops
```

How the loop would happen without this guard:
1. Claude finishes responding -> Stop hook fires
2. Stop hook returns `{"decision": "block", "reason": "some feedback"}`
3. Claude processes the feedback and responds again -> Stop hook fires again
4. Repeat forever

claude-voice outputs `{}` (no decision/reason), so it cannot directly cause loops. But the guard is still essential because **other plugins' Stop hooks** may inject prompts, and claude-voice should not play a sound for those synthetic stops.

### SubagentStop Content Awareness

SubagentStop also carries `last_assistant_message` — the subagent's final output. The same content-aware resolution applies, but with a different default: when no pattern matches, SubagentStop falls back to `agent_return` (not `task_complete`).

```python
def _resolve_sound_token(event_name, hook_data, theme, config):
    if event_name in ("Stop", "SubagentStop"):
        token = _content_aware_resolve(hook_data, theme, config)
        if token:
            return token
        # Fallback differs by event
        if event_name == "SubagentStop":
            return "agent_return"
        return "task_complete"  # Default for Stop
    # ... rest of resolution
```

---

## 5. Hook Handler Implementation (`hooks/voice_event.py`)

The hook handler is the entry point that Claude Code executes. It is a UV single-file script with PEP 723 inline metadata.

### Full Specification

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
claude-voice hook handler — single entry point for all hook events.

Receives the event name as argv[1] and JSON payload via stdin.
Routes through the theme engine to play appropriate sounds.

Usage (by Claude Code, via plugin.json):
    echo '<json>' | uv run ${CLAUDE_PLUGIN_ROOT}/hooks/voice_event.py SessionStart

Exit code is ALWAYS 0. Stdout is ALWAYS "{}".
These invariants are non-negotiable — a broken sound system must be
invisible to Claude Code. A hanging or crashing hook blocks the entire session.
"""

import json
import sys


def main() -> None:
    """Main entry point. Handles all error cases."""

    # --- Step 1: Parse argv for event name ---
    if len(sys.argv) < 2:
        # No event name provided. This should never happen if plugin.json
        # is correct, but handle it gracefully.
        print("{}")
        return

    event_name = sys.argv[1]

    # --- Step 2: Read stdin with protection ---
    hook_data = _parse_stdin()

    # --- Step 3: Early exit conditions ---
    # Mute check: if ~/.claude/local/voice/muted exists, skip everything.
    # This is a fast-path bypass that doesn't require loading config.yaml.
    import os
    from pathlib import Path
    mute_flag = Path.home() / ".claude" / "local" / "voice" / "muted"
    if mute_flag.exists():
        print("{}")
        return

    # --- Step 4: Import lib modules (lazy) ---
    # The lib/ directory is inside the plugin at ${CLAUDE_PLUGIN_ROOT}/lib/.
    # We add the plugin root to sys.path so we can import lib.router.
    plugin_root = Path(__file__).resolve().parent.parent
    if str(plugin_root) not in sys.path:
        sys.path.insert(0, str(plugin_root))

    try:
        from lib.router import route_event
    except ImportError:
        # lib/ not available (first install, broken cache, etc.)
        # Fail silently — no sound is better than a crash.
        print("{}")
        return

    # --- Step 5: Route the event ---
    route_event(event_name, hook_data)

    # --- Step 6: Respond to Claude Code ---
    # For SessionStart, optionally inject theme context into the session.
    if event_name == "SessionStart":
        response = _build_session_start_response(hook_data)
        print(json.dumps(response))
    else:
        print("{}")


def _parse_stdin() -> dict:
    """Read and parse hook stdin JSON.

    Hook stdin can be:
    - Valid JSON object (normal case)
    - Empty string (some events in edge cases)
    - Malformed (defensive parsing)

    Always returns a dict. Never raises.
    """
    try:
        raw = sys.stdin.read()
        if not raw or not raw.strip():
            return {}
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, IOError):
        return {}


def _build_session_start_response(hook_data: dict) -> dict:
    """Build optional SessionStart response with theme context.

    This injects theme information into the Claude Code session context
    so the agent knows what voice theme is active. The information appears
    in the system prompt via hookSpecificOutput.additionalContext.
    """
    try:
        from lib.config import load_config
        config = load_config()
        theme = config.get("theme", "default")
        volume = config.get("volume", 80)
        muted = config.get("muted", False)
        gamification = config.get("gamification", {}).get("enabled", False)

        status_parts = [
            f"Theme: {theme}",
            f"Volume: {volume}",
        ]
        if muted:
            status_parts.append("MUTED")
        if gamification:
            status_parts.append("XP: on")

        context_line = f"[voice] {' | '.join(status_parts)}"

        return {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context_line,
            }
        }
    except Exception:
        return {}


# --- Top-level error boundary ---
# This is the Prime Directive enforcement point.
# No matter what happens above, we print {} and exit 0.
if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise  # Allow explicit sys.exit() calls
    except Exception:
        # Catch absolutely everything else
        try:
            print("{}")
        except Exception:
            pass  # Even print can fail (broken pipe, etc.)
    # Exit code 0 is implicit when main() returns normally.
    # If we reach here via the except branch, Python exits 0.
```

### Execution Flow Diagram

```
Claude Code fires hook event
        |
        v
uv run voice_event.py <EventName>    # argv[1] = event name
        |
        v
stdin: JSON payload                   # Claude Code writes, then closes stdin
        |
        v
[1] Parse argv                        # Get event_name from sys.argv[1]
        |
        v
[2] Read stdin                        # sys.stdin.read() -> json.loads()
        |                              # On failure: return {}
        v
[3] Mute check                        # Fast path: ~/.claude/local/voice/muted
        |                              # Exists? -> print("{}"), return
        v
[4] Import lib.router                 # Lazy import, add plugin_root to sys.path
        |                              # ImportError? -> print("{}"), return
        v
[5] route_event(event_name, hook_data) # Full routing pipeline (see section 3)
        |                              # Internal try/except -> never raises
        v
[6] Print response                    # SessionStart: JSON with theme context
        |                              # All others: "{}"
        v
Exit 0                                # Always. No exceptions. Ever.
```

### Why No Dependencies in PEP 723

The `dependencies = []` line means the hook script itself has zero external dependencies. All imports are from the Python stdlib (`json`, `sys`, `pathlib`, `os`). The `lib/` modules may have their own dependencies (PyYAML for config, etc.), but those are managed by the plugin's `pyproject.toml` and resolved by UV when the plugin is installed. The hook script imports them lazily, and if they fail to import, it degrades to silence.

This is a deliberate design choice: the hook script must be able to start even if the plugin's dependencies are not yet installed. It will do nothing (no sound), but it will not crash.

---

## 6. Session State Machine

The session state machine tracks the lifecycle of a Claude Code session for contextual sound decisions. State is tracked per-session in a lightweight JSON file.

### State Diagram

```
                SessionStart
                    |
                    v
    INACTIVE ----> STARTING
                    |
                    | (after boot sound completes)
                    v
                  ACTIVE <-----------+
                    |                 |
          +---------+--------+       |
          |         |        |       |
   SubagentStart  Stop   Error       |
          |         |        |       |
          v         v        v       |
     ACTIVE      ACTIVE   ACTIVE     |
     (agents++)  (sound)  (sound)    |
          |                          |
    SubagentStop                     |
          |                          |
          v                          |
     ACTIVE ---------+               |
     (agents--)      |               |
                     |               |
              (if agents > 0)--------+
                     |
              (if agents == 0)
                     |
              SessionEnd
                     |
                     v
                  STOPPING
                     |
                     | (after farewell sound)
                     v
                  INACTIVE
```

### State Transitions

| Current State | Event | New State | Action | Condition |
|---------------|-------|-----------|--------|-----------|
| `INACTIVE` | `SessionStart` | `STARTING` | Play `session_start` sound. Create session state file. | — |
| `STARTING` | (immediate) | `ACTIVE` | Transition happens within the same hook invocation, after sound is fired. | — |
| `ACTIVE` | `Stop` | `ACTIVE` | Play content-aware sound. Increment `sound_count`. | `stop_hook_active == false` |
| `ACTIVE` | `SubagentStart` | `ACTIVE` | Play `agent_deploy` (if registered). Increment `agent_count`. | (when SubagentStart is enabled) |
| `ACTIVE` | `SubagentStop` | `ACTIVE` | Play `agent_return`. Decrement `agent_count`. | `stop_hook_active == false` |
| `ACTIVE` | `PostToolUseFailure` | `ACTIVE` | Play `error` sound. | — |
| `ACTIVE` | `Notification` | `ACTIVE` | Play `notification` sound. | — |
| `ACTIVE` | `SessionEnd` | `STOPPING` | Play `session_end` sound. | — |
| `STOPPING` | (immediate) | `INACTIVE` | Delete session state file (or mark as ended). | — |

### Session State File

Location: `~/.claude/local/voice/sessions/{session_id}.json`

```json
{
  "state": "active",
  "theme": "starcraft",
  "start_time": "2026-03-26T01:30:00-07:00",
  "source": "startup",
  "model": "claude-opus-4-6",
  "agent_count": 0,
  "sound_count": 12,
  "last_event": "Stop",
  "last_sound": "task_complete",
  "last_sound_time": "2026-03-26T01:45:23-07:00"
}
```

### Why Track Session State?

Session state enables decisions that a stateless handler cannot make:

1. **Idempotency**: Don't play `session_start` boot sound if the session file already exists with `state: "active"` (handles `source: "compact"` SessionStart events, which restart the context but not the session).

2. **Agent-aware routing**: When `agent_count > 0`, suppress noisy sounds (e.g., `prompt_ack`) to reduce audio clutter during multi-agent work. Play a different completion sound when agents are active vs solo.

3. **Session summary at end**: When `SessionEnd` fires, the session state file tells us how many sounds played, how long the session lasted, and what the last action was — useful for gamification XP calculation.

4. **Error streak detection**: If the last N events were all `PostToolUseFailure`, play an escalating error sound instead of the same beep. Requires history, which the state file provides.

### State File Operations

State files use the same atomic write pattern as `claude-statusline/lib/state.py`:

```python
import fcntl
import json
import os
import tempfile
from pathlib import Path

SESSIONS_DIR = Path.home() / ".claude" / "local" / "voice" / "sessions"
LOCK_SUFFIX = ".lock"


def read_session_state(session_id: str) -> dict:
    """Read session state. Returns empty dict if missing or corrupt."""
    state_path = SESSIONS_DIR / f"{session_id}.json"
    try:
        if state_path.exists():
            return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def write_session_state(session_id: str, state: dict) -> None:
    """Atomically write session state under file lock.

    Uses tempfile + os.rename for atomic write.
    Uses fcntl.flock for cross-process coordination.
    """
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    state_path = SESSIONS_DIR / f"{session_id}.json"
    lock_path = state_path.with_suffix(LOCK_SUFFIX)

    try:
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(SESSIONS_DIR),
                prefix=".session_",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                os.rename(tmp_path, str(state_path))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
    except Exception:
        pass  # Non-critical — skip state write
```

### Session Cleanup

Session state files accumulate over time. Cleanup is handled by:

1. **SessionEnd handler**: Deletes the session file (or moves to an archive directory for analytics).
2. **Periodic cleanup** (via `/maintain` or cron): Delete session files older than 7 days. Typical file count: 1-3 active sessions.
3. **Size**: Each file is <500 bytes. Even 1000 stale files is <500KB. Not a priority concern.

---

## 7. Subagent Sound Coordination

Multiple subagents can run concurrently within a single Claude Code session. This creates coordination challenges for sound playback.

### Problems

1. **Simultaneous SubagentStop events**: Multiple subagents can finish at the same time, triggering N SubagentStop hooks in rapid succession. Each hook invocation is a separate process.

2. **TTS narration serialization**: If TTS is enabled for subagent summaries, multiple agents finishing simultaneously would produce overlapping speech — unintelligible audio soup.

3. **Sound pile-up**: 3 agents finishing within 500ms means 3 `agent_return` sounds overlapping. Short earcons are tolerable when overlapped. Long sounds or TTS are not.

4. **Shared audio output**: All hook processes write to the same PipeWire audio output. PipeWire mixes concurrent streams automatically, but the result may not be pleasant.

### Solutions

**Earcons (short sound effects): Allow overlap.** Multiple short sounds (100-400ms) playing simultaneously is acceptable. PipeWire mixes them into a chord-like composite that reads as "multiple things completed." This is the game audio pattern — in StarCraft, multiple units completing simultaneously produce overlapping acknowledgment sounds and it works.

**TTS narration: fcntl queue.** When TTS is enabled for subagent summaries, each hook process must acquire an exclusive file lock before speaking. The Disler pattern from `tts_queue.py` solves this:

```python
import fcntl
import os
import time
from pathlib import Path

TTS_LOCK_PATH = Path.home() / ".claude" / "local" / "voice" / "tts.lock"
TTS_LOCK_META_PATH = TTS_LOCK_PATH.with_suffix(".meta")


def acquire_tts_lock(agent_id: str, timeout: float = 30.0) -> bool:
    """Acquire exclusive TTS lock with exponential backoff.

    Args:
        agent_id: Identifier for the requesting agent (for stale detection)
        timeout: Maximum wait time in seconds

    Returns:
        True if lock acquired, False if timed out
    """
    TTS_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    delay = 0.1  # Start at 100ms

    while (time.monotonic() - start) < timeout:
        try:
            lock_fd = os.open(str(TTS_LOCK_PATH), os.O_CREAT | os.O_WRONLY)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Lock acquired — write metadata
                TTS_LOCK_META_PATH.write_text(
                    f'{{"agent_id":"{agent_id}","pid":{os.getpid()},'
                    f'"time":{time.time()}}}'
                )
                # Store fd for later release
                acquire_tts_lock._fd = lock_fd
                return True
            except (OSError, BlockingIOError):
                os.close(lock_fd)
        except OSError:
            pass

        # Check for stale locks (holder PID died)
        _cleanup_stale_lock()

        time.sleep(delay)
        delay = min(delay * 2, 1.0)  # Cap at 1 second

    return False


def release_tts_lock(agent_id: str) -> None:
    """Release the TTS lock."""
    try:
        fd = getattr(acquire_tts_lock, "_fd", None)
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            acquire_tts_lock._fd = None
        TTS_LOCK_META_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _cleanup_stale_lock(max_age_seconds: float = 60.0) -> None:
    """Remove stale lock if holder PID is dead or lock is too old."""
    try:
        if not TTS_LOCK_META_PATH.exists():
            return
        import json
        meta = json.loads(TTS_LOCK_META_PATH.read_text())
        pid = meta.get("pid", 0)
        lock_time = meta.get("time", 0)

        # Check if PID is still alive
        if pid and not _pid_alive(pid):
            TTS_LOCK_PATH.unlink(missing_ok=True)
            TTS_LOCK_META_PATH.unlink(missing_ok=True)
            return

        # Check age
        if lock_time and (time.time() - lock_time) > max_age_seconds:
            TTS_LOCK_PATH.unlink(missing_ok=True)
            TTS_LOCK_META_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    """Check if a PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
```

**Usage in SubagentStop:**

```python
# In router.py, for SubagentStop with TTS enabled:
def _handle_subagent_tts(hook_data: dict, config: dict) -> None:
    agent_id = hook_data.get("agent_id", "unknown")
    message = hook_data.get("last_assistant_message", "")

    if not message or not config.get("tts", {}).get("subagent_summaries", False):
        return  # TTS disabled for subagent summaries

    # Compress message for TTS (Disler pattern: <20 words)
    summary = _compress_for_tts(message)

    if acquire_tts_lock(agent_id, timeout=30):
        try:
            from lib.tts import speak
            speak(summary)
        finally:
            release_tts_lock(agent_id)
```

**Agent count tracking**: The session state file's `agent_count` field is updated on SubagentStart and SubagentStop events. When `agent_count > 0`:

- Suppress `prompt_ack` sounds (too noisy during agent work)
- Use quieter variants for `notification` (agents generate lots of notifications)
- Apply a slight volume reduction to `agent_return` when multiple agents are active (prevent volume spike from N agents finishing simultaneously)

---

## 8. Stdin Payload Parsing

### The Parse Function

```python
def parse_hook_stdin() -> dict:
    """Read and parse hook stdin JSON.

    Hook stdin can be:
    - Valid JSON object (normal case — all registered events)
    - Empty string (edge cases, some events during shutdown)
    - Malformed (defensive parsing against future format changes)

    Claude Code writes the JSON payload to the hook's stdin and then
    closes it. The hook must read ALL of stdin before processing —
    do not use sys.stdin.readline(), as some payloads (SubagentStop
    with long transcripts, Stop with long messages) are many kilobytes.

    Always returns a dict. Never raises.
    """
    try:
        raw = sys.stdin.read()
        if not raw or not raw.strip():
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        return data
    except (json.JSONDecodeError, UnicodeDecodeError, IOError):
        return {}
```

### Common Envelope Fields

Every hook payload shares these fields (present on all 14 event types):

| Field | Type | Always Present | Description |
|-------|------|---------------|-------------|
| `session_id` | string (UUID) | Yes | Unique session identifier |
| `transcript_path` | string (path) | Yes | Path to the live JSONL transcript file |
| `cwd` | string (path) | Yes | Working directory of the Claude Code session |
| `hook_event_name` | string | Yes | The event name (redundant with argv, but useful for verification) |
| `permission_mode` | string | Usually | `"default"` or `"bypassPermissions"` |
| `agent_type` | string | Usually | `"claude-personas:matt"`, `"general-purpose"`, `"Explore"`, etc. |
| `agent_id` | string (hex) | Only in subagent context | Short hex ID like `"a7450863f27b3de78"` |

### Payload Notes

The hook payload is a **flat JSON object**. There is no nested `data` key — that's an artifact of claude-logging's storage schema. Claude Code passes everything at the top level.

The `hook_event_name` field in the payload is redundant with `sys.argv[1]`. Both carry the event name. Use `sys.argv[1]` as the primary source (it's always present), and `hook_event_name` for verification if needed.

---

## 9. Hook Response Format

### Standard Response (fire-and-forget)

For most events, the hook returns an empty JSON object:

```json
{}
```

This means "proceed normally, I have nothing to say." Claude Code ignores empty responses.

### SessionStart Context Injection

On `SessionStart`, the hook can inject text into the session context via `hookSpecificOutput.additionalContext`. This text appears in the system prompt, giving the agent awareness of the active theme and volume:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "[voice] Theme: starcraft | Volume: 80 | Sounds: enabled"
  }
}
```

This is optional and non-blocking. If the config is unavailable, return `{}` instead.

### Response Shapes by Event (reference)

| Event | Stdout Purpose | claude-voice Response |
|-------|---------------|----------------------|
| `SessionStart` | `hookSpecificOutput.additionalContext` injected into Claude's context | Theme/volume context line (see above) |
| `Stop` | `decision` (block/allow), `systemMessage`, `reason` | `{}` always. claude-voice never blocks stops. |
| `SubagentStop` | `systemMessage` shown in UI | `{}` |
| `Notification` | `systemMessage` shown in UI | `{}` |
| `PostToolUseFailure` | `systemMessage` shown in UI | `{}` |
| `SessionEnd` | `systemMessage` shown in UI | `{}` |

**Hard rule**: claude-voice NEVER uses `"decision": "block"` in Stop responses. That would prevent Claude from completing — catastrophic for UX. Only security hooks (like Disler's pre_tool_use.py) should ever block.

---

## 10. Performance Budget

Wall time allocation per hook call. The target is <150ms total, with a strong preference for <50ms:

```
Step                              Budget    Notes
────────────────────────────────  ──────    ─────────────────────────────────
Python interpreter start          ~30ms     UV cached, fast. First run may be
                                            slower as UV resolves the script.
sys.argv parse                    <1ms      Trivial string access
stdin read                        ~1ms      Small payloads (1-10KB typical,
                                            SubagentStop can be larger)
JSON parse                        ~1ms      json.loads on small dicts
Mute flag check                   <1ms      Path.exists() — single stat() call
sys.path + import lib.router      ~3ms      Module import, cached after first
Config load (YAML)                ~2ms      Small file, ~20 lines
Theme load (JSON)                 ~2ms      Small file, ~50 lines
stop_hook_active guard            <1ms      Dict lookup
Content-aware regex matching      ~1ms      6 compiled regexes on short strings
Sound token resolution            <1ms      Dict lookups + list indexing
Sound directory glob              ~1ms      Path.glob("*.wav") on 3-7 files
Random variant selection          <1ms      random.choice() on small list
Volume calculation                <1ms      Two multiplications + clamp
subprocess.Popen (pw-play)        ~5ms      Fork + exec + detach
Session state write (atomic)      ~2ms      JSON write + os.rename
Gamification DB write             ~3ms      SQLite INSERT (WAL mode)
Health heartbeat write            ~1ms      Single file write
stdout response (print)           <1ms      Print "{}" or small JSON
────────────────────────────────  ──────
Total hook wall time              ~45ms     Well under 150ms target
```

The Popen'd `pw-play` process continues independently after the hook exits. Its startup latency (~30ms to first audio sample) is NOT part of hook wall time. The user hears the sound ~75-95ms after the event fires (45ms hook + 30-50ms pw-play startup).

### Worst-Case Scenarios

| Scenario | Expected Time | Mitigation |
|----------|--------------|------------|
| First UV invocation (cold cache) | ~200ms | One-time cost. Subsequent calls are cached. |
| Large `last_assistant_message` (100KB) | ~5ms for regex | Content patterns use compiled regexes, short-circuit on first match |
| Missing theme directory | ~3ms | detect + return silently, no sound |
| SQLite WAL contention | ~10ms | WAL mode allows concurrent reads during writes |
| pw-play binary not found | ~5ms | Popen raises immediately, caught by try/except |

---

## 11. Error Handling Strategy

### Error Handling Table

| Layer | Error | Response |
|-------|-------|----------|
| **Top-level** | Any unhandled exception in `main()` | Catch in `__main__` block, print `{}`, implicit exit 0 |
| **argv** | Missing event name (`len(sys.argv) < 2`) | Print `{}`, return immediately |
| **stdin** | Read failure (`IOError`) | Return `{}` dict |
| **stdin** | Malformed JSON (`JSONDecodeError`) | Return `{}` dict |
| **stdin** | Non-dict JSON (e.g., array or string) | Return `{}` dict |
| **stdin** | Unicode errors (`UnicodeDecodeError`) | Return `{}` dict |
| **mute check** | Permission error on Path.exists() | Catch in try/except, continue (assume not muted) |
| **import** | `lib.router` ImportError | Print `{}`, return (no sound) |
| **config** | Missing `config.yaml` | Use defaults: `{theme: "default", volume: 80}` |
| **config** | YAML parse error | Use defaults |
| **theme** | Missing `theme.json` for active theme | Fall back to `"default"` theme |
| **theme** | Default theme also missing | Return silently (no sound) |
| **content** | Invalid regex in theme `content_aware_overrides` | Skip that pattern, try next |
| **sound** | Sound directory doesn't exist | Return silently |
| **sound** | No WAV files in directory | Return silently |
| **volume** | Invalid config values (non-numeric, out of range) | Clamp to `[0.0, 1.0]` |
| **playback** | `subprocess.Popen` failure (missing binary, permission) | Return `None`, continue to state/gamification |
| **playback** | `FileNotFoundError` for pw-play | Try fallback chain (paplay -> aplay -> mpv) |
| **state** | Session file write failure (permission, disk full) | Skip, non-critical |
| **state** | fcntl lock failure | Skip state write entirely |
| **gamification** | SQLite error (corrupt DB, schema mismatch) | Skip, non-critical |
| **gamification** | DB file locked by another process | Skip (WAL should prevent this) |
| **heartbeat** | File write failure | Skip, non-critical |
| **response** | `print()` raises (broken pipe) | Inner try/except around print |

### The Prime Directive

> **The hook handler must NEVER block Claude Code.** A broken sound system is invisible. A hanging hook breaks the entire session.

This is enforced at three levels:

1. **Timeout**: Claude Code kills the hook process after the configured timeout (3-5 seconds). But we should never get close to this — 45ms is the target.

2. **Exit code**: The hook must always exit 0. Non-zero exit codes (except 2 for PreToolUse blocking) cause Claude Code to log errors and potentially disable the hook.

3. **Stdout**: The hook must always print valid JSON to stdout. Invalid output causes parse errors in Claude Code. `{}` is the safe default.

### Degradation Cascade

When things go wrong, the handler degrades gracefully through these levels:

```
Level 0: FULL FUNCTION
  Everything works. Sound plays within 45ms. State updated. XP tracked.

Level 1: NO GAMIFICATION
  SQLite error. Sound still plays. State still updated. XP not tracked.
  User impact: none visible. Gamification catches up on next successful write.

Level 2: NO STATE
  State file write failed. Sound still plays. Session state not tracked.
  User impact: none visible. Some context-aware decisions may be suboptimal.

Level 3: NO SOUND
  Playback failed (no pw-play, no fallback). Handler still exits cleanly.
  User impact: silence. No error messages, no delays.

Level 4: NO ROUTING
  Config or theme loading failed. Handler still exits cleanly.
  User impact: silence. Same as Level 3 from user perspective.

Level 5: NO HANDLER
  Import failure. voice_event.py prints {} and exits 0.
  User impact: silence. Claude Code doesn't know anything went wrong.

Level 6: CRASH
  Unhandled exception escapes main(). Caught by __main__ try/except.
  Prints {}. Exits 0. Claude Code doesn't know anything went wrong.
  User impact: silence.
```

At every level, the user experience degrades to silence — never to errors, hangs, or broken sessions.

---

## 12. Configuration Integration

### Config File Location

`~/.claude/local/voice/config.yaml`

### Hook Control Section

```yaml
# config.yaml — hook event toggles

# Master mute (overrides everything)
muted: false

# Global volume (0-100)
volume: 80

# Active theme
theme: starcraft

# Per-event enable/disable
hooks:
  SessionStart: true          # Boot sound
  Stop: true                  # Task completion (content-aware)
  Notification: true          # Alert chime
  SubagentStop: true          # Agent return
  SessionEnd: true            # Farewell
  PostToolUseFailure: true    # Error sound
  UserPromptSubmit: false     # Disabled by default (opt-in)
  SubagentStart: false        # Disabled by default (opt-in)
  PermissionRequest: false    # Disabled by default (opt-in)
  PreCompact: false           # Disabled by default (opt-in)

# Per-category volume multipliers (applied on top of master volume)
category_volumes:
  feedback: 1.0               # session_start, session_end, task_complete, commit
  alert: 1.0                  # error, notification, permission
  agent: 0.8                  # agent_deploy, agent_return (slightly quieter)
  ambient: 0.6                # compact, level_up, achievement

# Content-aware routing
content_aware:
  enabled: true               # Set false to use only direct mapping for Stop
  # Note: content patterns are defined in code (CONTENT_PATTERNS) and
  # optionally overridden in theme.json (content_aware_overrides).

# Gamification
gamification:
  enabled: false              # Off by default in v0.1

# TTS for subagent summaries
tts:
  subagent_summaries: false   # Off by default in v0.1
```

### Config Loading

```python
import yaml
from pathlib import Path
from typing import Any, Dict

CONFIG_PATH = Path.home() / ".claude" / "local" / "voice" / "config.yaml"

DEFAULT_CONFIG: Dict[str, Any] = {
    "muted": False,
    "volume": 80,
    "theme": "default",
    "hooks": {
        "SessionStart": True,
        "Stop": True,
        "Notification": True,
        "SubagentStop": True,
        "SessionEnd": True,
        "PostToolUseFailure": True,
        "UserPromptSubmit": False,
        "SubagentStart": False,
        "PermissionRequest": False,
        "PreCompact": False,
    },
    "category_volumes": {
        "feedback": 1.0,
        "alert": 1.0,
        "agent": 0.8,
        "ambient": 0.6,
    },
    "content_aware": {
        "enabled": True,
    },
    "gamification": {
        "enabled": False,
    },
    "tts": {
        "subagent_summaries": False,
    },
}


def load_config() -> Dict[str, Any]:
    """Load config from disk. Returns defaults if missing or corrupt.

    Merges user config with defaults so new keys are always present.
    """
    config = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.exists():
            raw = CONFIG_PATH.read_text(encoding="utf-8")
            user_config = yaml.safe_load(raw)
            if isinstance(user_config, dict):
                _deep_merge(config, user_config)
    except Exception:
        pass  # Use defaults
    return config


def _deep_merge(base: dict, override: dict) -> None:
    """Merge override into base, recursively for nested dicts."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
```

### Config Check in Router

The config check is the second thing that happens in `route_event()`, right after the `stop_hook_active` guard:

```python
# In route_event():
config = load_config()

# Master mute check (redundant with mute file, but config.yaml is canonical)
if config.get("muted", False):
    return

# Per-event check
if not config.get("hooks", {}).get(event_name, False):
    return  # This event is disabled
```

The mute file (`~/.claude/local/voice/muted`) is a fast-path optimization: the hook script checks it before importing `lib/` modules. The `config.yaml` muted flag is the canonical setting checked inside the router. Both must agree.

---

## 13. Gamification Hook Points

Gamification is tracked per-session and persisted to SQLite. The hook handler writes XP events after successful sound playback. Detailed in `specs/09-gamification.md` (Wave 4) — here we define only the hook integration points.

### XP Award Table

| Event | Sound Token | XP | Condition |
|-------|------------|-----|-----------|
| `Stop` | `task_complete` | 10 | Every standard completion |
| `Stop` | `commit` | 50 | Content-aware: git commit detected |
| `Stop` | `error` | 0 | Error completion — no XP for errors (but error resolution earns bonus) |
| `SubagentStop` | `agent_return` | 20 | Agent completed work |
| `SessionEnd` | `session_end` | 5 | Session participation (showed up) |
| `PostToolUseFailure` | `error` | 0 | Tool failure — no direct XP |
| (derived) | `error_resolved` | 30 | An `error` sound followed by a `task_complete` within the same session. Detected by checking session state: if `last_sound == "error"` and current sound is `task_complete`, award bonus. |
| (derived) | `level_up` | 0 | Level boundary crossed — triggers special sound, no additional XP |
| (derived) | `achievement` | varies | Achievement unlocked — triggers special sound, XP varies by achievement |

### Hook Integration Point

```python
# In router.py, after successful playback:

XP_TABLE = {
    "task_complete": 10,
    "commit": 50,
    "agent_return": 20,
    "session_end": 5,
    "error_resolved": 30,
}

def _update_gamification(
    session_id: str,
    event_name: str,
    sound_token: str,
    hook_data: dict,
) -> None:
    """Record XP for a sound event. Non-critical — skips on any error."""
    try:
        xp = XP_TABLE.get(sound_token, 0)
        if xp <= 0:
            return

        # Error resolution bonus
        session_state = read_session_state(session_id)
        if sound_token == "task_complete" and session_state.get("last_sound") == "error":
            xp += XP_TABLE.get("error_resolved", 0)

        from lib.gamification import award_xp
        level_up = award_xp(session_id, sound_token, xp)

        if level_up:
            # Trigger level-up sound (async, don't wait)
            from lib.audio import play_sound
            from lib.theme import load_theme, resolve_sound_path
            theme = load_theme(load_config().get("theme", "default"))
            level_up_dir = resolve_sound_path(theme, "level_up")
            if level_up_dir:
                import random
                variants = list(level_up_dir.glob("*.wav"))
                if variants:
                    play_sound(str(random.choice(variants)))
    except Exception:
        pass  # Gamification is never critical
```

---

## 14. Testing Specification

### Unit Tests

Test the routing logic with mocked stdin payloads and mocked playback:

```python
# tests/test_router.py

def test_stop_routes_to_task_complete():
    """Default Stop event maps to task_complete sound token."""
    hook_data = {
        "session_id": "test-123",
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "last_assistant_message": "Done. The file has been updated.",
    }
    token = _resolve_sound_token("Stop", hook_data, DEFAULT_THEME, DEFAULT_CONFIG)
    assert token == "task_complete"


def test_stop_with_commit_routes_to_commit():
    """Stop event with git commit in message maps to commit sound."""
    hook_data = {
        "session_id": "test-123",
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "last_assistant_message": "Created commit abc1234: Fix login validation",
    }
    token = _resolve_sound_token("Stop", hook_data, DEFAULT_THEME, DEFAULT_CONFIG)
    assert token == "commit"


def test_stop_with_error_routes_to_error():
    """Stop event with error in message maps to error sound."""
    hook_data = {
        "session_id": "test-123",
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "last_assistant_message": "Error: the file was not found.",
    }
    token = _resolve_sound_token("Stop", hook_data, DEFAULT_THEME, DEFAULT_CONFIG)
    assert token == "error"


def test_stop_hook_active_skips():
    """stop_hook_active=True causes immediate return with no sound."""
    hook_data = {
        "session_id": "test-123",
        "stop_hook_active": True,
        "last_assistant_message": "...",
    }
    # route_event should return immediately
    # Mock play_sound and assert it was NOT called


def test_session_start_routes_correctly():
    """SessionStart maps to session_start token."""
    token = _resolve_sound_token("SessionStart", {}, DEFAULT_THEME, DEFAULT_CONFIG)
    assert token == "session_start"


def test_notification_routes_correctly():
    """Notification maps to notification token."""
    token = _resolve_sound_token("Notification", {}, DEFAULT_THEME, DEFAULT_CONFIG)
    assert token == "notification"


def test_post_tool_use_failure_routes_to_error():
    """PostToolUseFailure maps to error token."""
    token = _resolve_sound_token("PostToolUseFailure", {}, DEFAULT_THEME, DEFAULT_CONFIG)
    assert token == "error"


def test_subagent_stop_default():
    """SubagentStop with no content match falls back to agent_return."""
    hook_data = {
        "session_id": "test-123",
        "stop_hook_active": False,
        "last_assistant_message": "I gathered the information you requested.",
    }
    token = _resolve_sound_token("SubagentStop", hook_data, DEFAULT_THEME, DEFAULT_CONFIG)
    assert token == "agent_return"


def test_disabled_event_skips():
    """Disabled event in config produces no sound."""
    config = {**DEFAULT_CONFIG, "hooks": {"Stop": False}}
    # route_event should return after config check
    # Mock play_sound and assert it was NOT called


def test_muted_skips():
    """Muted config produces no sound for any event."""
    config = {**DEFAULT_CONFIG, "muted": True}
    # route_event should return after mute check


def test_content_patterns_all_match():
    """Verify each CONTENT_PATTERNS regex matches its intended text."""
    test_cases = [
        ("Created commit abc1234: Fix bug", "commit"),
        ("git commit -m 'fix'", "commit"),
        ("pushed to origin/main", "commit"),
        ("Error: file not found", "error"),
        ("FAILED to connect", "error"),
        ("Traceback (most recent call last):", "error"),
        ("All 42 tests passed", "task_complete"),
        ("Warning: deprecated API", "notification"),
        ("Successfully installed numpy-2.0", "task_complete"),
        ("File written to /home/shawn/output.txt", "task_complete"),
    ]
    for message, expected_token in test_cases:
        hook_data = {"last_assistant_message": message}
        token = _content_aware_resolve(hook_data, {}, {})
        assert token == expected_token, f"Expected {expected_token} for: {message}"
```

### Integration Tests

Fire actual hooks via the command line:

```bash
# Test SessionStart
echo '{"session_id":"test-001","cwd":"/home/shawn","hook_event_name":"SessionStart","source":"startup","model":"claude-opus-4-6"}' | \
  uv run hooks/voice_event.py SessionStart

# Test Stop (generic completion)
echo '{"session_id":"test-001","hook_event_name":"Stop","stop_hook_active":false,"last_assistant_message":"Done. The changes have been applied."}' | \
  uv run hooks/voice_event.py Stop

# Test Stop (git commit detected)
echo '{"session_id":"test-001","hook_event_name":"Stop","stop_hook_active":false,"last_assistant_message":"Created commit abc1234: Fix login validation\n\n3 files changed"}' | \
  uv run hooks/voice_event.py Stop

# Test Stop (error detected)
echo '{"session_id":"test-001","hook_event_name":"Stop","stop_hook_active":false,"last_assistant_message":"Error: FileNotFoundError: No such file or directory"}' | \
  uv run hooks/voice_event.py Stop

# Test stop_hook_active guard (should produce no sound)
echo '{"session_id":"test-001","hook_event_name":"Stop","stop_hook_active":true,"last_assistant_message":"..."}' | \
  uv run hooks/voice_event.py Stop

# Test PostToolUseFailure
echo '{"session_id":"test-001","hook_event_name":"PostToolUseFailure","tool_name":"Bash","error":"Command failed with exit code 1","is_interrupt":false}' | \
  uv run hooks/voice_event.py PostToolUseFailure

# Test Notification
echo '{"session_id":"test-001","hook_event_name":"Notification","message":"Claude needs your permission to use Write","notification_type":"permission_prompt"}' | \
  uv run hooks/voice_event.py Notification

# Test SubagentStop
echo '{"session_id":"test-001","agent_id":"a7450863f","agent_type":"Explore","hook_event_name":"SubagentStop","stop_hook_active":false,"last_assistant_message":"Research complete."}' | \
  uv run hooks/voice_event.py SubagentStop

# Test SessionEnd
echo '{"session_id":"test-001","hook_event_name":"SessionEnd","reason":"other"}' | \
  uv run hooks/voice_event.py SessionEnd
```

### Timing Tests

Measure wall time for each event type:

```bash
# Measure hook wall time (should be <150ms, target <50ms)
for event in SessionStart Stop Notification SubagentStop SessionEnd PostToolUseFailure; do
  echo "Testing $event..."
  time echo '{"session_id":"bench","hook_event_name":"'$event'","stop_hook_active":false,"last_assistant_message":"test"}' | \
    uv run hooks/voice_event.py $event
  echo "---"
done
```

### Crash Safety Tests

Verify the handler never exits non-zero:

```bash
# Empty stdin
echo "" | uv run hooks/voice_event.py Stop; echo "Exit: $?"

# Malformed JSON
echo "not json at all" | uv run hooks/voice_event.py Stop; echo "Exit: $?"

# Valid JSON but wrong type (array instead of object)
echo '[1,2,3]' | uv run hooks/voice_event.py Stop; echo "Exit: $?"

# Missing fields
echo '{}' | uv run hooks/voice_event.py Stop; echo "Exit: $?"

# No event name argument
echo '{"session_id":"test"}' | uv run hooks/voice_event.py; echo "Exit: $?"

# Huge payload (100KB message)
python3 -c "import json; print(json.dumps({'session_id':'test','last_assistant_message':'x'*100000,'stop_hook_active':False}))" | \
  uv run hooks/voice_event.py Stop; echo "Exit: $?"

# All should print "Exit: 0"
```

---

## 15. References

### Internal Specs

- `specs/01-plugin-scaffold.md` — Plugin directory structure, `plugin.json` hook registration, skill scaffold
- `specs/02-theme-engine.md` — `theme.json` schema, `hook_to_sound` mapping, semantic token vocabulary, theme inheritance
- `specs/05-audio-playback.md` — pw-play integration, fire-and-forget pattern, fallback chain, fcntl playback queue
- `specs/09-gamification.md` — XP system, level curve, achievements, SQLite schema, sound triggers (Wave 4)
- `ARCHITECTURE.md` — System overview, component inventory, data flow, integration map

### Research Sources

- `~/.claude/local/research/2026/03/25/voice/01-claude-code-hooks.md` — Complete hook event taxonomy with payload schemas from production data (14 events, 94K+ lifetime invocations)
- `~/.claude/local/research/2026/03/25/voice/03-disler-hooks-mastery.md` — Disler's hook patterns: TTS priority chain, fcntl queue, non-blocking subprocess pattern, AI-generated subagent summaries

### Production Data

- Hook event counts from `~/.claude/local/logging/-home-shawn/db/logging.db` (31K+ events across 14 types)
- Payload schemas extracted from live hook invocations and JSONL session logs
- Timing measurements from PipeWire audio pipeline on this machine (RTX 4070, PipeWire 1.6.2)

### Pattern References

- `claude-logging/hooks/log_event.py` — Gold standard hook handler implementation (940 lines, all 14 events, single-file dispatcher)
- `claude-statusline/lib/state.py` — Atomic state management with fcntl locks and tempfile+rename
- Disler's `tts_queue.py` — fcntl file-lock TTS queue with stale lock cleanup and exponential backoff
