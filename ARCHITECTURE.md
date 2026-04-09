---
title: "claude-voice — System Architecture"
status: current
created: 2026-03-26
updated: 2026-04-09
author: legion
tags: [claude-voice, architecture]
---

# claude-voice — System Architecture

Audio sensory layer for Claude Code. Plays themed earcons on hook events, speaks assistant responses via TTS, and coordinates multi-agent voice through a spatial volume mixer. The terminal wastes a sensory channel — this plugin fills it.

## Architecture Overview

Two playback paths, three daemons, file-as-IPC:

```
Hook event (stdin JSON)
    |
    v
voice_event.py          (entry point: parse, route, print {}, exit 0)
    |
    v
router.py: route_event()
    |
    ├── [Earcon path — fire-and-forget, <150ms]
    |   theme.py (load theme + resolve sound variant)
    |   _get_focus_state() → focus-state file or tmux subprocess
    |   _effective_volume() → spatial mixer + priority floors
    |   STT suppression check (stt-active flag)
    |   audio.py play_sound() → pw-play Popen(start_new_session=True)
    |   agents.py → per-persona RTS sounds (SubagentStart/Stop)
    |   ambient.py → background loop management
    |
    └── [TTS path — queued through arbiter]
        tts.py extract_speakable() → strip code, compress
        agents.py get_agent_voice() → persona-specific voice
        speak_via_daemon() → daemon.sock → Kokoro-82M synthesis
        queue_client.enqueue_speech() → arbiter.sock
        voice_arbiter.py → GateEngine → per-pane priority queue → pw-play
```

## Module Inventory

All modules live in `lib/`. Zero external Python dependencies in the hook path.

| Module | Lines | Purpose |
|--------|-------|---------|
| `router.py` | 367 | Event routing, spatial mixer, content-aware Stop parsing, TTS dispatch |
| `audio.py` | 106 | pw-play wrapper with fallback chain (pw-play → paplay → aplay → mpv) |
| `theme.py` | 154 | Theme loader, deep-merge inheritance, variant resolution |
| `state.py` | 240 | Config management, hand-written YAML parser (no pyyaml dep), atomic writes |
| `tts.py` | 386 | Kokoro-82M synthesis, dual-tier (daemon socket + subprocess fallback), SHA256 cache |
| `volume.py` | 127 | 5-stage gain chain: category × agent × policy × master × system_gain |
| `agents.py` | 75 | RTS-model agent sound profiles (select, acknowledge, complete, error per persona) |
| `ambient.py` | 113 | Background loop management (start on SubagentStart, stop on last SubagentStop) |
| `queue_client.py` | 102 | Enqueue speech to arbiter daemon, direct fallback if daemon unavailable |
| `flags.py` | 84 | PID+timestamp flag files with staleness detection |
| `logger.py` | 233 | JSONL + SQLite dual-layer event logging |
| `constants.py` | 157 | Single source of truth for all paths, defaults, and magic numbers |
| `presets.py` | 109 | Named focus presets (focus-only, spatial, hear-all, meeting, restore) |
| `utils.py` | 34 | `deep_merge()` and `cache_key()` |
| `mic.py` | 119 | MicCapture class (sounddevice ring buffer) — Phase 4 scaffold |
| `wake.py` | 111 | WakeWordDetector (openWakeWord + custom ONNX) — Phase 4 scaffold |
| `stt.py` | 234 | SileroVADWrapper + STTEngine (Parakeet + faster-whisper) — Phase 4 scaffold |
| `duplex.py` | 92 | DuplexManager (barge-in: TTS cancel → STT activate) — Phase 4 scaffold |
| `ptt.py` | 50 | PushToTalk (key-hold alternative to wake word) — Phase 4 scaffold |
| `__init__.py` | 1 | Package marker |

## Daemon Topology

Three persistent daemons managed by systemd user units:

| Daemon | Script | Socket | Service | Purpose |
|--------|--------|--------|---------|---------|
| **TTS** | `scripts/tts_daemon.py` | `daemon.sock` | `voice-tts.service` | Keeps Kokoro-82M warm in GPU VRAM (~555MB). Serves synthesis requests over Unix socket. |
| **Arbiter** | `scripts/voice_arbiter.py` | `arbiter.sock` + `queue.sock` | `voice-arbiter.service` | Voice orchestrator. 5 modes, per-pane priority queues, GateEngine, virtual voice pattern. |
| **STT** | `scripts/stt_daemon.py` | file-based | `voice-stt.service` | Wake word + VAD + transcription. Writes `last-transcript.txt`. |

Umbrella: `voice.target` starts all three. Health: `voice-health.timer` (periodic check with self-healing).

The arbiter superseded the original `voice_queue.py` (synchronous poll loop, global heap). The arbiter listens on both `arbiter.sock` and legacy `queue.sock` for backward compatibility. systemd `Conflicts=voice-queue.service` ensures they can't coexist.

### Arbiter Voice Modes

| Mode | Behavior |
|------|----------|
| **AMBIENT** | All panes can speak. Spatial volume mixing active. Default mode. |
| **FOCUSED** | Only the focused pane speaks. Others go VIRTUAL (re-promoted on focus change). |
| **SOLO** | Only one specific pane speaks. |
| **SILENT** | No TTS output. Earcons still play. |
| **BROADCAST** | All panes at full volume, ignoring spatial mixing. |

### GateEngine

The arbiter evaluates 5 gates (AND logic) before playing each queued message:

1. **TTS gate** — is something already playing?
2. **Focus gate** — is this pane focused? (FOCUSED/SOLO modes only)
3. **STT gate** — is the user speaking? (always blocks)
4. **Priority gate** — does this message meet the priority threshold?
5. **Cooldown gate** — speaker transition delay (300ms between different agents)

## Latency Budget

| Phase | Time | Notes |
|-------|------|-------|
| Stdin parse | 5ms | `json.loads()` in `voice_event.py` |
| Theme load (cached) | 10ms | In-process dict |
| Sound resolve | 5ms | `random.choice()` from variants |
| Focus state (file hit) | 0.1ms | Read `focus-state` file |
| Focus state (subprocess) | 5ms | `tmux display-message` fallback |
| Volume calculation | <1ms | Pure arithmetic |
| Popen launch | 15ms | `subprocess.Popen` for pw-play |
| Log (JSONL) | <1ms | fcntl-locked append |
| Log (SQLite) | async | `threading.Thread(daemon=True)` |
| **Total hook wall time** | **~40ms** | |
| pw-play audio start | +30-50ms | Async, not blocking hook |
| **User hears sound** | **~70-90ms** | Well within 150ms budget |

## State Files (File-as-IPC)

The filesystem is the IPC bus (ADR-002). Every process can read any flag without coupling to its writer.

| File | Semantics | Writer | Reader |
|------|-----------|--------|--------|
| `config.yaml` | Persistent user config | `/voice` skill, manual edit | every module |
| `focus-state` | Focused pane ID | `tmux_focus_hook.sh` | `router.py` (fast path) |
| `stt-active` | User is speaking — suppress audio | `stt_daemon.py` | `router.py`, arbiter |
| `tts-playing` | System is speaking — suppress wake word | arbiter | `stt_daemon.py` |
| `ambient.pid` | PID of ambient pw-play loop | `ambient.py` | `ambient.py` |
| `ambient-count` | Number of active subagents | `ambient.py` | `ambient.py` |
| `speaking-now.json` | Current arbiter state (mode, pane, agent) | arbiter | statusline, external |
| `mode-state` | Persisted voice mode | arbiter | arbiter on restart |
| `daemon.sock` | TTS synthesis IPC | `tts_daemon.py` | `tts.py` |
| `arbiter.sock` | Voice scheduling IPC (primary) | `voice_arbiter.py` | `queue_client.py` |
| `queue.sock` | Voice scheduling IPC (legacy compat) | `voice_arbiter.py` | `queue_client.py` |
| `voice.db` | Event analytics (SQLite WAL) | `logger.py` | queries |
| `events/YYYY-MM.jsonl` | Event time series | `logger.py` | KOI sensor |
| `last-transcript.txt` | Last STT result | `stt_daemon.py` | Claude Code input |
| `cache/tts/*.wav` | Cached TTS audio (SHA256-keyed) | `tts.py`, `tts_daemon.py` | playback |

All state lives under `~/.claude/local/voice/`.

## Spatial Volume Mixer

Volume follows cognitive distance. Each pane's relationship to the focused pane determines a volume multiplier. Priority floors let critical events override spatial silencing.

```
effective = max(base × spatial_multiplier, base × priority_floor)
```

Default spatial multipliers:
- `focused: 1.0` — full volume
- `same_window: 0.5` — visible in a split
- `same_session: 0.2` — one keypress away
- `other_session: 0.0` — fully background
- `no_tmux: 1.0` — not in tmux

Priority floors (errors always audible from background):
- Priority 2 (errors/notifications): 0.8
- Priority 1 (normal): 0.0
- Priority 0 (ambient): 0.0

## Theme Engine

7 themes with deep-merge inheritance. Game themes override only what differs from `default`.

| Theme | Sonic DNA | Accent |
|-------|-----------|--------|
| `default` | Clean sine/triangle, professional | `#a6e3a1` |
| `starcraft` | Square waves, radio chirps, digital military | `#00ff41` |
| `warcraft` | War drums, horn brass, fantasy organic | `#ff4444` |
| `mario` | Bright chiptune, bouncy pulse waves | `#ff0000` |
| `zelda` | Harp arpeggios, ocarina, crystalline bells | `#00cc66` |
| `smash` | Sharp transients, impacts, arena energy | `#ffaa00` |
| `kingdom-hearts` | Piano, choir, strings, orchestral | `#6699ff` |

Each theme: `theme.json` (mappings) + `sounds/` (WAVs, 48kHz 16-bit stereo, 3 variants per event). All WAVs synthesized from numpy/scipy via `scripts/generate_sounds.py`.

Content-aware sound routing: Stop events check `last_assistant_message` against regex patterns (git commit → commit sound, tests passed → task_complete, error → error sound).

## Design Decisions

| ADR | Decision | Rationale |
|-----|----------|-----------|
| [ADR-001](design/decisions/001-spatial-mixer-over-binary-gate.md) | Spatial volume mixer over binary gate | Continuous mixer is a strict generalization. One question broke the binary model. |
| [ADR-002](design/decisions/002-file-as-ipc.md) | File flags as IPC | Debuggable (`ls` shows all state), decoupled, zero-dependency. |
| [ADR-003](design/decisions/003-queue-only-tts.md) | Queue only TTS, fire-and-forget earcons | Earcon overlap sounds natural; TTS overlap is cacophony. Protects 150ms budget. |
| [ADR-004](design/decisions/004-fail-open-everywhere.md) | Fail open everywhere (except STT suppression) | Playing audio over the mic is catastrophically worse than silence. |

## See Also

- `design/` — Detailed architecture docs (state machine, volume pipeline, daemon topology, interaction map)
- `design/decisions/` — Architecture Decision Records (ADR-001 through ADR-004)
- `design/known-issues/` — Tracked issues (P0 through P2)
- `specs/` — Per-subsystem specifications (01 through 18)
- `ROADMAP2.md` — Active development roadmap
- `CLAUDE.md` — Quick reference for working with the plugin
