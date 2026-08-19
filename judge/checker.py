from __future__ import annotations


def normalize_output(value: str) -> str:
    """Whitespace-insensitive output normalization."""
    return " ".join(value.split())


def outputs_match(actual: str, expected: str) -> bool:
    return normalize_output(actual) == normalize_output(expected)
