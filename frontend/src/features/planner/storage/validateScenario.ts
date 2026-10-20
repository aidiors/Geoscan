import { validateTerrainAsset } from "@/features/terrain/model/validateTerrainAsset";
import type { SavedScenario } from "./scenario";
import { defaultSurvey, planningSettings } from "@/features/planner/model/defaults";
import { cloneFleet } from "@/features/planner/model/fleetCatalog";

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Повреждена структура настроек проекта");
  return value as Record<string, unknown>;
}
function text(value: unknown): void { if (typeof value !== "string") throw new Error("В проекте повреждено текстовое поле"); }
function number(value: unknown): void { if (typeof value !== "number" || !Number.isFinite(value)) throw new Error("В проекте повреждено числовое поле"); }
function array(value: unknown): unknown[] { if (!Array.isArray(value)) throw new Error("В проекте повреждён список объектов"); return value; }
function coordinate(value: unknown): void {
  const point = record(value);
  number(point.latitude); number(point.longitude);
  if (Math.abs(point.latitude as number) > 90 || Math.abs(point.longitude as number) > 180) throw new Error("Некорректные координаты в проекте");
}
function sites(value: unknown): void {
  for (const item of array(value)) {
    const site = record(item); text(site.site_id); text(site.name); text(site.assignedDroneId); coordinate(site.coordinate);
    if (site.assignedDroneIds !== undefined) array(site.assignedDroneIds).forEach(text);
    if (!["takeoff", "landing", "alternate"].includes(String(site.role))) throw new Error("Неизвестная роль площадки");
  }
}
function zones(value: unknown): void {
  for (const item of array(value)) {
    const zone = record(item); text(zone.zone_id); text(zone.name);
    if (!["work_area", "no_fly_zone", "allowed_airspace", "obstacle"].includes(String(zone.zone_type))) throw new Error("Неизвестный тип зоны проекта");
    array(zone.coordinates).forEach(coordinate);
    if (zone.interior_rings !== undefined) array(zone.interior_rings).forEach((ring) => array(ring).forEach(coordinate));
    for (const key of ["visible", "locked"]) if (zone[key] !== undefined && typeof zone[key] !== "boolean") throw new Error("Повреждены настройки слоя");
    if (zone.enabled !== undefined && typeof zone.enabled !== "boolean") throw new Error("Повреждён выбор рабочих областей");
  }
}

/** Validate before touching React state; API types alone cannot validate imported JSON. */
export function parseSavedScenario(value: unknown): SavedScenario {
  const data = record(value);
  if (data.version !== undefined && data.version !== 2 && data.version !== 3) throw new Error("Версия настроек проекта не поддерживается");
  text(data.scenarioId); text(data.selectedDroneId);
  if (data.workAreaSource !== undefined && data.workAreaSource !== "imported" && data.workAreaSource !== "scenario") throw new Error("Неизвестный источник рабочей геометрии");
  if (!["rgb", "multispectral", "thermal", "lidar", "geophysical", "video"].includes(String(data.task))) throw new Error("Неизвестный тип работ проекта");
  if (!["time", "flight", "balanced"].includes(String(data.objective))) throw new Error("Неизвестный критерий проекта");
  const drones = array(data.drones);
  if (!drones.length || drones.length > 100) throw new Error("В проекте должно быть от 1 до 100 БВС");
  const ids = new Set();
  const droneTemplate = cloneFleet()[0];
  for (const item of drones) {
    const drone = record(item);
    for (const key of ["id", "name", "color"]) text(drone[key]);
    if (ids.has(drone.id)) throw new Error("В проекте повторяются идентификаторы БВС");
    ids.add(drone.id);
    for (const key of ["battery", "reserve", "speed", "flightMinutes", "maxWind"]) number(drone[key]);
    for (const [key, template] of Object.entries(droneTemplate)) {
      if (drone[key] == null || ["installedSensor", "takeoffLandingProfile", "fixedWingPhaseProfile"].includes(key)) continue;
      if (template === null || typeof template === "number") number(drone[key]);
      else if (typeof template === "string") text(drone[key]);
    }
    if (typeof drone.enabled !== "boolean") throw new Error("Повреждено состояние БВС");
    array(drone.tasks).forEach(text);
    if (drone.autopilotFirmware !== undefined && !["generic_mavlink", "ardupilot", "px4"].includes(String(drone.autopilotFirmware))) throw new Error("Неизвестная прошивка в проекте");
    if (drone.airframeType !== undefined && !["fixed_wing", "quadrotor", "hexarotor", "octorotor", "vtol_fixed_rotor", "generic_multirotor"].includes(String(drone.airframeType))) throw new Error("Неизвестный тип БВС в проекте");
    for (const key of ["takeoffLandingProfile", "fixedWingPhaseProfile", "installedSensor"]) {
      if (drone[key] != null) {
        const profile = record(drone[key]);
        const required = key === "installedSensor"
          ? ["sensor_width_mm", "sensor_height_mm", "focal_length_mm", "image_width_px", "image_height_px", "minimum_trigger_interval_s"]
          : ["climb_rate_mps", "descent_rate_mps", "climb_energy_multiplier", "descent_energy_multiplier"];
        for (const field of required) number(profile[field]);
        if (key === "installedSensor") { text(profile.sensor_id); text(profile.name); }
      }
    }
    if (drone.payloads !== undefined) for (const entry of array(drone.payloads)) {
      const payload = record(entry); text(payload.payload_id); text(payload.name); array(payload.supported_tasks).forEach(text);
    }
  }
  sites(data.sites);
  zones(data.importedZones);
  if (data.terrain != null) validateTerrainAsset(data.terrain);
  const survey = record(data.survey), settings = record(data.settings);
  if (settings.altitudeReference !== undefined && !["msl", "agl"].includes(String(settings.altitudeReference))) throw new Error("Неизвестная система высот проекта");
  if (settings.terrainSamplingInterval !== undefined) {
    number(settings.terrainSamplingInterval);
    if ((settings.terrainSamplingInterval as number) < 5 || (settings.terrainSamplingInterval as number) > 1000) throw new Error("Неверный шаг профиля DEM");
  }
  for (const [key, template] of Object.entries(defaultSurvey)) {
    if (typeof template === "number") number(survey[key]); else text(survey[key]);
  }
  for (const [key, template] of Object.entries(planningSettings(120))) {
    if (settings[key] === undefined) continue; // Legacy fields receive explicit defaults on restore.
    if (typeof template === "number") number(settings[key]);
    else if (typeof settings[key] !== typeof template) throw new Error("Повреждены ограничения проекта");
  }
  if (data.baseScenario !== undefined) {
    const scenario = record(data.baseScenario);
    for (const key of ["id", "title", "description", "task"]) text(scenario[key]);
    number(scenario.altitude); zones(scenario.workAreas); zones(scenario.noFlyZones); sites(scenario.sites);
    for (const item of array(scenario.segments)) {
      const segment = record(item); text(segment.segment_id); number(segment.distance_km); array(segment.route).forEach(coordinate);
    }
    if (scenario.id !== data.scenarioId) throw new Error("Не совпадает базовая геометрия сценария");
  }
  return structuredClone(data) as unknown as SavedScenario;
}
