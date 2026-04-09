---
title: "Quality & Testing — Latency Gates, Benchmarks & Regression Suite"
spec: "12"
status: draft
created: 2026-03-26
author: matt
tags: [claude-voice, testing, quality, benchmark, latency]
---

# Spec 12: Quality & Testing

## 1. Overview

Quality assurance for claude-voice spans three domains: audio asset quality (synthesis output), runtime performance (latency budgets), and system integration (end-to-end hook-to-sound). This spec defines gates, benchmarks, regression tests, and the CLI tools to run them.

The quality strategy is defense-in-depth. Each layer catches a different class of defect:

- **Asset validation** catches synthesis bugs, malformed WAV files, and theme configuration errors before they reach the playback engine.
- **Latency gates** catch performance regressions that would break the <150ms psychological association threshold established in the game audio UX research (see `~/.claude/local/research/2026/03/25/voice/04-game-audio-ux.md`, Section 1.4).
- **Integration tests** catch wiring failures between the hook handler, event router, theme engine, and playback engine.
- **Fuzz tests** catch crash bugs that would violate the zero-crash guarantee (hooks must always exit 0).

Every gate is automated. Every threshold is defined in a machine-readable config. Every result is stored for historical comparison.

---

## 2. Quality Dimensions

| Dimension | Metric | Target | Maximum | Measurement Method |
|-----------|--------|--------|---------|-------------------|
| Earcon latency | Hook fire to first audio frame | 100ms | 150ms | Timer wrapping full hook pipeline |
| TTS latency (local) | `speak()` call to first audio frame | 300ms | 500ms | Timer in `lib/tts.py` |
| TTS latency (cloud) | `speak()` call to first audio frame | 100ms | 200ms | Timer in `lib/tts.py` (ElevenLabs path) |
| STT latency | Speech end to text available | 500ms | 1000ms | Timer in `lib/stt.py` |
| Asset quality | Pass all quality gates from spec 03 | 100% pass rate | 95% minimum | `generate_sounds.py --validate` |
| Crash safety | Hook exits 0 on any input | 100% | 100% (non-negotiable) | Fuzz testing with malformed payloads |
| Concurrent safety | No audio corruption with 5 simultaneous sounds | No glitches | No glitches | Stress test with parallel `pw-play` processes |
| Memory | Hook process peak RSS | 30MB | 50MB | `/proc/self/status` VmHWM field |
| Startup | Python + UV interpreter launch | 25ms | 40ms | Repeated timing of bare interpreter start |
| Hook wall time | Total time from stdin read to process exit | 45ms | 100ms | `time` wrapper around full hook execution |

### Dimension Sources

- **150ms earcon threshold**: From game audio UX research — the psychological association between action and sound degrades above 150ms, breaks significantly above 300ms (spec 05 Section 11, research doc Section 1.4).
- **56ms baseline**: The spec 05 latency analysis established a detailed pipeline breakdown showing ~56ms total for the `pw-play` path (hook stdin read through first PipeWire quantum processed).
- **Quality gates**: Inherited from spec 03 Section 9 — format validation, duration, peak amplitude, RMS level, file size, stereo balance, DC offset, and leading/trailing silence checks.

---

## 3. Latency Gates

### 3.1 Gate Definition File

File: `config/gates.json`

```json
{
  "version": 1,
  "description": "Quality gates for claude-voice benchmarks. Targets are ideal; max values are hard failures.",
  "gates": {
    "earcon_latency_ms": {
      "target": 100,
      "max": 150,
      "unit": "ms",
      "description": "Hook fire to first audible audio frame (earcon path)"
    },
    "tts_local_latency_ms": {
      "target": 300,
      "max": 500,
      "unit": "ms",
      "description": "speak() call to first audio frame via local TTS backend"
    },
    "tts_cloud_latency_ms": {
      "target": 100,
      "max": 200,
      "unit": "ms",
      "description": "speak() call to first audio frame via ElevenLabs streaming"
    },
    "stt_latency_ms": {
      "target": 500,
      "max": 1000,
      "unit": "ms",
      "description": "End of speech to transcribed text available"
    },
    "hook_wall_time_ms": {
      "target": 45,
      "max": 100,
      "unit": "ms",
      "description": "Total hook execution time from stdin read to exit"
    },
    "asset_validation_pass_rate": {
      "target": 1.0,
      "min": 0.95,
      "unit": "ratio",
      "description": "Fraction of asset files passing all quality gates"
    },
    "crash_rate": {
      "target": 0.0,
      "max": 0.0,
      "unit": "ratio",
      "description": "Fraction of hook invocations that exit non-zero. Must be zero."
    },
    "memory_peak_rss_mb": {
      "target": 30,
      "max": 50,
      "unit": "MB",
      "description": "Peak resident set size of hook process"
    },
    "startup_latency_ms": {
      "target": 25,
      "max": 40,
      "unit": "ms",
      "description": "Python + UV interpreter cold start time"
    }
  }
}
```

### 3.2 Gate Evaluation Logic

A gate result has three states:

- **PASS**: Value is at or below target (or at/above for minimums like pass rate).
- **WARN**: Value exceeds target but is within max. Performance is degraded but acceptable.
- **FAIL**: Value exceeds max. The build is broken. This must be fixed before merging.

The benchmark runner reads `gates.json`, evaluates each metric, and produces a result table with color-coded status.

### 3.3 Gate Precedence

When a gate conflicts with measured reality (e.g., a new backend changes baseline latency), the gate definition in `gates.json` is the source of truth. Baselines are descriptive; gates are prescriptive. If measured performance consistently exceeds a gate target, either the code must be optimized or the gate must be explicitly loosened with a documented justification in the commit message.

---

## 4. Benchmark CLI

### 4.1 Entry Point

File: `scripts/benchmark.py`

```
Usage:
  uv run scripts/benchmark.py                    # Run all benchmarks
  uv run scripts/benchmark.py --latency          # Latency tests only
  uv run scripts/benchmark.py --assets           # Asset validation only
  uv run scripts/benchmark.py --stress           # Concurrent playback stress test
  uv run scripts/benchmark.py --fuzz             # Fuzz hook handler
  uv run scripts/benchmark.py --memory           # Memory profiling
  uv run scripts/benchmark.py --theme starcraft  # Theme-specific tests only
  uv run scripts/benchmark.py --report           # Generate HTML report
  uv run scripts/benchmark.py --compare          # Compare against previous run
  uv run scripts/benchmark.py --ci               # Exit non-zero on any FAIL gate
```

### 4.2 Output Format

Standard output is a human-readable table:

```
claude-voice Benchmark Suite
════════════════════════════════════════════════════════════════

Latency Gates
────────────────────────────────────────────────────────────────
  Gate                      p50     p95     p99     Target  Max     Status
  earcon_latency_ms         54      62      71      100     150     PASS
  hook_wall_time_ms         38      44      52      45      100     WARN (p95)
  tts_local_latency_ms      280     340     410     300     500     WARN (p95)
  tts_cloud_latency_ms      85      120     160     100     200     WARN (p95)
  stt_latency_ms            420     580     710     500     1000    WARN (p95)
  startup_latency_ms        22      28      34      25      40      WARN (p95)

Asset Validation
────────────────────────────────────────────────────────────────
  7 themes, 304 files
  0 failures, 2 warnings
  Pass rate: 1.000 (target: 1.000, min: 0.950)             PASS

Crash Safety
────────────────────────────────────────────────────────────────
  200 fuzz inputs, 0 crashes
  Crash rate: 0.000 (target: 0.000, max: 0.000)            PASS

Memory
────────────────────────────────────────────────────────────────
  Peak RSS: 28.4 MB (target: 30 MB, max: 50 MB)            PASS

Concurrent Playback
────────────────────────────────────────────────────────────────
  5 simultaneous streams: all exit 0                        PASS

════════════════════════════════════════════════════════════════
  Overall: 6 PASS, 5 WARN, 0 FAIL
  Saved to: ~/.claude/local/voice/benchmarks/2026-03-26.json
```

### 4.3 Machine-Readable Output

Every run writes a JSON file to `~/.claude/local/voice/benchmarks/{YYYY-MM-DD}T{HH-MM-SS}.json`:

```json
{
  "timestamp": "2026-03-26T14:30:00Z",
  "version": "0.1.0",
  "system": {
    "hostname": "legion",
    "cpu": "i7-13700F",
    "audio_backend": "pw-play",
    "pipewire_version": "1.2.7",
    "python_version": "3.13.1",
    "uv_version": "0.7.2"
  },
  "gates": {
    "earcon_latency_ms": {
      "p50": 54,
      "p95": 62,
      "p99": 71,
      "target": 100,
      "max": 150,
      "status": "PASS"
    }
  },
  "asset_validation": {
    "themes": 7,
    "files": 304,
    "failures": 0,
    "warnings": 2,
    "pass_rate": 1.0
  },
  "fuzz": {
    "inputs": 200,
    "crashes": 0,
    "crash_rate": 0.0
  },
  "memory": {
    "peak_rss_mb": 28.4
  },
  "concurrent": {
    "streams": 5,
    "all_passed": true
  }
}
```

### 4.4 Comparison Mode

`--compare` loads the previous benchmark result and highlights deltas:

```
Comparison: 2026-03-26 vs 2026-03-25
────────────────────────────────────────────────────────────────
  earcon_latency_ms (p95)   62ms → 68ms   (+10%)   ⚠ REGRESSION
  hook_wall_time_ms (p95)   44ms → 42ms   (-5%)    OK
  memory_peak_rss_mb        28.4 → 27.9   (-2%)    OK
```

A regression is flagged when any metric worsens by more than 10% from the previous run. A regression exceeding 20% is flagged as a warning in the output.

---

## 5. Test Categories

### 5a. Unit Tests

Test each module in isolation. No audio output required. No PipeWire dependency. These run in under 5 seconds total.

**`lib/audio.py`**:
- `detect_backend()` returns a valid `(path, name)` tuple on this system.
- `detect_backend()` returns `("", "none")` when all backends are absent (mock `shutil.which` to return `None`).
- `play_sound()` with a mock backend calls `subprocess.Popen` with the correct arguments.
- `play_sound()` returns `None` when backend is `"none"` (no crash, no error).
- Volume calculation: master * event * theme multiplied correctly, clamped to 0.0-1.0.
- Volume calculation: zero master volume produces `--volume=0.0` argument.
- Debounce: rapid calls within debounce window return without spawning a process.
- Process tracking: `_track_process()` adds PID to active set, removes on completion.

**`lib/theme.py`**:
- Theme loading from a valid `theme.json` returns a populated Theme object.
- Theme loading from a missing file raises an appropriate error.
- Inheritance: a child theme inherits parent sounds when not overridden.
- Variant selection: `select_variant()` returns a file from the correct event directory.
- Variant selection: with history tracking enabled, does not repeat the same variant consecutively.
- Content-aware matching: error events map to error sounds, task completion maps to success sounds.
- Theme hot-swap: changing the active theme name causes the next `resolve_sound()` to load the new theme.

**`lib/router.py`**:
- Event routing: each of the 6 registered hook event types maps to the correct semantic token.
- Config checking: muted events are silently dropped (return early, no playback).
- Config checking: disabled plugin produces no output on any event.
- Unknown event types are logged and dropped without crash.
- Rate limiting: events arriving faster than the configured minimum interval are dropped.

**`lib/gamification.py`**:
- XP calculation: correct XP awarded per event type.
- Level formula: XP thresholds produce the correct level number.
- Achievement triggers: specific conditions (e.g., 100 commits) fire the achievement callback.
- State persistence: XP and level survive across invocations via `state.db`.

**`lib/identity.py`**:
- 4-layer resolution: persona, role, theme, default — each layer falls through correctly.
- Persona mapping: a configured persona name resolves to the correct voice/sound profile.
- Missing persona: falls through to role layer without error.

**`lib/state.py`**:
- Atomic read: reads the full state file without partial data.
- Atomic write: writes to a temp file and renames (no partial writes on crash).
- `fcntl` locking: concurrent reads do not block each other.
- `fcntl` locking: concurrent writes serialize correctly (no data corruption).
- State file creation: if the state file does not exist, it is created with defaults.

**`lib/tts.py`**:
- Backend selection: local backend chosen when no API key configured.
- Backend selection: ElevenLabs chosen when API key is present and cloud is enabled.
- Caching: identical text with identical voice settings returns cached audio file path.
- Caching: different voice settings for the same text produce separate cache entries.
- Cache eviction: oldest entries removed when cache exceeds configured size limit.
- Routing: `speak()` dispatches to the correct backend based on configuration.

**`lib/stt.py`**:
- Preprocessing: audio resampled to the model's expected sample rate.
- VAD thresholds: silence below threshold does not trigger transcription.
- VAD thresholds: speech above threshold does trigger transcription.
- Model loading: Whisper model loads without error on first call.
- Model loading: subsequent calls reuse the loaded model (no re-initialization).

### 5b. Integration Tests

End-to-end flows that exercise the full pipeline. These require PipeWire running and an audio backend available. They produce audible output (or can be run with `--volume=0.0` for CI).

**Hook-to-playback pipeline** (for each registered event):
- Feed a valid JSON payload on stdin to the hook handler.
- Verify the hook exits with code 0.
- Verify `subprocess.Popen` was called with the correct sound file path.
- Verify the wall time is within the `hook_wall_time_ms` gate.

**Hook-to-TTS pipeline**:
- Feed a `SubagentStop` event with a summary field.
- Verify TTS is invoked with the summary text.
- Verify audio playback is triggered with the TTS output file.

**Theme hot-swap**:
- Set active theme to `starcraft`.
- Fire an event. Verify the sound file comes from `assets/themes/starcraft/`.
- Change active theme to `minimal` via config write.
- Fire the same event. Verify the sound file now comes from `assets/themes/minimal/`.
- Verify no crash, no stale cache.

**Rapid event sequence**:
- Fire 20 events in 500ms (40 events/second).
- Verify no crashes (all hooks exit 0).
- Verify debounce logic drops excess events (not all 20 should trigger playback).
- Verify the events that do play use correct sound files.

**Configuration reload**:
- Modify `config.yaml` while events are firing.
- Verify the next event picks up the new configuration.
- Verify no crash from partial config read.

### 5c. Performance Tests

These measure quantitative metrics against the gates defined in Section 3.

**Latency profiling**:
- Time 100 hook invocations with valid payloads.
- Compute p50, p95, p99 for each latency metric.
- Compare against `gates.json` thresholds.
- Record the audio backend used and PipeWire version for reproducibility.

**Concurrent playback**:
- Fire 5 hooks simultaneously (parallel processes).
- Verify all 5 `pw-play` processes exit with code 0.
- Verify PipeWire mixes all 5 streams (no EPIPE, no buffer underrun errors in PipeWire log).
- This extends the 3-stream test from spec 05 Section 15 to 5 streams.

**Memory profiling**:
- Run 100 hook invocations in sequence.
- Read `/proc/self/status` VmHWM after each invocation.
- Verify peak RSS stays below the `memory_peak_rss_mb` gate.
- Check for memory leaks: RSS at invocation 100 should not be significantly higher than at invocation 10.

**Startup timing**:
- Measure bare `uv run python -c "pass"` startup time (baseline).
- Measure `uv run python -c "import lib.audio"` startup time (with imports).
- The delta is the module import cost. Verify it stays below 15ms.
- Repeat 50 times, report p50 and p95.

### 5d. Fuzz Tests

The hook handler must exit 0 and print valid JSON (`{}` at minimum) regardless of input. A non-zero exit code or a crash is a hard failure.

**Fuzz input corpus**:

| Input | Description | Expected |
|-------|-------------|----------|
| Empty stdin | Zero bytes on stdin | Exit 0, print `{}` |
| Empty JSON object | `{}` | Exit 0, print `{}` |
| Invalid JSON | `{not json at all` | Exit 0, print `{}` |
| Valid JSON, missing `type` | `{"data": "test"}` | Exit 0, print `{}` |
| Valid JSON, unknown `type` | `{"type": "FakeEvent"}` | Exit 0, print `{}` |
| Valid JSON, null fields | `{"type": null, "data": null}` | Exit 0, print `{}` |
| Extremely large payload | 1MB of valid JSON | Exit 0, print `{}` |
| Binary garbage | 1KB of random bytes | Exit 0, print `{}` |
| UTF-8 edge cases | Emoji, CJK, RTL, zero-width chars in fields | Exit 0, print `{}` |
| Nested depth bomb | 100 levels of nested objects | Exit 0, print `{}` |
| Null bytes in string | `{"type": "Test\x00Event"}` | Exit 0, print `{}` |
| Newlines in fields | `{"type": "Test\nEvent"}` | Exit 0, print `{}` |
| Very long string field | 100KB string in `type` field | Exit 0, print `{}` |

**Fuzz execution**:
- Run each input case 10 times.
- Capture exit code and stdout for every run.
- Report any non-zero exit or non-JSON stdout as FAIL.
- Total: 130+ fuzz invocations per run.

**Property-based fuzzing** (future):
- Use `hypothesis` to generate arbitrary JSON-like structures.
- Feed each to the hook handler.
- Assert exit 0 and valid JSON output for all inputs.

### 5e. Asset Tests

Validate every audio asset against the quality gates defined in spec 03 Section 9.

**Per-file validation**:
- WAV header: 48000 Hz, 16-bit, 2 channels (exact match).
- Duration within +/-20% of target from theme recipe.
- Peak amplitude: `0.0 < max(abs(signal)) <= 1.0` (no clipping, no silence).
- RMS level: -20 dB to -6 dB.
- File size: earcon <200 KB, ambient loop <6 MB.
- Stereo balance: `abs(rms_left_dB - rms_right_dB) < 6 dB`.
- DC offset: `abs(mean(signal)) < 0.01`.
- Leading silence: <50ms of samples below -60 dB at start.
- Trailing silence: <100ms of samples below -60 dB at end.

**Theme integrity**:
- Every `theme.json` `sounds` entry resolves to at least one existing WAV file.
- No orphaned WAV files (files in the sounds directory with no `theme.json` reference).
- Naming convention compliance: `{event}-{NN}.wav` pattern, zero-padded two-digit variant.
- No duplicate checksums across files within a theme (accidental copies).
- Every theme has at least one variant for every required event type.

**Cross-theme validation**:
- All themes define the same set of semantic event tokens (completeness check).
- Volume normalization: LUFS within 2 dB of -14 LUFS target across all themes.
- No two themes share identical sound files (each theme should have its own sonic identity).

### 5f. Regression Tests

Run after every code change. Compare against the stored baseline.

**Baseline management**:
- The first benchmark run after `gates.json` changes becomes the new baseline.
- Baselines are stored in `~/.claude/local/voice/benchmarks/baseline.json`.
- A new baseline is set explicitly with `uv run scripts/benchmark.py --set-baseline`.

**Regression detection**:
- Compare each metric's p95 against the baseline p95.
- Flag any metric that worsens by more than 10% as a regression.
- Flag any metric that worsens by more than 20% as a critical regression.
- Flag any metric that crosses from PASS to WARN or from WARN to FAIL.

**Regression response**:
- Regressions print a prominent warning with the specific metric, old value, new value, and percentage change.
- In `--ci` mode, critical regressions cause exit code 1.
- Non-critical regressions print warnings but do not fail the build.

---

## 6. Test Fixtures

### 6.1 Hook Payload Fixtures

Directory: `tests/fixtures/payloads/`

One JSON file per registered event type, containing a realistic payload matching what Claude Code actually sends:

| File | Event Type | Key Fields |
|------|-----------|------------|
| `notification.json` | Notification | `type`, `message`, `title` |
| `user-prompt-submit.json` | UserPromptSubmit | `type`, `content` |
| `subagent-stop.json` | SubagentStop | `type`, `summary`, `agent_id` |
| `tool-use.json` | ToolUse | `type`, `tool_name`, `arguments` |
| `session-start.json` | (synthetic) | Minimal payload for session start event |
| `session-end.json` | (synthetic) | Minimal payload for session end event |

Each fixture includes a `_test_meta` key (ignored by the hook handler) documenting what the fixture tests and what the expected behavior is.

### 6.2 Audio Fixtures

Directory: `tests/fixtures/audio/`

| File | Purpose |
|------|---------|
| `test-tone-440hz.wav` | 440 Hz sine wave, 200ms, 48kHz 16-bit stereo. Generated by `generate_test_tone()` from spec 05. Used for backend detection and latency measurement. |
| `silence.wav` | 200ms of digital silence. Used to test peak amplitude gate failure detection. |
| `clipped.wav` | Intentionally clipped signal (peak > 1.0 before normalization). Used to test clipping gate. |
| `dc-offset.wav` | Signal with DC offset > 0.01. Used to test DC offset gate. |
| `wrong-format.wav` | 44100 Hz, 8-bit, mono. Used to test format gate rejection. |

These fixtures are generated once by a setup script (`tests/generate_fixtures.py`) and committed to the repo. Total size: <100 KB.

### 6.3 Mock Backends

Directory: `tests/mocks/`

| File | Purpose |
|------|---------|
| `mock-pw-play` | Shell script that logs its arguments to a temp file and exits 0. Used to test playback without audio output. |
| `mock-pw-play-fail` | Shell script that exits 1. Used to test error handling. |
| `mock-pw-play-hang` | Shell script that sleeps 10 seconds. Used to test timeout handling. |
| `mock-pw-play-slow` | Shell script that sleeps 200ms then exits 0. Used to test latency gates with artificial delay. |

Mocks are injected by prepending `tests/mocks/` to `$PATH` during unit tests, so `shutil.which("pw-play")` finds the mock instead of the real binary.

---

## 7. CI / Pre-commit Integration

### 7.1 Pre-commit Hook (fast path)

Runs on every `git commit` within the claude-voice plugin directory. Must complete in under 5 seconds.

Scope:
- Asset validation: `generate_sounds.py --validate` on any changed WAV files.
- Theme integrity: verify `theme.json` references for any changed theme.
- Naming convention: verify new files match `{event}-{NN}.wav` pattern.
- JSON lint: validate `gates.json`, all `theme.json` files, all fixture files.

Implementation: a `pre-commit` script in the plugin root that detects changed files and runs the appropriate subset of checks.

### 7.2 On-demand Benchmark (full path)

Runs manually or triggered by major changes. Takes ~60 seconds.

Scope:
- All latency tests (100 iterations each).
- Full fuzz suite (130+ inputs).
- Full asset validation (all themes, all files).
- Memory profiling (100 iterations).
- Concurrent playback stress test.
- Regression comparison against baseline.

Triggered by:
- `uv run scripts/benchmark.py` (manual).
- Before any release tag.
- After any change to `lib/audio.py`, `lib/router.py`, or `lib/theme.py`.

### 7.3 Weekly Deep Fuzz (extended path)

A longer fuzz run using `hypothesis` property-based testing. Generates 10,000+ random payloads. Catches edge cases that the fixed corpus misses.

Triggered by:
- `uv run scripts/benchmark.py --fuzz --extended` (manual).
- Potentially automated via `claude-rhythms` weekly schedule (see Open Questions).

---

## 8. Health Monitoring (Production)

### 8.1 Heartbeat

File: `~/.claude/local/health/voice-heartbeat`

Updated by the hook handler on every successful invocation with the current Unix timestamp:

```
1711461000
```

A stale heartbeat (>24 hours old) indicates the voice system has not been invoked recently. This could mean Claude Code was not used, the hook is not registered, or the hook is crashing silently.

### 8.2 Integration with /status

The `/status` command checks voice health by reading the heartbeat file:

```
Voice: OK (last sound 2m ago, p95 earcon latency 62ms)
Voice: STALE (no sound in 26h — check hook registration)
Voice: MISSING (no heartbeat file — voice system never initialized)
```

### 8.3 Per-Event Latency Tracking

The hook handler records timing data in `~/.claude/local/voice/state.db` (SQLite):

```sql
CREATE TABLE IF NOT EXISTS latency_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    latency_ms REAL NOT NULL,
    backend TEXT NOT NULL,
    theme TEXT NOT NULL,
    sound_file TEXT
);
```

This table grows over time and supports queries like:
- p95 latency per event type over the last 7 days.
- Latency trend over time (detecting gradual degradation).
- Backend-specific latency distribution.

The table is pruned to the most recent 10,000 entries on each write to prevent unbounded growth.

### 8.4 Alert Conditions

| Condition | Severity | Detection | Response |
|-----------|----------|-----------|----------|
| Heartbeat stale >24h | Warning | `/status` check | Inform user, suggest `uv run scripts/benchmark.py --latency` |
| p95 earcon latency >150ms (rolling 100 events) | Error | Query `latency_log` | Flag regression, suggest investigation |
| p95 earcon latency >120ms (rolling 100 events) | Warning | Query `latency_log` | Note degradation trend |
| Crash detected (non-zero exit in log) | Critical | Hook wrapper captures exit code | Immediate investigation |
| Asset validation failure after generation | Error | `generate_sounds.py --validate` | Block the asset from being committed |

---

## 9. Reporting

### 9.1 Benchmark History

All benchmark results are stored in `~/.claude/local/voice/benchmarks/`:

```
~/.claude/local/voice/benchmarks/
  baseline.json                          # Current baseline for regression detection
  2026-03-26T14-30-00.json              # Individual run results
  2026-03-25T09-15-22.json
  2026-03-24T16-45-11.json
```

### 9.2 HTML Report

`uv run scripts/benchmark.py --report` generates `~/.claude/local/voice/benchmarks/report.html`:

- Summary table of latest run vs. gates.
- Historical trend chart for each latency metric (last 30 runs).
- Asset validation summary with per-theme breakdown.
- Fuzz test results with any failure details.
- System info (CPU, audio backend, PipeWire version) for reproducibility.

The HTML report uses inline CSS and inline SVG charts (no external dependencies). It can be opened directly in a browser.

### 9.3 Alert on Regression

When `--compare` detects a regression exceeding 20%, the benchmark output includes:

```
WARNING: Regression detected
  earcon_latency_ms p95: 62ms → 85ms (+37%)
  This exceeds the 20% regression threshold.
  Investigate recent changes to lib/audio.py or lib/router.py.
  Run: git log --oneline -10 -- lib/audio.py lib/router.py
```

In `--ci` mode, this also sets exit code 1, blocking the pre-commit or CI pipeline.

---

## 10. Open Questions

1. **Automated scheduling**: Should benchmarks be automated via `claude-rhythms` (nightly or weekly)? A nightly latency check would catch gradual degradation from system updates (PipeWire version bumps, kernel changes). The overhead is ~60 seconds of CPU time and produces negligible audio output. Recommendation: yes, weekly via rhythms, nightly is excessive.

2. **Audio quality analysis**: Should we add frequency-domain validation (FFT analysis to verify synthesized tones match their target frequencies, SNR measurement to catch noise floor issues)? This would catch subtle synthesis bugs that pass the current amplitude/RMS gates but produce wrong-sounding output. The cost is adding `numpy` or `scipy` as a test dependency. Recommendation: yes, for synthesized tones where the target frequency is known. Defer for sampled/imported sounds where there is no frequency target.

3. **Output device profiling**: Should we test on different output paths (HDMI vs headphones vs Bluetooth)? HDMI adds ~0ms (direct PCM path), USB headphones add ~5ms (USB audio class), Bluetooth adds 100-200ms (A2DP codec latency — documented in spec 05 Section 16). The benchmark currently measures only the software pipeline, not the hardware output path. Recommendation: document the hardware latency additions as known constants, do not attempt to measure them automatically (requires human listening to verify).

4. **Theme-specific latency targets**: Should different themes have different latency budgets? A `minimal` theme with shorter, simpler sounds might have tighter latency targets than a `starcraft` theme with richer sounds. Current design: all themes share the same gates. Recommendation: keep uniform gates. The latency is dominated by the pipeline (Popen, PipeWire connection), not by the sound file characteristics. File size differences (10KB vs 100KB) add <1ms of read time on NVMe.

5. **Hypothesis integration**: When should property-based fuzzing with `hypothesis` be introduced? It adds a test dependency and requires writing strategies for generating Claude Code hook payloads. Recommendation: introduce when the hook handler is stable (after the first 3 themes are implemented and the hook is processing real events daily). Until then, the fixed fuzz corpus is sufficient.
