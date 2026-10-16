import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.coverage.models import CoveragePlanRequest, CoveragePlanResult
from geoplan.coverage.sweep import BoustrophedonCoveragePlanner
from geoplan.domain.airspace import AllowedAirspace
from geoplan.domain.enums import CoverageAlgorithm, TurnRadiusStatus
from geoplan.domain.geo import Coordinate
from geoplan.navigation.curvature import PlannedSweepTransition, SweepTransitionPlanner
from geoplan.navigation.models import SweepRouteCurvatureRequest
from geoplan.navigation.routing import RouteGeometryError
from geoplan.planning.models import TurnRadiusAnalysisRequest
from geoplan.planning.turn_radius import analyze_turn_radii
from scenarios.coverage import default_coverage_benchmark_cases


class SplitCoveragePlanner:
    @property
    def coverage_algorithm(self) -> CoverageAlgorithm:
        return CoverageAlgorithm.BOUSTROPHEDON_SWEEP

    def plan(self, request: CoveragePlanRequest) -> CoveragePlanResult:
        baseline = BoustrophedonCoveragePlanner().plan(request)
        return baseline.model_copy(update={"segments": baseline.segments * 2})


class OversizedCoveragePlanner:
    @property
    def coverage_algorithm(self) -> CoverageAlgorithm:
        return CoverageAlgorithm.BOUSTROPHEDON_SWEEP

    def plan(self, request: CoveragePlanRequest) -> CoveragePlanResult:
        baseline = BoustrophedonCoveragePlanner().plan(request)
        segment = baseline.segments[0].model_copy(update={"route": baseline.segments[0].route * 13})
        return baseline.model_copy(update={"segments": [segment]})


class LowerRadiusFailurePlanner:
    def __init__(self, base: SweepTransitionPlanner) -> None:
        self.base = base

    def connect(
        self,
        source: list[Coordinate],
        target: list[Coordinate],
        request: SweepRouteCurvatureRequest,
    ) -> PlannedSweepTransition:
        if request.minimum_turn_radius_m < 30:
            raise RouteGeometryError("Тестовый поиск не нашёл переход.")
        return self.base.connect(source, target, request)


def test_turn_radius_request_rejects_unsorted_or_duplicate_radii() -> None:
    coverage = default_coverage_benchmark_cases()[0].request

    with pytest.raises(ValidationError, match="по возрастанию"):
        TurnRadiusAnalysisRequest(coverage=coverage, radii_m=[30, 15])
    with pytest.raises(ValidationError, match="по возрастанию"):
        TurnRadiusAnalysisRequest(coverage=coverage, radii_m=[15, 15])
    with pytest.raises(ValidationError, match="диапазоне"):
        TurnRadiusAnalysisRequest(coverage=coverage, radii_m=[-1])


def test_zero_radius_uses_unconstrained_polyline_without_curvature_search() -> None:
    coverage = default_coverage_benchmark_cases()[0].request
    dependencies = create_default_dependencies()

    result = analyze_turn_radii(
        TurnRadiusAnalysisRequest(coverage=coverage, radii_m=[0]),
        dependencies.radius_coverage_planner,
        LowerRadiusFailurePlanner(dependencies.sweep_transition_planner),
    )

    assert len(result.scenarios) == 1
    assert result.scenarios[0].status is TurnRadiusStatus.FEASIBLE
    assert result.scenarios[0].radius_m == 0
    assert result.scenarios[0].distance_km is not None
    assert result.scenarios[0].uncovered_area_m2 is None
    assert result.common_survey_route is not None
    assert "без ограничения радиуса" in result.message


def test_zero_radius_can_be_compared_with_positive_radius() -> None:
    coverage = default_coverage_benchmark_cases()[0].request
    dependencies = create_default_dependencies()

    result = analyze_turn_radii(
        TurnRadiusAnalysisRequest(coverage=coverage, radii_m=[0, 15]),
        dependencies.radius_coverage_planner,
        dependencies.sweep_transition_planner,
    )

    assert [scenario.status for scenario in result.scenarios] == [
        TurnRadiusStatus.FEASIBLE,
        TurnRadiusStatus.FEASIBLE,
    ]
    assert result.scenarios[0].distance_km is not None
    assert result.scenarios[1].distance_km is not None
    assert result.scenarios[0].distance_km < result.scenarios[1].distance_km


def test_turn_radius_analysis_returns_common_route_for_assumed_range() -> None:
    coverage = default_coverage_benchmark_cases()[0].request
    dependencies = create_default_dependencies()

    result = analyze_turn_radii(
        TurnRadiusAnalysisRequest(coverage=coverage, radii_m=[15, 30]),
        dependencies.radius_coverage_planner,
        dependencies.sweep_transition_planner,
    )

    assert [scenario.status for scenario in result.scenarios] == [
        TurnRadiusStatus.FEASIBLE,
        TurnRadiusStatus.FEASIBLE,
    ]
    assert result.common_survey_route is not None
    shorter_distance = result.scenarios[0].distance_km
    longer_distance = result.scenarios[1].distance_km
    assert shorter_distance is not None
    assert longer_distance is not None
    assert longer_distance > shorter_distance


def test_turn_radius_analysis_rejects_route_outside_explicit_airspace() -> None:
    coverage = default_coverage_benchmark_cases()[0].request
    dependencies = create_default_dependencies()
    airspace = AllowedAirspace(
        airspace_id="wrong-altitude",
        coordinates=coverage.area.coordinates,
        minimum_altitude_m=coverage.mission_altitude_m + 1,
        maximum_altitude_m=coverage.mission_altitude_m + 100,
    )

    result = analyze_turn_radii(
        TurnRadiusAnalysisRequest(
            coverage=coverage,
            allowed_airspaces=[airspace],
            radii_m=[15, 30],
        ),
        dependencies.radius_coverage_planner,
        dependencies.sweep_transition_planner,
    )

    assert all(
        scenario.status is TurnRadiusStatus.NO_ROUTE_FOR_SELECTED_SWEEPS
        for scenario in result.scenarios
    )
    assert result.common_survey_route is None


def test_turn_radius_analysis_reports_segmented_route_as_not_evaluated() -> None:
    coverage = default_coverage_benchmark_cases()[0].request
    dependencies = create_default_dependencies()

    result = analyze_turn_radii(
        TurnRadiusAnalysisRequest(coverage=coverage, radii_m=[15, 30]),
        SplitCoveragePlanner(),
        dependencies.sweep_transition_planner,
    )

    assert all(scenario.status is TurnRadiusStatus.NOT_EVALUATED for scenario in result.scenarios)
    assert result.common_survey_route is None


def test_turn_radius_analysis_rejects_oversized_interactive_search() -> None:
    coverage = default_coverage_benchmark_cases()[0].request
    dependencies = create_default_dependencies()

    result = analyze_turn_radii(
        TurnRadiusAnalysisRequest(coverage=coverage, radii_m=[15, 30]),
        OversizedCoveragePlanner(),
        dependencies.sweep_transition_planner,
    )

    assert all(scenario.status is TurnRadiusStatus.NOT_EVALUATED for scenario in result.scenarios)
    assert "100 галсов" in result.message


def test_turn_radius_analysis_reuses_maximum_radius_route_when_smaller_search_fails() -> None:
    coverage = default_coverage_benchmark_cases()[0].request
    dependencies = create_default_dependencies()

    result = analyze_turn_radii(
        TurnRadiusAnalysisRequest(coverage=coverage, radii_m=[15, 30]),
        dependencies.radius_coverage_planner,
        LowerRadiusFailurePlanner(dependencies.sweep_transition_planner),
    )

    assert [scenario.status for scenario in result.scenarios] == [
        TurnRadiusStatus.FEASIBLE,
        TurnRadiusStatus.FEASIBLE,
    ]
    assert result.scenarios[0].distance_km == result.scenarios[1].distance_km


def test_turn_radius_analysis_route_returns_stable_schema() -> None:
    client = TestClient(create_app(create_default_dependencies(), serve_frontend=False))
    coverage = default_coverage_benchmark_cases()[0].request

    response = client.post(
        "/v1/turn-radius-analysis",
        json={"coverage": coverage.model_dump(mode="json"), "radii_m": [15, 30]},
    )

    assert response.status_code == 200
    assert response.json()["area_id"] == coverage.area.area_id
    assert [item["status"] for item in response.json()["scenarios"]] == [
        "feasible",
        "feasible",
    ]


def test_turn_radius_analysis_route_accepts_zero_radius() -> None:
    client = TestClient(create_app(create_default_dependencies(), serve_frontend=False))
    coverage = default_coverage_benchmark_cases()[0].request

    response = client.post(
        "/v1/turn-radius-analysis",
        json={"coverage": coverage.model_dump(mode="json"), "radii_m": [0]},
    )

    assert response.status_code == 200
    assert response.json()["scenarios"][0]["radius_m"] == 0
    assert response.json()["scenarios"][0]["status"] == "feasible"


def test_turn_radius_analysis_route_rejects_unsorted_radii_with_stable_error() -> None:
    client = TestClient(create_app(create_default_dependencies(), serve_frontend=False))
    coverage = default_coverage_benchmark_cases()[0].request

    response = client.post(
        "/v1/turn-radius-analysis",
        json={"coverage": coverage.model_dump(mode="json"), "radii_m": [30, 15]},
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_request",
        "message": "Запрос не соответствует контракту API.",
        "recommendations": [],
    }
