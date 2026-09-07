# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""SQLite graph store.

One local ``.db`` file per indexed repository. The schema mirrors (in
simplified form) the graph tables of SwiftGate's ``skelett`` engine but drops
everything multi-tenant / PG-specific: no vector columns, no RLS, no audit
tables. Storage is a replaceable detail — everything above ``store`` talks to
the :class:`GraphStore` protocol, never to SQL directly.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from agentedit.model import Symbol

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    path         TEXT PRIMARY KEY,
    module_qname TEXT NOT NULL,
    language     TEXT NOT NULL,
    sha256       TEXT NOT NULL,
    line_count   INTEGER NOT NULL DEFAULT 0,
    deleted      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS symbols (
    qname      TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    file_path  TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
    signature  TEXT,
    line_start INTEGER NOT NULL DEFAULT 0,
    line_end   INTEGER NOT NULL DEFAULT 0,
    exported   INTEGER NOT NULL DEFAULT 0,
    decorated  INTEGER NOT NULL DEFAULT 0,
    external_entry INTEGER NOT NULL DEFAULT 0,
    external_hint  TEXT
);
CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path);

CREATE TABLE IF NOT EXISTS edges (
    id           INTEGER PRIMARY KEY,
    source_qname TEXT NOT NULL REFERENCES symbols(qname) ON DELETE CASCADE,
    target_qname TEXT NOT NULL REFERENCES symbols(qname) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    method       TEXT NOT NULL DEFAULT 'static',
    UNIQUE (source_qname, target_qname, kind, method)
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_qname);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_qname);

CREATE TABLE IF NOT EXISTS star_exports (
    path   TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
    target TEXT NOT NULL,
    PRIMARY KEY (path, target)
);

CREATE TABLE IF NOT EXISTS unresolved (
    id          INTEGER PRIMARY KEY,
    source_qname TEXT NOT NULL,
    target_text TEXT NOT NULL,
    kind        TEXT NOT NULL,
    bucket      TEXT NOT NULL,
    member      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_unresolved_member ON unresolved(member, bucket);
CREATE INDEX IF NOT EXISTS idx_unresolved_source ON unresolved(source_qname);

"""


class GraphStore:
    """Thin, dependency-free persistence over sqlite3."""

    def __init__(self, db_path: str | Path):
        self._path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    # -- connection lifecycle ------------------------------------------------

    def connect(self) -> GraphStore:
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._ensure_columns()
        self._drop_legacy_knowledge()
        self._conn.commit()
        return self

    def _drop_legacy_knowledge(self) -> None:
        """Remove tables owned by the pre-V1.0 knowledge layer.

        Versions up to 1.2.x created ``notes``/``knowledge`` tables inside the
        store. V1.0 is a pure structural engine: any such leftover table is
        dropped on connect so existing databases are cleaned in place.
        """
        assert self._conn is not None
        for table in ("notes", "knowledge"):
            self._conn.execute(f"DROP TABLE IF EXISTS {table}")

    def _ensure_columns(self) -> None:
        """In-place migrations for columns added after the original schema."""
        assert self._conn is not None
        existing = {
            str(r["name"])
            for r in self._conn.execute("PRAGMA table_info(symbols)").fetchall()
        }
        additions = {
            "decorated": "INTEGER NOT NULL DEFAULT 0",
            "external_entry": "INTEGER NOT NULL DEFAULT 0",
            "external_hint": "TEXT",
        }
        for name, decl in additions.items():
            if name not in existing:
                self._conn.execute(f"ALTER TABLE symbols ADD COLUMN {name} {decl}")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- metadata ------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        assert self._conn is not None
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._conn.commit()

    # -- files ---------------------------------------------------------------

    def index_files(self) -> dict[str, dict[str, Any]]:
        """Return {path: {sha256, module_qname}} for currently indexed files."""
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT path, sha256, module_qname, deleted FROM files"
        ).fetchall()
        return {r["path"]: dict(r) for r in rows}

    def mark_deleted(self, path: str) -> None:
        assert self._conn is not None
        self._conn.execute("UPDATE files SET deleted = 1 WHERE path = ?", (path,))

    def drop_deleted(self) -> None:
        """Physically remove deleted file rows (cascades their symbols/edges)."""
        assert self._conn is not None
        self._conn.execute("DELETE FROM files WHERE deleted = 1")
        self._conn.commit()

    # -- symbols -------------------------------------------------------------

    def symbols_in_file(self, path: str) -> list[dict[str, Any]]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT s.qname, s.name, s.kind, s.signature, s.exported, "
            "s.decorated, s.external_entry, s.external_hint, "
            "s.file_path, f.module_qname FROM symbols s "
            "JOIN files f ON f.path = s.file_path WHERE s.file_path = ?",
            (path,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_symbol(self, qname: str) -> dict[str, Any] | None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT s.qname, s.name, s.kind, s.signature, s.exported, "
            "s.decorated, s.external_entry, s.external_hint, s.file_path, "
            "f.language, f.module_qname FROM symbols s JOIN files f ON f.path = s.file_path "
            "WHERE s.qname = ?",
            (qname,),
        ).fetchone()
        return dict(row) if row else None

    def get_symbol_with_lines(self, qname: str) -> dict[str, Any] | None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT s.qname, s.name, s.kind, s.signature, s.exported, "
            "s.decorated, s.external_entry, s.external_hint, s.file_path, "
            "s.line_start, s.line_end, f.language, f.module_qname "
            "FROM symbols s JOIN files f ON f.path = s.file_path WHERE s.qname = ?",
            (qname,),
        ).fetchone()
        return dict(row) if row else None

    def search_symbols(self, query: str, limit: int = 25) -> list[dict[str, Any]]:
        assert self._conn is not None
        like = f"%{query}%"
        rows = self._conn.execute(
            "SELECT qname, name, kind, file_path FROM symbols "
            "WHERE qname LIKE ? OR name LIKE ? ORDER BY kind LIMIT ?",
            (like, like, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- edges ---------------------------------------------------------------

    def edges_from(self, qname: str) -> list[dict[str, Any]]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT source_qname, target_qname, kind, method FROM edges "
            "WHERE source_qname = ?",
            (qname,),
        ).fetchall()
        return [dict(r) for r in rows]

    def edges_to(self, qname: str, kinds: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        assert self._conn is not None
        if kinds:
            marks = ",".join("?" for _ in kinds)
            rows = self._conn.execute(
                f"SELECT source_qname, target_qname, kind, method FROM edges "
                f"WHERE target_qname = ? AND kind IN ({marks})",
                (qname, *kinds),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT source_qname, target_qname, kind, method FROM edges "
                "WHERE target_qname = ?",
                (qname,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_rows(self, table: str) -> int:
        assert self._conn is not None
        row = self._conn.execute(f"SELECT count(*) AS c FROM {table}").fetchone()
        return int(row["c"])

    def edges_by_method(self) -> dict[str, int]:
        """Edge-count distribution by resolution method (resolution health)."""
        assert self._conn is not None
        out: dict[str, int] = {}
        for row in self._conn.execute(
            "SELECT method, COUNT(*) AS c FROM edges GROUP BY method"
        ):
            out[str(row["method"])] = int(row["c"])
        return out

    def all_symbol_rows(self) -> list[dict[str, Any]]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT s.qname, s.name, s.kind, s.file_path, s.signature, "
            "s.line_start, s.line_end, s.exported, "
            "s.decorated, s.external_entry, s.external_hint, f.module_qname "
            "FROM symbols s JOIN files f ON f.path = s.file_path"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- transactional per-file replacement ----------------------------------

    def replace_symbols(self, path: str, language: str, module_qname: str, sha256: str,
                        symbols: list[Symbol], line_count: int) -> None:
        """Phase 1: (re)create file metadata + symbols for one file.

        Symbols that still exist keep their row (upsert), so incoming edges
        from other files into surviving symbols/modules are preserved across
        incremental re-parses. Only symbols that *disappeared* are deleted
        (their incoming edges cascade away with them — correct, the target is
        gone). Outgoing edges of this file are rebuilt in phase 2.
        """
        assert self._conn is not None
        cur = self._conn
        cur.execute("BEGIN IMMEDIATE")
        try:
            cur.execute(
                "INSERT INTO files(path, module_qname, language, sha256, line_count, deleted) "
                "VALUES (?, ?, ?, ?, ?, 0) "
                "ON CONFLICT(path) DO UPDATE SET module_qname=excluded.module_qname, "
                "language=excluded.language, sha256=excluded.sha256, "
                "line_count=excluded.line_count, deleted=0",
                (path, module_qname, language, sha256, line_count),
            )
            existing = {
                r["qname"]
                for r in cur.execute(
                    "SELECT qname FROM symbols WHERE file_path = ?", (path,)
                ).fetchall()
            }
            new_qnames = {s.qname for s in symbols}
            for qname in sorted(existing - new_qnames):
                cur.execute(
                    "DELETE FROM symbols WHERE qname = ? AND file_path = ?", (qname, path)
                )
            for s in symbols:
                cur.execute(
                    "INSERT INTO symbols"
                    "(qname, name, kind, file_path, signature, line_start, line_end, "
                    "exported, decorated, external_entry, external_hint) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(qname) DO UPDATE SET "
                    "name=excluded.name, kind=excluded.kind, signature=excluded.signature, "
                    "line_start=excluded.line_start, line_end=excluded.line_end, "
                    "exported=excluded.exported, decorated=excluded.decorated, "
                    "external_entry=excluded.external_entry, external_hint=excluded.external_hint "
                    "WHERE symbols.file_path = excluded.file_path",
                    (s.qname, s.name, s.kind, s.file_path, s.signature,
                     s.line_start, s.line_end, int(s.exported),
                     int(s.decorated), int(s.external_entry), s.external_hint),
                )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def replace_star_exports(self, path: str, targets: list[str]) -> None:
        """Persist a file's `export * from` targets (barrel re-export chains)."""
        assert self._conn is not None
        with self._conn:
            self._conn.execute("DELETE FROM star_exports WHERE path = ?", (path,))
            for target in targets:
                self._conn.execute(
                    "INSERT OR IGNORE INTO star_exports(path, target) VALUES (?, ?)",
                    (path, target),
                )

    def star_exports_all(self) -> list[dict[str, Any]]:
        assert self._conn is not None
        rows = self._conn.execute("SELECT path, target FROM star_exports").fetchall()
        return [dict(r) for r in rows]

    def global_edge_files(self) -> list[str]:
        """Distinct files with any outgoing edge resolved via 'global' fallback."""
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT DISTINCT s.file_path FROM edges e "
            "JOIN symbols s ON s.qname = e.source_qname WHERE e.method = 'global'"
        ).fetchall()
        return [str(r["file_path"]) for r in rows]

    def module_importer_files(self, module_ids: tuple[str, ...]) -> list[str]:
        """Distinct files with a whole-module import edge into ``module_ids``."""
        assert self._conn is not None
        if not module_ids:
            return []
        marks = ",".join("?" for _ in module_ids)
        rows = self._conn.execute(
            "SELECT DISTINCT s.file_path FROM edges e "
            "JOIN symbols s ON s.qname = e.source_qname "
            f"WHERE e.kind = 'imports' AND e.method = 'static' "
            f"AND e.target_qname IN ({marks})",
            (*module_ids,),
        ).fetchall()
        return [str(r["file_path"]) for r in rows]

    def replace_edges(self, path: str, edges: list[tuple[str, str, str, str]]) -> None:
        """Phase 2: rebuild the outgoing edges of one file's symbols.

        Runs after every changed file's symbols exist, so edges may reference
        symbols in files processed later in the same index run.
        """
        assert self._conn is not None
        cur = self._conn
        cur.execute("BEGIN IMMEDIATE")
        try:
            cur.execute(
                "DELETE FROM edges WHERE source_qname IN "
                "(SELECT qname FROM symbols WHERE file_path = ?)",
                (path,),
            )
            for source, target, kind, method in edges:
                cur.execute(
                    "INSERT OR IGNORE INTO edges(source_qname, target_qname, kind, method) "
                    "VALUES (?, ?, ?, ?)",
                    (source, target, kind, method),
                )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def replace_unresolved(self, path: str,
                           rows: list[dict[str, Any]]) -> None:
        """Persist one file's unresolved usage references (honest reporting).

        ``rows`` entries carry ``source_qname/target_text/kind/bucket/member``.
        Deleted with the file's symbols so a re-parse never leaves stale
        suspected-caller entries behind.
        """
        assert self._conn is not None
        cur = self._conn
        cur.execute("BEGIN IMMEDIATE")
        try:
            cur.execute(
                "DELETE FROM unresolved WHERE source_qname IN "
                "(SELECT qname FROM symbols WHERE file_path = ?)",
                (path,),
            )
            for r in rows:
                cur.execute(
                    "INSERT INTO unresolved"
                    "(source_qname, target_text, kind, bucket, member) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (r["source_qname"], r["target_text"], r["kind"],
                     r["bucket"], r["member"]),
                )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def unresolved_suspected(self, member: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Unresolved in-repo/dynamic usage refs whose final member matches.

        Used to surface *name-based* suspected callers when a symbol has no
        resolved dependents — a low-confidence pointer, never a fabricated edge.
        """
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT u.source_qname, u.target_text, u.kind, u.bucket, "
            "s.file_path FROM unresolved u "
            "LEFT JOIN symbols s ON s.qname = u.source_qname "
            "WHERE u.member = ? AND u.bucket != 'external' AND u.kind = 'calls' "
            "ORDER BY u.source_qname LIMIT ?",
            (member, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def inherits_pairs(self) -> list[tuple[str, str]]:
        """Resolved (class -> base) pairs from the persisted inherits edges."""
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT source_qname, target_qname FROM edges WHERE kind = 'inherits'"
        ).fetchall()
        return [(str(r["source_qname"]), str(r["target_qname"])) for r in rows]

    def unresolved_files(self) -> list[str]:
        """Distinct files that own persisted unresolved usage references."""
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT DISTINCT s.file_path FROM unresolved u "
            "JOIN symbols s ON s.qname = u.source_qname"
        ).fetchall()
        return [str(r["file_path"]) for r in rows]
