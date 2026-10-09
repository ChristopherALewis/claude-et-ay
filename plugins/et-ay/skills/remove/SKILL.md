---
name: remove
description: Take et-ay out of your status line and restore the status line you had before. Run this before uninstalling the plugin.
argument-hint: "[--purge]"
disable-model-invocation: true
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" *)
---

Run this command exactly once with the Bash tool:

```bash
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" --data-dir "${CLAUDE_PLUGIN_DATA}" remove $ARGUMENTS
```

Report what the command says changed. If the user is removing et-ay entirely, tell them the remaining step is to uninstall the plugin from `/plugin`, which also deletes the recorded history.
