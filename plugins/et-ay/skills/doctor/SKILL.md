---
name: doctor
description: Check that et-ay's hooks, status line and data directory are wired up correctly, and explain any problems.
disable-model-invocation: true
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" *)
---

Run this command with the Bash tool:

```bash
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" doctor
```

If the command prints nothing, no usable Python 3.9+ interpreter was found; tell the user that is the problem.

Otherwise show the output in a code block. For every line marked `[!!]`, explain in one sentence what it means and what the user should do, using the fix the line itself suggests. If every line is `[ok]`, say that everything looks healthy.
