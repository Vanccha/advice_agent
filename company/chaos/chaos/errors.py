"""Chaos tool error type.

Where this sits: raised by scenarios, caught by `cli.py` and printed as a
normal error message.

Read next: `cli.py`.
"""
from __future__ import annotations


class ChaosError(Exception):
    """A scenario could not find or build a suitable victim record."""
