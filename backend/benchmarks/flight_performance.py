from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from geoplan.domain.environment import WindCondition
from geoplan.domain.geo import Coordinate
from geoplan.flight.duration import CruiseMissionDurationCalculator
from geoplan.flight.models import MissionDurationRequest


class FlightPerformanceMeasurement(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: str
    duration_minutes: float
    effective_speed_kmh: float
    battery_used_percent: float
    remaining_after_reserve_percent: float


def run_experiment() -> list[FlightPerformanceMeasurement]:
    north = Coordinate(longitude=37.4, latitude=55.7)
    south = Coordinate(longitude=37.4, latitude=55.6)
    route = [south, north]
    out_and_back = [south, north, south]
    calculator = CruiseMissionDurationCalculator()
    cases = [
        ("calm", route, None),
        ("headwind_5_mps", route, WindCondition(speed_mps=5, direction_degrees=0)),
        ("tailwind_5_mps", route, WindCondition(speed_mps=5, direction_degrees=180)),
        ("crosswind_5_mps", route, WindCondition(speed_mps=5, direction_degrees=90)),
        (
            "out_and_back_5_mps",
            out_and_back,
            WindCondition(speed_mps=5, direction_degrees=0),
        ),
    ]
    measurements: list[FlightPerformanceMeasurement] = []
    for scenario, measured_route, wind in cases:
        distance_km = 20 if scenario == "out_and_back_5_mps" else 10
        estimate = calculator.calculate(
            MissionDurationRequest(
                distance_km=distance_km,
                cruise_speed_kmh=60,
                route=measured_route,
                wind=wind,
            )
        )
        battery_used = estimate.duration_minutes / 60 * 100
        measurements.append(
            FlightPerformanceMeasurement(
                scenario=scenario,
                duration_minutes=estimate.duration_minutes,
                effective_speed_kmh=estimate.effective_speed_kmh,
                battery_used_percent=round(battery_used, 2),
                remaining_after_reserve_percent=round(100 - battery_used - 20, 2),
            )
        )

    phased = calculator.calculate(
        MissionDurationRequest(
            distance_km=10,
            cruise_speed_kmh=60,
            route=route,
            speed_limit_kmh=20,
            transit_route=route,
            transit_distance_km=6,
            survey_route=route,
            survey_distance_km=2,
            landing_route=route,
            landing_distance_km=2,
        )
    )
    phased_battery = phased.duration_minutes / 60 * 100
    measurements.append(
        FlightPerformanceMeasurement(
            scenario="phased_8km_transit_2km_survey",
            duration_minutes=phased.duration_minutes,
            effective_speed_kmh=phased.effective_speed_kmh,
            battery_used_percent=round(phased_battery, 2),
            remaining_after_reserve_percent=round(100 - phased_battery - 20, 2),
        )
    )
    return measurements


if __name__ == "__main__":
    print(
        json.dumps(
            [measurement.model_dump(mode="json") for measurement in run_experiment()],
            ensure_ascii=False,
            indent=2,
        )
    )
