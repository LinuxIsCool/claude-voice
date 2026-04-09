---
title: "claude-voice — Design Directory"
created: 2026-03-30
updated: 2026-03-30
author: matt
---

# claude-voice Design Directory

Ground truth for the voice system's architecture, state, and evolution.
Everything here describes what IS, not what COULD BE. Aspirational work lives in `specs/` and `plans/`.

## Index

### Core Architecture
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — System overview, component inventory, data flow, integration map
- **[state-machine.md](state-machine.md)** — All state flags, transitions, race conditions, failure modes
- **[volume-pipeline.md](volume-pipeline.md)** — Complete volume computation trace from config to speaker

### System Interactions
- **[interaction-map.md](interaction-map.md)** — How voice connects to every other Legion plugin
- **[daemon-topology.md](daemon-topology.md)** — TTS daemon, STT daemon, queue daemon lifecycle and IPC

### Design Decisions
- **[decisions/](decisions/)** — ADR-style records of key architectural choices
  - `001-spatial-mixer-over-binary-gate.md`
  - `002-file-as-ipc.md`
  - `003-queue-only-tts.md`
  - `004-fail-open-everywhere.md`

### Known Issues
- **[known-issues/](known-issues/)** — Verified bugs from 2026-03-30 research audit
  - `P0-queue-ignores-stt-active.md`
  - `P0-tts-playing-orphan.md`
  - `P1-ambient-state-drift.md`
  - `P1-greeting-bypasses-queue.md`
  - `P2-architecture-md-stale.md`

### Diagrams
- **[diagrams/](diagrams/)** — ASCII and Mermaid diagrams for visual reference

## Relationship to Other Files

| File | Purpose | This directory's relationship |
|------|---------|-------------------------------|
| `CLAUDE.md` | Quick reference for Claude Code sessions | design/ is the source; CLAUDE.md is the summary |
| `ARCHITECTURE.md` (root) | Legacy architecture doc | **SUPERSEDED** by `design/ARCHITECTURE.md` |
| `ROADMAP.md` | Archived Phase 1-7 vision | Historical reference |
| `ROADMAP2.md` | Active development roadmap | References design/ for current state |
| `specs/` | Per-feature specifications | design/ describes the whole; specs describe parts |
| `plans/` | Implementation plans | Plans consume design/ as input |
