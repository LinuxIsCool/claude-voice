---
title: "claude-voice — System Interaction Map"
created: 2026-03-30
updated: 2026-03-30
author: matt
status: verified
tags: [claude-voice, interactions, legion, systems-thinking]
note: >
  This map captures known interactions as of 2026-03-30. The voice plugin
  touches nearly every other system in Legion. Future sessions will discover
  interactions not listed here. This is a living document.
---

# claude-voice — System Interaction Map

## Interaction Diagram

```
                             ┌──────────────┐
                             │ Claude Code   │
                             │ (hook system) │
                             └──────┬───────┘
                                    │ 10 hook events
                                    v
┌──────────────┐          ┌──────────────────┐          ┌──────────────┐
│ claude-tmux   │─────────>│   claude-voice    │<─────────│ claude-      │
│               │ focus-   │                  │ PERSONA_  │ personas     │
│ - pane hooks  │ state    │ - earcons        │ SLUG      │              │
│ - @options    │ file     │ - TTS            │ env var   │ - character  │
│ - statusline  │          │ - STT            │           │   YAML       │
│   glyphs      │          │ - spatial mix    │           │ - voice      │
└──────────────┘          │ - queue          │           │   profile    │
                          └────────┬─────────┘           └──────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              v                    v                    v
    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
    │ PipeWire      │    │ KOI / Hippo  │    │ claude-      │
    │               │    │ (knowledge)  │    │ recordings   │
    │ - pw-play     │    │              │    │              │
    │ - AEC source  │    │ - NOT YET    │    │ - shared STT │
    │ - sinks       │    │   CONNECTED  │    │   models     │
    └──────────────┘    └──────────────┘    └──────────────┘
```

## Detailed Interaction Table

### Active Interactions (verified working)

| From | To | Interface | Data | Direction | Notes |
|------|----|-----------|------|-----------|-------|
| Claude Code | voice | Hook events | 10 event types as stdin JSON | -> | Core trigger mechanism |
| voice | PipeWire | `pw-play` subprocess | WAV file path + volume | -> | Fire-and-forget playback |
| claude-personas | voice | `$PERSONA_SLUG` env var | Persona identifier string | -> | Used for agent sounds in router.py |
| voice | voice (queue) | Unix socket (queue.sock) | JSON enqueue requests | -> | TTS scheduled through queue |
| voice | voice (TTS) | Unix socket (daemon.sock) | Synthesis requests | -> | Kokoro-82M warm model |
| voice | filesystem | State flag files | stt-active, tts-playing | <-> | File-as-IPC coordination |
| voice | SQLite | voice.db | Event records | -> | 1,922 events logged |
| voice | JSONL | events/*.jsonl | Full event payloads | -> | Monthly append files |

### Partial Interactions (designed, partially wired)

| From | To | Interface | Status | Gap |
|------|----|-----------|--------|-----|
| tmux | voice | `focus-state` file | Router reads it | No tmux hook writes it |
| tmux | voice | `@claude_audio_sink` pane option | Router reads it | Undocumented, no setter |
| voice | STT daemon | `stt-active` flag | Router checks it | STT not in systemd |
| queue | STT daemon | `tts-playing` flag | Queue writes, STT reads | Queue doesn't read stt-active back |

### Missing Interactions (designed in specs, zero code)

| From | To | Spec | Potential Value | Effort |
|------|----|------|-----------------|--------|
| voice events | KOI | — | 1,922 events searchable, feed briefs/viz | 3hr |
| voice events | Hippo | — | Voice patterns as knowledge graph nodes | 4hr |
| personas | voice | spec 08 | Per-persona voices (not just am_onyx) | 2hr config |
| rhythms | voice | spec 10 | Morning brief spoken aloud | 2hr |
| schedule | voice | — | Auto meeting-mode on calendar events | 1hr |
| statusline | voice | — | Mute/volume/theme indicator in tmux bar | 1hr |
| matrix | voice | — | Inter-agent "I'm about to speak" coordination | 4hr |
| voice events | Letta | — | Episodic memory of voice interactions | 2hr |

## Cross-Cutting Concerns

### Shared Models
- faster-whisper `large-v3-turbo` is used by BOTH claude-voice (STT) and claude-recordings (transcription)
- A warm STT daemon benefits both use cases
- Risk: model loading contention if both try to load simultaneously

### Shared State Directory
- `~/.claude/local/voice/` is the namespace for ALL voice state
- Other plugins should NEVER write here directly — use defined interfaces
- Exception: tmux hook writing `focus-state` (cross-plugin by design)

### Environment Variable Chain
```
Claude Code sets $TMUX_PANE
  -> voice reads for spatial state
Claude-personas sets $PERSONA_SLUG (via CLAUDE_ENV_FILE or session hook)
  -> voice reads for agent sounds
$CLAUDE_VOICE_MUTE, $CLAUDE_VOICE_VOLUME, $CLAUDE_VOICE_THEME
  -> voice reads as overrides to config.yaml
```

## Leverage Analysis

| Intervention | Systems Affected | Estimated Impact |
|-------------|-----------------|------------------|
| Tune spatial mixer defaults | voice, all multi-agent workflows | High (immediate multi-agent usability) |
| Voice events -> KOI | knowledge, rhythms, viz, hippo | Very High (1,922 events become searchable) |
| STT systemd unit | voice, recordings, duplex | High (enables all STT work) |
| Persona-voice mapping | personas, voice, identity | Medium (differentiated agent experience) |
| Queue hold on stt-active | voice, STT, duplex | High (prerequisite for reliable STT) |
| AEC configuration | voice, STT, duplex | Critical (prerequisite for duplex) |
