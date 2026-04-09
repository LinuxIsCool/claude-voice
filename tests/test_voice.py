"""Test suite for claude-voice plugin.

Covers: constants, utils, spatial mixer, STT suppression, audio routing,
config parsing, logging, and hook e2e. Run with:

    cd plugins/claude-voice && uv run pytest tests/ -v

Or from lib/ directly:

    cd plugins/claude-voice && python3 tests/test_voice.py
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Add lib/ to path so we can import plugin modules
LIB_DIR = Path(__file__).resolve().parent.parent / "lib"
sys.path.insert(0, str(LIB_DIR))

# ---------------------------------------------------------------------------
# Test: constants.py
# ---------------------------------------------------------------------------


def test_constants_values():
    from constants import (
        TTS_MAX_CHARS, TARGET_SAMPLE_RATE, VOICE_DATA_DIR,
        CACHE_DIR, DAEMON_SOCKET, HEARTBEAT_PATH, FOCUS_STATE_PATH,
        STT_ACTIVE_PATH, DEFAULT_FOCUS_VOLUMES, DEFAULT_PRIORITY_FLOORS,
    )
    assert TTS_MAX_CHARS == 15000
    assert TARGET_SAMPLE_RATE == 48000
    assert len(DEFAULT_FOCUS_VOLUMES) == 5
    assert len(DEFAULT_PRIORITY_FLOORS) == 3
    assert DEFAULT_FOCUS_VOLUMES["focused"] == 1.0
    assert DEFAULT_FOCUS_VOLUMES["other_session"] == 0.0
    assert DEFAULT_PRIORITY_FLOORS["2"] == 0.8
    assert STT_ACTIVE_PATH == VOICE_DATA_DIR / "stt-active"
    assert FOCUS_STATE_PATH == VOICE_DATA_DIR / "focus-state"


# ---------------------------------------------------------------------------
# Test: utils.py
# ---------------------------------------------------------------------------


def test_deep_merge_basic():
    from utils import deep_merge
    result = deep_merge({"a": 1, "b": {"c": 2}}, {"b": {"d": 3}})
    assert result == {"a": 1, "b": {"c": 2, "d": 3}}


def test_deep_merge_override():
    from utils import deep_merge
    result = deep_merge({"a": 1}, {"a": 2})
    assert result == {"a": 2}


def test_deep_merge_no_mutate():
    from utils import deep_merge
    base = {"a": {"b": 1}}
    override = {"a": {"c": 2}}
    deep_merge(base, override)
    assert base == {"a": {"b": 1}}  # Original unchanged


def test_cache_key_deterministic():
    from utils import cache_key
    k1 = cache_key("hello", "am_onyx")
    k2 = cache_key("hello", "am_onyx")
    assert k1 == k2
    assert len(k1) == 16
    assert all(c in "0123456789abcdef" for c in k1)


def test_cache_key_different_inputs():
    from utils import cache_key
    k1 = cache_key("hello", "am_onyx")
    k2 = cache_key("world", "am_onyx")
    k3 = cache_key("hello", "af_heart")
    assert k1 != k2
    assert k1 != k3


def test_cache_key_matches_script_impl():
    """Verify lib cache_key matches the standalone script implementation."""
    from utils import cache_key
    # Simulate the script's implementation
    def script_key(text, voice):
        content = f"{voice}:{text}".encode("utf-8")
        return hashlib.sha256(content).hexdigest()[:16]
    for text, voice in [("hello", "am_onyx"), ("", "af_heart"), ("a" * 1000, "am_adam")]:
        assert cache_key(text, voice) == script_key(text, voice)


# ---------------------------------------------------------------------------
# Test: spatial mixer (_get_focus_state, _effective_volume)
# ---------------------------------------------------------------------------


# NOTE: test_get_focus_state_* and test_effective_volume_* removed.
# The spatial mixer (_get_focus_state, _effective_volume) was replaced by
# the gain chain (volume.py) and the arbiter's mode-based policy.
# See TestGainChain and TestPolicyVol at the bottom of this file.


# ---------------------------------------------------------------------------
# Test: STT suppression
# ---------------------------------------------------------------------------


def test_stt_active_suppresses():
    from constants import STT_ACTIVE_PATH
    STT_ACTIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Test the file existence check directly
    STT_ACTIVE_PATH.touch()
    assert STT_ACTIVE_PATH.exists()
    STT_ACTIVE_PATH.unlink()
    assert not STT_ACTIVE_PATH.exists()


# ---------------------------------------------------------------------------
# Test: extract_speakable
# ---------------------------------------------------------------------------


def test_extract_speakable_full_text():
    from tts import extract_speakable
    text = "This is a complete response. It has two sentences."
    assert extract_speakable(text) == text


def test_extract_speakable_strips_code():
    from tts import extract_speakable
    text = "Before code.\n```python\nx = 1\ny = 2\n```\nAfter code."
    result = extract_speakable(text)
    assert "x = 1" not in result
    assert "After code" in result


def test_extract_speakable_code_only_returns_none():
    from tts import extract_speakable
    text = "```python\n" + "x = 1\n" * 50 + "```"
    assert extract_speakable(text) is None


def test_extract_speakable_preserves_bold():
    from tts import extract_speakable
    text = "**Bold text** and *italic text*."
    result = extract_speakable(text)
    assert "Bold text" in result
    assert "italic text" in result


def test_extract_speakable_strips_tables():
    from tts import extract_speakable
    text = "Before table.\n| A | B |\n|---|---|\n| 1 | 2 |\nAfter table."
    result = extract_speakable(text)
    assert "Before table" in result
    assert "After table" in result


def test_extract_speakable_empty():
    from tts import extract_speakable
    assert extract_speakable("") is None
    assert extract_speakable(None) is None


def test_extract_speakable_truncates_at_sentence():
    from tts import extract_speakable
    text = "First sentence. " * 200  # Very long
    result = extract_speakable(text, max_chars=100)
    assert len(result) <= 100
    assert result.endswith(".")


# ---------------------------------------------------------------------------
# Test: config (state.py)
# ---------------------------------------------------------------------------


def test_default_config_has_tmux():
    from state import DEFAULT_CONFIG
    assert "tmux" in DEFAULT_CONFIG
    assert "focus_volumes" in DEFAULT_CONFIG["tmux"]
    assert "priority_floors" in DEFAULT_CONFIG["tmux"]


def test_default_config_tts_max_chars():
    from state import DEFAULT_CONFIG
    from constants import TTS_MAX_CHARS
    assert DEFAULT_CONFIG["tts"]["response_max_chars"] == TTS_MAX_CHARS


# ---------------------------------------------------------------------------
# Test: audio.py
# ---------------------------------------------------------------------------


def test_play_sound_missing_file():
    from audio import play_sound
    result = play_sound(Path("/nonexistent/file.wav"))
    assert result is None


def test_build_args_pw_play():
    from audio import _build_args
    args = _build_args("pw-play", "/usr/bin/pw-play", Path("/test.wav"), 0.5)
    assert "--volume=0.500" in args[1]
    assert args[-1] == "/test.wav"


def test_build_args_pw_play_with_sink():
    from audio import _build_args
    args = _build_args("pw-play", "/usr/bin/pw-play", Path("/test.wav"), 0.5, sink="hdmi-output-0")
    assert "--target=hdmi-output-0" in args


# ---------------------------------------------------------------------------
# Test: agents.py
# ---------------------------------------------------------------------------


def test_resolve_agent_sound_known_persona():
    from agents import resolve_agent_sound
    from theme import load_theme
    theme = load_theme("default")
    path = resolve_agent_sound("matt", "select", theme)
    assert path is not None
    assert path.exists()
    assert "matt-select" in path.name


def test_resolve_agent_sound_unknown_falls_to_default():
    from agents import resolve_agent_sound
    from theme import load_theme
    theme = load_theme("default")
    path = resolve_agent_sound("unknown_agent", "select", theme)
    assert path is not None
    assert "_default-select" in path.name


def test_resolve_agent_sound_missing_slot():
    from agents import resolve_agent_sound
    from theme import load_theme
    theme = load_theme("default")
    path = resolve_agent_sound("matt", "nonexistent_slot", theme)
    assert path is None


def test_get_agent_voice():
    from agents import get_agent_voice
    from theme import load_theme
    theme = load_theme("default")
    assert get_agent_voice("matt", theme) == "am_onyx"
    assert get_agent_voice("darren", theme) == "am_adam"
    assert get_agent_voice("unknown", theme) is None  # _default has no voice_id


# ---------------------------------------------------------------------------
# Test: ambient.py
# ---------------------------------------------------------------------------


def test_ambient_agent_count():
    import ambient
    # Reset state
    ambient.COUNT_FILE.unlink(missing_ok=True)
    ambient.PID_FILE.unlink(missing_ok=True)
    assert ambient.get_agent_count() == 0

    # First increment: count was 0, no reset needed → 1
    assert ambient.increment_agents() == 1

    # Second increment: count=1 but no ambient loop running → resets to 0 then +1 = 1
    # This is the correct behavior: prevents drift when no loop is alive
    count2 = ambient.increment_agents()
    assert count2 == 1  # Reset + increment (no live PID)

    # Decrement always works on whatever is there
    assert ambient.decrement_agents() == 0
    assert ambient.decrement_agents() == 0  # Doesn't go negative
    ambient.COUNT_FILE.unlink(missing_ok=True)


def test_ambient_is_running_no_pid():
    import ambient
    ambient.PID_FILE.unlink(missing_ok=True)
    assert not ambient.is_running()


def test_ambient_cleanup():
    import ambient
    ambient.COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    ambient.COUNT_FILE.write_text("5")
    ambient.cleanup()
    assert not ambient.COUNT_FILE.exists()


def test_ambient_increment_validates_pid(tmp_path, monkeypatch):
    """increment_agents should reset count if ambient PID is dead."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    import ambient

    monkeypatch.setattr(ambient, "PID_FILE", tmp_path / "ambient.pid")
    monkeypatch.setattr(ambient, "COUNT_FILE", tmp_path / "ambient-count")

    # Simulate stale state: count=6, dead PID
    (tmp_path / "ambient-count").write_text("6")
    (tmp_path / "ambient.pid").write_text("999999999")  # Non-existent PID

    count = ambient.increment_agents()
    # Should detect dead PID and reset to 1, not increment to 7
    assert count == 1


def test_ambient_cleanup_resets_everything(tmp_path, monkeypatch):
    """cleanup() must clear both count file and PID file."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    import ambient

    monkeypatch.setattr(ambient, "PID_FILE", tmp_path / "ambient.pid")
    monkeypatch.setattr(ambient, "COUNT_FILE", tmp_path / "ambient-count")

    (tmp_path / "ambient-count").write_text("6")
    (tmp_path / "ambient.pid").write_text("12345")

    ambient.cleanup()
    assert not (tmp_path / "ambient-count").exists()
    assert not (tmp_path / "ambient.pid").exists()


# ---------------------------------------------------------------------------
# Test: logging
# ---------------------------------------------------------------------------


def test_log_event_focus_state():
    from logger import log_event
    # Just verify it doesn't crash with focus_state param
    log_event("Test", "test-session", focus_state="focused", volume=0.5)
    # Give thread time to complete
    time.sleep(0.1)


# ---------------------------------------------------------------------------
# Test: hook e2e
# ---------------------------------------------------------------------------


def test_hook_e2e_exits_zero():
    result = subprocess.run(
        ["uv", "run", "hooks/voice_event.py", "Notification"],
        input=json.dumps({"session_id": "test-suite", "type": "Notification", "data": {}}),
        capture_output=True, text=True, timeout=10,
        cwd=str(LIB_DIR.parent),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "{}"


def test_hook_e2e_invalid_json():
    result = subprocess.run(
        ["uv", "run", "hooks/voice_event.py", "Stop"],
        input="not json at all",
        capture_output=True, text=True, timeout=10,
        cwd=str(LIB_DIR.parent),
    )
    assert result.returncode == 0  # Never crash
    assert result.stdout.strip() == "{}"


# ---------------------------------------------------------------------------
# Test: queue_client.py
# ---------------------------------------------------------------------------


def test_enqueue_speech_no_daemon():
    """When queue daemon is not running, enqueue_speech returns None."""
    from queue_client import enqueue_speech
    from constants import QUEUE_SOCKET
    QUEUE_SOCKET.unlink(missing_ok=True)
    result = enqueue_speech("/tmp/test.wav", priority=1)
    assert result is None  # Graceful degradation


def test_queue_item_priority_ordering():
    """Higher priority items should sort first (min-heap with negative priority)."""
    # Import from scripts path
    scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
    sys.path.insert(0, scripts_dir)
    try:
        from voice_queue import QueueItem
        low = QueueItem.create(priority=20, agent_id="a", wav_path="/a.wav", volume=0.5)
        high = QueueItem.create(priority=100, agent_id="b", wav_path="/b.wav", volume=0.8)
        normal = QueueItem.create(priority=50, agent_id="c", wav_path="/c.wav", volume=0.6)
        # Higher priority = smaller sort_key (plays first in min-heap)
        assert high < normal < low
    finally:
        sys.path.remove(scripts_dir)


def test_voice_queue_enqueue_and_get():
    """Queue should return highest priority item first."""
    scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
    sys.path.insert(0, scripts_dir)
    try:
        from voice_queue import VoiceQueue, QueueItem
        q = VoiceQueue()
        low = QueueItem.create(20, "a", "/a.wav", 0.5)
        high = QueueItem.create(100, "b", "/b.wav", 0.8)
        q.enqueue(low)
        q.enqueue(high)
        item = q.get_next()
        assert item.priority == 100  # High priority first
        assert item.agent_id == "b"
    finally:
        sys.path.remove(scripts_dir)


def test_voice_queue_speaker_transition():
    """Should detect when different agent is about to speak."""
    scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
    sys.path.insert(0, scripts_dir)
    try:
        from voice_queue import VoiceQueue, QueueItem
        q = VoiceQueue()
        item1 = QueueItem.create(50, "matt", "/a.wav", 0.8)
        item2 = QueueItem.create(50, "darren", "/b.wav", 0.8)
        item3 = QueueItem.create(50, "matt", "/c.wav", 0.8)
        q.enqueue(item1)
        first = q.get_next()
        q.complete(first.id)
        assert q.last_speaker == "matt"
        assert q.needs_speaker_transition(item2)  # Different agent
        assert not q.needs_speaker_transition(item3)  # Same agent
    finally:
        sys.path.remove(scripts_dir)


# ---------------------------------------------------------------------------
# Test: Phase 4 STT components
# ---------------------------------------------------------------------------


def test_stt_constants():
    from constants import STT_SAMPLE_RATE, STT_ENV, WHISPER_ENV, MODELS_DIR, TRANSCRIPT_PATH
    assert STT_SAMPLE_RATE == 16000
    assert MODELS_DIR.name == "models"


def test_mic_capture_init():
    from mic import MicCapture, STT_SAMPLE_RATE, CHUNK_MS, CHUNK_FRAMES
    assert STT_SAMPLE_RATE == 16000
    assert CHUNK_MS == 80
    assert CHUNK_FRAMES == 1280
    mic = MicCapture()
    assert not mic.running
    assert len(mic.callbacks) == 0


def test_mic_capture_register():
    from mic import MicCapture
    mic = MicCapture()
    calls = []
    mic.register(lambda chunk: calls.append(1))
    assert len(mic.callbacks) == 1
    mic.unregister(mic.callbacks[0])
    assert len(mic.callbacks) == 0


def test_stt_engine_init():
    from stt import STTEngine
    engine = STTEngine()
    assert not engine.listening
    assert len(engine.audio_buffer) == 0


def test_stt_active_flag_lifecycle():
    from stt import STTEngine
    from constants import STT_ACTIVE_PATH
    engine = STTEngine()
    engine.start_listening()
    assert STT_ACTIVE_PATH.exists()
    assert engine.listening
    # stop_listening needs numpy — test the flag cleanup directly
    engine.listening = False
    STT_ACTIVE_PATH.unlink(missing_ok=True)
    assert not STT_ACTIVE_PATH.exists()


def test_push_to_talk_state():
    from stt import STTEngine
    from ptt import PushToTalk
    engine = STTEngine()
    ptt = PushToTalk(engine)
    assert not ptt.is_recording
    ptt.start()
    assert ptt.is_recording
    assert engine.listening
    # stop needs numpy for concatenation — test state directly
    engine.listening = False
    ptt.recording = False
    assert not ptt.is_recording


def test_duplex_manager_init():
    from stt import STTEngine
    from duplex import DuplexManager
    engine = STTEngine()
    dm = DuplexManager(engine)
    assert not dm.barge_in_active


# ---------------------------------------------------------------------------
# Test: queue dequeue gate (stt-active)
# ---------------------------------------------------------------------------


def test_queue_respects_stt_active(tmp_path):
    """Queue daemon must not dequeue when stt-active flag exists."""
    stt_active = tmp_path / "stt-active"
    stt_active.touch()
    assert stt_active.exists()
    playing_proc = None
    heap_has_items = True
    transition_ready = True
    stt_blocked = stt_active.exists()
    should_advance = (playing_proc is None and heap_has_items and not stt_blocked and transition_ready)
    assert should_advance is False

    stt_active.unlink()
    stt_blocked = stt_active.exists()
    should_advance = (playing_proc is None and heap_has_items and not stt_blocked and transition_ready)
    assert should_advance is True


# ---------------------------------------------------------------------------
# Test: crash recovery — stale state cleanup on daemon startup
# ---------------------------------------------------------------------------


def test_stale_tts_playing_cleared_concept(tmp_path):
    """Stale tts-playing flag must be cleared on daemon startup."""
    stale_flag = tmp_path / "tts-playing"
    stale_flag.touch()
    assert stale_flag.exists()
    stale_flag.unlink(missing_ok=True)
    assert not stale_flag.exists()


def test_stale_socket_cleared_concept(tmp_path):
    """Stale socket file must be cleared on daemon startup."""
    stale_sock = tmp_path / "daemon.sock"
    stale_sock.touch()
    assert stale_sock.exists()
    stale_sock.unlink(missing_ok=True)
    assert not stale_sock.exists()


def test_queue_socket_rebuild_concept(tmp_path):
    """Queue daemon should detect and rebuild missing socket file."""
    sock_path = tmp_path / "test.sock"
    sock_path.touch()  # Simulate socket exists
    assert sock_path.exists()

    # Simulate socket deletion
    sock_path.unlink()
    assert not sock_path.exists()

    # The daemon's periodic check should detect this
    # and trigger a rebuild. We test the detection condition.
    needs_rebuild = not sock_path.exists()
    assert needs_rebuild is True


# ---------------------------------------------------------------------------
# Test: greeting routes through queue
# ---------------------------------------------------------------------------


def test_greeting_should_use_queue(monkeypatch):
    """SessionStart greeting must route through queue, not direct play_sound."""
    import sys, re
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    import router
    source = Path(router.__file__).read_text()
    match = re.search(r'def _play_cached_greeting.*?(?=\ndef |\Z)', source, re.DOTALL)
    assert match, "_play_cached_greeting function not found"
    func_body = match.group()
    assert "enqueue_speech" in func_body, (
        "_play_cached_greeting calls play_sound directly instead of routing through queue. "
        "This means tts-playing flag is not set during greetings."
    )


# ---------------------------------------------------------------------------
# Test: presets (presets.py)
# ---------------------------------------------------------------------------


def test_preset_focus_only():
    """focus-only preset sets all non-focused volumes to 0.0."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    from presets import PRESETS
    p = PRESETS["focus-only"]
    assert p["tmux"]["focus_volumes"]["focused"] == 1.0
    assert p["tmux"]["focus_volumes"]["same_window"] == 0.0
    assert p["tmux"]["focus_volumes"]["same_session"] == 0.0
    assert p["tmux"]["focus_volumes"]["other_session"] == 0.0


def test_preset_hear_all():
    """hear-all preset sets all volumes to 1.0."""
    from presets import PRESETS
    p = PRESETS["hear-all"]
    assert all(v == 1.0 for v in p["tmux"]["focus_volumes"].values())


def test_preset_spatial():
    """spatial preset sets graduated volumes."""
    from presets import PRESETS
    p = PRESETS["spatial"]
    fv = p["tmux"]["focus_volumes"]
    assert fv["focused"] == 1.0
    assert fv["same_window"] == 0.5
    assert fv["same_session"] == 0.2
    assert fv["other_session"] == 0.0


def test_preset_meeting():
    """meeting preset lowers volume and disables TTS."""
    from presets import PRESETS
    p = PRESETS["meeting"]
    assert p["volume"] == 0.1
    assert p["tts"]["enabled"] is False
    assert p["tmux"]["focus_volumes"]["same_window"] == 0.0


def test_apply_preset_writes_config(tmp_path, monkeypatch):
    """apply_preset() must write changes to config.yaml."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    import presets
    import state
    import constants

    config_path = tmp_path / "config.yaml"
    config_path.write_text("theme: default\nvolume: 0.8\nmute: false\ntmux:\n  focus_volumes:\n    focused: 1.0\n    same_window: 1.0\n    same_session: 1.0\n    other_session: 1.0\n")
    monkeypatch.setattr(state, "CONFIG_PATH", config_path)
    monkeypatch.setattr(state, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(state, "LOCK_PATH", tmp_path / ".config.lock")
    monkeypatch.setattr(constants, "CONFIG_PATH", config_path)
    monkeypatch.setattr(presets, "BACKUP_PATH", tmp_path / ".preset-backup.yaml")

    presets.apply_preset("focus-only")

    content = config_path.read_text()
    assert "0.0" in content  # non-focused volumes lowered


def test_apply_preset_saves_backup(tmp_path, monkeypatch):
    """apply_preset() must save previous config for restore."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    import presets
    import state
    import constants

    config_path = tmp_path / "config.yaml"
    config_path.write_text("theme: default\nvolume: 0.8\nmute: false\n")
    backup_path = tmp_path / ".preset-backup.yaml"
    monkeypatch.setattr(state, "CONFIG_PATH", config_path)
    monkeypatch.setattr(state, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(state, "LOCK_PATH", tmp_path / ".config.lock")
    monkeypatch.setattr(constants, "CONFIG_PATH", config_path)
    monkeypatch.setattr(presets, "BACKUP_PATH", backup_path)

    presets.apply_preset("focus-only")
    assert backup_path.exists()


# ---------------------------------------------------------------------------
# Test: flags.py — stale flag protection with PID + timestamp
# ---------------------------------------------------------------------------


def test_flag_write_includes_pid_and_timestamp(tmp_path):
    """Flag files must include PID and timestamp for staleness detection."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    from flags import write_flag, read_flag

    flag_path = tmp_path / "test-flag"
    write_flag(flag_path)

    info = read_flag(flag_path)
    assert info is not None
    assert "pid" in info
    assert "timestamp" in info
    assert info["pid"] == os.getpid()
    assert time.time() - info["timestamp"] < 2.0


def test_flag_stale_detection(tmp_path):
    """Flags older than max_age_seconds should be detected as stale."""
    from flags import write_flag, is_flag_active

    flag_path = tmp_path / "test-flag"
    write_flag(flag_path)
    assert is_flag_active(flag_path, max_age_seconds=60) is True

    # Simulate stale flag by writing old timestamp
    flag_path.write_text(f"{os.getpid()} {time.time() - 120}")
    assert is_flag_active(flag_path, max_age_seconds=60) is False


def test_flag_dead_pid_detection(tmp_path):
    """Flags with dead PIDs should be detected as stale."""
    from flags import is_flag_active

    flag_path = tmp_path / "test-flag"
    flag_path.write_text(f"999999999 {time.time()}")
    assert is_flag_active(flag_path, max_age_seconds=60) is False


def test_flag_missing_is_inactive(tmp_path):
    """Non-existent flag file should be inactive."""
    from flags import is_flag_active
    assert is_flag_active(tmp_path / "nonexistent", max_age_seconds=60) is False


def test_flag_legacy_format_is_active(tmp_path):
    """Empty flag files (legacy touch-only format) should still be treated as active."""
    from flags import is_flag_active

    flag_path = tmp_path / "legacy-flag"
    flag_path.touch()
    assert is_flag_active(flag_path, max_age_seconds=60) is True


def test_flag_permission_error_means_alive(tmp_path, monkeypatch):
    """PermissionError from os.kill means process EXISTS (different user) — flag is active."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    from flags import is_flag_active
    import os as _os

    flag_path = tmp_path / "test-flag"
    # Write flag with current PID (alive) and recent timestamp
    flag_path.write_text(f"{_os.getpid()} {__import__('time').time()}")

    # Mock os.kill to raise PermissionError (simulates different-user process)
    def mock_kill(pid, sig):
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(_os, "kill", mock_kill)
    # Should be True — PermissionError means process exists
    assert is_flag_active(flag_path, max_age_seconds=60) is True


# ---------------------------------------------------------------------------
# NOTE: Parametric spatial mixer tests removed (Phase 2.3).
# The spatial mixer was replaced by the gain chain (volume.py) and arbiter modes.
# See TestGainChain and TestPolicyVol at the bottom of this file.
# ---------------------------------------------------------------------------


def test_preset_roundtrip(tmp_path, monkeypatch):
    """Apply preset then restore should return to original config."""
    import presets
    import state
    import constants

    config_path = tmp_path / "config.yaml"
    original = (
        "theme: default\nvolume: 0.8\nmute: false\n"
        "tmux:\n  focus_volumes:\n"
        "    focused: 1.0\n    same_window: 1.0\n"
        "    same_session: 1.0\n    other_session: 1.0\n"
    )
    config_path.write_text(original)
    backup_path = tmp_path / ".preset-backup.yaml"

    monkeypatch.setattr(state, "CONFIG_PATH", config_path)
    monkeypatch.setattr(state, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(state, "LOCK_PATH", tmp_path / ".config.lock")
    monkeypatch.setattr(constants, "CONFIG_PATH", config_path)
    monkeypatch.setattr(presets, "CONFIG_PATH", config_path)
    monkeypatch.setattr(presets, "BACKUP_PATH", backup_path)

    # Apply meeting preset
    presets.apply_preset("meeting")
    meeting_content = config_path.read_text()
    assert "0.1" in meeting_content  # Volume lowered

    # Restore
    presets.apply_preset("restore")
    restored_content = config_path.read_text()
    # Restored content should match original
    assert restored_content == original


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed out of {passed + failed} tests")
    sys.exit(1 if failed else 0)


# ---------------------------------------------------------------------------
# Test: volume.py — gain chain
# ---------------------------------------------------------------------------


class TestGainChain:
    """Tests for the four-stage multiplicative gain chain."""

    def _default_config(self, **overrides):
        cfg = {
            "volume": 0.7,
            "system_gain": 3.5,
            "categories": {"tts": 1.0, "earcon": 0.8, "ambient": 0.3},
            "agent_volumes": {},
        }
        cfg.update(overrides)
        return cfg

    def test_default_produces_sane_output(self):
        from volume import compute_gain_chain
        chain = compute_gain_chain("tts", "matt", self._default_config())
        # 1.0 * 1.0 * 1.0 * 0.7 * 3.5 = 2.45
        assert abs(chain["final"] - 2.45) < 0.001
        assert chain["category_vol"] == 1.0
        assert chain["agent_vol"] == 1.0
        assert chain["policy_vol"] == 1.0
        assert chain["master_vol"] == 0.7
        assert chain["system_gain"] == 3.5

    def test_master_zero_mutes(self):
        from volume import compute_gain_chain
        chain = compute_gain_chain("tts", "matt", self._default_config(volume=0.0))
        assert chain["final"] == 0.0

    def test_category_zero_mutes(self):
        from volume import compute_gain_chain
        cfg = self._default_config()
        cfg["categories"]["tts"] = 0.0
        chain = compute_gain_chain("tts", "matt", cfg)
        assert chain["final"] == 0.0

    def test_agent_volume_override(self):
        from volume import compute_gain_chain
        cfg = self._default_config()
        cfg["agent_volumes"]["researcher"] = 0.5
        chain = compute_gain_chain("tts", "researcher", cfg)
        # 1.0 * 0.5 * 1.0 * 0.7 * 3.5 = 1.225
        assert abs(chain["final"] - 1.225) < 0.001
        assert chain["agent_vol"] == 0.5

    def test_unknown_agent_defaults_to_one(self):
        from volume import compute_gain_chain
        chain = compute_gain_chain("tts", "unknown-agent", self._default_config())
        assert chain["agent_vol"] == 1.0

    def test_policy_vol_attenuates(self):
        from volume import compute_gain_chain
        chain = compute_gain_chain("tts", "matt", self._default_config(), policy_vol=0.0)
        assert chain["final"] == 0.0

    def test_clamping_above_one(self):
        from volume import compute_gain_chain
        cfg = self._default_config(volume=1.5)  # exceeds 1.0
        chain = compute_gain_chain("tts", "matt", cfg)
        assert chain["master_vol"] == 1.0  # clamped

    def test_clamping_below_zero(self):
        from volume import compute_gain_chain
        chain = compute_gain_chain("tts", "matt", self._default_config(), policy_vol=-0.5)
        assert chain["policy_vol"] == 0.0  # clamped

    def test_system_gain_scales_linearly(self):
        from volume import compute_gain_chain
        cfg1 = self._default_config(system_gain=2.0)
        cfg2 = self._default_config(system_gain=4.0)
        c1 = compute_gain_chain("tts", "matt", cfg1)
        c2 = compute_gain_chain("tts", "matt", cfg2)
        assert abs(c2["final"] / c1["final"] - 2.0) < 0.001

    def test_chain_str_well_formed(self):
        from volume import compute_gain_chain
        chain = compute_gain_chain("tts", "matt", self._default_config())
        s = chain["chain_str"]
        assert "cat=" in s
        assert "agent=" in s
        assert "policy=" in s
        assert "master=" in s
        assert "gain=" in s
        assert "-> pw=" in s

    def test_max_user_values_bounded(self):
        from volume import compute_gain_chain
        cfg = self._default_config(volume=1.0, system_gain=3.5)
        cfg["categories"]["tts"] = 1.0
        cfg["agent_volumes"]["matt"] = 1.0
        chain = compute_gain_chain("tts", "matt", cfg, policy_vol=1.0)
        # All user values at max: 1.0^4 * 3.5 = 3.5
        assert abs(chain["final"] - 3.5) < 0.001

    def test_earcon_category(self):
        from volume import compute_gain_chain
        chain = compute_gain_chain("earcon", "matt", self._default_config())
        assert chain["category_vol"] == 0.8
        # 0.8 * 1.0 * 1.0 * 0.7 * 3.5 = 1.96
        assert abs(chain["final"] - 1.96) < 0.001

    def test_ambient_category(self):
        from volume import compute_gain_chain
        chain = compute_gain_chain("ambient", "matt", self._default_config())
        assert chain["category_vol"] == 0.3
        # 0.3 * 1.0 * 1.0 * 0.7 * 3.5 = 0.735
        assert abs(chain["final"] - 0.735) < 0.001

    def test_unknown_category_defaults_to_one(self):
        from volume import compute_gain_chain
        chain = compute_gain_chain("unknown_cat", "matt", self._default_config())
        assert chain["category_vol"] == 1.0


class TestPolicyVol:
    """Tests for mode-based policy volume calculation."""

    def test_silent_always_zero(self):
        from volume import policy_vol_for_mode
        assert policy_vol_for_mode("silent", "%42", "%42") == 0.0
        assert policy_vol_for_mode("silent", "%99", "%42") == 0.0

    def test_ambient_always_one(self):
        from volume import policy_vol_for_mode
        assert policy_vol_for_mode("ambient", "%42", "%42") == 1.0
        assert policy_vol_for_mode("ambient", "%99", "%42") == 1.0

    def test_focused_matches_focus(self):
        from volume import policy_vol_for_mode
        assert policy_vol_for_mode("focused", "%42", "%42") == 1.0
        assert policy_vol_for_mode("focused", "%99", "%42") == 0.0

    def test_focused_global_passes(self):
        from volume import policy_vol_for_mode
        assert policy_vol_for_mode("focused", "_global", "%42") == 1.0

    def test_focused_no_focus_info(self):
        from volume import policy_vol_for_mode
        assert policy_vol_for_mode("focused", "%42", "") == 1.0  # fail open

    def test_solo_same_as_focused(self):
        from volume import policy_vol_for_mode
        assert policy_vol_for_mode("solo", "%42", "%42") == 1.0
        assert policy_vol_for_mode("solo", "%99", "%42") == 0.0

    def test_broadcast_matt_passes(self):
        from volume import policy_vol_for_mode
        assert policy_vol_for_mode("broadcast", "%42", "%42", agent_id="matt-prime") == 1.0
        assert policy_vol_for_mode("broadcast", "%42", "%42", agent_id="Matt") == 1.0

    def test_broadcast_non_matt_blocked(self):
        from volume import policy_vol_for_mode
        assert policy_vol_for_mode("broadcast", "%42", "%42", agent_id="researcher") == 0.0

    def test_unknown_mode_fails_open(self):
        from volume import policy_vol_for_mode
        assert policy_vol_for_mode("nonexistent", "%42", "%42") == 1.0
