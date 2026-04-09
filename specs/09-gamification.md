---
title: "Gamification — XP System, Achievements, Levels & Sound Escalation"
spec: "09"
status: draft
created: 2026-03-26
author: matt
tags: [claude-voice, gamification, xp, achievements, levels]
---

# Spec 09 — Gamification

## 1. Overview

Gamification transforms routine development into a rewarding game loop. XP accrues from productive actions (task completions, commits, error recoveries), levels unlock via a square-root curve that rewards early progress while never capping out, achievements mark milestones and rare events, and sounds escalate in intensity and richness as the player progresses. The entire system is stored in local SQLite (WAL mode), visualized via claude-tmux statusline integration, and entirely optional behind a config toggle (`gamification.enabled: true`).

The design follows three principles drawn from the research survey (`08-gamification-dev-tools.md`):

1. **Celebrate, don't obligate.** XP only goes up. No HP loss, no streak-break punishment, no anxiety mechanics. This is a party horn, not a report card.
2. **Reward outcomes, not inputs.** XP comes from completed tasks, resolved errors, and committed code — not from prompts submitted or tokens generated.
3. **Sound variety prevents habituation.** Higher levels unlock richer, more dramatic sound variants. The reward for leveling up is that your environment sounds better.

Integration point: the hook handler (`hooks/voice_event.py`) calls `_update_gamification()` as step 11 of every event cycle, after sound playback, as defined in `specs/04-hook-architecture.md` section 13. Gamification failures are non-critical — they skip silently and never block Claude Code.

---

## 2. XP System

### 2.1 Level Formula

```
level = floor(k * sqrt(XP))
```

Where `k` is a tuning constant (default: `0.15`). This is derived from Code::Stats' proven formula. The square-root curve gives fast early progression that slows naturally at high XP without ever plateauing.

Properties:
- Level 1 reachable in the first session (just a few task completions)
- Level 5 reachable in roughly a week of regular use
- Level 20 requires sustained engagement over months
- No hard cap — the curve asymptotes but never stops

### 2.2 XP Awards Table

Every event that earns XP, the hook source, and the conditions:

| Event | XP | Condition | Hook Source | Sound Token |
|-------|-----|-----------|-------------|-------------|
| Task completion | 10 | Every Stop event with `task_complete` sound | `Stop` | `task_complete` |
| Git commit | 50 | Content-aware: commit message detected in `last_assistant_message` | `Stop` | `commit` |
| Agent completion | 20 | Subagent finished and returned results | `SubagentStop` | `agent_return` |
| Error resolved | 30 | `task_complete` fires when previous sound was `error` (stateful) | `Stop` (stateful) | `task_complete` (bonus) |
| Session participation | 5 | Per session, awarded once at end | `SessionEnd` | `session_end` |
| Research report | 100 | File written to `~/.claude/local/research/` detected in assistant output | `Stop` (content-aware) | `task_complete` (bonus) |
| First theme change | 25 | One-time achievement trigger on config change | Config event | achievement trigger |
| PR created | 40 | Content-aware: PR URL detected in `last_assistant_message` | `Stop` (content-aware) | `commit` variant |
| Test suite pass | 35 | Content-aware: "All tests passed" or similar in output | `Stop` (content-aware) | `task_complete` (bonus) |
| Multi-tool task | 15 | Stop event where session state shows 3+ tool uses since last Stop | `Stop` (stateful) | `task_complete` |

### 2.3 XP Design Rules

- **XP decay**: None. XP only goes up. Games don't take away progress.
- **Daily/weekly caps**: None. Reward productive marathons.
- **Error penalty**: None. Errors earn 0 XP but are never punished.
- **Gaming mitigation**: XP is tied to outcomes (Stop events with successful completions), not inputs (prompts submitted). Rapid-fire trivial prompts produce diminishing sound tokens via the content-aware router.
- **Streak multipliers**: Applied on top of base XP (see section 6).

---

## 3. Level System

### 3.1 Level Curve Table

With `k = 0.15`:

| Level | XP Required | Cumulative Effort (approx) |
|-------|------------|---------------------------|
| 1 | 45 | ~3 task completions + 1 session |
| 2 | 178 | ~1 commit + handful of tasks |
| 3 | 400 | A full working session |
| 4 | 712 | 2-3 productive sessions |
| 5 | 1,112 | ~1 week regular use |
| 6 | 1,600 | |
| 7 | 2,178 | |
| 8 | 2,845 | |
| 9 | 3,600 | |
| 10 | 4,445 | ~1 month regular use |
| 11 | 5,378 | |
| 12 | 6,400 | |
| 13 | 7,512 | |
| 14 | 8,712 | |
| 15 | 10,000 | ~2-3 months |
| 16 | 11,378 | |
| 17 | 12,845 | |
| 18 | 14,400 | |
| 19 | 16,045 | |
| 20 | 17,778 | ~6 months sustained use |

Formula for table: `XP = (level / k)^2 = (level / 0.15)^2`

### 3.2 Level-Up Mechanics

- **Level-up sound**: A special fanfare that plays on level transition, distinct from all regular earcons. Duration 3-5 seconds (rare event, earned celebration). Resolved via theme's `level_up` sound token.
- **Level-up notification**: On the next `SessionStart` hook after a level-up, inject context via `hookSpecificOutput.additionalContext`: `[voice] Level up! You are now Level {N} ({title})`.
- **Level-up log entry**: Written to `xp_log` table with `level_before` and `level_after` columns for history.

### 3.3 Sound Escalation by Level

Higher levels unlock more dramatic sound variants for all events:

| Level Range | Tier | Sound Character |
|-------------|------|-----------------|
| 1-5 | Base | Simple, short, clean tones. Single-instrument. <500ms for frequent events. |
| 6-10 | Enhanced | Richer harmonics, slightly longer. Layered with subtle reverb. Warmer timbre. |
| 11-15 | Premium | Multi-layered, dramatic. Chord progressions. Full frequency range. |
| 16-20 | Epic | Orchestral/cinematic quality. Rich stereo field. The sounds you hear in trailers. |

Implementation: `theme.json` specifies variant tiers per sound token:

```json
{
  "sounds": {
    "task_complete": {
      "base": "sounds/task_complete/base/",
      "enhanced": "sounds/task_complete/enhanced/",
      "premium": "sounds/task_complete/premium/",
      "epic": "sounds/task_complete/epic/"
    }
  }
}
```

The gamification engine resolves the current tier from the player's level, then the sound router picks a random variant from that tier's directory. If a tier directory is missing or empty, fall back to the highest available tier below it.

Themes that don't define tier variants simply use their flat sound directory at all levels — the escalation is opt-in per theme.

### 3.4 Level Titles

Optional flavor text. Displayed in tmux statusline and level-up notifications:

| Level | Title |
|-------|-------|
| 1 | Recruit |
| 2 | Initiate |
| 3 | Apprentice |
| 4 | Operative |
| 5 | Specialist |
| 6 | Veteran |
| 7 | Expert |
| 8 | Elite |
| 9 | Vanguard |
| 10 | Commander |
| 11 | Strategist |
| 12 | Warden |
| 13 | Sentinel |
| 14 | Champion |
| 15 | Admiral |
| 16 | Arbiter |
| 17 | Overseer |
| 18 | Sovereign |
| 19 | Mythic |
| 20 | Legend |

Titles are cosmetic. They have no gameplay effect.

---

## 4. Achievement System

Achievements are one-time unlockable milestones. Each has a trigger condition, an XP bonus (awarded once on unlock), a distinct sound, and a rarity class that determines how common the achievement is across the user population.

### 4.1 Achievement Categories

- **First Events** — Awarded the first time something happens. Easy to earn, designed to teach the system.
- **Streaks** — Awarded for sustained consecutive activity.
- **Milestones** — Awarded for cumulative totals reaching thresholds.
- **Rare Events** — Awarded for unusual or exceptional circumstances.
- **Meta** — Awarded for interacting with the voice system itself.

### 4.2 Achievement Unlock Sound

Achievement unlocks play a distinct sound (`special-achievement.wav` or theme-specific `achievement` token) that is separate from all regular earcons. This sound should be instantly recognizable as "something special happened" — longer (2-3 seconds), richer, with a rising resolution. It fires after the regular event sound, not instead of it.

### 4.3 Full Achievement Table

| # | Achievement | Slug | Category | Trigger | XP Bonus | Rarity |
|---|-------------|------|----------|---------|----------|--------|
| 1 | First Blood | `first_blood` | First Events | First error resolved (task_complete after error) | 50 | Common |
| 2 | Hello World | `hello_world` | First Events | First session completed | 25 | Common |
| 3 | First Commit | `first_commit` | First Events | First git commit detected | 50 | Common |
| 4 | Delegation | `delegation` | First Events | First subagent deployed | 30 | Common |
| 5 | Sound Check | `sound_check` | Meta | Changed audio theme for the first time | 25 | Common |
| 6 | Night Owl | `night_owl` | Rare Events | Session active after midnight (00:00-04:00) | 25 | Common |
| 7 | Early Bird | `early_bird` | Rare Events | Session active before 06:00 | 25 | Common |
| 8 | Hat Trick | `hat_trick` | Streaks | 3 commits in one session | 75 | Uncommon |
| 9 | Marathon | `marathon` | Rare Events | Single session longer than 2 hours | 100 | Uncommon |
| 10 | Sprint | `sprint` | Rare Events | 5 task completions within 10 minutes | 60 | Uncommon |
| 11 | Bug Squasher | `bug_squasher` | Milestones | 10 errors resolved lifetime | 100 | Uncommon |
| 12 | Centurion | `centurion` | Milestones | 100 tasks completed lifetime | 150 | Uncommon |
| 13 | Fleet Admiral | `fleet_admiral` | Rare Events | 10 subagents deployed in one session | 150 | Rare |
| 14 | The Architect | `the_architect` | Milestones | 50 commits lifetime | 200 | Rare |
| 15 | Unstoppable | `unstoppable` | Streaks | 10 consecutive task completions without error | 100 | Uncommon |
| 16 | Deep Work | `deep_work` | Rare Events | Session with 30+ minutes of continuous activity (no gaps >5 min) | 125 | Rare |
| 17 | Combo Master | `combo_master` | Rare Events | First 5x combo (5 productive Stops within combo window) | 75 | Uncommon |
| 18 | Seven Day Streak | `streak_7` | Streaks | 7 consecutive days with at least one session | 100 | Uncommon |
| 19 | Thirty Day Streak | `streak_30` | Streaks | 30 consecutive days with sessions | 300 | Rare |
| 20 | Hundred Day Streak | `streak_100` | Streaks | 100 consecutive days | 500 | Epic |
| 21 | Polyglot | `polyglot` | Milestones | Commits touching 5+ different file extensions in one session | 75 | Uncommon |
| 22 | Researcher | `researcher` | Milestones | 10 research reports written lifetime | 150 | Rare |
| 23 | The Fixer | `the_fixer` | Milestones | 50 errors resolved lifetime | 200 | Rare |
| 24 | Thousand Club | `thousand_club` | Milestones | 1,000 tasks completed lifetime | 500 | Epic |
| 25 | Level 10 | `level_10` | Milestones | Reached level 10 | 200 | Rare |
| 26 | Level 20 | `level_20` | Milestones | Reached level 20 | 500 | Epic |
| 27 | Weekend Warrior | `weekend_warrior` | Rare Events | Session on Saturday or Sunday | 25 | Common |
| 28 | Full Moon | `full_moon` | Rare Events | Session during a full moon (calendar check) | 50 | Rare |
| 29 | Speed Demon | `speed_demon` | Rare Events | Task completed in under 5 seconds (from prompt to Stop) | 40 | Uncommon |
| 30 | The Librarian | `the_librarian` | Milestones | 100 files read in one session | 75 | Uncommon |

### 4.4 Achievement Persistence

Achievements are stored in SQLite and never lost. The `achievements` table records the exact timestamp and session where each was unlocked. There is no mechanism to revoke an achievement. Resetting requires deleting `state.db`.

### 4.5 Achievement Rarity Classes

| Rarity | Color (tmux) | Estimated Unlock Rate | Sound Variant |
|--------|-------------|----------------------|---------------|
| Common | White | >50% of active users within first week | Short chime |
| Uncommon | Green | 20-50%, within first month | Richer chime with harmony |
| Rare | Blue | 5-20%, requires dedicated effort | Full fanfare, 2 seconds |
| Epic | Purple | <5%, requires sustained long-term engagement | Extended fanfare, 3+ seconds |

---

## 5. Sound Escalation

### 5.1 Tier Resolution

The gamification engine exposes a function `get_sound_tier(level: int) -> str` that maps the current level to a tier name:

```python
def get_sound_tier(level: int) -> str:
    if level <= 5:
        return "base"
    elif level <= 10:
        return "enhanced"
    elif level <= 15:
        return "premium"
    else:
        return "epic"
```

The sound router calls this before variant selection. The tier determines which subdirectory to pull WAV files from.

### 5.2 Combo System

Rapid productive actions trigger a "combo" modifier that makes sounds more energetic:

- **Combo window**: 300 seconds (5 minutes), configurable via `gamification.combo_window_seconds`
- **Combo counter**: Increments on each productive Stop event (sound tokens: `task_complete`, `commit`, `agent_return`) within the window. Resets when the window expires without activity.
- **Combo tiers**:

| Combo Count | Modifier | Effect |
|-------------|----------|--------|
| 1 | None | Normal sound |
| 2 | x2 | Slightly faster tempo variant (if available) |
| 3 | x3 | +5 bonus XP per event |
| 4 | x4 | +10 bonus XP per event |
| 5+ | x5 (max) | +15 bonus XP per event, "combo_max" sound overlay |

- **Combo break**: When the combo window expires, the counter silently resets. No penalty sound, no XP loss. The combo is a bonus, not an expectation.
- **Combo state**: Stored in session state (memory only, not persisted to SQLite). Combos don't survive session boundaries.
- **Combo sound**: At 3+ combo, a brief ascending pitch overlay plays alongside the regular event sound (like a fighting game hit counter). At 5+, a distinct "combo_max" sound fires once.

### 5.3 Theme Integration

Themes opt into sound escalation by providing tiered directories. A minimal theme can ignore escalation entirely — the sound router falls back gracefully:

```
assets/themes/starcraft/sounds/task_complete/
    base/          # 3-7 WAV variants (required)
    enhanced/      # 3-7 WAV variants (optional, falls back to base)
    premium/       # 3-7 WAV variants (optional, falls back to enhanced)
    epic/          # 3-7 WAV variants (optional, falls back to premium)
```

Fallback chain: `epic -> premium -> enhanced -> base`. If only `base/` exists, all levels hear the same sounds.

---

## 6. Streak Tracking

### 6.1 Streak Types

| Streak Type | Increment Condition | Reset Condition |
|-------------|-------------------|-----------------|
| Daily | At least one session in a calendar day | A calendar day passes with no session |
| Session | Consecutive sessions with zero errors | A session where an error sound fires |
| Commit | Consecutive sessions containing at least one git commit | A session that ends without any commit |

### 6.2 Streak XP Multiplier

Active streaks apply a multiplier to all XP earned:

| Streak Length | Multiplier |
|---------------|------------|
| 1-2 | 1.0x (no bonus) |
| 3-6 | 1.1x |
| 7-13 | 1.25x |
| 14-29 | 1.35x |
| 30+ | 1.5x |

The highest active streak multiplier applies (they don't stack across streak types). If the user has a 10-day daily streak (1.25x) and a 4-session session streak (1.1x), the effective multiplier is 1.25x.

### 6.3 Streak Break

- **Sound**: A brief, sympathetic descending tone. Not punishing, not dramatic — more "aww" than "fail." The streak-break sound is optional and can be disabled independently.
- **XP loss**: None. Absolutely zero. The streak counter resets to 0, and the multiplier returns to 1.0x. That's the only consequence.
- **Best record**: The `streaks` table stores `best` alongside `current`. Personal bests are never lost, even when the current streak resets.

### 6.4 Daily Streak Logic

The daily streak increments once per calendar day (local timezone). Multiple sessions in the same day don't increment it further. The check runs at `SessionEnd`:

```python
def update_daily_streak(db: sqlite3.Connection) -> int:
    today = date.today().isoformat()
    row = db.execute("SELECT current, best, last_updated FROM streaks WHERE type='daily'").fetchone()

    if row is None:
        db.execute("INSERT INTO streaks VALUES ('daily', 1, 1, ?)", (today,))
        return 1

    current, best, last_updated = row
    if last_updated == today:
        return current  # Already counted today

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    if last_updated == yesterday:
        new_current = current + 1
    else:
        new_current = 1  # Streak broken

    new_best = max(best, new_current)
    db.execute("UPDATE streaks SET current=?, best=?, last_updated=? WHERE type='daily'",
               (new_current, new_best, today))
    return new_current
```

---

## 7. State Schema

All gamification state lives in `~/.claude/local/voice/state.db` (SQLite, WAL mode for concurrent access from subagent hooks).

```sql
-- XP event log. One row per XP award. Append-only.
CREATE TABLE xp_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now', 'localtime')),
    session_id TEXT,
    event_type TEXT NOT NULL,      -- sound token that triggered award (e.g. 'commit', 'task_complete')
    xp_amount INTEGER NOT NULL,    -- base XP before multipliers
    xp_multiplier REAL DEFAULT 1.0,-- streak multiplier at time of award
    xp_total INTEGER NOT NULL,     -- XP amount * multiplier, rounded
    level_before INTEGER,
    level_after INTEGER,
    combo_count INTEGER DEFAULT 0,
    details TEXT                    -- JSON metadata (achievement slug, streak info, etc.)
);

-- Achievement registry. One row per achievement. Slug is unique.
CREATE TABLE achievements (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    category TEXT NOT NULL,        -- 'first_events', 'streaks', 'milestones', 'rare_events', 'meta'
    rarity TEXT NOT NULL,          -- 'common', 'uncommon', 'rare', 'epic'
    xp_bonus INTEGER DEFAULT 0,
    unlocked_at TEXT,              -- NULL if not yet unlocked
    session_id TEXT                -- session where it was unlocked
);

-- Streak tracker. One row per streak type.
CREATE TABLE streaks (
    type TEXT PRIMARY KEY,         -- 'daily', 'session', 'commit'
    current INTEGER DEFAULT 0,
    best INTEGER DEFAULT 0,
    last_updated TEXT              -- ISO date for daily, session_id for session/commit
);

-- Aggregate stats for fast lookups without scanning xp_log.
CREATE TABLE stats (
    key TEXT PRIMARY KEY,
    value TEXT                     -- JSON-encoded value
);
-- Expected keys:
--   'total_xp'           -> integer
--   'current_level'      -> integer
--   'total_tasks'        -> integer
--   'total_commits'      -> integer
--   'total_errors'       -> integer
--   'total_errors_resolved' -> integer
--   'total_sessions'     -> integer
--   'total_agents'       -> integer
--   'total_research'     -> integer
--   'files_read_session' -> integer (reset per session)
--   'combo_best'         -> integer

-- Index for time-range queries (weekly XP, daily summaries)
CREATE INDEX idx_xp_log_timestamp ON xp_log(timestamp);
CREATE INDEX idx_xp_log_session ON xp_log(session_id);
```

### 7.1 Schema Initialization

On first access, `lib/gamification.py` checks if tables exist and creates them if missing. The achievement table is pre-populated with all 30 achievement definitions (unlocked_at = NULL). This makes querying "which achievements are locked" trivial.

### 7.2 WAL Mode

WAL is essential because multiple concurrent hook processes (main agent + subagents) may write to the same database. WAL allows concurrent reads with one writer without blocking. Set on connection open:

```python
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA busy_timeout=1000")  -- Wait up to 1s for lock
```

### 7.3 Data Integrity

The gamification system is non-critical. If any SQLite operation fails (corrupt DB, locked, disk full), the hook handler catches the exception and continues. Sound still plays. Claude Code is never blocked. The worst case is lost XP for one event.

---

## 8. Core API (`lib/gamification.py`)

```python
from dataclasses import dataclass
from typing import Optional


@dataclass
class XPResult:
    xp_awarded: int          # After multiplier
    xp_total: int            # New lifetime total
    level_before: int
    level_after: int
    level_up: bool           # True if level changed
    combo_count: int
    achievements: list       # Newly unlocked achievements (if any)


@dataclass
class Achievement:
    slug: str
    name: str
    description: str
    category: str
    rarity: str
    xp_bonus: int


K_CONSTANT = 0.15  # Tuning constant for level curve


def award_xp(event: str, session_id: str, details: dict = None) -> XPResult:
    """
    Award XP for a game event. Calculates streak multiplier, updates
    combo counter, checks for achievement triggers, persists to SQLite.

    This is the primary entry point called from _update_gamification()
    in the hook handler.

    Args:
        event: Sound token that triggered the award (e.g., 'commit')
        session_id: Current session ID
        details: Optional metadata dict (file paths, error messages, etc.)

    Returns:
        XPResult with full state after the award.
    """
    ...


def get_level() -> int:
    """Return current level based on total XP."""
    ...


def get_xp() -> int:
    """Return total lifetime XP."""
    ...


def get_sound_tier(level: int = None) -> str:
    """
    Map level to sound tier name.

    Returns one of: 'base', 'enhanced', 'premium', 'epic'
    """
    if level is None:
        level = get_level()
    if level <= 5:
        return "base"
    elif level <= 10:
        return "enhanced"
    elif level <= 15:
        return "premium"
    else:
        return "epic"


def check_achievements(event: str, session_data: dict) -> list[Achievement]:
    """
    Check if any achievements should unlock based on the current event
    and cumulative stats. Returns list of newly unlocked achievements
    (empty list if none).

    Called internally by award_xp(). Can also be called independently
    for stat-check triggers (e.g., level milestones).
    """
    ...


def get_stats() -> dict:
    """
    Return full stats dict from the stats table.

    Keys: total_xp, current_level, total_tasks, total_commits,
    total_errors, total_errors_resolved, total_sessions, total_agents,
    total_research, combo_best
    """
    ...


def get_leaderboard() -> dict:
    """
    Return personal bests and records.

    Returns dict with:
        - level: current level and title
        - xp: total and today's XP
        - streaks: current and best for each type
        - achievements: unlocked count / total count, rarest unlocked
        - combo_best: highest combo reached
        - top_sessions: top 5 sessions by XP earned
    """
    ...


def get_streak_multiplier() -> float:
    """Return current active streak multiplier (highest across all streak types)."""
    ...


def update_combo(session_id: str, event: str) -> int:
    """
    Update combo counter for the session. Returns new combo count.
    Combo resets if time since last productive event exceeds combo_window_seconds.
    Combo state is in-memory (session-scoped), backed by session state file.
    """
    ...
```

---

## 9. Visual Integration (claude-tmux)

The gamification engine exposes state that claude-tmux can read and display in the statusline.

### 9.1 Statusline Format

```
[Lv.12 Strategist] ████████░░ 6,400/7,512 XP | 🔥 14-day streak | x3 combo
```

Components:
- **Level badge**: `[Lv.{N} {Title}]` — always visible when gamification is enabled
- **XP progress bar**: 10-character bar showing progress from current level to next level
- **XP numbers**: `{current_level_xp}/{next_level_xp} XP` — absolute XP toward next level
- **Streak indicator**: Shows the highest active streak with its count. Fire icon for daily, chain icon for session.
- **Combo indicator**: Only shown when combo >= 2. Disappears when combo expires.

### 9.2 State File for tmux

The gamification engine writes a JSON state file after every XP award:

```
~/.claude/local/voice/gamification_state.json
```

```json
{
  "level": 12,
  "title": "Strategist",
  "xp_total": 6400,
  "xp_for_current_level": 5378,
  "xp_for_next_level": 7512,
  "xp_progress_pct": 47.9,
  "streak_type": "daily",
  "streak_current": 14,
  "streak_multiplier": 1.35,
  "combo": 3,
  "last_achievement": "unstoppable",
  "last_achievement_at": "2026-03-26T14:32:00",
  "updated_at": "2026-03-26T14:35:12"
}
```

claude-tmux reads this file (atomic write via tmp+rename, same pattern as session state) and formats the statusline. If the file is missing or stale (>24h), the gamification section is hidden.

### 9.3 Achievement Popup

When an achievement unlocks, the gamification engine writes a transient notification file:

```
~/.claude/local/voice/achievement_popup.json
```

```json
{
  "slug": "unstoppable",
  "name": "Unstoppable",
  "description": "10 consecutive task completions without error",
  "rarity": "uncommon",
  "xp_bonus": 100,
  "expires_at": "2026-03-26T14:32:15"
}
```

claude-tmux checks for this file and displays a popup overlay for 5 seconds (or until expires_at). After display, tmux deletes the file.

### 9.4 Level-Up Animation

On level-up, the tmux statusline briefly flashes (color pulse via tmux `display-message` or `set-option status-style` cycling). The flash lasts 3 seconds. Implementation is a background script triggered by the gamification engine:

```bash
# Pseudocode for level-up flash
for color in gold white gold white normal; do
    tmux set-option -g status-style "bg=$color"
    sleep 0.3
done
tmux set-option -g status-style "$original_style"
```

---

## 10. Configuration

All gamification settings live under the `gamification` key in `~/.claude/local/voice/config.yaml`:

```yaml
gamification:
  enabled: true                    # Master toggle. False = no XP tracking, no achievements, no state writes.
  notifications: true              # Show level-up/achievement notifications in SessionStart context.
  xp_k_constant: 0.15             # Tuning constant for level formula. Higher = faster leveling.
  sound_escalation: true           # Use tiered sound variants based on level.
  combo_window_seconds: 300        # Time window for combo system (seconds).
  combo_enabled: true              # Toggle combo system independently.
  streaks: true                    # Track daily/session/commit streaks.
  streak_break_sound: true         # Play sympathetic sound on streak break.
  tmux_integration: true           # Write gamification_state.json for claude-tmux.
  achievement_popups: true         # Write achievement_popup.json for tmux overlay.
  level_up_flash: true             # Trigger tmux statusline flash on level-up.
```

### 10.1 Config Defaults

When `gamification` key is missing entirely, the system uses:

```python
GAMIFICATION_DEFAULTS = {
    "enabled": False,       # Off by default — opt-in
    "notifications": True,
    "xp_k_constant": 0.15,
    "sound_escalation": True,
    "combo_window_seconds": 300,
    "combo_enabled": True,
    "streaks": True,
    "streak_break_sound": True,
    "tmux_integration": True,
    "achievement_popups": True,
    "level_up_flash": True,
}
```

Gamification is **off by default**. The user must explicitly enable it. Once enabled, all sub-features default to on — the user can then selectively disable components they don't want.

---

## 11. Privacy

- **All data is local.** `state.db` lives in `~/.claude/local/voice/` and never leaves the machine.
- **No telemetry.** No XP data, achievement data, or session stats are transmitted anywhere.
- **No cloud sync.** The gamification state is single-machine. Moving to a new machine starts fresh.
- **Full reset**: `rm ~/.claude/local/voice/state.db` wipes all XP, achievements, streaks, and stats. The schema is recreated on next hook event.
- **Selective reset**: The API could expose `reset_xp()`, `reset_achievements()`, `reset_streaks()` functions, but for v1, `rm state.db` is sufficient.
- **No data in hooks output**: Gamification state is never included in `hookSpecificOutput` sent to Claude Code's context, except the opt-in level notification on SessionStart.

---

## 12. Open Questions

1. **Multiplayer/team XP?** If multiple agents are working (subagents, matrix agents), should they contribute to a shared XP pool or have independent tracking? Current design: all XP goes to one global pool regardless of which agent earned it.

2. **Per-persona XP?** Should each persona (from claude-personas) have its own level and achievement set? This would let different work modes progress independently. Risk: splits attention and slows progression per persona.

3. **Visual achievement badges in tmux?** Could render small Unicode/emoji badges next to the level display for recently unlocked achievements. Adds visual interest but complicates the statusline layout.

4. **Seasonal/weekly challenges?** Time-limited goals like "earn 500 XP this week" or "get 3 commits on a weekend." Adds variety but introduces obligation mechanics that conflict with the "celebrate, don't obligate" principle.

5. **Sound generation at level-up?** Instead of pre-authored tier variants, could the sound synthesis pipeline (`specs/03-sound-synthesis.md`) generate progressively more complex waveforms as level increases — making the sound escalation truly dynamic rather than switching between pre-made tiers.

6. **Achievement sharing?** Export achievement list as markdown or JSON for sharing. Low priority but fun for bragging rights.

7. **XP retroactive backfill?** If the user enables gamification after months of use, should we scan `claude-logging` event history and retroactively award XP? This is technically possible (all events are logged) but adds complexity. Current answer: no, you start from zero when you enable it.

8. **Diminishing returns on repetitive events?** The current design has no caps, but should the 50th commit in a single session still award full XP? The combo system rewards rapid activity, but there's no anti-farming mechanism. Current stance: trust the user.

---

## 13. References

### Internal Specs
- `specs/04-hook-architecture.md` section 13 — Gamification hook points, XP award table, `_update_gamification()` integration
- `specs/02-theme-engine.md` — `theme.json` schema, sound token vocabulary
- `specs/03-sound-synthesis.md` — WAV generation pipeline for sound variants
- `specs/05-audio-playback.md` — `pw-play` fire-and-forget, fallback chain
- `ARCHITECTURE.md` — Gamification Engine component definition

### Research
- `~/.claude/local/research/2026/03/25/voice/08-gamification-dev-tools.md` — Full prior art survey: Code::Stats, Habitica, Duolingo, earcon research, anti-patterns
- Code::Stats level formula: `level = floor(0.025 * sqrt(XP))` — adopted and tuned
- Octalysis framework: Drives 2 (Development) and 3 (Creativity) are primary for developer tools
- Earcon research: 8-12 distinct sounds max per interface, hierarchical families, <500ms for frequent events
- Anti-pattern research: no streak anxiety, no loss aversion, no social shaming
