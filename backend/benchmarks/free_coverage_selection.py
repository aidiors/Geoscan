"""Experimental selections with explicit endpoint protection against the reference."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

PROTECTED_METRICS = (
    "flight_minutes",
    "deadhead_minutes",
    "departure_minutes",
    "return_minutes",
)


def endpoint_protected_choice(rows: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Minimize flight time without moving cost into either endpoint transfer.

    This is an additional experiment, separate from the minimum-flight result.
    Comparisons must contain exactly one case and one valid reference. If the
    reference is invalid, no relative endpoint guarantee can be claimed.
    """
    if len({row["case_id"] for row in rows}) != 1:
        raise ValueError("Endpoint selection requires one frozen case")
    references = [row for row in rows if row["family"] == "production_reference"]
    if len(references) != 1:
        raise ValueError("Endpoint selection requires one reference")
    baseline = references[0]
    if not baseline["screen_passed"]:
        return None
    feasible = [
        row
        for row in rows
        if row["screen_passed"]
        and all(row[key] <= baseline[key] + 1e-8 for key in PROTECTED_METRICS)
    ]
    return min(feasible, key=lambda row: (row["flight_minutes"], row["deadhead_minutes"]))
