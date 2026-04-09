---
title: "claude-voice — Implementation Roadmap (ARCHIVED)"
status: archived
created: 2026-03-26
archived: 2026-03-27
author: matt
superseded_by: ROADMAP2.md
tags: [claude-voice, roadmap, implementation, milestones, archived]
---

# claude-voice — Implementation Roadmap (ARCHIVED)

> **This document is archived.** Superseded by [ROADMAP2.md](./ROADMAP2.md) on 2026-03-27.
>
> **Reason for archival:** Roadmap v1 was designed as a sequential 6-phase plan before multi-agent voice was the live environment. Phase 5 (tmux integration) was sequenced after Phase 4 (personality/gamification), but operational reality proved that tmux focus gating is a prerequisite for using TTS at all in a multi-pane environment. ROADMAP2.md resequences to front-load the focus gate as Phase 3.5, moves STT to its own phase (4), and defers personality/gamification to Phase 5.
>
> **Status at time of archival:**
> - Phase 1 (Foundation): COMPLETE
> - Phase 2 (Theme Engine): COMPLETE
> - Phase 3 (Voice I/O): PARTIAL — TTS daemon live (Kokoro-82M, 689ms warm), STT absent
> - Phase 4 (Personality): NOT STARTED
> - Phase 5 (Integration): NOT STARTED
> - Phase 6 (Autonomy): NOT STARTED

## 1. Overview

This roadmap defines the implementation sequence for claude-voice: from bare plugin scaffold to full speech-to-reality autonomous pipeline. 6 phases, each building on the last, each producing a shippable increment.

Every phase ends with a working system. Phase 1 plays a sound when a task completes. Phase 6 lets you say "fix the login bug" and hear narrated progress as agents find, fix, test, and report back. The intervening phases add themes, voice, personality, cross-plugin integration, and finally autonomous voice-driven workflows.

The design is intentionally front-loaded — Phase 1 delivers immediate value (audio feedback on hook events) with zero cloud dependencies, zero GPU requirements, and zero configuration. Each subsequent phase layers capability without breaking the previous. If you stop at any phase, you have a useful system.

All specs referenced below live in `specs/`. All file paths are relative to the plugin root (`~/.claude/plugins/local/legion-plugins/plugins/claude-voice/`). Runtime state lives at `~/.claude/local/voice/`.

---

## 2. Phase Summary

| Phase | Name | Deliverables | Est. Effort | Dependencies |
|-------|------|-------------|-------------|-------------|
| 1 | Foundation | Scaffold + hook script + pw-play + 1 theme (default) | 1-2 days | None |
| 2 | Theme Engine | Theme system + sound synthesis + all 6 themes | 2-3 days | Phase 1 |
| 3 | Voice I/O | TTS (ElevenLabs + local) + STT integration | 3-5 days | Phase 2 |
| 4 | Personality | Identity resolver + gamification + emotion | 2-3 days | Phase 3 |
| 5 | Integration | Rhythms + observatory + tmux sync + matrix | 2-3 days | Phase 4 |
| 6 | Autonomy | Speech-to-reality pipeline + claude-llms routing | 5-10 days | Phase 5 |

**Total estimated effort: 15-26 days** (non-contiguous, interleaved with other work).

---

## 3. Phase 1: Foundation (Minimum Viable Sound)

**Goal**: Claude Code plays a sound when a task completes. One theme, twelve events, fire-and-forget pw-play. The simplest possible thing that produces audio feedback on hook events.

**Entry criteria**: None. This is the starting phase.

**Deliverables**:

- [x] Plugin directory structure (`.claude-plugin/plugin.json`)
- [x] `hooks/voice_event.py` — single UV script handling 10 events. PEP 723 inline metadata. Reads stdin JSON, classifies event, plays sound, prints `{}`, exits 0. Never crashes.
- [x] `lib/audio.py` — pw-play wrapper with fallback chain (pw-play -> paplay -> aplay -> mpv). `subprocess.Popen` with `start_new_session=True`. Volume control via pw-play `--volume` flag.
- [x] `lib/router.py` — event-to-sound routing. Content-aware parsing on Stop events (git commit detection, error detection).
- [x] `lib/theme.py` — theme.json loader with deep-merge inheritance.
- [x] `lib/state.py` — atomic config read/write. fcntl locking for concurrent access safety.
- [x] Quality gates: try/except around all hook logic. Enforces `{}` stdout and exit 0 under all failure modes.
- [x] `assets/themes/default/` — default theme with 12 synthesized sounds, 3 variants each = 36 WAV files.
- [x] `assets/themes/default/theme.json` — theme definition.
- [x] `config.yaml` — runtime config at `~/.claude/local/voice/config.yaml`.
- [x] `scripts/play_test.py` — sound test utility.
- [x] `SKILL.md` — master skill entry point.
- [x] `CLAUDE.md` — agent-facing documentation.
- [x] `pyproject.toml` — UV project configuration.

**Exit criteria**: Running `echo '{"session_id":"test","type":"Stop","data":{"last_assistant_message":"done"}}' | uv run hooks/voice_event.py Stop` plays a completion sound through PipeWire within 150ms.

**Specs**: `01-plugin-scaffold.md`, `04-hook-architecture.md`, `05-audio-playback.md`

**Key decisions**:
- Default theme uses simple synthesized tones — no game theming yet. Clean, neutral, functional.
- Only 6 of 14 possible hook events wired in Phase 1. The remaining 8 (UserPromptSubmit, PostToolUseFailure, PermissionRequest, PreCompact, plus 4 reserved) are added in Phase 2 when the theme engine can handle them properly.
- No TTS, no STT, no gamification, no identity. Pure earcon feedback.
- Config file created on first hook invocation if it does not exist (sensible defaults).

---

## 4. Phase 2: Theme Engine (6 Game Worlds)

**Goal**: Hot-swappable themed sound packs with variant randomization. The user says `/voice theme starcraft` and every subsequent sound comes from the StarCraft sonic universe.

**Entry criteria**: Phase 1 complete. Default theme plays sounds on all 6 initial hook events.

**Deliverables**:

- [x] `scripts/generate_sounds.py` — numpy+scipy synthesis pipeline.
- [x] 7 complete themes (default + 6 game themes):
  - [x] **StarCraft** — Digital military. Sharp transients, metallic timbres, radio chirps.
  - [x] **Warcraft** — Fantasy organic. War drums, horn brass, wooden impacts.
  - [x] **Mario** — Cheerful chiptune. Square waves, bright bells, bouncy bass.
  - [x] **Zelda** — Mystical melodic. Harp arpeggios, ocarina tones, crystalline chimes.
  - [x] **Smash Bros** — Competitive punchy. Impact hits, announcer stabs, crowd energy.
  - [x] **Kingdom Hearts** — Orchestral emotional. String ensemble, choir pads, piano arpeggios.
- [x] Theme inheritance via deep merge.
- [x] Hot-swap (change theme in config, next hook uses new theme).
- [x] Content-aware Stop routing (git commit, error, test pass/fail).
- [x] Variant randomization with recency avoidance.
- [x] All 10 hook events wired in plugin.json (UserPromptSubmit disabled by default).
- [ ] ~~Subskills~~ (deferred to Phase 5)

**Exit criteria**: `/voice theme starcraft` changes all sounds immediately. `scripts/play_test.py --all-themes` passes with zero missing variants, zero format violations.

**Specs**: `02-theme-engine.md`, `03-sound-synthesis.md`, `11-asset-management.md`

**Key decisions**:
- All sounds are procedurally generated. Zero sampling from copyrighted game audio. Every waveform is original — inspired by the game's sonic identity but synthesized from mathematical primitives (sine, square, sawtooth, noise, FM synthesis).
- Variant counts vary by event importance: `task_complete` gets 5-7 (heard most often, habituation risk highest), `session_end` gets 3 (heard once, habituation irrelevant).
- Total asset size target: <5MB across all themes. WAV files at 100-800ms duration, 48kHz stereo, are 10-75KB each.

---

## 5. Phase 3: Voice I/O (Speak and Listen)

**Goal**: Claude narrates task completions and key events. The user can optionally speak commands via push-to-talk. Dual TTS backend: ElevenLabs for premium cloud voice, Kokoro-82M for zero-latency local GPU inference. Piper as offline CPU fallback.

**Entry criteria**: Phase 2 complete. All 6 themes working with hot-swap.

**Deliverables**:

_Phase 3 partially complete. TTS operational, STT moved to separate phase. See ROADMAP2.md for current plan._

- [x] `lib/tts.py` — dual backend TTS engine abstraction (Kokoro-82M local, daemon mode):
  - [ ] ElevenLabs Flash v2.5 integration (API v1, streaming WebSocket, 75ms first-byte latency). Voice catalog with 32+ premium voices. Cost tracking per session.
  - [ ] Kokoro-82M local TTS (RTX 4070 12GB, <100ms latency, 8 built-in voices, Apache 2.0). Lazy model loading — only loads when first TTS request arrives, not on hook startup.
  - [ ] Piper TTS fallback (CPU ONNX inference, ~200ms latency). For when GPU is busy with model training or inference workloads.
  - [ ] Backend selection logic: local GPU preferred when available, cloud when local is unavailable or when premium voice quality is requested. Configurable per-theme in theme.json `tts_voice` section.
- [ ] `lib/stt.py` — faster-whisper with Silero VAD:
  - [ ] Microphone capture via sounddevice (PipeWire native).
  - [ ] Silero VAD for voice activity detection — only transcribe when speech is present.
  - [ ] faster-whisper large-v3-turbo on RTX 4070 (pre-installed in whisperx-env). CTranslate2 backend.
  - [ ] Push-to-talk mode (keybind-triggered recording window).
  - [ ] Audio preprocessing: noise gate, normalization, 16kHz resample for Whisper input.
- [ ] TTS caching: SHA256 hash of (text + voice_id + provider) as cache key. WAV files stored at `~/.claude/local/voice/cache/tts/`. LRU eviction when cache exceeds 500MB. Cache hit skips synthesis entirely — just plays the cached WAV.
- [ ] fcntl TTS queue: serialized speech via `~/.claude/local/voice/tts.lock`. When multiple subagents fire SubagentStop simultaneously, TTS requests queue rather than overlap. LOCK_EX with LOCK_NB — if lock is held, caller waits up to 2 seconds, then drops the request.
- [ ] SubagentStop summary narration: when a subagent completes, extract a <20 word summary from `last_assistant_message` and speak it. Text compression pipeline: strip code blocks, extract first sentence of conclusion, compress to spoken-length.
- [ ] Audio state updates: `~/.claude/local/voice/state.json` updated with `tts_active: true/false` during speech. Consumed by claude-statusline.
- [ ] Subskills:
  - [ ] `skills/voice/subskills/tts.md` — TTS configuration, voice selection, provider switching
  - [ ] `skills/voice/subskills/stt.md` — STT activation, push-to-talk setup, transcription testing

**Exit criteria**: SubagentStop triggers themed earcon PLUS spoken narration: "Commander, task complete — tests passing" in the StarCraft theme voice. TTS latency p95 < 500ms. STT push-to-talk transcribes speech to text within 1 second.

**Specs**: `06-tts-engine.md`, `07-stt-engine.md`, `13-elevenlabs-deep.md` (to be written)

**Key decisions**:
- TTS is opt-in, not default. Earcons play by default; TTS narration requires explicit enablement in config.yaml (`tts_enabled: true`). Sound effects alone are the baseline experience.
- Kokoro-82M is the preferred default TTS backend. ElevenLabs is for users who want premium voice quality and have an API key configured. Piper is the last resort.
- STT is entirely Phase 3 infrastructure but Phase 6 functionality. The STT engine is built and tested here, but voice command parsing (intent recognition, command routing) is Phase 6. Phase 3 STT is push-to-talk transcription only.
- GPU VRAM budget: Kokoro-82M uses ~500MB VRAM. faster-whisper large-v3-turbo uses ~1.5GB. Both can coexist on the RTX 4070 12GB alongside normal Claude Code operations. Lazy loading ensures neither is loaded until needed.

---

## 6. Phase 4: Personality (Who's Speaking)

**Goal**: Each persona has a distinct voice and character. Matt speaks with StarCraft military precision. Philipp narrates with Zelda's mystical reverence. The gamification system tracks XP, levels, and achievements, reinforcing productive habits with audio rewards.

**Entry criteria**: Phase 3 complete. TTS working with at least one backend.

**Deliverables**:

- [ ] `lib/identity.py` — 4-layer identity resolver:
  - Layer 1: **Session override** — explicit theme/voice set via `/voice` command this session.
  - Layer 2: **Agent persona** — if a claude-personas character YAML defines `preferred_theme` and `voice_profile`, use those.
  - Layer 3: **Model default** — model-specific defaults (e.g., Opus gets a deeper voice than Haiku).
  - Layer 4: **System default** — falls back to config.yaml global settings.
  - Resolution is top-down: first non-null value wins.
- [ ] `lib/gamification.py` — XP, levels, achievements:
  - SQLite WAL schema: `events` (session_id, event, xp, ts), `levels` (domain, xp_total, level), `achievements` (id, name, unlocked_at, session_id), `streaks` (domain, current, longest, last_date).
  - Level curve: `level = floor(k * sqrt(XP))` where k is calibrated so level 10 = ~1000 XP.
  - 5 XP domains: coding, reviewing, testing, deploying, exploring. Events map to domains.
  - XP awards on every hook event (configurable per-event in theme.json).
  - Level-up detection: when XP crosses a level boundary, queue a `level_up` sound event with the new level number.
- [ ] Persona-to-theme mapping:
  - Matt → StarCraft (military precision, "Commander" greetings)
  - Philipp → Zelda (mystical analysis, "Hero" references)
  - Andrej → Kingdom Hearts (epic orchestral, teaching tone)
  - Trent → Warcraft (organic strategy, "Warchief" address)
  - Luke → Mario (cheerful energy, playful encouragement)
  - Danilo → Smash Bros (competitive intensity, arena hype)
- [ ] Persona-to-voice mapping: each persona defines an ElevenLabs voice_id and a Kokoro voice preset. When a persona is active, TTS automatically uses their voice.
- [ ] Emotion system: event type maps to an emotional modifier that adjusts TTS parameters:
  - `task_complete` → satisfied (slightly lower pitch, moderate pace)
  - `error` → concerned (faster pace, higher pitch)
  - `commit` → proud (slower pace, fuller tone)
  - `level_up` → excited (higher pitch, faster pace)
  - `session_start` → welcoming (warm tone, moderate pace)
  - Modifiers are percentage adjustments to TTS stability and similarity_boost parameters.
- [ ] Text transformers: theme-flavored narration templates.
  - StarCraft: "Commander, [summary]. Awaiting orders."
  - Zelda: "A discovery awaits... [summary]. The path forward is clear."
  - Mario: "Yahoo! [summary]! Let's-a go!"
  - Each theme defines 5-10 narration templates per event type. Random selection with recency avoidance.
- [ ] 30 achievements defined and implemented:
  - First session, first commit, first error survived, 10-session streak, 100 tasks completed, midnight coder, dawn patrol, theme explorer (tried all 6), volume master, mute toggle, speed demon (sub-50ms hook), marathon session (4+ hours), and 18 more.
  - Each achievement has a unique sound (short fanfare, 300-600ms) that plays on unlock.
  - Achievements are persistent (SQLite) and never re-trigger.
- [ ] Sound escalation by level: as the user levels up, variant pools expand. Level 1-5 gets the basic 3 variants. Level 6-10 unlocks 2 more per event. Level 11+ gets the full 7. This gives audible progression — the sonic palette literally grows with the user.
- [ ] `agents/narrator.md` — voice narrator agent persona definition. Invokable via Task tool for complex narration tasks (multi-paragraph summaries, story-mode session recaps).

**Exit criteria**: Matt persona active → StarCraft sounds play, TTS speaks with Matt's voice, greetings use "Commander" template, XP accumulates in coding domain, level-up triggers fanfare.

**Specs**: `08-identity-personality.md`, `09-gamification.md`

**Key decisions**:
- Gamification is opt-in but on by default. XP tracking starts immediately; achievements unlock silently (just a sound, no interruption). Users who find it annoying can disable via `config.yaml` (`gamification_enabled: false`).
- Persona resolution is read-only — claude-voice never writes to claude-personas character files. It only reads `preferred_theme` and `voice_profile` fields.
- The emotion system modifies TTS parameters only, never earcon selection. Earcons are theme-determined; emotions color the spoken narration.

---

## 7. Phase 5: Integration (The Living Terminal)

**Goal**: Unified audio-visual experience across all Legion plugins. The terminal becomes a living environment where sound, visuals, rhythm, and data flow together. Morning sessions start with dawn ambience and a spoken brief. Health alerts have sonic urgency. Cross-agent operations have spatial audio identity.

**Entry criteria**: Phase 4 complete. Identity resolution and gamification working.

**Deliverables**:

- [ ] tmux visual+audio sync:
  - Shared semantic token vocabulary between claude-voice and claude-tmux. When a hook fires, claude-tmux changes the status bar indicator color/glyph AND claude-voice plays the corresponding sound. Both plugins independently consume the same hook events; alignment is by convention via matching `hook_to_state` mappings.
  - Theme alignment: when `/voice theme starcraft` changes the sound theme, the tmux visual theme can optionally follow (if claude-tmux supports theme commands).
- [ ] Ambient soundscapes by time of day:
  - `lib/rhythms.py` — Rhythms Bridge reads phase state from `~/.claude/local/rhythms/state.json`.
  - 10 rhythm phases map to ambient sound profiles: dawn (digital birdsong, gentle hum), morning (focused energy, subtle pulse), midday (steady hum), afternoon (warm tones), evening (winding down, softer palette), night (deep space drone, minimal).
  - Ambient runs as a separate long-lived `pw-play` process with `--loop`. Crossfades between phases. Independently mutable (`ambient_muted: true` in config).
  - Volume auto-adjustment: ambient ducks to 20% during TTS speech, restores after.
- [ ] Rhythms brief voice delivery:
  - Morning summary: claude-rhythms writes a brief to `~/.claude/local/voice/queue/briefs/`. claude-voice picks it up and narrates via TTS on next SessionStart.
  - Evening review: same mechanism, triggered by evening rhythm phase.
  - Brief queue is FIFO, one brief per session start maximum (no queue pile-up).
- [ ] Observatory health sonification:
  - CPU temperature above threshold → low-frequency warning hum.
  - Disk usage critical → urgent ping (reuses `notification` event at elevated priority).
  - Memory pressure → subtle tonal shift in ambient soundscape.
  - Read from system metrics (psutil), threshold-based triggers, mapped to sound events.
- [ ] Matrix cross-agent sounds:
  - Each matrix agent gets a unique tonal modifier (pitch shift) based on agent_id hash.
  - Agent registry at `~/.claude/local/voice/agents.json` maps agent_id to pitch offset.
  - When multiple agents are running, their sounds are distinguishable by pitch even with the same theme.
  - fcntl playback lock prevents sound pile-up from simultaneous agent events.
- [ ] claude-logging sound event tracking:
  - Every sound event (played, skipped, failed, cached-hit) logged to claude-logging with latency metrics.
  - Event type: `VoiceSoundPlayed`, `VoiceTTSGenerated`, `VoiceTTSCached`, `VoiceSoundSkipped`.
  - Enables observatory dashboards: sound frequency analysis, habituation detection, latency regression tracking.
- [ ] Benchmark CLI and regression suite:
  - `scripts/benchmark.py` — measures hook-to-audio latency across all events and themes. Reports p50, p95, p99. Writes results to `~/.claude/local/voice/benchmarks/`.
  - Latency budget enforcement: benchmark failures (p95 > 150ms) produce warnings. CI can gate on this.
- [ ] Quality gates enforcement:
  - `lib/gates.py` extended with theme asset validation (all themes complete, all formats correct).
  - Runtime degradation detection: if hook latency exceeds 100ms for 3 consecutive invocations, log a warning to claude-logging.
- [ ] Full test suite:
  - Unit tests for all lib modules (router, theme, audio, tts, stt, identity, gamification, gates).
  - Integration tests: end-to-end hook invocation with mock stdin, verify sound file selection and Popen call.
  - Fuzz tests: random/malformed stdin JSON, verify no crashes (always exit 0, always `{}` stdout).
  - Performance tests: measure hook wall time under load, verify 150ms budget.

**Exit criteria**: Morning session starts with dawn ambient soundscape + spoken rhythm brief + health report earcon. `scripts/benchmark.py` shows p95 < 150ms across all themes and events. Test suite passes with >90% coverage on lib modules.

**Specs**: `10-rhythms-integration.md`, `12-quality-testing.md`

**Key decisions**:
- Ambient soundscapes are an optional layer. They require claude-rhythms to be running. If rhythms state file is missing, ambient is silently skipped.
- Observatory sonification is threshold-based, not continuous. No constant background noise from system metrics — only alerts when values cross defined thresholds.
- Matrix agent pitch differentiation is deterministic (hash-based) so the same agent always sounds the same across sessions.

---

## 8. Phase 6: Autonomy (Speech-to-Reality)

**Goal**: Voice-driven multi-agent workflows. The user speaks a natural language command, claude-voice transcribes it, parses intent, dispatches to Claude Code or subagents, and narrates progress in real-time. The full loop: speech in, action, speech out.

**Entry criteria**: Phase 5 complete. All integrations stable. TTS and STT both working reliably.

**Deliverables**:

- [ ] Intent parser:
  - Keyword-based fast path for common commands ("commit", "test", "deploy", "status", "theme", "mute").
  - LLM classification fallback for complex/ambiguous intents. Routes through claude-llms 3-tier system.
  - Intent categories: code action (fix, refactor, test, deploy), plugin command (theme, volume, mute), information query (status, health, stats), workflow trigger (morning brief, evening review).
- [ ] Voice command router:
  - Maps parsed intents to Claude Code actions. Code actions become user prompts injected into the session. Plugin commands execute directly. Queries trigger TTS responses.
  - Confirmation protocol: destructive actions (deploy, delete, force-push) require spoken confirmation before execution. "Deploy to production?" — user says "yes" or "cancel."
- [ ] Multi-agent dispatch from voice:
  - Complex voice commands can spawn subagents via claude-matrix. "Fix the login bug and update the tests" → two subagents, parallel execution.
  - Agent progress narrated in real-time: "Agent Alpha is investigating... found the issue in auth.py... applying fix..."
- [ ] Real-time narration of agent actions:
  - SubagentStart → "Deploying agent for [task summary]"
  - Tool use events → brief audio cues (typing sounds, file open sounds)
  - SubagentStop → "[Agent] reports: [summary]"
  - Narration is throttled: no more than one TTS utterance per 5 seconds to avoid verbal overload.
- [ ] claude-llms 3-tier routing for intent parsing:
  - Tier 1: Local keyword matching (zero latency, handles 60% of commands).
  - Tier 2: Local LLM intent classification via Ollama (if available, <500ms).
  - Tier 3: Cloud LLM classification via Claude API (for ambiguous intents, <1000ms).
  - Routing decision based on keyword match confidence. High confidence → Tier 1. Low confidence → escalate.
- [ ] Feedback loop: every stage of the voice-to-action pipeline produces audio:
  - STT capture start → subtle "listening" tone
  - Transcription complete → "heard" confirmation chirp
  - Intent parsed → "understood" tone (different pitch for different intent categories)
  - Action dispatched → agent_deploy earcon
  - Action complete → task_complete earcon + TTS narration of result
  - Error at any stage → error earcon + spoken error description
- [ ] Learning system:
  - Track which voice commands succeed vs. fail. Log to SQLite.
  - Track which intents required LLM escalation. Build keyword patterns from successful LLM classifications.
  - Over time, Tier 1 keyword matching improves and handles a larger percentage of commands without LLM calls.
  - Correction handling: if the user immediately re-phrases after a failed command, log the correction pair for future matching.

**Exit criteria**: User says "Fix the login bug" → STT transcribes within 1 second → intent parsed as code action → subagent dispatched → agent narrates "Investigating auth.py... found null check issue... applying fix... tests passing" → completion fanfare + "Commander, the login bug has been resolved. All tests green."

**Specs**: `14-speech-to-reality.md` (to be written)

**Key decisions**:
- Voice commands never bypass Claude Code's permission system. Destructive actions still require explicit confirmation (spoken or typed).
- Real-time narration is throttled aggressively. The goal is situational awareness, not a running commentary. One utterance per 5 seconds maximum.
- The learning system is local-only. No telemetry, no cloud training data. Keyword improvements are stored in the local SQLite gamification database.
- Phase 6 is the most speculative phase. Effort estimate (5-10 days) has the widest range because intent parsing quality depends heavily on the LLM tier available and the diversity of voice commands encountered.

---

## 9. Dependency Graph

```
Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 4 ──→ Phase 5 ──→ Phase 6
scaffold     themes      voice I/O   identity    integration  autonomy
hooks        synthesis   TTS/STT     gamification rhythms     speech-to-reality
pw-play      variants    ElevenLabs  emotion      tmux sync   claude-llms
             hot-swap    Kokoro      achievements testing     intent parsing
             6 worlds    caching     personas     matrix      learning
                         fcntl queue text xform   ambient     feedback loop
```

**Cross-phase dependencies**:

```
lib/audio.py ──────────────────────────────────────────→ (used by all phases)
lib/router.py ─────────────────────────────────────────→ (extended in Phase 2, 4, 6)
lib/theme.py ──────→ lib/identity.py ──────────────────→ (Phase 4 adds persona resolution)
lib/state.py ──────────────────────────────────────────→ (used by all phases)
lib/gates.py ──────────────────────────────────────────→ (extended in Phase 5)
lib/tts.py ────────→ lib/identity.py ──→ lib/rhythms.py → (TTS voice selection cascades)
lib/gamification.py ──→ lib/router.py ─────────────────→ (level-up events feed back into routing)
```

**External dependencies**:

| Dependency | Required By | Install Method | Notes |
|-----------|-------------|----------------|-------|
| PipeWire + pw-play | Phase 1 | System package | Already installed (PipeWire 1.6.2) |
| numpy + scipy | Phase 2 | `uv add` | Sound synthesis only (offline script) |
| PyYAML | Phase 1 | `uv add` | Config parsing |
| httpx | Phase 3 | `uv add` | ElevenLabs API |
| kokoro | Phase 3 | `uv add` | Local TTS |
| piper-tts | Phase 3 | `uv add` | Fallback TTS |
| sounddevice | Phase 3 | `uv add` | Microphone capture |
| faster-whisper | Phase 3 | Already in whisperx-env | STT |
| ElevenLabs API key | Phase 3 | User config | Optional (local TTS works without) |

---

## 10. Risk Register

| # | Risk | Probability | Impact | Phase | Mitigation |
|---|------|------------|--------|-------|------------|
| R1 | PipeWire API changes break pw-play CLI flags | Low | High | 1 | Fallback chain (paplay, aplay, mpv). Abstract playback behind `lib/audio.py` — swap implementation without touching callers. |
| R2 | ElevenLabs pricing increase makes cloud TTS uneconomical | Medium | Medium | 3 | Local TTS fallback (Kokoro-82M) is the default. ElevenLabs is optional premium. Budget tracking in config warns before overspend. |
| R3 | GPU VRAM contention between STT, TTS, and other workloads | Medium | Medium | 3 | Lazy loading — models only loaded on first use. Priority system: active STT > queued TTS > idle model. Piper CPU fallback when GPU is fully committed. Monitor with `nvidia-smi`. |
| R4 | Sound fatigue — too many sounds becomes annoying | High | Medium | 2 | Mute theme always available. Per-event toggles in config. Debounce: no repeat of same event within 500ms. `prompt_ack` disabled by default (fires too often). Volume granularity (0-100). |
| R5 | Hook latency regression as features accumulate | Medium | High | 3+ | Benchmark CLI (`scripts/benchmark.py`) with p95 enforcement. Latency logged to claude-logging. Quality gates abort gracefully if 150ms budget is at risk (skip gamification write, skip logging). |
| R6 | Theme sound quality insufficient — sounds feel cheap | Medium | Low | 2 | Iterative improvement of synthesis parameters. Each theme's sonic DNA is documented in specs — tunable without code changes. Community feedback loop. Sound preview (`/voice test <event>`) enables rapid iteration. |
| R7 | Claude Code hook API changes break event contract | Low | High | 1 | Abstract hook parsing behind `lib/router.py`. Version-pin expected fields. Graceful degradation: unknown event types produce no sound (not a crash). Monitor Claude Code changelogs. |
| R8 | Concurrent subagent audio produces cacophony | Medium | Medium | 4+ | fcntl playback lock serializes earcons. fcntl TTS lock serializes speech. Priority system: high-priority events (error, notification) interrupt; low-priority events (prompt_ack) drop on conflict. Matrix agent pitch differentiation adds spatial separation. |
| R9 | Kokoro-82M model quality degrades on long utterances | Low | Low | 3 | Text compression: TTS narration capped at 20 words. Long summaries chunked into 20-word segments with natural pause points. Quality monitoring via cache hit/miss ratio (bad generations never get re-requested). |
| R10 | faster-whisper transcription accuracy in noisy environments | Medium | Medium | 3 | Silero VAD filters non-speech audio. Noise gate preprocessing. Push-to-talk mode (explicit recording window) avoids ambient noise capture. Configurable confidence threshold — low-confidence transcriptions are discarded. |
| R11 | Plugin cache sync breaks asset paths | Low | Medium | 1 | All asset paths resolved via `${CLAUDE_PLUGIN_ROOT}` environment variable, never hardcoded. `play_test.py` validates all paths on demand. |
| R12 | Intent parsing in Phase 6 produces wrong actions | Medium | High | 6 | Confirmation protocol for destructive actions. Keyword fast-path handles common commands deterministically. LLM escalation only for ambiguous intents. Learning system improves over time. User can always cancel mid-action. |

---

## 11. Success Metrics

### Quantitative

| Metric | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 | Phase 6 |
|--------|---------|---------|---------|---------|---------|---------|
| Earcon latency p95 | <150ms | <150ms | <150ms | <150ms | <150ms | <150ms |
| TTS latency p95 | — | — | <500ms | <500ms | <500ms | <500ms |
| STT latency p95 | — | — | <1000ms | <1000ms | <1000ms | <1000ms |
| Theme count | 1 | 7 | 7 | 7 | 7 | 7+ |
| Sound asset count | 36 | 324+ | 324+ | 324+ | 324+ | 324+ |
| Hook events wired | 6 | 14 | 14 | 14 | 14 | 14 |
| Hook crash rate | 0% | 0% | 0% | 0% | 0% | 0% |
| Test coverage (lib/) | — | >60% | >75% | >85% | >90% | >90% |
| Achievement count | — | — | — | 30 | 30 | 30+ |
| Voice command accuracy | — | — | — | — | — | >85% |

### Qualitative

| Milestone | Target Feeling |
|-----------|---------------|
| Phase 1 complete | "It works — I hear a sound when Claude finishes" |
| Phase 2 complete | "This is fun — StarCraft sounds make coding feel like a game" |
| Phase 3 complete | "Claude talks to me — the terminal is alive" |
| Phase 4 complete | "It knows who I am — Matt's voice, my theme, my achievements" |
| Phase 5 complete | "Everything is connected — sound, visuals, rhythm, awareness" |
| Phase 6 complete | "I can't go back — voice-driven development is the standard now" |

---

## 12. Non-Goals

Explicitly out of scope for all phases:

- **Mobile or web support.** claude-voice is terminal-only. PipeWire is the audio backend. No browser audio, no mobile push notifications, no WebSocket streaming to remote clients.
- **Multi-user or collaborative audio.** Single user, single machine. No shared sound sessions, no "hear what your pair programmer hears."
- **Music generation.** Ambient soundscapes are pre-generated WAV loops, not AI-composed in real-time. Sound effects are synthesized offline via numpy/scipy, not generated on demand.
- **Video or visual effects beyond tmux.** No terminal animations, no OSD overlays, no screen effects. Visual integration is limited to tmux status bar indicators via claude-statusline.
- **Cross-platform support.** Linux only. PipeWire required. No macOS CoreAudio, no Windows WASAPI, no PulseAudio-only systems. The fallback chain (paplay, aplay) provides degraded support on non-PipeWire Linux systems but is not a priority.
- **Plugin marketplace distribution.** claude-voice is a local legion-plugins plugin. No packaging for external distribution, no version compatibility matrix with other users' setups.
- **Accessibility compliance.** While the plugin adds audio feedback, it is not designed as an accessibility tool. No screen reader integration, no WCAG compliance, no alternative modalities for the audio channel.

---

## 13. Spec Index

All design specifications live in `specs/`. Each spec covers one subsystem or concern. Specs are written before implementation of the corresponding phase.

| Spec | File | Title | Phase | Status |
|------|------|-------|-------|--------|
| 01 | `01-plugin-scaffold.md` | Plugin Scaffold — Directory Structure, Manifest & Registration | 1 | draft |
| 02 | `02-theme-engine.md` | Theme Engine — Schema, Loading, Hot-Swap, Inheritance | 2 | draft |
| 03 | `03-sound-synthesis.md` | Sound Synthesis — numpy/scipy Procedural Generation Pipeline | 2 | draft |
| 04 | `04-hook-architecture.md` | Hook Architecture — Event Handling, Routing, Quality Gates | 1 | draft |
| 05 | `05-audio-playback.md` | Audio Playback — pw-play Integration, Fallback Chain, Fire-and-Forget | 1 | draft |
| 06 | `06-tts-engine.md` | TTS Engine — ElevenLabs, Kokoro, Piper, Caching, Queue | 3 | draft |
| 07 | `07-stt-engine.md` | STT Engine — faster-whisper, Silero VAD, Push-to-Talk | 3 | draft |
| 08 | `08-identity-personality.md` | Identity & Personality — 4-Layer Resolver, Personas, Emotion | 4 | draft |
| 09 | `09-gamification.md` | Gamification — XP, Levels, Achievements, Streaks | 4 | draft |
| 10 | `10-rhythms-integration.md` | Rhythms Integration — Ambient Soundscapes, Brief Delivery | 5 | draft |
| 11 | `11-asset-management.md` | Asset Management — Catalog, Validation, Format Compliance | 2 | draft |
| 12 | `12-quality-testing.md` | Quality & Testing — Benchmarks, Regression, Fuzz, Coverage | 5 | draft |
| 13 | `13-elevenlabs-deep.md` | ElevenLabs Deep Dive — API, Voices, Streaming, Cost | 3 | planned |
| 14 | `14-speech-to-reality.md` | Speech-to-Reality — Intent Parsing, Dispatch, Narration, Learning | 6 | planned |

**Note**: Specs 13 and 14 are planned but not yet written. They will be authored before Phase 3 and Phase 6 implementation begins, respectively.

---

## 14. Open Questions

Tracked here until resolved. Each gets a decision before the relevant phase begins.

| # | Question | Phase | Status |
|---|----------|-------|--------|
| Q1 | Should `prompt_ack` (UserPromptSubmit) be enabled by default or opt-in? It fires on every user message — could be annoying. Current lean: opt-in. | 1 | open |
| Q2 | How many ElevenLabs voices should we pre-configure? Full catalog (32+) or curated subset (6, one per theme)? | 3 | open |
| Q3 | Should gamification XP persist across Claude Code upgrades, or reset? Leaning persistent (SQLite at `~/.claude/local/voice/`). | 4 | open |
| Q4 | Should ambient soundscapes loop a single WAV or crossfade between multiple segments? Looping is simpler; crossfading is more immersive. | 5 | open |
| Q5 | How should voice commands interact with Claude Code's existing slash command system? Parallel path or unified? | 6 | open |
| Q6 | Should the learning system (Phase 6) share its intent patterns with other Legion machines via KOI? | 6 | open |
