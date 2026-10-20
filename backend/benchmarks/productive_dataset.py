"""Replay frozen benchmark inputs without rebuilding the production reference grid."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from benchmarks.productive_model import Aircraft, Problem, Track, Trip
from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.enums import OptimizationCriterion
from geoplan.domain.environment import WindCondition
from geoplan.domain.geo import Coordinate


def load_cases(path: Path) -> list[tuple[Problem, dict[str, object]]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for entry in report["cases"]:
        value = entry["problem"]
        if set(value) != {field.name for field in fields(Problem)}:
            raise ValueError("Unsupported serialized Problem fields.")
        tracks = tuple(
            Track(
                t["track_id"],
                Coordinate.model_validate(t["start"]),
                Coordinate.model_validate(t["end"]),
                t["survey_speed_kmh"],
            )
            for t in value["tracks"]
        )
        fleet = tuple(
            Aircraft(
                p["drone_id"],
                p["speed_kmh"],
                p["available_minutes"],
                Coordinate.model_validate(p["home"]),
                p["turnaround_minutes"],
                frozenset(p["eligible_tracks"]) if p["eligible_tracks"] is not None else None,
                Coordinate.model_validate(p["landing"]) if p.get("landing") is not None else None,
            )
            for p in value["aircraft"]
        )
        trips = tuple(Trip(t["aircraft"], tuple(t["visits"])) for t in value["baseline"])
        problem = Problem(
            value["case_id"],
            tracks,
            fleet,
            trips,
            value["maximum_internal_distance_km"],
            WindCondition.model_validate(value["wind"]) if value["wind"] is not None else None,
            tuple(NoFlyZone.model_validate(z) for z in value["exclusions"]),
            OptimizationCriterion(value["criterion"]),
            value["deadline_minutes"],
            value["source"],
        )
        cases.append((problem, entry["production"]))
    return cases
