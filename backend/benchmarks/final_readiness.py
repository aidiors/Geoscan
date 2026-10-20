"""Repeatable final demo/evidence bundle. No assertions of global optimality.

Run from backend: python -m benchmarks.final_readiness --repeats 3
Outputs go to the ignored work directory, including exact requests and exports.
"""

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from time import perf_counter
from typing import Any

from fastapi.testclient import TestClient

from geoplan.api.app import create_app
from geoplan.composition import create_default_dependencies
from geoplan.planning.models import PlanRequest
from scenarios.optimization_scale import scale_scenarios
from scenarios.organizer import organizer_request
from scenarios.planning import parallel_survey_request, productive_survey_request


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _source_fingerprint() -> str:
    digest = hashlib.sha256()
    for source in sorted(Path("src/geoplan").rglob("*.py")):
        digest.update(source.as_posix().encode())
        digest.update(source.read_bytes())
    return digest.hexdigest()


def _selected_kml(output: Path, request: PlanRequest) -> None:
    document = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    folder = ET.SubElement(document, "Document")
    for area in request.coverage_areas:
        placemark = ET.SubElement(folder, "Placemark")
        ET.SubElement(placemark, "name").text = area.name
        polygon = ET.SubElement(placemark, "Polygon")
        for index, ring in enumerate([area.coordinates, *area.interior_rings]):
            boundary = ET.SubElement(
                polygon, "outerBoundaryIs" if index == 0 else "innerBoundaryIs"
            )
            linear = ET.SubElement(boundary, "LinearRing")
            closed = ring if ring[0] == ring[-1] else [*ring, ring[0]]
            ET.SubElement(linear, "coordinates").text = " ".join(
                f"{p.longitude},{p.latitude},{p.altitude_m or 0}" for p in closed
            )
    ET.ElementTree(document).write(
        output / "organizer-selected.kml", encoding="utf-8", xml_declaration=True
    )


def run(
    output: Path, source: Path, repeats: int, include_scale: bool = False, scale_repeats: int = 1
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    real, provenance = organizer_request(source)
    _selected_kml(output, real)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    _write(output / "organizer-source.json", {**provenance, "sha256": source_hash})
    synthetic = productive_survey_request()
    assert synthetic.survey_parameters is not None
    sensor = synthetic.survey_parameters.sensor.model_copy(
        update={"minimum_trigger_interval_s": 0.5}
    )
    mixed = synthetic.model_copy(
        update={
            "drones": [
                synthetic.drones[0].model_copy(
                    update={
                        "minimum_turn_radius_m": 40,
                        "installed_sensor": sensor,
                        "minimum_cruise_speed_kmh": 64,
                    }
                )
            ],
        }
    )
    scenarios: dict[str, PlanRequest] = {
        "organizer": real,
        "curved_camera": mixed,
        "parallel": parallel_survey_request(),
        "missed_deadline": parallel_survey_request(completion_deadline_minutes=1),
    }
    if include_scale:
        large = next(case.request for case in scale_scenarios() if case.name == "flat_100")
        assert large.survey_parameters is not None
        large_camera = large.survey_parameters.sensor
        large_drones = [
            d.model_copy(
                update={
                    "minimum_turn_radius_m": (0, 40, 80)[i % 3],
                    "installed_sensor": large_camera.model_copy(
                        update={
                            "sensor_id": f"scale-camera-{i}",
                            "sensor_width_mm": 13.2 + i % 3,
                            "sensor_height_mm": 8.8 + (i % 3) * 0.4,
                        }
                    ),
                }
            )
            for i, d in enumerate(large.drones)
        ]
        scenarios["scale_100_mixed_curved"] = large.model_copy(update={"drones": large_drones})
    manifest = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "backend_source_sha256": _source_fingerprint(),
        "repeats": repeats,
        "scenarios": {},
        "comparison": "Repeated identical requests with unchanged search budgets and seeds. "
        "Timings include the full pipeline. This bundle measures repeatability; "
        "it is not an equal-compute comparison with a different optimizer.",
    }
    dependencies = create_default_dependencies()
    with TestClient(create_app(dependencies, serve_frontend=False)) as client:
        for name, request in scenarios.items():
            _write(output / f"{name}-request.json", request.model_dump(mode="json"))
            runs: list[dict[str, Any]] = []
            last = None
            for repeat in range(scale_repeats if name.startswith("scale_") else repeats):
                started = perf_counter()
                response = client.post("/v1/plans", json=request.model_dump(mode="json"))
                elapsed = perf_counter() - started
                payload = response.json()
                _write(output / f"{name}-plan-{repeat + 1}.json", payload)
                runs.append(
                    {
                        "http_status": response.status_code,
                        "seconds": elapsed,
                        "metrics": payload.get("metrics"),
                        "audit": (payload.get("safety_audit") or {}).get("status"),
                    }
                )
                if response.status_code == 200:
                    last = payload
                print(name, repeat + 1, response.status_code, round(elapsed, 2), flush=True)
            summary = {
                "runs": runs,
                "median_seconds": statistics.median(r["seconds"] for r in runs),
            }
            valid_metrics = [r["metrics"] for r in runs if r["http_status"] == 200]
            summary["variation"] = {
                metric: {
                    "min": min(values),
                    "max": max(values),
                    "median": statistics.median(values),
                    "stdev": statistics.stdev(values) if len(values) > 1 else 0,
                }
                for metric in ("completion_minutes", "total_flight_minutes")
                if (values := [m[metric] for m in valid_metrics])
            }
            if last is not None:
                quality = client.post(
                    "/v1/survey-quality",
                    json={
                        "request": request.model_dump(mode="json"),
                        "plan": last,
                    },
                )
                _write(output / f"{name}-quality.json", quality.json())
                summary["quality"] = {k: v for k, v in quality.json().items() if k != "features"}
                summary["exports"] = []
                for drone in last["drone_plans"]:
                    if not drone["missions"]:
                        continue
                    for format_name in ("kml", "geojson", "qgroundcontrol", "mission-planner"):
                        execution = format_name in ("qgroundcontrol", "mission-planner")
                        for sortie_index in range(len(drone["missions"])) if execution else [None]:
                            exported = client.post(
                                f"/v1/exports/{format_name}",
                                json={
                                    "plan": last,
                                    "drone_id": drone["drone_id"],
                                    "sortie_index": sortie_index,
                                },
                            )
                            expected = (
                                422 if execution and not last.get("export_authorization") else 200
                            )
                            if exported.status_code != expected:
                                raise RuntimeError(
                                    f"Export failed: {name}/{format_name}: {exported.text}"
                                )
                            if (
                                expected == 422
                                and exported.json()["code"] != "export_release_required"
                            ):
                                raise RuntimeError("Unexpected export refusal")
                            summary["exports"].append(
                                {
                                    "drone_id": drone["drone_id"],
                                    "format": format_name,
                                    "sortie_index": sortie_index,
                                    "http_status": exported.status_code,
                                }
                            )
                            _write(
                                output
                                / f"{name}-{drone['drone_id']}-{format_name}-{sortie_index}.json",
                                exported.json(),
                            )
            manifest["scenarios"][name] = summary
            _write(output / "manifest.json", manifest)
        advice = client.post(
            "/v1/feasibility-advice",
            json={
                "request": scenarios["missed_deadline"].model_dump(mode="json"),
                "maximum_variants": 2,
            },
        )
        _write(output / "deadline-advisor.json", advice.json())
        # A physical negative scenario: camera rate cannot support fixed-wing
        # minimum airspeed. The expected outcome is refusal, not a pretty route.
        negative = mixed.model_copy(
            update={
                "drones": [
                    mixed.drones[0].model_copy(
                        update={
                            "installed_sensor": sensor.model_copy(
                                update={"minimum_trigger_interval_s": 4}
                            ),
                        }
                    )
                ],
            }
        )
        refused = client.post("/v1/plans", json=negative.model_dump(mode="json"))
        _write(output / "negative-request.json", negative.model_dump(mode="json"))
        _write(
            output / "negative-result.json",
            {"http_status": refused.status_code, "response": refused.json()},
        )
        manifest["negative_refused"] = refused.status_code == 422
        _write(output / "manifest.json", manifest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("../work/final-evidence"))
    parser.add_argument(
        "--source", type=Path, default=Path("../reference/hackathon/Границы полетов.kml")
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--include-scale", action="store_true")
    parser.add_argument("--scale-repeats", type=int, default=1)
    args = parser.parse_args()
    if args.repeats < 1 or args.scale_repeats < 1:
        parser.error("repeats must be positive")
    run(args.output, args.source, args.repeats, args.include_scale, args.scale_repeats)
