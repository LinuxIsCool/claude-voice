---
title: "ADR-003: Queue Only TTS, Not Earcons"
date: 2026-03-27
status: accepted
author: matt
context: Simplification from TypeScript POC (which queued everything)
---

# ADR-003: Queue Only TTS, Not Earcons

## Context

The TypeScript POC queued ALL audio (earcons and TTS). This required tracking
every pw-play process and coordinating sub-second timing.

## Decision

Only TTS speech goes through the voice queue daemon. Earcons play directly via
`audio.py` — fire-and-forget, no scheduling.

## Rationale

- Earcons are 100-800ms — overlap risk is low
- Two earcons overlapping briefly sounds natural (like game sound effects)
- Two TTS voices overlapping is cacophony — MUST be serialized
- Reduces queue daemon complexity from ~600 lines (POC) to ~437 lines
- Earcon latency is critical (150ms budget) — queue adds latency

## Consequences

- Rare earcon overlap during simultaneous hook events (acceptable)
- TTS always serialized with speaker transitions (desired)
- Simpler queue protocol (only handles speech-length audio)
