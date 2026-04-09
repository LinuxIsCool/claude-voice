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

# Register shortcuts in kglobalshortcutsrc
kwriteconfig6 --file kglobalshortcutsrc \
    --group "claude-voice-up.desktop" \
    --key "_launch" "Meta+Shift+Up,none,Voice Volume Up"

kwriteconfig6 --file kglobalshortcutsrc \
    --group "claude-voice-down.desktop" \
    --key "_launch" "Meta+Shift+Down,none,Voice Volume Down"

kwriteconfig6 --file kglobalshortcutsrc \
    --group "claude-voice-mute.desktop" \
    --key "_launch" "Meta+Shift+M,none,Voice Mute Toggle"

echo "Shortcuts registered."

# Reload kglobalaccel to pick up changes
# Try multiple methods — KDE Plasma 6 is finicky about reload
if command -v kquitapp6 &>/dev/null; then
    kquitapp6 kglobalaccel 2>/dev/null || true
    sleep 1
fi

echo ""
echo "KDE global shortcuts installed:"
echo "  Super+Shift+Up    → Voice Volume Up"
echo "  Super+Shift+Down  → Voice Volume Down"
echo "  Super+Shift+M     → Voice Mute Toggle"
echo ""
echo "If shortcuts don't work immediately:"
echo "  1. Open System Settings → Shortcuts → search 'Voice'"
echo "  2. Or log out and back in"
