---
title: "Identity & Personality — Voice Resolution, Emotion & Text Transforms"
spec: "08"
status: draft
created: 2026-03-26
author: matt
tags: [claude-voice, identity, personality, persona, emotion]
---

# 08 — Identity & Personality

## 1. Overview

The identity and personality system gives claude-voice contextual awareness: WHO is speaking (persona), WHAT mood (emotion), and HOW to express it (text transforms + voice parameters). This creates the illusion that each persona has a distinct voice and character, and that the system responds emotionally to events.

Three subsystems work together:

1. **Identity Resolver** — a 4-layer cascade that determines the active persona from environment signals, agent context, model detection, and defaults. Runs once at session start, cached for the session lifetime.

2. **Emotion System** — maps hook events and content analysis to emotional states, which modulate TTS parameters (pitch, speed, stability) and select sound variant tags. Runs on every event.

3. **Text Transformers** — a pipeline that converts raw event summaries into persona-flavored, theme-appropriate, emotionally-inflected speech text. Runs whenever TTS is triggered.

The identity resolver is the upstream dependency for the theme engine (spec 02) and TTS engine. The resolved identity selects the theme (via `persona_themes` in config.yaml), which selects the sound palette, which selects the voice. Identity flows downward through every layer of claude-voice.

```
Identity Resolver
    ├── Theme Engine (spec 02) ← identity.theme
    ├── TTS Engine ← identity.voice
    ├── Emotion System ← identity.personality
    └── Text Transformers ← identity.greeting + identity.personality
```

### Design Principles

**Voice owns the mapping.** Persona-to-voice and persona-to-theme mappings live in claude-voice's `config.yaml`, not in claude-personas character YAML files. Voice reads persona data as an input but never writes to it. This follows the same separation established in spec 02, Section 9: "claude-personas defines who a persona IS. claude-voice defines how a persona SOUNDS."

**Graceful degradation.** If claude-personas is not installed, if no persona is detected, if a character YAML is malformed — voice falls back silently to the `claude` default identity. No errors, no missing audio, no broken sessions.

**Personality is not theme.** A theme defines sonic identity (instruments, reverb, frequency range). A personality defines behavioral traits (formality, verbosity, catchphrases). Matt's personality is terse and military regardless of whether the theme is StarCraft or Zelda. Theme and personality are orthogonal — they compose, not compete.

---

## 2. 4-Layer Identity Resolver

The identity resolver runs a priority cascade. The first layer that produces a non-null result wins. Higher layers override lower ones.

```
Layer 1: Session Override     — CLAUDE_VOICE_PERSONA env var (explicit per-session)
Layer 2: Agent Context        — PERSONA_SLUG env var (set by claude-personas)
Layer 3: Model Detection      — Infer from model name in SessionStart payload
Layer 4: System Default       — "claude" (fallback identity)
```

### Layer 1: Session Override

| Property | Value |
|----------|-------|
| Signal | `CLAUDE_VOICE_PERSONA` environment variable |
| Set by | User, manually or via script |
| Data provided | Persona slug (e.g., `matt`, `philipp`) |
| Changes when | User explicitly sets a new value; persists for the shell session |
| Priority | Highest — overrides everything |
| Use case | "I want Matt's voice right now regardless of what agent is running" |

Detection: `os.environ.get("CLAUDE_VOICE_PERSONA")`. If present and non-empty, this is the persona slug. Look it up in the persona registry. If the slug is unknown, log a warning and fall through to Layer 2.

### Layer 2: Agent Context

| Property | Value |
|----------|-------|
| Signal | `PERSONA_SLUG` environment variable |
| Set by | claude-personas hooks (injected at session start when a persona agent launches) |
| Data provided | Persona slug matching a `*.character.yaml` file |
| Changes when | A different persona agent is launched; rarely changes mid-session |
| Priority | Second — the normal operational path |
| Use case | Claude Code is running the `matt` agent, so voice automatically uses Matt's identity |

Detection: `os.environ.get("PERSONA_SLUG")`. This is the primary mechanism in production. When a user runs `claude --agent matt`, the claude-personas hooks set `PERSONA_SLUG=matt` in the environment. Voice reads this passively.

### Layer 3: Model Detection

| Property | Value |
|----------|-------|
| Signal | `model` field in `SessionStart` hook payload |
| Set by | Claude Code runtime |
| Data provided | Model string (e.g., `claude-opus-4-6`, `claude-sonnet-4-5`) |
| Changes when | Session starts with a different model |
| Priority | Third — heuristic fallback |
| Use case | No persona is set, but the model name suggests a context (e.g., opus = primary work, sonnet = subagent) |

Detection: Parse the `hook_data` dict for `session.model` or equivalent field. Model-to-persona mapping is defined in config.yaml under `identity.model_personas`:

```yaml
identity:
  model_personas:
    claude-opus-4-6: matt      # Primary sessions default to Matt
    claude-sonnet-4-5: claude  # Subagent sessions use neutral Claude
```

This layer is a heuristic. It provides a reasonable default when no explicit persona is set. Most users will never configure it — it exists so that out-of-the-box, opus sessions get the chief-of-staff voice and sonnet subagent sessions stay neutral.

### Layer 4: System Default

| Property | Value |
|----------|-------|
| Signal | None — unconditional fallback |
| Set by | `identity.default_persona` in config.yaml (defaults to `"claude"`) |
| Data provided | The `claude` identity — neutral, no theme personality |
| Changes when | Never (unless user edits config.yaml) |
| Priority | Lowest — always available |
| Use case | Fresh install, no personas, no env vars |

Detection: Always succeeds. Reads `config.yaml` → `identity.default_persona`, or hardcoded `"claude"` if config is missing.

### Resolution Algorithm

```python
def resolve_identity(hook_data: dict, config: dict) -> Identity:
    """Resolve the current identity from all available signals.

    Returns Identity with: slug, name, theme, voice, greeting_template, personality.

    Resolution cascade:
      1. CLAUDE_VOICE_PERSONA env var (explicit override)
      2. PERSONA_SLUG env var (set by claude-personas)
      3. Model name from hook_data (heuristic)
      4. config.identity.default_persona (fallback)
    """
    slug = None
    source = None

    # Layer 1: Session override
    override = os.environ.get("CLAUDE_VOICE_PERSONA", "").strip()
    if override:
        slug = override
        source = "session_override"

    # Layer 2: Agent context
    if not slug:
        agent_persona = os.environ.get("PERSONA_SLUG", "").strip()
        if agent_persona:
            slug = agent_persona
            source = "agent_context"

    # Layer 3: Model detection
    if not slug:
        model = extract_model_name(hook_data)
        if model:
            model_map = config.get("identity", {}).get("model_personas", {})
            mapped = model_map.get(model)
            if mapped:
                slug = mapped
                source = "model_detection"

    # Layer 4: System default
    if not slug:
        slug = config.get("identity", {}).get("default_persona", "claude")
        source = "system_default"

    # Build identity from slug
    identity = build_identity(slug, config)
    identity.resolution_source = source
    return identity


def extract_model_name(hook_data: dict) -> Optional[str]:
    """Extract model name from SessionStart hook payload.

    Tries multiple paths since hook payload structure varies:
      - hook_data["session"]["model"]
      - hook_data["model"]
      - os.environ.get("CLAUDE_MODEL")
    """
    if "session" in hook_data and "model" in hook_data["session"]:
        return hook_data["session"]["model"]
    if "model" in hook_data:
        return hook_data["model"]
    return os.environ.get("CLAUDE_MODEL", "").strip() or None


def build_identity(slug: str, config: dict) -> Identity:
    """Build a full Identity object from a persona slug.

    Reads the persona registry (built-in + character YAML) to populate
    all identity fields. Falls back to CLAUDE_DEFAULT for unknown slugs.
    """
    registry = load_persona_registry(config)
    if slug in registry:
        return registry[slug]

    # Unknown slug — warn and return default
    logger.warning(f"Unknown persona slug '{slug}', falling back to 'claude'")
    return registry.get("claude", CLAUDE_DEFAULT)
```

---

## 3. Identity Schema

```python
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VoiceConfig:
    """TTS voice configuration for a persona.

    Supports three TTS backends (ElevenLabs, Kokoro, Piper).
    Only one is active at a time — selected by the TTS engine
    based on config.yaml tts.provider setting.
    """
    elevenlabs_voice_id: Optional[str] = None   # ElevenLabs voice ID string
    elevenlabs_voice_name: str = "Rachel"        # Human-readable name (for logging)
    kokoro_preset: str = "af_default"            # Kokoro voice preset name
    piper_model: str = "en_US-lessac-medium"     # Piper model name
    speed: float = 1.0                           # Base speech rate (1.0 = normal)
    pitch_offset: int = 0                        # Semitone offset from default (-12 to +12)
    stability: float = 0.5                       # ElevenLabs stability (0.0-1.0)
    similarity_boost: float = 0.75               # ElevenLabs similarity (0.0-1.0)


@dataclass
class PersonalityProfile:
    """Behavioral traits that shape text output and TTS modulation.

    Scores are 1-5 integers. They influence text transform decisions
    (e.g., high verbosity = longer greetings, high humor = occasional
    quips) and TTS parameter modulation (e.g., high formality = slower
    speed, lower pitch variance).
    """
    formality: int = 3          # 1=casual, 5=formal
    verbosity: int = 3          # 1=terse, 5=verbose
    humor: int = 2              # 1=none, 5=frequent
    technical_depth: int = 3    # 1=abstract, 5=implementation-detail
    energy: int = 3             # 1=calm, 5=intense
    warmth: int = 3             # 1=cold/clinical, 5=warm/friendly
    catchphrases: list = field(default_factory=list)      # 2-3 signature phrases
    vocabulary: list = field(default_factory=list)         # Preferred words/terms
    speed_modifier: float = 1.0  # TTS speed multiplier (0.8-1.2)


@dataclass
class Identity:
    """The resolved identity for the current session.

    Populated by the identity resolver at session start.
    Consumed by theme engine, TTS engine, emotion system,
    and text transformers throughout the session.
    """
    slug: str                       # e.g., "matt", "philipp", "claude"
    name: str                       # e.g., "Matt", "Philipp", "Claude"
    theme: str                      # Theme slug (e.g., "starcraft", "default")
    voice: VoiceConfig              # TTS voice configuration
    greeting: str                   # Template: "Commander, {summary}."
    personality: PersonalityProfile # Behavioral traits
    resolution_source: str = ""     # Which layer resolved this identity

    def effective_speed(self, emotion_modifier: float = 1.0) -> float:
        """Compute final TTS speed: base * personality * emotion."""
        return self.voice.speed * self.personality.speed_modifier * emotion_modifier
```

---

## 4. Persona-to-Voice Mapping

The canonical mapping of personas to voices, themes, greetings, and styles. This table is the single reference for all identity configuration. Values are defaults — all overridable via `config.yaml` → `identity.voice_overrides`.

### Complete Mapping Table

| Persona | Role | Theme | ElevenLabs Voice | Kokoro Preset | Piper Model | Greeting Template | Style |
|---------|------|-------|-----------------|---------------|-------------|-------------------|-------|
| matt | Chief of Staff | starcraft | Adam | am_adam | en_US-ryan-medium | "Commander, {summary}." | Terse, military, action-oriented |
| philipp | Data Viz | zelda | Bella | af_bella | en_US-amy-medium | "Hey! Listen! {summary}." | Curious, exploratory, detail-rich |
| darren | KOI Protocol | warcraft | Antoni | bf_emma | en_GB-alan-medium | "Work complete. {summary}." | Methodical, precise, protocol-aware |
| shawn | Architect | kingdom-hearts | Grace | af_nicole | en_US-lessac-medium | "{summary}. May your heart guide you." | Thoughtful, philosophical, deep |
| dan | AI Dev | mario | Elli | af_sarah | en_US-joe-medium | "Wahoo! {summary}!" | Energetic, fast, playful |
| alucek | Agent Architect | smash | Josh | am_michael | en_US-kusal-medium | "{summary} -- GAME!" | Analytical, intense, competitive |
| claude | Default | default | Rachel | af_default | en_US-lessac-medium | "{summary}" | Calm, neutral, professional |

### Detailed Personality Profiles

#### Matt (Chief of Staff)

| Trait | Score | Notes |
|-------|-------|-------|
| Formality | 4 | Military precision, no slang |
| Verbosity | 1 | Maximum terseness. One sentence where three would do. |
| Humor | 1 | Dry at most. Never jokes during operations. |
| Technical depth | 4 | References specific paths, ports, services |
| Energy | 4 | High agency, always moving forward |
| Warmth | 2 | Professional, not cold — but not cuddly |

- **Catchphrases**: "Handled.", "Dispatching.", "Commander, your call."
- **Vocabulary**: "deploy", "dispatch", "sector", "objective", "confirmed", "negative"
- **Speed modifier**: 1.1x (slightly faster than default — efficient speaker)

#### Philipp (Data Viz)

| Trait | Score | Notes |
|-------|-------|-------|
| Formality | 2 | Casual academic. First names, informal phrasing. |
| Verbosity | 4 | Likes to explain. Will describe what the data shows. |
| Humor | 3 | Occasional playful observations about data |
| Technical depth | 5 | Deep implementation detail. Column names, query plans. |
| Energy | 3 | Steady, measured curiosity |
| Warmth | 4 | Genuinely enthusiastic about findings |

- **Catchphrases**: "Look at this.", "The data says...", "Interesting pattern here."
- **Vocabulary**: "distribution", "outlier", "correlation", "render", "chart", "transform", "polars"
- **Speed modifier**: 0.95x (slightly slower — deliberate, letting ideas land)

#### Darren (KOI Protocol)

| Trait | Score | Notes |
|-------|-------|-------|
| Formality | 3 | Neither casual nor stiff. Protocol-appropriate. |
| Verbosity | 2 | Says what needs saying, nothing more. |
| Humor | 1 | Rarely. Protocol work is serious. |
| Technical depth | 5 | Deep protocol knowledge. Namespace schemas, bundle formats. |
| Energy | 2 | Calm, steady. Infrastructure doesn't rush. |
| Warmth | 3 | Reliable more than warm |

- **Catchphrases**: "Work complete.", "Bundle indexed.", "Namespace verified."
- **Vocabulary**: "bundle", "namespace", "index", "upsert", "reconcile", "schema", "federation"
- **Speed modifier**: 0.9x (slower, deliberate — like infrastructure chugging reliably)

#### Shawn (Architect)

| Trait | Score | Notes |
|-------|-------|-------|
| Formality | 3 | Natural, human. Not formal, not sloppy. |
| Verbosity | 3 | Medium. Explains why, not just what. |
| Humor | 3 | Philosophical humor. Observations about patterns. |
| Technical depth | 4 | Sees the system, references the whole. |
| Energy | 3 | Thoughtful energy. Considers before acting. |
| Warmth | 5 | Deep warmth. Cares about the people and the system. |

- **Catchphrases**: "May your heart guide you.", "The garden grows.", "Intents over implementations."
- **Vocabulary**: "garden", "cultivate", "rhythm", "intent", "light", "heart", "connection"
- **Speed modifier**: 0.95x (unhurried, reflective)

#### Dan (AI Dev)

| Trait | Score | Notes |
|-------|-------|-------|
| Formality | 1 | Very casual. Exclamation marks. Emoji-adjacent energy. |
| Verbosity | 2 | Short bursts. Punchy. |
| Humor | 4 | Frequent. Puns about models, layers, tokens. |
| Technical depth | 4 | Deep ML knowledge, expressed accessibly |
| Energy | 5 | Maximum. Always excited about the next thing. |
| Warmth | 4 | Enthusiastic warmth. Celebrates wins loudly. |

- **Catchphrases**: "Wahoo!", "Let's-a go!", "Here we go!"
- **Vocabulary**: "ship", "train", "finetune", "deploy", "inference", "benchmark", "speedrun"
- **Speed modifier**: 1.15x (fast talker, high energy)

#### Alucek (Agent Architect)

| Trait | Score | Notes |
|-------|-------|-------|
| Formality | 3 | Technical but not stuffy |
| Verbosity | 3 | Explains architecture, but concisely |
| Humor | 2 | Competitive humor. Trash talk about bad patterns. |
| Technical depth | 5 | Deep multi-agent systems knowledge |
| Energy | 4 | Intense focus. Tournament energy. |
| Warmth | 2 | Respects competence. Not overtly warm. |

- **Catchphrases**: "GAME!", "Final destination.", "No items."
- **Vocabulary**: "agent", "dispatch", "compose", "orchestrate", "spawn", "coordinate", "graph"
- **Speed modifier**: 1.05x (slightly fast, sharp delivery)

#### Claude (Default)

| Trait | Score | Notes |
|-------|-------|-------|
| Formality | 3 | Balanced, professional but approachable |
| Verbosity | 3 | Medium. Appropriate detail. |
| Humor | 2 | Occasional, gentle |
| Technical depth | 3 | Adapts to context |
| Energy | 3 | Calm, steady, reliable |
| Warmth | 3 | Present but not dominant |

- **Catchphrases**: "Done.", "Ready.", "Here's what I found."
- **Vocabulary**: general purpose, no strong preferences
- **Speed modifier**: 1.0x (baseline)

---

## 5. Emotion System

The emotion system maps events to emotional states, which modulate both TTS output and sound variant selection. Emotions are transient — they last for a single event, not the session.

### Event-to-Emotion Mapping

| Event | Semantic Token | Emotion | Acoustic Effect | Text Modifier |
|-------|---------------|---------|-----------------|---------------|
| SessionStart | `session_start` | ready | Neutral, professional. Baseline pitch and speed. | "Online." / "Ready." / greeting template |
| SessionEnd | `session_end` | calm | Soft, slightly lower pitch. Relaxed stability. | "Signing off." / "Until next time." |
| UserPromptSubmit | `prompt_ack` | attentive | Crisp, clear. Slight speed increase. | (no text — earcon only) |
| Stop (normal) | `task_complete` | satisfied | Warm tone, slightly raised pitch. Relaxed speed. | "Done." / "Complete." / "Finished." |
| Stop (commit) | `commit` | proud | Upbeat, energetic. Higher pitch, faster speed. | "Committed!" / "Shipped!" / "Pushed!" |
| Stop (error in output) | `error` | concerned | Lower pitch, slower speed. Higher stability (less variance). | "Issue detected." / "Problem found." |
| Stop (tests pass) | `task_complete` | satisfied | Same as normal completion, slightly warmer. | "Tests pass." / "All green." |
| SubagentStart | `agent_deploy` | focused | Sharp, clear. Moderate energy. | "Deploying." / "Agent dispatched." |
| SubagentStop | `agent_return` | relieved | Relaxed, warm. Slight pitch drop from focused. | "Agent returned." / "Work done." |
| PostToolUseFailure | `error` | concerned | Lower pitch, slower speed. Higher stability. | "Issue detected." / "Problem found." |
| Notification | `notification` | alert | Sharp, clear. Slight pitch raise. Faster speed. | "Attention:" / "Notice:" |
| PermissionRequest | `permission` | alert | Same as notification but slightly more urgent. | "Permission needed." / "Awaiting approval." |
| PreCompact | `compact` | neutral | Minimal modulation. Background event. | "Compacting." |

### Emotion Parameters

Each emotion maps to a set of TTS parameter modifiers. These are multiplied against the persona's base values.

```python
EMOTION_MODIFIERS = {
    "ready": {
        "pitch_offset": 0,      # No shift from base
        "speed_modifier": 1.0,  # Normal speed
        "stability": 0.0,       # No stability adjustment
        "energy_hint": "neutral",
    },
    "calm": {
        "pitch_offset": -1,     # Slightly lower
        "speed_modifier": 0.95, # Slightly slower
        "stability": 0.05,      # Slightly more stable (less variance)
        "energy_hint": "low",
    },
    "attentive": {
        "pitch_offset": 0,
        "speed_modifier": 1.05, # Slightly faster
        "stability": 0.0,
        "energy_hint": "moderate",
    },
    "satisfied": {
        "pitch_offset": 1,      # Slightly higher (warmth)
        "speed_modifier": 0.95, # Unhurried satisfaction
        "stability": -0.05,     # Slightly less stable (more natural)
        "energy_hint": "warm",
    },
    "proud": {
        "pitch_offset": 2,      # Noticeably higher (excitement)
        "speed_modifier": 1.1,  # Faster (upbeat)
        "stability": -0.1,      # Less stable (more expressive)
        "energy_hint": "high",
    },
    "concerned": {
        "pitch_offset": -2,     # Lower (gravity)
        "speed_modifier": 0.9,  # Slower (careful)
        "stability": 0.1,       # More stable (controlled)
        "energy_hint": "low",
    },
    "alert": {
        "pitch_offset": 2,      # Higher (urgency)
        "speed_modifier": 1.1,  # Faster (importance)
        "stability": 0.05,      # Slightly more stable (clarity)
        "energy_hint": "high",
    },
    "focused": {
        "pitch_offset": 0,
        "speed_modifier": 1.0,
        "stability": 0.05,      # Slightly more stable (precision)
        "energy_hint": "moderate",
    },
    "relieved": {
        "pitch_offset": -1,     # Slightly lower (relaxation)
        "speed_modifier": 0.95, # Slightly slower
        "stability": -0.05,     # Less stable (natural sigh quality)
        "energy_hint": "low",
    },
    "neutral": {
        "pitch_offset": 0,
        "speed_modifier": 1.0,
        "stability": 0.0,
        "energy_hint": "neutral",
    },
}
```

### Emotion Detection Algorithm

```python
def detect_emotion(event_type: str, hook_data: dict) -> str:
    """Determine the emotional state for a given event.

    Uses event type as the primary signal, with content analysis
    for events that carry output text (Stop).

    Returns one of: ready, calm, attentive, satisfied, proud,
    concerned, alert, focused, relieved, neutral.
    """
    # Direct event-to-emotion mapping
    EVENT_EMOTIONS = {
        "SessionStart": "ready",
        "SessionEnd": "calm",
        "UserPromptSubmit": "attentive",
        "SubagentStart": "focused",
        "SubagentStop": "relieved",
        "PostToolUseFailure": "concerned",
        "Notification": "alert",
        "PermissionRequest": "alert",
        "PreCompact": "neutral",
    }

    if event_type in EVENT_EMOTIONS:
        return EVENT_EMOTIONS[event_type]

    # Content-aware emotion for Stop events
    if event_type == "Stop":
        output = hook_data.get("last_assistant_message", "")
        return _analyze_stop_emotion(output)

    return "neutral"


def _analyze_stop_emotion(output: str) -> str:
    """Analyze Stop event output to determine emotion.

    Pattern matching against the assistant's last message.
    Order matters — first match wins.
    """
    import re

    PATTERNS = [
        # Errors and failures (concerned)
        (r"(?i)(error|Error|ERROR|failed|Failed|FAILED|exception|Exception|traceback)", "concerned"),
        # Commits and ships (proud)
        (r"(?i)(git commit|committed|Created commit|pushed|shipped|deployed)", "proud"),
        # Test passes (satisfied)
        (r"(?i)(test.*pass|tests passed|All.*pass|all green)", "satisfied"),
        # Default completion (satisfied)
    ]

    for pattern, emotion in PATTERNS:
        if re.search(pattern, output):
            return emotion

    return "satisfied"  # Default for normal Stop: task completed
```

### Emotion-Sound Variant Interaction

Themes can optionally tag sound variants by emotion. If a theme's `semantic_sounds` includes a `variants_by_emotion` field, the sound router will prefer variants tagged with the current emotion.

```json
{
  "task_complete": {
    "variants": ["complete-01.wav", "complete-02.wav", "complete-03.wav"],
    "variants_by_emotion": {
      "satisfied": ["complete-01.wav", "complete-02.wav"],
      "proud": ["complete-03.wav"]
    },
    "duration_ms": 300,
    "priority": 1,
    "mode": "overlap",
    "category": "earcon"
  }
}
```

If `variants_by_emotion` is not present (the default), all variants are equally eligible regardless of emotion. This keeps the feature optional — themes that don't care about emotional variant selection work unchanged.

---

## 6. Text Transformers

Text transformers convert raw event data into persona-flavored speech text for TTS. The pipeline runs five stages in sequence, producing a final string of at most 20 words (following the Disler TTS brevity pattern).

### Transform Pipeline

```
Raw event data
    → Stage 1: Greeting template (identity)
    → Stage 2: Theme vocabulary injection (theme)
    → Stage 3: Emotion modifier (emotion)
    → Stage 4: Persona style (personality)
    → Stage 5: Length constraint (max 20 words)
    → Final TTS text
```

### Implementation

```python
def transform_text(
    text: str,
    identity: Identity,
    emotion: str,
    event_type: str,
    theme_vocab: dict,
) -> str:
    """Apply persona + theme + emotion text transforms.

    The full pipeline that converts a raw summary into TTS-ready text.

    Args:
        text: Raw event summary (e.g., "Session started with 12 plugins")
        identity: Resolved Identity object
        emotion: Current emotional state string
        event_type: Hook event type (e.g., "SessionStart", "Stop")
        theme_vocab: Theme-specific vocabulary from theme.json

    Returns:
        Transformed text ready for TTS (max 20 words).

    Examples:
        StarCraft + matt + ready + SessionStart:
            "Session started" → "Commander, systems online. 12 plugins active."

        Mario + dan + proud + Stop (commit):
            "Committed changes" → "Wahoo! Shipped!"

        Zelda + philipp + satisfied + Stop (normal):
            "Analysis complete" → "Hey! Listen! Found the answer!"

        Warcraft + darren + satisfied + Stop (normal):
            "Task finished" → "Job's done. Work complete."

        Kingdom Hearts + shawn + calm + SessionEnd:
            "Session ending" → "Until next time. May your heart guide you."

        Smash + alucek + proud + Stop (commit):
            "Committed" → "Committed -- GAME!"
    """
    result = text

    # Stage 1: Apply greeting template from identity
    result = _apply_greeting_template(result, identity, event_type)

    # Stage 2: Inject event-specific vocabulary from theme
    result = _inject_theme_vocabulary(result, theme_vocab, event_type, emotion)

    # Stage 3: Apply emotion modifier
    result = _apply_emotion_modifier(result, emotion, identity.personality)

    # Stage 4: Apply persona style
    result = _apply_persona_style(result, identity.personality)

    # Stage 5: Enforce length constraint
    result = _enforce_length(result, max_words=20)

    return result


def _apply_greeting_template(text: str, identity: Identity, event_type: str) -> str:
    """Stage 1: Apply the persona's greeting template.

    Only applies to SessionStart events. For all other events,
    returns text unchanged.

    The greeting template contains {summary} which gets replaced
    with the compressed event summary.
    """
    if event_type == "SessionStart" and identity.greeting:
        return identity.greeting.replace("{summary}", text)
    return text


def _inject_theme_vocabulary(
    text: str, theme_vocab: dict, event_type: str, emotion: str
) -> str:
    """Stage 2: Replace generic terms with theme-specific vocabulary.

    Theme vocabulary is defined in theme.json under tts.vocabulary:

    {
      "tts": {
        "vocabulary": {
          "task_complete": {
            "satisfied": "Objective achieved.",
            "proud": "Mission accomplished!",
            "default": "Complete."
          },
          "error": {
            "concerned": "Warning: hostile contact.",
            "default": "Alert: system error."
          }
        }
      }
    }

    If the theme provides vocabulary for this event+emotion combo,
    use the theme text. Otherwise, pass through unchanged.
    """
    semantic_token = _event_to_token(event_type)
    if semantic_token in theme_vocab:
        token_vocab = theme_vocab[semantic_token]
        if emotion in token_vocab:
            return token_vocab[emotion]
        if "default" in token_vocab:
            return token_vocab["default"]
    return text


def _apply_emotion_modifier(text: str, emotion: str, personality: PersonalityProfile) -> str:
    """Stage 3: Apply punctuation and phrasing based on emotion.

    - proud/alert: Ensure exclamation if personality.energy >= 3
    - concerned: Add ellipsis or cautious phrasing
    - calm: Soften punctuation (! → .)
    - neutral: No modification
    """
    if emotion in ("proud", "alert") and personality.energy >= 3:
        if not text.endswith("!"):
            text = text.rstrip(".") + "!"
    elif emotion == "concerned":
        if not text.endswith("...") and not text.endswith("."):
            text = text.rstrip(".!") + "."
    elif emotion == "calm":
        text = text.replace("!", ".")
    return text


def _apply_persona_style(text: str, personality: PersonalityProfile) -> str:
    """Stage 4: Adjust text to match persona's behavioral traits.

    - verbosity 1 (terse): Strip subordinate clauses, reduce to core statement
    - verbosity 5 (verbose): Allow full text through (no artificial expansion)
    - formality 4-5: Remove contractions, casual phrases
    - formality 1-2: Allow contractions, casual phrasing

    Note: This stage only REDUCES, never EXPANDS. It removes words
    to match the persona's style, but never adds filler to reach a
    target length.
    """
    if personality.verbosity <= 2:
        # Terse: keep only the first sentence or clause
        for sep in [". ", ", ", " -- ", " - "]:
            if sep in text:
                parts = text.split(sep, 1)
                text = parts[0] + ("." if not parts[0].endswith((".", "!", "?")) else "")
                break

    return text


def _enforce_length(text: str, max_words: int = 20) -> str:
    """Stage 5: Hard cap at max_words.

    TTS should be brief — under 20 words. If text exceeds the limit,
    truncate at the last complete sentence boundary before the limit.
    If no sentence boundary exists, truncate at word boundary and
    add ellipsis.
    """
    words = text.split()
    if len(words) <= max_words:
        return text

    # Try to find a sentence boundary within the limit
    truncated = " ".join(words[:max_words])
    for end in (".", "!", "?"):
        last_end = truncated.rfind(end)
        if last_end > 0:
            return truncated[:last_end + 1]

    return truncated.rstrip(".,!?;: ") + "."


def _event_to_token(event_type: str) -> str:
    """Map a hook event type to its semantic sound token name.

    Mirrors the hook_to_sound mapping from theme.json.
    """
    EVENT_TOKEN_MAP = {
        "SessionStart": "session_start",
        "SessionEnd": "session_end",
        "UserPromptSubmit": "prompt_ack",
        "Stop": "task_complete",
        "SubagentStart": "agent_deploy",
        "SubagentStop": "agent_return",
        "PostToolUseFailure": "error",
        "Notification": "notification",
        "PermissionRequest": "permission",
        "PreCompact": "compact",
    }
    return EVENT_TOKEN_MAP.get(event_type, "")
```

### Theme Vocabulary Examples

Each theme provides event-specific vocabulary in its `theme.json`. These replace generic text with themed equivalents.

**StarCraft** (military sci-fi):
```json
{
  "tts": {
    "vocabulary": {
      "session_start": { "ready": "Systems online.", "default": "Adjutant ready." },
      "session_end": { "calm": "Shutting down.", "default": "Power off." },
      "task_complete": { "satisfied": "Objective achieved.", "proud": "Mission accomplished!", "default": "Complete." },
      "error": { "concerned": "Warning: hostile contact.", "default": "Alert: system error." },
      "commit": { "proud": "Payload delivered!", "default": "Committed." },
      "agent_deploy": { "focused": "Unit dispatched.", "default": "Deploying." },
      "agent_return": { "relieved": "Unit returned to base.", "default": "Unit recalled." },
      "notification": { "alert": "Incoming transmission.", "default": "Signal received." }
    }
  }
}
```

**Mario** (playful, high-energy):
```json
{
  "tts": {
    "vocabulary": {
      "session_start": { "ready": "Here we go!", "default": "Let's-a go!" },
      "session_end": { "calm": "See you next time!", "default": "Bye bye!" },
      "task_complete": { "satisfied": "Level clear!", "proud": "Wahoo! Star get!", "default": "Yahoo!" },
      "error": { "concerned": "Mamma mia!", "default": "Oof!" },
      "commit": { "proud": "One-up!", "default": "Coin!" },
      "agent_deploy": { "focused": "Go go go!", "default": "Power-up!" },
      "agent_return": { "relieved": "Welcome back!", "default": "Safe!" },
      "notification": { "alert": "Watch out!", "default": "Hey!" }
    }
  }
}
```

**Zelda** (exploration, discovery):
```json
{
  "tts": {
    "vocabulary": {
      "session_start": { "ready": "A new adventure begins.", "default": "Hey! Listen!" },
      "session_end": { "calm": "Save and quit.", "default": "Until next quest." },
      "task_complete": { "satisfied": "You found the answer!", "proud": "Puzzle solved!", "default": "Quest complete." },
      "error": { "concerned": "The path is blocked.", "default": "Try another way." },
      "commit": { "proud": "New item acquired!", "default": "Treasure found." },
      "agent_deploy": { "focused": "Companion summoned.", "default": "Fairy dispatched." },
      "agent_return": { "relieved": "Companion returned.", "default": "Fairy returned." },
      "notification": { "alert": "Danger ahead!", "default": "Listen!" }
    }
  }
}
```

**Warcraft** (RTS, methodical):
```json
{
  "tts": {
    "vocabulary": {
      "session_start": { "ready": "Ready to work.", "default": "Your command?" },
      "session_end": { "calm": "Job's done.", "default": "Off I go." },
      "task_complete": { "satisfied": "Work complete.", "proud": "Job's done, my lord!", "default": "Done." },
      "error": { "concerned": "We're under attack!", "default": "Not enough resources." },
      "commit": { "proud": "Upgrade complete!", "default": "Construction complete." },
      "agent_deploy": { "focused": "For the Horde!", "default": "At once." },
      "agent_return": { "relieved": "Returned to the keep.", "default": "Reporting." },
      "notification": { "alert": "Your base is under attack!", "default": "Attention." }
    }
  }
}
```

**Kingdom Hearts** (light/dark, connections):
```json
{
  "tts": {
    "vocabulary": {
      "session_start": { "ready": "The light shines on.", "default": "Heart connected." },
      "session_end": { "calm": "May your heart be your guiding key.", "default": "Until we meet again." },
      "task_complete": { "satisfied": "The path is clear.", "proud": "Light prevails!", "default": "Sealed." },
      "error": { "concerned": "Darkness gathers.", "default": "The way is lost." },
      "commit": { "proud": "Bond forged!", "default": "Connected." },
      "agent_deploy": { "focused": "Summoning ally.", "default": "Link forged." },
      "agent_return": { "relieved": "Friend returned.", "default": "Bond restored." },
      "notification": { "alert": "A new world calls.", "default": "Listen to the light." }
    }
  }
}
```

**Smash** (competitive, tournament):
```json
{
  "tts": {
    "vocabulary": {
      "session_start": { "ready": "Ready? Go!", "default": "New challenger!" },
      "session_end": { "calm": "Game set.", "default": "Results are in." },
      "task_complete": { "satisfied": "KO!", "proud": "GAME!", "default": "Hit confirmed." },
      "error": { "concerned": "Self-destruct.", "default": "Missed tech." },
      "commit": { "proud": "Combo landed!", "default": "Frame perfect." },
      "agent_deploy": { "focused": "Assist trophy!", "default": "Tag in." },
      "agent_return": { "relieved": "Respawn.", "default": "Back in action." },
      "notification": { "alert": "Final smash ready!", "default": "Incoming!" }
    }
  }
}
```

---

## 7. Voice Configuration Schema

The `VoiceConfig` dataclass (defined in Section 3) carries all parameters needed to configure any TTS backend for a persona. This section details how each field is used by each backend.

### Field Usage by TTS Backend

| Field | ElevenLabs | Kokoro | Piper | Notes |
|-------|------------|--------|-------|-------|
| `elevenlabs_voice_id` | Voice selection | unused | unused | The ElevenLabs voice ID string (e.g., `pNInz6obpgDQGcFmaJgB` for Adam) |
| `elevenlabs_voice_name` | Logging only | unused | unused | Human-readable name for status display and logs |
| `kokoro_preset` | unused | Voice selection | unused | Preset name (e.g., `am_adam`, `af_bella`) |
| `piper_model` | unused | unused | Model selection | Piper model identifier (e.g., `en_US-lessac-medium`) |
| `speed` | `speed` API param | `--speed` flag | `--length-scale` (inverted) | Base speaking rate. 1.0 = normal. |
| `pitch_offset` | Post-process via sox | `--pitch` flag | Post-process via sox | Semitone shift. 0 = no change. |
| `stability` | `stability` API param | unused | unused | ElevenLabs-specific. Higher = more consistent, lower = more expressive. |
| `similarity_boost` | `similarity_boost` API param | unused | unused | ElevenLabs-specific. Higher = closer to reference voice. |

### Effective Parameter Computation

The final TTS parameters are computed by layering three sources:

```python
def compute_tts_params(identity: Identity, emotion: str) -> dict:
    """Compute final TTS parameters from identity + emotion.

    Three layers compose multiplicatively:
      1. VoiceConfig base values (from persona mapping)
      2. PersonalityProfile modifiers (from personality)
      3. Emotion modifiers (from current event)

    Returns a dict of final values ready for the TTS backend.
    """
    voice = identity.voice
    personality = identity.personality
    emo = EMOTION_MODIFIERS.get(emotion, EMOTION_MODIFIERS["neutral"])

    return {
        "speed": voice.speed * personality.speed_modifier * emo["speed_modifier"],
        "pitch_offset": voice.pitch_offset + emo["pitch_offset"],
        "stability": max(0.0, min(1.0, voice.stability + emo["stability"])),
        "similarity_boost": voice.similarity_boost,
        "voice_id": voice.elevenlabs_voice_id,
        "kokoro_preset": voice.kokoro_preset,
        "piper_model": voice.piper_model,
    }
```

### Persona VoiceConfig Defaults

| Persona | speed | pitch_offset | stability | similarity_boost |
|---------|-------|-------------|-----------|-----------------|
| matt | 1.0 | 0 | 0.6 | 0.8 |
| philipp | 1.0 | 0 | 0.4 | 0.75 |
| darren | 1.0 | 0 | 0.7 | 0.8 |
| shawn | 1.0 | 0 | 0.4 | 0.7 |
| dan | 1.05 | 0 | 0.3 | 0.75 |
| alucek | 1.0 | 0 | 0.5 | 0.8 |
| claude | 1.0 | 0 | 0.5 | 0.75 |

Notes on stability choices:
- Higher stability (darren 0.7, matt 0.6) = more consistent, controlled delivery. Matches methodical/military personas.
- Lower stability (dan 0.3, philipp/shawn 0.4) = more expressive, variable delivery. Matches energetic/exploratory personas.

---

## 8. Dynamic Identity Updates

### Session Lifecycle

Identity is resolved **once** at `SessionStart` and cached in the session state file (`~/.claude/local/voice/session.json`). All subsequent events in the session use the cached identity.

```
SessionStart
    → resolve_identity()
    → write session.json: { slug, name, theme, voice, personality, resolved_at, source }
    → set active theme (theme engine reads session.json)
    → play session_start sound + TTS greeting

[... session events use cached identity ...]

SessionEnd
    → read session.json for identity
    → play session_end sound + TTS farewell
    → delete session.json
```

### When Identity Can Change Mid-Session

In normal operation, identity does not change. But three scenarios can trigger a re-resolution:

1. **CLAUDE_VOICE_PERSONA set mid-session**: If a user runs `export CLAUDE_VOICE_PERSONA=philipp` in another terminal, the next hook invocation will detect the change because hooks read the environment fresh on each invocation. However, session.json is authoritative — the hook should compare the env var against session.json and only re-resolve if they differ.

2. **PERSONA_SLUG changes**: Rare. Would happen if claude-personas hot-swaps the active persona. Same detection mechanism as above.

3. **Manual theme override**: The user runs `/voice theme zelda`, which writes `theme: zelda` to config.yaml. This changes the THEME but not the IDENTITY. Matt is still Matt — he just sounds like Zelda now. The theme engine handles this independently.

### Re-Resolution Protocol

```python
def check_identity_drift(hook_data: dict, config: dict) -> Optional[Identity]:
    """Check if the identity has changed since session start.

    Called on every hook invocation. Returns a new Identity if
    re-resolution is needed, None if the cached identity is still valid.

    Re-resolution triggers:
      - CLAUDE_VOICE_PERSONA differs from session.json
      - PERSONA_SLUG differs from session.json
      - session.json is missing (session crashed and restarted)
    """
    session = load_session_state()
    if session is None:
        # No session state — this is a fresh session or recovery
        return resolve_identity(hook_data, config)

    # Check Layer 1: explicit override changed?
    override = os.environ.get("CLAUDE_VOICE_PERSONA", "").strip()
    if override and override != session.get("slug"):
        return resolve_identity(hook_data, config)

    # Check Layer 2: agent context changed?
    agent_persona = os.environ.get("PERSONA_SLUG", "").strip()
    if agent_persona and agent_persona != session.get("slug"):
        if not override:  # Only if no explicit override is active
            return resolve_identity(hook_data, config)

    return None  # No change
```

### Session State File

```json
{
  "slug": "matt",
  "name": "Matt",
  "theme": "starcraft",
  "voice": {
    "elevenlabs_voice_id": null,
    "kokoro_preset": "am_adam",
    "piper_model": "en_US-ryan-medium",
    "speed": 1.0,
    "pitch_offset": 0,
    "stability": 0.6,
    "similarity_boost": 0.8
  },
  "personality": {
    "formality": 4,
    "verbosity": 1,
    "humor": 1,
    "technical_depth": 4,
    "energy": 4,
    "warmth": 2,
    "speed_modifier": 1.1
  },
  "resolved_at": "2026-03-26T09:15:00-06:00",
  "resolution_source": "agent_context"
}
```

---

## 9. Integration Points

### claude-personas (upstream)

- **Reads**: `PERSONA_SLUG` environment variable for Layer 2 resolution
- **Reads**: `*.character.yaml` files for persona name, personality adjectives, and style directives (optional enrichment — voice works without them)
- **Never writes to**: persona character YAML files, role files, or group files
- **Coupling**: Loose. If claude-personas is uninstalled, voice falls through to Layer 3/4 gracefully.

The character YAML schema (as seen in `matt.character.yaml`) provides:
- `identity.slug` and `identity.name` — used to confirm slug-to-name mapping
- `personality.adjectives` — could optionally seed the PersonalityProfile (e.g., "terse" → verbosity=1)
- `personality.style.all` — style directives that could inform text transforms
- `role` — the role slug, available for display in status/logging

Voice does NOT read the following from character YAML (these are persona-internal concerns):
- `memory` backend configuration
- `output_format` templates
- `knowledge` references
- `description` (long/short)

### Theme Engine (spec 02)

- **Consumes**: `identity.theme` to select the active theme
- **Provides**: `theme_vocab` (from `theme.json` → `tts.vocabulary`) to text transformers
- **Provides**: `personality_modifiers` (from `theme.json` → `tts.personality_modifiers`) for emotional TTS tuning
- **Coordination**: Identity sets the theme at session start. Theme engine reads `session.json` → `theme` on each event. If the user overrides the theme via `/voice theme <slug>`, the theme changes independently of identity.

### TTS Engine

- **Consumes**: `identity.voice` (VoiceConfig) for backend selection and voice parameters
- **Consumes**: `compute_tts_params()` output for the effective speed, pitch, and stability
- **Consumes**: text transformer output as the speech text
- **Coordination**: TTS engine calls `compute_tts_params(identity, emotion)` before each synthesis. It does not resolve identity itself.

### Hook Architecture (spec 04)

- **Coordination**: The main hook entry point calls `resolve_identity()` (or `check_identity_drift()`) at the top of every invocation, before routing to the sound router or TTS engine. Identity resolution is the first step in the event processing pipeline.
- **Data flow**: hook receives `hook_data` → identity resolver produces `Identity` → emotion detector produces `emotion` → sound router uses `identity.theme` → TTS uses `identity.voice` + `emotion` → text transformer uses `identity` + `emotion` + `theme_vocab`

### Gamification (spec 09)

- **Potential**: XP accumulation could be persona-specific. Matt earns "command XP" for delegation events, Philipp earns "discovery XP" for data exploration events. This is a future extension — the identity system provides the `slug` needed to partition XP by persona.

### claude-rhythms (upstream, indirect)

- **No direct integration.** Rhythms provide temporal context (briefs injected at session start) but do not interact with the identity system.
- **Future opportunity**: The emotion system could read the brief's urgency signals to set a session-wide emotional baseline (e.g., if the morning brief flags 3 overdue deadlines, start the session with a `concerned` baseline that gradually relaxes as items are resolved).

### claude-observatory (upstream, indirect)

- **No direct integration.** Observatory provides plugin audit data but does not interact with identity.
- **Future opportunity**: The session_start TTS greeting could include observatory data ("Commander, 12 plugins active, systems nominal."). This would require observatory to expose its audit summary via a shared file or environment variable.

---

## 10. Configuration Schema

The identity section in `~/.claude/local/voice/config.yaml`:

```yaml
# Identity & Personality configuration
identity:
  # Default persona when no env var or agent context is detected
  default_persona: claude

  # Persona → theme mapping
  # Which sonic theme activates for each persona
  persona_themes:
    matt: starcraft
    philipp: zelda
    darren: warcraft
    shawn: kingdom-hearts
    dan: mario
    alucek: smash
    # claude uses the top-level "theme" setting (no persona_themes entry needed)

  # Model → persona mapping (Layer 3 heuristic)
  # When no PERSONA_SLUG is set, infer persona from model name
  model_personas:
    claude-opus-4-6: matt
    claude-sonnet-4-5: claude

  # Per-persona voice overrides
  # Override any VoiceConfig field for a specific persona
  # These merge on top of the built-in defaults from Section 4
  voice_overrides:
    # Example: use a cloned voice for Matt
    # matt:
    #   elevenlabs_voice_id: "your-cloned-voice-id"
    #   stability: 0.8

  # Per-persona personality overrides
  # Override any PersonalityProfile field
  personality_overrides:
    # Example: make Matt more verbose for debugging
    # matt:
    #   verbosity: 3

  # Feature flags
  text_transforms: true       # Enable theme-flavored text (Stage 2)
  emotion_modifiers: true     # Enable emotion-based TTS tuning
  greeting_on_start: true     # Speak the greeting at session start
  farewell_on_end: false      # Speak a farewell at session end (off by default — sessions end abruptly)
```

### Config Merge Order

When building an Identity, values come from three sources in order of increasing priority:

1. **Built-in defaults** — the hardcoded values in Section 4 (the mapping table and personality profiles)
2. **Character YAML** — optional enrichment from `*.character.yaml` (name, adjectives)
3. **config.yaml overrides** — `voice_overrides` and `personality_overrides` sections

```python
def load_persona_registry(config: dict) -> dict[str, Identity]:
    """Build the full persona registry from built-in defaults + config overrides.

    Returns a dict mapping slug → Identity for all known personas.
    """
    registry = {}

    for slug, defaults in BUILTIN_PERSONAS.items():
        identity = Identity(**defaults)

        # Apply voice overrides from config
        voice_overrides = config.get("identity", {}).get("voice_overrides", {}).get(slug, {})
        for key, value in voice_overrides.items():
            if hasattr(identity.voice, key):
                setattr(identity.voice, key, value)

        # Apply personality overrides from config
        personality_overrides = config.get("identity", {}).get("personality_overrides", {}).get(slug, {})
        for key, value in personality_overrides.items():
            if hasattr(identity.personality, key):
                setattr(identity.personality, key, value)

        # Apply theme from persona_themes mapping
        persona_themes = config.get("identity", {}).get("persona_themes", {})
        if slug in persona_themes:
            identity.theme = persona_themes[slug]

        registry[slug] = identity

    return registry
```

---

## 11. Persona Registry — Built-in Defaults

The complete built-in persona registry, expressed as the `BUILTIN_PERSONAS` dict used by `load_persona_registry()`:

```python
BUILTIN_PERSONAS = {
    "matt": {
        "slug": "matt",
        "name": "Matt",
        "theme": "starcraft",
        "voice": VoiceConfig(
            elevenlabs_voice_id=None,
            elevenlabs_voice_name="Adam",
            kokoro_preset="am_adam",
            piper_model="en_US-ryan-medium",
            speed=1.0,
            pitch_offset=0,
            stability=0.6,
            similarity_boost=0.8,
        ),
        "greeting": "Commander, {summary}.",
        "personality": PersonalityProfile(
            formality=4, verbosity=1, humor=1,
            technical_depth=4, energy=4, warmth=2,
            catchphrases=["Handled.", "Dispatching.", "Commander, your call."],
            vocabulary=["deploy", "dispatch", "sector", "objective", "confirmed", "negative"],
            speed_modifier=1.1,
        ),
    },
    "philipp": {
        "slug": "philipp",
        "name": "Philipp",
        "theme": "zelda",
        "voice": VoiceConfig(
            elevenlabs_voice_id=None,
            elevenlabs_voice_name="Bella",
            kokoro_preset="af_bella",
            piper_model="en_US-amy-medium",
            speed=1.0,
            pitch_offset=0,
            stability=0.4,
            similarity_boost=0.75,
        ),
        "greeting": "Hey! Listen! {summary}.",
        "personality": PersonalityProfile(
            formality=2, verbosity=4, humor=3,
            technical_depth=5, energy=3, warmth=4,
            catchphrases=["Look at this.", "The data says...", "Interesting pattern here."],
            vocabulary=["distribution", "outlier", "correlation", "render", "chart", "transform", "polars"],
            speed_modifier=0.95,
        ),
    },
    "darren": {
        "slug": "darren",
        "name": "Darren",
        "theme": "warcraft",
        "voice": VoiceConfig(
            elevenlabs_voice_id=None,
            elevenlabs_voice_name="Antoni",
            kokoro_preset="bf_emma",
            piper_model="en_GB-alan-medium",
            speed=1.0,
            pitch_offset=0,
            stability=0.7,
            similarity_boost=0.8,
        ),
        "greeting": "Work complete. {summary}.",
        "personality": PersonalityProfile(
            formality=3, verbosity=2, humor=1,
            technical_depth=5, energy=2, warmth=3,
            catchphrases=["Work complete.", "Bundle indexed.", "Namespace verified."],
            vocabulary=["bundle", "namespace", "index", "upsert", "reconcile", "schema", "federation"],
            speed_modifier=0.9,
        ),
    },
    "shawn": {
        "slug": "shawn",
        "name": "Shawn",
        "theme": "kingdom-hearts",
        "voice": VoiceConfig(
            elevenlabs_voice_id=None,
            elevenlabs_voice_name="Grace",
            kokoro_preset="af_nicole",
            piper_model="en_US-lessac-medium",
            speed=1.0,
            pitch_offset=0,
            stability=0.4,
            similarity_boost=0.7,
        ),
        "greeting": "{summary}. May your heart guide you.",
        "personality": PersonalityProfile(
            formality=3, verbosity=3, humor=3,
            technical_depth=4, energy=3, warmth=5,
            catchphrases=["May your heart guide you.", "The garden grows.", "Intents over implementations."],
            vocabulary=["garden", "cultivate", "rhythm", "intent", "light", "heart", "connection"],
            speed_modifier=0.95,
        ),
    },
    "dan": {
        "slug": "dan",
        "name": "Dan",
        "theme": "mario",
        "voice": VoiceConfig(
            elevenlabs_voice_id=None,
            elevenlabs_voice_name="Elli",
            kokoro_preset="af_sarah",
            piper_model="en_US-joe-medium",
            speed=1.05,
            pitch_offset=0,
            stability=0.3,
            similarity_boost=0.75,
        ),
        "greeting": "Wahoo! {summary}!",
        "personality": PersonalityProfile(
            formality=1, verbosity=2, humor=4,
            technical_depth=4, energy=5, warmth=4,
            catchphrases=["Wahoo!", "Let's-a go!", "Here we go!"],
            vocabulary=["ship", "train", "finetune", "deploy", "inference", "benchmark", "speedrun"],
            speed_modifier=1.15,
        ),
    },
    "alucek": {
        "slug": "alucek",
        "name": "Alucek",
        "theme": "smash",
        "voice": VoiceConfig(
            elevenlabs_voice_id=None,
            elevenlabs_voice_name="Josh",
            kokoro_preset="am_michael",
            piper_model="en_US-kusal-medium",
            speed=1.0,
            pitch_offset=0,
            stability=0.5,
            similarity_boost=0.8,
        ),
        "greeting": "{summary} -- GAME!",
        "personality": PersonalityProfile(
            formality=3, verbosity=3, humor=2,
            technical_depth=5, energy=4, warmth=2,
            catchphrases=["GAME!", "Final destination.", "No items."],
            vocabulary=["agent", "dispatch", "compose", "orchestrate", "spawn", "coordinate", "graph"],
            speed_modifier=1.05,
        ),
    },
    "claude": {
        "slug": "claude",
        "name": "Claude",
        "theme": "default",
        "voice": VoiceConfig(
            elevenlabs_voice_id=None,
            elevenlabs_voice_name="Rachel",
            kokoro_preset="af_default",
            piper_model="en_US-lessac-medium",
            speed=1.0,
            pitch_offset=0,
            stability=0.5,
            similarity_boost=0.75,
        ),
        "greeting": "{summary}",
        "personality": PersonalityProfile(
            formality=3, verbosity=3, humor=2,
            technical_depth=3, energy=3, warmth=3,
            catchphrases=["Done.", "Ready.", "Here's what I found."],
            vocabulary=[],
            speed_modifier=1.0,
        ),
    },
}
```

---

## 12. Open Questions

### Q1: Should the identity resolver cache the resolved identity for the session, or re-resolve on every event?

**Current design**: Cache at session start, check for drift on each event (Section 8). This is the recommended approach because:
- Identity resolution reads environment variables and optionally parses YAML files. Doing this on every hook event (potentially 100+ per session) adds latency for no benefit.
- Identity rarely changes mid-session. The drift check is cheap (compare two strings) and handles the rare case.
- Session state in `session.json` provides observability — you can inspect the file to see who voice thinks is speaking.

**Alternative**: Re-resolve fully on every event. Simpler code, no session.json, no drift detection. But slower and produces no observable state.

**Recommendation**: Keep the cache. The drift check is the right tradeoff between correctness and performance.

### Q2: Should persona personality profiles live in claude-voice config, or read from claude-personas character YAML?

**Current design**: Built-in defaults in voice code (Section 11), overridable via `config.yaml` → `personality_overrides`. Character YAML is read-only enrichment, not the source of truth.

Three arguments for this:
1. **Separation of concerns**: Voice personality (formality=4, verbosity=1) is a voice-specific interpretation of the persona. The character YAML's `personality.adjectives: [terse, decisive]` is the upstream semantic description. Translating "terse" to "verbosity=1" is voice's job, not persona's.
2. **No coupling**: If claude-personas changes its YAML schema, voice is unaffected.
3. **Testability**: Voice can be tested in isolation with its built-in defaults, without requiring character YAML files on disk.

**Alternative**: Read `personality.adjectives` from character YAML and auto-derive personality scores. More DRY, but creates a hard dependency on claude-personas and requires a fragile adjective-to-score mapping.

**Recommendation**: Keep built-in defaults. Optionally enrich from character YAML in a future phase (with explicit adjective-to-score mapping documented and configurable).

### Q3: Should custom voices (cloned via ElevenLabs) be supported per-persona?

**Yes, architecturally supported.** The `voice_overrides` section in config.yaml allows setting `elevenlabs_voice_id` per persona. If a user clones their own voice in ElevenLabs, they set:

```yaml
identity:
  voice_overrides:
    shawn:
      elevenlabs_voice_id: "cloned-voice-id-here"
      stability: 0.7
```

No code changes needed — the registry merge logic (Section 10) handles this.

**Implementation note**: Cloned voices require an ElevenLabs API key with clone permissions. The TTS engine should validate the voice ID on first use and fall back to the default voice (Rachel) if the clone is unavailable.

### Q4: Should the system support user-created persona profiles?

**Not in Phase 1.** The current design supports 7 built-in personas (6 named + claude default). Adding a new persona requires:
1. Adding an entry to `BUILTIN_PERSONAS` in the voice code
2. Optionally adding a `*.character.yaml` in claude-personas

**Phase 2 possibility**: A `custom_personas` section in config.yaml that allows defining new personas without code changes:

```yaml
identity:
  custom_personas:
    trent:
      name: "Trent"
      theme: warcraft
      voice:
        kokoro_preset: am_adam
        speed: 1.0
      greeting: "Ocean calls. {summary}."
      personality:
        formality: 2
        verbosity: 3
        humor: 2
        technical_depth: 5
        energy: 3
        warmth: 3
        catchphrases: ["Ocean calls.", "Protocol established.", "Token deployed."]
```

This is straightforward to implement (merge custom_personas into the registry alongside BUILTIN_PERSONAS) but adds configuration surface area. Defer until there's a concrete need.

### Q5: Should emotions have persistence across events?

**Current design**: Emotions are stateless — each event gets a fresh emotion detection. A `concerned` event does not make the next event more likely to be `concerned`.

**Alternative**: Emotional momentum — maintain a running emotional state that decays toward neutral. Three consecutive errors would produce increasing concern (pitch dropping further, speed slowing more). A commit after errors would produce extra relief.

**Recommendation**: Defer to Phase 2. Stateless emotions are simpler to reason about and debug. Emotional momentum is compelling but requires careful tuning to avoid the system feeling "moody" rather than responsive.

### Q6: How should the identity system interact with subagent sessions?

**Current consideration**: When a SubagentStart fires, the subagent may be running as a different persona (e.g., Matt dispatches to a sonnet researcher). The SubagentStop event returns to the parent session. Should the voice briefly shift identity for the subagent duration?

**Recommendation**: No. Subagents are short-lived and their events are limited (SubagentStart, SubagentStop). Changing voice identity for a few seconds would be jarring, not informative. The parent session's identity should persist throughout. The SubagentStart/SubagentStop sounds already signal the dispatch-and-return cycle — that's sufficient audio feedback.
