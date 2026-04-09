#!/usr/bin/env -S uv run --with panel --with pandas --with param
"""Voice Explorer POC — Panel version.

Run: uv run --with panel --with pandas --with param panel serve notebooks/poc_panel.py --port 5013 --show
"""
import json
import pandas as pd
import panel as pn
from pathlib import Path

pn.extension("tabulator", sizing_mode="stretch_width")

# Load sound library
LIB = Path.home() / ".claude/local/voice/sound_library.json"
df = pd.DataFrame(json.loads(LIB.read_text()))

# State file
STATE = Path.home() / ".claude/local/voice/speaking-now.json"

# Widgets
theme_select = pn.widgets.Select(name="Theme", options=["all"] + sorted(df["theme"].unique().tolist()), value="all")
cat_select = pn.widgets.Select(name="Category", options=["all"] + sorted(df["slot"].unique().tolist()), value="all")

# Audio player (persistent)
audio_player = pn.pane.Audio(None, name="Player")
now_playing = pn.pane.Markdown("*Click a row to play*")
volume_slider = pn.widgets.IntSlider(name="Volume", value=50, start=0, end=100)

def bind_volume(vol):
    audio_player.volume = vol
pn.bind(bind_volume, volume_slider, watch=True)

# Table
display_cols = ["theme", "slot", "variant", "name", "duration_ms", "peak_db", "rms_db"]
table = pn.widgets.Tabulator(
    df[display_cols], height=400, show_index=False,
    selectable=1, pagination="local", page_size=20,
    sizing_mode="stretch_both",
)

# Filter logic
def update_table(*args):
    filtered = df.copy()
    if theme_select.value != "all":
        filtered = filtered[filtered["theme"] == theme_select.value]
    if cat_select.value != "all":
        filtered = filtered[filtered["slot"] == cat_select.value]
    table.value = filtered[display_cols]

theme_select.param.watch(update_table, "value")
cat_select.param.watch(update_table, "value")

# Click to play
_full_df = {"df": df}
def on_click(event):
    filtered = df.copy()
    if theme_select.value != "all":
        filtered = filtered[filtered["theme"] == theme_select.value]
    if cat_select.value != "all":
        filtered = filtered[filtered["slot"] == cat_select.value]
    filtered = filtered.reset_index(drop=True)
    if event.row < len(filtered):
        row = filtered.iloc[event.row]
        audio_player.object = row["path"]
        audio_player.paused = False
        now_playing.object = f"**{row['theme']}** / **{row['name']}** | {row['duration_ms']}ms | {row['rms_db']}dB RMS"

table.on_click(on_click)

# Voice state (polling)
state_md = pn.pane.Markdown("*Loading...*")
def update_state():
    try:
        data = json.loads(STATE.read_text().strip()) if STATE.exists() else {}
        speaking = data.get("speaking_pane") or "none"
        stt = "active" if data.get("stt_active") else "idle"
        state_md.object = f"**Speaking**: {speaking} | **STT**: {stt} | **Muted**: {data.get('muted', False)}"
    except:
        pass

pn.state.add_periodic_callback(update_state, period=1000)

# Layout
template = pn.template.FastListTemplate(
    title="Voice Explorer (Panel)",
    sidebar=[
        pn.Card(audio_player, now_playing, volume_slider, title="Player"),
        theme_select, cat_select,
    ],
    main=[
        pn.Card(state_md, title="Voice State"),
        pn.Card(table, title="Sound Library", sizing_mode="stretch_both"),
    ],
    accent_base_color="#89b4fa",
    header_background="#262626",
    theme="dark",
)
template.servable(title="Voice Explorer — Panel")
