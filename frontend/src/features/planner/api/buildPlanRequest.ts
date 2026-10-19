import type { DroneProfile, FlightSiteState, Objective, PlanningSettings, Scenario, SurveySettings, WorkAreaSource } from "@/features/planner/model/types";
import type { ImportedZone, MissionTask, PlanRequest } from "@/shared/api/types";
import { ImportedObstacleMapper } from "@/features/geodata/model/obstacles";

const obstacleMapper = new ImportedObstacleMapper();

export function activeWorkAreas(importedZones: ImportedZone[], scenario: Scenario, source?: WorkAreaSource): ImportedZone[] {
  const imported = importedZones.filter(
    (zone) => zone.zone_type === "work_area" && zone.geometry_type === "polygon",
  );
  return source === "imported" || importedZones.some((zone) => zone.zone_type === "work_area")
    ? imported.filter((zone) => zone.enabled !== false) : scenario.workAreas;
}

export function activeNoFlyZones(importedZones: ImportedZone[], scenario: Scenario, source?: WorkAreaSource): ImportedZone[] {
  const imported = importedZones.filter(
    (zone) => zone.zone_type === "no_fly_zone" && zone.geometry_type === "polygon",
  );
  const hasImportedWork = source === "imported" || importedZones.some((zone) => zone.zone_type === "work_area");
  const hasImportedRestrictions = importedZones.some((zone) => zone.zone_type === "no_fly_zone");
  return hasImportedRestrictions || hasImportedWork ? imported : scenario.noFlyZones;
}

export function objectiveToCriterion(objective: Objective): PlanRequest["criterion"] {
  if (objective === "time") {
    return "completion_time";
  }
  if (objective === "flight") {
    return "total_flight_time";
  }
  return "balanced";
}

export function buildPlanRequest(
  objective: Objective,
  task: MissionTask,
  scenario: Scenario,
  drones: DroneProfile[],
  sites: FlightSiteState[],
  importedZones: ImportedZone[],
  survey: SurveySettings,
  settings: PlanningSettings,
  source?: WorkAreaSource,
): PlanRequest {
  const ground = settings.flatGroundElevation ?? 0;
  const groundUncertainty = settings.terrainVerticalUncertainty ?? 0;
  if (!Number.isFinite(ground) || ground < -500 || ground > 10000
    || !Number.isFinite(groundUncertainty) || groundUncertainty < 0 || groundUncertainty > 500) {
    throw new Error("Задайте отметку поверхности от −500 до 10 000 м и погрешность от 0 до 500 м.");
  }
  if (!Number.isFinite(settings.altitude) || settings.altitude <= ground + groundUncertainty) {
    throw new Error("Высота полёта MSL должна превышать отметку поверхности с учётом погрешности.");
  }
  const unsupported = importedZones.find((zone) => zone.zone_type !== "obstacle"
    && !(zone.zone_type === "work_area" && zone.enabled === false) && zone.geometry_type !== "polygon");
  if (unsupported) throw new Error(`«${unsupported.name}»: для рабочей зоны или ограничения нужен полигон. Линия или точка не заменяют область.`);
  const airspaceWithHoles = importedZones.find((zone) => zone.zone_type === "allowed_airspace"
    && zone.interior_rings.length > 0);
  if (airspaceWithHoles) throw new Error(`«${airspaceWithHoles.name}»: разрешённое пространство с внутренними вырезами пока не поддерживается. Вырезы нельзя игнорировать.`);
  const enabledDrones = drones.filter((drone) => drone.enabled);
  const budget = settings.planningTimeLimitSeconds ?? 270;
  if (!Number.isFinite(budget) || budget < 1 || budget > 270) throw new Error("Задайте бюджет расчёта от 1 до 270 секунд.");
  if (enabledDrones.length === 0) {
    throw new Error("Включите хотя бы один БВС.");
  }
  for (const drone of enabledDrones) {
    const forward = drone.fixedWingPhaseProfile;
    if (forward) {
      if (drone.airframeType !== "fixed_wing" || !(Number.isFinite(drone.minimumTurnRadius) && (drone.minimumTurnRadius ?? 0) > 0)) {
        throw new Error(`${drone.name}: самолётный профиль требует тип «Самолёт» и подтверждённый радиус разворота.`);
      }
      if (![forward.climb_rate_mps, forward.descent_rate_mps].every((v) => Number.isFinite(v) && v > 0 && v <= 100)
        || ![forward.climb_energy_multiplier, forward.descent_energy_multiplier].every((v) => Number.isFinite(v) && v >= 1 && v <= 10)) {
        throw new Error(`${drone.name}: проверьте пределы набора и снижения и коэффициенты энергии самолётного профиля.`);
      }
      if ((drone.maximumClimbRate != null && forward.climb_rate_mps > drone.maximumClimbRate)
        || (drone.maximumDescentRate != null && forward.descent_rate_mps > drone.maximumDescentRate)) {
        throw new Error(`${drone.name}: плановый предел вертикальной скорости превышает возможности БВС.`);
      }
    }
    const phase = drone.takeoffLandingProfile;
    if (phase) {
      if (["fixed_wing", "vtol_fixed_rotor"].includes(drone.airframeType)) {
        throw new Error(`${drone.name}: вертикальная модель взлёта и посадки доступна только мультикоптерам.`);
      }
      if (![phase.climb_rate_mps, phase.descent_rate_mps].every((v) => Number.isFinite(v) && v > 0 && v <= 100)
        || ![phase.climb_energy_multiplier, phase.descent_energy_multiplier].every((v) => Number.isFinite(v) && v >= 1 && v <= 10)) {
        throw new Error(`${drone.name}: проверьте плановые скорости и коэффициенты энергии взлёта и посадки.`);
      }
      if ((drone.maximumClimbRate != null && phase.climb_rate_mps > drone.maximumClimbRate)
        || (drone.maximumDescentRate != null && phase.descent_rate_mps > drone.maximumDescentRate)) {
        throw new Error(`${drone.name}: плановая вертикальная скорость превышает предел БВС.`);
      }
    }
    const sets = drone.availableBatterySets;
    if (sets != null && (!Number.isInteger(sets) || sets < 0 || sets > 10000)) {
      throw new Error(`${drone.name}: число готовых комплектов АКБ должно быть целым от 0 до 10 000.`);
    }
  }
  const departureSites = sites.filter((flightSite) => flightSite.role === "takeoff");
  const landingSites = sites.filter((flightSite) => flightSite.role === "landing");
  const alternateSites = sites.filter((flightSite) => flightSite.role === "alternate");
  if (alternateSites.length > 0 && enabledDrones.some((drone) => drone.fixedWingPhaseProfile != null)) {
    throw new Error("Самолётный профиль пока не моделирует заход на резервную площадку. Сохраните резерв и отключите этот профиль либо создайте отдельный сценарий для GIS-проверки без резервных площадок.");
  }
  if (departureSites.length === 0) {
    throw new Error("Добавьте хотя бы одну точку взлёта.");
  }
  if (landingSites.length === 0) {
    throw new Error("Добавьте хотя бы одну основную площадку посадки.");
  }
  const deadline = optionalPositiveNumber(settings.deadline);
  if (objective === "balanced" && deadline === null) {
    throw new Error("Для сбалансированного плана задайте срок выполнения.");
  }
  const maximumActiveDrones = optionalPositiveInteger(settings.maximumActiveDrones);
  if (maximumActiveDrones !== null && maximumActiveDrones > enabledDrones.length) {
    throw new Error("Лимит активных БВС превышает размер включённого парка.");
  }
  if (["rgb", "multispectral", "thermal"].includes(task) && survey.sideOverlap < 50) {
    throw new Error("Для аэрофотосъёмки задайте боковое перекрытие не менее 50%.");
  }
  if (!Number.isFinite(settings.turnRadius) || settings.turnRadius < 0 || settings.turnRadius > 10000) {
    throw new Error("Радиус разворота должен быть от 0 до 10 000 м.");
  }
  if (settings.turnRadius > 0 && scenario.segments.length > 0) {
    throw new Error("Радиус разворота поддерживается для генерируемых галсов.");
  }
  const workAreas = activeWorkAreas(importedZones, scenario, source);
  const hasImportedWork = source === "imported" || importedZones.some((zone) => zone.zone_type === "work_area");
  if (hasImportedWork && workAreas.length === 0) throw new Error("Выберите хотя бы одну рабочую область из импортированных данных.");
  const noFlyZones = activeNoFlyZones(importedZones, scenario, source);
  const allowedAirspaces = importedZones.filter(
    (zone) => zone.zone_type === "allowed_airspace" && zone.geometry_type === "polygon",
  );
  const ambiguousTemporaryZones = [...allowedAirspaces, ...noFlyZones].filter(
    (zone) => zone.restriction_type === "temporary" && zone.validity?.mode !== "time_window",
  );
  if (ambiguousTemporaryZones.length > 0) {
    throw new Error("У временной зоны нет однозначного окна действия. Уточните исходные геоданные.");
  }
  const planningStartTime = optionalIsoDateTime(settings.planningStart);
  const planningEndTime = optionalIsoDateTime(settings.planningEnd);
  const hasTimedZone = [...allowedAirspaces, ...noFlyZones].some(
    (zone) => zone.validity?.mode === "time_window",
  );
  if (hasTimedZone && planningStartTime === null) {
    throw new Error("Для временных 4D-ограничений задайте начало плана.");
  }
  if (planningEndTime !== null && planningStartTime === null) {
    throw new Error("Для окончания рабочего окна задайте начало плана.");
  }
  if (
    planningStartTime !== null
    && planningEndTime !== null
    && new Date(planningEndTime).getTime() <= new Date(planningStartTime).getTime()
  ) {
    throw new Error("Окончание рабочего окна должно быть позже начала плана.");
  }
  return {
    criterion: objectiveToCriterion(objective),
    planning_time_limit_seconds: settings.planningTimeLimitSeconds ?? 270,
    drones: enabledDrones.map((drone) => ({
      drone_id: drone.id,
      name: drone.name,
      cruise_speed_kmh: drone.speed,
      maximum_flight_minutes: drone.flightMinutes,
      initial_charge_percent: drone.battery,
      available_battery_sets: drone.availableBatterySets ?? null,
      battery_health_percent: drone.batteryHealth,
      energy_consumption_multiplier: drone.energyConsumptionMultiplier,
      battery_reserve_percent: drone.reserve,
      maximum_wind_mps: drone.maxWind,
      supported_tasks: drone.tasks,
      autopilot_profile: {
        firmware: drone.autopilotFirmware,
        airframe: drone.airframeType,
        hover_speed_mps: drone.hoverSpeed,
      },
      maintenance_interval_flights: drone.maintenanceFlights,
      maintenance_interval_hours: drone.maintenanceHours,
      completed_flights_since_maintenance: drone.completedFlights,
      flight_hours_since_maintenance: drone.accumulatedHours,
      maximum_climb_rate_mps: drone.maximumClimbRate,
      takeoff_landing_profile: drone.takeoffLandingProfile ?? null,
      fixed_wing_phase_profile: drone.fixedWingPhaseProfile ?? null,
      maximum_descent_rate_mps: drone.maximumDescentRate,
      minimum_mission_altitude_m: null,
      maximum_mission_altitude_m: null,
      minimum_mission_altitude_agl_m: drone.minimumMissionAltitudeAgl ?? null,
      maximum_mission_altitude_agl_m: drone.maximumMissionAltitudeAgl ?? null,
      minimum_mission_altitude_msl_m: drone.minimumMissionAltitudeMsl ?? null,
      maximum_mission_altitude_msl_m: drone.maximumMissionAltitudeMsl ?? null,
      minimum_cruise_speed_kmh: drone.minimumCruiseSpeed ?? null,
      maximum_cruise_speed_kmh: drone.maximumCruiseSpeed ?? null,
      maximum_route_distance_km: drone.maximumRouteDistance ?? null,
      minimum_operating_temperature_c: drone.minimumOperatingTemperature ?? null,
      maximum_operating_temperature_c: drone.maximumOperatingTemperature ?? null,
      maximum_control_range_km: drone.maximumControlRange ?? null,
      minimum_turnaround_minutes: drone.minimumTurnaroundMinutes ?? 0,
      payloads: drone.payloads ?? [],
      installed_sensor: drone.installedSensor ?? null,
      minimum_turn_radius_m: drone.minimumTurnRadius ?? 0,
    })),
    segments: hasImportedWork ? [] : scenario.segments,
    coverage_areas: workAreas.map((zone) => ({
      area_id: zone.zone_id,
      name: zone.name,
      coordinates: zone.coordinates,
      interior_rings: zone.interior_rings,
    })),
    survey_parameters: {
      direction_mode: survey.directionMode,
      flight_direction_degrees: survey.direction,
      side_overlap_percent: survey.sideOverlap,
      forward_overlap_percent: survey.forwardOverlap,
      maximum_gsd_cm: survey.maximumGsd,
      strip_width_m: survey.stripWidth,
      maximum_segment_distance_km: survey.maximumSegmentDistance,
      sensor: {
        sensor_id: "configured-sensor",
        name: "Настраиваемая камера",
        sensor_width_mm: survey.sensorWidth,
        sensor_height_mm: survey.sensorHeight,
        focal_length_mm: survey.focalLength,
        image_width_px: survey.imageWidth,
        image_height_px: survey.imageHeight,
        minimum_trigger_interval_s: survey.triggerInterval,
      },
    },
    no_fly_zones: noFlyZones.map((zone) => ({
      zone_id: zone.zone_id,
      coordinates: zone.coordinates,
      interior_rings: zone.interior_rings,
      safety_margin_m: survey.restrictionMargin,
      minimum_altitude_m: zone.minimum_altitude_m ?? null,
      maximum_altitude_m: zone.maximum_altitude_m ?? null,
      altitude_reference: zoneAltitudeReference(zone),
      validity: zone.validity ?? null,
    })),
    obstacles: importedZones
      .filter((zone) => zone.zone_type === "obstacle")
      .map((zone) => obstacleMapper.toRequest(zone)),
    mission_altitude_m: settings.altitude,
    altitude_reference: "msl",
    flat_ground_elevation_m: ground,
    terrain_vertical_uncertainty_m: groundUncertainty,
    task,
    departure_sites: departureSites.map(apiSite),
    landing_sites: landingSites.map(apiSite),
    alternate_landing_sites: alternateSites.map(apiSite),
    wind: {
      speed_mps: settings.windSpeed,
      direction_degrees: settings.windDirection,
    },
    ambient_temperature_c: settings.ambientTemperature,
    allowed_airspaces: allowedAirspaces
      .map((zone) => ({
        airspace_id: zone.zone_id,
        coordinates: zone.coordinates,
        minimum_altitude_m: zone.minimum_altitude_m ?? settings.airspaceMinimumAltitude,
        maximum_altitude_m: zone.maximum_altitude_m ?? settings.airspaceMaximumAltitude,
        altitude_reference: zoneAltitudeReference(zone),
        validity: zone.validity ?? null,
      })),
    maximum_active_drones: maximumActiveDrones,
    completion_deadline_minutes: deadline,
    minimum_separation_m: settings.minimumSeparation,
    minimum_vertical_separation_m: settings.minimumVerticalSeparation,
    vertical_deconfliction_step_m: settings.verticalDeconflictionStep,
    maximum_vertical_deconfliction_m: settings.maximumVerticalDeconfliction,
    position_uncertainty_m: settings.positionUncertainty,
    start_time_uncertainty_seconds: settings.startTimeUncertainty,
    speed_uncertainty_percent: settings.speedUncertainty,
    flight_site_occupancy_seconds: settings.flightSiteOccupancy,
    turnaround_minutes: settings.turnaroundMinutes,
    planning_start_time: planningStartTime,
    planning_end_time: planningEndTime,
    minimum_turn_radius_m: settings.turnRadius,
    optimization_mode: settings.fastHeuristic ? "fast_heuristic" : "optimized",
    assignment_strategy: settings.fastHeuristic ? "hybrid" : "automatic",
    productive_routing: (settings.productiveRouting ?? true) && !settings.fastHeuristic,
  };
}

function zoneAltitudeReference(zone: ImportedZone): "msl" | "agl" {
  if (zone.altitude_reference) return zone.altitude_reference;
  if (zone.altitude_mode === "absolute") return "msl";
  if (zone.altitude_mode === "relativeToGround") return "agl";
  if (zone.minimum_altitude_m != null || zone.maximum_altitude_m != null) {
    throw new Error(`Для высотного диапазона «${zone.name}» укажите AGL или MSL.`);
  }
  return "msl";
}

function apiSite(site: FlightSiteState): PlanRequest["departure_sites"][number] {
  return {
    site_id: site.site_id,
    name: site.name,
    coordinate: site.coordinate,
    assigned_drone_ids: site.assignedDroneId ? [site.assignedDroneId] : [],
  };
}

function optionalPositiveNumber(value: string): number | null {
  if (value.trim() === "") {
    return null;
  }
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error("Срок должен быть положительным числом.");
  }
  return parsed;
}

function optionalPositiveInteger(value: string): number | null {
  if (value.trim() === "") {
    return null;
  }
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error("Лимит БВС должен быть положительным целым числом.");
  }
  return parsed;
}

function optionalIsoDateTime(value: string): string | null {
  if (value.trim() === "") {
    return null;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    throw new Error("Задайте корректные дату и время начала плана.");
  }
  return parsed.toISOString();
}
