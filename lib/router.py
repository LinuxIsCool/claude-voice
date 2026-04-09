"""Event router -- dispatches hook events to themed audio feedback.

This is the main entry point called by hook handlers. It ties together
config, theme resolution, spatial volume mixing, and audio playback
into a single route_event() call.
"""
from __future__ import annotations

import os
import subprocess
import time

from audio import play_sound
from constants import DEFAULT_FOCUS_VOLUMES, DEFAULT_PRIORITY_FLOORS, FOCUS_STATE_PATH, STT_ACTIVE_PATH
from logger import log_event
from state import load_config, write_heartbeat
from theme import get_sound_category, load_theme, resolve_sound


def _get_focus_state() -> str:
    """Determine this pane's spatial relationship to the focused pane.

    Returns one of: "focused", "same_window", "same_session", "other_session", "no_tmux".

    Three-tier resolution:
      1. No TMUX_PANE env var → "no_tmux" (full volume, not in tmux)
      2. Cached focus-state file exists → compare pane IDs (~0.1ms)
      3. Subprocess fallback → tmux display-message (~5ms)

    Fails open: returns "no_tmux" (full volume) if state can't be determined.
    """
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return "no_tmux"

    # Tier 1: check cached file (written by Layer 1 tmux hook)
    try:
        if FOCUS_STATE_PATH.exists():
            cached_pane = FOCUS_STATE_PATH.read_text().strip()
            if cached_pane == pane:
                return "focused"
            # File exists but doesn't match — we're not focused, but need
            # tmux to tell us if we're same_window/same_session/other_session.
            # Fall through to subprocess for the full picture.
    except Exception:
        pass

    # Tier 2: ask tmux directly (one call, three booleans)
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", pane,
             "#{pane_active} #{window_active} #{session_attached}"],
            capture_output=True, text=True, timeout=1,
        )
        parts = result.stdout.strip().split()
        pane_active = parts[0] == "1" if len(parts) > 0 else False
        window_active = parts[1] == "1" if len(parts) > 1 else False
        session_attached = parts[2] == "1" if len(parts) > 2 else False

        if pane_active and window_active:
            return "focused"
        elif window_active:
            return "same_window"
        elif session_attached:
            return "same_session"
        else:
            return "other_session"
    except Exception:
        return "no_tmux"  # Fail open — full volume


def _effective_volume(
    base: float, focus_state: str, priority: int, config: dict
) -> float:
    """Compute effective volume from spatial state and priority.

    Volume pipeline:
      spatial_vol = base × focus_multiplier
      floor_vol   = base × priority_floor
      effective   = max(spatial_vol, floor_vol)

    Priority floors let critical events (errors, notifications) override
    spatial silencing — you hear errors from background panes.
    """
    tmux_config = config.get("tmux", {})
    focus_volumes = tmux_config.get("focus_volumes", DEFAULT_FOCUS_VOLUMES)
    priority_floors = tmux_config.get("priority_floors", DEFAULT_PRIORITY_FLOORS)

    focus_mult = float(focus_volumes.get(focus_state, 1.0))
    spatial_vol = base * focus_mult

    # Handle both string and int keys (YAML parser may produce either)
    floor_mult = float(
        priority_floors.get(str(priority),
        priority_floors.get(priority, 0.0))
    )
    floor_vol = base * floor_mult

    return max(0.0, max(spatial_vol, floor_vol))  # No upper clamp — allow gain >1.0


def route_event(event_name: str, hook_data: dict) -> None:
    """Route a Claude Code hook event to audio playback.

    Steps:
    1. Check global mute (env var or config)
    2. Check per-hook enable/disable
    3. Load active theme
    4. Resolve event to sound file (with content-aware overrides for Stop)
    5. Calculate effective volume (master * category)
    6. Fire playback (non-blocking)
    7. Log event (JSONL + SQLite, async)
    8. Write health heartbeat

    Never raises. All failures are silently absorbed to avoid
    disrupting the Claude Code hook pipeline.
    """
    try:
        _route_event_inner(event_name, hook_data)
    except Exception:
        pass  # NEVER crash the hook


def _route_event_inner(event_name: str, hook_data: dict) -> None:
    """Inner routing logic, separated for clean exception boundary."""
    t_start = time.monotonic()

    # 1. Global mute check (env var)
    if os.environ.get("CLAUDE_VOICE_MUTE", "").lower() in ("true", "1", "yes"):
        log_event(event_name, hook_data.get("session_id"), muted=True)
        return

    config = load_config()

    # 1b. Global mute check (config)
    if config.get("mute", False):
        log_event(event_name, hook_data.get("session_id"), muted=True)
        return

    # 2. Per-hook enable check
    hooks_config = config.get("hooks", {})
    if not hooks_config.get(event_name, True):
        return  # Disabled hooks are not logged (too noisy)

    # 3. Load theme (env override or config)
    theme_name = os.environ.get("CLAUDE_VOICE_THEME") or config.get("theme", "default")
    theme = load_theme(theme_name)
    if not theme:
        return

    # 4. Resolve sound
    sound_path = resolve_sound(theme, event_name, hook_data)
    if not sound_path:
        return

    # 5. Calculate base volume (master × category)
    try:
        master_volume = float(
            os.environ.get("CLAUDE_VOICE_VOLUME") or config.get("volume", 0.8)
        )
    except (TypeError, ValueError):
        master_volume = 0.8

    hook_to_sound = theme.get("hook_to_sound", {})
    sound_token = hook_to_sound.get(event_name, "")
    category = get_sound_category(theme, sound_token)
    category_volume = config.get("categories", {}).get(category, 1.0)

    base_volume = max(0.0, master_volume * category_volume)  # No upper clamp — pw-play handles >1.0 as gain

    # 5b. Spatial volume mixing — the core of Phase 3.5
    focus_state = _get_focus_state()

    # Get priority from theme's semantic_sounds (0=ambient, 1=normal, 2=notification)
    semantic = theme.get("semantic_sounds", {}).get(sound_token, {})
    priority = semantic.get("priority", 0)

    mixed_vol = _effective_volume(base_volume, focus_state, priority, config)

    # 5c. STT suppression — mute everything while user is speaking
    try:
        from flags import is_flag_active
        if is_flag_active(STT_ACTIVE_PATH, max_age_seconds=120):
            mixed_vol = 0.0
    except ImportError:
        if STT_ACTIVE_PATH.exists():
            mixed_vol = 0.0

    # 5d. Audio sink routing (per-pane or global config)
    audio_config = config.get("audio", {})
    sink = audio_config.get("sink", "")
    # Per-pane override via tmux pane option (only if in tmux and no global sink)
    if not sink and os.environ.get("TMUX_PANE"):
        try:
            sink_result = subprocess.run(
                ["tmux", "show-option", "-pv", "@claude_audio_sink"],
                capture_output=True, text=True, timeout=1,
            )
            pane_sink = sink_result.stdout.strip()
            if pane_sink:
                sink = pane_sink
        except Exception:
            pass

    # 6. Fire earcon playback at mixed volume
    if mixed_vol > 0.0:
        play_sound(sound_path, volume=mixed_vol, sink=sink)

    # 6b. Agent sound profiles (RTS model) — play persona-specific sounds
    if mixed_vol > 0.0 and event_name in ("SubagentStart", "SubagentStop"):
        try:
            from agents import resolve_agent_sound
            persona = os.environ.get("PERSONA_SLUG", "")
            slot = "acknowledge" if event_name == "SubagentStart" else "complete"
            agent_wav = resolve_agent_sound(persona, slot, theme)
            if agent_wav:
                play_sound(agent_wav, volume=mixed_vol * 0.7, sink=sink)
        except Exception:
            pass  # Agent sounds are best-effort

    # 6c. Ambient engine — background drone during subagent activity
    try:
        import ambient as _ambient
        from agents import resolve_agent_sound as _resolve
        if event_name == "SubagentStart":
            count = _ambient.increment_agents()
            if not _ambient.is_running():
                ambient_wav = _resolve("_default", "select", theme)  # Reuse as ambient seed
                # Use the theme's actual ambient-loop if it exists
                theme_slug = theme.get("meta", {}).get("slug", "default")
                from constants import THEMES_DIR as _THEMES_DIR
                ambient_loop = _THEMES_DIR / theme_slug / "sounds" / "ambient-loop.wav"
                if ambient_loop.exists():
                    ambient_vol = config.get("categories", {}).get("ambient", 0.3)
                    _ambient.start_loop(ambient_loop, volume=ambient_vol)
        elif event_name == "SubagentStop":
            count = _ambient.decrement_agents()
            if count == 0:
                _ambient.stop_loop()
        elif event_name == "SessionEnd":
            _ambient.cleanup()
    except Exception:
        pass  # Ambient is best-effort

    # 7. TTS at mixed volume (skip synthesis entirely if volume is 0)
    tts_text = None
    tts_voice = None
    tts_config = config.get("tts", {})
    if event_name == "SessionStart" and mixed_vol > 0.0:
        tts_text, tts_voice = _play_cached_greeting(config, theme, mixed_vol)
    elif event_name == "Stop" and tts_config.get("enabled") and tts_config.get("response", True):
        if mixed_vol > 0.0:
            tts_text, tts_voice = _speak_response(hook_data, tts_config, mixed_vol, theme)

    # 7b. Ambient cleanup on SessionStart (resets stale state from crashes)
    if event_name == "SessionStart":
        try:
            import ambient as _amb
            _amb.cleanup()
        except Exception:
            pass

    # 8. Log full event payload — includes focus state and effective volume
    elapsed_ms = int((time.monotonic() - t_start) * 1000)
    log_event(
        event_name,
        hook_data.get("session_id"),
        theme=theme_name,
        sound=sound_path.name,
        tts_text=tts_text,
        tts_voice=tts_voice,
        volume=mixed_vol,
        muted=False,
        elapsed_ms=elapsed_ms,
        focus_state=focus_state,
    )

    # 9. Health heartbeat
    try:
        write_heartbeat()
    except Exception:
        pass  # Non-critical


def _speak_response(
    hook_data: dict, tts_config: dict, volume: float, theme: dict | None = None
) -> tuple[str | None, str | None]:
    """Speak a condensed excerpt of the assistant's response via TTS daemon.

    Extracts a speakable excerpt from last_assistant_message, then routes
    through the daemon (fast, ~90ms if warm) or subprocess (slow, ~8s cold).

    Voice resolution order: persona-specific (from theme agent_sounds) >
    global config (tts.voice) > fallback (af_heart).

    Returns (tts_text, tts_voice) if speech was dispatched, (None, None) otherwise.
    Never raises. Degrades silently.
    """
    try:
        message = hook_data.get("last_assistant_message", "")
        if not message:
            return None, None

        from constants import TTS_MAX_CHARS
        from tts import extract_speakable, speak_via_daemon

        max_chars = tts_config.get("response_max_chars", TTS_MAX_CHARS)
        speakable = extract_speakable(message, max_chars=max_chars)
        if not speakable:
            return None, None

        # Voice resolution: persona-specific > global config > fallback
        voice = tts_config.get("voice", "af_heart")
        if theme:
            persona = os.environ.get("PERSONA_SLUG", "")
            if persona:
                from agents import get_agent_voice
                agent_voice = get_agent_voice(persona, theme)
                if agent_voice:
                    voice = agent_voice

        speak_via_daemon(speakable, voice=voice, volume=volume)
        return speakable, voice
    except Exception:
        pass

    return None, None


def _play_cached_greeting(
    config: dict, theme: dict, volume: float
) -> tuple[str | None, str | None]:
    """Play a pre-cached TTS greeting if enabled.

    Routes through the voice queue daemon for proper tts-playing flag
    coordination. Falls back to direct play_sound if queue is unavailable.

    Returns (tts_text, tts_voice) if greeting was dispatched, (None, None) otherwise.
    Never raises. Degrades silently.
    """
    try:
        tts_config = config.get("tts", {})
        if not tts_config.get("enabled", False) or not tts_config.get("greeting", True):
            return None, None

        from tts import get_cached

        # Build greeting text from theme template
        greeting_template = (
            theme.get("tts", {}).get("greeting_template")
            or theme.get("meta", {}).get("greeting_template")
            or "{summary}"
        )
        greeting_text = greeting_template.replace("{summary}", "session ready")

        # Voice priority: theme tts config → global tts config → default
        voice = (
            theme.get("tts", {}).get("voice_id")
            or tts_config.get("voice", "af_heart")
        )

        cached_wav = get_cached(greeting_text, voice)
        if cached_wav is not None:
            # Route through queue for proper tts-playing flag coordination
            from queue_client import enqueue_speech
            result = enqueue_speech(
                wav_path=str(cached_wav),
                priority=1,
                agent_id=os.environ.get("PERSONA_SLUG", ""),
                volume=volume,
            )
            if result is None:
                # Queue unavailable — fall back to direct playback
                play_sound(cached_wav, volume=volume)
            return greeting_text, voice
    except Exception:
        pass  # TTS is best-effort — never crash for a greeting

    return None, None
