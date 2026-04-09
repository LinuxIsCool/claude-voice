---
title: "P1: Ambient State Drift (Live Bug)"
severity: P1
discovered: 2026-03-30
source: "Research report 02 — Audio Pipeline Verification"
status: fixed
file: lib/ambient.py
---

# P1: Ambient State Drift (Live Bug)

## Bug

As of 2026-03-30, `~/.claude/local/voice/ambient-count` = 6 and
`~/.claude/local/voice/ambient.pid` points to a dead process. No ambient
pw-play loop is actually running.

## Cause

SubagentStart increments count. SubagentStop decrements. But if a session
crashes or agents are killed without SubagentStop events firing, the count
drifts upward and never returns to zero.

## Impact

- Ambient loop cannot restart (count > 0, pid exists but dead)
- `ambient.cleanup()` on SessionEnd should fix, but SessionEnd may not fire on crash

## Fix

1. `start_loop()` should reset count to 1 on success (not just increment)
2. `increment_agents()` should validate PID is alive before trusting count
3. SessionStart hook should call `ambient.cleanup()` to reset state
4. Count file should include PID for staleness detection
