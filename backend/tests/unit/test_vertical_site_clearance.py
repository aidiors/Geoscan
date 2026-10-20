from geoplan.domain.airspace import AllowedAirspace, NoFlyZone
from geoplan.domain.geo import Coordinate
from geoplan.flight.site_clearance import VerticalSiteClearance


def square():
    return [
        Coordinate(longitude=x, latitude=y)
        for x, y in (
            (36.99, 54.99),
            (37.01, 54.99),
            (37.01, 55.01),
            (36.99, 55.01),
        )
    ]


def allows(spaces=(), zones=()):
    return VerticalSiteClearance().allows(
        Coordinate(longitude=37, latitude=55),
        150,
        250,
        110,
        10,
        list(spaces),
        list(zones),
        [],
    )


def test_column_checks_prohibition_below_cruise_and_ignores_one_above_it():
    zone = NoFlyZone(
        zone_id="low", coordinates=square(), minimum_altitude_m=180, maximum_altitude_m=200
    )
    assert not allows(zones=[zone])
    assert allows(
        zones=[zone.model_copy(update={"minimum_altitude_m": 300, "maximum_altitude_m": 400})]
    )


def test_permission_column_can_use_contiguous_layers_but_not_jump_a_gap():
    lower = AllowedAirspace(
        airspace_id="lower", coordinates=square(), minimum_altitude_m=100, maximum_altitude_m=200
    )
    upper = AllowedAirspace(
        airspace_id="upper", coordinates=square(), minimum_altitude_m=200, maximum_altitude_m=300
    )
    assert allows([lower, upper])
    assert not allows([lower.model_copy(update={"maximum_altitude_m": 190}), upper])
