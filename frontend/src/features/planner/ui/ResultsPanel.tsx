import { TerrainAltitudeProfile } from "@/features/terrain/ui/TerrainAltitudeProfile";
import { Download, Plane, Route, ShieldCheck, ShieldX, Sparkles } from "lucide-react";
import { useState } from "react";
import { ForwardFlightAltitudeProfile } from "@/features/planner/ui/ForwardFlightAltitudeProfile";
import type { ReactNode } from "react";

import { radiusSamples } from "@/features/planner/model/radii";
import type { DroneProfile } from "@/features/planner/model/types";
import { PlanSearchProgress } from "./PlanSearchProgress";
import type { ImportedZone, PlanResponse, TurnRadiusAnalysisResult } from "@/shared/api/types";

interface ResultsPanelProps {
  planningIssue?: string | null;
  archived?: boolean;
  terrainMode?: boolean;
  insights?: ReactNode;
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
  cp_sat: "Распределение CP-SAT",
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
  fleet_resources: "Комплекты АКБ и ресурс до ТО",
  coverage_completeness: "Полнота покрытия",
  drone_separation: "Разведение БВС",
  export_consistency: "Экспорт маршрута",
  flight_envelope: "Эксплуатационный конверт",
  no_fly_clearance: "Запретные зоны",
  obstacle_clearance: "Препятствия",
  schedule_integrity: "Расписание",
  survey_resolution: "Разрешение съёмки (GSD)",
  camera_timing: "Интервалы и команды камеры",
  vertical_profile: "Рельеф и AGL",
};

export function ResultsPanel({
  planningIssue,
  archived = false,
  terrainMode = false,
  insights,
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
  const forwardExportBlocked = selectedPlan?.missions.some((mission) => mission.forward_flight_phases != null) ?? false;
  const releaseBlocked = !plan?.export_authorization || plan.safety_audit?.status !== "passed" || plan.feasibility?.status !== "feasible";
  const forwardMission = selectedPlan?.missions.find((mission) => mission.forward_flight_phases != null);
  const coverageAngleSummary = plan?.optimization?.coverage_angle_cost === "wind_adjusted_duration"
    ? `Направление галсов выбрано по времени с учётом ветра · опорная скорость ${format(plan.optimization.coverage_reference_airspeed_kmh ?? 0)} км/ч`
    : "Направление галсов выбрано по геометрической длине";
  return (
    <aside className="panel results-panel" id="mission-results" tabIndex={-1}>
      <div className="eyebrow"><Route size={15} /> Результат оптимизации</div>
      <h2>{plan ? "Расчётный план" : "Ожидает расчёта"}</h2>
      {planningIssue && <section className="planning-issue" role="alert">
        <b>{plan ? "Поиск завершён с сообщением" : "План пока не построен"}</b>
        <p>{planningIssue}</p>
        <p>Измените указанные условия и повторите расчёт. В блоке «Качество съёмки и варианты выполнения» можно проверить альтернативы.</p>
        <a href="#mission-inputs">Изменить условия задания →</a>
      </section>}
      {archived && <p className="hint" role="note">Архивный результат: условия и качество не перепроверялись при открытии. Для экспорта в автопилот выполните новый расчёт.</p>}
      <p className="lead">{plan?.feasibility?.message ?? "Настройте сценарий и запустите расчёт — демонстрационные цифры не используются."}</p>
      {plan && <p className="hint">{plan.criterion === "balanced"
        ? "Приоритет: минимальный налёт в заданный срок. Если срок недостижим — минимальное превышение."
        : plan.criterion === "completion_time"
          ? "Приоритет: завершить быстрее; среди равных вариантов — меньше налёта."
          : "Приоритет: минимальный налёт; среди равных вариантов — завершить быстрее."}</p>}

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
      {plan && <p className="fleet-summary"><Plane size={16} /> {plan.metrics.active_drone_count} БВС в работе · {plan.metrics.sortie_count} вылетов <span>Расчётное время полётов; длительность поиска показана отдельно.</span></p>}

      <PlanSearchProgress search={plan?.search} />
      {plan?.optimization && (
        <div className="algorithm-card">
          <Sparkles size={17} />
          <span><b>{algorithmLabels[plan.optimization.algorithm]}</b><small>{plan.optimization.algorithm === "productive_route_pool" ? "Сравнение вариантов вылетов после расчёта безопасного расписания" : plan.optimization.optimality === "proven_optimal" ? "Распределение оптимально для рассчитанных безопасных маршрутов" : "Маршруты оптимизированы, распределение приближённое из-за размера задачи"}</small><small>{plan.optimization.scope === "freeform_coverage_route_pool" ? "Контуры и прямые проходы; учтены запуск и посадка" : coverageAngleSummary} · обход по графу видимости</small><small>{Object.values(plan.optimization.turn_radii_by_drone ?? {}).some((radius) => radius > 0) ? `Радиусы по бортам: ${Object.entries(plan.optimization.turn_radii_by_drone ?? {}).map(([id, radius]) => `${id}: ${format(radius)} м`).join(", ")}.` : plan.optimization.survey_turn_radius_m > 0 ? `Развороты, транзит и подход к посадке рассчитаны для радиуса ${format(plan.optimization.survey_turn_radius_m)} м.` : "Повороты показаны ломаной: ограничение радиуса отключено (0 м)."}</small></span>
        </div>
      )}

      {plan?.optimization?.assignment_search && (
        <p className="hint">{plan.optimization.assignment_search.message} Поиск назначений: {format(plan.optimization.assignment_search.runtime_seconds)} с. Последующая оптимизация вылетов может дополнительно улучшить план.</p>
      )}

      {plan?.optimization?.productive_routing && (
        <div className="algorithm-card">
          <Route size={17} />
          <span>
            <b>{plan.optimization.scope === "freeform_coverage_route_pool" ? "Свободное покрытие" : "Сравнение вылетов"}</b>
            {(plan.optimization.productive_routing.cross_area_sortie_count ?? 0) > 0 && <small>Вылетов по нескольким областям: {plan.optimization.productive_routing.cross_area_sortie_count}</small>}
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
          <small>Проверено миссий: {plan.safety_audit.audited_mission_count}.</small>
          {plan.safety_audit.assumptions.length > 0 && <details>
            <summary>Допущения и границы проверки</summary>
            {plan.safety_audit.assumptions.map((assumption) => <p className="hint" key={assumption}>{assumption}</p>)}
          </details>}
        </section>
      )}

      {insights}
      <section className="radius-analysis-card">
        <b>Допущение о радиусе разворота</b>
        <p>Это отдельный анализ диапазона радиусов: он не меняет уже рассчитанный план. Радиус основного плана задаётся слева и учитывается при построении и экспорте маршрута; 0 м отключает ограничение.</p>
        <label className="field"><span>Рабочая зона</span><select value={selectedAreaId} onChange={(event) => setAreaId(event.target.value)}>{workAreas.map((area) => <option key={area.zone_id} value={area.zone_id}>{area.name}</option>)}</select></label>
        <div className="field-grid">
          <label className="field"><span>От, м</span><input type="number" min="0" max="10000" step="any" value={minimumRadius} onChange={(event) => setMinimumRadius(Number(event.target.value))} /></label>
          <label className="field"><span>До, м</span><input type="number" min="0" max="10000" step="any" value={maximumRadius} onChange={(event) => setMaximumRadius(Number(event.target.value))} /></label>
        </div>
        <button className="secondary-button" type="button" disabled={terrainMode || !plan || !radiusRangeValid || !selectedAreaId || analyzingRadii} onClick={() => onAnalyzeRadii(selectedAreaId, minimumRadius, maximumRadius)}>{analyzingRadii ? "Проверяем…" : "Проверить диапазон"}</button>
        {terrainMode && <p className="hint">Сравнение радиусов доступно для плоской поверхности MSL. Над DEM заданный радиус учитывается в основном расчёте.</p>}
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
          if (dronePlan.mission_count === 0) return (
            <button type="button" key={dronePlan.drone_id} className={`mission-card ${dronePlan.drone_id === selectedDroneId ? "active" : ""}`} onClick={() => onSelectDrone(dronePlan.drone_id)}>
              <span className="mission-title"><Plane size={16} style={{ color: drone?.color }} /><b>{drone?.name ?? dronePlan.drone_id}</b><em>Не используется</em></span>
              <span className="mission-details">Борт передан в расчёт; в итоговом плане вылеты не назначены.</span>
            </button>
          );
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
          const forwardPhases = dronePlan.missions.flatMap((mission) => mission.forward_flight_phases ? [mission.forward_flight_phases] : []);
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
              {forwardPhases.length > 0 && <span className="mission-details">Самолётный профиль: набор до {format(Math.max(...forwardPhases.map((p) => p.maximum_climb_rate_mps)))} м/с · снижение до {format(Math.max(...forwardPhases.map((p) => p.maximum_descent_rate_mps)))} м/с · выполняются во время перелётов, без добавления времени · GIS-проверка</span>}
              {maximumVerticalLayer > 0 && <span className="mission-details">Вертикальное эшелонирование: до +{format(maximumVerticalLayer)} м · набор/снижение {format(verticalPhaseMinutes)} мин включены в расписание и АКБ</span>}
              {maximumVerticalLayer === 0 && verticalPhaseMinutes > 0 && <span className="mission-details">Взлёт и посадка: {format(verticalPhaseMinutes)} мин включены в расписание и АКБ</span>}
              <span className="battery-bar"><i style={{ width: `${Math.min(maximumBattery, 100)}%`, background: drone?.color ?? "#6d28d9" }} /></span>
              {drone?.availableBatterySets != null && <span className="mission-details">Готовые комплекты АКБ: {dronePlan.mission_count} / {drone.availableBatterySets} использовано</span>}
              {dronePlan.maintenance && <span className="mission-details">{dronePlan.maintenance.message}</span>}
              {dronePlan.maintenance?.maintenance_due_after_plan && <span className="maintenance-warning">После плана требуется ТО</span>}
            </button>
          );
        }) ?? <div className="empty-state">После расчёта здесь появятся индивидуальные задания БВС.</div>}
      </div>

      {forwardMission && <ForwardFlightAltitudeProfile mission={forwardMission} />}
      {selectedPlan && <TerrainAltitudeProfile key={selectedDroneId} missions={selectedPlan.missions} />}
      <div className="export-group" id="mission-exports" tabIndex={-1}>
        <button type="button" disabled={exportDisabled} onClick={() => onExport("geojson")}><Download size={15} /> GeoJSON</button>
        <button type="button" disabled={exportDisabled} onClick={() => onExport("kml")}><Download size={15} /> KML</button>
        <button type="button" disabled={exportDisabled || forwardExportBlocked || releaseBlocked} onClick={() => onExport("qgroundcontrol")}><Download size={15} /> QGroundControl</button>
        <button type="button" disabled={exportDisabled || forwardExportBlocked || releaseBlocked} onClick={() => onExport("mission-planner")}><Download size={15} /> Mission Planner</button>
      </div>
      {plan && <p className="hint">{releaseBlocked
        ? "Для экспорта в автопилот нужен проверенный план, укладывающийся в заданные условия. При устаревшем подтверждении повторите расчёт. GIS доступен для анализа."
        : "Целостность расчётного плана подтверждена сервером. Совместимость с прошивкой и готовность к полёту требуют отдельной проверки."}</p>}
      {selectedPlan?.missions.some((mission) => mission.altitude_reference === "agl") && <p className="hint">AGL-экспорт требует поддержки terrain-frame в прошивке и согласованной бортовой модели рельефа. Загруженный GeoTIFF автоматически на БВС не передаётся.</p>}
      {selectedPlan && selectedPlan.missions.length > 1 && <p className="hint">Экспорт в автопилот: ZIP с отдельным файлом на каждый вылет. Откройте dispatch.html для просмотра расписания. Оператор запускает вылеты отдельно, соблюдая интервалы и подготовку АКБ.</p>}
      {forwardExportBlocked && <p className="hint">Самолётный профиль доступен в GeoJSON и KML. Автопилотный экспорт требует проверки команд взлёта и посадки под конкретную прошивку.</p>}
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
