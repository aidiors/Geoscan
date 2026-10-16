import { useMemo, useRef, useState } from "react";
import { analyzeTurnRadii, createPlan } from "@/features/planner/api/client";
import { exportMission } from "@/features/mission-export/api/exportMission";
import { importGeodata } from "@/features/geodata/api/importGeodata";
import { defaultDemoCaseForScenario, demoCaseById, demoCases } from "@/features/demos/data/demoCases";
import { cloneFleet, cloneSites, scenarios } from "@/features/demos/data/scenarios";
import { activeNoFlyZones, activeWorkAreas, buildPlanRequest } from "@/features/planner/api/buildPlanRequest";
import { radiusSamples } from "@/features/planner/model/radii";
import type { Coordinate } from "@/shared/geo/types";
import type { DroneProfile, FlightSiteState, Objective, PlanningSettings, SiteRole, SurveySettings } from "@/features/planner/model/types";
import type { ImportedZone, MissionTask, PlanResponse, TurnRadiusAnalysisResult, ZoneType } from "@/shared/api/types";
import { defaultSurvey, planningSettings } from "@/features/planner/model/defaults";
import type { SavedScenario } from "@/features/planner/storage/scenario";
import { migrateLegacyDroneProfile, normalizeDroneProfile } from "@/features/planner/storage/scenario";
import { errorMessage } from "@/shared/api/errorMessage";

export function usePlanner() {
  const initialScenario = scenarios[0];
  const initialDemoCase = demoCases[0];
  const [scenarioId, setScenarioId] = useState(initialScenario.id);
  const [demoCaseId, setDemoCaseId] = useState(initialDemoCase.id);
  const [showcaseOpen, setShowcaseOpen] = useState(false);
  const [task, setTask] = useState<MissionTask>(initialScenario.task);
  const [objective, setObjective] = useState<Objective>("time");
  const [drones, setDrones] = useState<DroneProfile[]>(cloneFleet);
  const [selectedDroneId, setSelectedDroneId] = useState("201");
  const [sites, setSites] = useState<FlightSiteState[]>(() => cloneSites(initialScenario));
  const [importedZones, setImportedZones] = useState<ImportedZone[]>([]);
  const [survey, setSurvey] = useState<SurveySettings>(defaultSurvey);
  const [settings, setSettings] = useState<PlanningSettings>(() => planningSettings(initialScenario.altitude));
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  const [radiusAnalysis, setRadiusAnalysis] = useState<TurnRadiusAnalysisResult | null>(null);
  const [analyzingRadii, setAnalyzingRadii] = useState(false);
  const radiusAnalysisRevision = useRef(0);
  const [revision, setRevision] = useState(0);
  const [status, setStatus] = useState("Сценарий готов к расчёту");
  const [pendingSiteRole, setPendingSiteRole] = useState<SiteRole | null>(null);
  const [calculating, setCalculating] = useState(false);
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);

  const scenario = scenarios.find((candidate) => candidate.id === scenarioId) ?? initialScenario;
  const mapZones = useMemo(() => {
    const supplemental = importedZones.filter((zone) => zone.zone_type !== "work_area" && zone.zone_type !== "no_fly_zone");
    return [...activeWorkAreas(importedZones, scenario), ...activeNoFlyZones(importedZones, scenario), ...supplemental];
  }, [importedZones, scenario]);
  const fitKey = `${scenarioId}:${importedZones.map((zone) => zone.zone_id).join(",")}:${revision}`;


  function invalidate(nextStatus: string): void {
    radiusAnalysisRevision.current += 1;
    setPlan(null);
    setRadiusAnalysis(null);
    setAnalyzingRadii(false);
    setStatus(nextStatus);
  }

  function handleScenarioChange(nextScenarioId: string): void {
    const nextScenario = scenarios.find((candidate) => candidate.id === nextScenarioId);
    if (!nextScenario) return;
    setScenarioId(nextScenario.id);
    setDemoCaseId(defaultDemoCaseForScenario(nextScenario.id)?.id ?? initialDemoCase.id);
    setTask(nextScenario.task);
    setSites(cloneSites(nextScenario));
    setImportedZones([]);
    setPendingSiteRole(null);
    setSettings((current) => ({ ...current, altitude: nextScenario.altitude }));
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
    setScenarioId(nextScenario.id);
    setTask(nextScenario.task);
    setObjective(demoCase.recommendedObjective);
    setDrones(nextDrones);
    setSites(nextSites);
    setImportedZones([]);
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

  function handleAddDrone(): void {
    const ordinal = drones.length + 1;
    const id = `custom-${Date.now()}`;
    setDrones((current) => [...current, {
      id,
      name: `БВС ${ordinal}`,
      color: ["#0891b2", "#ea580c", "#4f46e5"][ordinal % 3],
      battery: 100,
      batteryHealth: 100,
      energyConsumptionMultiplier: 1,
      speed: 50,
      flightMinutes: 60,
      maxWind: 10,
      reserve: 20,
      enabled: true,
      tasks: [task],
      autopilotFirmware: "generic_mavlink",
      airframeType: "generic_multirotor",
      hoverSpeed: 5,
      maintenanceFlights: null,
      maintenanceHours: null,
      completedFlights: 0,
      accumulatedHours: 0,
      maximumClimbRate: null,
      maximumDescentRate: null,
      minimumMissionAltitude: null,
      maximumMissionAltitude: null,
      minimumMissionAltitudeAgl: null,
      maximumMissionAltitudeAgl: null,
      minimumMissionAltitudeMsl: null,
      maximumMissionAltitudeMsl: null,
      minimumCruiseSpeed: null,
      maximumCruiseSpeed: null,
      maximumRouteDistance: null,
      minimumOperatingTemperature: null,
      maximumOperatingTemperature: null,
      maximumControlRange: null,
      minimumTurnaroundMinutes: 0,
      payloads: [{ payload_id: `custom-${id}`, name: "Пользовательская нагрузка", supported_tasks: [task] }],
    }]);
    setSelectedDroneId(id);
    invalidate("Новый БВС добавлен в парк");
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
      setImportedZones((current) => [...current, ...result.zones]);
      invalidate(`Импортировано зон: ${result.zones.length}`);
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
    try {
      setCalculating(true);
      setStatus("Выполняется оптимизация маршрутов…");
      const result = await createPlan(request);
      setPlan(result);
      setRevision((current) => current + 1);
      const active = result.drone_plans.find((dronePlan) => dronePlan.mission_count > 0);
      if (active && !result.drone_plans.some((dronePlan) => dronePlan.drone_id === preferredDroneId && dronePlan.mission_count > 0)) {
        setSelectedDroneId(active.drone_id);
      }
      setStatus("План рассчитан сервером");
    } catch (error: unknown) {
      setStatus(errorMessage(error));
    } finally {
      setCalculating(false);
    }
  }

  async function handleCalculate(): Promise<void> {
    radiusAnalysisRevision.current += 1;
    setRadiusAnalysis(null);
    setAnalyzingRadii(false);
    let request;
    try {
      request = buildPlanRequest(objective, task, scenario, drones, sites, importedZones, survey, settings);
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
    let request;
    try {
      request = buildPlanRequest(objective, task, scenario, drones, sites, importedZones, survey, settings);
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
          mission_altitude_m: request.mission_altitude_m,
          parameters: request.survey_parameters,
          exclusions: request.no_fly_zones,
        },
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

  function saveScenario(): void {
    const snapshot: SavedScenario = {
      version: 3,
      scenarioId,
      task,
      objective,
      drones,
      selectedDroneId,
      sites,
      importedZones: importedZones.length <= 500 ? importedZones : [],
      survey,
      settings,
    };
    try {
      window.localStorage.setItem("geoplan-scenario", JSON.stringify(snapshot));
      setStatus(importedZones.length <= 500 ? "Сценарий сохранён" : "Сценарий сохранён без большого набора геоданных");
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
      snapshot = JSON.parse(serialized) as SavedScenario;
    } catch (error: unknown) {
      setStatus(error instanceof Error ? "Сохранённый сценарий повреждён" : "Не удалось восстановить сценарий");
      return;
    }
    if (!scenarios.some((candidate) => candidate.id === snapshot.scenarioId) || snapshot.drones.length === 0) {
      setStatus("Сохранённый сценарий имеет неподдерживаемый формат");
      return;
    }
    setScenarioId(snapshot.scenarioId);
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
    setSurvey({ ...defaultSurvey, ...snapshot.survey });
    setSettings({
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
    });
    invalidate("Сценарий восстановлен — требуется новый расчёт");
  }

  return {
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
    handleRemoveDrone,
    handleAddSite,
    handleImport,
    handleCalculate,
    handleDemoCaseRun,
    handleAnalyzeRadii,
    handleExport,
    saveScenario,
    loadScenario,
  };
}
