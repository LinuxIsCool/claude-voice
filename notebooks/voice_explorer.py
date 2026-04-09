"""Voice System Explorer — Interactive audio dashboard.

Run with: panel serve notebooks/voice_explorer.py --show
Or in Jupyter: from voice_explorer import app; app.servable()

Data sources:
  - Sound Library: 254 WAVs across 7 themes
  - Voice Events: queue.log + daemon.log (streaming)
  - STT Events: journalctl voice-stt (streaming)
  - Voice State: speaking-now.json (polling)
  - Gain Chain: config.yaml (interactive)
"""
from __future__ import annotations

import io
import json
import os
import struct
import subprocess
import time
import wave
from math import log10, sqrt
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import param
import panel as pn

pn.extension("tabulator", sizing_mode="stretch_width")

# ── Paths ──────────────────────────────────────────────────────────────

VOICE_DIR = Path("~/.claude/local/voice").expanduser()
THEMES_DIR = Path("~/.claude/plugins/local/legion-plugins/plugins/claude-voice/assets/themes").expanduser()
QUEUE_LOG = VOICE_DIR / "queue.log"
DAEMON_LOG = VOICE_DIR / "daemon.log"
STATE_FILE = VOICE_DIR / "speaking-now.json"
CONFIG_FILE = VOICE_DIR / "config.yaml"


# ── Audio Analysis ─────────────────────────────────────────────────────

def analyze_wav(path: Path) -> dict:
    """Analyze a WAV file. Returns metadata dict. Pure stdlib + numpy."""
    try:
        with wave.open(str(path), "rb") as wf:
            nframes = wf.getnframes()
            nchan = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            rate = wf.getframerate()
            raw = wf.readframes(nframes)

        duration_ms = int(nframes / rate * 1000)

        # Parse PCM samples
        if sampwidth == 2:
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
            norm = 32767.0
        elif sampwidth == 4:
            samples = np.frombuffer(raw, dtype=np.int32).astype(np.float64)
            norm = 2147483647.0
        else:
            return {"error": f"unsupported sample width {sampwidth}"}

        peak = np.max(np.abs(samples)) / norm
        rms = sqrt(np.mean(samples ** 2)) / norm
        peak_db = 20 * log10(max(peak, 1e-10))
        rms_db = 20 * log10(max(rms, 1e-10))

        return {
            "duration_ms": duration_ms,
            "sample_rate": rate,
            "channels": nchan,
            "peak": round(peak, 4),
            "peak_db": round(peak_db, 1),
            "rms": round(rms, 4),
            "rms_db": round(rms_db, 1),
            "samples": len(samples),
        }
    except Exception as e:
        return {"error": str(e)}


# ── Sound Library ──────────────────────────────────────────────────────

class SoundLibrary(param.Parameterized):
    """Static library of all WAV files across all themes."""

    theme = param.Selector(default="all", doc="Filter by theme")
    category = param.Selector(default="all", doc="Filter by sound category")
    selected_sound = param.String(default="", doc="Path of selected sound")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._build_library()

    def _build_library(self):
        """Scan all themes and build the sound DataFrame."""
        rows = []
        themes = sorted([d.name for d in THEMES_DIR.iterdir() if d.is_dir()])

        for theme in themes:
            sounds_dir = THEMES_DIR / theme / "sounds"
            if not sounds_dir.exists():
                continue
            for wav in sorted(sounds_dir.glob("*.wav")):
                # Parse: "notification-01.wav" → slot="notification", variant=1
                name = wav.stem
                parts = name.rsplit("-", 1)
                slot = parts[0] if len(parts) > 1 else name
                variant = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0

                analysis = analyze_wav(wav)
                rows.append({
                    "theme": theme,
                    "slot": slot,
                    "variant": variant,
                    "name": name,
                    "path": str(wav),
                    **{k: v for k, v in analysis.items() if k != "error"},
                })

        self._full_df = pd.DataFrame(rows)
        self.param.theme.objects = ["all"] + themes
        categories = sorted(self._full_df["slot"].unique().tolist())
        self.param.category.objects = ["all"] + categories

    @param.depends("theme", "category")
    def filtered_df(self) -> pd.DataFrame:
        df = self._full_df.copy()
        if self.theme != "all":
            df = df[df["theme"] == self.theme]
        if self.category != "all":
            df = df[df["slot"] == self.category]
        return df

    def play(self, path: str):
        """Play a WAV file via pw-play."""
        try:
            subprocess.Popen(
                ["pw-play", "--volume=0.5", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            pass


# ── Voice Event Stream ─────────────────────────────────────────────────

class VoiceEventStream(param.Parameterized):
    """Streams voice events from queue.log and daemon.log."""

    max_rows = param.Integer(default=100, doc="Maximum rows to keep")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._queue_pos = 0
        self._daemon_pos = 0
        self.queue_df = pd.DataFrame(columns=["timestamp", "event", "item_id", "agent", "volume", "priority"])
        self.daemon_df = pd.DataFrame(columns=["timestamp", "event", "text", "wav"])

    def poll(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Read new lines from log files. Returns (queue_events, daemon_events)."""
        self.queue_df = self._read_queue_log()
        self.daemon_df = self._read_daemon_log()
        return self.queue_df, self.daemon_df

    def _read_queue_log(self) -> pd.DataFrame:
        if not QUEUE_LOG.exists():
            return self.queue_df
        try:
            text = QUEUE_LOG.read_text()
            lines = text.splitlines()
            new_lines = lines[self._queue_pos:]
            self._queue_pos = len(lines)

            rows = []
            for line in new_lines:
                if "enqueued:" in line or "playing:" in line or "playback complete:" in line:
                    ts = line[:19]
                    if "enqueued:" in line:
                        event = "enqueued"
                    elif "playing:" in line:
                        event = "playing"
                    else:
                        event = "complete"
                    # Extract item_id
                    item_id = ""
                    for part in line.split():
                        if part.startswith("vq-"):
                            item_id = part
                            break
                    # Extract agent
                    agent = ""
                    if "[" in line and "]" in line:
                        agent = line[line.index("[") + 1:line.index("]")]
                    # Extract volume
                    vol = ""
                    if "vol=" in line:
                        vol = line.split("vol=")[1].split()[0]

                    rows.append({"timestamp": ts, "event": event, "item_id": item_id,
                                 "agent": agent, "volume": vol, "priority": ""})

            if rows:
                new_df = pd.DataFrame(rows)
                self.queue_df = pd.concat([self.queue_df, new_df], ignore_index=True).tail(self.max_rows)
        except Exception:
            pass
        return self.queue_df

    def _read_daemon_log(self) -> pd.DataFrame:
        if not DAEMON_LOG.exists():
            return self.daemon_df
        try:
            text = DAEMON_LOG.read_text()
            lines = text.splitlines()
            new_lines = lines[self._daemon_pos:]
            self._daemon_pos = len(lines)

            rows = []
            for line in new_lines:
                ts = line[:19]
                if "synthesized" in line:
                    event = "synthesized"
                    text_preview = line.split("'")[1][:40] if "'" in line else ""
                    wav_name = line.split("→")[-1].strip() if "→" in line else ""
                    rows.append({"timestamp": ts, "event": event, "text": text_preview, "wav": wav_name})
                elif "enqueued in voice queue" in line:
                    item_id = line.split(":")[-1].strip()
                    rows.append({"timestamp": ts, "event": "enqueued", "text": "", "wav": item_id})

            if rows:
                new_df = pd.DataFrame(rows)
                self.daemon_df = pd.concat([self.daemon_df, new_df], ignore_index=True).tail(self.max_rows)
        except Exception:
            pass
        return self.daemon_df


# ── Voice State ────────────────────────────────────────────────────────

class VoiceState(param.Parameterized):
    """Current voice system state from speaking-now.json."""

    speaking_pane = param.String(default="")
    stt_active = param.Boolean(default=False)
    muted = param.Boolean(default=False)
    focused_pane = param.String(default="")
    queued = param.Dict(default={})

    def poll(self):
        """Read current state."""
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text().strip())
                self.speaking_pane = data.get("speaking_pane") or ""
                self.stt_active = data.get("stt_active", False)
                self.muted = data.get("muted", False)
                self.focused_pane = data.get("focused_pane", "")
                self.queued = data.get("queued", {})
        except Exception:
            pass


# ── Dashboard ──────────────────────────────────────────────────────────

def build_dashboard():
    """Build the Panel dashboard."""
    library = SoundLibrary()
    events = VoiceEventStream()
    state = VoiceState()

    # ── Audio Player (persistent, shared) ──
    audio_player = pn.pane.Audio(None, name="Player")
    volume_slider = pn.widgets.IntSlider(name="Preview Volume", value=50, start=0, end=100)
    now_playing_md = pn.pane.Markdown("*Click a sound to play*")

    def _bind_volume(vol):
        audio_player.volume = vol
    pn.bind(_bind_volume, volume_slider, watch=True)

    # ── Sound Library Panel ──
    theme_select = pn.widgets.Select.from_param(library.param.theme, name="Theme")
    category_select = pn.widgets.Select.from_param(library.param.category, name="Category")

    # The table needs to be a stable widget (not recreated on filter)
    # so on_click stays wired. We update its .value instead.
    sound_tab = pn.widgets.Tabulator(
        library.filtered_df()[["theme", "slot", "variant", "name", "duration_ms", "peak_db", "rms_db"]],
        height=400, show_index=False, selectable=1,
        pagination="local", page_size=20, sizing_mode="stretch_both",
    )

    # Keep a reference to the full (with path) DataFrame for lookup
    _full_ref = {"df": library.filtered_df()}

    def _update_table(*args):
        df = library.filtered_df()
        _full_ref["df"] = df
        sound_tab.value = df[["theme", "slot", "variant", "name", "duration_ms", "peak_db", "rms_db"]]

    library.param.watch(_update_table, ["theme", "category"])

    def on_row_click(event):
        """Play the clicked sound in the browser audio player."""
        df = _full_ref["df"]
        if event.row < len(df):
            row = df.iloc[event.row]
            path = row["path"]
            name = row["name"]
            theme = row["theme"]
            audio_player.object = path
            audio_player.paused = False
            now_playing_md.object = f"**Now Playing**: {theme} / {name} ({row.get('duration_ms', '?')}ms, {row.get('rms_db', '?')}dB RMS)"

    sound_tab.on_click(on_row_click)

    # ── Voice State Indicator ──
    state_md = pn.pane.Markdown("*Loading...*")

    def update_state():
        state.poll()

        if state.muted:
            state_md.object = "## Voice State\n\n**MUTED**"
        else:
            parts = [f"## Voice State"]
            speaking = state.speaking_pane or "none"
            parts.append(f"**Speaking**: {speaking}")
            parts.append(f"**STT**: {'active' if state.stt_active else 'idle'}")
            parts.append(f"**Focus**: {state.focused_pane or '?'}")
            if state.queued:
                parts.append(f"**Queued**: {state.queued}")
            state_md.object = "\n\n".join(parts)

    pn.state.add_periodic_callback(update_state, period=500)

    # ── Event Stream Tables ──
    queue_table = pn.widgets.Tabulator(
        events.queue_df, height=200, show_index=False,
        pagination=None, name="Queue Events", sizing_mode="stretch_both",
    )
    daemon_table = pn.widgets.Tabulator(
        events.daemon_df, height=200, show_index=False,
        pagination=None, name="TTS Events", sizing_mode="stretch_both",
    )

    def update_tables():
        events.poll()
        if not events.queue_df.empty:
            queue_table.value = events.queue_df
        if not events.daemon_df.empty:
            daemon_table.value = events.daemon_df

    pn.state.add_periodic_callback(update_tables, period=1000)

    # ── Layout ──
    player_card = pn.Card(
        audio_player,
        now_playing_md,
        volume_slider,
        title="Audio Player",
        collapsed=False,
    )

    sidebar = pn.Column(
        "# Voice Explorer",
        player_card,
        "---",
        "## Sound Library",
        pn.Row(theme_select, category_select),
        sound_tab,
        width=600,
    )

    main = pn.Column(
        state_md,
        "---",
        "## Queue Events (live)",
        queue_table,
        "---",
        "## TTS Events (live)",
        daemon_table,
    )

    template = pn.template.FastListTemplate(
        title="Voice Explorer",
        sidebar=[player_card, "---", theme_select, category_select],
        main=[
            pn.Card(sound_tab, title="Sound Library", collapsed=False, sizing_mode="stretch_both"),
            pn.Row(
                pn.Card(state_md, title="Voice State", sizing_mode="stretch_both"),
                sizing_mode="stretch_width",
            ),
            pn.Card(queue_table, title="Queue Events (live)", collapsed=False, collapsible=True, sizing_mode="stretch_both"),
            pn.Card(daemon_table, title="TTS Events (live)", collapsed=False, collapsible=True, sizing_mode="stretch_both"),
        ],
        accent_base_color="#89b4fa",
        header_background="#262626",
        theme="dark",
    )
    return template


# ── App ────────────────────────────────────────────────────────────────

app = build_dashboard()

if __name__ == "__main__" or __name__.startswith("bokeh"):
    app.servable(title="Voice System Explorer")
