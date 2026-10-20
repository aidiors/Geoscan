"""Exercise the real HTTP service at scale and with simultaneous independent clients.

Uses an already running, preferably isolated, server. Never modifies production settings.
Exact requests/responses are compressed beside incremental JSONL evidence.
"""

import argparse
import gzip
import hashlib
import json
import platform
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from threading import Barrier, Event, Thread
from time import perf_counter
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZipFile

from benchmarks.acceptance_scale import build_scenarios
from geoplan.budget import PLANNING_SECONDS
from geoplan.domain.enums import OptimizationCriterion
from geoplan.planning.models import PlanRequest
from scenarios.customer import customer_polygons, customer_request, local_batch, stratified_polygons
from scenarios.neighbor_fields import neighbor_fields_request
from scenarios.optimization_scale import scale_scenarios


def post(url: str, path: str, payload: dict, *, stream: bool = False) -> tuple[int, dict, dict]:
    request = Request(
        url + path,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/x-ndjson" if stream else "application/json",
        },
    )
    started = perf_counter()
    timeout = float(payload.get("planning_time_limit_seconds", PLANNING_SECONDS)) + 30
    try:
        response = urlopen(request, timeout=timeout)
    except HTTPError as error:
        return error.code, json.load(error), {}
    with response:
        if stream and response.headers.get_content_type() == "application/x-ndjson":
            events, first, last, error = [], None, None, None
            started_seconds = None
            candidate_audits = []
            for line in response:
                if perf_counter() - started > timeout:
                    raise TimeoutError("Progressive request exceeded its delivery deadline")
                event = json.loads(line)
                events.append(event["kind"])
                if event["kind"] == "started":
                    started_seconds = perf_counter() - started
                if "plan" in event:
                    candidate_audits.append(event["plan"].get("safety_audit", {}).get("status"))
                    if first is None:
                        first = perf_counter() - started
                    last = event["plan"]
                if event["kind"] == "error":
                    error = event["error"]
            return (
                response.status,
                last or error or {},
                {
                    "events": events,
                    "started_seconds": started_seconds,
                    "candidate_audits": candidate_audits,
                    "first_audited_seconds": first,
                    "stream_error": error,
                    "completed": "completed" in events,
                },
            )
        return response.status, json.load(response), {}


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * quantile + 0.5))]


def memory_mib(value: str) -> float:
    number = value.split("/")[0].strip()
    for unit, scale in (("GiB", 1024), ("MiB", 1), ("KiB", 1 / 1024), ("B", 1 / 1024**2)):
        if number.endswith(unit):
            return float(number.removesuffix(unit)) * scale
    raise ValueError(f"Unknown Docker memory unit: {number}")


class Monitor:
    def __init__(self, url: str, container: str | None) -> None:
        self.url, self.container = url, container
        self.stop = Event()
        self.samples: list[dict] = []
        self.worker = Thread(target=self.run, daemon=True)

    def run(self) -> None:
        while not self.stop.is_set():
            row: dict[str, Any] = {}
            started = perf_counter()
            try:
                with urlopen(self.url + "/health", timeout=5) as response:
                    row["health_status"] = response.status
                    row["health_seconds"] = perf_counter() - started
            except Exception as error:
                row["health_error"] = type(error).__name__
            if self.container:
                try:
                    stats = json.loads(
                        subprocess.check_output(
                            [
                                "docker",
                                "stats",
                                "--no-stream",
                                "--format",
                                "{{json .}}",
                                self.container,
                            ],
                            text=True,
                            timeout=8,
                        )
                    )
                    row["container_memory_mib"] = memory_mib(stats["MemUsage"])
                    row["container_cpu_percent"] = stats["CPUPerc"]
                except Exception as error:
                    row["stats_error"] = type(error).__name__
            self.samples.append(row)
            self.stop.wait(0.5)

    def finish(self) -> dict:
        self.stop.set()
        self.worker.join(timeout=10)
        times = [s["health_seconds"] for s in self.samples if "health_seconds" in s]
        memories = [s["container_memory_mib"] for s in self.samples if "container_memory_mib" in s]
        return {
            "samples": self.samples,
            "health_errors": sum("health_error" in s for s in self.samples),
            "health_p95_seconds": percentile(times, 0.95),
            "health_max_seconds": max(times, default=None),
            "container_peak_sampled_mib": max(memories, default=None),
        }


@dataclass
class Recorder:
    url: str
    output: Path
    container: str | None

    def write(self, row: dict) -> None:
        with self.output.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps({k: v for k, v in row.items() if k != "monitor"}), flush=True)

    def run(self, name: str, query: PlanRequest, *, stream: bool = False) -> tuple[dict, dict]:
        query = PlanRequest.model_validate(query.model_dump())
        payload = query.model_dump(mode="json")
        artifacts = self.output.parent / self.output.stem
        artifacts.mkdir(exist_ok=True)
        (artifacts / f"{name}-request.json").write_text(query.model_dump_json(), encoding="utf-8")
        started = perf_counter()
        row: dict[str, Any] = {
            "name": name,
            "criterion": query.criterion.value,
            "areas": len(query.coverage_areas),
            "fleet_size": len(query.drones),
            "budget_seconds": query.planning_time_limit_seconds,
            "stream": stream,
        }
        body: dict = {}
        try:
            status, body, delivery = post(self.url, "/v1/plans", payload, stream=stream)
            row.update(status=status, **delivery)
            if "metrics" in body:
                missions = [m for d in body["drone_plans"] for m in d["missions"]]
                checks = body["safety_audit"]["checks"]
                row.update(
                    metrics=body["metrics"],
                    audit=body["safety_audit"]["status"],
                    checks=checks,
                    feasibility=body["feasibility"]["status"],
                    search=body.get("search"),
                    frame_count=sum(m.get("image_count") or 0 for m in missions),
                    transit_minutes=sum(m.get("transit_duration_minutes") or 0 for m in missions),
                    fleet_resource_limits={
                        d.drone_id: d.resource_limit().model_dump() for d in query.drones
                    },
                    resource_usage={
                        d.drone_id: {
                            "sorties": sum(m["drone_id"] == d.drone_id for m in missions),
                            "minutes": sum(
                                m["duration_minutes"]
                                for m in missions
                                if m["drone_id"] == d.drone_id
                            ),
                        }
                        for d in query.drones
                    },
                    input_fingerprint_matches=(
                        body.get("search", {}).get("request_fingerprint")
                        == hashlib.sha256(query.model_dump_json().encode()).hexdigest()
                    ),
                    engine_build_revision=body.get("engine_build_revision"),
                )
            else:
                row["error"] = body
            with gzip.open(
                artifacts / f"{name}-response.json.gz", "wt", encoding="utf-8"
            ) as target:
                json.dump(body, target, ensure_ascii=False)
        except Exception as error:
            row["exception"] = f"{type(error).__name__}: {error}"
        row["seconds"] = perf_counter() - started
        return row, body

    def inspect(self, row: dict, query: PlanRequest, plan: dict) -> None:
        if "metrics" not in plan:
            return
        started = perf_counter()
        status, quality, _ = post(
            self.url, "/v1/survey-quality", {"request": query.model_dump(mode="json"), "plan": plan}
        )
        row["photography"] = {k: v for k, v in quality.items() if k != "features"}
        row["photography_status"] = status
        exports = []
        for drone in plan["drone_plans"]:
            if not drone["missions"]:
                continue
            for kind in ("geojson", "kml"):
                status, _, _ = post(
                    self.url, f"/v1/exports/{kind}", {"plan": plan, "drone_id": drone["drone_id"]}
                )
                exports.append({"drone": drone["drone_id"], "kind": kind, "status": status})
            for kind in ("qgroundcontrol", "mission-planner"):
                request = Request(
                    self.url + "/v1/exports/flight-package",
                    data=json.dumps(
                        {"plan": plan, "drone_id": drone["drone_id"], "format": kind}
                    ).encode(),
                    headers={"Content-Type": "application/json"},
                )
                entry: dict[str, Any] = {"drone": drone["drone_id"], "kind": kind}
                try:
                    with urlopen(request, timeout=120) as response:
                        with ZipFile(BytesIO(response.read())) as archive:
                            manifest = json.loads(archive.read("manifest.json"))
                            sorties = manifest["sorties"]
                            entry.update(
                                status=response.status,
                                sorties=len(sorties),
                                integrity=all(
                                    hashlib.sha256(archive.read(s["file"])).hexdigest()
                                    == s["sha256"]
                                    for s in sorties
                                )
                                and len(sorties) == len(drone["missions"]),
                            )
                except HTTPError as error:
                    entry.update(status=error.code, error=json.load(error))
                exports.append(entry)
        row["exports"] = exports
        row["inspection_seconds"] = perf_counter() - started


def quality(
    recorder: Recorder,
    counts: list[int],
    budget: float,
    names: list[str] | None,
    *,
    stream: bool = False,
) -> None:
    jobs: list[tuple[str, PlanRequest]] = []
    flat = next(s.request for s in scale_scenarios() if s.name == "flat_100")
    for criterion in OptimizationCriterion:
        jobs.append(
            (
                f"flat100-{criterion.value}",
                flat.model_copy(
                    update={
                        "criterion": criterion,
                        "completion_deadline_minutes": (
                            180 if criterion is OptimizationCriterion.BALANCED else None
                        ),
                    }
                ),
            )
        )
    for case in build_scenarios()[1:]:
        jobs.append(
            (
                f"{case.name}-completion_time",
                case.request.model_copy(update={"productive_routing": True}),
            )
        )
    for battery_sets in (2, 4):
        jobs.append(
            (
                "flat100-finite-fleet" + ("-4" if battery_sets == 4 else ""),
                flat.model_copy(
                    update={
                        "drones": [
                            d.model_copy(
                                update={
                                    "available_battery_sets": battery_sets,
                                    "maintenance_interval_flights": battery_sets + 2,
                                    "completed_flights_since_maintenance": 1,
                                    "maintenance_interval_hours": 8,
                                    "flight_hours_since_maintenance": 1,
                                }
                            )
                            for d in flat.drones
                        ]
                    }
                ),
            )
        )
    source = Path("../reference/hackathon/Границы полетов.kml")
    if counts:
        polygons = customer_polygons(source)
        seed = stratified_polygons(polygons, 7)[1]
        for count in counts:
            query = customer_request(
                local_batch(polygons, seed, count), criterion=OptimizationCriterion.COMPLETION_TIME
            )
            jobs.append((f"customer-{count}", query))
    for name, query in jobs:
        if names and name not in names:
            continue
        query = query.model_copy(update={"planning_time_limit_seconds": budget})
        monitor = Monitor(recorder.url, recorder.container)
        monitor.worker.start()
        row, plan = recorder.run(name, query, stream=stream)
        row["monitor"] = monitor.finish()
        try:
            recorder.inspect(row, query, plan)
        except Exception as error:
            row["inspection_error"] = f"{type(error).__name__}: {error}"
        recorder.write(row)


def load(recorder: Recorder, users: list[int], budget: float) -> None:
    flat = next(s.request for s in scale_scenarios() if s.name == "flat_100")
    for count in users:
        barrier = Barrier(count)

        def client(index: int, count: int = count, barrier: Barrier = barrier) -> dict:
            query = flat.model_copy(
                update={
                    "planning_time_limit_seconds": budget,
                    "completion_deadline_minutes": 180 + index,
                }
            )
            barrier.wait(timeout=30)
            row, _ = recorder.run(f"load-{count}-{index}", query, stream=index % 2 == 0)
            return row

        monitor = Monitor(recorder.url, recorder.container)
        monitor.worker.start()
        with ThreadPoolExecutor(max_workers=count) as pool:
            rows = list(pool.map(client, range(count)))
        recorder.write({"name": f"load-{count}", "clients": rows, "monitor": monitor.finish()})

    # Disconnect an accepted progressive request, then verify slot recovery with two clients.
    request = Request(
        recorder.url + "/v1/plans",
        data=flat.model_dump_json().encode(),
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
    )
    with urlopen(request, timeout=30) as response:
        first = json.loads(response.readline())
    attempts = []
    started = perf_counter()
    for attempt in range(15):
        query = neighbor_fields_request(planning_time_limit_seconds=10)
        gate = Barrier(2)

        def recover(
            index: int, attempt: int = attempt, query: PlanRequest = query, gate: Barrier = gate
        ) -> dict:
            gate.wait(timeout=10)
            return recorder.run(f"recovery-{attempt}-{index}", query)[0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(recover, range(2)))
        attempts.append(rows)
        if all(r.get("audit") == "passed" for r in rows):
            break
        Event().wait(1)
    recorder.write(
        {
            "name": "disconnect-recovery",
            "first_event": first,
            "seconds": perf_counter() - started,
            "attempts": attempts,
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("quality", "load"))
    parser.add_argument("--url", default="http://127.0.0.1:8002")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--container")
    parser.add_argument("--budget", type=float, default=PLANNING_SECONDS)
    parser.add_argument("--counts", type=int, nargs="*", default=[32, 64, 128])
    parser.add_argument("--names", nargs="+")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--users", type=int, nargs="+", default=[2, 4, 8])
    args = parser.parse_args()
    if args.output.exists() or not 1 <= args.budget <= PLANNING_SECONDS:
        parser.error(f"Use a new output and budget between 1 and {PLANNING_SECONDS} seconds")
    if any(n < 1 or n > 32 for n in args.users) or any(n < 1 or n > 561 for n in args.counts):
        parser.error("Users: 1..32, polygon count: 1..561")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    ready_started = perf_counter()
    while True:
        try:
            with urlopen(args.url.rstrip("/") + "/health", timeout=2) as response:
                if response.status == 200:
                    break
        except OSError:
            pass
        if perf_counter() - ready_started > 30:
            raise RuntimeError("Waited 30 seconds for a healthy service")
        Event().wait(0.5)
    artifacts = args.output.parent / args.output.stem
    artifacts.mkdir(exist_ok=True)
    (artifacts / "harness.py").write_bytes(Path(__file__).read_bytes())
    recorder = Recorder(args.url.rstrip("/"), args.output, args.container)
    recorder.write(
        {
            "name": "provenance",
            "arguments": vars(args) | {"output": str(args.output)},
            "python": platform.python_version(),
            "code_sha256": {
                str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(Path("src/geoplan").rglob("*.py"))
            },
            "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "customer_source_sha256": hashlib.sha256(
                Path("../reference/hackathon/Границы полетов.kml").read_bytes()
            ).hexdigest(),
        }
    )
    if args.phase == "quality":
        quality(recorder, args.counts, args.budget, args.names, stream=args.stream)
    else:
        load(recorder, args.users, args.budget)


if __name__ == "__main__":
    main()
