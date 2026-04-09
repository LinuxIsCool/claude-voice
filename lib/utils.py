"""Shared utilities for claude-voice.

Functions used by multiple modules. Defined once here, imported everywhere.
"""
from __future__ import annotations

from typing import Any, Dict


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge two dicts. Override wins for leaf values.

    Used by theme.py (theme inheritance) and state.py (config defaults merge).
    """
    merged: Dict[str, Any] = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def cache_key(text: str, voice: str) -> str:
    """Generate a deterministic cache key for text + voice combo.

    Used by tts.py, tts_daemon.py, and tts_warmup.py. The key scheme
    MUST be identical everywhere or cache lookups silently fail.

    Returns first 16 hex chars of SHA256(voice:text).
    """
    import hashlib
    content = f"{voice}:{text}".encode("utf-8")
    return hashlib.sha256(content).hexdigest()[:16]
