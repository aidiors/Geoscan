"""Deterministic reference/holdout geometries and frozen experiment inputs."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

from shapely.affinity import rotate, scale
from shapely.geometry import LineString, Point, Polygon

from geoplan.coverage.models import CoverageArea, CoveragePlanRequest
from geoplan.domain.airspace import NoFlyZone
from geoplan.domain.environment import WindCondition
from geoplan.navigation.geometry import LocalProjection
from geoplan.routing.freeform import GeometryCase, GeometryModel
from scenarios.coverage import (
    _survey_parameters,
    default_coverage_benchmark_cases,
    geometry_stress_coverage_benchmark_cases,
)


def cases() -> list[GeometryCase]:
    result = []
    source = [*default_coverage_benchmark_cases(), *geometry_stress_coverage_benchmark_cases()]
    for case in source:
        if case.case_id not in ("axis_aligned", "rotated_35", "concave_l", "round_32"):
            continue
        parameters = case.request.parameters.model_copy(
            update={
                "maximum_segment_distance_km": 100,
                "sensor": case.request.parameters.sensor.model_copy(
                    update={"minimum_trigger_interval_s": 2}
                ),
            }
        )
        request = case.request.model_copy(update={"parameters": parameters})
        probe = GeometryModel(GeometryCase(case.case_id, request, (0, 0), (0, 0)))
        min_x, min_y, _, _ = probe.target.bounds
        home = (min_x - 120, min_y - 120)
        result.append(GeometryCase(case.case_id, request, home, home))
    projection = LocalProjection(37.62, 55.75)
    parameters = _survey_parameters()
    parameters = parameters.model_copy(
        update={"sensor": parameters.sensor.model_copy(update={"minimum_trigger_interval_s": 2})}
    )
    # A long, turning corridor and an internal obstacle exercise topology that
    # cannot be represented by one uncomplicated rectangular sweep.
    corridor = LineString([(-900, -400), (-300, -400), (-300, 200), (600, 200)]).buffer(
        120, cap_style="flat", join_style="mitre"
    )
    request = CoveragePlanRequest(
        area=CoverageArea(
            area_id="bent-corridor",
            name="Bent corridor",
            coordinates=[
                projection.unproject(x, y) for x, y in list(corridor.exterior.coords)[:-1]
            ],
        ),
        mission_altitude_m=100,
        parameters=parameters,
    )
    probe = GeometryModel(GeometryCase("bent_corridor", request, (0, 0), (0, 0)))
    min_x, min_y, _, _ = probe.target.bounds
    home = (min_x - 120, min_y - 120)
    result.append(GeometryCase("bent_corridor", request, home, home))
    outer = [(-700, -450), (700, -450), (700, 450), (-700, 450)]
    zone = [(-120, -120), (120, -120), (120, 120), (-120, 120)]
    request = CoveragePlanRequest(
        area=CoverageArea(
            area_id="obstacle",
            name="Internal obstacle",
            coordinates=[projection.unproject(*p) for p in outer],
        ),
        mission_altitude_m=100,
        parameters=parameters,
        exclusions=[
            NoFlyZone(
                zone_id="obstacle",
                safety_margin_m=20,
                coordinates=[projection.unproject(*p) for p in zone],
            )
        ],
    )
    probe = GeometryModel(GeometryCase("obstacle", request, (0, 0), (0, 0)))
    min_x, min_y, _, _ = probe.target.bounds
    home = (min_x - 120, min_y - 120)
    result.append(GeometryCase("obstacle", request, home, home))
    base = result[0]
    probe = GeometryModel(base)
    _, _, max_x, max_y = probe.target.bounds
    result.append(replace(base, case_id="different_landing", landing=(max_x + 120, max_y + 120)))
    result.append(
        replace(
            base,
            case_id="wind_6mps",
            request=base.request.model_copy(
                update={"wind": WindCondition(speed_mps=6, direction_degrees=230)}
            ),
        )
    )
    return result


def load_inputs(path: Path) -> list[GeometryCase]:
    result = []
    for item in json.loads(path.read_text(encoding="utf-8")):
        case = GeometryCase(
            item["case_id"],
            CoveragePlanRequest.model_validate(item["coverage_request"]),
            tuple(item["home_local_m"]),
            tuple(item["landing_local_m"]),
            item["cruise_speed_kmh"],
            item["endurance_minutes"],
            item["reserve_percent"],
        )
        if asdict(GeometryModel(case).projection) != item["projection"]:
            raise ValueError("Frozen coordinate system differs from the current projection")
        result.append(case)
    return result


def holdout_cases() -> list[GeometryCase]:
    """New shapes, fixed before measuring winners; camera defaults to one second."""
    projection = LocalProjection(37.62, 55.75)
    circle = Point(0, 0).buffer(720, quad_segs=16)
    ellipse = rotate(scale(circle, xfact=1.15, yfact=0.62), 27, origin=(0, 0))
    shapes = [
        ("holdout_circle64", circle, False),
        ("holdout_ellipse27", ellipse, False),
        ("holdout_circle_cross_sites", circle, True),
        ("holdout_ellipse_cross_sites", ellipse, True),
        (
            "holdout_u_shape",
            Polygon(
                [
                    (-700, -600),
                    (700, -600),
                    (700, 600),
                    (350, 600),
                    (350, -200),
                    (-350, -200),
                    (-350, 600),
                    (-700, 600),
                ]
            ),
            False,
        ),
        (
            "holdout_neck_cross_sites",
            Polygon(
                [
                    (-850, -400),
                    (-150, -400),
                    (-150, -80),
                    (150, -80),
                    (150, -400),
                    (850, -400),
                    (850, 400),
                    (150, 400),
                    (150, 80),
                    (-150, 80),
                    (-150, 400),
                    (-850, 400),
                ]
            ),
            True,
        ),
    ]
    result = []
    for name, shape, cross_sites in shapes:
        request = CoveragePlanRequest(
            area=CoverageArea(
                area_id=name,
                name=name,
                coordinates=[
                    projection.unproject(x, y) for x, y in list(shape.exterior.coords)[:-1]
                ],
            ),
            mission_altitude_m=100,
            parameters=_survey_parameters(),
        )
        probe = GeometryModel(GeometryCase(name, request, (0, 0), (0, 0)))
        min_x, min_y, max_x, max_y = probe.target.bounds
        if cross_sites:
            home = (min_x - 140, (min_y + max_y) / 2)
            landing = (max_x + 140, (min_y + max_y) / 2)
        else:
            home = (min_x - 140, min_y - 140)
            landing = home
        result.append(GeometryCase(name, request, home, landing))
    return result


def dataset(suite: str, trigger_interval_s: float) -> list[GeometryCase]:
    if suite not in ("reference", "holdout", "all") or trigger_interval_s <= 0:
        raise ValueError("Unknown suite or nonpositive camera interval")
    selected = ([] if suite == "holdout" else cases()) + (
        [] if suite == "reference" else holdout_cases()
    )
    return [
        replace(
            case,
            request=case.request.model_copy(
                update={
                    "parameters": case.request.parameters.model_copy(
                        update={
                            "sensor": case.request.parameters.sensor.model_copy(
                                update={
                                    "minimum_trigger_interval_s": trigger_interval_s,
                                }
                            ),
                        }
                    ),
                }
            ),
        )
        for case in selected
    ]
