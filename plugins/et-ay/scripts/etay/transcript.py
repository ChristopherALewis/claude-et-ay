# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christopher Lewis
"""Best-effort reads from the session transcript.

The transcript format is internal to Claude Code and can change without notice,
so everything here is optional: a failure returns None and the estimator simply
has one fewer signal.
"""

from __future__ import annotations

import re
from typing import Optional

# Only the top-level "requestedModel" key of assistant entries is trusted; a bare
# "model" key could just as well appear inside tool output quoted in the transcript.
_MODEL = re.compile(rb'"requestedModel"\s*:\s*"(claude-[A-Za-z0-9.\-]+)"')


def last_model(transcript_path: Optional[str], tail_bytes: int = 65536) -> Optional[str]:
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    try:
        with open(transcript_path, "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - tail_bytes))
            chunk = handle.read()
    except OSError:
        return None
    matches = _MODEL.findall(chunk)
    if not matches:
        return None
    return matches[-1].decode("ascii", "replace")
