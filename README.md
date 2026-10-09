# et-ay

[![CI](https://github.com/ChristopherALewis/claude-et-ay/actions/workflows/ci.yml/badge.svg)](https://github.com/ChristopherALewis/claude-et-ay/actions/workflows/ci.yml)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)

et-ay is a Claude Code plugin that puts an estimated time to completion in your status line for whatever Claude is working on, so that when you hand over a prompt and go to make a coffee you have a decent idea whether it will be finished by the time you get back. It learns from your own history rather than guessing from first principles, which means the estimates start rough and get noticeably better after a few dozen prompts.

```text
ETA ~4m left (2-9m) · done ≈14:32 · 1m 20s in · 2/5 tasks · +1 queued
```

It has no dependencies beyond Python 3.9 or later, keeps everything on your machine, never stores the text of your prompts, and sits alongside any status line you already have rather than replacing it.

## What it shows

While Claude is working, the status line shows the time likely to be left with a range around it, the clock time it should finish by, how long the turn has been running, progress through Claude's task list when there is one, and the number of queued messages Claude has picked up mid-turn. The range is the 10th to 90th percentile of comparable past turns, so roughly four turns in five should land inside it once there is enough history.

| Situation | Example |
| --- | --- |
| Working normally | `ETA ~4m left (2-9m) · done ≈14:32 · 1m 20s in` |
| Still learning (fewer than three comparable turns) | `ETA ~2m? left (40s-6m) · ...` |
| Waiting on a permission prompt or a question to you | `ETA paused, waiting for you · 3m 10s in` |
| Running longer than nearly all comparable turns | `ETA running long, maybe 6m more · 18m 02s in` |
| Idle, showing how the last turn went | `ETA last 4m 12s (est 3m)` |

Time spent waiting for you is left out of both the estimate and the history, so a permission prompt you leave sitting over lunch does not teach et-ay that the task took an hour.

## Install

You need Claude Code and Python 3.9 or later on your `PATH` (macOS ships one with the Command Line Tools, which `xcode-select --install` provides).

Inside Claude Code, add the marketplace and install the plugin:

```text
/plugin marketplace add ChristopherALewis/claude-et-ay
/plugin install et-ay@christopheralewis
```

The hooks start recording straight away. To see the estimate, add et-ay to your status line:

```text
/et-ay:setup
```

Setup is needed because Claude Code does not let a plugin set the status line itself. It edits `~/.claude/settings.json` for you after taking a timestamped backup beside it (following a symlink if your settings live in a dotfiles repository, and keeping the file's permissions), and if you already have a status line it keeps it and appends the estimate to it. It also sets `refreshInterval` to one second so the countdown moves on its own, keeping a faster interval if you already had one.

If you prefer to do it by hand, or setup declines because your settings file contains comments, add this to `~/.claude/settings.json` and run `/et-ay:setup` once afterwards so the shim it points to exists:

```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.claude/et-ay/statusline.sh",
    "refreshInterval": 1
  }
}
```

## Commands

| Command | What it does |
| --- | --- |
| `/et-ay:setup [--position after\|before\|line] [--refresh N] [--no-wrap]` | Adds et-ay to your status line, wrapping any existing one |
| `/et-ay:stats [--recent N]` | Shows how long your turns take and how accurate the estimates have been |
| `/et-ay:doctor` | Checks the hooks, status line, pointer files and data directory, and explains anything wrong |
| `/et-ay:config [show \| set KEY VALUE \| unset KEY]` | Shows or changes settings |
| `/et-ay:remove [--purge]` | Restores the status line you had before and, with `--purge`, deletes et-ay's config and shim |

## How the estimate works

When you submit a prompt, et-ay describes it with a few coarse features (length, whether it reads as a question, a task or a slash command, how many files it mentions, whether it contains pasted content) and weights every past completed turn by how similar it looks and how recent it is. Turns in the same project, on the same model and at the same effort level count for more, and a slash command is compared mainly with earlier runs of the same command. The weighted durations form a distribution whose 10th, 50th and 90th percentiles become the range and the central estimate. With little history, a weak prior based on prompt length fills the gap and fades out as data accumulates.

While the turn runs, the remaining time is recalculated every second by asking how much longer comparable turns ran given that this one has already lasted as long as it has. That is why a turn that outlasts most of its peers shows a growing estimate rather than a negative one. If Claude has written a task list, the rate at which it is ticking tasks off is blended in, weighted by how far through the list it is.

[docs/how-it-works.md](docs/how-it-works.md) covers the weighting, the prior and the edge cases in detail.

## Queued prompts

Claude Code lets you type further messages while Claude is busy, and these are what the plugin can and cannot see.

A queued message is invisible to plugins while it sits in the queue. Claude Code hands it to Claude once the current tool calls finish, inside the same turn, and only at that point does a hook fire. et-ay records it as joined to the running turn, adds its own estimate to the time remaining, and shows `+1 queued`. Messages still sitting in the queue when the turn ends start the next turn as normal. In practice this means the estimate jumps when Claude picks up your follow-up rather than when you type it, and there is no way for a plugin to do better until Claude Code exposes the queue.

Interrupting a turn with Esc fires no hook either. et-ay notices when your next prompt arrives under a new prompt id, records the previous turn as interrupted and leaves it out of the training data.

## Configuration

Settings live in `~/.claude/et-ay/config.json` and are easiest to change with `/et-ay:config`, for example `/et-ay:config set segments ["left","elapsed"]`. Every setting has a default, so the file is optional.

| Setting | Default | Meaning |
| --- | --- | --- |
| `segments` | `["left","range","clock","elapsed","tasks","queued"]` | What to show while a turn runs, in order. Also available: `confidence` |
| `label` | `"ETA"` | Text before the segments; set to `""` to drop it |
| `separator` | `" · "` | Between segments |
| `clock_format` | `"%H:%M"` | `strftime` format for the finish time |
| `color` | `true` | ANSI colours; the `NO_COLOR` environment variable always turns them off |
| `ascii` | `false` | Plain ASCII output for terminals that struggle with `·` and `≈` |
| `idle` | `"last"` | `"last"` shows how the previous turn went, `"none"` shows nothing |
| `idle_seconds` | `900` | How long the idle summary stays up; `0` keeps it indefinitely |
| `position` | `"after"` | Where et-ay goes relative to a wrapped status line: `after`, `before` or on its own `line` |
| `wrapped_timeout` | `3.0` | Seconds to wait for your existing status line command |
| `half_life_days` | `45` | How quickly old turns lose influence |
| `history_window` | `3000` | How many recent turns the estimator reads |
| `prior_strength` | `3` | How many turns' worth of weight the cold-start prior carries |
| `stale_minutes` | `120` | A turn idle for longer than this is treated as abandoned |
| `debug` | `false` | Write redacted hook payloads to `debug.jsonl` in the data directory |

## Privacy

Everything stays on your machine, in two places: history and session state in the plugin data directory that Claude Code manages (`~/.claude/plugins/data/`), and config plus the status line shim in `~/.claude/et-ay/`. Nothing is sent anywhere.

Prompt text is never written to disk. The history records a handful of numbers and labels per turn (character and word counts, a coarse kind, the slash command name if there was one, timings, tool and task counts, model and effort) and a short one-way hash of the project path so that turns in the same project can be compared. Debug logging, which is off by default, records only an allowlist of structural fields (ids, event and tool names, timings), reduces prompts, Claude's replies, tool errors and task titles to their length and a hash, and leaves tool inputs, tool outputs and any field it does not recognise out entirely. The tests check that a distinctive prompt never appears anywhere in the data directory.

## Performance

Each hook is a short Python process that reads a small JSON file, updates it and exits, in roughly 50 to 90 milliseconds on a typical laptop even with thousands of turns of history. The prompt and stop hooks run synchronously because their order matters; the tool, task and wait hooks run asynchronously so they never hold Claude up. The status line reads one small state file per refresh and does no estimation of its own beyond conditioning a stored sample.

With `refreshInterval` at one second, a status line you already had is also run every second. If yours does something slow such as `git status` on a large repository, either cache it in your script or run `/et-ay:setup --refresh 2`.

## Uninstalling

Run `/et-ay:remove` first so your previous status line comes back, then uninstall the plugin from `/plugin`, which also deletes the recorded history. If you uninstall without running remove, nothing breaks: the shim notices the plugin has gone (or that Python is no longer available) and falls back to your old status line, or to nothing if you did not have one.

## Troubleshooting

`/et-ay:doctor` is the first port of call, since it checks every moving part and says what to do about anything amiss. Beyond that, a blank status line usually means one of three things: the folder has not been trusted yet (Claude Code runs status line commands only in trusted workspaces), `disableAllHooks` is set in your settings, or no Python 3.9+ interpreter is on the `PATH` that Claude Code sees. If you want to check exactly which events et-ay receives, `/et-ay:config set debug true` starts a redacted event log in the data directory.

## Development

The repository is a Claude Code marketplace with the plugin in [`plugins/et-ay`](plugins/et-ay). The code is plain Python with no dependencies.

```text
plugins/et-ay/
  .claude-plugin/plugin.json   manifest
  hooks/hooks.json             hook registrations
  skills/                      the five slash commands
  scripts/run.sh               interpreter finder used by every hook
  scripts/etay/                tracker, estimator, renderer, settings, reports
tests/                         unit and end-to-end tests (standard library unittest)
docs/how-it-works.md           design notes
```

Run the tests with `python3 -m unittest discover -s tests`, and try a local copy with `claude --plugin-dir ./plugins/et-ay`. `claude plugin validate ./plugins/et-ay` checks the manifest. [CONTRIBUTING.md](CONTRIBUTING.md) has the rest.

## Licence

et-ay is released under the MIT licence, the full text of which is in [LICENSE](LICENSE), and it has no third-party dependencies, so there are no other licences to account for.

et-ay is an independent project and is not affiliated with or endorsed by Anthropic. Claude and Claude Code are trademarks of Anthropic, PBC.
