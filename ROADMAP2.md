---
title: "claude-voice — Roadmap v2"
status: active
created: 2026-03-27
author: matt
supersedes: ROADMAP.md
tags: [claude-voice, roadmap, implementation, milestones, v2]
---

# claude-voice — Roadmap v2

_Supersedes [ROADMAP.md](./ROADMAP.md) (archived 2026-03-27). Resequenced based on operational reality: multi-agent voice is the live environment, not a future aspiration._

---

## 1. Vision

The terminal is a living world. Every agent is a unit. Every event has a sound. Every persona has a voice. The user speaks and the system acts. The system speaks and the user understands. Sound is the feedback loop that makes the invisible visible — agent state, system health, task progress, emotional tone, temporal rhythm. This isn't a notification system. It's a sensory layer for an embodied AI.

The complete loop: **speech in, intent parsed, agents dispatched, progress narrated, ambient soundscape reflecting system state, earcons confirming each event, all gated by tmux focus so only the pane you're looking at speaks.**

Reference: Donella Meadows' leverage points hierarchy. Voice-to-speech is a paradigm shift in interaction capacity — the highest leverage intervention in how Shawn and Legion collaborate.

---

## 2. Source Material Index

### Legion Ecosystem (50% of design input)

| Source | Path | What It Tells Us |
|--------|------|------------------|
| ARCHITECTURE.md | `plugins/claude-voice/ARCHITECTURE.md` | Component inventory, data flow, 150ms budget |
| ROADMAP.md (v1, archived) | `plugins/claude-voice/ROADMAP.md` | Original 6-phase sequential plan |
| Specs (14 files) | `plugins/claude-voice/specs/01-14` | Per-subsystem design docs |
| Voice audit | `~/.claude/local/research/2026/03/27/voice-infrastructure/legion-voice-audit.md` | Full gap analysis: what exists, what's missing |
| Voice config (live) | `~/.claude/local/voice/config.yaml` | Runtime truth: tts.enabled=true, voice=am_onyx |
| claude-tmux hooks | `plugins/claude-tmux/hooks/hook.sh` | `TMUX_PANE` + `@claude_*` pane option pattern |
| claude-personas | `plugins/claude-personas/` | Identity resolution, `PERSONA_SLUG`, voice-dispatcher.sh |
| claude-statusline | `plugins/claude-statusline/` | 300ms tick, state detection, no voice awareness yet |
| claude-rhythms | `plugins/claude-rhythms/` | Time-of-day phases, brief orchestration |
| Scratchpad vision | `scratchpad/2026-03-25T20-07-56-claude-voice-plugin-vision.md` | Original brainstorm: RTS game worlds, sensory layer |
| Scratchpad hooks deep-think | `scratchpad/2026-03-27T17-10-00-hooks-voice-architecture-deep-think.md` | Universal voice adapter, embodied cognition, robotics frame |
| Scratchpad priorities | `scratchpad/2026-03-27T15-27-30-voice-note-priorities-and-vision.md` | Meadows leverage points, 0.007% capacity, seamless speech-to-speech |
| Scratchpad tmux design | `scratchpad/2026-03-27T18-33-00-voice-tmux-integrated-design.md` | Two-channel architecture, focus gate, integrated layer plan |
| Handoff from matt:56 | Matrix message, 2026-03-27 18:32 | TTS daemon live, 2 bugs fixed, Phase 4 priorities |
| Journal (24 entries today) | `~/.claude/local/journal/2026/03/27/` | Full session context |

### Web Research (50% of design input)

| Source | Path | Key Insight |
|--------|------|-------------|
| tmux focus events | `research/2026/03/27/voice-infrastructure/tmux-focus-events.md` | `pane-focus-in` hook, `#{pane_active}`, Issue #2808 event ordering |
| PipeWire routing | `research/2026/03/27/voice-infrastructure/pipewire-routing.md` | `wpctl set-mute --pid`, `PIPEWIRE_NODE` env var, virtual null sinks |
| Nvidia ACE | `research/2026/03/27/voice-infrastructure/nvidia-ace-conversational.md` | Nemotron Speech ASR 600M, 24ms TTFT, EOU detection, cache-aware streaming |
| RTS audio design | `research/2026/03/27/voice-infrastructure/rts-audio-design.md` | WC3 9-category unit sound taxonomy, SC2 2381 files, Pissed category |

### Key URLs

| URL | Relevance |
|-----|-----------|
| https://docs.nvidia.com/ace/overview/2025.03.06/index.html | ACE NIM microservices — reference architecture for conversational AI |
| https://huggingface.co/blog/nvidia/nemotron-speech-asr-scaling-voice-agents | Nemotron Speech ASR — cache-aware streaming, EOU detection |
| https://wiki.archlinux.org/title/PipeWire | PipeWire config, virtual sinks, WirePlumber policy |
| https://wiki.archlinux.org/title/PipeWire/Examples | Per-app routing, null sinks, loopback |
| https://man.archlinux.org/man/extra/wireplumber/wpctl.1.en | wpctl set-mute --pid, set-volume, inspect |
| https://world-editor-tutorials.thehelper.net/cat_usersubmit.php?view=39614 | WC3 unit sound categories (Ready/What/Yes/Pissed/Death) |
| https://news.blizzard.com/en-gb/starcraft/20722027/the-sounds-of-koprulu | Blizzard audio design philosophy — identity through sound |
| https://www.uxmatters.com/mt/archives/2024/08/the-role-of-sound-design-in-ux-design-beyond-notifications-and-alerts.php | Frequency rule: more frequent = shorter/subtler |
| https://github.com/disler/claude-code-hooks-mastery | Claude Code hooks patterns and payload documentation |
| https://docs.pipewire.org/page_pulse_module_null_sink.html | PipeWire null sink creation (virtual sinks for agent isolation) |

### Mental Models

| Model | Application |
|-------|-------------|
| **Donella Meadows — Leverage Points** | Voice is a paradigm shift (level 1-2), not parameter tuning (level 12). Design for maximum leverage. |
| **RTS Command Interface** | WC3/SC2 solved multi-unit audio: per-unit sound profiles, selection sounds, priority-based interruption. Direct mapping to multi-agent tmux. |
| **Sensory-Motor Loop** | Perception (hooks) -> Processing (classify/template) -> Action (TTS/earcon). This IS robotics. The body is the machine. |
| **Two-Channel Architecture** | Channel A (speech, focus-gated) vs Channel B (earcons/ambient, always-on). Prevents cross-talk without losing ambient awareness. |
| **Garden, Not Architecture** | Each phase leaves a working system. Phases are tending, not construction. Nothing breaks if you stop. |
| **Embodied Cognition / Umwelt** | The hook set defines the AI's perceptual world. Expanding hooks = expanding the senses. Sound = making the invisible visible. |
| **Parsimony** | As simple as possible. Cached WAVs > on-demand synthesis. File flags > virtual sinks (when sufficient). `TMUX_PANE` check > PipeWire graph manipulation. |

---

## 3. Phase Summary

| Phase | Name | Status | Core Deliverable | Effort |
|-------|------|--------|-----------------|--------|
| 1 | Foundation | **COMPLETE** | Scaffold + hooks + pw-play + default theme | Done |
| 2 | Theme Engine | **COMPLETE** | 7 themes, hot-swap, variant randomization | Done |
| 3 | Voice I/O | **PARTIAL** | TTS daemon live (Kokoro-82M), STT absent | In progress |
| 3.5 | Multi-Agent Audio | **NEW** | Spatial volume mixer, per-pane routing, RTS agent sounds, ambient | Next |
| 4 | STT + Conversation | Planned | Parakeet-TDT, wake word, duplex | After 3.5 |
| 5 | Personality | Planned | Identity resolver, gamification, emotion | After 4 |
| 6 | Integration | Planned | Rhythms, observatory, matrix, statusline | After 5 |
| 7 | Autonomy | Planned | Speech-to-reality, intent parsing, learning | After 6 |

### What Changed from v1

| v1 | v2 | Reason |
|----|-----|--------|
| Phase 3 = TTS + STT together | Phase 3 = TTS only, Phase 4 = STT | TTS is live; STT is a separate workstream with different deps |
| Phase 4 = Personality | Phase 5 = Personality | Gamification is polish, doesn't unblock anything |
| Phase 5 = Integration (tmux+rhythms) | Phase 3.5 = Multi-Agent Audio (extracted, front-loaded) | tmux focus gating blocks voice usability NOW |
| No explicit ambient/RTS layer | Phase 3.5 includes RTS sound model | Research validated the game audio UX pattern |
| STT = faster-whisper | STT = Parakeet-TDT-1.1B | Nvidia's model: 100x realtime on RTX 4070, 24ms TTFT |
| No wake word | Phase 4 includes openWakeWord | "Legion" trigger word enables hands-free |
| ElevenLabs as primary cloud TTS | ElevenLabs deprioritized | Kokoro-82M at 689ms warm is good enough; sovereignty > cloud |
| Binary focus gate (on/off) | Spatial volume mixer (4-level) | Continuous mixer is simpler (fewer code paths), more general (handles priority floors, ambient ducking), and more configurable (4 numbers vs 2 booleans) |

---

## 4. Phase 1: Foundation — COMPLETE

**Completed 2026-03-26.**

- [x] Plugin scaffold (`.claude-plugin/plugin.json`, inline hooks)
- [x] `hooks/voice_event.py` — single dispatcher, 10 hook events
- [x] `lib/audio.py` — pw-play with fallback chain (pw-play -> paplay -> aplay -> mpv)
- [x] `lib/router.py` — event routing, content-aware Stop parsing (commits, errors)
- [x] `lib/theme.py` — theme.json loader, deep-merge inheritance
- [x] `lib/state.py` — atomic config (fcntl + tmpfile + rename)
- [x] `assets/themes/default/` — 12 events x 3 variants = 36 WAVs (48kHz 16-bit stereo)
- [x] `scripts/play_test.py` — sound test utility
- [x] SKILL.md, CLAUDE.md, ARCHITECTURE.md

**Exit criteria met**: Hook-to-audio latency <150ms confirmed.

---

## 5. Phase 2: Theme Engine — COMPLETE

**Completed 2026-03-26.**

- [x] `scripts/generate_sounds.py` — numpy+scipy procedural synthesis
- [x] 7 complete themes: default, starcraft, warcraft, mario, zelda, smash, kingdom-hearts
- [x] Theme inheritance via deep merge
- [x] Hot-swap (change `theme:` in config, next hook uses new theme)
- [x] Variant randomization with recency avoidance
- [x] All 10 hook events wired (UserPromptSubmit disabled by default)
- [x] Content-aware Stop routing (git commit, error, test pass/fail)

**Exit criteria met**: `/voice theme starcraft` changes all sounds immediately.

---

## 6. Phase 3: Voice I/O (TTS) — IN PROGRESS

**Started 2026-03-27. TTS operational, STT moved to Phase 4.**

### Done

- [x] `lib/tts.py` — dual-tier TTS (daemon + subprocess fallback)
- [x] Kokoro-82M local GPU TTS (RTX 4070, ~555MB VRAM, 689ms warm synthesis)
- [x] TTS daemon (`scripts/tts_daemon.py`) — Unix socket, keeps model warm in VRAM
- [x] SHA256-keyed WAV cache at `~/.claude/local/voice/cache/tts/` (23 cached WAVs)
- [x] SessionStart TTS greeting (theme-templated)
- [x] Stop event TTS (extract_speakable: first 1-2 sentences, <=250 chars)
- [x] `lib/logger.py` — JSONL + SQLite event logging (41 events logged)
- [x] Silent fallback bug fixed (daemon not running -> graceful silence)
- [x] max_chars flow-through bug fixed

### Remaining

- [ ] systemd user service for TTS daemon (stable lifecycle management)
- [ ] TTS on Notification events (with 30s cooldown to prevent spam)
- [ ] fcntl TTS queue (serialize speech from concurrent subagents)
- [ ] Piper TTS fallback (CPU ONNX, ~200ms, for when GPU is busy)
- [ ] ElevenLabs integration (optional cloud premium voice — deferred, low priority)

### Exit Criteria

- TTS daemon starts on boot via systemd and self-heals on crash
- Notification events produce spoken narration with cooldown
- Concurrent subagent TTS requests serialize cleanly via fcntl lock

### Specs

`06-tts-engine.md`, `13-elevenlabs-deep.md` (deferred)

---

## 7. Phase 3.5: Multi-Agent Audio — NEXT

**Goal**: Volume follows cognitive distance. Focused pane at full volume. Same-window panes at half. Same-session at a whisper. Other sessions silent — unless the event is critical. No cross-talk contamination. The terminal becomes a spatial audio environment where navigating between agents produces distinct sonic feedback.

**Entry criteria**: Phase 3 TTS working (DONE — Kokoro daemon live).

**Why this is urgent**: Shawn is actively doing voice I/O. Other Claude instances speaking contaminates STT transcription. This is blocking, not polish.

**Core insight (2026-03-27)**: A binary focus gate (on/off) is a degenerate case of a continuous volume mixer. The mixer is simpler to implement (fewer code paths), more general (handles ambient ducking and priority floors too), and more configurable (4 numbers instead of 2 booleans). Build the general version first.

### Infrastructure Available (from audit)

| Requirement | Already Present | Gap |
|-------------|----------------|-----|
| tmux spatial state | `#{pane_active} #{window_active} #{session_attached}` | No consumer for voice |
| `TMUX_PANE` in hooks | claude-tmux uses it, env var available | claude-voice ignores it |
| Per-pane state | `@claude_persona`, `@claude_state` options | No `@claude_voice_*` options |
| Priority per sound | `theme.json` semantic_sounds[slot].priority (0/1/2) | Not used for volume decisions |
| PipeWire per-sink routing | `pw-play --target <node>` supported | `audio.py` hardcodes default sink |
| Ambient loop WAV | `ambient-loop.wav` exists in default theme | Never triggered |

### The Spatial Volume Mixer (Core Abstraction)

One subprocess call to tmux returns three booleans → four spatial states → four volume multipliers:

| pane_active | window_active | session_attached | State | Default Volume |
|-------------|---------------|------------------|-------|---------------|
| 1 | 1 | 1 | **focused** | 100% |
| 0 | 1 | 1 | **same_window** | 50% |
| 0 | 0 | 1 | **same_session** | 20% |
| 0 | 0 | 0 | **other_session** | 0% |

Priority sets a floor that overrides spatial silencing:

| Priority | Floor | Events |
|----------|-------|--------|
| 2 (notification) | 80% | error, notification, permission — always audible |
| 1 (normal) | 0% | task_complete, commit, agent_return — follows spatial rules |
| 0 (ambient) | 0% | session_start, prompt_ack, compact — follows spatial rules |

Volume pipeline: `effective = max(base × focus_mult × stt_mult, priority_floor × base)`

### Deliverables

#### Layer 0: Spatial Volume Mixer (45min)

```python
def _get_focus_state() -> str:
    """4-level spatial state from one tmux query. Fails open to 'no_tmux'."""

def effective_volume(base: float, focus_state: str, priority: int, config: dict) -> float:
    """Volume = max(base × spatial_multiplier, priority_floor × base)"""
```

- [ ] `_get_focus_state()` in `router.py` — returns focused/same_window/same_session/other_session/no_tmux
- [ ] `effective_volume()` in `router.py` — spatial multiplier × base, floored by priority
- [ ] Wire into `_route_event_inner()` — earcons AND TTS both go through mixer
- [ ] Skip TTS synthesis when effective volume is 0.0 (save GPU cycles)
- [ ] `FOCUSED_PANE_PATH` constant for Layer 1 cache compatibility
- [ ] Config section:
  ```yaml
  tmux:
    focus_volumes:
      focused: 1.0
      same_window: 0.5
      same_session: 0.2
      other_session: 0.0
    priority_floors:
      "2": 0.8    # errors/notifications always audible
      "1": 0.0    # normal events follow spatial rules
      "0": 0.0    # ambient events follow spatial rules
  ```
- [ ] Log `focus_state` and `effective_volume` in event records

#### Layer 1: Cached Spatial State (1h)

- [ ] `~/.claude/local/scripts/voice-focus-change.sh` — writes spatial state to `~/.claude/local/voice/focus-state`
- [ ] tmux hook: `set-hook -g after-select-pane` and `after-select-window`
- [ ] `_get_focus_state()` checks cached file first (~0.1ms), subprocess fallback (~5ms)

#### Layer 2: STT Multiplier (30min)

- [ ] `stt-active` file flag
- [ ] One more multiplier in the volume pipeline: `stt_mult = 0.0 if stt_active else 1.0`
- [ ] All TTS suppressed while user is speaking — from ALL panes, including focused

#### Layer 3: Per-Pane Audio Routing (2h)

- [ ] `audio.py._build_args()` — add `--target <sink>` support for `pw-play`
- [ ] Config key: `audio.sink: default` (or `hdmi`, `bluetooth`, `<node_name>`)
- [ ] Per-pane override: tmux pane option `@claude_audio_sink`
- [ ] Routing is about WHERE (which sink), mixer is about HOW LOUD — independent

#### Layer 4: Agent Sound Profiles — RTS Model (3h)

Warcraft 3's unit sound taxonomy applied to agents:

```yaml
# In theme.json, per-persona sound profiles
agent_sounds:
  matt:
    select: "sounds/agents/matt-select.wav"       # pane focused
    acknowledge: "sounds/agents/matt-yes.wav"      # task accepted
    complete: "sounds/agents/matt-done.wav"        # task done
    error: "sounds/agents/matt-error.wav"          # failure
    voice_id: "am_onyx"                            # Kokoro TTS voice
  darren:
    select: "sounds/agents/darren-select.wav"
    voice_id: "am_adam"
  _default:
    select: "sounds/earcons/agent-select.wav"
```

- [ ] `lib/agents.py` — agent sound profile resolution (persona slug -> sound set)
- [ ] tmux `after-select-pane` hook plays the focused agent's `select` sound
- [ ] SubagentStart plays `acknowledge`, SubagentStop plays `complete`

#### Layer 5: Ambient Engine (2h)

- [ ] `lib/ambient.py` — background loop management
- [ ] Ambient volume is one more multiplier in the mixer (not a separate mechanism)
- [ ] PID tracking at `~/.claude/local/voice/ambient.pid`
- [ ] Agent count scaling: 1 agent = subtle, 3+ = richer
- [ ] TTS ducking: ambient multiplier drops to 0.2 during speech, restores after

#### Layer 6: Voice Queue Daemon (3h)

The mixer answers HOW LOUD. The queue answers WHEN. Both are needed for usable multi-agent voice.

- [ ] `scripts/voice_queue.py` — priority heap + Unix socket server (~200 lines)
- [ ] `lib/queue_client.py` — enqueue function, fail-open to direct playback (~80 lines)
- [ ] Priority mapping: theme.json 0/1/2 → queue LOW(20)/NORMAL(50)/CRITICAL(100)
- [ ] Speaker transition: 300ms pause between different agents, no pause for same agent
- [ ] Expiration: items older than 30s dropped from queue
- [ ] Interruption: CRITICAL events preempt current speaker
- [ ] Earcons bypass queue (short, low overlap risk) — only TTS queued
- [ ] Graceful degradation: queue daemon not running → play directly (current behavior)
- [ ] Config: `queue.enabled`, `queue.max_items`, `queue.max_wait_seconds`, `queue.speaker_transition_ms`

Prior art: `LinuxIsCool/claude-plugins-public/plugins/voice/src/coordination/` (6 TypeScript files, ~700 lines). Ported to Python, simplified.

### Exit Criteria

- Focused pane plays at full volume
- Same-window panes play at 50% (configurable)
- Same-session panes play at 20% (configurable)
- Other-session panes are silent unless priority >= 2 (configurable)
- STT cannot be contaminated by TTS from any pane
- Navigating to an agent pane plays that agent's "select" sound
- Ambient drone plays when subagents are running, stops when they finish
- **Agents take turns speaking — never two voices at once**
- **Speaker transitions have 300ms pause between different agents**
- **Stale queued speech expires after 30s**
- All volume decisions logged (focus_state, effective_volume, priority)

### Specs

- [x] `15-tmux-focus-gate.md` — spatial mixer design (updated from binary gate)
- [ ] `18-voice-queue-daemon.md` — turn-taking, priority scheduling, speaker transitions
- [ ] `16-rts-agent-sounds.md` — agent sound taxonomy, profile resolution
- [ ] `17-ambient-engine.md` — loop management, ducking, agent count

---

## 8. Phase 4: STT + Conversation

**Goal**: Shawn speaks and the system hears. Push-to-talk, then wake-word, then full-duplex. Parakeet-TDT replaces faster-whisper. openWakeWord enables "Legion, ..." trigger.

**Entry criteria**: Phase 3.5 complete. Focus gate prevents STT cross-talk.

### Deliverables

- [ ] `lib/stt.py` — Parakeet-TDT-1.1B-v2 STT engine
  - 100x realtime on RTX 4070 (vs ~30x for faster-whisper large-v3-turbo)
  - Cache-aware streaming (Nvidia's key innovation — no re-processing on each chunk)
  - 24ms time-to-final-transcription
  - End-of-utterance (EOU) detection via `parakeet_realtime_eou_120m-v1`
- [ ] Push-to-talk mode (keybind-triggered recording window)
- [ ] openWakeWord integration — "Legion" trigger word
  - Model: ~5MB, CPU-only, always-on monitoring
  - On detection: start STT recording, play "listening" earcon
- [ ] Microphone capture via sounddevice (PipeWire native, 16kHz mono)
- [ ] Silero VAD for voice activity detection (filter noise)
- [ ] Audio preprocessing: noise gate, normalization, 16kHz resample
- [ ] Interrupt handling: when user speaks during TTS, cancel TTS and start STT
  - Nvidia ACE pattern: cache reset on barge-in
- [ ] Acoustic Echo Cancellation (AEC) via PipeWire loopback monitoring
  - TTS output fed back into cancellation module so STT ignores system speech
  - This is the long-term fix for cross-talk (complements focus gate)

### Exit Criteria

- "Legion, what's the status?" → wake word detected → STT transcribes → prompt injected
- Barge-in works: speaking during TTS cancels TTS and captures speech
- Push-to-talk latency p95 < 1000ms from button release to text

### Specs

`07-stt-engine.md` (update), `18-wake-word.md` (new), `19-duplex-conversation.md` (new)

---

## 9. Phase 5: Personality

**Goal**: Each persona has a distinct voice, theme, and character. Gamification tracks XP, levels, achievements.

**Entry criteria**: Phase 4 complete. STT and TTS both working.

### Deliverables

- [ ] `lib/identity.py` — 4-layer identity resolver:
  1. Session override (`/voice theme starcraft`)
  2. Agent persona (character YAML `preferred_theme`, `voice_profile`)
  3. Model default (Opus = deeper, Haiku = lighter)
  4. System default (config.yaml)
- [ ] Persona-to-theme mapping (Matt -> StarCraft, Philipp -> Zelda, etc.)
- [ ] Persona-to-voice mapping (Kokoro voice preset per persona)
- [ ] Emotion system: event type -> TTS parameter modifiers (pitch, pace, warmth)
- [ ] Text transformers: theme-flavored narration templates
  - StarCraft: "Commander, [summary]. Awaiting orders."
  - Zelda: "A discovery awaits... [summary]."
- [ ] `lib/gamification.py` — XP, levels, achievements
  - SQLite WAL schema, 5 XP domains (coding, reviewing, testing, deploying, exploring)
  - `level = floor(k * sqrt(XP))`
  - 30 achievements (first commit, 10-session streak, midnight coder, theme explorer...)
  - Level-up -> unique fanfare sound
  - Sound palette expands with level (basic 3 variants -> full 7 at level 11+)

### Exit Criteria

- Matt persona active -> StarCraft sounds, "Commander" greetings, am_onyx voice
- Level-up triggers fanfare, achievements play unique unlock sounds
- XP persists across sessions (SQLite at `~/.claude/local/voice/`)

### Specs

`08-identity-personality.md`, `09-gamification.md`

---

## 10. Phase 6: Integration

**Goal**: Unified audio-visual experience. Sound, visuals, rhythm, and data flow together.

**Entry criteria**: Phase 5 complete.

### Deliverables

- [ ] claude-statusline voice awareness (mute indicator, TTS status, pane focus)
- [ ] Ambient soundscapes by time of day (rhythms bridge)
  - 10 rhythm phases -> ambient profiles (dawn=birdsong, night=deep space drone)
  - Volume auto-duck during TTS
- [ ] Rhythms brief voice delivery (morning/evening briefs narrated on SessionStart)
- [ ] Observatory health sonification (CPU temp -> warning hum, disk critical -> urgent ping)
- [ ] Matrix cross-agent pitch differentiation (agent_id hash -> pitch offset)
- [ ] claude-logging voice event tracking (VoiceSoundPlayed, VoiceTTSGenerated, etc.)
- [ ] Benchmark CLI (`scripts/benchmark.py`) — p50/p95/p99 latency
- [ ] Full test suite (unit, integration, fuzz, performance, >90% coverage)

### Exit Criteria

- Morning session: dawn ambient + spoken brief + health earcon
- Benchmark p95 < 150ms across all themes
- Test suite >90% coverage on lib modules

### Specs

`10-rhythms-integration.md`, `12-quality-testing.md`

---

## 11. Phase 7: Autonomy (Speech-to-Reality)

**Goal**: Voice-driven multi-agent workflows. Speak -> intent -> dispatch -> narrate -> complete.

**Entry criteria**: Phase 6 complete.

### Deliverables

- [ ] Intent parser (keyword fast-path + LLM fallback via claude-llms 3-tier)
- [ ] Voice command router (intent -> Claude Code action / plugin command / query)
- [ ] Confirmation protocol for destructive actions ("Deploy to production?" -> "yes" / "cancel")
- [ ] Multi-agent dispatch from voice ("Fix the login bug and update tests" -> 2 subagents)
- [ ] Real-time narration of agent actions (throttled: max 1 utterance / 5 seconds)
- [ ] Feedback loop: listening tone -> heard chirp -> understood tone -> deploy earcon -> narrated result
- [ ] Learning system: track command success/failure, build keyword patterns from LLM classifications

### Exit Criteria

"Fix the login bug" -> STT -> intent -> subagent -> narrates "Investigating auth.py... found null check... applying fix... tests passing" -> "Commander, the login bug has been resolved."

### Specs

`14-speech-to-reality.md` (to be written)

---

## 12. Dependency Graph

```
Phase 1 ──> Phase 2 ──> Phase 3 ──> Phase 3.5 ──> Phase 4 ──> Phase 5 ──> Phase 6 ──> Phase 7
scaffold    themes      TTS         multi-agent    STT+conv    personality  integration  autonomy
hooks       synthesis   Kokoro      SPATIAL MIXER  Parakeet    identity     rhythms      speech2real
pw-play     variants    daemon      per-pane route wake word   gamification statusline   intent parse
            hot-swap    caching     RTS sounds     duplex      emotion      matrix       learning
            7 worlds    systemd     ambient engine AEC         text xform   testing
```

### Key Resequencing Rationale

**Phase 3.5 before Phase 4 (STT)**: The spatial mixer MUST exist before STT is built. Without it, the STT engine would transcribe other agents' TTS output as user speech. Building STT without spatial volume control would produce a broken system.

**Phase 5 (Personality) after Phase 4 (STT)**: Gamification, XP, and achievements are reward systems for an interaction loop that doesn't fully exist until STT is working. Building the reward system before the interaction loop is premature.

**Phase 6 (Integration) after Phase 5 (Personality)**: Rhythms ambient, observatory sonification, and matrix integration assume identity resolution is working. The integration layer composes subsystems that must each be solid first.

---

## 13. Cross-Phase Component Map

```
lib/constants.py ──────────────────────────────────────────> (all phases — single source of truth)
lib/utils.py ──────────────────────────────────────────────> (all phases — deep_merge, cache_key)
lib/audio.py ──────────────────────────────────────────────> (all phases, add --target in 3.5)
lib/router.py ─────────────────────────────────────────────> (3.5: spatial mixer, 5: identity, 7: intent)
  └── _get_focus_state() + effective_volume() ─────────────> (core volume pipeline, used by ALL sound dispatch)
lib/theme.py ──────────> lib/identity.py ──────────────────> (Phase 5 adds persona resolution)
lib/state.py ──────────────────────────────────────────────> (all phases)
lib/tts.py ────────────> lib/identity.py ──> lib/rhythms.py > (voice selection cascades)
lib/agents.py ──────────> lib/ambient.py ──────────────────> (Phase 3.5: RTS sounds + ambient)
lib/stt.py ────────────> lib/wake.py ──────> lib/duplex.py > (Phase 4: STT chain)
lib/gamification.py ──> lib/router.py ─────────────────────> (level-up events feed routing)
```

---

## 14. Risk Register

| # | Risk | P | I | Phase | Mitigation |
|---|------|---|---|-------|------------|
| R1 | tmux focus detection adds latency (subprocess call) | M | M | 3.5 | Cache focused pane ID in file (Layer 1), read file instead of spawning tmux. ~1ms file read vs ~5ms subprocess. |
| R2 | PipeWire per-PID mute requires WirePlumber 0.5+ | L | H | 3.5 | Version check at startup. Fallback: file-based focus gate (Layer 0) works without WirePlumber features. |
| R3 | Ambient loops leak (PID not tracked, orphan processes) | M | M | 3.5 | Health check in `ambient.py.check_running()`. Cleanup on SessionEnd. systemd user service for daemon manages lifecycle. |
| R4 | Parakeet-TDT model not available for local inference | M | H | 4 | Fallback to faster-whisper large-v3-turbo (already installed). Parakeet is preferred but not required. |
| R5 | Wake word false positives | M | L | 4 | openWakeWord sensitivity tuning. Require "Legion" + pause + command structure. Log all activations for calibration. |
| R6 | Sound fatigue from too many earcons | H | M | all | Per-event toggles, mute, debounce (500ms), volume granularity, Pissed category (frustration detection). |
| R7 | GPU VRAM contention (Kokoro + Parakeet + other workloads) | M | M | 4 | Lazy loading, priority system (active STT > queued TTS > idle model). Piper CPU fallback. VRAM budget: Kokoro 555MB + Parakeet ~500MB = ~1GB of 12GB. |
| R8 | Concurrent subagent audio produces cacophony | M | M | 3.5 | fcntl playback lock, priority system, ambient volume ducking, focus gate for TTS. |
| R9 | AEC (acoustic echo cancellation) adds latency | L | M | 4 | PipeWire native AEC module. If latency unacceptable, fall back to STT-active flag (software gate from Phase 3.5). |
| R10 | Intent parsing in Phase 7 produces wrong actions | M | H | 7 | Confirmation protocol for destructive actions. Keyword fast-path is deterministic. LLM escalation only for ambiguous. |

---

## 15. Success Metrics

### Quantitative

| Metric | Ph 1 | Ph 2 | Ph 3 | Ph 3.5 | Ph 4 | Ph 5 | Ph 6 | Ph 7 |
|--------|------|------|------|--------|------|------|------|------|
| Earcon latency p95 | <150ms | <150ms | <150ms | <150ms | <150ms | <150ms | <150ms | <150ms |
| TTS latency p95 | -- | -- | <700ms | <700ms | <500ms | <500ms | <500ms | <500ms |
| STT latency p95 | -- | -- | -- | -- | <1000ms | <1000ms | <1000ms | <1000ms |
| Theme count | 1 | 7 | 7 | 7 | 7 | 7 | 7 | 7+ |
| Sound assets | 36 | 324+ | 324+ | 360+ | 360+ | 360+ | 360+ | 360+ |
| Hook events wired | 6 | 10 | 10 | 10 | 10 | 10 | 14 | 14 |
| Focus gate (cross-talk prevention) | -- | -- | -- | 100% | 100% | 100% | 100% | 100% |
| Test coverage (lib/) | -- | >60% | >60% | >70% | >75% | >85% | >90% | >90% |

### Qualitative

| Phase | Target Feeling |
|-------|---------------|
| 1 | "It works — I hear a sound when Claude finishes" |
| 2 | "This is fun — StarCraft sounds make coding feel like a game" |
| 3 | "Claude talks to me — the terminal is alive" |
| 3.5 | "Only the agent I'm looking at speaks — the rest hum quietly" |
| 4 | "I can talk to Legion and it hears me — hands-free coding" |
| 5 | "It knows who I am — Matt's voice, my theme, my achievements" |
| 6 | "Everything is connected — sound, visuals, rhythm, awareness" |
| 7 | "I can't go back — voice-driven development is the standard now" |

---

## 16. Non-Goals

- Mobile/web support (terminal-only, PipeWire backend)
- Multi-user/collaborative audio
- Real-time music generation (pre-generated WAV loops only)
- Video/visual effects beyond tmux status bar
- Cross-platform (Linux only, PipeWire required)
- Marketplace distribution (local legion-plugins only)
- Accessibility compliance (audio feedback, not accessibility tool)

---

## 17. Spec Index

| # | File | Title | Phase | Status |
|---|------|-------|-------|--------|
| 01 | `01-plugin-scaffold.md` | Plugin Scaffold | 1 | complete |
| 02 | `02-theme-engine.md` | Theme Engine | 2 | complete |
| 03 | `03-sound-synthesis.md` | Sound Synthesis | 2 | complete |
| 04 | `04-hook-architecture.md` | Hook Architecture | 1 | complete |
| 05 | `05-audio-playback.md` | Audio Playback | 1 | complete |
| 06 | `06-tts-engine.md` | TTS Engine | 3 | in progress |
| 07 | `07-stt-engine.md` | STT Engine | 4 | draft (update for Parakeet) |
| 08 | `08-identity-personality.md` | Identity & Personality | 5 | draft |
| 09 | `09-gamification.md` | Gamification | 5 | draft |
| 10 | `10-rhythms-integration.md` | Rhythms Integration | 6 | draft |
| 11 | `11-asset-management.md` | Asset Management | 2 | complete |
| 12 | `12-quality-testing.md` | Quality & Testing | 6 | draft |
| 13 | `13-elevenlabs-deep.md` | ElevenLabs (deferred) | 3 | deferred |
| 14 | `14-speech-to-reality.md` | Speech-to-Reality | 7 | planned |
| 15 | `15-tmux-focus-gate.md` | tmux Focus Gate | 3.5 | **NEW — to write** |
| 16 | `16-rts-agent-sounds.md` | RTS Agent Sound Taxonomy | 3.5 | **NEW — to write** |
| 17 | `17-ambient-engine.md` | Ambient Engine | 3.5 | **NEW — to write** |
| 18 | `18-wake-word.md` | Wake Word (openWakeWord) | 4 | **NEW — to write** |
| 19 | `19-duplex-conversation.md` | Duplex Conversation & AEC | 4 | **NEW — to write** |

---

## 18. Open Questions

| # | Question | Phase | Status |
|---|----------|-------|--------|
| Q1 | Should earcons from unfocused panes play at reduced volume or not at all? Current lean: play at 30% volume. | 3.5 | open |
| Q2 | Should the "Pissed" category (repeated commands) produce a distinct sound? WC3 uses it as frustration valve. | 3.5 | open |
| Q3 | Parakeet-TDT vs faster-whisper: is the 100x speedup worth the model switch cost? Need to benchmark on RTX 4070. | 4 | open |
| Q4 | Should wake word ("Legion") activate only in the focused pane, or globally? Global = hands-free from any pane. | 4 | open |
| Q5 | Should gamification XP persist across Claude Code upgrades? Lean: yes (SQLite at `~/.claude/local/voice/`). | 5 | open |
| Q6 | Should ambient soundscapes crossfade between phases or hard-cut? Crossfade is more immersive, harder to implement. | 6 | open |
| Q7 | Should the learning system (Phase 7) share intent patterns via KOI? | 7 | open |

---

## 19. What Matters Most Along the Way

1. **Each phase must leave a working system.** If you stop at any phase, what you have is useful. Never break the previous phase to build the next.
2. **Study before design.** The research for Phase 3.5 (tmux focus events, PipeWire routing, RTS audio, Nvidia ACE) was done BEFORE writing this roadmap. That's the pattern for every phase.
3. **Census before action.** Before implementing anything, audit what exists. The voice audit revealed that 90% of the infrastructure for Phase 3.5 was already present on the machine.
4. **Parsimony.** Choose the simplest mechanism that solves the problem. File-based state (Layer 1) before PipeWire virtual sinks (Layer 3). `TMUX_PANE` check before WirePlumber policy rules.
5. **Multiplication over branching.** The spatial volume mixer replaces if/else gates with volume × multiplier. Each new feature (STT suppression, ambient duck, priority floor) is one more multiplier in the pipeline — not a new code path. The simplification IS the generalization.
6. **DRY is structural.** Constants live in `constants.py`, functions in `utils.py`. One source of truth, imported everywhere. Never duplicate a value — future you will change one copy and miss the other.
7. **Parallelize by default.** Research agents run in background. Specs are written in parallel with implementation. Multiple layers within a phase can be developed concurrently.
8. **Version control everything.** Every spec, every config, every sound asset. Git is memory.
9. **Document the WHY.** Specs explain design decisions. This roadmap explains sequencing decisions. Journal entries capture context that would otherwise be lost between sessions.
10. **Test the boundary, not the interior.** Hook-to-audio latency matters more than unit test coverage of internal functions. The 150ms budget is the real test.
11. **Taste matters.** Sounds should feel natural. TTS should feel alive. The system should be something you want to use, not something you tolerate.
12. **This is robotics.** Hooks are senses. Scripts are muscles. Sound is voice. The mental model is embodied cognition, not software engineering.
