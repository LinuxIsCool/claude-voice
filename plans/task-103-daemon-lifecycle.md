---
title: "Task-103: Daemon Lifecycle Management — Complete Plan"
date: 2026-03-27
author: matt
session: matt:56
task: task-103
tags: [plan, systemd, daemon, lifecycle, tts, queue, reliability]
---

# Task-103: Daemon Lifecycle Management

## Scope

Both voice daemons (TTS + Queue) need systemd user services for:
- Auto-start on login
- Auto-restart on crash
- Auto-restart on code/config change (PathChanged trigger)
- Clean shutdown on logout
- Proper VRAM cleanup (no orphan Kokoro processes)

## Files to Create

```
~/.config/systemd/user/
├── voice-tts.service        # TTS daemon (Kokoro-82M)
├── voice-tts-watch.path     # Watches for TTS code/config changes
├── voice-queue.service      # Voice queue daemon
├── voice-queue-watch.path   # Watches for queue code/config changes
└── voice.target             # Groups both services
```

Plus a setup script:
```
~/.claude/local/scripts/voice-systemd-setup.sh
```

## Implementation

### 1. voice-tts.service

```ini
[Unit]
Description=claude-voice TTS daemon (Kokoro-82M)
After=default.target

[Service]
Type=simple
ExecStart=/home/shawn/.local/share/kokoro-env/bin/python3 \
  /home/shawn/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/tts_daemon.py
ExecStop=/bin/kill -TERM $MAINPID
Restart=on-failure
RestartSec=5
# Give Kokoro 15s to load before systemd considers it failed
TimeoutStartSec=30
# VRAM cleanup: SIGTERM triggers the daemon's _shutdown handler
KillSignal=SIGTERM
KillMode=process
# Environment
Environment=PYTHONUNBUFFERED=1
# Don't restart more than 3 times in 60 seconds (prevents VRAM thrash)
StartLimitIntervalSec=60
StartLimitBurst=3

[Install]
WantedBy=default.target
```

### 2. voice-tts-watch.path

```ini
[Unit]
Description=Watch for TTS daemon code/config changes

[Path]
PathChanged=/home/shawn/.claude/local/voice/config.yaml
PathChanged=/home/shawn/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/tts_daemon.py

[Install]
WantedBy=default.target
```

When either file changes, systemd triggers a restart of `voice-tts.service`.
Need a small bridge service for this:

### 3. voice-tts-restart.service

```ini
[Unit]
Description=Restart TTS daemon on code/config change

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl --user restart voice-tts.service
```

Update `voice-tts-watch.path` to reference this:
```ini
[Path]
PathChanged=/home/shawn/.claude/local/voice/config.yaml
PathChanged=/home/shawn/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/tts_daemon.py
Unit=voice-tts-restart.service
```

### 4. voice-queue.service

```ini
[Unit]
Description=claude-voice queue daemon (turn-taking)
After=default.target

[Service]
Type=simple
WorkingDirectory=/home/shawn/.claude/plugins/local/legion-plugins/plugins/claude-voice
ExecStart=/home/shawn/.local/bin/uv run scripts/voice_queue.py
ExecStop=/bin/kill -TERM $MAINPID
Restart=on-failure
RestartSec=3
TimeoutStartSec=10
KillSignal=SIGTERM
KillMode=process
Environment=PYTHONUNBUFFERED=1
StartLimitIntervalSec=60
StartLimitBurst=5

[Install]
WantedBy=default.target
```

### 5. voice-queue-watch.path

```ini
[Unit]
Description=Watch for queue daemon code/config changes

[Path]
PathChanged=/home/shawn/.claude/local/voice/config.yaml
PathChanged=/home/shawn/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/voice_queue.py
Unit=voice-queue-restart.service

[Install]
WantedBy=default.target
```

### 6. voice-queue-restart.service

```ini
[Unit]
Description=Restart queue daemon on code/config change

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl --user restart voice-queue.service
```

### 7. voice.target

```ini
[Unit]
Description=claude-voice daemon group
Wants=voice-tts.service voice-queue.service voice-tts-watch.path voice-queue-watch.path

[Install]
WantedBy=default.target
```

### 8. Setup Script

`~/.claude/local/scripts/voice-systemd-setup.sh`:

```bash
#!/bin/bash
# Install and enable claude-voice systemd user services.
# Run once. Idempotent.
set -euo pipefail

UNIT_DIR="$HOME/.config/systemd/user"
VOICE_DIR="$HOME/.claude/plugins/local/legion-plugins/plugins/claude-voice"
mkdir -p "$UNIT_DIR"

# Stop manually-started daemons
kill "$(cat ~/.claude/local/voice/daemon.pid 2>/dev/null)" 2>/dev/null || true
kill "$(cat ~/.claude/local/voice/queue.pid 2>/dev/null)" 2>/dev/null || true
sleep 1

# Write unit files
# (each cat heredoc writes one file)

cat > "$UNIT_DIR/voice-tts.service" << 'EOF'
[Unit]
Description=claude-voice TTS daemon (Kokoro-82M)
After=default.target

[Service]
Type=simple
ExecStart=/home/shawn/.local/share/kokoro-env/bin/python3 \
  /home/shawn/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/tts_daemon.py
Restart=on-failure
RestartSec=5
TimeoutStartSec=30
KillSignal=SIGTERM
KillMode=process
Environment=PYTHONUNBUFFERED=1
StartLimitIntervalSec=60
StartLimitBurst=3

[Install]
WantedBy=default.target
EOF

cat > "$UNIT_DIR/voice-tts-restart.service" << 'EOF'
[Unit]
Description=Restart TTS daemon on code/config change

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl --user restart voice-tts.service
EOF

cat > "$UNIT_DIR/voice-tts-watch.path" << 'EOF'
[Unit]
Description=Watch for TTS daemon code/config changes

[Path]
PathChanged=/home/shawn/.claude/local/voice/config.yaml
PathChanged=/home/shawn/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/tts_daemon.py
Unit=voice-tts-restart.service

[Install]
WantedBy=default.target
EOF

cat > "$UNIT_DIR/voice-queue.service" << 'EOF'
[Unit]
Description=claude-voice queue daemon (turn-taking)
After=default.target

[Service]
Type=simple
WorkingDirectory=/home/shawn/.claude/plugins/local/legion-plugins/plugins/claude-voice
ExecStart=/home/shawn/.local/bin/uv run scripts/voice_queue.py
Restart=on-failure
RestartSec=3
TimeoutStartSec=10
KillSignal=SIGTERM
KillMode=process
Environment=PYTHONUNBUFFERED=1
StartLimitIntervalSec=60
StartLimitBurst=5

[Install]
WantedBy=default.target
EOF

cat > "$UNIT_DIR/voice-queue-restart.service" << 'EOF'
[Unit]
Description=Restart queue daemon on code/config change

[Service]
Type=oneshot
ExecStart=/usr/bin/systemctl --user restart voice-queue.service
EOF

cat > "$UNIT_DIR/voice-queue-watch.path" << 'EOF'
[Unit]
Description=Watch for queue daemon code/config changes

[Path]
PathChanged=/home/shawn/.claude/local/voice/config.yaml
PathChanged=/home/shawn/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/voice_queue.py
Unit=voice-queue-restart.service

[Install]
WantedBy=default.target
EOF

cat > "$UNIT_DIR/voice.target" << 'EOF'
[Unit]
Description=claude-voice daemon group
Wants=voice-tts.service voice-queue.service voice-tts-watch.path voice-queue-watch.path

[Install]
WantedBy=default.target
EOF

# Reload and enable
systemctl --user daemon-reload
systemctl --user enable --now voice.target

echo "claude-voice systemd services installed and started"
echo "  voice-tts.service: $(systemctl --user is-active voice-tts.service)"
echo "  voice-queue.service: $(systemctl --user is-active voice-queue.service)"
echo "  voice-tts-watch.path: $(systemctl --user is-active voice-tts-watch.path)"
echo "  voice-queue-watch.path: $(systemctl --user is-active voice-queue-watch.path)"
```

## Implementation Steps

1. **Create setup script** at `~/.claude/local/scripts/voice-systemd-setup.sh`
2. **Review with Shawn** — the script needs to be run manually (writes to systemd dirs, kills running daemons)
3. **Run setup** — `bash ~/.claude/local/scripts/voice-systemd-setup.sh`
4. **Verify** — `systemctl --user status voice.target`
5. **Test auto-restart** — edit config.yaml, verify daemon restarts within 10s
6. **Test crash recovery** — `kill -9 $(cat ~/.claude/local/voice/daemon.pid)`, verify systemd restarts it
7. **Test code change** — edit tts_daemon.py, verify restart
8. **Remove manual daemon startup** from any hooks or scripts
9. **Update CLAUDE.md** with systemd management commands
10. **Commit** plan + setup script

## Testing Plan

| Test | Command | Expected |
|------|---------|----------|
| Services running | `systemctl --user status voice.target` | active |
| TTS daemon alive | `systemctl --user status voice-tts.service` | active (running) |
| Queue daemon alive | `systemctl --user status voice-queue.service` | active (running) |
| Config change restarts TTS | `touch ~/.claude/local/voice/config.yaml` then wait 10s | TTS daemon PID changes |
| Code change restarts TTS | `touch scripts/tts_daemon.py` then wait 10s | TTS daemon PID changes |
| Crash recovery TTS | `kill -9 $(cat ~/.claude/local/voice/daemon.pid)` | Restarts in 5s |
| Crash recovery queue | `kill -9 $(cat ~/.claude/local/voice/queue.pid)` | Restarts in 3s |
| No VRAM leak | `nvidia-smi` after restart | Kokoro VRAM stable at ~555MB |
| Startup limit | Kill 4 times in 60s | systemd stops trying (burst=3) |
| Logs | `journalctl --user -u voice-tts.service -f` | Shows daemon output |

## Risks

| Risk | Mitigation |
|------|------------|
| systemd user session not enabled | `loginctl enable-linger shawn` |
| uv not in PATH for systemd | Full path `/home/shawn/.local/bin/uv` in ExecStart |
| Kokoro VRAM not freed on kill | Daemon's _shutdown handler does cleanup; SIGTERM gives it time |
| PathChanged fires too often (editor auto-save) | StartLimitBurst=3 per 60s prevents thrash |
| PID file conflicts with systemd | Daemon already writes PID; systemd tracks MAINPID independently |

## What This Unlocks

- **No more manual daemon starts** — services start on login
- **No more lost speech during restarts** — systemd restarts in 3-5 seconds
- **No more stale code** — PathChanged triggers restart on any source/config change
- **Crash resilience** — auto-recovery without human intervention
- **Proper logging** — `journalctl --user -u voice-tts.service` for daemon output
- **Clean shutdown** — SIGTERM on logout, VRAM freed

## Effort

30 minutes. The setup script is the deliverable. Everything else is verification.
