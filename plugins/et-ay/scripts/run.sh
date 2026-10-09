#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christopher Lewis
# et-ay launcher.
#
# Finds a usable Python 3 interpreter and hands over to et_ay.py. Every hook
# and the status line go through this script, so it is written to fail quietly:
# if no suitable interpreter exists it exits 0 with no output, which Claude Code
# treats as "nothing to report" rather than as a hook error.

DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

usable() {
    # On macOS, /usr/bin/python3 is a stub that opens an installer dialog when
    # the Command Line Tools are missing. Never trigger that from a hook.
    if [ "$(uname -s 2>/dev/null)" = "Darwin" ] && [ "$(command -v "$1")" = "/usr/bin/python3" ]; then
        xcode-select -p >/dev/null 2>&1 || return 1
    fi
    command -v "$1" >/dev/null 2>&1
}

for candidate in "${ET_AY_PYTHON:-}" python3 python; do
    [ -n "$candidate" ] || continue
    if usable "$candidate"; then
        exec "$candidate" "$DIR/et_ay.py" "$@"
    fi
done

exit 0
