# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""MCP stdio server (protocol 2024-11-05), eight read-only tools.

Eight read-only tools operate on a local graph (``<repo>/.agentedit/graph.db``
or a named graph) and lazily index the repo on first call if no graph exists.
The structural graph is rebuilt from code and is immutable for agents — there
is no write surface. All log output goes to stderr; stdout carries only
newline-delimited JSON-RPC frames.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict
from typing import Any

from agentedit import __version__, graphs
from agentedit.defaults import default_db
from agentedit.model import ImpactReport
from agentedit.store.sqlite import GraphStore

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2024-11-05"
_KNOWN_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "impact",
        "description": "Who depends on a symbol and what a change to it would ripple into. "
                       "Returns direct + transitive dependants with confidence grades.",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string", "description": "qualified symbol name, e.g. src.auth.authenticate"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "dependents",
        "description": "Direct + transitive structural dependants of a symbol (calls / inherits / renders).",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
    {
        "name": "would_break",
        "description": "Predict breakage for a concrete change to a symbol: removed/renamed/signature/type.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "change": {"type": "string", "enum": ["removed", "renamed", "signature", "type", "return_type", "members"], "default": "removed"},
                "new_signature": {"type": "string", "description": "required when change=signature"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "changes",
        "description": "Change surface of the current working tree: changed files + symbols in unchanged "
                       "files that depend on edited code.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "search",
        "description": "Find symbols by name or qualified-name fragment (kind + qname + file).",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 25}},
            "required": ["query"],
        },
    },
    {
        "name": "symbols_in_file",
        "description": "List every indexed symbol defined in one file (kind + qname + line range).",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "repo-relative file path"}},
            "required": ["path"],
        },
    },
    {
        "name": "audit",
        "description": "One-call crash-audit before an edit: what the change ripples into "
                       "(impact + would_break), grouped affected files, git rationale, "
                       "suspected unresolved references, and resolution health.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "symbol qname or a repo-relative file path"},
                "limit_files": {"type": "integer", "default": 15},
            },
            "required": ["target"],
        },
    },
    {
        "name": "why",
        "description": "Git rationale for a symbol: deterministic commit facts on its line range "
                       "plus candidate observations mined from commit messages (never confirmed).",
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    },
]


class McpServer:
    def __init__(
        self,
        repo: str | None = None,
        graph: str | None = None,
        graphs_dir: str | None = None,
    ):
        self._repo = os.path.abspath(repo) if repo else None
        self._graph = graph
        self._graphs_dir = graphs_dir or (graph and graphs.default_graphs_dir())
        if self._repo is not None:
            self._db = default_db(self._repo)
        elif graph is not None:
            if not graphs.exists(graph, self._graphs_dir):
                raise ValueError(f"graph not found: {graph}")
            self._db = ""
        else:  # pragma: no cover - guarded by CLI
            raise ValueError("mcp requires --repo or --graph")
        self._store: GraphStore | None = None

    # -- store lazy init -----------------------------------------------------

    def _ensure_store(self) -> GraphStore:
        if self._store is not None:
            return self._store
        assert self._repo is not None  # graph mode uses _call_tool_graph
        if not os.path.isfile(self._db):
            logger.info("no graph at %s — indexing %s", self._db, self._repo)
            from agentedit.index.indexer import index_repo

            index_repo(self._repo, self._db)
        self._store = GraphStore(self._db).connect()
        return self._store

    def _graph_entries(self) -> list[dict[str, str]]:
        assert self._graph is not None
        return graphs.entries(self._graph, self._graphs_dir)

    # -- dispatch ------------------------------------------------------------

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method = msg.get("method")
        ident = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            requested = str(params.get("protocolVersion") or _PROTOCOL_VERSION)
            negotiated = requested if requested in _KNOWN_PROTOCOLS else _PROTOCOL_VERSION
            return {"id": ident, "result": {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "agentedit", "version": __version__},
            }}
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return {"id": ident, "result": {}}
        if method == "tools/list":
            return {"id": ident, "result": {"tools": TOOLS}}
        if method == "tools/call":
            return self._call_tool(ident, params)
        if method is None and ident is None:
            return None
        return {"id": ident, "error": {"code": -32601, "message": f"unknown method {method}"}}

    def _call_tool(self, ident: Any, params: dict[str, Any]) -> dict[str, Any]:
        if self._graph is not None:
            return self._call_tool_graph(ident, params)
        assert self._repo is not None
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            if name == "impact":
                from agentedit.analyze.impact import impact

                text: dict[str, Any] = _report_dict(impact(self._ensure_store(), args["symbol"]))
            elif name == "dependents":
                from agentedit.analyze.impact import dependents

                text = _report_dict(dependents(self._ensure_store(), args["symbol"]))
            elif name == "would_break":
                from agentedit.analyze.impact import would_break

                text = _report_dict(would_break(
                    self._ensure_store(), args["symbol"],
                    change=args.get("change", "removed"),
                    new_signature=args.get("new_signature"),
                ))
            elif name == "changes":
                from agentedit.analyze.changes import changes

                text = changes(self._ensure_store(), self._repo)
            elif name == "search":
                store = self._ensure_store()
                results = store.search_symbols(str(args["query"]), limit=int(args.get("limit", 25)))
                text = {"results": results}
            elif name == "symbols_in_file":
                store = self._ensure_store()
                rows = store.symbols_in_file(str(args["path"]))
                text = {"file": args["path"], "symbols": rows}
            elif name == "audit":
                from agentedit.audit import audit

                store = self._ensure_store()
                text = audit(store, self._repo, str(args["target"]),
                             limit_files=int(args.get("limit_files", 15)))
            elif name == "why":
                from agentedit.audit import why

                store = self._ensure_store()
                text = why(store, self._repo, str(args["symbol"]))
            else:
                return {"id": ident, "error": {"code": -32602, "message": f"unknown tool {name}"}}
            return {"id": ident, "result": {
                "content": [{"type": "text", "text": json.dumps(text, indent=2)}],
                "isError": False,
            }}
        except KeyError as exc:
            return {"id": ident, "error": {"code": -32602, "message": f"missing argument: {exc}"}}
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("tool call failed")
            return {"id": ident, "error": {"code": -32603, "message": str(exc)}}


    def _call_tool_graph(self, ident: Any, params: dict[str, Any]) -> dict[str, Any]:
        """Fan a tool call out over every member repo of the graph.

        Each repo keeps its own fully-correct index; answers are repo-tagged.
        Structural edges never cross repos here (see the contract-edge phase
        for cross-repo reach) — a missing symbol in one member is not guessed.
        """
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            entries = self._graph_entries()
            graph_name = self._graph
            assert graph_name is not None
            text: dict[str, Any] = {}
            if name in ("impact", "dependents", "would_break"):
                if "symbol" not in args:
                    raise KeyError("symbol")
                symbol = str(args["symbol"])
                from agentedit.analyze.impact import dependents, impact, would_break

                matched: list[dict[str, Any]] = []
                for entry in entries:
                    graphs.ensure_indexed(entry)
                    store = GraphStore(entry["db"]).connect()
                    try:
                        if store.get_symbol(symbol) is None:
                            continue
                        if name == "impact":
                            payload: dict[str, Any] = _report_dict(impact(store, symbol))
                        elif name == "dependents":
                            payload = _report_dict(dependents(store, symbol))
                        else:
                            payload = _report_dict(would_break(
                                store, symbol,
                                change=args.get("change", "removed"),
                                new_signature=args.get("new_signature"),
                            ))
                        matched.append({"repo": entry["repo"], "report": payload})
                    finally:
                        store.close()
                if matched:
                    consumers = _contract_consumers(graph_name,
                                                    [{"repo": m["repo"], "qname": symbol}
                                                     for m in matched],
                                                    self._graphs_dir)
                    text = {"graph": self._graph, "repos": matched}
                    if consumers:
                        text["contract_consumers"] = consumers
                else:
                    text = {"graph": self._graph, "symbol": symbol, "risk": "none", "repos": []}
            elif name == "search":
                results: list[dict[str, Any]] = []
                for entry in entries:
                    graphs.ensure_indexed(entry)
                    store = GraphStore(entry["db"]).connect()
                    try:
                        for row in store.search_symbols(str(args["query"]),
                                                         limit=int(args.get("limit", 25))):
                            results.append({"repo": entry["repo"], **row})
                    finally:
                        store.close()
                text = {"graph": self._graph, "results": results}
            elif name == "symbols_in_file":
                files: list[dict[str, Any]] = []
                for entry in entries:
                    graphs.ensure_indexed(entry)
                    store = GraphStore(entry["db"]).connect()
                    try:
                        rows = store.symbols_in_file(str(args["path"]))
                        if rows:
                            files.append({"repo": entry["repo"], "file": args["path"], "symbols": rows})
                    finally:
                        store.close()
                text = {"graph": self._graph, "files": files}
            elif name == "changes":
                from agentedit.analyze.changes import changes

                per: list[dict[str, Any]] = []
                for entry in entries:
                    graphs.ensure_indexed(entry)
                    store = GraphStore(entry["db"]).connect()
                    try:
                        per.append({"repo": entry["repo"], "report": changes(store, entry["repo"])})
                    finally:
                        store.close()
                text = {"graph": self._graph, "repos": per}
            elif name == "audit":
                if "target" not in args:
                    raise KeyError("target")
                from agentedit.audit import audit as run_audit

                per_audit: list[dict[str, Any]] = []
                for entry in entries:
                    graphs.ensure_indexed(entry)
                    store = GraphStore(entry["db"]).connect()
                    try:
                        target = str(args["target"])
                        if store.get_symbol(target) is None and not store.symbols_in_file(target):
                            continue
                        per_audit.append({
                            "repo": entry["repo"],
                            "report": run_audit(store, entry["repo"], target,
                                                limit_files=int(args.get("limit_files", 15))),
                        })
                    finally:
                        store.close()
                if per_audit:
                    text = {"graph": graph_name, "target": args["target"], "repos": per_audit}
                    symbol_providers = [
                        {"repo": item["repo"], "qname": str(args["target"])}
                        for item in per_audit if item["report"].get("kind") == "symbol"
                    ]
                    if symbol_providers:
                        consumers = _contract_consumers(graph_name, symbol_providers,
                                                        self._graphs_dir)
                        if consumers:
                            text["contract_consumers"] = consumers
                else:
                    text = {"graph": self._graph, "target": args["target"],
                            "risk": "none", "repos": []}
            elif name == "why":
                if "symbol" not in args:
                    raise KeyError("symbol")
                from agentedit.audit import why as why_for

                per_why: list[dict[str, Any]] = []
                for entry in entries:
                    graphs.ensure_indexed(entry)
                    store = GraphStore(entry["db"]).connect()
                    try:
                        if store.get_symbol(str(args["symbol"])) is None:
                            continue
                        per_why.append({"repo": entry["repo"],
                                        "report": why_for(store, entry["repo"], str(args["symbol"]))})
                    finally:
                        store.close()
                if per_why:
                    text = {"graph": self._graph, "symbol": args["symbol"], "repos": per_why}
                else:
                    text = {"graph": self._graph, "symbol": args["symbol"],
                            "facts": [], "candidates": [], "notes": ["symbol not found in graph"]}
            else:
                return {"id": ident, "error": {"code": -32602, "message": f"unknown tool {name}"}}
            return {"id": ident, "result": {
                "content": [{"type": "text", "text": json.dumps(text, indent=2)}],
                "isError": False,
            }}
        except KeyError as exc:
            return {"id": ident, "error": {"code": -32602, "message": f"missing argument: {exc}"}}
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("graph tool call failed")
            return {"id": ident, "error": {"code": -32603, "message": str(exc)}}

def _contract_consumers(graph: str, providers: list[dict[str, str]],
                        graphs_dir: str | None) -> list[dict[str, Any]]:
    """Consumers reachable via declared cross-repo contracts of providers.

    Every consumer is verified against its own store so only real symbols
    appear; the consumer's repo-relative file is resolved for read-sets.
    """
    out: list[dict[str, Any]] = []
    for provider in providers:
        for c in graphs.contracts_for_provider(graph, provider["repo"],
                                                provider["qname"], graphs_dir):
            entry = {"repo": c["consumer_repo"], "db": default_db(c["consumer_repo"])}
            graphs.ensure_indexed(entry)
            store = GraphStore(entry["db"]).connect()
            try:
                sym = store.get_symbol(c["consumer_qname"])
            finally:
                store.close()
            out.append({
                "consumer_repo": c["consumer_repo"],
                "consumer_qname": c["consumer_qname"],
                "kind": c["kind"],
                "file_path": sym["file_path"] if sym else None,
            })
    return out


def _report_dict(report: ImpactReport) -> dict[str, Any]:
    return {
        "symbol": report.root,
        "change": report.change,
        "risk": report.risk,
        "confidence": report.confidence,
        "notes": report.notes,
        "direct": [asdict(a) for a in report.direct],
        "transitive": [asdict(a) for a in report.transitive],
        "affected_files": report.affected_files,
        "suspected": report.suspected,
        "external_entry": report.external_entry,
        "external_hint": report.external_hint,
    }


def serve(repo: str | None = None, graph: str | None = None,
          graphs_dir: str | None = None) -> None:
    server = McpServer(repo=repo, graph=graph, graphs_dir=graphs_dir)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("dropping malformed JSON-RPC frame")
            continue
        response = server.handle(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
