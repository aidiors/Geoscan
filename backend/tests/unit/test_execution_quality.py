import pytest

from geoplan.airspace.windows import _vertical_layer_options
from geoplan.coverage.resolution import SurveyResolution
from geoplan.domain.enums import SafetyAuditStatus
from geoplan.domain.geo import Coordinate
from geoplan.domain.mission import Mission
from geoplan.safety.audit import _schedule_integrity_check, _survey_resolution_check
from scenarios.planning import productive_survey_request


def mission(**updates) -> Mission:
    return Mission(
        drone_id="fast",
        segment_id="first",
        duration_minutes=5,
        distance_km=5,
        route=[Coordinate(longitude=37, latitude=55), Coordinate(longitude=37.01, latitude=55)],
        altitude_m=120,
        cruise_speed_kmh=60,
        effective_speed_kmh=60,
        estimated_battery_percent=20,
        battery_reserve_margin_percent=40,
    ).model_copy(update=updates)


def test_turnaround_arithmetic_noise_is_accepted_but_real_shortfall_is_not():
    request = productive_survey_request()
    request = request.model_copy(update={"turnaround_minutes": 3})
    first = mission(start_offset_minutes=8.488, end_offset_minutes=13.944, duration_minutes=5.456)
    second = mission(
        segment_id="second",
        start_offset_minutes=16.944,
        end_offset_minutes=22.963,
        duration_minutes=6.019,
    )
    assert first.end_offset_minutes is not None and second.start_offset_minutes is not None
    assert first.end_offset_minutes + 3 > second.start_offset_minutes
    assert _schedule_integrity_check(request, [first, second]).status is SafetyAuditStatus.PASSED
    short = second.model_copy(update={"start_offset_minutes": 16.9439})
    assert _schedule_integrity_check(request, [first, short]).status is SafetyAuditStatus.FAILED


def test_gsd_uses_scheduled_height_not_stale_metadata():
    request = productive_survey_request()
    assert request.survey_parameters is not None
    survey = request.survey_parameters.model_copy(update={"maximum_gsd_cm": 3.5})
    request = request.model_copy(update={"survey_parameters": survey})
    raised = mission(altitude_m=150, computed_gsd_cm=2.741)
    assert SurveyResolution().gsd_cm(raised, survey.sensor) > 3.5
    assert _survey_resolution_check(request, [raised]).status is SafetyAuditStatus.FAILED
    assert _survey_resolution_check(request, [mission()]).status is SafetyAuditStatus.PASSED


def test_vertical_layers_cannot_trade_resolution_for_faster_schedule():
    request = productive_survey_request()
    assert request.survey_parameters is not None
    drone = request.drones[0].model_copy(
        update={"maximum_climb_rate_mps": 3, "maximum_descent_rate_mps": 3}
    )
    request = request.model_copy(
        update={
            "drones": [drone],
            "maximum_vertical_deconfliction_m": 30,
            "survey_parameters": request.survey_parameters.model_copy(
                update={"maximum_gsd_cm": 3.5}
            ),
        }
    )
    options = _vertical_layer_options(mission(), drone, request)
    assert [o.offset_m for o in options] == [0]
    assert request.survey_parameters is not None
    permissive = request.model_copy(
        update={
            "survey_parameters": request.survey_parameters.model_copy(update={"maximum_gsd_cm": 5})
        }
    )
    assert [o.offset_m for o in _vertical_layer_options(mission(), drone, permissive)] == [0, 30]


def test_resolution_scales_linearly_with_height():
    survey = productive_survey_request().survey_parameters
    assert survey is not None
    sensor = survey.sensor
    assert SurveyResolution().gsd_cm(mission(altitude_m=150), sensor) == pytest.approx(
        SurveyResolution().gsd_cm(mission(), sensor) * 1.25
    )
