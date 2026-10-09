# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christopher Lewis
# et-ay entry point.
#
# This file deliberately uses syntax that every Python version can parse, so an
# old interpreter picked up by run.sh exits quietly instead of raising a
# SyntaxError that Claude Code would report as a hook failure.
import os
import sys

if sys.version_info < (3, 9):
    sys.exit(0)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from etay.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
