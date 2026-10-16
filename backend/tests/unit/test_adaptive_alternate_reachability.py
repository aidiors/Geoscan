from geoplan.domain.fleet import FlightSite
from geoplan.domain.geo import Coordinate
from geoplan.flight.emergency import (
    EmergencyCheckpoint,
    EvaluatedEmergencyCheckpoint,
    _certify_emergency_interval,
)


def test_adaptive_certificate_refines_narrow_internal_peak() -> None:
    site = FlightSite(
        site_id="alternate",
        name="Alternate",
        coordinate=Coordinate(longitude=0.005, latitude=0.01),
    )
    evaluated_longitudes: list[float] = []

    def evaluate(checkpoint: EmergencyCheckpoint) -> EvaluatedEmergencyCheckpoint:
        fraction = checkpoint.coordinate.longitude / 0.01
        diversion_minutes = 5 * (1 - abs(2 * fraction - 1))
        evaluated_longitudes.append(checkpoint.coordinate.longitude)
        return EvaluatedEmergencyCheckpoint(
            checkpoint=checkpoint,
            site=site,
            diversion_minutes=diversion_minutes,
            diversion_route=[checkpoint.coordinate, site.coordinate],
        )

    start = evaluate(
        EmergencyCheckpoint(
            coordinate=Coordinate(longitude=0, latitude=0),
            elapsed_minutes=0,
        )
    )
    end = evaluate(
        EmergencyCheckpoint(
            coordinate=Coordinate(longitude=0.01, latitude=0),
            elapsed_minutes=0,
        )
    )

    certificate = _certify_emergency_interval(start, end, evaluate, 6)

    assert len(evaluated_longitudes) > 2
    assert 0.005 in evaluated_longitudes
    assert certificate.maximum_required_minutes >= 5
    assert certificate.maximum_required_minutes <= 5.1
