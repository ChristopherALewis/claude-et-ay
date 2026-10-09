---
name: setup
description: Add the et-ay time-to-completion estimate to your Claude Code status line, keeping any status line you already have.
argument-hint: "[--position after|before|line] [--refresh SECONDS] [--no-wrap]"
disable-model-invocation: true
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" *)
---

Run this command exactly once with the Bash tool:

```bash
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" setup $ARGUMENTS
```

Then tell the user what changed, using the command's own output. Keep it short. Mention that:

- the estimate appears in the status line the next time it refreshes, with no restart needed;
- estimates are rough for the first few dozen prompts and improve as et-ay learns from their history;
- `/et-ay:remove` puts their previous status line back, and should be run before uninstalling the plugin.

If the command prints nothing at all, Python 3.9 or later could not be found. Say so, and suggest installing Python 3 (on macOS, `xcode-select --install` provides it) before running `/et-ay:setup` again.

If the output says setup stopped because `settings.json` is not plain JSON, do not edit the file yourself. Show the user the manual `statusLine` entry from the plugin README instead.
