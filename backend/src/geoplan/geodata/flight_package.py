"""One autopilot file per sortie and an explicit operator dispatch manifest."""

import hashlib
import json
from html import escape
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from geoplan.domain.enums import ErrorCode
from geoplan.errors import PlanningError
from geoplan.geodata.exporters import (
    build_mission_planner_export,
    build_qgroundcontrol_export,
)
from geoplan.geodata.models import FlightPackageRequest


class FlightPackageBuilder:
    def build(self, request: FlightPackageRequest) -> bytes:
        drone = next((d for d in request.plan.drone_plans if d.drone_id == request.drone_id), None)
        if drone is None or not drone.missions:
            raise PlanningError(ErrorCode.NO_EXPORTABLE_MISSIONS, "Нет вылетов для экспорта.")
        if request.sortie_index is not None:
            raise PlanningError(
                ErrorCode.EXPORT_SORTIE_REQUIRED,
                "Пакет содержит все вылеты выбранного БВС; не задавайте sortie_index.",
            )
        indices = sorted(
            range(len(drone.missions)),
            key=lambda i: drone.missions[i].start_offset_minutes or 0,
        )
        entries = []
        files: list[tuple[str, str]] = []
        previous_end = 0.0
        for number, index in enumerate(indices, start=1):
            mission = drone.missions[index]
            if mission.start_offset_minutes is None or mission.end_offset_minutes is None:
                raise PlanningError(
                    ErrorCode.EXPORT_RELEASE_REQUIRED, "Расписание вылетов отсутствует."
                )
            if mission.start_offset_minutes < previous_end - 1e-7:
                raise PlanningError(ErrorCode.EXPORT_RELEASE_REQUIRED, "Вылеты БВС пересекаются.")
            selected = request.model_copy(update={"sortie_index": index})
            exporter = (
                build_qgroundcontrol_export
                if request.format == "qgroundcontrol"
                else build_mission_planner_export
            )
            exported = exporter(selected)
            suffix = "plan" if request.format == "qgroundcontrol" else "waypoints"
            filename = f"sorties/flight-{number:04d}.{suffix}"
            files.append((filename, exported.content))
            entries.append(
                {
                    "sequence": number,
                    "sortie_index": index,
                    "segment_id": mission.segment_id,
                    "file": filename,
                    "sha256": hashlib.sha256(exported.content.encode()).hexdigest(),
                    "start_offset_minutes": mission.start_offset_minutes,
                    "end_offset_minutes": mission.end_offset_minutes,
                    "start_time": mission.start_time.isoformat() if mission.start_time else None,
                    "end_time": mission.end_time.isoformat() if mission.end_time else None,
                    "ground_interval_before_minutes": mission.start_offset_minutes - previous_end,
                    "departure": mission.route[0].model_dump(mode="json"),
                    "arrival": mission.route[-1].model_dump(mode="json"),
                    "starting_battery_percent": mission.starting_battery_percent,
                    "estimated_battery_percent": mission.estimated_battery_percent,
                    "remaining_battery_percent": mission.remaining_battery_percent,
                }
            )
            previous_end = mission.end_offset_minutes
        manifest = {
            "schema_version": 1,
            "drone_id": drone.drone_id,
            "format": request.format,
            "request_fingerprint": request.plan.search.request_fingerprint
            if request.plan.search
            else None,
            "autopilot_profile": drone.autopilot_profile.model_dump(mode="json"),
            "sorties": entries,
            "execution_policy": "operator_dispatch_each_sortie",
            "warnings": [
                "Расписание не исполняется файлами автопилота автоматически. "
                "Оператор загружает и запускает каждый вылет отдельно по общему плану.",
                "Наземные интервалы включают плановое ожидание и обслуживание; "
                "перед следующим вылетом нужно обеспечить указанный заряд АКБ.",
                "Подтверждение целостности плана не подтверждает совместимость прошивки "
                "и не является разрешением на полёт.",
                "Расписание выбранного БВС является частью общего группового плана. "
                "Нельзя независимо сдвигать вылеты без повторной проверки разведения.",
            ],
            "assumptions": request.plan.safety_audit.assumptions
            if request.plan.safety_audit
            else [],
        }
        buffer = BytesIO()
        with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.writestr(
                "calculated-plan.json",
                request.plan.model_dump_json(exclude={"export_authorization"}, indent=2),
            )
            for filename, content in files:
                archive.writestr(filename, content)
            rows = "".join(
                "<tr>"
                f'<td>{e["sequence"]}</td><td><a href="{e["file"]}">'
                f"{escape(str(e['segment_id']))}</a></td>"
                f"<td>{e['start_offset_minutes']:.2f}</td><td>{e['end_offset_minutes']:.2f}</td>"
                f"<td>{escape(str(e['start_time'] or '—'))}</td>"
                f"<td>{e['ground_interval_before_minutes']:.2f}</td>"
                f"<td>{e['starting_battery_percent']:g}%</td></tr>"
                for e in entries
            )
            warnings = "".join(f"<li>{escape(w)}</li>" for w in manifest["warnings"])
            archive.writestr(
                "dispatch.html",
                '<!doctype html><html lang="ru"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                "<title>Порядок выполнения вылетов</title><style>"
                "body{font:16px system-ui;margin:2rem;max-width:1200px;color:#172033}"
                "table{border-collapse:collapse;width:100%}th,td{padding:12px;text-align:left;"
                "border:1px solid #ccd3dd}th{background:#eef2f6}li{margin:12px 0}"
                "</style><h1>Порядок выполнения вылетов</h1>"
                f"<p>БВС: {escape(drone.drone_id)}</p>"
                "<p>Отсчёт минут — от начала общего плана. Абсолютное время включает часовой "
                "пояс, если оно задано в исходном задании.</p><ul>"
                f"{warnings}</ul><table><thead><tr><th>№</th><th>Файл вылета</th>"
                "<th>Начало, мин</th><th>Конец, мин</th><th>Начало, дата и время</th>"
                "<th>Интервал на земле до вылета, мин</th><th>Заряд перед стартом</th>"
                f"</tr></thead><tbody>{rows}</tbody></table></html>",
            )
        return buffer.getvalue()
