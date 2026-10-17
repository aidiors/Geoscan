import { Download, Plane, Route, ShieldCheck, ShieldX, Sparkles } from "lucide-react";
import { useState } from "react";

import { radiusSamples } from "@/features/planner/model/radii";
import type { DroneProfile } from "@/features/planner/model/types";
import type { ImportedZone, PlanResponse, TurnRadiusAnalysisResult } from "@/shared/api/types";

interface ResultsPanelProps {
  plan: PlanResponse | null;
  drones: DroneProfile[];
  selectedDroneId: string;
  exporting: boolean;
  workAreas: ImportedZone[];
  radiusAnalysis: TurnRadiusAnalysisResult | null;
  analyzingRadii: boolean;
  onAnalyzeRadii: (areaId: string, minimumRadius: number, maximumRadius: number) => void;
  onSelectDrone: (droneId: string) => void;
  onExport: (format: "geojson" | "kml" | "qgroundcontrol" | "mission-planner") => void;
}

const algorithmLabels: Record<NonNullable<PlanResponse["optimization"]>["algorithm"], string> = {
  productive_route_pool: "Оптимизация галсов и вылетов",
  exact_branch_and_bound: "Точное распределение branch-and-bound",
  fast_greedy: "Быстрая эвристика",
  multistart_local_search: "Многопроходная оптимизация",
  minimum_cost: "Минимальная стоимость",
  deadline_aware_local_search: "Поиск под заданный срок",
};

const safetyCheckLabels: Record<NonNullable<PlanResponse["safety_audit"]>["checks"][number]["code"], string> = {
  airspace_compliance: "Воздушное пространство",
  alternate_reserve: "Резервный аэродром",
  battery_reserve: "Резерв АКБ",
  coverage_completeness: "Полнота покрытия",
  drone_separation: "Разведение БВС",
  export_consistency: "Экспорт маршрута",
  flight_envelope: "Эксплуатационный конверт",
  no_fly_clearance: "Запретные зоны",
  obstacle_clearance: "Препятствия",
  schedule_integrity: "Расписание",
  vertical_profile: "Рельеф и AGL",
};

export function ResultsPanel({
  plan,
  drones,
  selectedDroneId,
  exporting,
  workAreas,
  radiusAnalysis,
  analyzingRadii,
  onAnalyzeRadii,
  onSelectDrone,
  onExport,
}: ResultsPanelProps) {
  const [areaId, setAreaId] = useState("");
  const [minimumRadius, setMinimumRadius] = useState(0);
  const [maximumRadius, setMaximumRadius] = useState(0);
  const selectedAreaId = workAreas.some((area) => area.zone_id === areaId) ? areaId : (workAreas[0]?.zone_id ?? "");
  const radiusRangeValid = Number.isFinite(minimumRadius) && Number.isFinite(maximumRadius) && minimumRadius >= 0 && minimumRadius <= maximumRadius && maximumRadius <= 10000;
  const expectedRadii = radiusSamples(minimumRadius, maximumRadius);
  const radiusAnalysisMatchesInputs = radiusAnalysis?.area_id === selectedAreaId
    && radiusRangeValid
    && radiusAnalysis.scenarios.length === expectedRadii.length
    && radiusAnalysis.scenarios.every((scenario, index) => scenario.radius_m === expectedRadii[index]);
  const hasPartialRadiusResult = radiusAnalysisMatchesInputs
    && radiusAnalysis.common_survey_route === null
    && radiusAnalysis.scenarios.some((scenario) => scenario.status === "feasible")
    && radiusAnalysis.scenarios.at(-1)?.status !== "feasible";
  const selectedPlan = plan?.drone_plans.find((dronePlan) => dronePlan.drone_id === selectedDroneId);
  const exportDisabled = exporting || !selectedPlan || selectedPlan.mission_count === 0;
  const coverageAngleSummary = plan?.optimization?.coverage_angle_cost === "wind_adjusted_duration"
    ? `Направление галсов выбрано по времени с учётом ветра · опорная скорость ${format(plan.optimization.coverage_reference_airspeed_kmh ?? 0)} км/ч`
    : "Направление галсов выбрано по геометрической длине";
  return (
    <aside className="panel results-panel">
      <div className="eyebrow"><Route size={15} /> Результат оптимизации</div>
      <h2>{plan ? "План готов" : "Ожидает расчёта"}</h2>
      <p className="lead">{plan?.feasibility?.message ?? "Настройте сценарий и запустите расчёт — демонстрационные цифры не используются."}</p>

      {plan?.feasibility && plan.feasibility.recommendations.length > 0 && (
        <section className="recommendation-card">
          <b>Как сделать план выполнимым</b>
          {plan.feasibility.recommendations.map((recommendation) => (
            <div key={`${recommendation.code}-${recommendation.parameter ?? "general"}`}>
              <span>{recommendation.message}</span>
              <small>{recommendation.confidence === "exact" ? "точный контрфактуал" : "оценка — требуется пересчёт"}</small>
            </div>
          ))}
        </section>
      )}

      <div className="metrics">
        <Metric label="Срок" value={plan ? `${format(plan.metrics.completion_minutes)} мин` : "—"} detail="до завершения" />
        <Metric label="Налёт" value={plan ? `${format(plan.metrics.total_flight_minutes)} мин` : "—"} detail="по флоту" />
        <Metric label="Маршрут" value={plan ? `${format(plan.metrics.total_distance_km)} км` : "—"} detail={plan ? `${format(plan.metrics.useful_flight_percent)}% полезного пути` : "общая дистанция"} />
      </div>

      {plan?.optimization && (
        <div className="algorithm-card">
          <Sparkles size={17} />
          <span><b>{algorithmLabels[plan.optimization.algorithm]}</b><small>{plan.optimization.algorithm === "productive_route_pool" ? "Сравнение вариантов вылетов после расчёта безопасного расписания" : plan.optimization.optimality === "proven_optimal" ? "Распределение оптимально для рассчитанных безопасных маршрутов" : "Маршруты оптимизированы, распределение приближённое из-за размера задачи"}</small><small>{plan.optimization.scope === "freeform_coverage_route_pool" ? "Контуры и прямые проходы; учтены запуск и посадка" : coverageAngleSummary} · обход по графу видимости</small><small>{plan.optimization.survey_turn_radius_m > 0 ? `Развороты, транзит и подход к посадке рассчитаны для радиуса ${format(plan.optimization.survey_turn_radius_m)} м.` : "Повороты показаны ломаной: ограничение радиуса отключено (0 м)."}</small></span>
        </div>
      )}

      {plan?.optimization?.productive_routing && (
        <div className="algorithm-card">
          <Route size={17} />
          <span>
            <b>{plan.optimization.scope === "freeform_coverage_route_pool" ? "Свободное покрытие" : "Сравнение вылетов"}</b>
            <small>{plan.optimization.productive_routing.message}</small>
            {plan.optimization.productive_routing.baseline_makespan_minutes !== null && (
              <small>Исходный план: {format(plan.optimization.productive_routing.baseline_makespan_minutes)} мин до завершения · {format(plan.optimization.productive_routing.baseline_total_flight_minutes ?? 0)} мин налёта</small>
            )}
            <small>Холостой налёт: {format(plan.drone_plans.reduce((sum, drone) => sum + drone.missions.reduce((elapsed, mission) => elapsed + (mission.transit_duration_minutes ?? 0), 0), 0))} мин{plan.optimization.productive_routing.baseline_transit_minutes !== null ? ` · исходный ${format(plan.optimization.productive_routing.baseline_transit_minutes)} мин` : ""}</small>
          </span>
        </div>
      )}

      {plan?.schedule && (
        <div className="algorithm-card">
          <Route size={17} />
          <span><b>Робастное расписание вылетов</b><small>Разведение {format(plan.schedule.minimum_separation_m)} м по горизонтали / {format(plan.schedule.minimum_vertical_separation_m ?? 30)} м по вертикали · позиция ±{format(plan.schedule.position_uncertainty_m)} м · старт ±{format(plan.schedule.start_time_uncertainty_seconds)} с · скорость ±{format(plan.schedule.speed_uncertainty_percent)}%</small><small>Площадка {format(plan.schedule.flight_site_occupancy_seconds)} с · подготовка {format(plan.schedule.turnaround_minutes)} мин · эшелонировано миссий: {plan.schedule.vertically_deconflicted_mission_count ?? 0} · устранено конфликтов: {plan.schedule.resolved_conflict_count}</small></span>
        </div>
      )}

      {plan?.safety_audit && (
        <section className={`safety-audit-card ${plan.safety_audit.status}`}>
          <div className="safety-audit-title">
            {plan.safety_audit.status === "passed" ? <ShieldCheck size={18} /> : <ShieldX size={18} />}
            <span><b>Независимый аудит плана</b><small>{plan.safety_audit.status === "passed" ? "Все применимые проверки пройдены" : "Обнаружены нарушения — план нельзя выпускать"}</small></span>
          </div>
          <div className="safety-check-list">
            {plan.safety_audit.checks.map((check) => (
              <div key={check.code} className={check.status} title={check.message}>
                <span>{safetyCheckLabels[check.code]}</span>
                <b>{check.status === "passed" ? "пройдено" : check.status === "failed" ? "нарушение" : "не применимо"}</b>
              </div>
            ))}
          </div>
          <small>Проверено миссий: {plan.safety_audit.audited_mission_count}. Допущения явно перечислены в ответе API.</small>
        </section>
      )}

      <section className="radius-analysis-card">
        <b>Допущение о радиусе разворота</b>
        <p>Это отдельный анализ диапазона радиусов: он не меняет уже рассчитанный план. Радиус основного плана задаётся слева и учитывается при построении и экспорте маршрута; 0 м отключает ограничение.</p>
        <label className="field"><span>Рабочая зона</span><select value={selectedAreaId} onChange={(event) => setAreaId(event.target.value)}>{workAreas.map((area) => <option key={area.zone_id} value={area.zone_id}>{area.name}</option>)}</select></label>
        <div className="field-grid">
          <label className="field"><span>От, м</span><input type="number" min="0" max="10000" step="any" value={minimumRadius} onChange={(event) => setMinimumRadius(Number(event.target.value))} /></label>
          <label className="field"><span>До, м</span><input type="number" min="0" max="10000" step="any" value={maximumRadius} onChange={(event) => setMaximumRadius(Number(event.target.value))} /></label>
        </div>
        <button className="secondary-button" type="button" disabled={!plan || !radiusRangeValid || !selectedAreaId || analyzingRadii} onClick={() => onAnalyzeRadii(selectedAreaId, minimumRadius, maximumRadius)}>{analyzingRadii ? "Проверяем…" : "Проверить диапазон"}</button>
        {!radiusRangeValid && <small>Укажите радиус от 0 до 10 000 м; нижняя граница не больше верхней.</small>}
        {radiusAnalysisMatchesInputs && <div className="radius-analysis-result">
          {radiusAnalysis.scenarios.map((scenario) => <div key={scenario.radius_m}><b>{format(scenario.radius_m)} м</b><span title={scenario.message}>{scenario.status === "feasible" ? `${format(scenario.distance_km ?? 0)} км · построен` : scenario.status === "not_evaluated" ? "Не проверено" : "Для этих галсов не найден"}</span></div>)}
          <small>{hasPartialRadiusResult ? "Частичный результат: маршрут построен для некоторых меньших радиусов, но не для наибольшего заданного радиуса. Общего маршрута на карте нет; это не доказывает невыполнимость всей задачи." : radiusAnalysis.message}</small>
        </div>}
      </section>

      <div className="missions-heading"><span>Миссии</span><small>{plan ? `${plan.metrics.sortie_count} вылетов` : "Нет плана"}</small></div>
      <div className="mission-list">
        {plan?.drone_plans.map((dronePlan) => {
          const drone = drones.find((candidate) => candidate.id === dronePlan.drone_id);
          const maximumBattery = Math.max(0, ...dronePlan.missions.map((mission) => mission.estimated_battery_percent));
          const minimumStartingBattery = Math.min(100, ...dronePlan.missions.map((mission) => mission.starting_battery_percent));
          const reserveMargins = dronePlan.missions
            .map((mission) => mission.battery_reserve_margin_percent)
            .filter((value): value is number => value !== null);
          const minimumReserveMargin = reserveMargins.length > 0 ? Math.min(...reserveMargins) : null;
          const alternateMargins = dronePlan.missions
            .map((mission) => mission.minimum_alternate_margin_percent)
            .filter((value): value is number => value !== null);
          const minimumAlternateMargin = alternateMargins.length > 0 ? Math.min(...alternateMargins) : null;
          const alternateEvaluations = dronePlan.missions.reduce(
            (sum, mission) => sum + (mission.alternate_evaluated_checkpoint_count ?? 0),
            0,
          );
          const maximumAlternateInterval = Math.max(0, ...dronePlan.missions.map((mission) => mission.alternate_check_spacing_m ?? 0));
          const maximumAlternateError = Math.max(0, ...dronePlan.missions.map((mission) => mission.alternate_certification_error_minutes ?? 0));
          const totalDistance = dronePlan.missions.reduce((sum, mission) => sum + mission.distance_km, 0);
          const surveyDistance = dronePlan.missions.reduce((sum, mission) => sum + (mission.survey_distance_km ?? 0), 0);
          const usefulPercent = totalDistance > 0 ? surveyDistance / totalDistance * 100 : 0;
          const sweepAngles = [...new Set(dronePlan.missions.map((mission) => mission.flight_direction_degrees).filter((angle): angle is number => angle !== null))];
          const scheduledStarts = dronePlan.missions.map((mission) => mission.start_offset_minutes).filter((value): value is number => value !== null);
          const scheduledEnds = dronePlan.missions.map((mission) => mission.end_offset_minutes).filter((value): value is number => value !== null);
          const absoluteStarts = dronePlan.missions.map((mission) => mission.start_time).filter((value): value is string => Boolean(value));
          const absoluteEnds = dronePlan.missions.map((mission) => mission.end_time).filter((value): value is string => Boolean(value));
          const terrainMissions = dronePlan.missions.filter((mission) => mission.altitude_reference === "agl");
          const maximumTerrain = Math.max(...terrainMissions.map((mission) => mission.maximum_terrain_elevation_m ?? 0));
          const verticalDistance = terrainMissions.reduce((sum, mission) => sum + mission.vertical_distance_m, 0);
          const maximumVerticalLayer = Math.max(0, ...dronePlan.missions.map((mission) => mission.vertical_deconfliction_offset_m ?? 0));
          const verticalPhaseMinutes = dronePlan.missions.reduce(
            (sum, mission) => sum + mission.vertical_climb_duration_minutes + mission.vertical_descent_duration_minutes,
            0,
          );
          const scheduleText = absoluteStarts.length > 0 && absoluteEnds.length > 0
            ? ` · расписание ${formatDateTime(absoluteStarts.sort()[0])}–${formatDateTime(absoluteEnds.sort().at(-1) ?? absoluteEnds[0])}`
            : scheduledStarts.length > 0 && scheduledEnds.length > 0 ? ` · расписание ${format(Math.min(...scheduledStarts))}–${format(Math.max(...scheduledEnds))} мин` : "";
          return (
            <button type="button" key={dronePlan.drone_id} className={`mission-card ${dronePlan.drone_id === selectedDroneId ? "active" : ""}`} onClick={() => onSelectDrone(dronePlan.drone_id)}>
              <span className="mission-title"><Plane size={16} style={{ color: drone?.color }} /><b>{drone?.name ?? dronePlan.drone_id}</b><em>{dronePlan.mission_count > 0 ? `${dronePlan.mission_count} выл.` : "Не используется"}</em></span>
              <span className="mission-details">{format(dronePlan.total_duration_minutes)} мин · {format(totalDistance)} км · {format(usefulPercent)}% съёмка · АКБ старт {format(minimumStartingBattery)}%, расход до {format(maximumBattery)}%{minimumReserveMargin !== null ? ` · сверх резерва ${format(minimumReserveMargin)} п.п.` : ""}{sweepAngles.length > 0 ? ` · галсы ${sweepAngles.map((angle) => `${format(angle)}°`).join("/")}` : ""}{scheduleText}</span>
              {minimumAlternateMargin !== null && <span className="mission-details">Аварийный уход: минимум {format(minimumAlternateMargin)} п.п. сверх резерва · адаптивно проверено {alternateEvaluations} точек · интервал до {format(maximumAlternateInterval)} м · погрешность ≤ {format(maximumAlternateError)} мин</span>}
              {terrainMissions.length > 0 && <span className="mission-details">AGL-профиль: рельеф до {format(maximumTerrain)} м MSL · вертикальный путь {format(verticalDistance)} м</span>}
              {maximumVerticalLayer > 0 && <span className="mission-details">Вертикальное эшелонирование: до +{format(maximumVerticalLayer)} м · набор/снижение {format(verticalPhaseMinutes)} мин включены в расписание и АКБ</span>}
              <span className="battery-bar"><i style={{ width: `${Math.min(maximumBattery, 100)}%`, background: drone?.color ?? "#6d28d9" }} /></span>
              {dronePlan.maintenance?.maintenance_due_after_plan && <span className="maintenance-warning">После плана требуется ТО</span>}
            </button>
          );
        }) ?? <div className="empty-state">После расчёта здесь появятся индивидуальные задания БВС.</div>}
      </div>

      <div className="export-group">
        <button type="button" disabled={exportDisabled} onClick={() => onExport("geojson")}><Download size={15} /> GeoJSON</button>
        <button type="button" disabled={exportDisabled} onClick={() => onExport("kml")}><Download size={15} /> KML</button>
        <button type="button" disabled={exportDisabled} onClick={() => onExport("qgroundcontrol")}><Download size={15} /> QGroundControl</button>
        <button type="button" disabled={exportDisabled} onClick={() => onExport("mission-planner")}><Download size={15} /> Mission Planner</button>
      </div>
    </aside>
  );
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div className="metric"><small>{label}</small><strong>{value}</strong><span>{detail}</span></div>;
}

function format(value: number): string {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(value);
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
