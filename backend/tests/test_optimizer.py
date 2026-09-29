from src.optimizer import BidirectionalRouteOptimizer
from src.routing import route_distance_km
from src.schemas import Coordinate, NoFlyZone, RouteOptimizationRequest


def test_bidirectional_optimizer_reverses_route_for_shorter_departure_transit() -> None:
    optimizer = BidirectionalRouteOptimizer()
    near = Coordinate(longitude=37.4, latitude=55.7)
    far = Coordinate(longitude=38.0, latitude=56.0)

    result = optimizer.optimize(
        RouteOptimizationRequest(
            route=[far, near],
            departure=Coordinate(longitude=37.401, latitude=55.701),
        )
    )

    assert result.reversed_for_shorter_transit
    assert result.route[0] == near


def test_bidirectional_optimizer_compares_safe_transit_instead_of_direct_distance() -> None:
    optimizer = BidirectionalRouteOptimizer()
    departure = Coordinate(longitude=37.4, latitude=55.7)
    direct_but_blocked = Coordinate(longitude=37.42, latitude=55.7)
    safe_start = Coordinate(longitude=37.4, latitude=55.708)
    restriction = NoFlyZone(
        zone_id="restriction",
        coordinates=[
            Coordinate(longitude=37.405, latitude=55.695),
            Coordinate(longitude=37.418, latitude=55.695),
            Coordinate(longitude=37.418, latitude=55.705),
            Coordinate(longitude=37.405, latitude=55.705),
        ],
        safety_margin_m=30,
    )

    result = optimizer.optimize(
        RouteOptimizationRequest(
            route=[direct_but_blocked, safe_start],
            departure=departure,
            no_fly_zones=[restriction],
        )
    )

    forward_transit_distance = route_distance_km(result.route)
    assert result.reversed_for_shorter_transit
    assert forward_transit_distance > 0


def test_bidirectional_optimizer_considers_landing_transit() -> None:
    optimizer = BidirectionalRouteOptimizer()
    west = Coordinate(longitude=37.4, latitude=55.7)
    east = Coordinate(longitude=37.5, latitude=55.7)

    result = optimizer.optimize(
        RouteOptimizationRequest(
            route=[west, east],
            departure=Coordinate(longitude=37.45, latitude=55.7),
            landings=[west],
        )
    )

    assert result.reversed_for_shorter_transit
    assert result.route == [east, west]


def test_optimizer_selects_alternative_snake_with_less_empty_transit() -> None:
    optimizer = BidirectionalRouteOptimizer()
    departure = Coordinate(longitude=37.4, latitude=55.7)
    landing = Coordinate(longitude=37.5, latitude=55.7)
    remote_route = [
        Coordinate(longitude=37.6, latitude=55.7),
        Coordinate(longitude=37.7, latitude=55.7),
    ]
    nearby_alternative = [departure, landing]

    result = optimizer.optimize(
        RouteOptimizationRequest(
            route=remote_route,
            alternative_routes=[nearby_alternative],
            departure=departure,
            landings=[landing],
        )
    )

    assert result.selected_route_index == 1
    assert result.route == nearby_alternative
