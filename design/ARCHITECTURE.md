---
title: "claude-voice — System Architecture (Verified)"
created: 2026-03-30
updated: 2026-03-30
author: matt
status: verified
audit: 2026-03-30 (5-agent parallel research audit, 10 reports)
tags: [claude-voice, architecture, verified]
---

# claude-voice — System Architecture

> This document describes what IS built and running as of 2026-03-30.
> Aspirational features are explicitly marked. See `specs/` for future work.

## 1. Purpose

claude-voice transforms the terminal from a silent text interface into an audio-rich
workspace. It exists because coding in silence wastes a sensory channel — audio
feedback creates subconscious associations between sounds and system states. The
system progresses through layers: earcon beeps (Phase 1-2) -> TTS speech (Phase 3)
-> spatial mixing (Phase 3.5) -> speech-to-text input (Phase 4) -> full duplex
conversation (future).

## 2. System Diagram (Verified)

```
                        CLAUDE CODE HOOKS (10 event types)
                                    |
                                    v
                        +-----------------------+
                        |   voice_event.py      |    Entry point
                        |   (PEP 723, uv run)   |    Always exits 0
                        |   stdin JSON -> parse  |    Always prints {}
                        +-----------------------+
                                    |
                                    v
                        +-----------------------+
                        |     router.py         |    Core routing logic
                        |  1. Mute check        |    344 lines
                        |  2. Per-hook enable    |
                        |  3. Theme load         |
                        |  4. Sound resolve      |
                        |  5. Volume pipeline    |
                        |  6. Spatial mixing     |
                        |  7. STT suppression    |
                        |  8. Earcon playback    |
                        |  9. Agent sounds       |
                        | 10. Ambient engine     |
                        | 11. TTS routing        |
                        | 12. Event logging      |
                        | 13. Health heartbeat   |
                        +-----------------------+
                           |        |        |
              +------------+        |        +-----------+
              v                     v                    v
    +----------------+    +------------------+    +--------------+
    | audio.py       |    | queue_client.py  |    | logger.py    |
    | pw-play Popen  |    | Unix socket IPC  |    | JSONL+SQLite |
    | fire-and-forget|    | to queue daemon  |    | dual-layer   |
    | 105 lines      |    | 75 lines         |    | 233 lines    |
    +----------------+    +------------------+    +--------------+
              |                     |
              |                     v
              |           +------------------+
              |           | voice_queue.py   |    systemd: voice-queue.service
              |           | Priority heap    |    437 lines
              |           | Speaker trans.   |    Unix socket server
              |           | Expiration       |    50ms poll loop
              |           | tts-playing flag |
              |           +------------------+
              |                     |
              v                     v
         +---------+          +---------+
         | pw-play |          | pw-play |    PipeWire native playback
         | earcons |          | TTS wav |    48kHz 16-bit stereo
         +---------+          +---------+

    SEPARATE DAEMON:
    +------------------+
    | tts_daemon.py    |    systemd: voice-tts.service
    | Kokoro-82M GPU   |    387 lines
    | Unix socket      |    Warm model in VRAM
    | WAV synthesis    |    ~90ms warm, ~8s cold
    | Cache to disk    |
    +------------------+

    PHASE 4 (written, not daemonized):
    +------------------+
    | stt_daemon.py    |    NO systemd unit yet
    | openWakeWord     |    299 lines
    | Silero VAD       |    "hey jarvis" wake word
    | faster-whisper   |    stt-active flag
    +------------------+
```

## 3. Component Inventory (Verified)

### lib/ — Runtime Library (2,446 lines total)

| Module | Lines | Purpose | Status |
|--------|-------|---------|--------|
| `router.py` | 344 | Core event routing, volume pipeline, spatial mixing, TTS dispatch | **Live** |
| `tts.py` | 374 | Kokoro-82M synthesis, cache management, daemon IPC, speakable extraction | **Live** |
| `state.py` | 237 | Config loading with defaults merge, atomic writes, minimal YAML parser | **Live** |
| `stt.py` | 234 | faster-whisper transcription, Parakeet Tier 1 (designed), model management | **Written, untested** |
| `logger.py` | 233 | Dual JSONL+SQLite event logging, async fork writes | **Live** |
| `theme.py` | 154 | Theme loader, deep merge inheritance, sound resolution, variant selection | **Live** |
| `mic.py` | 119 | PipeWire microphone capture via sounddevice, AEC source detection | **Written, untested** |
| `wake.py` | 111 | openWakeWord detection, custom ONNX model loading, cooldown | **Written, tested manually** |
| `ambient.py` | 105 | Background ambient loop, agent count tracking, pw-play --loop | **Live (buggy — state drift)** |
| `audio.py` | 105 | pw-play wrapper, fallback chain, volume control, sink routing | **Live** |
| `constants.py` | 103 | All shared paths, timeouts, defaults — single source of truth | **Live** |
| `duplex.py` | 92 | Barge-in manager, TTS cancellation, stt-active coordination | **Written, never instantiated** |
| `agents.py` | 75 | Per-persona sound profile resolution, agent sound mapping | **Live** |
| `queue_client.py` | 75 | Unix socket client for voice queue daemon | **Live** |
| `ptt.py` | 50 | Push-to-talk keybinding (stub) | **Stub** |
| `utils.py` | 34 | deep_merge helper, cache_key generation | **Live** |
| `__init__.py` | 1 | Package marker | — |

### scripts/ — Daemons and Tools (3,657 lines total)

| Script | Lines | Purpose | Status |
|--------|-------|---------|--------|
| `generate_sounds.py` | 1,994 | numpy/scipy procedural sound synthesis for all 7 themes | **Complete** |
| `voice_queue.py` | 437 | Voice queue daemon — priority scheduling, turn-taking | **Running (systemd)** |
| `tts_daemon.py` | 387 | Kokoro-82M TTS daemon — warm model, Unix socket | **Running (systemd)** |
| `stt_daemon.py` | 299 | STT daemon — wake word + VAD + transcription | **Written, no systemd** |
| `play_test.py` | 207 | Manual sound testing tool | **Working** |
| `tts_warmup.py` | 194 | Pre-generate TTS greetings for all themes | **Working** |
| `generate_agent_sounds.py` | 139 | Generate per-persona sound profiles | **Working** |

### assets/ — Sound Files

| Directory | Contents | Count |
|-----------|----------|-------|
| `assets/themes/default/` | Clean professional sine/triangle earcons | 34 WAVs |
| `assets/themes/starcraft/` | Digital military square waves | ~40 WAVs |
| `assets/themes/warcraft/` | Fantasy organic drums/brass | ~40 WAVs |
| `assets/themes/mario/` | Cheerful chiptune | ~40 WAVs |
| `assets/themes/zelda/` | Mystical melodic harp/ocarina | ~40 WAVs |
| `assets/themes/smash/` | Competitive punchy impacts | ~40 WAVs |
| `assets/themes/kingdom-hearts/` | Orchestral emotional piano/choir | ~40 WAVs |
| **Total** | 7 themes, 10 events each, 3-7 variants | ~259 WAVs |

## 4. Data Flow — What Happens When a Hook Fires

### Earcon Path (40-80ms total)

```
Hook fires -> voice_event.py reads stdin JSON (5ms)
           -> router.py: mute check, config load (5ms)
           -> theme.py: resolve sound file + random variant (5ms)
           -> router.py: volume pipeline (spatial × category × master) (1ms)
           -> router.py: STT suppression check (0.1ms)
           -> audio.py: subprocess.Popen(["pw-play", "--volume=X", wav]) (10ms)
           -> pw-play: first audio frame reaches speaker (30ms async)
Total: ~56ms hook wall time, ~80ms to ear
```

### TTS Path (90-700ms, via queue)

```
Hook fires (Stop event) -> router.py extracts speakable text
                        -> tts.py: speak_via_daemon() sends to tts_daemon.py via Unix socket
                        -> tts_daemon.py: Kokoro-82M synthesizes WAV (90ms warm, 700ms+ long text)
                        -> tts_daemon.py: writes to cache, returns path
                        -> queue_client.py: enqueues WAV to voice_queue.py via Unix socket
                        -> voice_queue.py: waits for current playback to finish
                        -> voice_queue.py: sets tts-playing flag, spawns pw-play
                        -> pw-play: audio output
```

## 5. Volume Pipeline (Complete Trace)

```python
# 1. Master volume (config or env override)
master = float(config.get("volume", 0.8))        # 0.0 - 1.0

# 2. Category volume (earcon, notification, ambient)
category = config["categories"].get(sound_category, 1.0)  # 0.0 - 1.0

# 3. Base volume
base = clamp(master * category, 0.0, 1.0)

# 4. Spatial multiplier (tmux focus state)
focus_state = _get_focus_state()                  # "focused" | "same_window" | ...
spatial_mult = config["tmux"]["focus_volumes"][focus_state]  # 0.0 - 1.0
spatial_vol = base * spatial_mult

# 5. Priority floor (errors override spatial silencing)
priority = theme["semantic_sounds"][sound]["priority"]  # 0, 1, or 2
floor_mult = config["tmux"]["priority_floors"][str(priority)]
floor_vol = base * floor_mult

# 6. Effective volume
effective = clamp(max(spatial_vol, floor_vol), 0.0, 1.0)

# 7. STT suppression (absolute override)
if STT_ACTIVE_PATH.exists():
    effective = 0.0

# 8. Play at effective volume (or skip if 0.0)
if effective > 0.0:
    play_sound(wav, volume=effective)
```

## 6. State Files

All state is coordinated via files in `~/.claude/local/voice/`.

| File | Created By | Read By | Lifecycle | Stale Risk |
|------|-----------|---------|-----------|------------|
| `config.yaml` | User / skill commands | router.py (every event) | Persistent | None |
| `focus-state` | tmux hook (NOT YET INSTALLED) | router.py Tier 1 cache | Per focus change | Medium — no writer yet |
| `stt-active` | stt_daemon.py / duplex.py | router.py, queue daemon (NOT YET) | Per speech session | **HIGH — daemon crash = permanent mute** |
| `tts-playing` | voice_queue.py (on play) | stt_daemon.py (wake suppression) | Per TTS utterance | **HIGH — daemon crash = permanent suppression** |
| `daemon.sock` | tts_daemon.py | tts.py speak_via_daemon() | Daemon lifetime | High — crash leaves stale socket |
| `queue.sock` | voice_queue.py | queue_client.py | Daemon lifetime | High — crash leaves stale socket |
| `queue.pid` | voice_queue.py | voice_queue.py --stop | Daemon lifetime | Medium — stale PID |
| `queue.log` | voice_queue.py | Human debugging | Append-only | None |
| `voice.db` | logger.py | Analytics queries | Persistent | None |
| `events/YYYY-MM.jsonl` | logger.py | Analytics / KOI (future) | Append-only monthly | None |
| `cache/tts/*.wav` | tts_daemon.py | router.py (greeting), queue | Persistent, no eviction yet | Low |
| `ambient-count` | ambient.py | ambient.py | Session lifetime | **LIVE BUG — drift** |
| `ambient.pid` | ambient.py | ambient.py | Session lifetime | **LIVE BUG — dead process** |

## 7. Daemon Topology

```
systemd user session
  |
  +-- voice-tts.service (Kokoro-82M, daemon.sock)
  |     Restart=on-failure, RestartSec=5
  |     VRAM: ~555MB (Kokoro model)
  |     RAM: ~1.2GB total
  |
  +-- voice-queue.service (scheduler, queue.sock)
  |     Restart=on-failure, RestartSec=5
  |     RAM: ~15MB
  |
  +-- voice-stt.service (NOT YET CREATED)
        Would manage: wake word, VAD, transcription
        Dependencies: voice-queue.service (for queue hold)
```

## 8. Integration Map (Verified)

| Plugin | Direction | Interface | What's Exchanged | Status |
|--------|-----------|-----------|-----------------|--------|
| **claude-tmux** | tmux -> voice | `focus-state` file, `$TMUX_PANE` env | Focused pane ID for spatial mixing | **Partial** — router reads file, no writer hook installed |
| **claude-tmux** | voice -> tmux | `@claude_audio_sink` pane option | Per-pane audio routing | **Wired but undocumented** |
| **claude-statusline** | voice -> statusline | None currently | Nothing | **Gap** — statusline doesn't show voice state |
| **claude-personas** | personas -> voice | `$PERSONA_SLUG` env var | Current persona for agent sounds | **Live but fragile** |
| **claude-logging** | voice -> logging | Shared DB pattern | Voice events in voice.db (separate from logging.db) | **Live but siloed** |
| **KOI** | voice -> KOI | None | Nothing | **Gap** — 1,922 events not in KOI |
| **claude-hippo** | voice -> hippo | None | Nothing | **Gap** |
| **claude-rhythms** | rhythms -> voice | None | Nothing | **Gap** — briefs not narrated |
| **claude-matrix** | matrix -> voice | None | No inter-agent coordination | **Gap** |
| **claude-schedule** | schedule -> voice | None | No meeting-mode auto-trigger | **Gap** |

## 9. Spec Index (Corrected)

| # | Title | Phase | Status | Notes |
|---|-------|-------|--------|-------|
| 01 | Plugin Scaffold | 1 | **Implemented** | Spec says draft — wrong |
| 02 | Theme Engine | 2 | **Implemented** | Spec says draft — wrong |
| 03 | Sound Synthesis | 2 | **Implemented** | 1,994-line generator |
| 04 | Hook Architecture | 1 | **Implemented** | 10 events wired |
| 05 | Audio Playback | 1 | **Implemented** (subset) | No debounce/concurrency modes |
| 06 | TTS Engine | 3 | **Implemented** | Kokoro-82M via daemon |
| 07 | STT Engine | 4 | **Stale** | Says faster-whisper; code uses Parakeet Tier 1 |
| 08 | Identity & Personality | — | **Designed only** | Zero code written |
| 09 | Gamification | — | **Designed only** | Zero code written |
| 10 | Rhythms Integration | — | **Designed only** | Zero code written |
| 11 | Asset Management | — | **Designed only** | Zero code written |
| 12 | Quality & Testing | — | **Designed only** | 49 tests exist but not per spec |
| 13 | ElevenLabs Deep | — | **Designed only** | Full API spec, zero HTTP calls |
| 14 | Speech-to-Reality | — | **Aspirational** | End-to-end vision doc |
| 15 | Spatial Volume Mixer | 3.5 | **Implemented** | Spec matched code |
| 18 | Voice Queue Daemon | 3.5 | **Implemented** | Running as systemd service |

Missing spec numbers: 16 (wake word training), 17 (tmux focus hook), 19 (duplex).

## 10. Design Principles (Verified in Code)

| Principle | Evidence |
|-----------|----------|
| **Hooks never crash** | `try/except Exception: pass` in voice_event.py main(), router.py route_event(), and every subsystem call |
| **Fire-and-forget playback** | All `subprocess.Popen` use `start_new_session=True` (verified 4 call sites) |
| **Fail-open** | No TMUX_PANE -> full volume. No config -> defaults. No daemon -> direct play. No theme -> skip |
| **Theme as single source of truth** | `theme.json` defines all sound mappings. No hardcoded sound paths |
| **Sub-150ms latency** | Measured median 26ms hook time. One outlier at 1,384ms (uv cold-start) |
| **Zero external deps in hook path** | voice_event.py has `dependencies = []`. All imports are from lib/ |
| **Dual-layer logging** | JSONL for time-series append, SQLite for queries. Both capture full payload |
| **Configurable, not coded** | Volume, theme, hooks, spatial mix, TTS — all in config.yaml |

## 11. Technology Stack (Actual)

| Layer | Technology | Notes |
|-------|-----------|-------|
| Playback | `pw-play` (PipeWire 1.6.2) | Native client, 30ms to first frame |
| Audio format | WAV 48kHz 16-bit | Matches PipeWire native spec — zero resampling |
| Hook runtime | Python 3.11+ via `uv run` | PEP 723 single-file scripts |
| TTS (local) | Kokoro-82M | Apache 2.0, 82M params, RTX 4070, ~90ms warm |
| TTS (cloud) | ElevenLabs | **Not yet integrated** (spec 13 only) |
| TTS fallback | Piper | **Not yet integrated** |
| STT | faster-whisper large-v3-turbo | In `whisperx-env`, CTranslate2 on GPU |
| STT Tier 1 | Parakeet-TDT-1.1B-v2 | **Referenced in ROADMAP2, not in code** |
| Wake word | openWakeWord | "hey_jarvis" default, custom ONNX planned |
| VAD | Silero VAD | Via torch, in stt_daemon.py |
| Config | Custom minimal YAML parser | No pyyaml dependency in hot path |
| IPC | Unix domain sockets + file flags | daemon.sock, queue.sock, state files |
| Logging | SQLite WAL + JSONL | voice.db + events/*.jsonl |
| Sound synthesis | numpy + scipy | 1,994-line generator, run offline |
| Daemon management | systemd user units | voice-tts.service, voice-queue.service |
