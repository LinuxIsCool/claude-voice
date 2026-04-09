---
name: voice
description: "Control claude-voice audio settings"
---

# /voice

Control the claude-voice audio feedback system.

## Usage

- `/voice` — Show current status (active theme, volume level, mute state)
- `/voice mute` — Mute all audio feedback
- `/voice unmute` — Unmute audio feedback
- `/voice volume 0.5` — Set volume (0.0 to 1.0)
- `/voice theme starcraft` — Switch active sound theme

## Implementation

Read the current config from `~/.claude/local/voice/config.yaml` and either display it or update it based on the subcommand. Route to the **voice** skill for all operations.

For status display, show:
1. Active theme name
2. Current volume level
3. Mute state (on/off)
4. Number of sound files in active theme
5. Audio backend (pw-play / paplay / aplay)
