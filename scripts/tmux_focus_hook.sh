#!/usr/bin/env bash
# tmux focus hook — writes focused pane ID and notifies the arbiter.
#
# Install in tmux.conf:
#   set-hook -g after-select-pane 'run-shell "~/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/tmux_focus_hook.sh"'
#   set-hook -g after-select-window 'run-shell "~/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/tmux_focus_hook.sh"'
#
# This updates the focus-state file that the arbiter polls (50ms).
# Also sends a focus IPC message to the arbiter for immediate response.

VOICE_DIR="$HOME/.claude/local/voice"
FOCUS_FILE="$VOICE_DIR/focus-state"
ARBITER_SOCK="$VOICE_DIR/arbiter.sock"
LEGACY_SOCK="$VOICE_DIR/queue.sock"

# Get the active pane ID
PANE_ID=$(tmux display-message -p '#{pane_id}' 2>/dev/null)
[ -z "$PANE_ID" ] && exit 0

# Atomic write to focus-state file (write tmp + mv to avoid TOCTOU)
mkdir -p "$VOICE_DIR"
printf '%s' "$PANE_ID" > "$FOCUS_FILE.tmp" && mv "$FOCUS_FILE.tmp" "$FOCUS_FILE"

# Send IPC message to arbiter for immediate response (best-effort)
SOCK="$ARBITER_SOCK"
[ ! -S "$SOCK" ] && SOCK="$LEGACY_SOCK"
[ ! -S "$SOCK" ] && exit 0

# Pass values via env vars (safe against injection in Python string literals)
VOICE_SOCK="$SOCK" VOICE_PANE_ID="$PANE_ID" python3 -c "
import socket, json, os
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1)
    s.connect(os.environ['VOICE_SOCK'])
    msg = json.dumps({'type': 'focus', 'pane_id': os.environ['VOICE_PANE_ID']})
    s.sendall((msg + '\n').encode())
    s.recv(4096)
    s.close()
except Exception:
    pass
" &>/dev/null &
disown

exit 0
