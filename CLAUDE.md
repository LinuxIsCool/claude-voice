# claude-voice

Audio feedback plugin for Claude Code. Plays themed sounds and TTS greetings on hook events.

## Quick Reference
- Config: `~/.claude/local/voice/config.yaml`
- Sounds: `assets/themes/{theme}/sounds/` (7 themes available)
- Theme def: `assets/themes/{theme}/theme.json`
- Test: `uv run scripts/play_test.py`
- Volume keys: `Super+Shift+Up/Down/M` (install via `scripts/install-voice-keybindings.sh`)
- Volume CLI: `scripts/voice-volume.sh up|down|mute|set <value>|status`

## Themes

| Theme | Slug | Accent | Sonic Character |
|-------|------|--------|-----------------|
| Default | `default` | `#a6e3a1` | Clean, professional sine/triangle |
| StarCraft | `starcraft` | `#00ff41` | Digital military. Square waves, radio chirps. |
| Warcraft | `warcraft` | `#ff4444` | Fantasy organic. War drums, horn brass. |
| Mario | `mario` | `#ff0000` | Cheerful chiptune. Bouncy and bright. |
| Zelda | `zelda` | `#00cc66` | Mystical melodic. Harp, ocarina, bells. |
| Smash Bros | `smash` | `#ffaa00` | Competitive punchy. Impacts and arena energy. |
| Kingdom Hearts | `kingdom-hearts` | `#6699ff` | Orchestral emotional. Piano, choir, strings. |

Game themes inherit from default via deep merge -- only overridden fields are required.

Switch theme: set `theme: starcraft` in `~/.claude/local/voice/config.yaml`

## Architecture
```
Hook event -> voice_event.py -> router.py -> theme.py (resolve sound) -> audio.py (pw-play)  [earcons: fire-and-forget]
                                          └-> tts.py -> tts_daemon.py -> queue_client.py -> voice_arbiter.py -> pw-play  [TTS: queued]
```

Two playback paths:
- **Earcons** (short sounds): fire-and-forget via `audio.py`, `start_new_session=True`, bypass queue
- **TTS speech**: synthesized by `tts_daemon.py` (Kokoro-82M), queued through `voice_arbiter.py`, played one at a time
- **Per-persona voices**: each persona (matt, darren, philipp) speaks in its own Kokoro voice via `agents.get_agent_voice()`

## Key Design Rules
- Hooks NEVER crash (always exit 0, print {})
- Earcons are fire-and-forget (`start_new_session=True`); TTS is queued (daemon-managed, no `start_new_session`)
- Theme.json is single source of truth for all sound mappings
- <150ms latency budget from hook fire to first audio frame
- Zero external Python dependencies in the hook path
- Voice arbiter has 300s watchdog, ghost state guard, GateEngine, and self-healing health check

## Files
- `hooks/voice_event.py` -- Single dispatcher for all events
- `lib/audio.py` -- pw-play wrapper with fallback chain (pw-play -> paplay -> aplay -> mpv)
- `lib/router.py` -- Event routing logic
- `lib/theme.py` -- Theme loader and sound resolver (with deep merge inheritance)
- `lib/state.py` -- Config management (atomic writes via tempfile + rename)
- `scripts/play_test.py` -- Manual sound testing
- `lib/tts.py` -- TTS engine (Kokoro-82M cached synthesis, tiered fallback)
- `lib/queue_client.py` -- Enqueue speech to queue daemon (pane_id detection, socket IPC)
- `lib/flags.py` -- PID+timestamp flag files with staleness detection
- `lib/volume.py` -- Gain chain: category_vol x agent_vol x policy_vol x master_vol x system_gain
- `scripts/voice_arbiter.py` -- **Voice arbiter** — asyncio orchestrator, 5 modes, per-pane queues, GateEngine
- `scripts/voice_queue.py` -- Legacy queue daemon (superseded by arbiter, kept for reference)
- `scripts/tts_daemon.py` -- TTS synthesis daemon (Kokoro-82M, LUFS normalization, systemd)
- `scripts/stt_daemon.py` -- STT daemon (wake word + VAD + transcription)
- `scripts/voice_health.py` -- Health check with self-healing (dead pid flags auto-cleaned)
- `scripts/generate_sounds.py` -- Generate theme sounds (numpy+scipy via PEP 723)
- `scripts/tts_warmup.py` -- Pre-generate TTS greetings for all themes
- `scripts/tmux_focus_hook.sh` -- Tmux hook: atomic focus-state write + arbiter IPC
- `assets/themes/default/` -- Built-in theme (theme.json + sounds/)
- `assets/themes/{starcraft,warcraft,mario,zelda,smash,kingdom-hearts}/` -- Game themes

## Voice Orchestration (Arbiter)

The voice arbiter (`scripts/voice_arbiter.py`) manages turn-taking for multi-agent TTS. It superseded the original queue daemon with per-pane queues, 5 voice modes, and a GateEngine.

**Services**: `systemctl --user {start,stop,restart} voice-arbiter.service`

**Sockets**:
- `~/.claude/local/voice/arbiter.sock` — primary (Unix domain, JSON-over-newline)
- `~/.claude/local/voice/queue.sock` — legacy compat (same protocol)

**Voice Modes**:
- **AMBIENT** — all panes speak, spatial volume mixing active (default)
- **FOCUSED** — only focused pane speaks, others go VIRTUAL (re-promoted on focus change)
- **SOLO** — only one specific pane speaks
- **SILENT** — no TTS output (earcons still play)
- **BROADCAST** — all panes at full volume

**GateEngine** (5 gates, AND logic): TTS gate, Focus gate, STT gate, Priority gate, Cooldown gate.

**Flags**:
- `~/.claude/local/voice/tts-playing` — PID + timestamp of active pw-play process
- `~/.claude/local/voice/stt-active` — PID + timestamp of active STT session (blocks queue)
- `~/.claude/local/voice/speaking-now.json` — Current voice state for other consumers
- `~/.claude/local/voice/mode-state` — Persisted voice mode

**Tmux indicators** (via `@claude_voice` pane option, read by `window-status-format`):
- 🎤 = voice enabled (all panes with active Claude sessions)
- 🌟 = speaking (pane whose TTS is currently playing)
- 💬 = queued (pane with items waiting to play)
- 🔇 = muted (all panes when mute=true)

**Resilience**:
- 300s watchdog kills stuck pw-play
- Ghost state guard clears orphaned current playback
- Startup clears stale tmux indicators and kills orphaned pw-play
- Virtual voice pattern: unfocused messages shelved, not dropped
- Health check reports playback duration, queue stall, stale flags
- `KillMode=mixed` in systemd ensures children die with daemon
- `Conflicts=voice-queue.service` prevents dual-daemon

**Health**: `systemctl --user status voice-arbiter.service`

## Event -> Sound Mapping
| Hook Event | Sound Slot | Description |
|------------|-----------|-------------|
| SessionStart | session_start | New session greeting |
| Stop | task_complete | Task finished (content-aware: detects commits, errors, test passes) |
| Notification | notification | Attention needed |
| SubagentStart | agent_deploy | Subagent dispatched |
| SubagentStop | agent_return | Subagent finished |
| SessionEnd | session_end | Session closing |
| PostToolUseFailure | error | Tool failed |
| UserPromptSubmit | prompt_ack | User sent a prompt (disabled by default) |
| PreCompact | compact | Context compaction starting |
| PermissionRequest | permission | Permission dialog |

## Config Schema
```yaml
theme: default      # Active theme directory name
volume: 0.8         # 0.0 to 1.0
mute: false         # Kill switch
hooks:
  SessionStart: true
  Stop: true
  Notification: true
  SubagentStart: true
  SubagentStop: true
  SessionEnd: true
  PostToolUseFailure: true
  UserPromptSubmit: false   # Disabled by default (too frequent)
  PreCompact: true
  PermissionRequest: true
categories:
  earcon: 1.0
  notification: 1.0
  ambient: 0.3
tts:
  enabled: false     # Enable TTS greetings on SessionStart
  backend: auto      # auto | local | kokoro | piper
  voice: am_onyx     # Kokoro voice preset
  quality: normal    # normal | best
  cache: true        # Cache synthesized audio
  greeting: true     # Speak theme greeting on session start
  response: true     # Speak assistant response on Stop events
  response_max_chars: 15000  # Max chars of prose to speak (code blocks stripped first)
tmux:
  focus_volumes:
    focused: 1.0        # Full volume — this pane is selected
    same_window: 0.5    # Half volume — visible in a split
    same_session: 0.2   # Whisper — one keypress away
    other_session: 0.0  # Silent — fully background
  priority_floors:
    "2": 0.8            # Errors/notifications always audible at 80%
    "1": 0.0            # Normal events follow spatial rules
    "0": 0.0            # Ambient events follow spatial rules
```

## Spatial Volume Mixer (Phase 3.5)

Volume follows cognitive distance. Each pane's spatial relationship to the focused
pane determines a volume multiplier. Priority floors let critical events (errors,
notifications) override spatial silencing.

Pipeline: `effective = max(base × spatial_multiplier, base × priority_floor)`

Set all non-focused to 0.0 for binary muting. Set all to 1.0 to hear everything.

## TTS (Text-to-Speech)

Kokoro-82M provides local GPU TTS. Greetings are pre-cached for instant playback.

```bash
# Pre-generate greetings for all themes
uv run scripts/tts_warmup.py

# List available voices
uv run scripts/tts_warmup.py --list-voices

# Warm specific theme with specific voice
uv run scripts/tts_warmup.py --theme starcraft --voice am_onyx
```

Kokoro env: `~/.local/share/kokoro-env/` (separate venv, ~555MB VRAM)
TTS cache: `~/.claude/local/voice/cache/tts/` (SHA256-keyed WAVs)
