"""Physical freeform coverage paths and finite-frame validation shared by app and experiments.

Routes contain camera-on polylines, not a mandatory set of service tracks. The
same nadir camera, wind model, safe connectors and usable battery apply to every
family. Passing the geometric screen is not a full operational/photogrammetric
audit: turn dynamics, terrain and multi-aircraft scheduling are not modeled here.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from math import hypot, inf, isfinite

from shapely.geometry import GeometryCollection, LineString, Point, Polygon
from shapely.ops import unary_union

from geoplan.coverage.models import CoveragePlanRequest
from geoplan.coverage.sweep import (
    _flyable_geometry,
)
from geoplan.domain.enums import OptimizationCriterion
from geoplan.flight.performance import route_duration_minutes
from geoplan.navigation.geometry import polygon_parts
from geoplan.navigation.routing import route_distance_km, route_is_clear_of_no_fly_zones
from geoplan.routing.model import Aircraft, CostModel, RoutingProblem

XY = tuple[float, float]
Stroke = tuple[XY, ...]


@dataclass(frozen=True)
class GeometryCase:
    case_id: str
    request: CoveragePlanRequest
    home: XY
    landing: XY
    cruise_speed_kmh: float = 90
    endurance_minutes: float = 60
    reserve_percent: float = 20

    @property
    def usable_minutes(self) -> float:
        return self.endurance_minutes * (1 - self.reserve_percent / 100)


@dataclass(frozen=True)
class Route:
    family: str
    sorties: tuple[tuple[Stroke, ...], ...]
    scan_connections: bool = False


@dataclass(frozen=True)
class Leg:
    start: XY
    end: XY
    minutes: float
    distance_km: float
    phase: str


class GeometryModel:
    def __init__(self, case: GeometryCase) -> None:
        self.case = case
        self.target, self.projection = _flyable_geometry(case.request)
        parameters = case.request.parameters
        sensor = parameters.sensor
        self.width = (
            case.request.mission_altitude_m * sensor.sensor_width_mm / sensor.focal_length_mm
        )
        self.height = (
            case.request.mission_altitude_m * sensor.sensor_height_mm / sensor.focal_length_mm
        )
        self.spacing = min(self.width, parameters.strip_width_m) * (
            1 - parameters.side_overlap_percent / 100
        )
        self.trigger = self.height * (1 - parameters.forward_overlap_percent / 100)
        self.interval_minutes = sensor.minimum_trigger_interval_s / 60
        self.survey_cap = self.trigger / sensor.minimum_trigger_interval_s * 3.6
        self.cost = CostModel(
            RoutingProblem(
                (),
                (
                    Aircraft(
                        "geometry-uav",
                        case.cruise_speed_kmh,
                        case.usable_minutes,
                        self.coordinate(case.home),
                        landing=self.coordinate(case.landing),
                    ),
                ),
                None,
                case.request.wind,
                tuple(case.request.exclusions),
                OptimizationCriterion.TOTAL_FLIGHT_TIME,
                None,
            )
        )

    def coordinate(self, point: XY):
        return self.projection.unproject(*point)

    def connector(self, a: XY, b: XY) -> tuple[XY, ...]:
        result = self.cost.path(self.coordinate(a), self.coordinate(b))
        if result is None:
            raise ValueError("No safe connection")
        return tuple(self.projection.project(p) for p in result[0])

    def travel_minutes(self, a: XY, b: XY) -> float:
        return self.cost.leg(0, self.coordinate(a), self.coordinate(b))[0]

    def path_minutes(self, stroke: Stroke) -> float:
        route = [self.coordinate(p) for p in stroke]
        return route_duration_minutes(
            route,
            route_distance_km(route),
            self.case.cruise_speed_kmh,
            self.case.request.wind,
            self.survey_cap,
        )


def oriented_order(model: GeometryModel, strokes: tuple[Stroke, ...]) -> tuple[Stroke, ...]:
    """Exact two-endpoint time DP for a fixed order and both actual sites."""
    if not strokes:
        return ()
    labels: list[tuple[float, tuple[Stroke, ...]]] = [(0, ()), (0, ())]
    for stroke in strokes:
        updated = []
        for variant in (stroke, tuple(reversed(stroke))):
            options = []
            for duration, previous in labels:
                end = previous[-1][-1] if previous else model.case.home
                value = (
                    duration + model.travel_minutes(end, variant[0]) + model.path_minutes(variant)
                )
                options.append((value, (*previous, variant)))
            updated.append(min(options, key=lambda item: item[0]))
        labels = updated
    return min(
        labels,
        key=lambda item: item[0] + model.travel_minutes(item[1][-1][-1], model.case.landing),
    )[1]


def contour_strokes(model: GeometryModel) -> tuple[Stroke, ...]:
    """Nested polygon offsets, including obstacle boundaries and split components."""
    strokes = []
    offset = model.spacing / 2
    for _ in range(500):
        inner = model.target.buffer(-offset, join_style="mitre")
        if inner.is_empty:
            break
        for polygon in sorted(polygon_parts(inner), key=lambda p: (*p.bounds, p.area)):
            for ring in (polygon.exterior, *polygon.interiors):
                if ring.length > 0.1:
                    strokes.append(tuple((float(x), float(y)) for x, y in ring.coords))
        offset += model.spacing
    return tuple(strokes)


def anchor_contours(model: GeometryModel, strokes: tuple[Stroke, ...]) -> tuple[Stroke, ...]:
    """Open each closed contour at its closest point to the preceding endpoint."""
    result = []
    previous = model.case.home
    for stroke in strokes:
        line = LineString(stroke)
        anchor = line.interpolate(line.project(Point(previous)))
        point = (float(anchor.x), float(anchor.y))
        segments = list(pairwise(stroke))
        index = min(
            range(len(segments)),
            key=lambda i: LineString(segments[i]).distance(anchor),
        )
        opened = (point, *stroke[index + 1 : -1], *stroke[: index + 1], point)
        cleaned = tuple(p for i, p in enumerate(opened) if i == 0 or p != opened[i - 1])
        result.append(cleaned)
        previous = point
    return tuple(result)


def route_legs(model: GeometryModel, route: Route) -> tuple[tuple[Leg, ...], ...]:
    result = []
    for sortie in route.sorties:
        legs = []
        previous = model.case.home
        for index, stroke in enumerate(sortie):
            connector = model.connector(previous, stroke[0])
            scan = (
                index > 0
                and route.scan_connections
                and model.target.buffer(1e-7).covers(LineString(connector))
            )
            phase = "survey" if scan else ("departure" if index == 0 else "connection")
            legs.extend(_legs(model, connector, phase))
            legs.extend(_legs(model, stroke, "survey"))
            previous = stroke[-1]
        legs.extend(_legs(model, model.connector(previous, model.case.landing), "return"))
        result.append(tuple(legs))
    return tuple(result)


def _legs(model: GeometryModel, path: Stroke, phase: str) -> list[Leg]:
    result = []
    for start, end in pairwise(path):
        if hypot(end[0] - start[0], end[1] - start[1]) < 1e-7:
            continue
        coordinates = [model.coordinate(start), model.coordinate(end)]
        distance = route_distance_km(coordinates)
        minutes = route_duration_minutes(
            coordinates,
            distance,
            model.case.cruise_speed_kmh,
            model.case.request.wind,
            model.survey_cap if phase == "survey" else None,
        )
        if phase == "survey":
            # Conservatively satisfy the trigger spacing in the local metric
            # projection as well as in the shared geodesic flight calculation.
            minutes = max(
                minutes,
                hypot(end[0] - start[0], end[1] - start[1]) / model.survey_cap * 0.06,
            )
        result.append(Leg(start, end, minutes, distance, phase))
    return result


def image_footprint(center: XY, start: XY, end: XY, width: float, height: float) -> Polygon:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = hypot(dx, dy)
    ux, uy = dx / length, dy / length
    return Polygon(
        [
            (
                center[0] + along * ux - across * uy,
                center[1] + along * uy + across * ux,
            )
            for along, across in (
                (-height / 2, -width / 2),
                (height / 2, -width / 2),
                (height / 2, width / 2),
                (-height / 2, width / 2),
            )
        ]
    )


def evaluate(model: GeometryModel, route: Route) -> tuple[dict, tuple[tuple[Leg, ...], ...]]:
    """Independent area/actual-image screen, without checking generator track IDs.

    Effective-width corridors on the interior are an overlap-spacing proxy, not
    proof of side overlap for arbitrary headings. Actual finite image rectangles
    are checked separately. Images obey one camera clock across every leg/turn.
    """
    flights = route_legs(model, route)
    return evaluate_flights(model, route.family, flights), flights


def evaluate_flights(
    model: GeometryModel, family: str, flights: tuple[tuple[Leg, ...], ...]
) -> dict:
    """Screen explicit trajectories, including experimental nonuniform timing."""
    images = []
    cores = []
    clocks = []
    phases = dict.fromkeys(("departure", "survey", "connection", "return"), 0.0)
    trigger_ok = True
    safe = True
    timing_ok = True
    continuous = True
    survey_length_m = 0.0
    service_area = model.target.buffer(1e-7)
    for flight in flights:
        clock = 0.0
        last_image = -inf
        previous_position = model.case.home
        for leg in flight:
            if not isfinite(leg.minutes) or leg.minutes <= 0:
                raise ValueError("Flight legs require positive elapsed time")
            nominal = _legs(model, (leg.start, leg.end), leg.phase)[0]
            timing_ok &= leg.minutes + 1e-7 >= nominal.minutes
            timing_ok &= abs(leg.distance_km - nominal.distance_km) <= 1e-7
            continuous &= (
                hypot(leg.start[0] - previous_position[0], leg.start[1] - previous_position[1])
                <= 1e-5
            )
            previous_position = leg.end
            phases[leg.phase] += leg.minutes
            safe &= route_is_clear_of_no_fly_zones(
                [model.coordinate(leg.start), model.coordinate(leg.end)],
                list(model.case.request.exclusions),
            )
            if leg.phase == "survey":
                line = LineString((leg.start, leg.end))
                safe &= service_area.covers(line)
                core = line.buffer(model.spacing / 2).intersection(model.target)
                cores.append(core)
                survey_length_m += line.length
                distance = line.length
                trigger_ok &= (
                    distance * model.interval_minutes / leg.minutes <= model.trigger + 1e-6
                )
                timestamp = max(clock, last_image + model.interval_minutes)
                while timestamp <= clock + leg.minutes + 1e-10:
                    fraction = min(1, max(0, (timestamp - clock) / leg.minutes))
                    center = (
                        leg.start[0] + fraction * (leg.end[0] - leg.start[0]),
                        leg.start[1] + fraction * (leg.end[1] - leg.start[1]),
                    )
                    images.append(
                        image_footprint(center, leg.start, leg.end, model.width, model.height)
                    )
                    last_image = timestamp
                    timestamp += model.interval_minutes
            clock += leg.minutes
        continuous &= (
            hypot(
                previous_position[0] - model.case.landing[0],
                previous_position[1] - model.case.landing[1],
            )
            <= 1e-5
        )
        clocks.append(clock)
    image_union = unary_union(images) if images else GeometryCollection()
    core_union = unary_union(cores) if cores else GeometryCollection()
    interior = model.target.buffer(-max(model.width, model.height) / 2)
    image_missing = model.target.difference(image_union)
    interior_missing = interior.difference(core_union)
    image_percent = 100 * (1 - image_missing.area / model.target.area)
    interior_percent = 100 * (1 - interior_missing.area / interior.area) if interior.area else 100.0
    # Width * length is additive and does not change when a leg is subdivided.
    # Summing individually buffered short edges would count their overlapping
    # end caps as repeated coverage, unfairly penalizing curved polylines.
    exposure = model.spacing * survey_length_m
    inefficiency = max(0, 1 - core_union.area / exposure) if exposure else 0
    nonproductive_survey_proxy = phases["survey"] * inefficiency
    gsd = model.width / model.case.request.parameters.sensor.image_width_px * 100
    battery_ok = bool(clocks) and max(clocks) <= model.case.usable_minutes + 1e-8
    passed = (
        safe
        and trigger_ok
        and battery_ok
        and timing_ok
        and continuous
        and image_percent >= 99.99
        and interior_percent >= 99.9
        and gsd <= model.case.request.parameters.maximum_gsd_cm
    )
    metrics = {
        "family": family,
        "screen_passed": bool(passed),
        "safe_geometry": bool(safe),
        "battery_feasible": battery_ok,
        "trigger_feasible": bool(trigger_ok),
        "physical_timing_feasible": bool(timing_ok),
        "trajectory_continuous": bool(continuous),
        "gsd_cm": gsd,
        "image_coverage_percent": image_percent,
        "image_missing_m2": image_missing.area,
        "interior_spacing_coverage_percent": interior_percent,
        "interior_spacing_missing_m2": interior_missing.area,
        "effective_width_inefficiency_percent": 100 * inefficiency,
        "effective_width_exposure_m2": exposure,
        "effective_width_covered_m2": core_union.area,
        "nonproductive_survey_proxy_minutes": nonproductive_survey_proxy,
        "nonproductive_time_proxy_minutes": (
            nonproductive_survey_proxy
            + phases["departure"]
            + phases["connection"]
            + phases["return"]
        ),
        "images": len(images),
        "sorties": len(flights),
        "flight_minutes": sum(clocks),
        "sortie_minutes": clocks,
        "deadhead_minutes": phases["departure"] + phases["connection"] + phases["return"],
        "departure_minutes": phases["departure"],
        "connection_minutes": phases["connection"],
        "return_minutes": phases["return"],
        "survey_minutes": phases["survey"],
        "distance_km": sum(leg.distance_km for flight in flights for leg in flight),
    }
    return metrics
