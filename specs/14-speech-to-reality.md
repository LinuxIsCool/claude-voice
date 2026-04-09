---
title: "Speech-to-Reality — Autonomous Voice Pipeline & claude-llms Integration"
spec: "14"
status: draft
created: 2026-03-26
author: matt
tags: [claude-voice, speech-to-reality, autonomous, claude-llms, pipeline]
---

# Speech-to-Reality — Autonomous Voice Pipeline & claude-llms Integration

## 1. Overview

Speech-to-reality is the long-term vision for claude-voice: a complete pipeline from spoken intent to executed action to auditory feedback. The user speaks, the system understands, dispatches agents, produces results, and narrates completion. Today claude-voice plays themed sounds on hook events and reads assistant output aloud. Speech-to-reality extends this from passive feedback into active control — voice becomes the primary interface for driving Legion, with the keyboard as fallback rather than default.

This spec bridges three systems:

- **claude-voice** (audio I/O) — microphone capture, STT transcription, TTS narration, themed sound feedback, gamification
- **claude-llms** (model routing) — provider-prefix addressing, three-tier routing, embedding pipelines, dynamic model discovery
- **Legion agent architecture** — plugin agents, persona agents, venture agents, hook events, MCP servers, KOI bundles

The research foundation comes from two architectural blueprints identifying 50 essential components and 50 essential mental models for speech-to-reality systems, synthesized against the claude-llms docking fleet analysis of 115 repositories. The core architectural insight: this system is not a pipeline but a dissipative structure — an open, far-from-equilibrium system that maintains coherence through continuous energy exchange with its environment.

## 2. Vision Statement

The ultimate goal is a terminal that listens, thinks, acts, and speaks.

- **Voice as primary interface.** Shawn speaks a command. The system transcribes, parses intent, routes to the right agent, executes, and narrates the result. The keyboard becomes optional for most workflows. Coding in silence wastes a sensory channel — speech-to-reality fills it with bidirectional intelligence.

- **Spoken commands trigger complex multi-agent workflows.** "Fix the login bug that Greg reported yesterday" should search messages for Greg's report, find the bug description, dispatch a coding agent to search the codebase, identify the issue, write the fix, run tests, and report back. One utterance, many agents, real results.

- **System narrates its own progress in real-time.** Not silence followed by a wall of text — a running commentary. "Searching for Greg's message... Found it. The login timeout issue in auth.py. Analyzing... Fix applied. Running tests... All pass. Ready for your review, Commander."

- **Ambient soundscape reflects system state.** The terminal has a mood. Quiet ambient when idle. Tension building during long operations. Triumph on success. The rhythms bridge already provides time-of-day awareness — speech-to-reality extends this to task-state awareness.

- **The terminal becomes a living, responsive environment.** Not a dumb text box waiting for keystrokes. A collaborator that listens, responds, anticipates, and grows more capable over time. The gamification layer already tracks XP and levels — speech-to-reality makes the game real.

## 3. Pipeline Architecture

```
Speech --> STT --> Intent Parser --> Action Router --> Agent Dispatch --> Execution --> Result --> TTS --> Audio
  ^                                                                                                      |
  |                                                                                                      v
  +---------------------------------------------- Feedback Loop ----------------------------------------+
```

### Stage 1: Capture

Microphone audio enters through PipeWire (the audio server already running on this CachyOS machine). The STT engine (`lib/stt.py`) manages a rolling audio buffer using `sounddevice` for capture. Silero VAD (87.7% TPR at 5% FPR, under 1ms per chunk) handles voice activity detection — the system listens continuously but only processes speech segments. The buffer maintains 60 seconds of audio for error recovery and re-transcription.

Audio format: 16kHz mono PCM, matching faster-whisper's expected input. PipeWire handles sample rate conversion transparently if the microphone runs at a different native rate.

### Stage 2: Transcribe

faster-whisper running Whisper large-v3-turbo on the RTX 4070 transcribes speech segments into raw text. The model is already installed in the `whisperx-env` environment. Performance target: real-time factor under 0.1x (transcribe 10 seconds of audio in under 1 second). The 12GB VRAM on the RTX 4070 comfortably holds the large-v3-turbo model alongside normal GPU workloads.

Output: raw transcript text with word-level timestamps and confidence scores. Low-confidence segments get flagged for potential clarification.

### Stage 3: Parse

Raw transcript text goes through LLM-based intent extraction to produce a structured command. This is where claude-llms integration begins. The intent parser produces a typed structure:

```python
@dataclass
class VoiceIntent:
    action: str           # "fix", "search", "commit", "status", "research"
    target: str           # "login bug", "auth module", "main branch"
    context: str          # "Greg reported", "from yesterday"
    confidence: float     # 0.0-1.0
    raw_transcript: str   # original spoken text
    temporal: str | None  # "yesterday", "last week", "before the meeting"
    entities: list[str]   # extracted names, paths, identifiers
```

Two-stage classification minimizes latency:

1. **Fast keyword match** (regex on transcribed text) — under 1ms. Catches obvious commands: "commit", "git status", "run tests". No LLM call needed for these.
2. **LLM classification** for ambiguous commands — approximately 200ms via Haiku. Handles nuance: "do the thing we did yesterday", "fix what Greg mentioned", "make that component look better".

### Stage 4: Route

The action router matches parsed intent to a Legion capability. This is the dispatch table — which plugin, agent, or tool handles this request?

```python
ROUTE_TABLE = {
    "code":     ["write", "edit", "fix", "refactor", "implement", "add", "delete", "rename"],
    "search":   ["find", "search", "look for", "where is", "grep", "locate"],
    "git":      ["commit", "push", "branch", "merge", "diff", "status", "log", "rebase"],
    "system":   ["status", "health", "restart", "deploy", "backup", "disk", "memory"],
    "research": ["research", "study", "investigate", "explore", "learn", "read about"],
    "navigate": ["open", "go to", "switch", "cd", "show", "display", "list"],
    "message":  ["message", "email", "text", "send", "reply", "check messages"],
    "meta":     ["help", "settings", "theme", "volume", "mute", "unmute", "voice"],
    "journal":  ["journal", "note", "log", "record", "reflect", "write down"],
    "koi":      ["bundle", "ingest", "index", "embed", "koi"],
}
```

For commands that don't match the route table, the router falls back to embedding similarity against known command patterns using TELUS E5-v5 (1024-dim vectors). This enables fuzzy matching: "do the thing we did yesterday" can match against a history of recent voice commands stored in KOI.

### Stage 5: Dispatch

Launch the appropriate agent(s). Three dispatch mechanisms, selected by complexity:

1. **Direct tool call** — simple commands that map to a single operation. "Git status" becomes `subprocess.run(["git", "status"])`. No agent needed.
2. **claude -p** — moderate complexity. "Fix the typo in auth.py" becomes `claude -p "Fix the typo in auth.py" --allowedTools Write Edit`. Claude Max subscription makes this essentially unlimited.
3. **Agent SDK** — complex multi-step workflows. "Fix the login bug that Greg reported" requires searching messages, analyzing code, writing a fix, running tests. The Agent SDK provides typed messages, permission handling, and async iteration.

```python
# Dispatch via Agent SDK (production path)
from claude_agent_sdk import query, ClaudeAgentOptions

async def dispatch_agent(intent: VoiceIntent) -> AsyncIterator[AgentMessage]:
    prompt = construct_prompt(intent)
    options = ClaudeAgentOptions(
        allowed_tools=resolve_tools(intent.action),
        max_tokens=4096,
    )
    async for message in query(prompt=prompt, options=options):
        yield message
```

### Stage 6: Execute

The dispatched agent performs work. This stage is opaque to the voice pipeline — the agent uses whatever tools it needs (file edits, searches, API calls, subprocess commands). The pipeline's role here is monitoring, not controlling.

Monitoring hooks:
- PostToolUse events signal tool completion
- Agent stdout/stderr provides progress information
- Exit code signals success or failure

### Stage 7: Narrate

Agent output gets compressed into a TTS-friendly summary. Raw agent output is often too verbose for speech — nobody wants to hear 200 lines of diff read aloud. The narration layer:

1. Captures full agent output (preserving it for display)
2. Generates a spoken summary via Haiku (target: under 20 words)
3. Applies theme-appropriate framing ("Commander, the fix is deployed" vs "Hey! Listen! Bug squashed!")
4. Sends the summary text to the TTS engine

```python
NARRATION_TEMPLATES = {
    "starcraft": {
        "success": "Commander, {summary}. Awaiting further orders.",
        "failure": "Commander, we have a problem. {summary}.",
        "progress": "Tactical update: {summary}.",
    },
    "zelda": {
        "success": "Hey! Listen! {summary}!",
        "failure": "Watch out! {summary}!",
        "progress": "{summary}...",
    },
    "default": {
        "success": "{summary}. Done.",
        "failure": "{summary}. Failed.",
        "progress": "{summary}...",
    },
}
```

### Stage 8: Feedback

Every pipeline stage transition produces audio feedback. The theme engine already maps semantic tokens to sound files — speech-to-reality adds new tokens for voice-specific events:

| Stage Transition | Sound Token | StarCraft Example | Zelda Example |
|-----------------|-------------|-------------------|---------------|
| Recording started | `voice.capture.start` | Radio click-on | Fairy sparkle |
| Transcription complete | `voice.transcribe.done` | Comm beep | Item get chime |
| Intent parsed | `voice.intent.parsed` | Target acquired | Puzzle solved |
| Agent dispatched | `voice.agent.dispatch` | Unit deployment | Hookshot fire |
| Execution progress | `voice.exec.progress` | Ambient radio chatter | Background music shift |
| Success | `voice.exec.success` | Mission complete fanfare | Treasure chest open |
| Failure | `voice.exec.failure` | Unit lost | Game over sting |
| Clarification needed | `voice.clarify` | "Need more info" voice | Navi "Hey!" |

## 4. claude-llms Integration

The voice pipeline uses claude-llms as its LLM backend for every stage that requires language understanding. The provider-prefix syntax (`a:claude-3-5-haiku`, `t:gpt-oss-120b`) provides clean model addressing.

### 4a. Intent Parsing

Route spoken text through claude-llms `complete()` API with three-tier fallback:

| Tier | Provider-Prefix | Model | Use Case | Latency Target |
|------|----------------|-------|----------|----------------|
| Tier 1 (local) | `t:gemma-3-27b` | TELUS Gemma 27B | Simple command parsing ("commit", "status") | <100ms |
| Tier 2 (fast) | `a:claude-3-5-haiku` | Haiku | Complex intent extraction with context | <300ms |
| Tier 3 (frontier) | `a:claude-sonnet-4` | Sonnet | Ambiguous multi-step commands | <1000ms |

Tier selection is automatic based on regex match confidence. If the fast keyword matcher fires with high confidence, skip the LLM entirely. If partial match, use Tier 1. If no match or low confidence, escalate to Tier 2. Tier 3 reserved for genuinely ambiguous multi-step requests.

### 4b. Agent Dispatch

Use `claude -p` as the backend LLM for agent execution:

- The Claude Max subscription ($100-200/mo) enables essentially unlimited `-p` usage for batch processing
- Voice command flow: parse intent, construct prompt with full context, dispatch via `claude -p "task" --allowedTools ...`
- For production workflows: `claude_agent_sdk.query(prompt=task, options=ClaudeAgentOptions(...))` provides typed messages, permission handling, and async iteration
- Three invocation patterns from the docking fleet analysis:
  1. **Shell**: `claude -p "task" --allowedTools "Write" "Edit" | process`
  2. **Python**: `subprocess.Popen(["claude", "-p", task], stdout=PIPE)` with streaming
  3. **SDK**: `claude_agent_sdk.query(prompt=task)` — the production path

### 4c. Summary Generation

Use Haiku for generating concise TTS summaries from verbose agent output:

```
Agent output (500 lines of diff + test results)
    --> a:claude-3-5-haiku with prompt: "Summarize in under 20 words for spoken narration"
    --> "Fixed session validation in auth.py, all 47 tests pass"
    --> Apply theme template
    --> "Commander, session validation fixed in auth.py. All 47 tests pass. Awaiting further orders."
    --> TTS engine
    --> Speaker
```

The summary prompt includes theme context so Haiku can adjust tone. The 20-word limit is a hard constraint — spoken summaries must be brief or they become annoying.

### 4d. Embedding for Semantic Matching

Use TELUS E5-v5 (1024-dim, `t:nv-embedqa-e5-v5`) for semantic matching of voice commands against known intents:

- Voice commands are embedded and compared against a vector index of known command patterns
- Enables fuzzy command recognition: "do the thing we did yesterday" matches against recent command history
- Command history stored in KOI (`legion.claude-voice` namespace) with embeddings
- Similarity threshold: 0.85 for auto-execute, 0.65-0.85 for confirmation, below 0.65 for clarification
- The embedding model is already integrated into the TELUS infrastructure — zero additional cost

## 5. Intent Classification

How spoken text becomes structured commands. The classification system must be fast (human patience for voice interfaces is measured in hundreds of milliseconds) and accurate (misinterpreted commands erode trust quickly).

### Intent Categories

```python
INTENT_CATEGORIES = {
    "code":     ["write", "edit", "fix", "refactor", "implement", "add", "delete", "rename", "create"],
    "search":   ["find", "search", "look for", "where is", "grep", "locate", "show me"],
    "git":      ["commit", "push", "branch", "merge", "diff", "status", "log", "rebase", "stash"],
    "system":   ["status", "health", "restart", "deploy", "backup", "disk", "memory", "uptime"],
    "research": ["research", "study", "investigate", "explore", "learn", "read about", "what is"],
    "navigate": ["open", "go to", "switch", "cd", "show", "display", "list", "tree"],
    "message":  ["message", "email", "text", "send", "reply", "check messages", "who wrote"],
    "meta":     ["help", "settings", "theme", "volume", "mute", "unmute", "voice", "quiet"],
    "journal":  ["journal", "note", "log", "record", "reflect", "write down", "capture"],
    "koi":      ["bundle", "ingest", "index", "embed", "koi", "namespace"],
}
```

### Two-Stage Classification

**Stage 1: Fast keyword match** — under 1ms

Compiled regex patterns match against the transcribed text. Each category has a regex built from its keyword list, with word boundary anchors to prevent false positives. Matching returns immediately with a category and high confidence.

```python
# Pre-compiled at module load time
CATEGORY_PATTERNS = {
    category: re.compile(
        r'\b(' + '|'.join(re.escape(kw) for kw in keywords) + r')\b',
        re.IGNORECASE
    )
    for category, keywords in INTENT_CATEGORIES.items()
}
```

When Stage 1 matches with high confidence (single category, strong keyword), the system skips Stage 2 entirely. "Git status" does not need an LLM call.

**Stage 2: LLM classification** — approximately 200ms via Haiku

For ambiguous commands (multiple category matches, no keyword match, or complex phrasing), the intent parser sends the transcript to `a:claude-3-5-haiku` with a structured extraction prompt:

```
Extract the intent from this voice command. Return JSON.
Command: "{transcript}"
Recent context: {last_3_commands}

Return: {"action": str, "target": str, "context": str, "category": str, "confidence": float}
```

The recent context window (last 3 commands) enables coreference resolution: "do that again" or "but for the other file" become interpretable.

### Confidence-Based Routing

| Confidence | Action |
|-----------|--------|
| 0.90-1.00 | Auto-execute immediately |
| 0.70-0.89 | Execute with brief confirmation tone |
| 0.40-0.69 | Speak back interpretation: "I heard: fix the login module. Proceed?" |
| 0.00-0.39 | Request clarification: "Could you say that again?" |

The thresholds are tunable. Early phases should bias toward confirmation (lower auto-execute threshold) and gradually increase autonomy as the system proves reliable.

## 6. Mental Models Applied

The speech-to-reality research identifies 50 mental models. These six are most directly applicable to the claude-voice pipeline:

### OODA Loop

Colonel Boyd's Observe-Orient-Decide-Act loop maps directly to the pipeline stages:

| OODA Phase | Pipeline Stage | Implementation |
|-----------|---------------|----------------|
| **Observe** | Capture + Transcribe | Microphone -> PipeWire -> faster-whisper -> raw text |
| **Orient** | Parse + Route | Intent extraction via claude-llms, match to capability |
| **Decide** | Dispatch | Select agent type, construct prompt, choose tools |
| **Act** | Execute + Narrate | Agent performs work, system narrates result |

The critical insight from Boyd: **orientation is where all intelligence lives**. Speed without good orientation is dangerous. The intent parsing stage (Orient) must be thorough even if it costs 200ms — a misinterpreted command that executes instantly is worse than a correctly interpreted one that takes a beat.

OODA loops operate at five nested timescales in the voice pipeline:
- **Milliseconds**: VAD decides if audio chunk contains speech
- **Seconds**: Transcription + intent parsing for a single utterance
- **Minutes**: Multi-turn voice conversation with context accumulation
- **Hours**: Session-level patterns (common commands, preferred phrasing)
- **Days**: Long-term learning (which commands Shawn uses most, what gets corrected)

### Stigmergy

Agents leave traces of their work in shared state — logs, KOI bundles, hippo graph nodes, journal entries. These traces inform future voice commands without explicit communication:

- A voice command "fix the bug" triggers a search. The agent writes its findings to the logging DB. Next time Shawn says "that bug", the system finds the trace and knows which bug.
- Command patterns accumulate in KOI. Frequently spoken commands get higher priority in fuzzy matching.
- Failed commands leave traces too — the system learns which phrasings lead to misinterpretation and can proactively suggest alternatives.

The stigmergic layer is what makes the system feel like it has memory. No explicit memory management — just traces that naturally accumulate and decay.

### Antifragility

The system should improve from voice input errors, not just tolerate them:

- **Misrecognition patterns**: When Shawn corrects a transcription ("No, I said 'refactor' not 'reactor'"), the system logs the error. Over time, these corrections build a domain-specific language model prior.
- **Intent correction patterns**: When Shawn says "No, I meant the other thing", the system logs which intent was wrong and what the correct one was. This trains the confidence thresholds.
- **Barbell strategy**: 90% battle-tested, deterministic components (regex matching, known command patterns, proven TTS/STT) combined with 10% experimental (LLM intent parsing, fuzzy matching, autonomous dispatch). The deterministic components provide stability; the experimental ones provide growth.

### Ashby's Law of Requisite Variety

The variety of voice commands must match the variety of system capabilities. If Legion can do 200 things but the voice interface only recognizes 20 commands, the interface is a bottleneck. Conversely, if the voice interface claims to understand everything but routes poorly, it creates false confidence.

Implementation: the route table must grow with the plugin ecosystem. Every new plugin that registers MCP tools should automatically expand the voice command vocabulary. Dynamic discovery (from the claude-llms architecture) applies here — the voice system probes available capabilities at session start and builds its route table accordingly.

### Monte Carlo Tree Search (for ambiguous commands)

When a voice command has multiple valid interpretations, the system can explore them in parallel:

```
"Fix the auth thing" -->
  Interpretation A: Fix authentication module (auth.py)    [P=0.6]
  Interpretation B: Fix authorization config (authz.yaml)  [P=0.3]
  Interpretation C: Fix auth test failures                 [P=0.1]
```

Rather than picking the highest-probability interpretation blindly, MCTS-style exploration evaluates each interpretation cheaply (check if the file exists, check recent git changes, check test status) before committing to execution. This adds 100-200ms but dramatically reduces misinterpretation for ambiguous commands.

### Viable System Model

Stafford Beer's VSM maps to the voice pipeline's organizational structure:

| VSM System | Voice Pipeline Component |
|-----------|-------------------------|
| **S1 Operations** | Individual agents executing commands |
| **S2 Coordination** | Route table preventing conflicts (two agents editing the same file) |
| **S3 Control** | Confidence thresholds, latency budgets, cost limits |
| **S3* Audit** | Logging every voice command, intent, and outcome to claude-logging |
| **S4 Intelligence** | Embedding similarity tracking trends in voice usage |
| **S5 Policy** | Human-in-the-loop confirmation for destructive actions |

## 7. Feedback Loop Design

Every pipeline stage produces audio feedback. The principle: the user should never wonder "did it hear me?" or "is it working?" Audio fills the information gap that text cannot.

### Stage-by-Stage Feedback

| Stage | Audio Feedback | Purpose | Latency Budget |
|-------|---------------|---------|----------------|
| Capture start | Recording indicator sound (radio click-on) | Confirms microphone is active | <10ms |
| VAD trigger | Subtle acknowledgment (soft chime) | Confirms speech detected | <10ms |
| Transcription complete | Confirmation tone (data received beep) | Confirms words were captured | <50ms |
| Intent parsed | Affirmative tone (target locked) | Confirms understanding | <10ms |
| Agent dispatched | Deployment sound (unit acknowledged) | Confirms work has begun | <10ms |
| Execution in progress | Ambient shift (background music/tension) | Indicates ongoing work | Continuous |
| Completion (success) | Narrated summary + victory sound | Reports result | <500ms for TTS |
| Completion (failure) | Narrated error + failure sound | Reports problem | <500ms for TTS |
| Clarification needed | Question tone + spoken prompt | Requests more input | <500ms for TTS |

### Theme-Appropriate Feedback

The six swappable themes (StarCraft, Warcraft, Mario, Zelda, Smash Bros, Kingdom Hearts) each provide their own feedback personality:

**StarCraft**: Military radio comms. Click-on for capture, tactical beep for parse, "SCV ready" for dispatch, Terran victory theme for success.

**Zelda**: Fairy and nature sounds. Sparkle for capture, item chime for parse, hookshot for dispatch, treasure chest fanfare for success, Navi's "Hey! Listen!" for clarification.

**Mario**: Coin and power-up sounds. Coin for capture, mushroom for parse, pipe travel for dispatch, star power for success, death jingle for failure.

### Ambient Soundscape

During long-running agent operations, the terminal should not be silent. The rhythms bridge already provides time-of-day ambient awareness. Speech-to-reality adds task-state ambient layers:

- **Idle**: Quiet ambient matching time-of-day rhythm
- **Listening**: Subtle "attention" tone layered over ambient
- **Processing**: Gentle tension build (tempo increase, harmonic shift)
- **Executing**: Active work sounds (keyboard clicks, data transmission)
- **Waiting**: Patient hold music (the agent is blocked on something)
- **Complete**: Resolution (tension release, return to idle ambient)

These layers mix dynamically using PipeWire's audio routing. Multiple simultaneous agents can each contribute their own ambient layer.

## 8. Real-Time Narration

The system narrates its own actions as they happen, creating a conversational feel rather than a request-response pattern.

### Narration Strategy

Not every action deserves narration. PostToolUse hooks fire tens of thousands of times per session — narrating all of them would be overwhelming and unusable. The narration filter selects only significant events:

**Always narrate:**
- Agent start ("Searching for the authentication module...")
- Significant findings ("Found 3 matching files. Analyzing...")
- Write/Edit completions ("Fix applied to auth.py, line 47.")
- Test results ("All tests pass." or "2 tests failed: test_login, test_session.")
- Agent completion ("Ready for commit.")

**Never narrate:**
- Read operations (too frequent, too boring)
- Internal reasoning steps (the agent's thought process is for logs, not speech)
- Glob/Grep operations (intermediate search steps)
- Repeat operations (if the agent reads the same file twice, only narrate once)

**Narrate on request:**
- Detailed progress ("Tell me what you're doing" enables verbose mode)
- File contents ("Read that back to me" triggers TTS of the last read file)

### Narration Generation

Each narration-worthy event gets a brief TTS summary:

```python
async def narrate_event(event: ToolUseEvent, theme: str) -> None:
    """Generate and speak a brief narration for a significant tool event."""
    if not is_narration_worthy(event):
        return

    # Generate summary via Haiku (fast, cheap)
    summary = await claude_llms.complete(
        model="a:claude-3-5-haiku",
        prompt=f"Summarize this tool result in under 15 words for spoken narration: {event.output[:500]}",
    )

    # Apply theme template
    template = NARRATION_TEMPLATES[theme][event.status]
    narration = template.format(summary=summary)

    # Speak via TTS engine (fire-and-forget, non-blocking)
    await tts_engine.speak(narration, priority="narration")
```

### Narration Queue

Multiple narration events can arrive in quick succession. A priority queue prevents overlap:

| Priority | Type | Behavior |
|---------|------|----------|
| 1 (highest) | Error/failure | Interrupts current narration |
| 2 | Completion summary | Waits for current narration to finish |
| 3 | Progress update | Dropped if queue length > 2 |
| 4 (lowest) | Ambient observation | Dropped if any higher-priority item queued |

The fcntl file lock already used in `lib/tts.py` prevents concurrent TTS collisions from subagents. The narration queue sits above this lock, managing ordering before TTS submission.

## 9. Autonomous Workflow Example

End-to-end scenario demonstrating the full speech-to-reality pipeline.

### Scenario: "Fix the login bug that Greg reported yesterday"

**Stage 1 — Capture:**
Shawn speaks into the microphone. PipeWire captures audio at 16kHz mono. Silero VAD detects speech onset at 0ms, speech offset at 2,800ms. Audio buffer contains 2.8 seconds of speech.

Sound: `voice.capture.start` (radio click-on)

**Stage 2 — Transcribe:**
faster-whisper large-v3-turbo processes the buffer in 280ms (0.1x real-time factor). Output: "Fix the login bug that Greg reported yesterday" with 0.94 confidence.

Sound: `voice.transcribe.done` (comm beep)

**Stage 3 — Parse:**
Fast keyword match catches "fix" (code category) but the full command is complex. Escalate to Tier 2.

Haiku processes in 210ms. Returns:
```json
{
    "action": "fix",
    "target": "login bug",
    "context": "Greg reported",
    "category": "code",
    "confidence": 0.87,
    "temporal": "yesterday",
    "entities": ["Greg", "login"]
}
```

Sound: `voice.intent.parsed` (target acquired)

**Stage 4 — Route:**
Category "code" with entity "Greg" and temporal "yesterday" triggers a compound route:
1. First: search messages for Greg's report from yesterday (message route)
2. Then: fix the identified bug (code route)

This is a multi-step plan, so dispatch goes to Agent SDK, not simple `claude -p`.

**Stage 5 — Dispatch:**
Agent SDK launches with the constructed prompt:

```
Search messages from Greg from yesterday about a login bug. Find the bug description, then:
1. Search the codebase for the relevant code
2. Identify the bug
3. Write a fix
4. Run the relevant tests
Report what you did.
```

Sound: `voice.agent.dispatch` (unit deployment)

Narration (TTS): "Searching Greg's messages from yesterday..."

**Stage 6 — Execute:**
The agent works through the steps:

1. Calls messages MCP tool: `messages_search("from:Greg login bug", after:"2026-03-25")`
   - Finds: "The login page times out after 30 seconds when the session token expires"
   - Narration: "Found Greg's report. Session token timeout issue."

2. Calls Grep: searches for session token handling in auth code
   - Finds: `auth/session.py` with `SESSION_TIMEOUT = 30`
   - Narration: "Found the session handler. Analyzing..."

3. Calls Edit: increases timeout and adds token refresh logic
   - Narration: "Fix applied to session.py."

4. Calls Bash: runs `pytest tests/test_auth.py`
   - 12 tests pass, 0 fail
   - Narration: "All 12 auth tests pass."

Sound: ambient tension building during execution, punctuated by progress chimes

**Stage 7 — Narrate:**
Agent completes. Full output (47 lines of diff, 12 test results) gets summarized by Haiku:

Summary: "Fixed session validation timeout in auth/session.py. Added token refresh. All 12 tests pass."

Theme template (StarCraft): "Commander, session validation fixed in auth/session.py. Token refresh added. All 12 tests pass. Ready for your review."

TTS speaks the themed summary.

**Stage 8 — Feedback:**
Sound: `voice.exec.success` (mission complete fanfare)
Gamification: +150 XP for voice-driven bug fix
Achievement check: "Voice Commander" (10 voice-driven fixes) — not yet unlocked (7/10)

Total elapsed time: approximately 45 seconds from speech to completion narration.

The keyboard was never touched.

## 10. Phase 6 Roadmap

Speech-to-reality is Phase 6 of the claude-voice development plan. It builds on all prior phases (scaffold, themes, sounds, hooks, TTS, STT, identity, gamification, rhythms, assets, quality).

| Phase | Capability | Dependencies | Complexity | Estimated Effort |
|-------|-----------|-------------|------------|-----------------|
| **6a** | Basic voice commands ("commit", "status", "search") | STT engine (spec 07) + intent regex | Low | 1-2 days |
| **6b** | LLM intent parsing for complex commands | claude-llms integration, Haiku access | Medium | 3-5 days |
| **6c** | Multi-agent dispatch from voice | Agent SDK + claude -p, route table | High | 1-2 weeks |
| **6d** | Real-time narration of agent actions | Selective PostToolUse TTS, narration queue | High | 1-2 weeks |
| **6e** | Autonomous workflow (voice -> multi-step execution) | All above integrated | Very High | 2-4 weeks |
| **6f** | Learning system (improve from corrections) | Embedding + KOI feedback storage | Very High | 2-4 weeks |

### 6a: Basic Voice Commands

The minimum viable voice interface. A small set of hardcoded commands that map directly to shell operations:

```python
BASIC_COMMANDS = {
    "commit":       "git add -A && git commit",
    "status":       "git status",
    "diff":         "git diff",
    "push":         "git push",
    "test":         "pytest",
    "build":        "npm run build",  # or project-appropriate
}
```

Spoken command -> regex match -> shell execution -> TTS result summary. No LLM needed. This proves the pipeline works end-to-end and builds confidence in the voice interface.

### 6b: LLM Intent Parsing

Add claude-llms as the brain behind intent classification. The regex matcher becomes Stage 1 (fast path), and Haiku becomes Stage 2 (smart path). This phase introduces the VoiceIntent dataclass and confidence-based routing.

Key integration point: the claude-llms plugin must expose a `complete()` function (or MCP tool) that the voice pipeline can call with provider-prefix model addressing.

### 6c: Multi-Agent Dispatch

Voice commands can now trigger complex workflows via Agent SDK. This phase introduces the route table, compound routing (search then fix), and agent lifecycle management. The voice pipeline becomes an orchestrator, not just a command translator.

### 6d: Real-Time Narration

Hook into PostToolUse events and generate selective TTS narrations. This phase introduces the narration filter, narration queue, and theme-appropriate narration templates. The terminal starts talking back.

### 6e: Autonomous Workflow

The full pipeline from the scenario in Section 9. Voice commands trigger multi-step, multi-agent workflows with real-time narration at every stage. This is where speech-to-reality becomes real — the user speaks an intent and the system handles everything.

### 6f: Learning System

The system improves from its own errors. Misrecognitions, misinterpretations, and failed commands get logged with corrections. Embeddings of command patterns build a personalized vocabulary. Confidence thresholds auto-calibrate based on historical accuracy. The system gets better the more Shawn talks to it.

## 11. 50-Component Mapping

The speech-to-reality research identifies 50 essential components across 8 clusters. This mapping shows which components are already implemented in Legion, which are partially covered, and which represent future work.

### Cluster A — Audio Capture and Speech Recognition (Components 1-5)

| # | Research Component | Legion Implementation | Status |
|---|-------------------|----------------------|--------|
| 1 | Mobile audio capture with WebRTC/Opus | PipeWire + sounddevice capture in `lib/stt.py` | Adapted (desktop, not mobile) |
| 2 | Streaming ASR engine | faster-whisper large-v3-turbo on RTX 4070 | Ready (installed in whisperx-env) |
| 3 | On-device ASR fallback | faster-whisper IS the on-device engine (no cloud ASR) | Complete (all local) |
| 4 | Speaker diarization | Not needed for single-user desktop | Scoped out |
| 5 | Real-time audio streaming via gRPC | Not needed (single-machine, no server-to-server) | Scoped out |

### Cluster B — Language Understanding and Intent Extraction (Components 6-10)

| # | Research Component | Legion Implementation | Status |
|---|-------------------|----------------------|--------|
| 6 | Joint intent classification + slot filling | Two-stage: regex + Haiku via claude-llms | Phase 6b |
| 7 | Semantic parser (NL -> structured) | VoiceIntent dataclass, Pydantic schema extraction | Phase 6b |
| 8 | Goal decomposition (HTN) | Compound routing in action router | Phase 6c |
| 9 | Contextual dialogue manager | Recent command context window (last 3 commands) | Phase 6e |
| 10 | Pragmatic inference | Embedding similarity for fuzzy matching | Phase 6f |

### Cluster C — Agentic AI Architecture (Components 11-18)

| # | Research Component | Legion Implementation | Status |
|---|-------------------|----------------------|--------|
| 11 | ReAct reasoning-action loop | Claude Code's native agent loop | Exists |
| 12 | Hierarchical multi-agent orchestrator | Agent SDK + route table dispatch | Phase 6c |
| 13 | Unified tool integration (MCP + function calling) | 26 plugins, 6 MCP servers, hook architecture | Exists |
| 14 | Reflection and self-correction | Agent SDK handles retries; learning system adds reflection | Phase 6f |
| 15 | Human-in-the-loop escalation | Confidence-based confirmation gateway | Phase 6b |
| 16 | Inter-agent communication | KOI bundles + claude-logging events as message bus | Exists |
| 17 | Self-improving meta-learning | Learning system with correction logging | Phase 6f |
| 18 | End-to-end observability | claude-logging (31K+ events, 9 types, SQLite+FTS5) | Exists |

### Cluster D — Planning and Decision-Making (Components 19-27)

| # | Research Component | Legion Implementation | Status |
|---|-------------------|----------------------|--------|
| 19 | Learned world model | Not applicable at current scale | Future |
| 20 | MCTS with learned priors | Lightweight interpretation exploration for ambiguous commands | Phase 6e |
| 21 | Belief-state tracking (POMDP) | Confidence scores + command history as belief state | Phase 6b |
| 22 | Hierarchical task network planner | Compound routing (search -> analyze -> fix -> test) | Phase 6c |
| 23 | Goal-conditioned RL with HER | Not applicable (no RL training loop) | Scoped out |
| 24 | Probabilistic inference engine | Not applicable at current scale | Future |
| 25 | Stochastic scenario planner | Not applicable at current scale | Future |
| 26 | Structured LLM reasoning (ToT/GoT) | Claude Code's native reasoning handles this | Exists (via LLM) |
| 27 | Contingency planning | Multi-interpretation exploration | Phase 6e |

### Cluster E — Knowledge, Memory, and Temporal Reasoning (Components 28-35)

| # | Research Component | Legion Implementation | Status |
|---|-------------------|----------------------|--------|
| 28 | Knowledge graph (GraphRAG) | claude-hippo (FalkorDB, 8,124 entities, 10,782 edges) | Exists |
| 29 | Temporal knowledge graph | Hippo supports temporal edges; KOI has timestamps | Exists |
| 30 | Bi-temporal memory (Graphiti/Zep) | Hippo's graph + KOI's bundle timestamps | Partial |
| 31 | Hierarchical memory (MemGPT/Letta) | Letta agent running (port 8283, GPT OSS 120B) | Exists |
| 32 | Semantic memory (vector DB) | KOI embeddings (91K/411K, TELUS E5-v5, 1024-dim) | Partial (22%) |
| 33 | Episodic memory | claude-logging session JSONL (222 sessions) | Exists |
| 34 | Ontology schema layer | Not formalized beyond plugin conventions | Future |
| 35 | Temporal reasoning (Allen's Interval Algebra) | Not implemented | Future |

### Cluster F — Infrastructure and Orchestration (Components 36-42)

| # | Research Component | Legion Implementation | Status |
|---|-------------------|----------------------|--------|
| 36 | Durable workflow orchestration (Temporal.io) | Not needed at current scale (single machine) | Future |
| 37 | Event backbone (Kafka) | claude-logging + KOI serve as event store | Adapted |
| 38 | AI compute layer (Ray) | Single RTX 4070, no distributed compute needed | Scoped out |
| 39 | Data pipeline orchestration (Prefect) | uv scripts + hooks handle orchestration | Adapted |
| 40 | MLOps continuous improvement | Not applicable (no model training) | Scoped out |
| 41 | Automated project management | Not applicable at current scale | Future |
| 42 | Digital twin simulation | Not applicable | Scoped out |

### Cluster G — Economic Modeling and Execution (Components 43-47)

| # | Research Component | Legion Implementation | Status |
|---|-------------------|----------------------|--------|
| 43 | Economic modeling / resource allocation | claude-llms three-tier routing with cost awareness | Phase 6b |
| 44 | Real-world action execution sandbox | Claude Code's permission model + allowed tools | Exists |
| 45 | Grounding and world state tracking | Plugin registry + dynamic discovery from claude-llms | Partial |
| 46 | Closed-loop feedback (Inner Monologue) | Real-time narration + correction handling | Phase 6d |
| 47 | Monitoring dashboard | claude-marimo observatory notebooks | Exists |

### Cluster H — Cross-Cutting Concerns (Components 48-50)

| # | Research Component | Legion Implementation | Status |
|---|-------------------|----------------------|--------|
| 48 | Security, sandboxing, guardrails | All STT local, confidence gates, permission model | Exists |
| 49 | Adaptive replanning | Correction-based re-dispatch | Phase 6e |
| 50 | Stigmergic coordination | KOI traces + logging events as stigmergic medium | Exists |

**Summary**: Of 50 research components, 14 exist in Legion today, 6 are adapted to the single-machine context, 15 are planned for Phase 6 sub-phases, 4 are partially implemented, 5 are future work, and 6 are scoped out as irrelevant to the desktop single-user context.

## 12. Economic Model

Cost analysis for voice-driven development on Legion.

### Fixed Costs (Already Owned)

| Resource | Cost | Notes |
|---------|------|-------|
| RTX 4070 12GB | $0/mo | Already installed. Runs faster-whisper + Kokoro locally. |
| 32GB RAM | $0/mo | Sufficient for all local inference. |
| CachyOS + PipeWire | $0/mo | Open source audio infrastructure. |
| TELUS LLM endpoints | $0/mo | Employee access. Gemma 27B, GPT OSS 120B, E5-v5. |

### Variable Costs

| Service | Usage Tier | Monthly Cost | What It Covers |
|---------|-----------|-------------|----------------|
| Claude Max subscription | Pro/Max | $100-200/mo | Unlimited `claude -p` for agent dispatch |
| ElevenLabs | Creator | $5-22/mo | Flash v2.5 TTS for narration (100K-500K chars) |
| Anthropic API (Haiku) | Pay-per-use | $2-10/mo | Intent parsing, summary generation |

### Total Monthly Cost

| Scenario | Cost | Description |
|---------|------|-------------|
| Minimal (local TTS only) | $100/mo | Claude Max + Kokoro TTS + TELUS intent parsing |
| Standard (cloud TTS) | $115-130/mo | Claude Max + ElevenLabs Creator + Haiku API |
| Full (high usage) | $150-250/mo | Claude Max + ElevenLabs Pro + heavy Haiku usage |

### Return on Investment

Quantifying ROI for a voice interface is inherently fuzzy, but the proxies are:

- **Context switching reduction**: Voice commands don't require switching from thinking to typing. If this saves 30 minutes per day of keyboard-to-thought friction, that's approximately 15 hours/month.
- **Ambient awareness**: Audio feedback creates subconscious state tracking that text cannot. Fewer "wait, what's happening?" moments.
- **Multitasking enablement**: Voice commands work while hands are occupied (coffee, stretching, pacing, whiteboarding). Recovers otherwise-dead time.
- **Gamification motivation**: XP and achievements from voice commands create a dopamine loop that encourages consistent usage.

At a conservative $50/hr developer rate, 15 hours/month of recovered time = $750/month, against $115-250/month cost. The ROI is positive if voice saves more than 30 minutes per week.

## 13. Privacy and Security

Voice interfaces create unique privacy concerns. Legion's architecture addresses these by keeping audio processing local.

### Audio Processing: All Local

- **STT is local**: faster-whisper runs on the RTX 4070. No audio leaves the machine.
- **VAD is local**: Silero VAD runs on CPU. No audio analysis leaves the machine.
- **TTS can be local**: Kokoro-82M and Piper run entirely on-device. ElevenLabs is optional cloud TTS.
- **No persistent audio recording**: The capture buffer is a rolling 60-second window in memory. Audio is transcribed and discarded. No WAV files are saved.

### What Leaves the Machine

| Data | Destination | Purpose | Sensitivity |
|------|------------|---------|-------------|
| Transcribed text (not audio) | Anthropic API (Haiku) | Intent parsing | Low (text commands, not voice) |
| Summary text (not audio) | ElevenLabs (optional) | TTS narration | Low (system summaries, not user data) |
| Transcribed text (not audio) | TELUS endpoints | Tier 1 intent parsing | Low (employer infrastructure) |
| Nothing | Anywhere else | N/A | N/A |

### Logging Policy

- Voice commands are logged to claude-logging as text (the transcribed command, not audio)
- Intent classifications are logged with confidence scores
- Agent dispatch and results are logged (same as any Claude Code session)
- No biometric voice data is stored or transmitted
- KOI bundles in `legion.claude-voice` namespace store command patterns (text) for fuzzy matching

### Destructive Action Protection

Voice commands for destructive actions require explicit confirmation:

```python
DESTRUCTIVE_ACTIONS = {
    "delete", "remove", "drop", "reset --hard", "force push",
    "rm -rf", "truncate", "destroy", "nuke", "wipe",
}
```

Any intent matching a destructive keyword triggers a confirmation prompt regardless of confidence score:

- TTS: "You said delete all test files. Are you sure?"
- Wait for voice confirmation: "Yes" / "Confirm" / "Do it"
- Timeout after 10 seconds defaults to cancel
- Confirmation must match within 2 seconds of the prompt ending (prevents old audio triggering confirmation)

## 14. Open Questions

These questions represent genuine design decisions that require experience to resolve. They are not blockers for Phase 6a-6b but become important for 6c-6f.

### Q1: Should the system learn from voice command corrections?

When Shawn says "No, I meant...", should the system update its intent classification model? The upside is personalized accuracy improvement. The downside is potential drift — if the correction corpus is small, a few atypical corrections could skew the model. Proposed approach: log all corrections to KOI, but only update the fuzzy matching index after 5+ consistent corrections for the same pattern.

### Q2: Should destructive actions always require voice confirmation?

The current design says yes. But if confidence is 0.99 and the command is "delete that temp file I just created", the confirmation feels like friction. Should there be an "expert mode" that trusts high-confidence destructive commands? Proposed approach: start strict, add expert mode only after 100+ successful voice sessions with zero accidental destructive actions.

### Q3: How to handle multi-turn voice conversations?

"Fix the login bug" -> (agent works) -> "Actually, also fix the timeout" -> (agent continues). The current pipeline is request-response. Multi-turn requires maintaining conversational state across utterances, with the agent staying "hot" between turns. Proposed approach: after agent completion, keep a 30-second listening window. If new speech arrives within the window, treat it as a follow-up to the same context. Otherwise, treat it as a new command.

### Q4: Should voice commands work when Claude Code is not in focus?

System-wide voice control would let Shawn speak commands from any window. This requires a background daemon listening on the microphone, a global hotword ("Hey Legion" or just a push-to-talk keybind), and a mechanism to inject commands into Claude Code. Proposed approach: push-to-talk via a global keybind (e.g., Super+V) is simpler and more reliable than hotword detection. The daemon captures audio only while the key is held, transcribes, and sends the command to Claude Code via a Unix socket or named pipe.

### Q5: How to handle overlapping agents?

If Shawn speaks a new command while an agent from the previous command is still running, should the new command queue, interrupt, or run in parallel? Proposed approach: run in parallel by default (the plugin already handles concurrent hook events). But if the new command targets the same files as the running agent, queue it and narrate: "The previous task is still modifying auth.py. I'll run your new command when it's done."

### Q6: What is the optimal narration density?

Too much narration is annoying. Too little makes the system feel dead. The right density probably varies by person and by task. Proposed approach: start with minimal narration (agent start + completion only), add a voice command "be more verbose" / "be quieter" to adjust density in real-time, and learn the preferred density over time from explicit feedback.

### Q7: Should the voice pipeline integrate with Letta (the subconscious)?

Letta already runs as a persistent agent with self-editing memory. Voice commands could route through Letta for context enrichment — "fix that thing" could be resolved by Letta's memory of what "that thing" refers to, even across sessions. Proposed approach: Phase 6f integration. Letta as an optional context oracle that the intent parser can consult for ambiguous references.

---

This spec is a design document. No code has been written for Phase 6. The foundation (Phases 1-5: scaffold, themes, sounds, hooks, TTS, STT, identity, gamification, rhythms, assets, quality) must be solid before speech-to-reality construction begins. The vision is clear. The components are mapped. The mental models are applied. When the foundation is ready, the pipeline builds itself one stage at a time.
