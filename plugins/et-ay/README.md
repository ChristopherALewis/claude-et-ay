# et-ay

et-ay shows an estimated time to completion for the prompt Claude is working on, live in your Claude Code status line, and learns from your own history to make the estimate better over time.

Install it from inside Claude Code with:

```text
/plugin install et-ay --marketplace ChristopherALewis/claude-et-ay
```

After installing, run `/et-ay:setup` to add it to your status line (Claude Code does not let plugins do this themselves). Your existing status line, if you have one, is kept and the estimate is appended to it.

| Command | What it does |
| --- | --- |
| `/et-ay:setup` | Adds et-ay to your status line |
| `/et-ay:stats` | Turn lengths and estimate accuracy |
| `/et-ay:doctor` | Health check with fixes for anything wrong |
| `/et-ay:config` | Shows or changes settings |
| `/et-ay:remove` | Restores your previous status line; run it before uninstalling |

Settings are stored in `~/.claude/et-ay/config.json`. Run `/et-ay:config show` to list them with their current values. The main ones are `segments` (which parts to show, from `left`, `range`, `clock`, `elapsed`, `tasks`, `queued` and `confidence`), `label`, `clock_format`, `color`, `ascii`, `idle`, `position` (`after`, `before` or `line` relative to a wrapped status line) and `debug`.

The full documentation, including how the estimate is calculated and what is stored, is at <https://github.com/ChristopherALewis/claude-et-ay>.

Released under the MIT licence (see `LICENSE`). Not affiliated with or endorsed by Anthropic.
