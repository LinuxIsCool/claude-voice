"""State management for claude-voice.

Config loading with defaults merge, atomic writes via fcntl + tempfile + rename,
and health heartbeat. Minimal YAML parser (no pyyaml dependency).
"""
from __future__ import annotations

import fcntl
import os
import tempfile
import time
from typing import Any, Dict

from constants import (
    CONFIG_DIR, CONFIG_PATH, DEFAULT_FOCUS_VOLUMES,
    DEFAULT_MASTER_VOLUME, DEFAULT_PRIORITY_FLOORS,
    DEFAULT_SYSTEM_GAIN, HEARTBEAT_PATH, TTS_MAX_CHARS,
)
from utils import deep_merge as _deep_merge

LOCK_PATH = CONFIG_DIR / ".config.lock"

DEFAULT_CONFIG: Dict[str, Any] = {
    "theme": "default",
    "volume": DEFAULT_MASTER_VOLUME,
    "system_gain": DEFAULT_SYSTEM_GAIN,
    "mute": False,
    "agent_volumes": {"_default": 1.0},
    "hooks": {
        "SessionStart": True,
        "Stop": True,
        "Notification": True,
        "SubagentStart": True,
        "SubagentStop": True,
        "SessionEnd": True,
        "PostToolUseFailure": True,
        "UserPromptSubmit": False,
        "PreCompact": True,
        "PermissionRequest": True,
    },
    "categories": {
        "earcon": 1.0,
        "notification": 1.0,
        "ambient": 0.3,
    },
    "tts": {
        "enabled": False,
        "backend": "auto",
        "voice": "am_onyx",
        "quality": "normal",
        "cache": True,
        "greeting": True,
        "response": True,
        "response_max_chars": TTS_MAX_CHARS,
    },
    "audio": {
        "sink": "",  # PipeWire target sink. Empty = system default.
    },
    "tmux": {
        "focus_volumes": dict(DEFAULT_FOCUS_VOLUMES),
        "priority_floors": dict(DEFAULT_PRIORITY_FLOORS),
    },
}


def _parse_scalar(raw: str) -> Any:
    """Convert a raw YAML string value to the appropriate Python type."""
    v = raw.strip()
    if not v:
        return ""
    # Boolean
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    # Integer (no decimal point)
    try:
        if "." not in v:
            return int(v)
    except ValueError:
        pass
    # Float
    try:
        return float(v)
    except ValueError:
        pass
    # String -- strip surrounding quotes if present
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    return v


def _parse_yaml_simple(text: str) -> Dict[str, Any]:
    """Minimal YAML parser for simple key:value configs.

    Handles:
    - Up to 3-level nesting (indent 0/2/4 = depth 0/1/2)
    - Scalar values: string, int, float, bool
    - Comments (lines starting with #) and empty lines
    - Inline comments after space-hash

    IMPORTANT: Uses 2-space indentation exclusively. 4-space indentation
    for the first level will be misinterpreted as depth 2.

    Does NOT handle: lists, anchors, multi-line strings, flow style,
    or quoted strings with embedded colons.
    """
    result: Dict[str, Any] = {}
    current_path: list[str] = []

    def _get_or_create_dict(path: list[str]) -> Dict[str, Any]:
        """Walk result dict along path, creating dicts as needed."""
        node: Dict[str, Any] = result
        for part in path:
            if part not in node or not isinstance(node[part], dict):
                node[part] = {}
            node = node[part]
        return node

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            continue

        # Detect indentation level (number of leading spaces)
        indent = len(line) - len(line.lstrip(" "))

        key_part, _, value_part = stripped.partition(":")
        key = key_part.strip()
        value_raw = value_part.strip()

        # Remove inline comment (only when '#' is preceded by whitespace)
        if " #" in value_raw:
            value_raw = value_raw[: value_raw.index(" #")].strip()

        is_section_header = value_raw == ""

        # Truncate current_path based on indent depth
        # indent 0 -> depth 0 (top-level)
        # indent 2 -> depth 1 (one level under top-level section)
        # indent 4+ -> depth 2 (two levels deep)
        depth = indent // 2
        current_path = current_path[:depth]

        if is_section_header:
            parent = _get_or_create_dict(current_path)
            parent[key] = {}
            current_path.append(key)
        else:
            parent = _get_or_create_dict(current_path)
            parent[key] = _parse_scalar(value_raw)

    return result


def _serialize_yaml(data: Dict[str, Any], indent: int = 0) -> str:
    """Serialize a dict to minimal YAML (2-level max)."""
    lines: list[str] = []
    prefix = "  " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(_serialize_yaml(value, indent + 1))
        elif isinstance(value, bool):
            lines.append(f"{prefix}{key}: {'true' if value else 'false'}")
        elif isinstance(value, float):
            lines.append(f"{prefix}{key}: {value}")
        else:
            lines.append(f"{prefix}{key}: {value}")
    return "\n".join(lines)


def load_config() -> Dict[str, Any]:
    """Load config from disk, merged with defaults.

    Auto-creates config dir and file if missing. Returns a complete
    config dict with all DEFAULT_CONFIG keys guaranteed present.
    """
    try:
        if CONFIG_PATH.exists():
            text = CONFIG_PATH.read_text(encoding="utf-8")
            user_config = _parse_yaml_simple(text)
            if isinstance(user_config, dict):
                return _deep_merge(DEFAULT_CONFIG, user_config)
    except (OSError, TypeError, ValueError):
        pass

    # No config file or parse failure -- create default
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        save_config(DEFAULT_CONFIG)
    except OSError:
        pass  # Can't write? Use defaults in memory.

    return dict(DEFAULT_CONFIG)


def save_config(config: Dict[str, Any]) -> None:
    """Atomically write config under file lock.

    Uses fcntl.flock + tempfile.mkstemp + os.rename for crash safety.
    Same pattern as claude-statusline.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_PATH, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yaml_text = _serialize_yaml(config)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(CONFIG_DIR),
                prefix=".config_",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(yaml_text)
                    f.write("\n")
                os.rename(tmp_path, str(CONFIG_PATH))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def write_heartbeat() -> None:
    """Write a health heartbeat timestamp.

    Used by /status and monitoring to confirm voice hook is alive.
    """
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass  # Non-critical -- never crash for heartbeat
