---
title: "P2: Root ARCHITECTURE.md Significantly Out of Date"
severity: P2
discovered: 2026-03-30
source: "Research report 05 — Architecture & Best Practices"
status: open
file: ARCHITECTURE.md (root)
---

# P2: Root ARCHITECTURE.md Significantly Out of Date

## Bug

The root `ARCHITECTURE.md` (444 lines, created 2026-03-26) describes a system
that doesn't match the current implementation:

### Wrong file references
- `lib/sound.py` — doesn't exist (functionality is in theme.py + router.py)
- `lib/playback.py` — doesn't exist (is audio.py)
- `lib/gates.py` — doesn't exist (logic is in router.py try/except)
- `lib/identity.py` — doesn't exist (designed in spec 08, never written)
- `lib/rhythms.py` — doesn't exist (designed in spec 10, never written)
- `lib/assets.py` — doesn't exist (designed in spec 11, never written)
- `lib/gamification.py` — doesn't exist (designed in spec 09, never written)

### Wrong spec index
- Lists 14 specs with wrong titles
- Misses specs 15 (spatial mixer) and 18 (queue daemon)
- All specs listed as "planned" — many are implemented

### Describes aspirational features as implemented
- Gamification (XP, levels, achievements) — zero code
- Rhythms integration — zero code
- Identity resolver — zero code
- ElevenLabs TTS — zero code

## Fix

The new `design/ARCHITECTURE.md` supersedes this file. Options:
1. Delete root ARCHITECTURE.md, add redirect note
2. Replace content with pointer to design/
3. Keep as historical artifact with "SUPERSEDED" header
