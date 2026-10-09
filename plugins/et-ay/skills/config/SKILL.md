---
name: config
description: Show or change et-ay settings such as which status line segments appear, colours, the clock format or debug logging.
argument-hint: "[show | set KEY VALUE | unset KEY]"
disable-model-invocation: true
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" *)
---

The user's request: $ARGUMENTS

If the request is empty or asks to see the settings, run:

```bash
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" config show
```

If the user wants to change something, translate the request into `config set KEY VALUE` or `config unset KEY` and run it the same way, for example:

```bash
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" config set segments '["left","range","elapsed"]'
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" config set clock_format '"%I:%M %p"'
sh "${CLAUDE_PLUGIN_ROOT}/scripts/run.sh" config set color false
```

Values are JSON, so strings need double quotes inside single quotes. The available settings and their meanings are listed by `config show` and documented in `${CLAUDE_PLUGIN_ROOT}/README.md`. Changes apply on the next status line refresh. Report the result in one or two sentences.
