# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""Backend registry.

Thin, importable registry mapping canonical language ids to backend instances.
The indexer and the change-surface layer use ``get_backend`` /
``detect_backend`` here instead of reaching into language modules directly.
"""
from __future__ import annotations

from agentedit.backends.base import LanguageBackend, file_language
from agentedit.backends.go import GoBackend
from agentedit.backends.java import JavaBackend
from agentedit.backends.python import PythonBackend
from agentedit.backends.rust import RustBackend
from agentedit.backends.typescript import TypeScriptBackend

__all__ = ["LanguageBackend", "backend_for_path", "detect_backend", "file_language", "get_backend", "registered_backends"]

_TS = TypeScriptBackend()
_PY = PythonBackend()
_GO = GoBackend()
_RS = RustBackend()
_JV = JavaBackend()

#: Registered backend instances, keyed by canonical language id.
_BACKENDS: dict[str, LanguageBackend] = {_TS.language: _TS, _PY.language: _PY, _GO.language: _GO, _RS.language: _RS, _JV.language: _JV}


def registered_backends() -> list[LanguageBackend]:
    return list(_BACKENDS.values())


def get_backend(language: str) -> LanguageBackend | None:
    return _BACKENDS.get(language)


def backend_for_path(path: str) -> LanguageBackend | None:
    """First backend that claims ``path`` (by extension; no disk I/O)."""
    lang = file_language(path, registered_backends())
    return get_backend(lang) if lang else None


def detect_backend(files: list[str]) -> LanguageBackend | None:
    """Pick the backend governing ``files`` (dominant by count, ties by order)."""
    order = list(_BACKENDS)
    scores: dict[str, int] = {}
    for rel in files:
        lang = file_language(rel, registered_backends())
        if lang is not None:
            scores[lang] = scores.get(lang, 0) + 1
    if not scores:
        return None
    best = max(scores, key=lambda lang: (scores[lang], -order.index(lang)))
    return get_backend(best)
