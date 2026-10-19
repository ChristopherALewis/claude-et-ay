# How et-ay works

This note explains the moving parts in enough detail to change them with confidence. The code is short, and each module's docstring repeats the essentials.

## Events and the turn lifecycle

et-ay listens to Claude Code hook events and keeps one small JSON state file per session in the plugin data directory. The behaviour below was observed directly on Claude Code 2.1.295 by logging every hook payload, and the tests encode it.

| Event | Hook | Mode | What et-ay does |
| --- | --- | --- | --- |
| `SessionStart` | `session-start` | sync | Writes the plugin root and data directory pointers, tidies old session and progress files, exports `ET_AY_PROGRESS` through `CLAUDE_ENV_FILE`, and closes a turn left open by a crash when the session is resumed. Compaction is ignored for turn tracking because it can happen mid-turn. |
| `UserPromptSubmit` | `prompt` | sync | Starts a turn and computes its estimate, or joins a queued message to the running turn (same `prompt_id`), or closes an interrupted turn at its last recorded activity (new `prompt_id` while one is still open). |
| `PostToolUse`, `PostToolUseFailure` | `tool` | async | Counts tools and tool time, records the effort level and ends any wait. Events from subagents do not end a wait, and events arriving after `Stop` (from background agents or shells that outlive the turn) are ignored. |
| `PreToolUse` for `AskUserQuestion` and `ExitPlanMode`, `PermissionRequest` | `wait` | async | Marks the turn as waiting on the user until the next tool event. |
| `TaskCreated`, `TaskCompleted` | `task-created`, `task-completed` | async | Tracks Claude's task list for the progress blend. |
| `Stop` | `stop` | sync | Closes the turn as completed and appends it to the history. A second `Stop` for the same prompt means another plugin's Stop hook kept Claude working, so the turn is extended and the new record supersedes the first. |
| `StopFailure` | `stop-failure` | sync | Closes the turn as failed (an API error ended it). |
| `SessionEnd` | `session-end` | sync | Closes any open turn as abandoned and deletes the session and progress files. |
| `PostModelSwitch` | `model` | async | Records the new model. |

The prompt and stop hooks stay synchronous because ordering matters. When a turn ends with messages still queued, Claude Code sends the next one immediately, and an asynchronous Stop could land after the next prompt and close the wrong turn.

Every hook exits 0 and prints nothing. Anything a `UserPromptSubmit` or `SessionStart` hook writes to stdout is added to Claude's context, so silence is a correctness requirement rather than a nicety. Errors go to `errors.log` in the data directory, which `/et-ay:doctor` surfaces.

### What counts as a turn

A turn runs from `UserPromptSubmit` to `Stop`. Its active time is the wall-clock time minus any time spent waiting on you, and active time is what the estimator learns from and predicts. A turn is used for training only if it completed normally and contained a single prompt. Interrupted, failed, abandoned and multi-prompt turns are kept in the history for the record and for `/et-ay:stats`, but they would distort the distribution of how long one prompt takes.

### Prompts that are not yours

`UserPromptSubmit` also fires when a scheduled task runs, when a background subagent reports back and when another session sends a message. The feature extractor tags prompts that open with a notification-style tag as `notification`, so they are compared mainly with each other.

## Features

The prompt is reduced to: character, word and line counts, the number of fenced code blocks, `@` file mentions and URLs, whether it contains pasted content, whether it uses sweeping words such as "every" or "codebase", the slash command name if any, and a coarse kind (`question`, `task`, `command`, `chat` or `notification`). The text itself is discarded. A slash command is stored by name only when it looks like one (lower-case letters, digits, hyphens and colons); anything else after a leading slash is stored as a short hash, so a sentence that happens to start with `/` leaves no words behind.

## The estimate

For each usable past turn the estimator computes a weight:

```text
weight = similarity(current, past) * 0.5 ** (age_days / half_life_days)
```

Similarity multiplies several factors. Prompt length contributes a Gaussian kernel on the difference of `log(1 + chars)` with a bandwidth of 0.9, so a 200-character prompt resembles a 300-character one far more than a 3,000-character one. A matching slash command multiplies the weight by 4 and a different one by 0.25, which makes a command's own past runs dominate. The kind contributes 1.5 when it matches and 0.6 when it does not, and the same project, model and effort contribute 1.8, 1.3 and 1.3 respectively (or 0.75 for a different model or effort).

The weighted durations are normalised to sum to their Kish effective sample size, `(Σw)² / Σw²`, which answers "how many equally weighted turns is this worth". A log-normal prior centred on a length-based guess is added as nine pseudo-observations with total weight `prior_strength * max(0.1, 1 - n_eff / 30)`, so it carries real weight on day one and fades to a tenth of its strength once there are about thirty comparable turns. The confidence label follows the effective sample size: `learning` below 3, `low` below 10, `medium` below 30 and `high` above.

The 160 heaviest points are stored with the turn, along with the 10th, 50th and 90th percentiles computed by midpoint interpolation over the weighted sample.

## Time remaining

The status line conditions the stored sample on the time already spent. Of the comparable turns that ran longer than `elapsed`, how much longer did they run? The answer is the weighted quantiles of `d - elapsed` over those points. When fewer than about one and a half effective points remain, the turn has outrun nearly everything comparable and et-ay switches to an "overdue" extrapolation in which the time left scales with the time spent (a median of 0.35 times the elapsed time). That reflects the long tail of agentic work: a job that has already overrun is more likely to be a big one.

If Claude has created at least two tasks and finished at least one, the rate of completion gives a second estimate, `elapsed_since_first_task / completed * (created - completed)`, which is blended into the median with weight `0.65 * completed / created`. The range is scaled by the same factor. Once every task is ticked off, the estimate collapses to a short wrap-up.

Each queued message Claude picked up mid-turn adds its own estimate, minus the time since it joined.

## Background progress

Claude Code gives SessionStart hooks, plugin hooks included, a `CLAUDE_ENV_FILE` path, and every Bash command later in the session runs that file first. The session-start hook appends one line to it:

```bash
export ET_AY_PROGRESS='<data dir>/progress/<safe session id>'
```

The path is single-quoted (with any single quote in it escaped as `'\''`), and the line is only added when the file does not already contain it, because SessionStart fires again after compaction and on resume. Each hook invocation may be handed a fresh file, in which case the check simply finds nothing. The hook also creates the `progress` directory, so a script can write to the path straight away, and it stays silent like every other hook. With `progress` set to `false` nothing is exported, and since the export happens only at session start, turning it on affects new sessions.

A script reports by overwriting the file with one line: `3/7 Building`, `42% Uploading` or a bare label. The status line already receives the session id, so it finds the file without any environment variable. It reads at most 4 KB, keeps the first line, removes ANSI escape sequences, control characters and undecodable bytes, collapses whitespace and caps the result at 200 characters, so a script cannot put terminal codes on your screen. A count needs a positive total and a percentage must be at most 100; anything else is shown as a label. The file's mtime is the time of the last update.

The segment comes after everything else in every state: after the running estimate, after the idle summary, or on its own after the label when there is neither. It reads `bg ▕██████░░░░▏ 3/7 Building · 2m ago`, or `bg [######----] ...` in ASCII mode, and a bare label shows only the label and its age. A report older than `progress_stale_minutes` shows `no update for 34m` in the warning colour. A finished report (a full count or 100%) is shown in the good colour, is never marked stale, and is hidden once it is older than `idle_seconds`. The progress segment sits outside the `segments` setting; `progress` turns it on or off as a whole.

The plugin's `progress` skill is the only skill Claude can invoke itself (it is hidden from the `/` menu instead). Its one-line description is always in context, and the body, which explains the line format and asks Claude to write the final count when a job finishes, loads only when Claude starts a long-running command.

## Files

| Path | Contents |
| --- | --- |
| `~/.claude/plugins/data/<id>/history.jsonl` | One JSON line per finished turn |
| `~/.claude/plugins/data/<id>/sessions/<session>.json` | The turn in progress and the last turn's summary |
| `~/.claude/plugins/data/<id>/progress/<session>` | The latest progress line from a background job, deleted at session end |
| `~/.claude/plugins/data/<id>/heartbeat.json` | When each hook last fired, for `/et-ay:doctor` |
| `~/.claude/plugins/data/<id>/errors.log`, `debug.jsonl` | Error log, and the optional redacted event log |
| `~/.claude/et-ay/config.json` | Your settings |
| `~/.claude/et-ay/statusline.sh` | The shim your `statusLine` points at |
| `~/.claude/et-ay/plugin_root`, `data_dir` | Pointers written at each session start |
| `~/.claude/et-ay/wrapped.json`, `wrapped_command` | Your previous status line, for wrapping and restoring |

The anchor directory exists because the status line command is not a hook. It receives none of the plugin variables, and the plugin's install path changes with every update, so the status line needs a fixed path to call and a way to find the current plugin. The shim reads the pointer, runs the current plugin's launcher, and falls back to your previous status line if the plugin has gone.

`CLAUDE_CONFIG_DIR` is respected throughout, so a non-default Claude config directory works.

## Known limits

Queued messages are invisible until Claude Code hands them over, and an Esc interrupt is only detected when the next prompt arrives. Durations for the very first turns come from the prior, which is a rough length-based guess. The transcript is read only to find the model name (from the `requestedModel` field) and only on a best-effort basis, because its format is internal to Claude Code. Windows has not been tested; the code uses `msvcrt` locking where `fcntl` is unavailable and should work under Git Bash with Python installed.
