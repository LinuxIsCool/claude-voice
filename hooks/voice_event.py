#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""claude-voice hook handler — routes Claude Code events to audio feedback.

Usage:
    echo '{"session_id":"..."}' | uv run voice_event.py Stop

Registered events: SessionStart, Stop, Notification, SubagentStop, SessionEnd, PostToolUseFailure
"""
import json
import sys
from pathlib import Path

# Add lib/ to import path
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = PLUGIN_ROOT / "lib"
sys.path.insert(0, str(LIB_DIR))


def main() -> None:
    """Main entry point. Always exits 0, always prints {}."""
    event_name = sys.argv[1] if len(sys.argv) > 1 else "Unknown"

    # Parse stdin JSON (hook payload)
    try:
        raw = sys.stdin.read()
        hook_data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, UnicodeDecodeError, IOError):
        hook_data = {}

    # Route the event to audio
    try:
        from router import route_event

        route_event(event_name, hook_data)
    except Exception:
        pass  # NEVER crash — silent failure over broken sessions

    # Always return valid JSON response to Claude Code
    print(json.dumps({}), flush=True)


if __name__ == "__main__":
    main()
