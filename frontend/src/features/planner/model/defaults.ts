import type { PlanningSettings, SurveySettings } from "@/features/planner/model/types";
import { maximumPlanningSeconds } from "./planningBudget";

export const defaultSurvey: SurveySettings = {
  directionMode: "automatic",
  direction: 90,
  stripWidth: 200,
  sideOverlap: 60,
  forwardOverlap: 75,
  maximumGsd: 5,
  maximumSegmentDistance: 20,
  sensorWidth: 13.2,
  sensorHeight: 8.8,
  focalLength: 8.8,
  imageWidth: 5472,
  imageHeight: 3648,
  triggerInterval: 1,
  restrictionMargin: 30,
};

export function planningSettings(
  altitude: number,
  overrides: Partial<PlanningSettings> = {},
): PlanningSettings {
  return {
    altitude,
    altitudeReference: "msl",
    terrainSamplingInterval: 25,
    flatGroundElevation: 0,
    terrainVerticalUncertainty: 0,
    windSpeed: 5,
    windDirection: 315,
    ambientTemperature: 20,
    deadline: "",
    maximumActiveDrones: "",
    airspaceMinimumAltitude: 20,
    airspaceMaximumAltitude: 500,
    minimumSeparation: 50,
    minimumVerticalSeparation: 30,
    verticalDeconflictionStep: 30,
    maximumVerticalDeconfliction: 0,
    positionUncertainty: 0,
    startTimeUncertainty: 0,
    speedUncertainty: 0,
    flightSiteOccupancy: 0,
    turnaroundMinutes: 0,
    planningStart: "",
    planningEnd: "",
    turnRadius: 0,
    fastHeuristic: false,
    productiveRouting: true,
    planningTimeLimitSeconds: maximumPlanningSeconds,
    ...overrides,
  };
}
