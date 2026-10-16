import pytest

from geoplan.domain.environment import WindCondition
from geoplan.domain.geo import Coordinate
from geoplan.flight.duration import CruiseMissionDurationCalculator
from geoplan.flight.models import MissionDurationRequest

NORTHBOUND_ROUTE = [
    Coordinate(longitude=37.4, latitude=55.7),
    Coordinate(longitude=37.4, latitude=55.8),
]


def test_sensor_speed_limit_applies_only_to_survey_phase() -> None:
    estimate = CruiseMissionDurationCalculator().calculate(
        MissionDurationRequest(
            distance_km=10,
            cruise_speed_kmh=60,
            route=NORTHBOUND_ROUTE,
            speed_limit_kmh=20,
            transit_route=NORTHBOUND_ROUTE,
            transit_distance_km=6,
            survey_route=NORTHBOUND_ROUTE,
            survey_distance_km=2,
            landing_route=NORTHBOUND_ROUTE,
            landing_distance_km=2,
        )
    )

    assert estimate.duration_minutes == pytest.approx(14)
    assert estimate.transit_duration_minutes == pytest.approx(8)
    assert estimate.survey_duration_minutes == pytest.approx(6)
    assert estimate.landing_duration_minutes == pytest.approx(2)


def test_crosswind_correction_increases_duration_to_hold_track() -> None:
    calculator = CruiseMissionDurationCalculator()
    calm = calculator.calculate(
        MissionDurationRequest(
            distance_km=10,
            cruise_speed_kmh=60,
            route=NORTHBOUND_ROUTE,
        )
    )
    crosswind = calculator.calculate(
        MissionDurationRequest(
            distance_km=10,
            cruise_speed_kmh=60,
            route=NORTHBOUND_ROUTE,
            wind=WindCondition(speed_mps=5, direction_degrees=90),
        )
    )

    assert crosswind.duration_minutes > calm.duration_minutes
    assert crosswind.effective_speed_kmh < calm.effective_speed_kmh


def test_opposite_legs_take_longer_in_wind_than_in_calm() -> None:
    out_and_back = [*NORTHBOUND_ROUTE, NORTHBOUND_ROUTE[0]]
    calculator = CruiseMissionDurationCalculator()
    calm = calculator.calculate(
        MissionDurationRequest(
            distance_km=20,
            cruise_speed_kmh=60,
            route=out_and_back,
        )
    )
    windy = calculator.calculate(
        MissionDurationRequest(
            distance_km=20,
            cruise_speed_kmh=60,
            route=out_and_back,
            wind=WindCondition(speed_mps=5, direction_degrees=0),
        )
    )

    assert windy.duration_minutes > calm.duration_minutes
