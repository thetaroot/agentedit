# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""CLI entrypoint.

    agentedit index .                 # build/refresh the local graph
    agentedit impact <symbol>         # who is affected by <symbol>
    agentedit would-break <symbol>    # predict breakage for a change
    agentedit changes                 # change surface of the working tree
    agentedit search <query>          # find symbols
    agentedit mcp                     # MCP stdio server
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from typing import Any

from agentedit import __version__
from agentedit.defaults import default_db
from agentedit.model import ImpactReport
from agentedit.store.sqlite import GraphStore


def _open_store(db: str) -> GraphStore:
    return GraphStore(db).connect()


def main(argv: list[str] | None = None) -> int:
    """Entry point — surfaces user-facing errors instead of raw tracebacks."""
    try:
        return _dispatch(argv)
    except sqlite3.Error as exc:
        message = f"error: {exc}"
        if "not a database" in str(exc).lower():
            message += (
                "\nThe graph database is corrupted. Delete it and re-index "
                "(e.g. `rm <repo>/.agentedit/graph.db && agentedit index <repo>`)."
            )
        print(message, file=sys.stderr)
        return 1


def _dispatch(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agentedit",
        description="Know what your code change will break before you make it.",
    )
    parser.add_argument("--version", action="version", version=f"agentedit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def _ctx(p: Any) -> None:
        """A query target: a single repo (default ``.``) or a named graph."""
        p.add_argument("--repo", default=".")
        p.add_argument("--graph", default=None,
                       help="run over every repo in this named graph (multi-repo)")
        p.add_argument("--graphs-dir", default=None,
                       help="graphs dir (default: ./.agentedit-graphs)")

    p_index = sub.add_parser("index", help="build or refresh the local code graph")
    p_index.add_argument("repo", nargs="?", default=".", help="path to the repo to index")
    p_index.add_argument("--force", action="store_true", help="re-parse every file")

    p_impact = sub.add_parser("impact", help="who depends on a symbol")
    p_impact.add_argument("qname")
    _ctx(p_impact)

    p_dep = sub.add_parser("dependents", help="direct + transitive structural dependents")
    p_dep.add_argument("qname")
    _ctx(p_dep)

    p_wb = sub.add_parser("would-break", help="predict breakage for a change")
    p_wb.add_argument("qname")
    _ctx(p_wb)
    p_wb.add_argument("--change", default="removed",
                      choices=["removed", "renamed", "signature", "type", "return_type", "members"])
    p_wb.add_argument("--new-signature", default=None)

    p_changes = sub.add_parser("changes", help="change surface of the working tree")
    p_changes.add_argument("--repo", default=".")

    p_search = sub.add_parser("search", help="search symbols by name")
    p_search.add_argument("query")
    p_search.add_argument("--repo", default=".")

    p_audit = sub.add_parser("audit", help="one-call crash-audit before an edit")
    p_audit.add_argument("target", help="symbol qname or a repo-relative file path")
    _ctx(p_audit)
    p_audit.add_argument("--limit-files", type=int, default=15)

    p_why = sub.add_parser("why", help="git rationale for a symbol (deterministic facts + candidates)")
    p_why.add_argument("qname")
    _ctx(p_why)

    p_mcp = sub.add_parser("mcp", help="run the MCP stdio server")
    p_mcp.add_argument("--repo", default=None)
    p_mcp.add_argument("--graph", default=None)
    p_mcp.add_argument("--graphs-dir", default=None)

    p_graph = sub.add_parser("graph", help="manage named graphs of repositories")
    gsub = p_graph.add_subparsers(dest="graph_cmd", required=True)

    def _graph_sub(cmd: str, help_: str) -> Any:
        p = gsub.add_parser(cmd, help=help_)
        p.add_argument("--dir", default=None, help="graphs dir (default: ./.agentedit-graphs)")
        return p

    _graph_sub("create", "create a new graph").add_argument("name")
    _graph_sub("list", "list graphs")
    _graph_sub("rm", "delete a graph").add_argument("name")
    gs_add_repo = _graph_sub("add-repo", "add a repository to a graph")
    gs_add_repo.add_argument("name")
    gs_add_repo.add_argument("repo")
    gs_rm_repo = _graph_sub("rm-repo", "remove a repository from a graph")
    gs_rm_repo.add_argument("name")
    gs_rm_repo.add_argument("repo")
    _graph_sub("inspect", "list a graph's repositories").add_argument("name")
    _graph_sub("refresh", "one watch cycle over every member").add_argument("name")
    gs_cadd = _graph_sub("contract-add", "declare a cross-repo dependency")
    gs_cadd.add_argument("name")
    gs_cadd.add_argument("--consumer-repo", required=True)
    gs_cadd.add_argument("--consumer", required=True)
    gs_cadd.add_argument("--provider-repo", required=True)
    gs_cadd.add_argument("--provider", required=True)
    gs_cadd.add_argument("--kind", default="calls",
                         choices=["calls", "imports", "uses_type", "renders"])
    _graph_sub("contract-list", "list declared cross-repo contracts").add_argument("name")
    gs_crm = _graph_sub("contract-rm", "remove a declared contract")
    gs_crm.add_argument("name")
    gs_crm.add_argument("id", type=int)

    p_watch = sub.add_parser("watch", help="keep the graph fresh and report the change surface")
    p_watch.add_argument("repo", nargs="?", default=".")
    p_watch.add_argument("--interval", type=float, default=1.0, help="poll interval in seconds")
    p_watch.add_argument("--once", action="store_true", help="run a single cycle and exit")

    p_wsadd = sub.add_parser("workspace-add", help="index a repo into the workspace")
    p_wsadd.add_argument("repo")
    p_wsadd.add_argument("--dir", default=None, help="workspace dir (default: ./.agentedit-workspace)")
    p_wslist = sub.add_parser("workspace-list", help="list workspace repos")
    p_wslist.add_argument("--dir", default=None)
    p_wssearch = sub.add_parser("workspace-search", help="search symbols across workspace repos")
    p_wssearch.add_argument("query")
    p_wssearch.add_argument("--dir", default=None)
    p_wsimpact = sub.add_parser("workspace-impact", help="impact across repos (scoped by --repo)")
    p_wsimpact.add_argument("qname")
    p_wsimpact.add_argument("--dir", default=None)
    p_wsimpact.add_argument("--repo", default=None, help="restrict to one repo slug")
    p_wswb = sub.add_parser("workspace-would-break", help="would-break across repos")
    p_wswb.add_argument("qname")
    p_wswb.add_argument("--dir", default=None)
    p_wswb.add_argument("--repo", default=None)
    p_wswb.add_argument("--change", default="removed",
                        choices=["removed", "renamed", "signature", "type", "return_type", "members"])
    p_wswb.add_argument("--new-signature", default=None)

    args = parser.parse_args(argv)

    if args.command == "index":
        from agentedit.index.indexer import index_repo

        summary = index_repo(os.path.abspath(args.repo), default_db(args.repo), force=args.force)
        language = summary.get("language")
        lang = f" ({language})" if language else ""
        unresolved = summary["unresolved"]
        breakdown = ""
        if unresolved:
            breakdown = (
                f" [{summary['unresolved_external']} external, "
                f"{summary['unresolved_in_repo']} in-repo, "
                f"{summary['unresolved_dynamic']} dynamic]"
            )
        print(
            f"indexed{lang} {summary['files']} files -> {summary['symbols']} symbols, "
            f"{summary['edges']} edges "
            f"({summary['changed']} changed, {summary['reconciled']} reconciled, "
            f"{unresolved} unresolved{breakdown}, {summary['duration_s']}s)"
        )
        return 0

    if args.command == "mcp":
        from agentedit.mcp_server import serve

        if args.graph:
            serve(graph=args.graph, graphs_dir=args.graphs_dir)
        else:
            serve(repo=os.path.abspath(args.repo or "."))
        return 0

    if args.command == "graph":
        from agentedit import graphs

        if args.graph_cmd == "create":
            graphs.create(args.name, args.dir)
            print(f"created graph '{args.name}'")
            return 0
        if args.graph_cmd == "list":
            for name in graphs.list_graphs(args.dir):
                print(name)
            return 0
        if args.graph_cmd == "rm":
            print("removed" if graphs.delete(args.name, args.dir) else f"graph not found: {args.name}")
            return 0
        if args.graph_cmd == "add-repo":
            graphs.add_repo(args.name, os.path.abspath(args.repo), args.dir)
            print(f"added repo '{os.path.abspath(args.repo)}' to graph '{args.name}'")
            return 0
        if args.graph_cmd == "rm-repo":
            print("removed" if graphs.remove_repo(args.name, os.path.abspath(args.repo), args.dir)
                  else "repo not in graph")
            return 0
        if args.graph_cmd == "inspect":
            info = graphs.inspect(args.name, args.dir)
            print(f"graph {info['name']} ({len(info['repos'])} repo(s))")
            for r in info["repos"]:
                print(f"  {r}")
            return 0
        if args.graph_cmd == "refresh":
            result = graphs.refresh_once(args.name, args.dir)
            changed = 0
            for member in result["members"]:
                if member["changed_files"]:
                    changed += len(member["changed_files"])
                    print(f"[{member['repo']}] {len(member['changed_files'])} file(s) changed: "
                          f"{', '.join(member['changed_files'])}")
                    for a in member["affected"]:
                        print(f"  {a['relation']:<10} {a['qname']}  ({a['file_path']})")
            if changed == 0:
                print(f"graph '{args.name}' fresh: {len(result['repos'])} repo(s), no changes")
            return 0
        if args.graph_cmd == "contract-add":
            cid = graphs.add_contract(
                args.name,
                consumer_repo=os.path.abspath(args.consumer_repo),
                consumer_qname=args.consumer,
                provider_repo=os.path.abspath(args.provider_repo),
                provider_qname=args.provider,
                kind=args.kind, graphs_dir=args.dir,
            )
            print(f"contract {cid}: {args.consumer} ({args.consumer_repo}) -> "
                  f"{args.provider} ({args.provider_repo}) [{args.kind}]")
            return 0
        if args.graph_cmd == "contract-list":
            for c in graphs.list_contracts(args.name, args.dir):
                print(f"{c['id']}: {c['consumer_qname']} ({c['consumer_repo']}) "
                      f"[{c['kind']}] -> {c['provider_qname']} ({c['provider_repo']})")
            return 0
        if args.graph_cmd == "contract-rm":
            print("removed" if graphs.remove_contract(args.name, args.id, args.dir)
                  else f"contract {args.id} not found")
            return 0

    if args.command == "watch":
        from agentedit.watch import watch

        return watch(os.path.abspath(args.repo), interval=args.interval, once=args.once)

    if args.command == "workspace-add":
        from agentedit.multirepo import add_repo

        slug = add_repo(args.repo, args.dir)
        print(f"added repo '{os.path.abspath(args.repo)}' as '{slug}'")
        return 0
    if args.command == "workspace-list":
        from agentedit.multirepo import list_repos

        repos = list_repos(args.dir)
        if not repos:
            print("workspace is empty")
        for r in repos:
            print(f"{r['slug']:<24} {r['path']}")
        return 0
    if args.command == "workspace-search":
        from agentedit.multirepo import search

        for r in search(args.query, args.dir):
            print(f"[{r['repo']}] {r['kind']:<12} {r['qname']}")
        return 0
    if args.command in ("workspace-impact", "workspace-would-break"):
        from agentedit.multirepo import run_impact

        mode = "would_break" if args.command == "workspace-would-break" else "impact"
        reports = run_impact(args.qname, args.dir, slug_filter=args.repo,
                             change=getattr(args, "change", "removed"),
                             new_signature=getattr(args, "new_signature", None), mode=mode)
        for rep in reports:
            print(f"[{rep['repo']}] {rep['symbol']} risk={rep['risk']} "
                  f"conf={rep['confidence']} direct={len(rep['direct'])}")
            for a in rep["direct"]:
                print(f"    [{a['edge_kind']:<8}] {a['qname']}  ({a['file_path']})")
        if not reports:
            print("no repo contains this symbol")
        return 0

    if getattr(args, "graph", None) and args.command in (
        "impact", "dependents", "would-break", "audit", "why",
    ):
        return _run_graph_mode(args)

    db = default_db(args.repo)
    if not os.path.isfile(db):
        target = args.repo if args.repo != "." else "<repo-path>"
        print(f"no index at {db}.", file=sys.stderr)
        print(
            f"Build it once with:  agentedit index {target}\n"
            "Then query that same repo (e.g. with --repo <repo-path>).",
            file=sys.stderr,
        )
        return 1
    store = _open_store(db)
    try:
        if args.command == "impact":
            from agentedit.analyze.impact import impact

            _print_report(impact(store, args.qname))
        elif args.command == "dependents":
            from agentedit.analyze.impact import dependents

            _print_report(dependents(store, args.qname))
        elif args.command == "would-break":
            from agentedit.analyze.impact import would_break

            _print_report(would_break(store, args.qname, change=args.change,
                                      new_signature=args.new_signature))
        elif args.command == "changes":
            from agentedit.analyze.changes import changes

            _print_changes(changes(store, os.path.abspath(args.repo)))
        elif args.command == "search":
            for r in store.search_symbols(args.query, limit=25):
                print(f"{r['kind']:<12} {r['qname']}")
        elif args.command == "audit":
            from agentedit.audit import audit

            _print_audit(audit(store, os.path.abspath(args.repo), args.target,
                               limit_files=args.limit_files))
        elif args.command == "why":
            from agentedit.audit import why

            _print_why(why(store, os.path.abspath(args.repo), args.qname))
        return 0
    finally:
        store.close()


def _print_audit(aud: dict[str, Any]) -> None:
    if aud.get("notes"):
        print(f"AUDIT {aud['target']}: {'; '.join(aud['notes'])}")
        return
    print(f"AUDIT   target={aud['target']} ({aud['kind']}, {aud.get('symbols_audited', '')} syms)"
          f" risk={aud['risk']} conf={aud['confidence']}")
    if aud.get("external_entry"):
        hint = aud.get("external_hint") or "(decorator)"
        print(f"        EXTERNAL framework/plugin entry — {hint}")
    print(f"        {aud['affected_files_count']} affected file(s)")
    for f in aud["files"]:
        print(f"  [{','.join(f['relations']):<10}] {f['file']} conf={f['confidence']}")
    if aud.get("suspected"):
        print(f"SUSPECTED unresolved name-matches: {len(aud['suspected'])}")
        for s in aud["suspected"][:8]:
            print(f"  {s.get('source_qname', '')} -> {s.get('target_text', '')} ({s.get('bucket', '?')})")
    for note in aud.get("symbol_notes") or []:
        print(f"NOTE     {note}")
    print("READ SET")
    for path in aud["read_set"]:
        print(f"  {path}")
    why = aud.get("why") or {}
    if why.get("facts"):
        print(f"GIT {len(why['facts'])} commit(s) touch this code")
        for f in why["facts"][:5]:
            print(f"  {f['date']} {f['commit'][:8]} {f['author']}: {f['subject']}")
    print("RESOLUTION", aud.get("resolution", {}))


def _print_why(result: dict[str, Any]) -> None:
    print(f"WHY     {result['symbol']} ({result.get('file', '-')} "
          f"lines {result.get('lines', [])})")
    if result.get("notes"):
        print(f"        {'; '.join(result['notes'])}")
        return
    if not result["git"]:
        print("        repo has no git history")
    for f in result["facts"]:
        print(f"  {f['date']} {f['commit'][:8]} {f['author']}: {f['subject']}")
    for c in result["candidates"]:
        print(f"  candidate[git] {c['text']}")


def _print_report(rep: ImpactReport) -> None:
    print(f"SYMBOL   {rep.root or '-'}")
    print(f"CHANGE   {rep.change or '-'}")
    print(f"RISK     {rep.risk}   (confidence {rep.confidence})")
    if rep.external_entry:
        hint = rep.external_hint or "(decorator)"
        print(f"EXTERNAL framework/plugin entry — {hint}")
    for note in rep.notes:
        print(f"NOTE     {note}")
    print(f"{'-' * 78}")
    print("DIRECT DEPENDENTS")
    for a in rep.direct:
        print(
            f"  [{a.edge_kind:<8}] {a.qname}  "
            f"({a.kind}, {a.file_path}) conf={a.confidence}"
        )
        if a.evidence:
            print(f"            {a.evidence}")
    print("TRANSITIVE / FILE-LEVEL")
    for a in rep.transitive:
        print(f"  [{a.edge_kind:<8}] {a.qname}  ({a.file_path}) conf={a.confidence}")
    if rep.suspected:
        print("SUSPECTED (unresolved, name-based — verify, not edges)")
        for s in rep.suspected[:12]:
            print(
                f"  [unresolved {s.get('bucket', '?')}] {s.get('source_qname', '')}  "
                f"-> {s.get('target_text', '')}  ({s.get('file_path', '')})"
            )


def _print_changes(report: dict[str, Any]) -> None:
    changed: list[str] = report["changed_files"]
    affected: list[dict[str, Any]] = report["affected"]
    print(f"{len(changed)} changed files")
    for path in changed:
        print(f"  ~ {path}")
    print("AFFECTED ELSEWHERE")
    for item in affected:
        print(f"  [{item['relation']:<10}] {item['qname']}  ({item['file_path']})")
    print("TOTAL AFFECTED FILES", len(report["affected_files"]))


# ---------------------------------------------------------------------------
# Graph mode (multi-repo): one query fanned out over every member repo.
# Answers are repo-tagged; cross-repo reach exists only via declared,
# verified contracts — never guessed.
# ---------------------------------------------------------------------------


def _run_graph_mode(args: Any) -> int:
    from agentedit import graphs
    from agentedit.analyze.impact import dependents, impact, would_break
    from agentedit.audit import audit as audit_run
    from agentedit.audit import why as why_run
    from agentedit.index.indexer import index_repo
    from agentedit.store.sqlite import GraphStore

    try:
        entries = graphs.entries(args.graph, args.graphs_dir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not entries:
        print(f"graph '{args.graph}' is empty", file=sys.stderr)
        return 1

    command = args.command
    matched: list[tuple[str, dict[str, Any]]] = []
    for entry in entries:
        if not os.path.isfile(entry["db"]):
            index_repo(entry["repo"], entry["db"])
        store = GraphStore(entry["db"]).connect()
        try:
            repo = entry["repo"]
            if command in ("impact", "dependents", "would-break", "why"):
                if store.get_symbol(args.qname) is None:
                    continue
            elif command == "audit" and (
                store.get_symbol(args.target) is None and not store.symbols_in_file(args.target)
            ):
                continue
            if command == "impact":
                payload: dict[str, Any] = {"report": impact(store, args.qname)}
            elif command == "dependents":
                payload = {"report": dependents(store, args.qname)}
            elif command == "would-break":
                payload = {"report": would_break(store, args.qname, change=args.change,
                                                 new_signature=args.new_signature)}
            elif command == "audit":
                payload = {"audit": audit_run(store, repo, args.target,
                                              limit_files=args.limit_files)}
            else:
                payload = {"why": why_run(store, repo, args.qname)}
            matched.append((repo, payload))
        finally:
            store.close()

    if not matched:
        what = args.qname if command != "audit" else args.target
        print(f"no member of graph '{args.graph}' contains {what}")
        return 0

    for idx, (repo, payload) in enumerate(matched):
        if idx:
            print()
        print(f"[{repo}]")
        if "report" in payload:
            _print_report(payload["report"])
        elif "audit" in payload:
            _print_audit(payload["audit"])
        else:
            _print_why(payload["why"])

    # Cross-repo consumers: providers found in this graph and reached by a
    # declared contract from a *different* member repo.
    if command in ("impact", "dependents", "would-break"):
        providers = [{"repo": repo, "qname": args.qname} for repo, _ in matched]
        consumers = _graph_contract_consumers(graphs, args.graph, providers,
                                              args.graphs_dir)
        if consumers:
            print("\nCONTRACT CONSUMERS (cross-repo, declared)")
            for c in consumers:
                print(f"  {c['consumer_qname']} ({c['consumer_repo']}) "
                      f"[{c['kind']}] -> {args.qname}  ({c['file_path'] or '-'})")
    elif command == "audit":
        providers = [
            {"repo": repo, "qname": payload["audit"]["target"]}
            for repo, payload in matched
            if payload["audit"].get("kind") == "symbol"
        ]
        if providers:
            target = providers[0]["qname"]
            consumers = _graph_contract_consumers(graphs, args.graph, providers,
                                                  args.graphs_dir)
            if consumers:
                print("\nCONTRACT CONSUMERS (cross-repo, declared)")
                for c in consumers:
                    print(f"  {c['consumer_qname']} ({c['consumer_repo']}) "
                          f"[{c['kind']}] -> {target}  ({c['file_path'] or '-'})")
    return 0


def _graph_contract_consumers(graphs: Any, graph: str,
                              providers: list[dict[str, str]],
                              graphs_dir: str | None) -> list[dict[str, Any]]:
    """Consumers reached from ``providers`` by declared cross-repo contracts,
    verified to be real symbols in their own member store."""
    from agentedit.defaults import default_db
    from agentedit.index.indexer import index_repo
    from agentedit.store.sqlite import GraphStore

    out: list[dict[str, Any]] = []
    for provider in providers:
        for c in graphs.contracts_for_provider(graph, provider["repo"],
                                               provider["qname"], graphs_dir):
            if c["consumer_repo"] == provider["repo"]:
                continue
            entry = {"repo": c["consumer_repo"], "db": default_db(c["consumer_repo"])}
            if not os.path.isfile(entry["db"]):
                index_repo(entry["repo"], entry["db"])
            store = GraphStore(entry["db"]).connect()
            try:
                sym = store.get_symbol(c["consumer_qname"])
            finally:
                store.close()
            if sym is not None:
                out.append({
                    "consumer_repo": c["consumer_repo"],
                    "consumer_qname": c["consumer_qname"],
                    "kind": c["kind"],
                    "file_path": sym["file_path"],
                })
    return out


if __name__ == "__main__":
    sys.exit(main())
