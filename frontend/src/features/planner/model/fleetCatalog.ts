import type { DroneProfile } from "./types";
import type { MissionTask } from "@/shared/api/types";

const modelProfiles: DroneProfile[] = [
  {
    id: "201",
    name: "Геоскан 201",
    color: "#6d28d9",
    battery: 100,
    batteryHealth: 100,
    energyConsumptionMultiplier: 1,
    speed: 70,
    flightMinutes: 180,
    maxWind: 12,
    reserve: 20,
    enabled: true,
    tasks: ["rgb", "multispectral"],
    autopilotFirmware: "generic_mavlink",
    airframeType: "fixed_wing",
    hoverSpeed: 5,
    maintenanceFlights: 80,
    maintenanceHours: null,
    completedFlights: 0,
    accumulatedHours: 0,
    maximumClimbRate: null,
    maximumDescentRate: null,
    minimumMissionAltitude: null,
    maximumMissionAltitude: null,
    minimumMissionAltitudeAgl: 100,
    maximumMissionAltitudeAgl: null,
    minimumMissionAltitudeMsl: null,
    maximumMissionAltitudeMsl: 4000,
    minimumCruiseSpeed: 64,
    maximumCruiseSpeed: 130,
    maximumRouteDistance: 210,
    minimumOperatingTemperature: -20,
    maximumOperatingTemperature: 40,
    maximumControlRange: 40,
    minimumTurnaroundMinutes: 0,
    payloads: [
      { payload_id: "201-rgb", name: "Камера Геоскан 201 RGB", supported_tasks: ["rgb"] },
      { payload_id: "201-pollux", name: "Геоскан Pollux", supported_tasks: ["multispectral"] },
    ],
  },
  {
    id: "801",
    name: "Геоскан 801",
    color: "#d946ef",
    battery: 100,
    batteryHealth: 100,
    energyConsumptionMultiplier: 1,
    speed: 54,
    flightMinutes: 40,
    maxWind: 10,
    reserve: 20,
    enabled: true,
    tasks: ["rgb", "thermal", "video"],
    autopilotFirmware: "generic_mavlink",
    airframeType: "quadrotor",
    hoverSpeed: 5,
    maintenanceFlights: null,
    maintenanceHours: 160,
    completedFlights: 0,
    accumulatedHours: 0,
    maximumClimbRate: null,
    maximumDescentRate: null,
    minimumMissionAltitude: null,
    maximumMissionAltitude: null,
    minimumMissionAltitudeAgl: null,
    maximumMissionAltitudeAgl: null,
    minimumMissionAltitudeMsl: null,
    maximumMissionAltitudeMsl: 4000,
    minimumCruiseSpeed: null,
    maximumCruiseSpeed: 54,
    maximumRouteDistance: null,
    minimumOperatingTemperature: -20,
    maximumOperatingTemperature: 40,
    maximumControlRange: 10,
    minimumTurnaroundMinutes: 0,
    payloads: [
      { payload_id: "801-visible", name: "Камера видимого спектра 801", supported_tasks: ["rgb", "video"] },
      { payload_id: "801-thermal", name: "Тепловизор 801", supported_tasks: ["thermal", "video"] },
    ],
  },
  {
    id: "gemini",
    name: "Геоскан Gemini",
    color: "#0f766e",
    battery: 100,
    batteryHealth: 100,
    energyConsumptionMultiplier: 1,
    speed: 43,
    flightMinutes: 40,
    maxWind: 10,
    reserve: 20,
    enabled: true,
    tasks: ["rgb", "multispectral"],
    autopilotFirmware: "generic_mavlink",
    airframeType: "quadrotor",
    hoverSpeed: 5,
    maintenanceFlights: null,
    maintenanceHours: null,
    completedFlights: 0,
    accumulatedHours: 0,
    maximumClimbRate: 5,
    maximumDescentRate: 5,
    minimumMissionAltitude: null,
    maximumMissionAltitude: null,
    minimumMissionAltitudeAgl: null,
    maximumMissionAltitudeAgl: 500,
    minimumMissionAltitudeMsl: null,
    maximumMissionAltitudeMsl: 4000,
    minimumCruiseSpeed: null,
    maximumCruiseSpeed: 54,
    maximumRouteDistance: null,
    minimumOperatingTemperature: -15,
    maximumOperatingTemperature: 40,
    maximumControlRange: 5,
    minimumTurnaroundMinutes: 0,
    payloads: [
      { payload_id: "gemini-pf1b", name: "Geoscan PF1B", supported_tasks: ["rgb"] },
      { payload_id: "gemini-pollux", name: "Геоскан Pollux", supported_tasks: ["multispectral"] },
    ],
  },
];

export class FleetCatalog {
  readonly models = modelProfiles.map((profile) => ({ id: profile.id, name: profile.name }));

  create(modelId: string, count: number, existing: DroneProfile[], task: MissionTask): DroneProfile[] {
    this.validateCount(count, existing.length);
    const preset = modelProfiles.find((profile) => profile.id === modelId);
    if (!preset && modelId !== "custom") throw new Error("Неизвестная модель БВС.");
    return Array.from({ length: count }, (_, index) => {
      const ordinal = existing.length + index + 1;
      const profile = preset ? structuredClone(preset) : this.customProfile(task);
      return {
        ...profile, id: crypto.randomUUID(), modelId,
        name: `${preset?.name ?? "БВС"} · ${ordinal}`,
        color: ["#0891b2", "#ea580c", "#4f46e5"][ordinal % 3],
      };
    });
  }

  clone(source: DroneProfile, existing: DroneProfile[]): DroneProfile {
    this.validateCount(1, existing.length);
    return {
      ...structuredClone(source), id: crypto.randomUUID(),
      name: `${source.name} · копия`,
      color: ["#0891b2", "#ea580c", "#4f46e5"][existing.length % 3],
    };
  }

  private validateCount(count: number, current: number): void {
    if (!Number.isInteger(count) || count < 1 || current + count > 100) {
      throw new Error("Добавьте целое количество БВС; в парке допускается до 100 бортов.");
    }
  }

  private customProfile(task: MissionTask): DroneProfile {
    return {
      id: "custom", name: "БВС", color: "#0891b2", battery: 100,
      batteryHealth: 100, energyConsumptionMultiplier: 1, speed: 50,
      flightMinutes: 60, maxWind: 10, reserve: 20, enabled: true, tasks: [task],
      autopilotFirmware: "generic_mavlink", airframeType: "generic_multirotor", hoverSpeed: 5,
      maintenanceFlights: null, maintenanceHours: null, completedFlights: 0, accumulatedHours: 0,
      maximumClimbRate: null, maximumDescentRate: null,
      minimumMissionAltitude: null, maximumMissionAltitude: null,
      minimumMissionAltitudeAgl: null, maximumMissionAltitudeAgl: null,
      minimumMissionAltitudeMsl: null, maximumMissionAltitudeMsl: null,
      minimumCruiseSpeed: null, maximumCruiseSpeed: null, maximumRouteDistance: null,
      minimumOperatingTemperature: null, maximumOperatingTemperature: null,
      maximumControlRange: null, minimumTurnaroundMinutes: 0,
      payloads: [{ payload_id: "custom-payload", name: "Пользовательская нагрузка", supported_tasks: [task] }],
    };
  }
}

export const fleetCatalog = new FleetCatalog();
export const initialFleet: DroneProfile[] = modelProfiles.map((profile) => ({
  ...structuredClone(profile), modelId: profile.id,
}));

export function cloneFleet(): DroneProfile[] {
  return structuredClone(initialFleet);
}

