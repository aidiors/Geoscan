import { cloneFleet } from "@/features/planner/model/fleetCatalog";
import type { DroneProfile, FlightSiteState, Objective, PlanningSettings, SurveySettings, WorkAreaSource } from "@/features/planner/model/types";
import type { ImportedZone, MissionTask } from "@/shared/api/types";

export interface SavedScenario {
  version?: 2 | 3;
  scenarioId: string;
  task: MissionTask;
  objective: Objective;
  drones: DroneProfile[];
  selectedDroneId: string;
  sites: FlightSiteState[];
  importedZones: ImportedZone[];
  workAreaSource?: WorkAreaSource;
  survey: SurveySettings;
  settings: PlanningSettings;
}

export function migrateLegacyDroneProfile(drone: DroneProfile): DroneProfile {
  const preset = cloneFleet().find((candidate) => candidate.id === drone.id);
  if (!preset) return drone;
  return {
    ...drone,
    ...preset,
    battery: drone.battery,
    reserve: drone.reserve,
    enabled: drone.enabled,
    completedFlights: drone.completedFlights,
    accumulatedHours: drone.accumulatedHours,
  };
}

export function normalizeDroneProfile(drone: DroneProfile): DroneProfile {
  return {
    ...drone,
    modelId: drone.modelId ?? (cloneFleet().some((preset) => preset.id === drone.id) ? drone.id : "custom"),
    availableBatterySets: drone.availableBatterySets ?? null,
    autopilotFirmware: drone.autopilotFirmware ?? "generic_mavlink",
    airframeType: drone.airframeType ?? "generic_multirotor",
    hoverSpeed: drone.hoverSpeed ?? 5,
    batteryHealth: drone.batteryHealth ?? 100,
    energyConsumptionMultiplier: drone.energyConsumptionMultiplier ?? 1,
    maximumClimbRate: drone.maximumClimbRate ?? null,
    maximumDescentRate: drone.maximumDescentRate ?? null,
    minimumMissionAltitude: drone.minimumMissionAltitude ?? null,
    maximumMissionAltitude: drone.maximumMissionAltitude ?? null,
    minimumMissionAltitudeAgl:
      drone.minimumMissionAltitudeAgl ?? drone.minimumMissionAltitude ?? null,
    maximumMissionAltitudeAgl:
      drone.maximumMissionAltitudeAgl ?? drone.maximumMissionAltitude ?? null,
    minimumMissionAltitudeMsl:
      drone.minimumMissionAltitudeMsl ?? drone.minimumMissionAltitude ?? null,
    maximumMissionAltitudeMsl:
      drone.maximumMissionAltitudeMsl ?? drone.maximumMissionAltitude ?? null,
    minimumCruiseSpeed: drone.minimumCruiseSpeed ?? null,
    maximumCruiseSpeed: drone.maximumCruiseSpeed ?? null,
    maximumRouteDistance: drone.maximumRouteDistance ?? null,
    minimumOperatingTemperature: drone.minimumOperatingTemperature ?? null,
    maximumOperatingTemperature: drone.maximumOperatingTemperature ?? null,
    maximumControlRange: drone.maximumControlRange ?? null,
    minimumTurnaroundMinutes: drone.minimumTurnaroundMinutes ?? 0,
    payloads: drone.payloads ?? [],
  };
}
