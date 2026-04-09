"""Agent sound profile resolution for claude-voice.

Maps persona slugs to per-agent sound sets from theme.json's agent_sounds section.
Each agent can have: select (pane focused), acknowledge (task accepted),
complete (task done), error (failure). Falls back to _default profile.

This is the RTS game model: navigate to a unit → hear its "what?" sound.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from constants import THEMES_DIR

# Sound slots available per agent (inspired by WC3 unit sound taxonomy)
AGENT_SOUND_SLOTS = ("select", "acknowledge", "complete", "error")


def resolve_agent_sound(
    persona: str, slot: str, theme: dict
) -> Optional[Path]:
    """Resolve a persona + slot to a WAV path from the theme's agent_sounds.

    Resolution order:
      1. theme.agent_sounds[persona][slot]
      2. theme.agent_sounds[_default][slot]
      3. None (no sound for this agent/slot combo)

    Args:
        persona: Agent persona slug (e.g. "matt", "darren"). Empty = _default.
        slot: Sound slot name (select, acknowledge, complete, error).
        theme: Loaded theme dict.

    Returns:
        Path to WAV file, or None if not found.
    """
    agent_sounds = theme.get("agent_sounds", {})
    if not agent_sounds:
        return None

    # Try persona-specific, then _default
    for key in (persona, "_default"):
        if not key:
            continue
        profile = agent_sounds.get(key, {})
        wav_name = profile.get(slot)
        if wav_name:
            theme_slug = theme.get("meta", {}).get("slug", "default")
            path = THEMES_DIR / theme_slug / wav_name
            if path.exists():
                return path

    return None


def get_agent_voice(persona: str, theme: dict) -> Optional[str]:
    """Get the TTS voice ID for an agent from theme config.

    Args:
        persona: Agent persona slug.
        theme: Loaded theme dict.

    Returns:
        Kokoro voice preset string (e.g. "am_onyx"), or None.
    """
    agent_sounds = theme.get("agent_sounds", {})
    for key in (persona, "_default"):
        if not key:
            continue
        profile = agent_sounds.get(key, {})
        voice = profile.get("voice_id")
        if voice:
            return voice
    return None
