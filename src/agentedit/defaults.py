# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""Shared path conventions (thin module so cli and mcp do not import each other)."""
from __future__ import annotations

import os
from pathlib import Path


def default_db(repo: str) -> str:
    """Default graph location for a repo: ``<repo>/.agentedit/graph.db``.

    ``AGENTEDIT_DB`` overrides it (used for tests and the Docker image).
    """
    env = os.environ.get("AGENTEDIT_DB")
    if env:
        return env
    return str(Path(repo) / ".agentedit" / "graph.db")
