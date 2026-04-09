#!/usr/bin/env -S uv run --with streamlit --with pandas
"""Voice Explorer POC — Streamlit version.

Run: uv run --with streamlit --with pandas streamlit run notebooks/poc_streamlit.py --server.port 5014
"""
import json
import pandas as pd
import streamlit as st
from pathlib import Path

st.set_page_config(page_title="Voice Explorer — Streamlit", layout="wide")

# Load sound library
LIB = Path.home() / ".claude/local/voice/sound_library.json"
df = pd.DataFrame(json.loads(LIB.read_text()))

# State file
STATE = Path.home() / ".claude/local/voice/speaking-now.json"

st.title("Voice Explorer (Streamlit POC)")

# Voice state
try:
    data = json.loads(STATE.read_text().strip()) if STATE.exists() else {}
    speaking = data.get("speaking_pane") or "none"
    stt = "active" if data.get("stt_active") else "idle"
    col1, col2, col3 = st.columns(3)
    col1.metric("Speaking", speaking)
    col2.metric("STT", stt)
    col3.metric("Muted", str(data.get("muted", False)))
except:
    st.info("Voice state unavailable")

# Filters
col_a, col_b = st.columns(2)
theme = col_a.selectbox("Theme", ["all"] + sorted(df["theme"].unique().tolist()))
category = col_b.selectbox("Category", ["all"] + sorted(df["slot"].unique().tolist()))

# Filter
filtered = df.copy()
if theme != "all":
    filtered = filtered[filtered["theme"] == theme]
if category != "all":
    filtered = filtered[filtered["slot"] == category]

# Sound table
st.subheader(f"Sound Library ({len(filtered)} sounds)")
display_df = filtered[["theme", "slot", "variant", "name", "duration_ms", "peak_db", "rms_db"]].reset_index(drop=True)

# Streamlit data_editor with selection
event = st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
)

# Play selected sound
if event and event.selection and event.selection.rows:
    idx = event.selection.rows[0]
    if idx < len(filtered):
        row = filtered.iloc[idx]
        st.subheader(f"Playing: {row['theme']} / {row['name']}")
        st.caption(f"{row['duration_ms']}ms | peak {row['peak_db']}dB | rms {row['rms_db']}dB")
        st.audio(row["path"], format="audio/wav", autoplay=True)

# Stats
with st.expander("Library Stats"):
    st.write(f"**{len(df)}** sounds across **{df['theme'].nunique()}** themes")
    theme_stats = df.groupby("theme").agg(count=("name", "count"), avg_rms=("rms_db", "mean")).round(1)
    st.dataframe(theme_stats)
