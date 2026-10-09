# Security policy

et-ay runs on your machine as part of Claude Code: its hooks execute on every prompt, and `/et-ay:setup` edits your Claude Code settings file. Security reports are therefore taken seriously, even for a small project.

## Supported versions

Only the latest release receives fixes. Claude Code updates plugins installed from the marketplace when the version changes, so staying current is a matter of accepting updates.

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub's [private vulnerability reporting](https://github.com/ChristopherALewis/claude-et-ay/security/advisories/new) rather than in a public issue. Include what you found, how to reproduce it and the versions involved. You should hear back within a week, and once a fix is released you will be credited in the advisory unless you would rather not be.

## What is in scope

Reports of particular interest include anything that lets hook input cause code execution, anything that writes prompt text, tool input or tool output to disk, anything that causes a hook to print to stdout (which Claude Code would feed into Claude's context), and any way `setup` or `remove` could damage or leak the contents of `settings.json`.
