---
title: "claude-voice — Daemon Topology"
created: 2026-03-30
updated: 2026-03-30
author: matt
status: verified
tags: [claude-voice, daemons, systemd, lifecycle]
note: >
  Describes the three daemons (TTS, queue, STT) and their lifecycle.
  Only TTS and queue are running as systemd services today.
---

# claude-voice — Daemon Topology

## Overview

Voice uses three long-lived daemons, each with a Unix socket for IPC.
The hook process (voice_event.py) is short-lived — one invocation per event.

```
Hook process (transient)          Daemons (long-lived)
┌──────────────────┐
│ voice_event.py   │──────> daemon.sock ──────> TTS daemon (Kokoro GPU)
│ (50ms lifetime)  │──────> queue.sock  ──────> Queue daemon (scheduler)
└──────────────────┘              ^
                                  │
                    STT daemon ───┘ (future: stt.sock)
                    (wake word,      writes stt-active
                     VAD,            reads tts-playing
                     transcription)
```

## TTS Daemon (`voice-tts.service`)

**Purpose**: Keep Kokoro-82M warm in GPU VRAM for instant synthesis.

| Attribute | Value |
|-----------|-------|
| Script | `scripts/tts_daemon.py` |
| Socket | `~/.claude/local/voice/daemon.sock` |
| systemd | `voice-tts.service` |
| Python env | `~/.local/share/kokoro-env/bin/python3` |
| VRAM | ~555MB |
| RAM | ~1.2GB |
| Warm latency | ~90ms |
| Cold latency | ~8s (model load) |
| Protocol | JSON over Unix socket (request/response) |

**Lifecycle**:
1. On start: load Kokoro model into GPU, bind socket
2. On request: synthesize WAV, write to cache, return path
3. On idle: model stays warm (no timeout under systemd)
4. On stop: SIGTERM -> cleanup socket, exit

**Known issue**: Stale `daemon.sock` on crash prevents restart. Fix: unlink on startup.

## Queue Daemon (`voice-queue.service`)

**Purpose**: Ensure only one agent speaks at a time.

| Attribute | Value |
|-----------|-------|
| Script | `scripts/voice_queue.py` |
| Socket | `~/.claude/local/voice/queue.sock` |
| systemd | `voice-queue.service` |
| Python env | System Python (no deps) |
| RAM | ~15MB |
| Poll interval | 50ms |
| Max queue | 50 items |
| Expiration | 30s |
| Protocol | JSON over Unix socket (request/response) |

**Lifecycle**:
1. On start: bind socket, create PID file, acquire flock
2. On enqueue: add to priority heap
3. On advance: pop highest priority, check speaker transition, set tts-playing, spawn pw-play
4. On playback complete: clear tts-playing, advance queue
5. On idle timeout: exit (disabled under systemd)
6. On stop: SIGTERM -> cleanup socket, PID, exit

**Known issues**:
- Does NOT check `stt-active` before dequeueing (P0 bug)
- Does NOT clear `tts-playing` on startup (P0 bug)
- Stale `queue.sock` on crash prevents restart

## STT Daemon (NOT YET DAEMONIZED)

**Purpose**: Wake word detection + speech-to-text transcription.

| Attribute | Value |
|-----------|-------|
| Script | `scripts/stt_daemon.py` |
| Socket | None (writes to file) |
| systemd | **Does not exist** |
| Python env | `~/.local/share/stt-env/bin/python3` (**not created**) |
| Models | openWakeWord, Silero VAD, faster-whisper |
| GPU | Shared with TTS (model loading contention possible) |

**Designed lifecycle** (from Phase 4 plan):
1. On start: load wake word model, initialize VAD, clear stale stt-active
2. Wake word detected: set stt-active flag, begin recording
3. Speech ends (VAD): transcribe via faster-whisper, write transcript, clear stt-active
4. During TTS playback: suppress wake word detection (reads tts-playing flag)
5. On stop: clear stt-active, cleanup

**Dependencies**:
- `stt-env` Python environment (not created)
- Parakeet or faster-whisper model downloaded
- PipeWire AEC configured (not done — critical for duplex)
- Queue daemon must respect stt-active (not done — P0 bug)

## Systemd Configuration

### voice-tts.service
```ini
[Unit]
Description=Voice TTS Daemon (Kokoro-82M)
# Missing: After=pipewire.service (P2 bug)

[Service]
ExecStart=/home/shawn/.local/share/kokoro-env/bin/python3 \
  /home/shawn/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/tts_daemon.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

### voice-queue.service
```ini
[Unit]
Description=Voice Queue Daemon

[Service]
ExecStart=/usr/bin/uv run \
  /home/shawn/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/voice_queue.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

### voice.target (NOT YET CREATED)

```ini
[Unit]
Description=Voice Services
Wants=voice-tts.service voice-queue.service
After=pipewire.service

[Install]
WantedBy=graphical-session.target
```

This target would ensure all voice daemons start on login and survive reboots.

## IPC Protocol

All daemon communication uses the same pattern:

```
Client:  JSON + newline -> Unix socket
Server:  JSON + newline -> response
Close:   Client closes connection
```

Example TTS request:
```json
{"type": "synthesize", "text": "Hello", "voice": "am_onyx", "volume": 0.8}
```

Example queue request:
```json
{"type": "enqueue", "wav_path": "/path/to/file.wav", "priority": 1, "agent_id": "matt", "volume": 0.8}
```

Example status request:
```json
{"type": "status"}
```
