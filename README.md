# Claude Code Voice Plugin

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An audio sensory layer for Claude Code. Themed earcons on hook events, text-to-speech assistant responses, spatial volume mixing across tmux panes, and per-persona voice identities — all running locally on your GPU.

The terminal wastes a sensory channel. You code in silence while your AI agent dispatches subagents, completes tasks, encounters errors, and manages context — all invisibly. This plugin makes those events audible. A boot chime on session start. A completion sound when a task finishes. An error tone when something breaks. A deployment sound when a subagent is dispatched. Over time, you stop consciously listening and start *knowing* — the way a gamer knows the battlefield state from audio alone.

---

## Features

- **Themed earcons** — short sounds on every hook event (session start, task complete, error, subagent deploy/return, etc.). 7 themes from clean professional to game-inspired.
- **Text-to-speech** — assistant responses spoken aloud via Kokoro-82M (local GPU, ~555MB VRAM). Per-persona voices: each agent can speak in its own voice.
- **Spatial volume mixer** — volume follows cognitive distance. Focused pane at full volume, split panes at half, background sessions at whisper, other sessions silent. Errors override spatial rules.
- **7 game themes** — Default, StarCraft, Warcraft, Mario, Zelda, Smash Bros, Kingdom Hearts. Each with distinct sonic DNA, deep-merge inherited from default. Hot-swap without restart.
- **RTS agent sound model** — inspired by Warcraft III's unit sound taxonomy. Each persona gets select/acknowledge/complete/error sounds. Subagent dispatch = unit deployment.
- **Voice arbiter** — asyncio daemon coordinating multi-agent TTS. 5 voice modes (Ambient/Focused/Solo/Silent/Broadcast), per-pane priority queues, GateEngine with 5 gates, virtual voice pattern for non-destructive focus switching.
- **Content-aware routing** — Stop events check the assistant's response: git commits get a commit sound, test passes get a success sound, errors get an error sound.
- **Procedural sound synthesis** — all 252+ WAVs generated from numpy/scipy via DSP primitives. No sample libraries, no assets to download.
- **<150ms latency** — from hook fire to first audio frame. Earcons are fire-and-forget. TTS is queued through the arbiter.
- **Zero external dependencies in the hook path** — the critical path imports nothing outside stdlib + the plugin's own `lib/`.

---

## Install

```
/plugin marketplace add linuxiscool/claude-voice
/plugin install claude-voice
```

Or clone locally:

```bash
git clone https://github.com/LinuxIsCool/claude-voice ~/.claude/plugins/claude-voice
```

### Requirements

- **PipeWire** (Linux) — `pw-play` is the primary audio backend. Fallback chain: `paplay` → `aplay` → `mpv`.
- **For TTS**: Kokoro-82M in a separate venv at `~/.local/share/kokoro-env/` (~555MB VRAM on GPU).
- **For sound generation**: `numpy` and `scipy` (via PEP 723, auto-installed by `uv`).

---

## Quick Start

```
/voice                    # show current status (theme, volume, mute)
/voice theme starcraft    # switch theme
/voice test               # play test sounds
/voice volume 0.6         # set volume
/voice mute               # mute all audio
/voice unmute             # unmute
/voice preset focus-only  # only focused pane speaks
/voice preset hear-all    # all panes at full volume
```

---

## Themes

| Theme | Slug | Sonic Character |
|-------|------|-----------------|
| Default | `default` | Clean sine/triangle, professional calm |
| StarCraft | `starcraft` | Square waves, radio chirps, digital military |
| Warcraft | `warcraft` | War drums, horn brass, fantasy organic |
| Mario | `mario` | Bright chiptune, bouncy pulse waves |
| Zelda | `zelda` | Harp arpeggios, ocarina, crystalline bells |
| Smash Bros | `smash` | Sharp transients, impacts, arena energy |
| Kingdom Hearts | `kingdom-hearts` | Piano, choir, strings, orchestral |

Game themes inherit from default via deep merge — only overridden fields are required. Switch with `theme: starcraft` in config or `/voice theme starcraft`.

---

## Architecture

Two playback paths:

```
Hook event → voice_event.py → router.py → theme.py → audio.py → pw-play   [earcons: fire-and-forget]
                                        └→ tts.py → tts_daemon.py → queue_client.py → arbiter → pw-play   [TTS: queued]
```

- **Earcons** bypass the queue entirely — `subprocess.Popen(start_new_session=True)`, fire-and-forget.
- **TTS speech** is synthesized by the TTS daemon (Kokoro-82M, kept warm in VRAM), then scheduled through the voice arbiter which manages turn-taking across multiple agents and panes.

### Daemons

Three systemd user services:

| Service | Purpose |
|---------|---------|
| `voice-tts.service` | Keeps Kokoro-82M warm in GPU VRAM, serves synthesis requests |
| `voice-arbiter.service` | Voice orchestrator: 5 modes, per-pane queues, GateEngine |
| `voice-stt.service` | Wake word + VAD + transcription (experimental) |

Managed via `voice.target` (umbrella unit) and `voice-health.timer` (self-healing health checks).

### Voice Modes

| Mode | Behavior |
|------|----------|
| Ambient | All panes speak, spatial volume mixing active (default) |
| Focused | Only the focused pane speaks; others go virtual |
| Solo | One specific pane speaks |
| Silent | No TTS output; earcons still play |
| Broadcast | All panes at full volume |

---

## Spatial Volume Mixer

Volume follows cognitive distance:

```
effective = max(base × spatial_multiplier, base × priority_floor)
```

| Pane relationship | Default multiplier |
|-------------------|-------------------|
| Focused (selected) | 1.0 |
| Same window (split) | 0.5 |
| Same session | 0.2 |
| Other session | 0.0 (silent) |

**Priority floors** let critical events override spatial silencing — errors from background panes are still audible at 80%.

---

## Event → Sound Mapping

| Hook Event | Sound | Description |
|------------|-------|-------------|
| SessionStart | `session_start` | Session greeting |
| Stop | `task_complete` | Task finished (content-aware: commits, errors, test passes) |
| Notification | `notification` | Attention needed |
| SubagentStart | `agent_deploy` | Subagent dispatched |
| SubagentStop | `agent_return` | Subagent finished |
| SessionEnd | `session_end` | Session closing |
| PostToolUseFailure | `error` | Tool failed |
| PreCompact | `compact` | Context compaction |
| PermissionRequest | `permission` | Permission dialog |

---

## Configuration

`~/.claude/local/voice/config.yaml`:

```yaml
theme: default
volume: 0.8
mute: false
hooks:
  SessionStart: true
  Stop: true
  Notification: true
  SubagentStart: true
  SubagentStop: true
  SessionEnd: true
  PostToolUseFailure: true
  UserPromptSubmit: false    # disabled by default (too frequent)
  PreCompact: true
  PermissionRequest: true
categories:
  earcon: 1.0
  notification: 1.0
  ambient: 0.3
tts:
  enabled: false              # set true to enable TTS
  voice: am_onyx              # Kokoro voice preset
  greeting: true              # speak theme greeting on session start
  response: true              # speak assistant response on Stop
  response_max_chars: 15000
tmux:
  focus_volumes:
    focused: 1.0
    same_window: 0.5
    same_session: 0.2
    other_session: 0.0
  priority_floors:
    "2": 0.8                  # errors always audible
    "1": 0.0
    "0": 0.0
```

---

## Design Principles

- **Hooks never crash.** Every entry point is wrapped in try/except. Always exit 0, always print `{}`. A crashed hook blocks Claude Code.
- **Fire-and-forget earcons, queued TTS.** Earcon overlap sounds natural (like game audio). TTS overlap is cacophony. The queue prevents it.
- **File-as-IPC.** The filesystem is the IPC bus. `stt-active` exists when the user speaks. `tts-playing` exists when TTS plays. Every process can read any flag. `ls ~/.claude/local/voice/` shows all state.
- **Fail open, except STT.** Audio failures default to "play normally." But `stt-active` causes silence — playing audio over the microphone is catastrophically worse than silence.
- **Sovereignty over cloud.** Kokoro-82M runs locally on your GPU. No cloud APIs required. No data leaves your machine.

---

## Philosophy

The terminal is a living world. Every agent is a unit. Every event has a sound. Every persona has a voice.

Sound is the feedback loop that makes the invisible visible — agent state, system health, task progress, temporal rhythm. This isn't a notification system. It's a sensory layer for an embodied AI.

Six game themes because coding should feel like commanding an army, not filling out spreadsheets.

---

## Data Location

All runtime data lives under `~/.claude/local/voice/`:
- `config.yaml` — user config
- `cache/tts/` — cached TTS audio (SHA256-keyed WAVs)
- `events/` — event logs (JSONL time series)
- `voice.db` — event analytics (SQLite)
- Various state files (focus-state, stt-active, tts-playing, speaking-now.json)

Nothing is uploaded. Nothing leaves your machine.

---

## Contributing

Issues and pull requests welcome. The plugin is opinionated (PipeWire-first, GPU TTS, game audio design) but the patterns are portable. Contributions that maintain the <150ms latency budget and the "hooks never crash" guarantee are appreciated.

---

## License

MIT — see [LICENSE](LICENSE).
