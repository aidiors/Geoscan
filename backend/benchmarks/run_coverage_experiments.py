from __future__ import annotations

import csv
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from benchmarks.coverage import (
    BENCHMARK_ALGORITHMS,
    OPTIMISTIC_LOWER_BOUND_ALGORITHMS,
    run_coverage_benchmark,
)
from benchmarks.models import (
    CoverageBenchmarkResult,
    CoverageExperimentArtifacts,
    CoverageExperimentReport,
)
from scenarios.coverage import default_coverage_benchmark_cases


def run_experiment() -> CoverageExperimentReport:
    generated_at = datetime.now(UTC)
    cases = default_coverage_benchmark_cases()
    results = run_coverage_benchmark(cases)
    return CoverageExperimentReport(
        experiment_id=generated_at.strftime("coverage-%Y%m%d-%H%M%S"),
        generated_at_utc=generated_at,
        scenario_ids=[case.case_id for case in cases],
        algorithms=BENCHMARK_ALGORITHMS,
        results=results,
    )


def save_experiment(
    report: CoverageExperimentReport,
    output_root: Path,
) -> CoverageExperimentArtifacts:
    directory = output_root / report.experiment_id
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except OSError as error:
        raise RuntimeError(f"Could not create experiment directory {directory}.") from error
    csv_path = directory / "results.csv"
    json_path = directory / "results.json"
    markdown_path = directory / "report.md"
    _write_text(csv_path, _csv_content(report.results))
    _write_text(json_path, report.model_dump_json(indent=2))
    _write_text(markdown_path, _markdown_content(report))
    return CoverageExperimentArtifacts(
        experiment_id=report.experiment_id,
        directory=str(directory),
        csv_path=str(csv_path),
        json_path=str(json_path),
        markdown_path=str(markdown_path),
    )


def _write_text(path: Path, content: str) -> None:
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"Could not save experiment file {path}.") from error


def _csv_content(results: list[CoverageBenchmarkResult]) -> str:
    output = StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "case_id",
            "algorithm",
            "angle_degrees",
            "route_distance_km",
            "turn_count",
            "segment_count",
            "runtime_ms",
            "gap_percent",
            "geometry_valid",
            "error_message",
        ]
    )
    for result in results:
        writer.writerow(
            [
                result.case_id,
                result.algorithm,
                _csv_number(result.selected_angle_degrees),
                _csv_number(result.route_distance_km),
                result.turn_count if result.turn_count is not None else "",
                result.segment_count,
                result.runtime_ms,
                _csv_number(result.gap_to_reference_percent),
                str(result.geometry_valid).lower(),
                result.error_message or "",
            ]
        )
    return output.getvalue()


def _markdown_content(report: CoverageExperimentReport) -> str:
    lines = [
        f"# Experiment {report.experiment_id}",
        "",
        f"UTC date: {report.generated_at_utc.isoformat()}",
        "",
        f"Scenarios: {len(report.scenario_ids)}. Algorithms: {len(report.algorithms)}. "
        f"Measurements: {len(report.results)}.",
        "",
        "## Best feasible result for every scenario",
        "",
        "| Scenario | Algorithm | Distance, km | Turns | Sections |",
        "|---|---|---:|---:|---:|",
    ]
    for case_id in report.scenario_ids:
        valid_results = [
            result
            for result in report.results
            if result.case_id == case_id
            and result.geometry_valid
            and result.route_distance_km is not None
            and result.algorithm not in OPTIMISTIC_LOWER_BOUND_ALGORITHMS
        ]
        best = min(valid_results, key=_result_key)
        lines.append(
            f"| {case_id} | {best.algorithm} | {best.route_distance_km:.3f} | "
            f"{best.turn_count or 0} | {best.segment_count} |"
        )
    lines.extend(
        [
            "",
            "## All results",
            "",
            "| Scenario | Algorithm | Angle | Distance, km | Turns | Sections | "
            "Gap, % | Runtime, ms | Feasible |",
            "|---|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    lines.extend(_result_markdown_row(result) for result in report.results)
    lines.append("")
    return "\n".join(lines)


def _result_key(result: CoverageBenchmarkResult) -> tuple[float, int]:
    distance = result.route_distance_km if result.route_distance_km is not None else float("inf")
    turns = result.turn_count if result.turn_count is not None else 1_000_000_000
    return distance, turns


def _result_markdown_row(result: CoverageBenchmarkResult) -> str:
    return (
        f"| {result.case_id} | {result.algorithm} | "
        f"{_optional_number(result.selected_angle_degrees)} | "
        f"{_optional_number(result.route_distance_km)} | "
        f"{result.turn_count if result.turn_count is not None else '—'} | "
        f"{result.segment_count} | {_optional_number(result.gap_to_reference_percent)} | "
        f"{result.runtime_ms:.3f} | {'yes' if result.geometry_valid else 'no'} |"
    )


def _optional_number(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "—"


def _csv_number(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else ""


def main() -> None:
    report = run_experiment()
    repository_root = Path(__file__).resolve().parents[2]
    artifacts = save_experiment(report, repository_root / "docs" / "benchmark-results")
    print(artifacts.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
