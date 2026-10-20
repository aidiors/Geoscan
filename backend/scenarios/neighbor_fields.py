"""Separated survey targets with explicit camera-off transfers and a remote base."""

from geoplan.coverage.models import CoverageArea
from geoplan.navigation.geometry import LocalProjection
from geoplan.planning.models import PlanRequest
from scenarios.planning import productive_survey_request


def neighbor_fields_request(**overrides) -> PlanRequest:
    request = productive_survey_request()
    projection = LocalProjection(37, 55)
    assert request.departure_site is not None and request.landing_site is not None
    site = request.departure_site.model_copy(
        update={"coordinate": projection.unproject(-4000, -500)}
    )
    return PlanRequest.model_validate(
        {
            **request.model_dump(),
            "coverage_areas": [
                CoverageArea(
                    area_id=f"field-{i}",
                    name=f"Field {i}",
                    coordinates=[
                        projection.unproject(x + i * 500, y + i * 40)
                        for x, y in ((0, 0), (400, 0), (400, 240), (0, 240))
                    ],
                )
                for i in range(2)
            ],
            "departure_site": site,
            "landing_site": site,
            **overrides,
        }
    )
