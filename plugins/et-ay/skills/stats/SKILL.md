---
name: stats
description: Show how long your Claude Code turns take and how accurate et-ay's time estimates have been.
argument-hint: "[--recent N]"
disable-model-invocation: true
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" *)
---

Run this command with the Bash tool:

```bash
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" stats $ARGUMENTS
```

Show the output to the user as a code block so the columns line up. After it, add at most two sentences of interpretation: whether the estimates are well calibrated (the shown range should contain roughly 80% of turns) and, if they lean consistently short or long, that this usually settles as more history builds up. Do not invent numbers that are not in the output.
