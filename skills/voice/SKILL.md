---
name: voice
description: |
  Gamified audio feedback and voice I/O for Claude Code.
  Themed sound effects on every hook event. 7 themes (default + 6 game).

  Use when user says "play sound", "change theme", "test audio",
  "volume", "mute", "unmute", or references sound/audio/theme.
---

# Voice Skill

Control and configure the claude-voice audio feedback system. Themed sound effects play automatically on Claude Code hook events (session start, task complete, errors, notifications).

## Routing

| Keyword | Action |
|---------|--------|
| `theme`, `switch theme`, `set theme` | Change active theme in config |
| `test`, `play`, `preview` | Play test sounds via `scripts/play_test.py` |
| `volume`, `louder`, `quieter` | Adjust volume (0.0 - 1.0) in config |
| `mute` | Set mute: true in config |
| `unmute` | Set mute: false in config |
| `preset`, `focus`, `mode` | Apply a focus preset (focus-only, spatial, hear-all, meeting, restore) |
| *(default)* | Show current status: theme, volume, mute state |

## Config

Location: `~/.claude/local/voice/config.yaml`

```yaml
theme: default
volume: 0.8
mute: false
```

## Available Themes

- **default** -- clean, minimal notification sounds (ships with plugin)
- **starcraft** -- digital military. Square waves, radio chirps, scanner sweeps.
- **warcraft** -- fantasy organic. War drums, horn brass, stone cavern reverb.
- **mario** -- cheerful chiptune. Bouncy, bright, 8-bit.
- **zelda** -- mystical melodic. Harp, ocarina, crystalline bells.
- **smash** -- competitive punchy. Impacts, arena energy, sharp transients.
- **kingdom-hearts** -- orchestral emotional. Piano, choir, strings.

## Focus Presets

Switch spatial mixer behavior instantly:

| Preset | Effect |
|--------|--------|
| `focus-only` | Only the active pane speaks. All others silent. |
| `spatial` | Graduated: splits at 50%, background at 20%, other sessions silent. |
| `hear-all` | Full volume everywhere (default). |
| `meeting` | Near-silent: volume 0.1, TTS off, only focused pane. |
| `restore` | Undo last preset change. |

Usage: `/voice preset focus-only`

## Volume Keybindings

Physical keyboard shortcuts for instant volume control (KDE Plasma):

| Shortcut | Action | Visual Feedback |
|----------|--------|-----------------|
| `Super+Shift+Up` | Volume +10% | Progress bar notification |
| `Super+Shift+Down` | Volume -10% | Progress bar notification |
| `Super+Shift+M` | Mute toggle | Muted/Unmuted notification |

Install: `scripts/install-voice-keybindings.sh` (run once)

CLI alternative: `scripts/voice-volume.sh up|down|mute|set <0.0-1.0>|status`

## Subskills

See `subskills/` for specialized operations (theme authoring, sound synthesis, TTS configuration).
