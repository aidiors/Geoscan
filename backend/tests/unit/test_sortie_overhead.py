from dataclasses import replace
from time import perf_counter

import pytest

from geoplan.domain.enums import OptimizationCriterion
from geoplan.domain.geo import Coordinate
from geoplan.routing.model import Aircraft, CostModel, RoutingProblem, Track, Trip
from geoplan.routing.split import route_catalog


def model(overhead=0, surcharge=0, available=100):
    start = Coordinate(longitude=37, latitude=55)
    end = Coordinate(longitude=37.01, latitude=55)
    return CostModel(
        RoutingProblem(
            (Track("line", start, end, 60),),
            (
                Aircraft(
                    "rotor",
                    60,
                    available,
                    start,
                    landing=end,
                    sortie_overhead_minutes=overhead,
                    sortie_energy_surcharge_minutes=surcharge,
                ),
            ),
            None,
            None,
            (),
            OptimizationCriterion.TOTAL_FLIGHT_TIME,
            None,
        )
    )


def test_sortie_overhead_is_in_canonical_and_catalog_cost_once():
    baseline, phases = model(), model(2.75, 0.9)
    first, second = baseline.trip(Trip(0, (0,))), phases.trip(Trip(0, (0,)))
    assert first is not None and second is not None
    assert second.total == pytest.approx(first.total + 2.75)
    assert second.survey == first.survey
    catalog = route_catalog(phases, (0,), perf_counter() + 10)
    assert catalog[0][0].cost.total == pytest.approx(second.total)
    solution = (Trip(0, (0,)),)
    assert phases.validate_geometry(solution)


def test_energy_surcharge_limits_capacity_without_inflating_flight_time():
    with_phases = model(1, 2, available=3)
    assert with_phases.trip(Trip(0, (0,))) is None
    without_surcharge = CostModel(
        replace(
            with_phases.problem,
            aircraft=(replace(with_phases.problem.aircraft[0], sortie_energy_surcharge_minutes=0),),
        )
    )
    assert without_surcharge.trip(Trip(0, (0,))) is not None
