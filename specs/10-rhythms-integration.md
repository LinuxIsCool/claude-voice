---
title: "Rhythms & System Integration — Ambient Soundscapes, Brief Delivery & Plugin Sync"
spec: "10"
status: draft
created: 2026-03-26
author: matt
tags: [claude-voice, rhythms, ambient, integration, tmux, observatory]
---

# Spec 10: Rhythms & System Integration

## 1. Overview

claude-voice does not exist in isolation. It is one node in a 26-plugin ecosystem where temporal awareness, visual state, inter-agent messaging, system health, and identity all flow through shared conventions (filesystem state, hook events, config files). This spec defines how claude-voice integrates with five sibling plugins to create a unified audio-visual experience:

- **claude-rhythms** — time-of-day ambient soundscapes and voice delivery of briefs (morning, midday, evening summaries)
- **claude-tmux** — visual indicator synchronization so color and sound reinforce the same semantic state
- **claude-observatory** — system health sonification (plugin count, context injectors, pipeline health)
- **claude-matrix** — inter-agent sound coordination across multiple concurrent Claude Code sessions
- **claude-personas** — persona-driven theme and voice selection

Additionally, this spec covers integration with **claude-logging** (sound event analytics), **claude-llms** (TTS model routing), and **claude-statusline** (audio state display).

All integration follows the legion convention: no direct plugin-to-plugin calls, no shared memory, no daemon IPC. Communication is filesystem state files, environment variables, and hook event responses. Plugins are isolated processes that read and write to agreed-upon paths.

---

## 2. Time-of-Day Ambient Soundscapes

### 2.1 Schedule

Ambient sound changes based on time of day. The schedule aligns with claude-rhythms' temporal phases but is independently evaluated by claude-voice — no runtime dependency on the rhythms service.

| Time Block | Hours | Ambient Sound | Character | Volume |
|------------|-------|---------------|-----------|--------|
| Dawn | 05:00-08:00 | Birds, gentle wind | Awakening, fresh | 0.2 |
| Morning | 08:00-12:00 | Soft focus music/tone | Productive, clear | 0.15 |
| Afternoon | 12:00-17:00 | White noise, gentle rain | Sustained focus | 0.15 |
| Evening | 17:00-21:00 | Warm tones, fireplace | Winding down | 0.2 |
| Night | 21:00-01:00 | Deep ambient, slow waves | Deep work | 0.1 |
| Late Night | 01:00-05:00 | Near silence, subtle drone | Minimal distraction | 0.05 |

### 2.2 Audio Channel Separation

Ambient runs as a separate long-lived `pw-play` process on a dedicated PipeWire stream. It does not interfere with earcon playback or TTS output. The three audio channels are:

| Channel | Purpose | Playback Mode | Volume Control |
|---------|---------|---------------|----------------|
| **Earcons** | Hook-triggered sound effects | Fire-and-forget Popen per event | `config.yaml` volume (0-100) |
| **TTS** | Synthesized speech output | Sequential, fcntl-locked | TTS-specific volume in config |
| **Ambient** | Background soundscape loop | Persistent process, looping | `integration.ambient.volume` (0.0-1.0) |

Ambient playback uses `pw-play --loop` (if available) or a wrapper script that restarts playback when the file ends. The ambient process PID is written to `~/.claude/local/voice/ambient.pid` for lifecycle management.

### 2.3 Crossfade Transitions

When the time block changes, ambient crossfades over 30 seconds:

1. New ambient process starts at volume 0.0
2. Over 30 seconds, new process ramps to target volume while old process ramps to 0.0
3. Old process is killed after ramp completes
4. `ambient.pid` is updated to the new process

Implementation: a small Python helper (`lib/ambient.py`) manages the crossfade using `pw-play` volume control via PipeWire's `pw-cli` or by spawning overlapping processes with `--volume` flags.

### 2.4 Theme-Specific Ambient

Each game theme can override the default ambient soundscape with its own flavor:

| Theme | Dawn | Morning | Afternoon | Evening | Night | Late Night |
|-------|------|---------|-----------|---------|-------|------------|
| **StarCraft** | Space station dawn cycle | Bridge hum, scanner pings | Engine room steady state | Off-duty cantina murmur | Deep space drone | Cryo-bay silence |
| **Warcraft** | Forest birds, campfire embers | Forge hammer, village bustle | March drums, distant horns | Tavern warmth, crackling fire | Night forest, owls | Dungeon ambient, dripping |
| **Zelda** | Fairy fountain, gentle harp | Field theme, wind | Lake Hylia, gentle waves | Kakariko Village, evening | Temple interior, echo | Lost Woods, distant melody |
| **Mario** | Overworld dawn, pipe ambiance | Underground theme, coins | Athletic theme, wind | Ghost house, subtle | Star Road, cosmic | Subcon, dream static |
| **Smash Bros** | Training room power-up | Battlefield, crowd murmur | Final Destination, tension | Results screen, calm | Menu drift, slow | Subspace ambient |
| **Kingdom Hearts** | Destiny Islands, waves | Traverse Town, gentle | Hollow Bastion, mechanical | Twilight Town, warmth | End of the World, void | Dive to the Heart, silence |

When `integration.ambient.theme_specific` is `true`, the theme-specific ambient replaces the default. When `false`, the generic nature-based ambient plays regardless of theme.

### 2.5 Trigger Mechanism

Ambient phase is evaluated on two triggers:

1. **SessionStart hook** — determines current time block, starts ambient if enabled
2. **Manual command** — `/voice ambient [on|off|phase]` controls ambient directly

The SessionStart hook in `voice_event.py` calls `lib/ambient.py`:

```python
def evaluate_ambient(config: dict, current_time: datetime) -> Optional[str]:
    """Determine which ambient phase to play based on current time."""
    if not config.get("integration", {}).get("ambient", {}).get("enabled", False):
        return None

    hour = current_time.hour
    PHASE_MAP = [
        (5, 8, "dawn"),
        (8, 12, "morning"),
        (12, 17, "afternoon"),
        (17, 21, "evening"),
        (21, 25, "night"),      # 25 = wraps past midnight
        (1, 5, "late_night"),
    ]
    for start, end, phase in PHASE_MAP:
        if start <= hour < end or (end > 24 and hour < end - 24):
            return phase
    return "late_night"  # 00:00-01:00 and 01:00-05:00
```

---

## 3. Brief Delivery Channel

### 3.1 Concept

claude-rhythms produces briefs three times daily: morning (6:00 AM), midday (12:00 PM), evening (5:00 PM). These are markdown files containing structured intelligence about tasks, contacts, ventures, signals, and patterns. claude-voice can deliver a spoken summary of these briefs via TTS, creating a "morning radio" experience.

### 3.2 Delivery Schedule

| Brief | Trigger Condition | TTS Length | Voice |
|-------|-------------------|------------|-------|
| Morning brief | SessionStart after 06:00, before 10:00 | <30 seconds | Active persona voice |
| Midday brief | SessionStart between 11:00 and 13:00 | <20 seconds | Active persona voice |
| Evening brief | SessionStart after 17:00, before 20:00 | <20 seconds | Active persona voice |

### 3.3 How It Works

1. **SessionStart hook fires.** `voice_event.py` checks the current time and whether `integration.rhythms.voice_briefs` is enabled.
2. **Locate latest brief.** Read `~/.claude/local/rhythms/state.json` for the most recent successful run of the matching rhythm ID (e.g., `morning-brief`). The state file contains the path to the brief.
3. **Extract speakable summary.** The brief is full markdown (typically 500-2000 words). The TTS delivery must be under 30 seconds (~75-100 words). Extraction strategy:
   - Look for a "The One Thing" section (present in most briefs) — use that sentence.
   - If absent, use the first paragraph after the top-level heading.
   - If the brief is older than 18 hours, skip delivery (stale brief).
4. **Synthesize and play.** Route the extracted text through the TTS engine (spec 06). Use the active persona's voice profile. Play via the TTS audio channel (not the earcon channel).
5. **Log the delivery.** Write a sound event to claude-logging with type `VoiceBriefDelivered`.

### 3.4 Content Source

Briefs live at `~/.claude/local/rhythms/briefs/{date}-{rhythm_id}.md`. The state file at `~/.claude/local/rhythms/state.json` tracks:

```json
{
  "last_runs": {
    "morning-brief": {
      "status": "success",
      "started_at": "2026-03-26T06:01:23Z",
      "completed_at": "2026-03-26T06:14:47Z",
      "brief_path": "~/.claude/local/rhythms/briefs/2026-03-26-morning-brief.md"
    }
  }
}
```

### 3.5 Opt-In

Brief delivery is disabled by default. Enable via:

```yaml
# ~/.claude/local/voice/config.yaml
integration:
  rhythms:
    voice_briefs: true
    brief_max_seconds: 30
```

---

## 4. claude-tmux Visual+Audio Sync

### 4.1 Shared Semantic State

claude-tmux and claude-voice both respond to the same 14 hook event types. They share a semantic token vocabulary so that visual state and audio state reinforce each other:

| Semantic State | tmux Visual | voice Audio | Hook Source |
|---------------|-------------|-------------|------------|
| `active` | Blue dot, "working" glyph | Prompt ack chirp | `UserPromptSubmit` |
| `ready` | Green dot, "idle" glyph | Task complete chime | `Stop` |
| `error` | Red dot, "error" glyph | Error alert | `PostToolUseFailure` |
| `agent_out` | Agent count indicator | Agent deploy sweep | `SubagentStart` |
| `agent_in` | Agent count decrement | Agent return chirp | `SubagentStop` |
| `attention` | Flashing indicator | Permission snap | `PermissionRequest` |
| `notification` | Notification badge | Notification ping | `Notification` |
| `compact` | Memory indicator update | Compact crunch | `PreCompact` |
| `session_on` | Session indicator on | Boot sequence | `SessionStart` |
| `session_off` | Session indicator off | Shutdown sweep | `SessionEnd` |

No direct communication between the two plugins. Both independently consume hook events and map them through their respective `theme.json` files. Alignment is maintained by convention: both plugins use the same semantic token names (defined in their theme files), and the theme author ensures audio and visual tokens match.

### 4.2 Theme-Driven Visual Sync

Each claude-voice `theme.json` includes a `visual_sync` section that specifies the tmux accent color for the theme:

```json
{
  "visual_sync": {
    "tmux_theme": "starcraft",
    "accent_color": "#00ff41",
    "status_style": "military"
  }
}
```

When a theme change occurs in claude-voice (via `/voice theme starcraft`), the hook handler can optionally call tmux to update visual state:

```python
def sync_tmux_theme(theme_config: dict):
    """Update tmux visual state to match voice theme."""
    if not config.get("integration", {}).get("tmux", {}).get("visual_sync", True):
        return
    accent = theme_config.get("visual_sync", {}).get("accent_color")
    if accent:
        subprocess.run(
            ["tmux", "set-option", "-g", "status-style", f"fg={accent}"],
            capture_output=True,
        )
```

This is a lightweight write — tmux processes the option change instantly. No daemon, no polling.

### 4.3 Session State Reflection

The combined audio+visual state creates a multi-sensory feedback loop:

- **Working**: blue dot + silence (or ambient). The user sees and hears that Claude is processing.
- **Done**: green dot + completion chime. Both channels confirm the task ended.
- **Error**: red dot + error alert. Both channels demand attention.
- **Agents active**: agent count glyph + deploy/return sounds. The user perceives parallel work visually and auditorily.

---

## 5. claude-observatory Sonification

### 5.1 System Health as Sound

claude-observatory audits the plugin ecosystem at every SessionStart. Its structured output (plugin count, context injectors, tool gatekeepers) can drive ambient and alert sounds.

### 5.2 Health Indicators

| Health Signal | Sound Response | Trigger | Severity |
|--------------|---------------|---------|----------|
| High CPU (>85%) | Subtle tension undertone layered on ambient | SessionStart reads `/proc/loadavg` or `~/.claude/local/health/` | Warning |
| Low disk (<10% free on root) | Warning chime at session start | SessionStart reads `btrfs fi usage` output or health file | Alert |
| Service down (hippo, KOI, messages) | Alert tone at session start | SessionStart checks systemd unit status or health file | Alert |
| GPU temperature >80C | Reduce GPU TTS to CPU fallback, play thermal warning | SessionStart reads `nvidia-smi` output or health file | Critical |
| Plugin count change | Brief notification chime | Observatory audit detects different count vs last session | Info |
| Context injector missing | Absence of expected startup tick | Observatory audit shows fewer injectors than expected | Warning |

### 5.3 Implementation

The SessionStart hook in `voice_event.py` reads health state from files (not by executing system commands — that would blow the 150ms timing budget):

```python
HEALTH_DIR = Path.home() / ".claude" / "local" / "health"

def check_health_alerts(config: dict) -> list[str]:
    """Read pre-computed health files and return alert sound events."""
    alerts = []
    if not config.get("integration", {}).get("observatory", {}).get("health_alerts", True):
        return alerts

    cpu_file = HEALTH_DIR / "cpu.json"
    if cpu_file.exists():
        cpu = json.loads(cpu_file.read_text())
        if cpu.get("load_pct", 0) > 85:
            alerts.append("health_cpu_high")

    disk_file = HEALTH_DIR / "disk.json"
    if disk_file.exists():
        disk = json.loads(disk_file.read_text())
        if disk.get("root_free_pct", 100) < 10:
            alerts.append("health_disk_low")

    gpu_file = HEALTH_DIR / "gpu.json"
    if gpu_file.exists():
        gpu = json.loads(gpu_file.read_text())
        if gpu.get("temp_c", 0) > 80:
            alerts.append("health_gpu_hot")

    return alerts
```

Health files are written by a separate process (a cron job, a systemd timer, or the nightly-integration script). claude-voice never gathers health data itself — it only reads pre-computed state.

### 5.4 Absence as Signal

One of the most powerful audio patterns: the sound that does NOT play. If the user grows accustomed to hearing the morning brief chime at session start, and one day it is absent, that silence itself signals a problem (rhythms service failed, state.json corrupted, brief generation crashed). This requires no implementation — it emerges naturally from habitual use.

---

## 6. claude-matrix Inter-Agent Sounds

### 6.1 Multi-Terminal Sound Coordination

claude-matrix enables multiple Claude Code instances to communicate. When agents run in separate terminals, their sounds must be coordinated to avoid cacophony.

### 6.2 Sound Events

| Matrix Event | Sound | Volume | Description |
|-------------|-------|--------|-------------|
| Agent spawned on another terminal | Distant deployment sweep | 0.3x normal | Spatial awareness — work happening elsewhere |
| Agent completed on another terminal | Subtle completion chime | 0.3x normal | Results arrived elsewhere |
| Message received from another agent | Message notification | 0.3x normal | Inter-agent communication |

### 6.3 Collision Prevention

The existing fcntl lock mechanism (spec 05) prevents simultaneous playback. For matrix events:

1. Matrix agent writes a notification to `~/.claude/local/claudematrix/notifications/`
2. claude-voice's `Notification` hook detects the matrix origin from the notification payload
3. Sound Router applies the 0.3x volume reduction for cross-agent sounds
4. If the playback lock is held (local sound playing), the matrix sound is dropped (low priority)

### 6.4 Agent Identity

Each matrix agent can be assigned a pitch shift or tonal offset so the user can distinguish which terminal produced the sound:

```json
// ~/.claude/local/voice/agents.json
{
  "agent-082cb39e": {
    "pitch_shift": 0,
    "label": "primary"
  },
  "agent-1a2b3c4d": {
    "pitch_shift": 200,
    "label": "research"
  }
}
```

Implementation deferred to P3 — initial version treats all matrix agents identically with volume reduction.

---

## 7. claude-personas Integration

### 7.1 Persona-Theme Mapping

Each persona defined in claude-personas has a preferred theme and voice profile. When a persona is active, claude-voice auto-selects their configuration:

```yaml
# Example from a persona's character.yaml
identity:
  name: "Matt"
  archetype: "Matt Gray"
  role: "chief-of-staff"
  preferred_theme: "starcraft"
  voice_profile:
    provider: "elevenlabs"
    voice_id: "pNInz6obpgDQGcFmaJgB"
    style: "commanding"
    speed: 1.0
```

### 7.2 Resolution Chain

The Identity Resolver (spec 08) determines the active voice profile using a 4-layer resolution chain:

1. **Session override** — user explicitly set theme/voice via `/voice theme` or `/voice voice`
2. **Agent persona** — if a subagent has a persona assignment, use that persona's preferences
3. **Model default** — model-specific defaults (e.g., opus gets a different voice than sonnet)
4. **System default** — `config.yaml` defaults

### 7.3 Mid-Session Persona Change

If the active persona changes during a session (e.g., switching from Matt to Philipp):

1. New persona's theme is loaded
2. A `theme_change` transition sound plays from the NEW theme
3. Ambient soundscape crossfades to the new theme's ambient (if theme-specific ambient is enabled)
4. TTS voice switches to the new persona's voice profile
5. The transition takes effect on the next hook event — no retroactive changes

### 7.4 Subagent Personas

When a subagent is spawned with a different persona (e.g., a research subagent using the Philipp persona while the main session uses Matt):

- Subagent's sounds use the subagent persona's theme
- Main session sounds remain on the main persona's theme
- The user hears the contrast: main session StarCraft sounds, subagent Zelda sounds
- This provides auditory spatial awareness of which agent is active

---

## 8. claude-logging Integration

### 8.1 Sound Event Logging

Every sound event (played, skipped, failed) is logged to claude-logging for analytics:

```python
def log_sound_event(event: dict):
    """Emit a sound event to claude-logging."""
    payload = {
        "type": "VoiceSoundPlayed",
        "timestamp": datetime.utcnow().isoformat(),
        "data": {
            "sound_event": event["sound_event"],
            "theme": event["theme"],
            "variant": event["variant"],
            "volume": event["volume"],
            "latency_ms": event["latency_ms"],
            "session_id": event["session_id"],
            "channel": event.get("channel", "earcon"),  # earcon | tts | ambient
            "outcome": event.get("outcome", "played"),   # played | skipped | failed
        }
    }
    # Append to session JSONL (same pattern as claude-logging hooks)
    log_path = VOICE_DIR / "events.jsonl"
    with open(log_path, "a") as f:
        f.write(json.dumps(payload) + "\n")
```

### 8.2 Analytics Enabled

With sound event logging, the following analyses become possible:

| Metric | Query | Insight |
|--------|-------|---------|
| Most-played sound event | Count by `sound_event` | Which events fire most often — calibrate variant pool size |
| Average latency by engine | Mean `latency_ms` grouped by channel | Performance monitoring for earcon vs TTS vs ambient |
| Error rate | Count where `outcome == "failed"` | Detect broken themes, missing assets, playback failures |
| Habituation risk | Count of `task_complete` per session | If >50 per session, consider reducing prompt_ack frequency |
| Theme usage | Count by `theme` | Which themes get used — prioritize polish for popular themes |
| Brief delivery success | Count `VoiceBriefDelivered` vs expected | Are briefs actually being spoken? |

### 8.3 Integration with Existing Logging

claude-logging already captures 31K+ events across 9 hook types. Voice events add a new event type (`VoiceSoundPlayed`, `VoiceBriefDelivered`, `VoiceAmbientChanged`, `VoiceTTSGenerated`) to the existing taxonomy. The logging plugin's FTS5 index makes these searchable alongside all other session events.

---

## 9. claude-llms Integration

### 9.1 TTS Model Routing

TTS model selection is delegated to the claude-llms 3-tier provider system rather than hardcoded in claude-voice:

| Tier | Provider | Model | Latency | Cost | Use Case |
|------|----------|-------|---------|------|----------|
| Tier 1 | Local GPU | Kokoro-82M | <100ms | $0 | Default for all short utterances (<20 words) |
| Tier 2 | Cloud fast | ElevenLabs Flash v2.5 | ~75ms | ~$0.15/1K chars | Brief delivery, longer narration |
| Tier 3 | Cloud premium | ElevenLabs Multilingual v2 | ~200ms | ~$0.30/1K chars | High-quality persona voice, important briefs |

### 9.2 Dynamic Selection

The TTS engine requests a model from claude-llms based on task characteristics:

```python
def select_tts_model(text: str, importance: str = "normal") -> dict:
    """Select TTS backend based on text length and importance."""
    text_length = len(text)

    if importance == "high":
        return {"provider": "elevenlabs", "model": "multilingual-v2", "tier": 3}
    elif text_length > 200:
        return {"provider": "elevenlabs", "model": "flash-v2.5", "tier": 2}
    else:
        return {"provider": "kokoro", "model": "kokoro-82m", "tier": 1}
```

### 9.3 Cost Tracking

claude-llms tracks spend across all API calls. TTS costs are attributed to the `voice` category:

```python
# After TTS generation
spend_record = {
    "service": "elevenlabs",
    "category": "voice",
    "chars": len(text),
    "cost_usd": len(text) * 0.00015,  # Flash tier rate
    "timestamp": datetime.utcnow().isoformat(),
}
```

This integrates with claude-llms' existing spend tracking so Shawn can see total API costs including voice in one place.

---

## 10. Plugin Communication Protocol

### 10.1 Principles

Plugins in the legion ecosystem are isolated processes. There is no shared memory, no event bus, no message queue, no daemon-to-daemon IPC. Communication happens through:

1. **Shared filesystem** — plugins read and write to agreed-upon paths under `~/.claude/local/`
2. **Environment variables** — hook scripts receive env vars set by Claude Code or other hooks
3. **Hook event responses** — hooks return JSON to Claude Code, which may influence subsequent hooks
4. **tmux options** — plugins can read/write tmux server options as a lightweight key-value store

### 10.2 What claude-voice Reads

| Source Plugin | Data | Path | Format | Frequency |
|--------------|------|------|--------|-----------|
| claude-rhythms | Latest brief | `~/.claude/local/rhythms/briefs/*.md` | Markdown | SessionStart |
| claude-rhythms | Rhythm state | `~/.claude/local/rhythms/state.json` | JSON | SessionStart |
| claude-rhythms | Dawn data | `~/.claude/local/rhythms/dawn-data.json` | JSON | SessionStart (morning) |
| claude-personas | Active persona | `~/.claude/local/personas/state.json` | JSON | Every hook |
| claude-personas | Character definition | `plugins/claude-personas/characters/*.yaml` | YAML | Theme change |
| claude-observatory | Plugin audit | Observatory's `additionalContext` output | String | SessionStart |
| claude-matrix | Agent notifications | `~/.claude/local/claudematrix/notifications/` | JSON files | Notification hook |
| System health | CPU/disk/GPU state | `~/.claude/local/health/*.json` | JSON | SessionStart |

### 10.3 What claude-voice Writes

| Target | Data | Path | Format | Frequency |
|--------|------|------|--------|-----------|
| claude-statusline | Audio state | `~/.claude/local/voice/state.json` | JSON | Every sound event |
| claude-logging | Sound events | `~/.claude/local/voice/events.jsonl` | JSONL | Every sound event |
| tmux | Visual theme sync | tmux server options | tmux command | Theme change |
| Self | Ambient PID | `~/.claude/local/voice/ambient.pid` | Text (PID) | Ambient start/stop |
| Self | Config | `~/.claude/local/voice/config.yaml` | YAML | User config change |

### 10.4 No Circular Dependencies

claude-voice has read-only relationships with all source plugins. It writes state that other plugins may read (statusline reads `state.json`), but no plugin depends on claude-voice for its core function. If claude-voice is disabled or crashes, all other plugins continue operating normally. If source plugins are missing (rhythms not installed, personas not configured), claude-voice degrades gracefully — no briefs to deliver, default theme and voice used.

---

## 11. Configuration

All integration settings live under the `integration` key in `~/.claude/local/voice/config.yaml`:

```yaml
integration:
  ambient:
    enabled: false
    volume: 0.15
    theme_specific: true
    crossfade_seconds: 30
  rhythms:
    voice_briefs: false
    brief_max_seconds: 30
  tmux:
    visual_sync: true
    accent_color_sync: true
  observatory:
    health_alerts: true
    health_ambient: false
  matrix:
    cross_agent_sounds: true
    cross_agent_volume: 0.3
  logging:
    sound_events: true
```

### Configuration Semantics

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `ambient.enabled` | bool | `false` | Master switch for ambient soundscapes. Off by default — ambient is opt-in because it runs a persistent process. |
| `ambient.volume` | float | `0.15` | Ambient volume as a fraction of system volume (0.0-1.0). Kept low so ambient never competes with earcons or TTS. |
| `ambient.theme_specific` | bool | `true` | When true, use the active game theme's ambient sounds. When false, use generic nature-based ambient regardless of theme. |
| `ambient.crossfade_seconds` | int | `30` | Duration of crossfade between time blocks. 30 seconds is long enough to be imperceptible as a "switch" — it feels like natural environmental change. |
| `rhythms.voice_briefs` | bool | `false` | Enable TTS delivery of rhythm briefs at session start. Off by default — briefs are already delivered via journal and Matrix. Voice delivery is a third channel for users who want it. |
| `rhythms.brief_max_seconds` | int | `30` | Maximum TTS duration for brief delivery. Limits the extracted text to ~75-100 words. Prevents a 2000-word brief from becoming a 3-minute monologue. |
| `tmux.visual_sync` | bool | `true` | Sync tmux accent color when voice theme changes. On by default because the cost is near-zero (one tmux set-option call). |
| `tmux.accent_color_sync` | bool | `true` | Whether to push the theme's accent color to tmux status style. Can be disabled if the user has a custom tmux theme they don't want overridden. |
| `observatory.health_alerts` | bool | `true` | Play alert sounds at session start for health issues (high CPU, low disk, service down). On by default — these are rare, high-value signals. |
| `observatory.health_ambient` | bool | `false` | Layer health state into the ambient soundscape (tension undertone for high CPU, etc.). Off by default — this is experimental and may be annoying. |
| `matrix.cross_agent_sounds` | bool | `true` | Play sounds for events from other claude-matrix agents. On by default for spatial awareness. |
| `matrix.cross_agent_volume` | float | `0.3` | Volume multiplier for cross-agent sounds (0.0-1.0). At 0.3, they are audible but clearly "background" relative to local sounds at 1.0. |
| `logging.sound_events` | bool | `true` | Log every sound event to JSONL. On by default — the cost is negligible (one file append) and the analytics value is high. |

---

## 12. Implementation Priority

| Integration | Priority | Complexity | Value | Rationale |
|-------------|----------|------------|-------|-----------|
| tmux visual sync | P1 | Low | High | One tmux set-option call. Unified audio+visual experience with zero ongoing cost. |
| logging | P1 | Low | High | One JSONL append per event. Enables all future analytics and debugging. |
| personas | P1 | Low | High | Read one YAML file, select theme+voice. Identity-driven experience with minimal code. |
| rhythms ambient | P2 | Medium | Medium | Requires ambient process management, crossfade logic, per-theme asset creation. Nice-to-have. |
| rhythms briefs | P2 | Medium | Medium | Requires brief text extraction, TTS pipeline, opt-in config. Novel delivery channel. |
| observatory health | P3 | Medium | Low | Requires health file infrastructure (may not exist yet). Edge case alerts. |
| matrix cross-agent | P3 | High | Low | Requires matrix notification integration, agent identity tracking. Multi-terminal is niche. |

### Dependency Chain

```
P1 (can start immediately, no external dependencies):
  tmux sync    — needs only tmux available (always is)
  logging      — needs only filesystem write
  personas     — needs only character YAML files exist

P2 (depends on P1 + external plugin state):
  ambient      — needs theme assets created (spec 03/05), ambient process manager
  briefs       — needs TTS engine (spec 06), rhythms state.json populated

P3 (depends on P2 + external infrastructure):
  health       — needs health file writers (cron/systemd, not yet built)
  matrix       — needs matrix notification protocol (claude-matrix evolution)
```

---

## 13. Open Questions

1. **Ambient process model.** Should ambient loops run as a separate persistent process managed by systemd, or be started/stopped by each SessionStart/SessionEnd hook? A persistent process survives between sessions but requires lifecycle management. Hook-triggered ambient dies between sessions but is simpler.

2. **Multi-session ambient conflict.** When multiple Claude Code sessions are active simultaneously, which one controls the ambient soundscape? Options: first session wins (lock file), last session wins (overwrite), dedicated ambient service (independent of sessions).

3. **Brief delivery format.** Should TTS deliver the full "One Thing" sentence from the brief, or an abbreviated bullet-point summary? Full sentence sounds more natural. Bullets convey more information per second. User preference likely varies.

4. **Theme-specific vs time-of-day ambient.** Should theme-specific ambient replace time-of-day ambient entirely, or layer on top of it? Replacement is simpler and avoids sonic clutter. Layering preserves temporal awareness even when a game theme is active. Could offer both modes via config.

5. **Health file producers.** claude-voice reads health files at `~/.claude/local/health/*.json` but who writes them? Options: a dedicated systemd timer, the nightly-integration script, claude-observatory extending its audit to include system metrics. This is an external dependency that must be resolved before P3 health alerts can work.

6. **Dawn data sonification.** The `dawn-data.json` contract contains rich overnight processing data (entities bridged, hippo nodes, embedding counts, pipeline health). Is it worth building a dedicated "overnight summary" sound that plays on first morning session? Or is the morning brief TTS delivery sufficient?

7. **Ambient asset budget.** Each theme needs 6 ambient loops (one per time block). At ~60 seconds per loop, that is 360 seconds of audio per theme, 2160 seconds across 6 themes. Should these be synthesized (numpy/scipy, matching spec 05) or sourced from CC0/public domain ambient libraries?
