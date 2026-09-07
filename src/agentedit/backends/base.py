# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""Language backend protocol.

The engine core (indexer/resolver/store/analyze) is language-agnostic. A
:class:`LanguageBackend` is the *only* language-specific piece: it knows how to
recognise, parse and name files of one language. Adding a language means
implementing this protocol — nothing in the core changes.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentedit.model import ParsedFile


@runtime_checkable
class LanguageBackend(Protocol):
    """Interface every supported language must implement."""

    #: Canonical language id stored in ``files.language`` (e.g. "typescript").
    language: str

    def language_for_path(self, path: str) -> str | None:
        """Return this backend's language id for ``path`` or None if not ours.

        Called with repo-relative or bare filenames; must not touch the disk.
        """
        ...

    def should_skip(self, path: str) -> bool:
        """True if a matching file must not be indexed (e.g. ``*.d.ts``)."""
        ...

    def module_id(self, path: str) -> str:
        """Canonical module/namespace id for a repo-relative path.

        The id is the *identity* under which this file's symbols live
        (TS/Python: dotted module path; Go: package import path; ...). It is
        stored on ``files.module_qname`` and never derived from paths by the
        core.
        """
        ...

    def parse_file(self, path: str, source_bytes: bytes) -> ParsedFile:
        """Deterministically extract a file into :class:`ParsedFile`.

        ``path`` is repo-relative. Every emitted ``Symbol`` must carry its
        ``module_qname`` (= ``module_id(path)``).
        """
        ...

    def spec_candidates(self, spec: str, from_path: str) -> list[str]:
        """Ordered candidate module ids an import specifier may resolve to.

        The resolver tries each candidate against the known-module set and
        keeps the first hit. ``spec`` is the raw text as written
        (``"./lib/auth"``, ``"react"``, ``"crate::util"``, ...); ``from_path``
        is the importing file's repo-relative path. External references
        (node_modules, stdlib, crates.io, maven) return ``[]``.
        """
        ...


def file_language(path: str, backends: list[LanguageBackend]) -> str | None:
    """Return the id of the first backend claiming ``path``."""
    for backend in backends:
        lang = backend.language_for_path(path)
        if lang is not None:
            return lang
    return None
