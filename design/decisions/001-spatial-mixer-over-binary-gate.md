---
title: "ADR-001: Spatial Volume Mixer Over Binary Focus Gate"
date: 2026-03-27
status: accepted
author: matt
context: Phase 3.5 design session (Opus 1M marathon, commit f29dfe5)
---

# ADR-001: Spatial Volume Mixer Over Binary Focus Gate

## Context

When multiple Claude instances run in tmux panes, unfocused panes produce TTS
that contaminates STT input in the focused pane. The initial design was a binary
gate: focused pane plays, unfocused panes are silent.

## Decision

Replace the binary gate with a continuous spatial volume mixer. Four states
(focused, same_window, same_session, other_session) each have configurable
volume multipliers. Priority floors let critical events override spatial silencing.

## Rationale

- The binary gate is a degenerate case of the mixer (set all non-focused to 0.0)
- The mixer is SIMPLER to implement — no branching, just multiplication
- More general — handles graduated awareness, not just on/off
- More extensible — new features are new multipliers, not new code paths
- Users tune to taste via config, not code changes

## Consequences

- Volume pipeline is slightly more complex to trace (5 multipliers vs 1 boolean)
- Config has more options (focus_volumes dict vs single boolean)
- But: behavior is always predictable (pure math, no branching logic)

## Origin

Shawn asked one question during the Phase 3.5 session that broke the binary gate
model: "What if I want to hear background panes at lower volume?" The mixer
emerged as the answer.
