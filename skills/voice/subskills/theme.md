---
name: theme
description: |
  Switch, preview, or list voice themes.
  Use when user says "theme", "switch theme", "set theme", "list themes", "preview".
---

# Theme Management

## List Available Themes

Show all installed themes:

| Theme | Slug | Description |
|-------|------|-------------|
| Default | `default` | Clean, professional notifications |
| StarCraft | `starcraft` | Digital military. Square waves, radio chirps. |
| Warcraft | `warcraft` | Fantasy organic. War drums, horn brass. |
| Mario | `mario` | Cheerful chiptune. Bouncy and bright. |
| Zelda | `zelda` | Mystical melodic. Harp, ocarina, bells. |
| Smash Bros | `smash` | Competitive punchy. Impacts and arena energy. |
| Kingdom Hearts | `kingdom-hearts` | Orchestral emotional. Piano, choir, strings. |

## Switch Theme

Update `~/.claude/local/voice/config.yaml`:

```bash
# Read current config, update theme field
python3 -c "
import yaml
from pathlib import Path
p = Path.home() / '.claude/local/voice/config.yaml'
cfg = yaml.safe_load(p.read_text()) if p.exists() else {}
cfg['theme'] = 'THEME_SLUG'
p.write_text(yaml.dump(cfg, default_flow_style=False))
print(f'Theme set to: {cfg[\"theme\"]}')
"
```

## Preview Theme

Play the session_start sound from a theme:

```bash
cd ~/.claude/plugins/local/legion-plugins/plugins/claude-voice
pw-play assets/themes/THEME_SLUG/sounds/SESSION_START_VARIANT.wav
```
