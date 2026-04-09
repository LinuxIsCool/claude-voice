"""Theme engine -- load theme.json, resolve sounds, select variants."""
from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Optional

# Resolve PLUGIN_ROOT from this file's location
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
THEMES_DIR = PLUGIN_ROOT / "assets" / "themes"

_theme_cache: dict[str, dict] = {}


def clear_theme_cache() -> None:
    """Clear the in-memory theme cache.

    Useful in long-running contexts (tests, interactive use) where the
    theme config may change mid-process. Not needed in the hook path
    since each hook invocation is a separate process.
    """
    _theme_cache.clear()


def _load_json(path: Path) -> dict:
    """Load JSON file, return empty dict on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


from utils import deep_merge as _deep_merge


def load_theme(theme_name: str = "default") -> dict:
    """Load and cache a theme definition from theme.json.

    Game themes inherit from default -- only overridden keys need
    to be present in the game theme.json.  A full deep merge is
    performed so that missing sections fall back to default values.

    Falls back to "default" theme if the requested theme is missing.
    Returns empty dict on total failure (never raises).
    """
    if theme_name in _theme_cache:
        return _theme_cache[theme_name]

    # Always load default as base
    default = _load_json(THEMES_DIR / "default" / "theme.json")
    if theme_name == "default":
        _theme_cache["default"] = default
        return default

    # Sanitize theme_name against path traversal
    if "/" in theme_name or "\\" in theme_name or ".." in theme_name:
        _theme_cache[theme_name] = default
        return default

    # Load + merge game theme
    game_path = THEMES_DIR / theme_name / "theme.json"
    if not game_path.exists():
        _theme_cache[theme_name] = default
        return default

    game = _load_json(game_path)
    merged = _deep_merge(default, game)
    _theme_cache[theme_name] = merged
    return merged


def resolve_sound(
    theme: dict,
    event_name: str,
    hook_data: Optional[dict] = None,
) -> Optional[Path]:
    """Resolve a hook event to a WAV file path.

    Steps:
    1. Map event via hook_to_sound
    2. Apply content-aware overrides for Stop events
    3. Look up semantic_sounds for variant list
    4. Random variant selection
    5. Construct and verify file path

    Returns None if no sound should play (missing mapping, no file, etc).
    """
    # Guard: stop_hook_active prevents infinite loops
    if hook_data and hook_data.get("stop_hook_active"):
        return None

    hook_to_sound = theme.get("hook_to_sound", {})
    sound_token = hook_to_sound.get(event_name)
    if not sound_token:
        return None

    # Content-aware override for Stop and SubagentStop
    if event_name in ("Stop", "SubagentStop") and hook_data:
        message = hook_data.get("last_assistant_message", "")
        if message:
            overrides = (
                theme.get("content_aware_overrides", {})
                .get(event_name, {})
                .get("patterns", {})
            )
            # Also check Stop overrides for SubagentStop as fallback
            if event_name == "SubagentStop" and not overrides:
                overrides = (
                    theme.get("content_aware_overrides", {})
                    .get("Stop", {})
                    .get("patterns", {})
                )
            # Patterns evaluated in JSON key order — first match wins.
            # Put specific patterns (commit, test) before general (error).
            for pattern, token in overrides.items():
                try:
                    if re.search(pattern, message):
                        sound_token = token
                        break
                except re.error:
                    continue

    # Look up variants from semantic_sounds
    sounds = theme.get("semantic_sounds", {})
    sound_def = sounds.get(sound_token)
    if not sound_def:
        return None

    variants = sound_def.get("variants", [])
    if not variants:
        return None

    # Random variant selection
    chosen = random.choice(variants)

    # Resolve path using theme slug from meta
    theme_slug = theme.get("meta", {}).get("slug", "default")
    sound_path = THEMES_DIR / theme_slug / "sounds" / chosen

    if sound_path.exists():
        return sound_path
    return None


def get_sound_category(theme: dict, sound_token: str) -> str:
    """Get the category for a sound token (earcon, notification, ambient).

    Returns "earcon" as default if token not found.
    """
    sounds = theme.get("semantic_sounds", {})
    sound_def = sounds.get(sound_token, {})
    return sound_def.get("category", "earcon")
