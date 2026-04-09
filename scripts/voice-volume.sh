#!/bin/bash
# Voice volume control with KDE Plasma notification.
# Usage: voice-volume.sh up|down|mute|set <value>|status
#
# Reads/writes ~/.claude/local/voice/config.yaml
# Shows a progress-bar notification via notify-send.
# Designed for KDE global shortcut binding.

set -euo pipefail

# Ensure DBus/Wayland env vars are set (needed when called from tmux run-shell)
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
export DISPLAY="${DISPLAY:-:0}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

CONFIG="$HOME/.claude/local/voice/config.yaml"
STEP=0.1
MIN=0.0
MAX=1.0
APP_NAME="claude-voice"

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
    python3 -c "print(max(${MIN}, min(${MAX}, round(float(${val}), 1))))"
}

# --- Send KDE notification with progress bar ---
notify() {
    local title="$1"
    local body="$2"
    local percent="$3"

    # Choose icon based on level
    local icon="audio-volume-medium"
    if [[ "$percent" -eq 0 ]]; then
        icon="audio-volume-muted"
    elif [[ "$percent" -lt 34 ]]; then
        icon="audio-volume-low"
    elif [[ "$percent" -gt 66 ]]; then
        icon="audio-volume-high"
    fi

    # "synchronous:volume" makes KDE replace the notification (not spam)
    notify-send \
        -a "$APP_NAME" \
        -i "$icon" \
        -h "int:value:${percent}" \
        -h "string:x-kde-appname:${APP_NAME}" \
        -h "string:synchronous:volume" \
        "$title" "$body" 2>/dev/null || true
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
        new_vol=$(clamp "$(python3 -c "print(${vol} + ${STEP})")")
        set_volume "$new_vol"
        percent=$(python3 -c "print(int(${new_vol} * 100))")
        notify "Voice Volume" "${percent}%" "$percent"
        ;;
    down)
        vol=$(get_volume)
        new_vol=$(clamp "$(python3 -c "print(${vol} - ${STEP})")")
        set_volume "$new_vol"
        percent=$(python3 -c "print(int(${new_vol} * 100))")
        if [[ "$percent" -eq 0 ]]; then
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
        gain=$(grep -m1 '^system_gain:' "$CONFIG" 2>/dev/null | awk '{print $2}')
        gain="${gain:-3.5}"
        percent=$(python3 -c "print(int(float(${vol}) * 100))")
        effective=$(python3 -c "print(round(float(${vol}) * float(${gain}), 2))")
        echo "volume: $vol ($percent%) system_gain: $gain effective_max: $effective mute: $mute"
        ;;
    gain)
        if [[ -n "${2:-}" ]]; then
            # Set system_gain (sed only — never append to avoid TOCTOU with state.py)
            new_gain="$2"
            if grep -q '^system_gain:' "$CONFIG" 2>/dev/null; then
                sed -i "s/^system_gain: .*/system_gain: ${new_gain}/" "$CONFIG"
            else
                # Key missing — add after volume line (safe insertion point)
                sed -i "/^volume:/a system_gain: ${new_gain}" "$CONFIG"
            fi
            echo "system_gain: $new_gain"
        else
            # Show system_gain
            gain=$(grep -m1 '^system_gain:' "$CONFIG" 2>/dev/null | awk '{print $2}')
            echo "system_gain: ${gain:-3.5}"
        fi
        ;;
    chain)
        # Query arbiter for current volume state
        PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
        uv run "$PLUGIN_DIR/scripts/voice_arbiter.py" --check 2>/dev/null || echo "Arbiter not running"
        vol=$(get_volume)
        gain=$(grep -m1 '^system_gain:' "$CONFIG" 2>/dev/null | awk '{print $2}')
        gain="${gain:-3.5}"
        echo "Gain chain: cat * agent * policy * master($vol) * gain($gain) = pw-play volume"
        ;;
    *)
        echo "Usage: voice-volume.sh up|down|mute|set <0.0-1.0>|status|gain [value]|chain"
        exit 1
        ;;
esac
