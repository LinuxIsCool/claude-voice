#!/usr/bin/env -S uv run --with gradio --with pandas
"""Voice Explorer POC — Gradio version.

Run: uv run --with gradio --with pandas notebooks/poc_gradio.py
"""
import json
import pandas as pd
import gradio as gr
from pathlib import Path

# Load sound library
LIB = Path.home() / ".claude/local/voice/sound_library.json"
df = pd.DataFrame(json.loads(LIB.read_text()))

# State file
STATE = Path.home() / ".claude/local/voice/speaking-now.json"

def get_voice_state():
    try:
        data = json.loads(STATE.read_text().strip()) if STATE.exists() else {}
        return f"Speaking: {data.get('speaking_pane', 'none')} | STT: {'active' if data.get('stt_active') else 'idle'} | Muted: {data.get('muted', False)}"
    except:
        return "State unavailable"

def filter_sounds(theme, category):
    filtered = df.copy()
    if theme != "all":
        filtered = filtered[filtered["theme"] == theme]
    if category != "all":
        filtered = filtered[filtered["slot"] == category]
    return filtered[["theme", "slot", "variant", "name", "duration_ms", "peak_db", "rms_db", "path"]]

def play_sound(evt: gr.SelectData, filtered_df):
    if evt.index[0] < len(filtered_df):
        path = filtered_df.iloc[evt.index[0]]["path"]
        name = filtered_df.iloc[evt.index[0]]["name"]
        return path, f"Playing: {name}"
    return None, "No selection"

themes = ["all"] + sorted(df["theme"].unique().tolist())
categories = ["all"] + sorted(df["slot"].unique().tolist())

with gr.Blocks(title="Voice Explorer — Gradio", theme=gr.themes.Soft(primary_hue="blue")) as app:
    gr.Markdown("# Voice Explorer (Gradio POC)")

    with gr.Row():
        theme_dd = gr.Dropdown(choices=themes, value="all", label="Theme")
        cat_dd = gr.Dropdown(choices=categories, value="all", label="Category")
        state_txt = gr.Textbox(label="Voice State", value=get_voice_state(), interactive=False)

    sound_table = gr.DataFrame(
        value=filter_sounds("all", "all"),
        label="Sound Library",
        interactive=False,
    )

    with gr.Row():
        audio_player = gr.Audio(label="Player", type="filepath", autoplay=True)
        now_playing = gr.Textbox(label="Now Playing", interactive=False)

    # Wire events
    theme_dd.change(filter_sounds, [theme_dd, cat_dd], sound_table)
    cat_dd.change(filter_sounds, [theme_dd, cat_dd], sound_table)
    sound_table.select(play_sound, [sound_table], [audio_player, now_playing])

app.launch(server_port=5012, share=False)
