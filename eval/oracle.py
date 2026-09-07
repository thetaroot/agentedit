"""tsc ground-truth oracle.

Compiling is the only honest ground truth for "what would this removal break":
after a mutation, TypeScript reports the exact files that fail to type-check.
Everything else (our predictions) is measured against that set.

Oracle availability: the harness installs ``typescript`` once into a tool dir
(npm required on first run), then type-checks each mutation project copy.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

_ERROR_LINE = re.compile(r"^(.+?)\(\d+,\d+\): error TS\d+:")


def ensure_tsc(work_root: str, cache_tsc: bool = True) -> str:
    """Return path to the tsc JS binary, installing typescript if needed."""
    tool = os.path.join(work_root, ".eval-tool")
    if not os.path.isdir(tool):
        os.makedirs(tool, exist_ok=True)
    tsc = os.path.join(tool, "node_modules", "typescript", "bin", "tsc")
    if not os.path.isfile(tsc):
        print("  installing typescript into eval toolchain ...", file=sys.stderr)
        proc = subprocess.run(
            ["npm", "install", "--no-save", "--prefix", tool, "typescript@^5.6.0"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"npm install typescript failed:\n{proc.stderr[-2000:]}")
    if cache_tsc and not os.path.isfile(tsc):
        raise RuntimeError("typescript install produced no tsc binary")
    return tsc


def ensure_oracle(work_root: str, kind: str) -> str:
    """Install/return the oracle runner for ``kind`` ('tsc' or 'pyright')."""
    if kind == "tsc":
        return ensure_tsc(work_root)
    if kind == "go":
        return ensure_go(work_root)
    if kind == "rust":
        return ensure_rust(work_root)
    if kind == "java":
        return ensure_java(work_root)
    return ensure_pyright(work_root)


def ensure_pyright(work_root: str) -> str:
    """Install pyright into the tool dir; return the runner command name."""
    tool = os.path.join(work_root, ".eval-tool")
    os.makedirs(tool, exist_ok=True)
    runner = os.path.join(tool, "node_modules", ".bin", "pyright")
    if not os.path.exists(runner):
        proc = subprocess.run(
            ["npm", "install", "--no-save", "--prefix", tool, "pyright"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"npm install pyright failed:\n{proc.stderr[-2000:]}")
    if not os.path.exists(runner):
        raise RuntimeError("pyright install produced no runner")
    return runner


def typecheck(project_dir: str, oracle_script: str, kind: str = "tsc") -> list[str]:
    """Type-check ``project_dir``; return repo-relative file paths with errors."""
    if kind == "go":
        return _go_error_files(project_dir, oracle_script)
    if kind == "rust":
        return _rust_error_files(project_dir, oracle_script)
    if kind == "java":
        return _java_error_files(project_dir, oracle_script)
    if kind == "pyright":
        proc = subprocess.run(
            [oracle_script, "--outputjson", "--project", project_dir],
            capture_output=True, text=True, cwd=project_dir,
        )
        files: list[str] = []
        try:
            import json as _json
            payload = _json.loads(proc.stdout)
        except Exception:
            return files
        for diag in payload.get("generalDiagnostics", []):
            if diag.get("severity") == "error":
                rel = os.path.normpath(diag.get("file", ""))
                if rel.startswith(os.path.join(project_dir, "pkg")):
                    rel = os.path.relpath(rel, project_dir)
                if rel and rel not in files:
                    files.append(rel)
        return files
    proc = subprocess.run(
        ["node", oracle_script, "-p", project_dir, "--noEmit", "--pretty", "false"],
        capture_output=True, text=True, cwd=project_dir,
    )
    output = proc.stdout + "\n" + proc.stderr
    files = []
    for line in output.splitlines():
        match = _ERROR_LINE.match(line.strip())
        if match:
            rel = os.path.normpath(match.group(1))
            if rel not in files:
                files.append(rel)
    return files


def clean_typecheck(project_dir: str, oracle_script: str, kind: str = "tsc") -> bool:
    """Baseline sanity check — corpus must compile clean before mutation."""
    files = typecheck(project_dir, oracle_script, kind=kind)
    if files:
        raise SystemExit(
            f"corpus does not type-check cleanly before mutation: {files[:10]}"
        )
    return True


def copy_tree(src: str, dst: str) -> None:
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    # never copy node_modules into mutation copies
    nm = os.path.join(dst, "node_modules")
    if os.path.isdir(nm):
        shutil.rmtree(nm)


def ensure_go(work_root: str) -> str:
    """Return the go binary; raise a clear error if not on PATH."""
    import shutil

    path = shutil.which("go")
    if path is None:
        raise RuntimeError(
            "go toolchain not found on PATH — install it or set PATH to the "
            "extracted toolchain (e.g. /tmp/opencode/go/bin) to run the Go eval."
        )
    return path


_GO_ERROR = re.compile(r"^(\S+\.go):(\d+):(\d+):")


def _go_error_files(project_dir: str, go_bin: str) -> list[str]:
    proc = subprocess.run(
        [go_bin, "build", "./..."],
        capture_output=True, text=True, cwd=project_dir,
        env={**os.environ, "GOCACHE": os.path.join(project_dir, ".gocache"), "GOFLAGS": "-mod=mod"},
    )
    output = proc.stderr + "\n" + proc.stdout
    files: list[str] = []
    for line in output.splitlines():
        match = _GO_ERROR.match(line.strip())
        if match:
            rel = os.path.normpath(match.group(1))
            if rel not in files:
                files.append(rel)
    return files


def ensure_rust(work_root: str) -> str:
    """Return the cargo binary; raise a clear error if not on PATH."""
    import shutil

    path = shutil.which("cargo")
    if path is None:
        raise RuntimeError(
            "cargo toolchain not found on PATH — install Rust or set PATH to "
            "the extracted toolchain (e.g. /tmp/opencode/cargo/bin) to run the Rust eval."
        )
    return path


def _rust_error_files(project_dir: str, cargo_bin: str) -> list[str]:
    proc = subprocess.run(
        [cargo_bin, "check"],
        capture_output=True, text=True, cwd=project_dir,
        env={
            **os.environ,
            "CARGO_TARGET_DIR": os.path.join(project_dir, ".cargo-target"),
            "RUSTFLAGS": "-Awarnings",
        },
    )
    files: list[str] = []
    for line in (proc.stderr + "\n" + proc.stdout).splitlines():
        stripped = line.strip()
        if stripped.startswith("-->"):
            rest = stripped[3:].strip()
            path = rest.split(":")[0]
            if path.endswith(".rs") and path not in files:
                files.append(os.path.normpath(path))
    return files


def ensure_java(work_root: str) -> str:
    """Return the javac binary; raise a clear error if not on PATH."""
    import shutil

    path = shutil.which("javac")
    if path is None:
        raise RuntimeError(
            "javac toolchain not found on PATH — install a JDK or set PATH to "
            "the extracted JDK bin dir to run the Java eval."
        )
    return path


def _java_error_files(project_dir: str, javac_bin: str) -> list[str]:
    java_files: list[str] = []
    for dp, _dn, fn in os.walk(project_dir):
        for f in sorted(fn):
            if f.endswith(".java"):
                rel = os.path.relpath(os.path.join(dp, f), project_dir)
                java_files.append(rel)
    out_dir = os.path.join(project_dir, ".javac-out")
    os.makedirs(out_dir, exist_ok=True)
    proc = subprocess.run(
        [javac_bin, "-d", out_dir, *java_files],
        capture_output=True, text=True, cwd=project_dir,
    )
    files: list[str] = []
    for line in (proc.stderr + "\n" + proc.stdout).splitlines():
        stripped = line.strip()
        idx = stripped.find(".java:")
        if idx > 0:
            rel = os.path.normpath(stripped[: idx + len(".java")])
            if rel not in files:
                files.append(rel)
    return files
