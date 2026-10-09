# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Images of the status line in each state in the README, and a one-step install line in the plugin's own README.

## [0.1.0] - 2026-10-09

### Added

- Live estimated time to completion in the Claude Code status line, with a range, finish time, elapsed time, task-list progress and a count of queued messages joined mid-turn.
- Estimator that weights past turns by prompt similarity and recency, with a length-based prior for the first few turns, and conditions on time already spent while a turn runs.
- Turn tracking through hooks, covering queued messages, interrupts, permission prompts and questions (time waiting on the user is excluded), failures, crashes and other plugins blocking Stop.
- `/et-ay:setup` and `/et-ay:remove`, which add et-ay to the status line and restore the previous one, wrapping an existing status line rather than replacing it and taking a backup before every edit.
- `/et-ay:stats`, `/et-ay:doctor` and `/et-ay:config`, plus CSV export and history reset from the command line.
- Privacy by design: prompt text is never stored, and the optional debug log is redacted.

[Unreleased]: https://github.com/ChristopherALewis/claude-et-ay/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ChristopherALewis/claude-et-ay/releases/tag/v0.1.0
