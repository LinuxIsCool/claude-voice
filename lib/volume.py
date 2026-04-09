"""Volume gain chain for claude-voice.

Implements the four-stage multiplicative gain chain:

    output = category_vol x agent_vol x policy_vol x master_vol x system_gain

All user-facing values are clamped to 0.0-1.0. The system_gain is a hidden
hardware calibration constant (typically 3.0-4.0) that bridges the gap between
normalized 0-1 controls and actual output loudness.

Design reference:
    ~/.claude/local/journal/legion/2026/04/03/14-08-voice-volume-redesign-plan.md
    ~/.claude/local/research/2026/04/03/digital-mixing-principles/report.md
"""
from __future__ import annotations

from typing import Any, Dict


# Defaults (also in constants.py, duplicated here for standalone testability)
_DEFAULT_SYSTEM_GAIN = 1.0
_DEFAULT_MASTER_VOLUME = 0.7


def _clamp01(v: float) -> float:
    """Clamp a value to 0.0-1.0."""
    return max(0.0, min(1.0, v))


def compute_gain_chain(
    category: str,
    agent_id: str,
    config: Dict[str, Any],
    policy_vol: float = 1.0,
) -> Dict[str, Any]:
    """Compute the full gain chain for a voice message.

    All user-facing volumes are clamped to 0.0-1.0.
    system_gain is unclamped (hidden from user).

    Args:
        category: Sound category ("tts", "earcon", "notification", "ambient").
        agent_id: Persona slug for per-agent volume lookup.
        config: Full voice config dict (from load_config()).
        policy_vol: Mode/focus-based attenuation (0.0-1.0), supplied by arbiter.

    Returns:
        Dict with keys: category_vol, agent_vol, policy_vol, master_vol,
        system_gain, final (the pw-play volume), and chain_str (human-readable).
    """
    # Stage 1: Category volume
    categories = config.get("categories", {})
    if not isinstance(categories, dict):
        categories = {}
    category_vol = _clamp01(float(categories.get(category, 1.0)))

    # Stage 2: Per-agent volume
    agent_volumes = config.get("agent_volumes", {})
    if not isinstance(agent_volumes, dict):
        agent_volumes = {}
    agent_vol = _clamp01(float(agent_volumes.get(agent_id, agent_volumes.get("_default", 1.0))))

    # Stage 3: Policy volume (supplied by arbiter based on mode + focus)
    policy_vol = _clamp01(float(policy_vol))

    # Stage 4: Master volume
    master_vol = _clamp01(float(config.get("volume", _DEFAULT_MASTER_VOLUME)))

    # Hidden: System gain (hardware calibration)
    system_gain = float(config.get("system_gain", _DEFAULT_SYSTEM_GAIN))

    # Final output
    final = category_vol * agent_vol * policy_vol * master_vol * system_gain

    chain_str = (
        f"cat={category_vol:.2f} agent={agent_vol:.2f} policy={policy_vol:.2f} "
        f"master={master_vol:.2f} gain={system_gain:.2f} -> pw={final:.3f}"
    )

    return {
        "category_vol": category_vol,
        "agent_vol": agent_vol,
        "policy_vol": policy_vol,
        "master_vol": master_vol,
        "system_gain": system_gain,
        "final": final,
        "chain_str": chain_str,
    }


def policy_vol_for_mode(
    mode: str,
    pane_id: str,
    focused_pane: str,
    agent_id: str = "",
) -> float:
    """Determine policy_vol based on arbiter mode and pane focus state.

    Args:
        mode: Voice mode name (ambient, focused, solo, silent, broadcast).
        pane_id: The pane this message belongs to.
        focused_pane: The currently focused tmux pane.
        agent_id: Persona slug (used for broadcast mode filtering).

    Returns:
        Float 0.0-1.0 representing the mode/focus-based volume policy.
    """
    if mode == "silent":
        return 0.0

    if mode == "ambient":
        return 1.0

    if mode in ("focused", "solo"):
        if pane_id == "_global":
            return 1.0
        if not focused_pane:
            return 1.0  # No focus info = fail open
        return 1.0 if pane_id == focused_pane else 0.0

    if mode == "broadcast":
        if "matt" in agent_id.lower():
            return 1.0
        return 0.0

    # Unknown mode = ambient (fail open)
    return 1.0
