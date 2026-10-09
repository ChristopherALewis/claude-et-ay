# Contributing to et-ay

Contributions are welcome, whether that is a bug report, a better default for the estimator or a fix for a platform I have not tested. This file covers how to get set up and what a change needs before it can be merged.

## Getting set up

You need Python 3.9 or later and, to try the plugin for real, Claude Code. There are no dependencies to install.

```bash
git clone https://github.com/ChristopherALewis/claude-et-ay.git
cd claude-et-ay
python3 -m unittest discover -s tests
```

To run your working copy inside Claude Code without installing it, start a session with `claude --plugin-dir ./plugins/et-ay`, then run `/et-ay:setup` and `/et-ay:doctor`. Setting `/et-ay:config set debug true` writes a redacted log of every hook event to `debug.jsonl` in the data directory, which is the quickest way to see what Claude Code is actually sending.

## Before you open a pull request

Please make sure that:

- the tests pass with `python3 -m unittest discover -s tests`, and new behaviour comes with a test;
- `ruff check .` and `ruff format --check .` are clean (configuration is in `pyproject.toml`);
- `claude plugin validate ./plugins/et-ay` and `claude plugin validate .` pass if you have Claude Code installed;
- hooks still exit 0 and print nothing, whatever input they receive, because stdout from some hooks is fed straight into Claude's context;
- no prompt text, tool input or tool output is written to disk;
- the plugin keeps working with the standard library alone.

When a change alters behaviour that users will notice, bump the version in `VERSION`, `plugins/et-ay/.claude-plugin/plugin.json` and `plugins/et-ay/scripts/etay/__init__.py` in the same commit, and add an entry to `CHANGELOG.md`. A test fails if those versions disagree. Claude Code pins installed plugins to the manifest version, so users only receive a change once the version moves.

## Commit messages

Start with an imperative summary line under about seventy characters, leave a blank line, then explain what changed and why, grouped by area where that helps, with any behavioural notes a future maintainer would need. End the body with the test result, for example `Tests: 112 passing`.

## Writing style

Documentation is written in British English. Please avoid em dashes (the test suite checks for them) and prefer plain, specific sentences to marketing language.

## Reporting bugs

Open an issue using the bug report template and include the output of `/et-ay:doctor`, your operating system, your Python version and your Claude Code version (`claude --version`). If the problem is in the estimates rather than the plumbing, the output of `/et-ay:stats` helps too. Both are safe to share: neither contains prompt text.

## Code of conduct

Everyone taking part is expected to follow the [code of conduct](CODE_OF_CONDUCT.md).

## Licence

By contributing you agree that your contributions are licensed under the MIT licence that covers the project.
