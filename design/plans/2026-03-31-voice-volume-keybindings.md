# Voice Volume Keybindings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Physical keybindings (Super+Shift+Up/Down/M) to control voice volume with KDE Plasma visual feedback (progress bar notification).

**Architecture:** One self-contained shell script (`voice-volume.sh`) handles all three operations (up, down, mute). Reads and writes `config.yaml` via the existing `state.py` atomic write pattern (but in bash for speed). KDE global shortcuts call the script with an argument. `notify-send` with `int:value:N` hint renders a progress bar in the Plasma notification popup.

**Tech Stack:** Bash, KDE Plasma 6 global shortcuts (kwriteconfig6 + kglobalaccel6), notify-send, PipeWire (wpctl)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `scripts/voice-volume.sh` | Read/modify config.yaml volume, send notification. Self-contained, no Python. |
| `scripts/install-voice-keybindings.sh` | Register KDE global shortcuts. Run once. |
| `tests/test_voice.py` | Test the volume clamp logic (via Python helper) |
| `skills/voice/SKILL.md` | Document the keybindings |

---

### Task 1: Create voice-volume.sh

**Files:**
- Create: `~/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/voice-volume.sh`

- [ ] **Step 1: Create the script**

Create `scripts/voice-volume.sh`:

```bash
#!/bin/bash
# Voice volume control with KDE Plasma notification.
# Usage: voice-volume.sh up|down|mute|set <value>
#
# Reads/writes ~/.claude/local/voice/config.yaml
# Shows a progress-bar notification via notify-send.
# Designed for KDE global shortcut binding.

set -euo pipefail

CONFIG="$HOME/.claude/local/voice/config.yaml"
STEP=0.1
MIN=0.0
MAX=1.0
APP_NAME="claude-voice"
ICON="audio-volume-medium"
REPLACE_ID_FILE="$HOME/.claude/local/voice/.notify-id"

# --- Read current volume from config ---
get_volume() {
    if [[ -f "$CONFIG" ]]; then
        grep -m1 '^volume:' "$CONFIG" | awk '{print $2}'
    else
        echo "0.8"
    fi
}

# --- Read mute state from config ---
get_mute() {
    if [[ -f "$CONFIG" ]]; then
        grep -m1 '^mute:' "$CONFIG" | awk '{print $2}'
    else
        echo "false"
    fi
}

# --- Write volume to config (sed in-place, preserves structure) ---
set_volume() {
    local new_vol="$1"
    if [[ -f "$CONFIG" ]]; then
        sed -i "s/^volume: .*/volume: ${new_vol}/" "$CONFIG"
    fi
}

# --- Write mute state to config ---
set_mute() {
    local new_mute="$1"
    if [[ -f "$CONFIG" ]]; then
        sed -i "s/^mute: .*/mute: ${new_mute}/" "$CONFIG"
    fi
}

# --- Clamp value between MIN and MAX ---
clamp() {
    local val="$1"
    python3 -c "print(max(${MIN}, min(${MAX}, round(${val}, 1))))"
}

# --- Send KDE notification with progress bar ---
notify() {
    local title="$1"
    local body="$2"
    local percent="$3"

    # Choose icon based on state
    local icon="audio-volume-medium"
    if (( $(echo "$percent == 0" | bc -l) )); then
        icon="audio-volume-muted"
    elif (( $(echo "$percent < 34" | bc -l) )); then
        icon="audio-volume-low"
    elif (( $(echo "$percent > 66" | bc -l) )); then
        icon="audio-volume-high"
    fi

    # Use replace-id to update the same notification (not spam new ones)
    notify-send \
        -a "$APP_NAME" \
        -i "$icon" \
        -h "int:value:${percent}" \
        -h "string:x-kde-appname:${APP_NAME}" \
        -h "string:synchronous:volume" \
        "$title" "$body"
}

# --- Main ---
action="${1:-status}"

case "$action" in
    up)
        vol=$(get_volume)
        mute=$(get_mute)
        if [[ "$mute" == "true" ]]; then
            set_mute "false"
        fi
        new_vol=$(clamp "$(echo "$vol + $STEP" | bc -l)")
        set_volume "$new_vol"
        percent=$(python3 -c "print(int(${new_vol} * 100))")
        notify "Voice Volume" "${percent}%" "$percent"
        ;;
    down)
        vol=$(get_volume)
        new_vol=$(clamp "$(echo "$vol - $STEP" | bc -l)")
        set_volume "$new_vol"
        percent=$(python3 -c "print(int(${new_vol} * 100))")
        if (( $(echo "$new_vol == 0" | bc -l) )); then
            notify "Voice Volume" "Silent" "0"
        else
            notify "Voice Volume" "${percent}%" "$percent"
        fi
        ;;
    mute)
        mute=$(get_mute)
        if [[ "$mute" == "true" ]]; then
            set_mute "false"
            vol=$(get_volume)
            percent=$(python3 -c "print(int(${vol} * 100))")
            notify "Voice Unmuted" "${percent}%" "$percent"
        else
            set_mute "true"
            notify "Voice Muted" "All voice silenced" "0"
        fi
        ;;
    set)
        new_vol="${2:-0.8}"
        new_vol=$(clamp "$new_vol")
        set_volume "$new_vol"
        percent=$(python3 -c "print(int(${new_vol} * 100))")
        notify "Voice Volume" "${percent}%" "$percent"
        ;;
    status)
        vol=$(get_volume)
        mute=$(get_mute)
        percent=$(python3 -c "print(int(${vol} * 100))")
        echo "volume: $vol ($percent%) mute: $mute"
        ;;
    *)
        echo "Usage: voice-volume.sh up|down|mute|set <0.0-1.0>|status"
        exit 1
        ;;
esac
```

- [ ] **Step 2: Make executable**

```bash
chmod +x ~/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/voice-volume.sh
```

- [ ] **Step 3: Test manually**

```bash
# Test each action:
cd ~/.claude/plugins/local/legion-plugins/plugins/claude-voice

# Status
./scripts/voice-volume.sh status
# Expected: "volume: 0.8 (80%) mute: false"

# Volume up
./scripts/voice-volume.sh up
# Expected: notification shows "Voice Volume 90%" with progress bar
./scripts/voice-volume.sh status
# Expected: "volume: 0.9 (90%) mute: false"

# Volume down twice
./scripts/voice-volume.sh down
./scripts/voice-volume.sh down
./scripts/voice-volume.sh status
# Expected: "volume: 0.7 (70%) mute: false"

# Mute toggle
./scripts/voice-volume.sh mute
./scripts/voice-volume.sh status
# Expected: "volume: 0.7 (70%) mute: true"

# Unmute
./scripts/voice-volume.sh mute
./scripts/voice-volume.sh status
# Expected: "volume: 0.7 (70%) mute: false"

# Set exact value
./scripts/voice-volume.sh set 0.8
./scripts/voice-volume.sh status
# Expected: "volume: 0.8 (80%) mute: false"

# Boundary test
./scripts/voice-volume.sh set 1.5
./scripts/voice-volume.sh status
# Expected: "volume: 1.0 (100%) mute: false" — clamped

./scripts/voice-volume.sh set 0.8
# Restore to normal
```

- [ ] **Step 4: Commit**

```bash
cd ~/.claude/plugins/local/legion-plugins
git add plugins/claude-voice/scripts/voice-volume.sh
git commit -m "feat(claude-voice): voice-volume.sh — keybinding-ready volume control with KDE notifications

Self-contained bash script for voice volume up/down/mute/set.
Reads and writes config.yaml directly. Shows KDE Plasma progress bar
notification (same style as hardware volume keys). Designed for global
shortcut binding."
```

---

### Task 2: Install KDE global shortcuts

**Files:**
- Create: `~/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/install-voice-keybindings.sh`

- [ ] **Step 1: Create installer script**

Create `scripts/install-voice-keybindings.sh`:

```bash
#!/bin/bash
# Install KDE Plasma 6 global shortcuts for voice volume control.
# Run once. Shortcuts persist across reboots.
#
# Bindings:
#   Super+Shift+Up    → voice volume up
#   Super+Shift+Down  → voice volume down
#   Super+Shift+M     → voice mute toggle

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOLUME_SCRIPT="${SCRIPT_DIR}/voice-volume.sh"

if [[ ! -x "$VOLUME_SCRIPT" ]]; then
    echo "Error: $VOLUME_SCRIPT not found or not executable"
    exit 1
fi

# KDE Plasma 6 uses .desktop files in ~/.local/share/kglobalaccel/ for custom shortcuts.
# Alternative: use khotkeys via kwriteconfig6.

DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"

# Create .desktop files for each action
cat > "$DESKTOP_DIR/claude-voice-up.desktop" << EOF
[Desktop Entry]
Type=Application
Name=Voice Volume Up
Exec=${VOLUME_SCRIPT} up
NoDisplay=true
X-KDE-GlobalAccel-CommandShortcut=true
EOF

cat > "$DESKTOP_DIR/claude-voice-down.desktop" << EOF
[Desktop Entry]
Type=Application
Name=Voice Volume Down
Exec=${VOLUME_SCRIPT} down
NoDisplay=true
X-KDE-GlobalAccel-CommandShortcut=true
EOF

cat > "$DESKTOP_DIR/claude-voice-mute.desktop" << EOF
[Desktop Entry]
Type=Application
Name=Voice Mute Toggle
Exec=${VOLUME_SCRIPT} mute
NoDisplay=true
X-KDE-GlobalAccel-CommandShortcut=true
EOF

echo "Desktop entries created."
echo ""
echo "Now register the shortcuts with KDE:"
echo ""

# Register shortcuts using kglobalaccel6 via kwriteconfig6
# The shortcut format for KDE Plasma 6:
kwriteconfig6 --file kglobalshortcutsrc \
    --group "claude-voice-up.desktop" \
    --key "_launch" "Meta+Shift+Up,none,Voice Volume Up"

kwriteconfig6 --file kglobalshortcutsrc \
    --group "claude-voice-down.desktop" \
    --key "_launch" "Meta+Shift+Down,none,Voice Volume Down"

kwriteconfig6 --file kglobalshortcutsrc \
    --group "claude-voice-mute.desktop" \
    --key "_launch" "Meta+Shift+M,none,Voice Mute Toggle"

echo "Shortcuts registered in kglobalshortcutsrc."
echo ""

# Reload kglobalaccel to pick up new shortcuts
# Method 1: dbus signal
qdbus6 org.kde.kglobalaccel /kglobalaccel blockGlobalShortcuts false 2>/dev/null || true
# Method 2: reconfigure
kquitapp6 kglobalaccel 2>/dev/null || true
sleep 1
kstart6 kglobalaccel 2>/dev/null &

echo "KDE global shortcuts installed:"
echo "  Super+Shift+Up    → Voice Volume Up"
echo "  Super+Shift+Down  → Voice Volume Down"
echo "  Super+Shift+M     → Voice Mute Toggle"
echo ""
echo "If shortcuts don't work immediately, log out and back in."
echo "Or: System Settings → Shortcuts → search 'Voice'"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x ~/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/install-voice-keybindings.sh
```

- [ ] **Step 3: Run the installer**

```bash
~/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/install-voice-keybindings.sh
```

- [ ] **Step 4: Test the keybindings**

Press each key combination:
- `Super+Shift+Up` — should see "Voice Volume 90%" notification with progress bar
- `Super+Shift+Down` — should see "Voice Volume 80%" notification
- `Super+Shift+M` — should see "Voice Muted" notification

If shortcuts don't fire, open System Settings → Shortcuts → search "Voice" and verify bindings.

- [ ] **Step 5: Restore volume to 0.8**

```bash
~/.claude/plugins/local/legion-plugins/plugins/claude-voice/scripts/voice-volume.sh set 0.8
```

- [ ] **Step 6: Commit**

```bash
cd ~/.claude/plugins/local/legion-plugins
git add plugins/claude-voice/scripts/install-voice-keybindings.sh
git commit -m "feat(claude-voice): KDE global shortcuts for volume control

Super+Shift+Up/Down/M for volume up/down/mute with visual feedback.
Desktop entries + kglobalshortcutsrc registration. One-time install."
```

---

### Task 3: Update documentation

**Files:**
- Modify: `~/.claude/plugins/local/legion-plugins/plugins/claude-voice/skills/voice/SKILL.md`
- Modify: `~/.claude/plugins/local/legion-plugins/plugins/claude-voice/CLAUDE.md`

- [ ] **Step 1: Add keybinding section to SKILL.md**

Add after the "## Focus Presets" section:

```markdown
## Volume Keybindings

Physical keyboard shortcuts for instant volume control (KDE Plasma):

| Shortcut | Action | Visual Feedback |
|----------|--------|-----------------|
| `Super+Shift+Up` | Volume +10% | Progress bar notification |
| `Super+Shift+Down` | Volume -10% | Progress bar notification |
| `Super+Shift+M` | Mute toggle | Muted/Unmuted notification |

Install: `scripts/install-voice-keybindings.sh` (run once)

CLI alternative: `scripts/voice-volume.sh up|down|mute|set <0.0-1.0>|status`
```

- [ ] **Step 2: Add to CLAUDE.md quick reference**

Add to the CLAUDE.md "## Quick Reference" section:

```markdown
- Volume keys: `Super+Shift+Up/Down/M` (install via `scripts/install-voice-keybindings.sh`)
- Volume CLI: `scripts/voice-volume.sh up|down|mute|set <value>|status`
```

- [ ] **Step 3: Commit**

```bash
cd ~/.claude/plugins/local/legion-plugins
git add plugins/claude-voice/skills/voice/SKILL.md plugins/claude-voice/CLAUDE.md
git commit -m "docs(claude-voice): document volume keybindings in SKILL.md and CLAUDE.md"
```

---

## Summary

| Task | Description | Est. |
|------|-------------|------|
| 1 | voice-volume.sh — read/write config, notify-send with progress bar | 15min |
| 2 | KDE global shortcuts — .desktop entries + kglobalshortcutsrc | 10min |
| 3 | Documentation — SKILL.md + CLAUDE.md | 5min |
| **Total** | | **~30min** |

## User Experience

After installation:

1. Press `Super+Shift+Up` — screen shows progress bar at 90%, voice gets louder
2. Press `Super+Shift+Down` — progress bar drops to 80%
3. Press `Super+Shift+M` — "Voice Muted" notification, all voice silent
4. Press `Super+Shift+M` again — "Voice Unmuted 80%", voice resumes

Same visual style as hardware volume keys. Notification replaces itself (doesn't spam). Volume is clamped 0.0-1.0. Mute+up auto-unmutes.
