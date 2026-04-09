import marimo

__generated_with = "0.22.4"
app = marimo.App(width="full")


@app.cell
def _(mo):
    mo.md("""
    # Voice Explorer
    """)
    return


@app.cell
def _():
    import marimo as mo
    import numpy as np
    import pandas as pd
    import wave
    from math import log10, sqrt
    from pathlib import Path
    import json
    import subprocess

    return Path, json, log10, mo, np, pd, sqrt, wave


@app.cell
def _(Path):
    VOICE_DIR = Path.home() / ".claude" / "local" / "voice"
    THEMES_DIR = Path.home() / ".claude" / "plugins" / "local" / "legion-plugins" / "plugins" / "claude-voice" / "assets" / "themes"
    return THEMES_DIR, VOICE_DIR


@app.cell
def _(Path, log10, np, sqrt, wave):
    def analyze_wav(path: Path) -> dict:
        """Analyze a WAV file — duration, peak, RMS."""
        try:
            with wave.open(str(path), "rb") as wf:
                nframes = wf.getnframes()
                nchan = wf.getnchannels()
                rate = wf.getframerate()
                sampwidth = wf.getsampwidth()
                raw = wf.readframes(nframes)
            duration_ms = int(nframes / rate * 1000)
            if sampwidth == 2:
                samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
                norm = 32767.0
            else:
                return {"duration_ms": 0, "peak_db": 0, "rms_db": 0}
            peak = np.max(np.abs(samples)) / norm
            rms = sqrt(np.mean(samples ** 2)) / norm
            return {
                "duration_ms": duration_ms,
                "channels": nchan,
                "sample_rate": rate,
                "peak": round(peak, 4),
                "peak_db": round(20 * log10(max(peak, 1e-10)), 1),
                "rms": round(rms, 4),
                "rms_db": round(20 * log10(max(rms, 1e-10)), 1),
            }
        except Exception as e:
            return {"duration_ms": 0, "peak_db": 0, "rms_db": 0, "error": str(e)}

    return (analyze_wav,)


@app.cell
def _(THEMES_DIR, analyze_wav, pd):
    """Build the sound library DataFrame."""
    rows = []
    for theme_dir in sorted(THEMES_DIR.iterdir()):
        if not theme_dir.is_dir():
            continue
        sounds_dir = theme_dir / "sounds"
        if not sounds_dir.exists():
            continue
        for wav in sorted(sounds_dir.glob("*.wav")):
            name = wav.stem
            parts = name.rsplit("-", 1)
            slot = parts[0] if len(parts) > 1 else name
            variant = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            analysis = analyze_wav(wav)
            rows.append({
                "theme": theme_dir.name,
                "slot": slot,
                "variant": variant,
                "name": name,
                "path": str(wav),
                **{k: v for k, v in analysis.items() if k != "error"},
            })
    sound_library = pd.DataFrame(rows)
    return (sound_library,)


@app.cell
def _(mo, sound_library):
    """Theme and category filters."""
    themes = ["all"] + sorted(sound_library["theme"].unique().tolist())
    categories = ["all"] + sorted(sound_library["slot"].unique().tolist())
    theme_dropdown = mo.ui.dropdown(themes, value="all", label="Theme")
    category_dropdown = mo.ui.dropdown(categories, value="all", label="Category")
    mo.hstack([theme_dropdown, category_dropdown])
    return category_dropdown, theme_dropdown


@app.cell
def _(category_dropdown, sound_library, theme_dropdown):
    """Filter the library."""
    df = sound_library.copy()
    if theme_dropdown.value != "all":
        df = df[df["theme"] == theme_dropdown.value]
    if category_dropdown.value != "all":
        df = df[df["slot"] == category_dropdown.value]
    filtered = df.reset_index(drop=True)
    return (filtered,)


@app.cell
def _(filtered, mo):
    """Sound library table — click to select."""
    display_cols = ["theme", "slot", "variant", "name", "duration_ms", "peak_db", "rms_db"]
    table = mo.ui.table(
        filtered[display_cols],
        selection="single",
        label=f"Sound Library ({len(filtered)} sounds)",
    )
    table
    return (table,)


@app.cell
def _(filtered, mo, table):
    """Audio player — plays selected sound."""
    selected_idx = table.value.index.tolist() if not table.value.empty else []
    if selected_idx:
        _row = filtered.iloc[selected_idx[0]]
        _path = _row["path"]
        _info = f"**{_row['theme']}** / **{_row['name']}** | {_row['duration_ms']}ms | peak {_row['peak_db']}dB | rms {_row['rms_db']}dB"
        player_output = mo.vstack([mo.md(_info), mo.audio(src=_path, autoplay=True)])
    else:
        player_output = mo.md("*Select a row to play*")
    player_output
    return (player_output,)


@app.cell
def _(VOICE_DIR, json, mo):
    """Live voice state (auto-refreshes)."""
    state_file = VOICE_DIR / "speaking-now.json"
    try:
        data = json.loads(state_file.read_text().strip()) if state_file.exists() else {}
    except Exception:
        data = {}

    speaking = data.get("speaking_pane") or "none"
    stt = "active" if data.get("stt_active") else "idle"
    muted = data.get("muted", False)
    focused = data.get("focused_pane", "?")
    queued = data.get("queued", {})

    state_text = f"""
    ## Voice State
    | Key | Value |
    |-----|-------|
    | Speaking | {speaking} |
    | STT | {stt} |
    | Muted | {muted} |
    | Focused | {focused} |
    | Queued | {queued if queued else "none"} |
    """
    mo.md(state_text)
    return


@app.cell
def _(VOICE_DIR, mo, pd):
    """Queue event log."""
    _queue_log = VOICE_DIR / "queue.log"
    _rows_q = []
    if _queue_log.exists():
        for _line in _queue_log.read_text().splitlines()[-50:]:
            if "enqueued:" in _line or "playing:" in _line or "playback complete:" in _line:
                _ts = _line[:19]
                if "enqueued:" in _line:
                    _evt = "enqueued"
                elif "playing:" in _line:
                    _evt = "playing"
                else:
                    _evt = "complete"
                _vol = ""
                if "vol=" in _line:
                    _vol = _line.split("vol=")[1].split()[0]
                _rows_q.append({"time": _ts, "event": _evt, "volume": _vol, "raw": _line[20:80]})
    queue_df = pd.DataFrame(_rows_q) if _rows_q else pd.DataFrame(columns=["time", "event", "volume", "raw"])
    mo.ui.table(queue_df, label=f"Queue Events (last {len(queue_df)})", selection=None)
    return


@app.cell
def _(VOICE_DIR, mo, pd):
    """TTS daemon log."""
    _daemon_log = VOICE_DIR / "daemon.log"
    _rows_d = []
    if _daemon_log.exists():
        for _dline in _daemon_log.read_text().splitlines()[-30:]:
            _dts = _dline[:19]
            if "synthesized" in _dline:
                _text = _dline.split("'")[1][:50] if "'" in _dline else ""
                _rows_d.append({"time": _dts, "event": "synthesized", "text": _text})
            elif "enqueued" in _dline:
                _rows_d.append({"time": _dts, "event": "enqueued", "text": _dline.split(":")[-1].strip()})
    daemon_df = pd.DataFrame(_rows_d) if _rows_d else pd.DataFrame(columns=["time", "event", "text"])
    mo.ui.table(daemon_df, label=f"TTS Events (last {len(daemon_df)})", selection=None)
    return


@app.cell
def _(mo, sound_library):
    """Library stats."""
    _total = len(sound_library)
    _themes = sound_library["theme"].nunique()
    _slots = sound_library["slot"].nunique()
    _total_dur = sound_library["duration_ms"].sum() / 1000
    _avg_rms = sound_library["rms_db"].mean()
    _avg_peak = sound_library["peak_db"].mean()
    mo.md(f"""## Library Stats

| Metric | Value |
|--------|-------|
| Total sounds | **{_total}** |
| Themes | **{_themes}** |
| Unique slots | **{_slots}** |
| Total duration | **{_total_dur:.0f}s** |
| Avg RMS | **{_avg_rms:.1f} dBFS** |
| Avg Peak | **{_avg_peak:.1f} dBFS** |
""")
    return


@app.cell
def _(mo, sound_library, pd):
    """Sounds per theme breakdown."""
    _theme_stats = sound_library.groupby("theme").agg(
        count=("name", "count"),
        total_duration_s=("duration_ms", lambda x: round(x.sum() / 1000, 1)),
        avg_rms_db=("rms_db", "mean"),
        avg_peak_db=("peak_db", "mean"),
    ).round(1).reset_index()
    mo.ui.table(_theme_stats, label="Sounds per Theme", selection=None)
    return


@app.cell
def _(VOICE_DIR, mo):
    """Current voice config."""
    _config_path = VOICE_DIR / "config.yaml"
    _config_text = _config_path.read_text() if _config_path.exists() else "not found"
    mo.md(f"## Voice Config\n```yaml\n{_config_text}\n```")
    return


@app.cell
def _(mo, sound_library):
    """Loudest and quietest sounds."""
    _loudest = sound_library.nlargest(5, "rms_db")[["theme", "name", "rms_db", "peak_db", "duration_ms"]]
    _quietest = sound_library.nsmallest(5, "rms_db")[["theme", "name", "rms_db", "peak_db", "duration_ms"]]
    mo.vstack([
        mo.md("## Loudest Sounds"),
        mo.ui.table(_loudest, label="Top 5 Loudest (by RMS)", selection=None),
        mo.md("## Quietest Sounds"),
        mo.ui.table(_quietest, label="Top 5 Quietest (by RMS)", selection=None),
    ])
    return


@app.cell
def _():
    return


@app.cell
def _():
    return


@app.cell
def _():
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
