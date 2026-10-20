"""Synthetic controls and production-generated tracks with real assignment baselines."""

from __future__ import annotations

from dataclasses import asdict, replace
from itertools import pairwise

from shapely.affinity import rotate

from benchmarks.productive_model import Aircraft, CostModel, Problem, Track, Trip
from geoplan.assignment.models import AssignmentOptimizationRequest
from geoplan.composition import create_default_dependencies
from geoplan.coverage.models import CoveragePlanRequest
from geoplan.coverage.sweep import (
    BoustrophedonCoveragePlanner,
    _flyable_geometry,
    _sweep_rows,
    _unproject_route,
)
from geoplan.domain.enums import (
    MissionStageType,
    MissionTask,
    OptimizationCriterion,
    SweepDirectionMode,
)
from geoplan.domain.environment import WindCondition
from geoplan.domain.fleet import DroneSpec, FlightSite
from geoplan.domain.geo import Coordinate
from geoplan.errors import PlanningError
from geoplan.flight.energy import _effective_endurance_minutes
from geoplan.navigation.geometry import LocalProjection
from geoplan.planning.candidates import _create_candidate_groups
from geoplan.planning.models import PlanRequest
from scenarios.coverage import default_coverage_benchmark_cases


def tiny_problems() -> list[Problem]:
    projection = LocalProjection(origin_longitude=37, origin_latitude=55)
    home = projection.unproject(0, 0)
    tracks = tuple(
        Track(str(i), projection.unproject(1_000, y), projection.unproject(2_000, y), 45)
        for i, y in enumerate((0, 180, 360, 540))
    )
    aircraft = (
        Aircraft("slow", 50, 30, home),
        Aircraft("fast", 90, 30, home),
    )
    baseline = tuple(Trip(i % 2, (2 * i,)) for i in range(len(tracks)))
    base = Problem("tiny_merge", tracks, aircraft, baseline, 8)
    limited = replace(base, case_id="tiny_capacity", maximum_internal_distance_km=1.5)
    eligible = replace(
        base,
        case_id="tiny_eligibility",
        aircraft=(
            replace(aircraft[0], eligible_tracks=frozenset((0, 2))),
            replace(aircraft[1], eligible_tracks=frozenset((1, 3))),
        ),
    )
    windy = replace(
        base,
        case_id="tiny_wind",
        wind=WindCondition(
            speed_mps=6,
            direction_degrees=90,
        ),
    )
    return [base, limited, eligible, windy]


def production_problems() -> list[tuple[Problem, dict[str, object]]]:
    results = []
    angles = {
        "axis_aligned": 90,
        "rotated_35": 55,
        "diagonal_corridor": 23,
        "concave_l": 62,
        "two_restrictions": 68,
        "long_corridor_43": 47,
    }
    for case in default_coverage_benchmark_cases():
        if case.case_id not in angles:
            continue
        parameters = case.request.parameters.model_copy(
            update={
                "direction_mode": SweepDirectionMode.MANUAL,
                "flight_direction_degrees": angles[case.case_id],
                "maximum_segment_distance_km": 6,
            }
        )
        request = case.request.model_copy(update={"parameters": parameters})
        geometry, projection = _flyable_geometry(request)
        min_x, min_y, _, _ = geometry.bounds
        home = projection.unproject(min_x - 1_000, min_y - 500)
        drones = [
            DroneSpec(
                drone_id=f"uav-{i}",
                name=f"UAV {i}",
                cruise_speed_kmh=speed,
                maximum_flight_minutes=60,
                battery_reserve_percent=20,
                minimum_turnaround_minutes=2,
                supported_tasks=[MissionTask.RGB],
            )
            for i, speed in enumerate((50, 70, 90))
        ]
        site = FlightSite(site_id="home", name="Home", coordinate=home)
        plan = PlanRequest(
            criterion=OptimizationCriterion.TOTAL_FLIGHT_TIME,
            drones=drones,
            coverage_areas=[request.area],
            survey_parameters=parameters,
            mission_altitude_m=request.mission_altitude_m,
            task=MissionTask.RGB,
            departure_site=site,
            landing_site=site,
            no_fly_zones=request.exclusions,
        )
        results.extend(_generated_problems(case.case_id, request, plan, 25))
    return results


def scale_problems(
    *,
    internal_distance_km: float = 25,
    case_names: tuple[str, ...] | None = None,
    case_suffix: str = "",
    sortie_distance_limited: bool = True,
) -> list[tuple[Problem, dict[str, object]]]:
    from benchmarks.acceptance_scale import _base_request, _central_no_fly_zone, _square_area

    projection = LocalProjection(origin_longitude=37.62, origin_latitude=55.75)
    base = _base_request(_square_area(projection))
    parameters = base.survey_parameters
    assert parameters is not None
    parameters = parameters.model_copy(
        update={
            "direction_mode": SweepDirectionMode.MANUAL,
            "maximum_segment_distance_km": internal_distance_km,
        }
    )
    site = FlightSite(
        site_id="scale-home", name="Scale home", coordinate=projection.unproject(-6_000, -5_500)
    )
    base = base.model_copy(
        update={
            "criterion": OptimizationCriterion.TOTAL_FLIGHT_TIME,
            "survey_parameters": parameters,
            "departure_site": site,
            "landing_site": site,
        }
    )
    results = []
    for name, zones, wind in (
        ("scale_flat", [], None),
        ("scale_wind", [], WindCondition(speed_mps=5, direction_degrees=230)),
        ("scale_restriction", [_central_no_fly_zone(projection)], None),
    ):
        if case_names is not None and name not in case_names:
            continue
        plan = base.model_copy(update={"no_fly_zones": zones, "wind": wind})
        request = CoveragePlanRequest(
            area=plan.coverage_areas[0],
            mission_altitude_m=plan.mission_altitude_m,
            parameters=parameters,
            exclusions=zones,
            wind=wind,
        )
        results.extend(
            _generated_problems(
                name + case_suffix,
                request,
                plan,
                400,
                sortie_distance_limited=sortie_distance_limited,
            )
        )
    return results


def sensitivity_problems() -> list[tuple[Problem, dict[str, object]]]:
    return [
        case
        for limit in (25, 50, 100, 200)
        for case in scale_problems(
            internal_distance_km=limit,
            case_names=("scale_flat",),
            case_suffix=f"_cap{limit}",
        )
    ]


def adaptive_problems() -> list[tuple[Problem, dict[str, object]]]:
    """A strong reference grid under common battery-only sortie constraints."""
    candidates: dict[
        tuple[str, OptimizationCriterion], list[tuple[Problem, dict[str, object]]]
    ] = {}
    skipped: dict[str, list[dict[str, object]]] = {}
    for name in ("scale_flat", "scale_wind", "scale_restriction"):
        for limit in (25, 50, 100, 150, 200, 225, 250):
            try:
                generated = scale_problems(
                    internal_distance_km=limit, case_names=(name,), sortie_distance_limited=False
                )
            except PlanningError as error:
                skipped.setdefault(name, []).append(
                    {"segment_limit_km": limit, "reason": str(error)}
                )
                continue
            for problem, metadata in generated:
                candidates.setdefault((name, problem.criterion), []).append((problem, metadata))
    results = []
    for (name, criterion), variants_ in candidates.items():
        first = variants_[0][0]
        context = replace(
            first,
            case_id=name + "_physical",
            maximum_internal_distance_km=None,
            source="physical_endurance_reference_grid",
        )
        model = CostModel(context)
        reference_rows = []
        for problem, source_metadata in variants_:
            if problem.tracks != first.tracks or problem.aircraft != first.aircraft:
                raise ValueError("The reference grid changed tracks or aircraft.")
            metrics = model.metrics(problem.baseline)
            if metrics is None:
                raise ValueError("A reference grid plan violates the common physical constraints.")
            reference_rows.append(
                {
                    "segment_limit_km": source_metadata["generator_segment_limit_km"],
                    "metrics": metrics,
                    "solution": [asdict(t) for t in problem.baseline],
                }
            )
        best, metadata = min(variants_, key=lambda p: model.score(p[0].baseline))
        results.append(
            (
                replace(context, baseline=best.baseline),
                {
                    **metadata,
                    "reference_grid": reference_rows,
                    "selected_segment_limit_km": metadata["generator_segment_limit_km"],
                    "reference_skipped": skipped.get(name, []),
                    "criterion": criterion.value,
                },
            )
        )
    return results


def _generated_problems(
    case_id: str,
    request: CoveragePlanRequest,
    plan: PlanRequest,
    deadline: float,
    *,
    sortie_distance_limited: bool = True,
) -> list[tuple[Problem, dict[str, object]]]:
    """Freeze original sweep rows; reject any baseline with missing or split tracks."""
    dependencies = create_default_dependencies()
    coverage = BoustrophedonCoveragePlanner().plan(request)
    geometry, projection = _flyable_geometry(request)
    rotation = coverage.selected_flight_direction_degrees - 90
    center = float(geometry.centroid.x), float(geometry.centroid.y)
    parameters = request.parameters
    swath = min(
        request.mission_altitude_m
        * parameters.sensor.sensor_width_mm
        / parameters.sensor.focal_length_mm,
        parameters.strip_width_m,
    )
    spacing = swath * (1 - parameters.side_overlap_percent / 100)
    rows = _sweep_rows(rotate(geometry, rotation, origin="centroid"), spacing)
    speed = coverage.segments[0].survey_speed_limit_kmh
    assert speed is not None
    tracks = []
    for row in rows:
        for line in row:
            points = _unproject_route(
                [(float(x), float(y)) for x, y in line.coords], rotation, center, projection
            )
            tracks.append(Track(str(len(tracks)), points[0], points[-1], speed))
    groups = _create_candidate_groups(
        coverage.segments,
        plan.drones,
        plan,
        dependencies.duration_calculator,
        dependencies.route_optimizer,
        dependencies.sweep_transition_planner,
        dependencies.vertical_profile_planner,
    )
    lookup = {}
    for index, track in enumerate(tracks):
        lookup[(_key(track.start), _key(track.end))] = 2 * index
        lookup[(_key(track.end), _key(track.start))] = 2 * index + 1
    assert plan.departure_site is not None and plan.landing_site == plan.departure_site
    fleet = tuple(
        Aircraft(
            d.drone_id,
            d.cruise_speed_kmh,
            _effective_endurance_minutes(d)
            * (d.initial_charge_percent - d.battery_reserve_percent)
            / 100,
            plan.departure_site.coordinate,
            max(plan.turnaround_minutes, d.minimum_turnaround_minutes),
        )
        for d in plan.drones
    )
    plane_ids = {plane.drone_id: i for i, plane in enumerate(fleet)}
    results = []
    for criterion in OptimizationCriterion:
        assignment = dependencies.assignment_optimizer.optimize(
            AssignmentOptimizationRequest(
                criterion=criterion,
                drone_ids=list(plane_ids),
                candidate_groups=groups,
                completion_deadline_minutes=deadline,
                turnaround_minutes_by_drone={p.drone_id: p.turnaround_minutes for p in fleet},
            )
        )
        trips = []
        for candidate in assignment.assignments:
            visits = tuple(
                lookup[(_key(a), _key(b))]
                for stage in candidate.mission.stages
                if stage.stage_type is MissionStageType.SURVEY
                for a, b in pairwise(stage.coordinates)
                if (_key(a), _key(b)) in lookup
            )
            trips.append(Trip(plane_ids[candidate.drone_id], visits))
        problem = Problem(
            case_id,
            tuple(tracks),
            fleet,
            tuple(trips),
            parameters.maximum_segment_distance_km if sortie_distance_limited else None,
            exclusions=tuple(request.exclusions),
            wind=request.wind,
            criterion=criterion,
            deadline_minutes=deadline,
            source="production_sweep_candidates_and_assignment",
        )
        if not CostModel(problem).validate_geometry(problem.baseline):
            raise ValueError(f"Baseline track extraction/cost mismatch: {case_id}")
        results.append(
            (
                problem,
                {
                    "assignment_algorithm": assignment.algorithm.value,
                    "generator_segment_limit_km": parameters.maximum_segment_distance_km,
                    "original_total_minutes": assignment.total_flight_minutes,
                    "original_load_makespan_minutes": assignment.completion_minutes,
                    "frozen_angle_degrees": coverage.selected_flight_direction_degrees,
                    "strip_spacing_m": spacing,
                    "coverage_request": request.model_dump(mode="json"),
                },
            )
        )
    return results


def _key(coordinate: Coordinate) -> tuple[float, float]:
    return round(coordinate.longitude, 9), round(coordinate.latitude, 9)
