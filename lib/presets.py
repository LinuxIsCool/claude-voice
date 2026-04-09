"""Focus presets for claude-voice.

Named configurations that switch spatial mixer behavior.
Each preset is a partial config dict merged into config.yaml.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict

from constants import CONFIG_PATH, VOICE_DATA_DIR
from state import load_config, save_config

BACKUP_PATH = VOICE_DATA_DIR / ".preset-backup.yaml"

PRESETS: Dict[str, Dict[str, Any]] = {
    "focus-only": {
        "tmux": {
            "focus_volumes": {
                "focused": 1.0,
                "same_window": 0.0,
                "same_session": 0.0,
                "other_session": 0.0,
            },
        },
    },
    "spatial": {
        "tmux": {
            "focus_volumes": {
                "focused": 1.0,
                "same_window": 0.5,
                "same_session": 0.2,
                "other_session": 0.0,
            },
        },
    },
    "hear-all": {
        "tmux": {
            "focus_volumes": {
                "focused": 1.0,
                "same_window": 1.0,
                "same_session": 1.0,
                "other_session": 1.0,
            },
        },
    },
    "meeting": {
        "volume": 0.1,
        "tts": {
            "enabled": False,
        },
        "tmux": {
            "focus_volumes": {
                "focused": 0.1,
                "same_window": 0.0,
                "same_session": 0.0,
                "other_session": 0.0,
            },
        },
    },
}


def apply_preset(name: str) -> bool:
    """Apply a named preset to config.yaml.

    Saves current config as backup for restore. Returns True on success.
    """
    if name == "restore":
        return _restore()

    preset = PRESETS.get(name)
    if not preset:
        return False

    # Save backup of current config for restore
    if CONFIG_PATH.exists():
        shutil.copy2(CONFIG_PATH, BACKUP_PATH)

    # Load current config, merge preset values, save
    config = load_config()
    _deep_merge(config, preset)
    save_config(config)
    return True


def _restore() -> bool:
    """Restore config from backup created by last apply_preset."""
    if not BACKUP_PATH.exists():
        return False
    shutil.copy2(BACKUP_PATH, CONFIG_PATH)
    BACKUP_PATH.unlink(missing_ok=True)
    return True


def list_presets() -> list[str]:
    """Return available preset names."""
    return list(PRESETS.keys()) + ["restore"]


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Modifies base in place."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base
