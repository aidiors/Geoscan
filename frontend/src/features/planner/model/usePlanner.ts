import { TerrainRepository } from "@/features/terrain/api/TerrainRepository";
import type { TerrainAsset } from "@/features/terrain/model/types";
import { cloneFleet, fleetCatalog } from "./fleetCatalog";
import { useEffect, useMemo, useRef, useState } from "react";
import { analyzeTurnRadii, createPlan } from "@/features/planner/api/client";
import { exportMission } from "@/features/mission-export/api/exportMission";
import { importGeodata } from "@/features/geodata/api/importGeodata";
import { mergeZones } from "@/features/geodata/model/mergeZones";
import { defaultDemoCaseForScenario, demoCaseById, demoCases } from "@/features/demos/data/demoCases";
import { cloneSites, scenarios } from "@/features/demos/data/scenarios";
import { activeNoFlyZones, activeWorkAreas, buildPlanRequest } from "@/features/planner/api/buildPlanRequest";
import { radiusSamples } from "@/features/planner/model/radii";
import type { Coordinate } from "@/shared/geo/types";
import type { DroneProfile, FlightSiteState, Objective, PlanningSettings, SiteRole, SurveySettings, WorkAreaSource } from "@/features/planner/model/types";
import type { ImportedZone, MissionTask, PlanResponse, TurnRadiusAnalysisResult, ZoneType } from "@/shared/api/types";
import { defaultSurvey, planningSettings } from "@/features/planner/model/defaults";
import type { SavedScenario } from "@/features/planner/storage/scenario";
import { migrateLegacyDroneProfile, normalizeDroneProfile } from "@/features/planner/storage/scenario";
import { errorMessage } from "@/shared/api/errorMessage";
import { useMissionInsights } from "@/features/planner/model/useMissionInsights";
import type { PlanRequest } from "@/shared/api/types";
import type { ProjectDocument, SourceFile } from "@/features/projects/model/types";
import type { Scenario } from "@/features/planner/model/types";
import { parseSavedScenario } from "@/features/planner/storage/validateScenario";

const terrainRepository = new TerrainRepository();

export function usePlanner() {
  const [terrain, setTerrain] = useState<TerrainAsset | null>(null);
  const [terrainImporting, setTerrainImporting] = useState(false);
  const terrainImportVersion = useRef(0);
  const initialScenario = scenarios[0];
  const initialDemoCase = demoCases[0];
  const [scenarioId, setScenarioId] = useState(initialScenario.id);
  const [scenarioOverride, setScenarioOverride] = useState<Scenario | null>(null);
  const [sourceFiles, setSourceFiles] = useState<SourceFile[]>([]);
  const [demoCaseId, setDemoCaseId] = useState(initialDemoCase.id);
  const [showcaseOpen, setShowcaseOpen] = useState(false);
  const [task, setTask] = useState<MissionTask>(initialScenario.task);
  const [objective, setObjective] = useState<Objective>("flight");
  const [drones, setDrones] = useState<DroneProfile[]>(cloneFleet);
  const [selectedDroneId, setSelectedDroneId] = useState("201");
  const [sites, setSites] = useState<FlightSiteState[]>(() => cloneSites(initialScenario));
  const [importedZones, setImportedZones] = useState<ImportedZone[]>([]);
  const [workAreaSource, setWorkAreaSource] = useState<WorkAreaSource>("scenario");
  const [survey, setSurvey] = useState<SurveySettings>(defaultSurvey);
  const [settings, setSettings] = useState<PlanningSettings>(() => planningSettings(initialScenario.altitude));
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [archivedPlan, setArchivedPlan] = useState(false);
  const [lastRequest, setLastRequest] = useState<PlanRequest | null>(null);
  const planRevision = useRef(0);
  const planningController = useRef<AbortController | null>(null);
  useEffect(() => () => planningController.current?.abort(), []);
  const [radiusAnalysis, setRadiusAnalysis] = useState<TurnRadiusAnalysisResult | null>(null);
  const [analyzingRadii, setAnalyzingRadii] = useState(false);
  const radiusAnalysisRevision = useRef(0);
  const [revision, setRevision] = useState(0);
  const [status, setStatus] = useState("Сценарий готов к расчёту");
  const [pendingSiteRole, setPendingSiteRole] = useState<SiteRole | null>(null);
  const [calculating, setCalculating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const insights = useMissionInsights(lastRequest, plan, setStatus);

  const scenario = scenarioOverride?.id === scenarioId ? scenarioOverride : scenarios.find((candidate) => candidate.id === scenarioId) ?? initialScenario;
  const mapZones = useMemo(() => {
    const supplemental = importedZones.filter((zone) => zone.zone_type !== "work_area" && zone.zone_type !== "no_fly_zone");
    return [...activeWorkAreas(importedZones, scenario, workAreaSource), ...activeNoFlyZones(importedZones, scenario, workAreaSource), ...supplemental];
  }, [importedZones, scenario, workAreaSource]);
  const fitKey = `${scenarioId}:${importedZones.map((zone) => zone.zone_id).join(",")}:${revision}`;


  function invalidate(nextStatus: string): void {
    planningController.current?.abort();
    setCalculating(false);
    planRevision.current += 1;
    setLastRequest(null);
    radiusAnalysisRevision.current += 1;
    setPlan(null);
    setArchivedPlan(false);
    setRadiusAnalysis(null);
    setAnalyzingRadii(false);
    setStatus(nextStatus);
  }

  function handleTerrainChange(asset: TerrainAsset | null): void {
    terrainImportVersion.current += 1;
    setTerrainImporting(false);
    setTerrain(asset);
    invalidate("DEM изменён — требуется новый расчёт");
  }

  async function handleImportTerrain(file: File, band: number): Promise<void> {
    const version = ++terrainImportVersion.current;
    const revision = planRevision.current;
    setTerrainImporting(true);
    setStatus("Читаем и проверяем GeoTIFF…");
    try {
      const asset = await terrainRepository.load(file, band);
      if (version !== terrainImportVersion.current || revision !== planRevision.current) return;
      setTerrain(asset);
      invalidate("DEM загружен. Подтвердите метры и систему высот MSL.");
    } catch (error) {
      if (version === terrainImportVersion.current && revision === planRevision.current) setStatus(`${errorMessage(error)} Предыдущий DEM сохранён, если был загружен.`);
    } finally {
      if (version === terrainImportVersion.current) setTerrainImporting(false);
    }
  }

  function handleScenarioChange(nextScenarioId: string): void {
    const nextScenario = scenarios.find((candidate) => candidate.id === nextScenarioId);
    if (!nextScenario) return;
    setScenarioId(nextScenario.id);
    setScenarioOverride(null);
    setSourceFiles([]);
    terrainImportVersion.current += 1;
    setTerrainImporting(false);
    setTerrain(null);
    setDemoCaseId(defaultDemoCaseForScenario(nextScenario.id)?.id ?? initialDemoCase.id);
    setTask(nextScenario.task);
    setSites(cloneSites(nextScenario));
    setImportedZones([]);
    setWorkAreaSource("scenario");
    setPendingSiteRole(null);
    setSettings((current) => ({ ...current, altitude: nextScenario.altitude,
      altitudeReference: "msl", flatGroundElevation: 0, terrainVerticalUncertainty: 0 }));
    const capable = drones.find((drone) => drone.tasks.includes(nextScenario.task));
    if (capable) setSelectedDroneId(capable.id);
    invalidate("Тестовый сценарий загружен");
  }

  function loadDemoCase(caseId: string): {
    scenario: typeof initialScenario;
    objective: Objective;
    drones: DroneProfile[];
    sites: FlightSiteState[];
    survey: SurveySettings;
    settings: PlanningSettings;
    selectedDroneId: string;
  } | null {
    const demoCase = demoCaseById(caseId);
    const nextScenario = scenarios.find((candidate) => candidate.id === demoCase?.scenarioId);
    if (!demoCase || !nextScenario) return null;
    const nextDrones = cloneFleet();
    const nextSites = cloneSites(nextScenario);
    const nextSurvey = { ...defaultSurvey };
    const nextSettings = planningSettings(nextScenario.altitude, demoCase.settings);
    const nextSelectedDroneId = nextDrones.find((drone) => drone.tasks.includes(nextScenario.task))?.id
      ?? nextDrones[0].id;
    setDemoCaseId(demoCase.id);
    setScenarioOverride(null);
    setSourceFiles([]);
    terrainImportVersion.current += 1;
    setTerrainImporting(false);
    setTerrain(null);
    setScenarioId(nextScenario.id);
    setTask(nextScenario.task);
    setObjective(demoCase.recommendedObjective);
    setDrones(nextDrones);
    setSites(nextSites);
    setImportedZones([]);
    setWorkAreaSource("scenario");
    setSurvey(nextSurvey);
    setSettings(nextSettings);
    setSelectedDroneId(nextSelectedDroneId);
    setPendingSiteRole(null);
    invalidate(`Демо-кейс «${demoCase.title}» загружен`);
    return {
      scenario: nextScenario,
      objective: demoCase.recommendedObjective,
      drones: nextDrones,
      sites: nextSites,
      survey: nextSurvey,
      settings: nextSettings,
      selectedDroneId: nextSelectedDroneId,
    };
  }

  function handleDemoCaseLoad(caseId: string): void {
    loadDemoCase(caseId);
  }

  function handleTaskChange(nextTask: MissionTask): void {
    setTask(nextTask);
    const capable = drones.find((drone) => drone.tasks.includes(nextTask));
    if (capable) setSelectedDroneId(capable.id);
    invalidate("Тип работ изменён — требуется новый расчёт");
  }

  function handleUpdateDrone(droneId: string, patch: Partial<DroneProfile>): void {
    setDrones((current) => current.map((drone) => drone.id === droneId ? { ...drone, ...patch } : drone));
    invalidate("Параметры парка изменены — требуется новый расчёт");
  }

  function handleAddDrone(modelId = "custom", count = 1): void {
    try {
      const added = fleetCatalog.create(modelId, count, drones, task);
      setDrones((current) => [...current, ...added]);
      setSelectedDroneId(added[0].id);
      invalidate(`Добавлено БВС: ${added.length}. Проверьте комплектацию и состояние каждого борта`);
    } catch (error) { setStatus(errorMessage(error)); }
  }

  function handleCloneDrone(): void {
    const source = drones.find((drone) => drone.id === selectedDroneId);
    if (!source) return;
    try {
      const added = fleetCatalog.clone(source, drones);
      setDrones((current) => [...current, added]);
      setSelectedDroneId(added.id);
      invalidate("Настройки скопированы. Уточните заряд, ресурс и площадки нового борта");
    } catch (error) { setStatus(errorMessage(error)); }
  }

  function handleRemoveDrone(): void {
    if (drones.length === 1) {
      setStatus("В парке должен остаться хотя бы один БВС");
      return;
    }
    const remaining = drones.filter((drone) => drone.id !== selectedDroneId);
    setDrones(remaining);
    setSelectedDroneId(remaining[0].id);
    setSites((current) => current.map((flightSite) => flightSite.assignedDroneId === selectedDroneId ? { ...flightSite, assignedDroneId: "" } : flightSite));
    invalidate("БВС удалён из парка");
  }

  function handleAddSite(coordinate: Coordinate): void {
    if (pendingSiteRole === null) return;
    const role = pendingSiteRole;
    const ordinal = sites.filter((flightSite) => flightSite.role === role).length + 1;
    const roleName = role === "takeoff" ? "Взлёт" : role === "landing" ? "Посадка" : "Резерв";
    setSites((current) => [...current, {
      site_id: `site-${role}-${Date.now()}`,
      name: `${roleName} ${ordinal}`,
      role,
      coordinate,
      assignedDroneId: selectedDroneId,
    }]);
    setPendingSiteRole(null);
    invalidate("Площадка добавлена и назначена выбранному БВС");
  }

  async function handleImport(file: File, zoneType: ZoneType): Promise<void> {
    const targetRevision = planRevision.current;
    setImporting(true);
    setStatus("Проверяем геоданные…");
    let content: string;
    try {
      content = await file.text();
    } catch (error: unknown) {
      setStatus(error instanceof Error ? error.message : "Не удалось прочитать файл");
      setImporting(false);
      return;
    }
    try {
      const result = await importGeodata(file.name, zoneType, content);
      if (targetRevision !== planRevision.current) return;
      setImportedZones((current) => mergeZones(current, result.zones));
      setSourceFiles((current) => [...current, { name: file.name, zone_type: zoneType, content }]);
      if (result.zones.some((zone) => zone.zone_type === "work_area")) setWorkAreaSource("imported");
      const invalidCount = result.zones.filter((zone) => zone.metadata?.geometry_issue).length;
      invalidate(`Импортировано зон: ${result.zones.length}${invalidCount ? `. Без площади: ${invalidCount}; проверьте список геоданных` : ""}`);
    } catch (error: unknown) {
      setStatus(errorMessage(error));
    } finally {
      setImporting(false);
    }
  }

  async function calculatePlan(
    request: ReturnType<typeof buildPlanRequest>,
    preferredDroneId: string,
  ): Promise<void> {
    planningController.current?.abort();
    const controller = new AbortController();
    planningController.current = controller;
    const version = ++planRevision.current;
    let received = false;
    setLastRequest(request);
    setArchivedPlan(false);
    setPlan(null);
    try {
      setCalculating(true);
      setStatus("Строим первый проверенный план… Поиск можно остановить.");
      const result = await createPlan(request, (candidate) => {
        if (version !== planRevision.current) return;
        setPlan(candidate);
        setSelectedDroneId((current) => candidate.drone_plans.some((p) => p.drone_id === current && p.mission_count > 0)
          ? current : candidate.drone_plans.find((p) => p.mission_count > 0)?.drone_id ?? current);
        if (!received) { setRevision((current) => current + 1); received = true; }
        setStatus(`Проверенный план: ${candidate.metrics.completion_minutes.toFixed(1)} мин. Продолжаем поиск улучшений…`);
      }, controller.signal);
      if (version !== planRevision.current) return;
      setPlan(result);
      if (!received) setRevision((current) => current + 1);
      const active = result.drone_plans.find((dronePlan) => dronePlan.mission_count > 0);
      if (active && !result.drone_plans.some((dronePlan) => dronePlan.drone_id === preferredDroneId && dronePlan.mission_count > 0)) {
        setSelectedDroneId(active.drone_id);
      }
      setStatus("План рассчитан сервером");
    } catch (error: unknown) {
      if (version === planRevision.current) {
        setStatus(errorMessage(error));
        setPlan((current) => current?.search ? { ...current, search: { ...current.search,
          status: controller.signal.aborted ? "cancelled" : "retained_after_error" } } : current);
      }
    } finally {
      if (version === planRevision.current) setCalculating(false);
      if (planningController.current === controller) planningController.current = null;
    }
  }

  async function handleAdvise(allowAltitudeChange = false): Promise<void> {
    try {
      const request = lastRequest ?? buildPlanRequest(objective, task, scenario, drones, sites, importedZones, survey, settings, workAreaSource, terrain);
      setLastRequest(request);
      await insights.advise(request, allowAltitudeChange);
    } catch (error) { setStatus(errorMessage(error)); }
  }

  function handleApplyAdvice(index: number): void {
    const variant = insights.advice?.variants[index];
    if (!variant || variant.status !== "verified" || !variant.plan) return;
    planRevision.current += 1;
    setLastRequest(variant.request);
    setPlan(variant.plan);
    setArchivedPlan(false);
    setObjective(variant.request.criterion === "completion_time" ? "time" : variant.request.criterion === "balanced" ? "balanced" : "flight");
    setSettings((current) => ({ ...current, altitude: variant.request.mission_altitude_m,
      deadline: variant.request.completion_deadline_minutes?.toString() ?? "",
      maximumActiveDrones: variant.request.maximum_active_drones?.toString() ?? "" }));
    setRadiusAnalysis(null);
    setRevision((current) => current + 1);
    setStatus(`Применён вариант: ${variant.label}`);
  }

  async function handleCalculate(): Promise<void> {
    radiusAnalysisRevision.current += 1;
    setRadiusAnalysis(null);
    setAnalyzingRadii(false);
    let request;
    try {
      request = buildPlanRequest(objective, task, scenario, drones, sites, importedZones, survey, settings, workAreaSource, terrain);
    } catch (error: unknown) {
      setStatus(errorMessage(error));
      return;
    }
    await calculatePlan(request, selectedDroneId);
  }

  async function handleDemoCaseRun(caseId: string): Promise<void> {
    const demoState = loadDemoCase(caseId);
    if (demoState === null) {
      setStatus("Демонстрационный кейс не найден");
      return;
    }
    setShowcaseOpen(false);
    let request;
    try {
      request = buildPlanRequest(
        demoState.objective,
        demoState.scenario.task,
        demoState.scenario,
        demoState.drones,
        demoState.sites,
        [],
        demoState.survey,
        demoState.settings,
      );
    } catch (error: unknown) {
      setStatus(errorMessage(error));
      return;
    }
    await calculatePlan(request, demoState.selectedDroneId);
  }

  async function handleAnalyzeRadii(areaId: string, minimumRadius: number, maximumRadius: number): Promise<void> {
    if (settings.altitudeReference === "agl") {
      setStatus("Сравнение радиусов пока рассчитано на плоскую поверхность MSL. Для DEM используйте основной расчёт с заданным радиусом.");
      return;
    }
    let request;
    try {
      request = buildPlanRequest(objective, task, scenario, drones, sites, importedZones, survey, settings, workAreaSource, terrain);
    } catch (error: unknown) {
      setStatus(errorMessage(error));
      return;
    }
    const area = request.coverage_areas.find((candidate) => candidate.area_id === areaId);
    if (!area) {
      setStatus("Выбранная рабочая зона больше недоступна.");
      return;
    }
    setAnalyzingRadii(true);
    setRadiusAnalysis(null);
    setStatus("Проверяем предположения о радиусе разворота…");
    const analysisRevision = ++radiusAnalysisRevision.current;
    try {
      const result = await analyzeTurnRadii({
        coverage: {
          area,
          mission_altitude_m: request.mission_altitude_m - (request.flat_ground_elevation_m ?? 0)
            - (request.terrain_vertical_uncertainty_m ?? 0),
          parameters: request.survey_parameters,
          exclusions: request.no_fly_zones,
        },
        flight_altitude_msl_m: request.mission_altitude_m,
        allowed_airspaces: request.allowed_airspaces,
        radii_m: radiusSamples(minimumRadius, maximumRadius),
      });
      if (analysisRevision === radiusAnalysisRevision.current) {
        setRadiusAnalysis(result);
        setStatus("Проверка радиусов завершена");
      }
    } catch (error: unknown) {
      if (analysisRevision === radiusAnalysisRevision.current) setStatus(errorMessage(error));
    } finally {
      if (analysisRevision === radiusAnalysisRevision.current) setAnalyzingRadii(false);
    }
  }

  async function handleExport(format: "geojson" | "kml" | "qgroundcontrol" | "mission-planner"): Promise<void> {
    if (plan === null) return;
    setExporting(true);
    setStatus("Формируем файл полётного задания…");
    try {
      await exportMission(plan, selectedDroneId, format);
      setStatus(`Миссия ${selectedDroneId} экспортирована`);
    } catch (error: unknown) {
      setStatus(errorMessage(error));
    } finally {
      setExporting(false);
    }
  }

  function scenarioSnapshot(): SavedScenario {
    return {
      version: 3,
      baseScenario: scenario,
      terrain,
      scenarioId,
      task,
      objective,
      drones,
      selectedDroneId,
      sites,
      importedZones,
      workAreaSource: importedZones.some((zone) => zone.zone_type === "work_area") ? "imported" : workAreaSource,
      survey,
      settings,
    };
  }

  function saveScenario(): void {
    const snapshot = scenarioSnapshot();
    try {
      window.localStorage.setItem("geoplan-scenario", JSON.stringify(snapshot));
      setStatus("Сценарий сохранён");
    } catch (error: unknown) {
      setStatus(error instanceof DOMException && error.name === "QuotaExceededError"
        ? "Недостаточно места в браузере для полного сценария. Предыдущее сохранение не изменено."
        : errorMessage(error));
    }
  }

  function forgetSavedScenario(): void {
    try {
      window.localStorage.removeItem("geoplan-scenario");
      setStatus("Локальное сохранение сценария удалено");
    } catch (error: unknown) {
      setStatus(errorMessage(error));
    }
  }

  function loadScenario(): void {
    const serialized = window.localStorage.getItem("geoplan-scenario");
    if (serialized === null) {
      setStatus("Сохранённый сценарий не найден");
      return;
    }
    let snapshot: SavedScenario;
    try {
      snapshot = parseSavedScenario(JSON.parse(serialized));
    } catch (error: unknown) {
      setStatus(error instanceof Error ? "Сохранённый сценарий повреждён" : "Не удалось восстановить сценарий");
      return;
    }
    applyScenarioSnapshot(snapshot);
  }

  function applyScenarioSnapshot(snapshot: SavedScenario): void {
    if ((!snapshot.baseScenario && !scenarios.some((candidate) => candidate.id === snapshot.scenarioId)) || snapshot.drones.length === 0) {
      setStatus("Сохранённый сценарий имеет неподдерживаемый формат");
      return;
    }
    terrainImportVersion.current += 1;
    setTerrainImporting(false);
    setTerrain(snapshot.terrain ?? null);
    setScenarioId(snapshot.scenarioId);
    setScenarioOverride(snapshot.baseScenario ?? null);
    setSourceFiles([]);
    setPendingSiteRole(null);
    setDemoCaseId(defaultDemoCaseForScenario(snapshot.scenarioId)?.id ?? initialDemoCase.id);
    setTask(snapshot.task);
    setObjective(snapshot.objective);
    const restoredDrones = snapshot.drones
      .map((drone) => snapshot.version === 3 ? drone : migrateLegacyDroneProfile(drone))
      .map(normalizeDroneProfile);
    setDrones(restoredDrones);
    setSelectedDroneId(restoredDrones.some((drone) => drone.id === snapshot.selectedDroneId) ? snapshot.selectedDroneId : restoredDrones[0].id);
    setSites(snapshot.sites);
    setImportedZones(snapshot.importedZones);
    setWorkAreaSource(snapshot.workAreaSource === "imported" || snapshot.importedZones.some((zone) => zone.zone_type === "work_area") ? "imported" : "scenario");
    setSurvey({ ...defaultSurvey, ...snapshot.survey });
    setSettings({
      ...planningSettings(snapshot.baseScenario?.altitude ?? 120),
      ...snapshot.settings,
      minimumSeparation: snapshot.settings.minimumSeparation ?? 50,
      ambientTemperature: snapshot.settings.ambientTemperature ?? 20,
      minimumVerticalSeparation: snapshot.settings.minimumVerticalSeparation ?? 30,
      verticalDeconflictionStep: snapshot.settings.verticalDeconflictionStep ?? 30,
      maximumVerticalDeconfliction: snapshot.settings.maximumVerticalDeconfliction ?? 0,
      positionUncertainty: snapshot.settings.positionUncertainty ?? 0,
      startTimeUncertainty: snapshot.settings.startTimeUncertainty ?? 0,
      speedUncertainty: snapshot.settings.speedUncertainty ?? 0,
      flightSiteOccupancy: snapshot.settings.flightSiteOccupancy ?? 0,
      turnaroundMinutes: snapshot.settings.turnaroundMinutes ?? 0,
      planningStart: snapshot.settings.planningStart ?? "",
      planningEnd: snapshot.settings.planningEnd ?? "",
      turnRadius: snapshot.settings.turnRadius ?? 0,
      fastHeuristic: snapshot.settings.fastHeuristic ?? false,
      productiveRouting: snapshot.settings.productiveRouting ?? true,
    });
    invalidate(snapshot.workAreaSource === undefined && !snapshot.importedZones.some((zone) => zone.zone_type === "work_area")
      ? "Старое сохранение не содержит импортированных рабочих областей. Используется выбранный демосценарий; при необходимости импортируйте исходный файл повторно."
      : "Сценарий восстановлен — требуется новый расчёт");
  }

  function captureProject(): ProjectDocument {
    return {
      schema_version: 1,
      editor_state: scenarioSnapshot(),
      source_files: sourceFiles,
      request: lastRequest,
      plan,
      survey_quality: insights.quality,
      turn_radius_analysis: radiusAnalysis,
    };
  }

  function restoreProject(document: ProjectDocument): void {
    const snapshot = parseSavedScenario(document.editor_state);
    if (!snapshot.baseScenario && !scenarios.some((candidate) => candidate.id === snapshot.scenarioId)) throw new Error("Не найдена исходная геометрия сценария");
    applyScenarioSnapshot(snapshot);
    const restoredPlan = document.plan ? { ...document.plan, export_authorization: null } : null;
    setSourceFiles(document.source_files);
    setLastRequest(document.request);
    setPlan(restoredPlan);
    setArchivedPlan(restoredPlan !== null);
    setRadiusAnalysis(document.turn_radius_analysis);
    insights.restore(document.request, restoredPlan, document.survey_quality);
    setRevision((current) => current + 1);
    setStatus("Проект восстановлен. Сохранённый план доступен для анализа; экспорт в автопилот требует нового расчёта.");
  }

  return {
    terrain, terrainImporting, handleImportTerrain, handleTerrainChange,
    insights,
    handleAdvise,
    handleApplyAdvice,
    canInspectQuality: lastRequest !== null && plan !== null,
    scenarioId,
    demoCaseId,
    showcaseOpen,
    setShowcaseOpen,
    task,
    objective,
    setObjective,
    drones,
    selectedDroneId,
    setSelectedDroneId,
    sites,
    setSites,
    importedZones,
    setImportedZones,
    workAreaSource,
    survey,
    setSurvey,
    settings,
    setSettings,
    plan,
    radiusAnalysis,
    analyzingRadii,
    revision,
    status,
    setStatus,
    pendingSiteRole,
    setPendingSiteRole,
    calculating,
    importing,
    exporting,
    scenario,
    mapZones,
    fitKey,
    invalidate,
    handleScenarioChange,
    handleDemoCaseLoad,
    handleTaskChange,
    handleUpdateDrone,
    handleAddDrone,
    handleCloneDrone,
    handleRemoveDrone,
    handleAddSite,
    handleImport,
    handleCalculate,
    handleCancelPlanning: () => planningController.current?.abort(),
    handleDemoCaseRun,
    handleAnalyzeRadii,
    handleExport,
    saveScenario,
    loadScenario,
    forgetSavedScenario,
    captureProject,
    restoreProject,
    archivedPlan,
  };
}
