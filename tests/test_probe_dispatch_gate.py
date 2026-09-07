"""CI gate: engine-v4 dispatch/inheritance probe must stay 1.0/1.0."""
from __future__ import annotations

from typing import cast

from eval.probe_dispatch import run


def test_dispatch_probe_gate() -> None:
    results = run()
    for label, raw in results.items():
        assert cast(float, raw["recall"]) == 1.0, f"{label} recall dropped: {raw}"
        assert cast(float, raw["precision"]) == 1.0, f"{label} precision dropped: {raw}"
