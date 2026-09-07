"""Pytest bootstrap: make the repo root importable regardless of invocation.

Tests import sibling tooling packages living in the repository root (``eval``).
Under the plain ``pytest`` binary the default ``prepend`` import mode only puts
the directory above the test files (``tests/``) on ``sys.path``, so those
packages are not importable. ``python -m pytest`` happened to work only because
it adds the current working directory. This module guarantees the repo root is
on ``sys.path`` for every invocation style.
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
