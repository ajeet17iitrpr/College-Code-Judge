from __future__ import annotations

import re


def normalize_output(value: str) -> str:
    """
    Line-aware output normalization for judge comparison.

    Rules applied (in order):
      1. Normalize CRLF → LF  (handles Windows editors / Java println on some JVMs)
      2. Strip trailing whitespace from every line  (avoids spurious trailing-space WA)
      3. Remove trailing blank lines  (many problems allow a final newline or not)

    Internal blank lines ARE preserved so that programs printing multi-block
    output (e.g. two matrices separated by a blank line) are compared correctly.
    We do NOT collapse all whitespace into a single token because that would
    turn "1\n2\n3" into "1 2 3" and give AC to programs with wrong line breaks.
    """
    # Step 1: normalize line endings
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    # Step 2: strip trailing whitespace from each line
    lines = [line.rstrip() for line in normalized.split("\n")]
    # Step 3: remove trailing blank lines (but keep a single trailing newline
    # equivalent by joining without adding one)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def outputs_match(actual: str, expected: str) -> bool:
    """Return True iff actual output matches expected after normalization."""
    return normalize_output(actual) == normalize_output(expected)


def sanitize_compiler_message(message: str, max_length: int = 32768) -> str:
    """
    Strip sandbox-internal paths from compiler output before showing students.

    The sandbox mounts source code under /workspace/. Exposing that path is a
    minor information leak and looks confusing to students. We replace it with
    the bare filename so error lines look like:  main.c:5:3: error: ...
    """
    # Remove /workspace/ prefix that GCC/G++/javac emit in error messages
    cleaned = re.sub(r"/workspace/", "", message)
    # Also strip any absolute temp-dir paths the host may inject
    cleaned = re.sub(r"/tmp/judge-[^/]+/", "", cleaned)
    # Remove null bytes
    cleaned = cleaned.replace("\x00", "")
    return cleaned[:max_length]
