import type { MissionTask, SweepDirectionMode, AutopilotFirmware, AirframeType, ImportedZone, WorkSegment, PayloadCapability } from "@/shared/api/types";
import type { Coordinate } from "@/shared/geo/types";

export type Objective = "time" | "flight" | "balanced";

export type SiteRole = "takeoff" | "landing" | "alternate";

export interface DroneProfile {
  id: string;
  name: string;
  color: string;
  battery: number;
  batteryHealth: number;
  energyConsumptionMultiplier: number;
  speed: number;
  flightMinutes: number;
  maxWind: number;
  reserve: number;
  enabled: boolean;
  tasks: MissionTask[];
  autopilotFirmware: AutopilotFirmware;
  airframeType: AirframeType;
  hoverSpeed: number;
  maintenanceFlights: number | null;
  maintenanceHours: number | null;
  completedFlights: number;
  accumulatedHours: number;
  maximumClimbRate: number | null;
  maximumDescentRate: number | null;
  minimumMissionAltitude: number | null;
  maximumMissionAltitude: number | null;
  minimumMissionAltitudeAgl: number | null;
  maximumMissionAltitudeAgl: number | null;
  minimumMissionAltitudeMsl: number | null;
  maximumMissionAltitudeMsl: number | null;
  minimumCruiseSpeed: number | null;
  maximumCruiseSpeed: number | null;
  maximumRouteDistance: number | null;
  minimumOperatingTemperature: number | null;
  maximumOperatingTemperature: number | null;
  maximumControlRange: number | null;
  minimumTurnaroundMinutes: number;
  payloads: PayloadCapability[];
}

export interface FlightSiteState {
  site_id: string;
  name: string;
  role: SiteRole;
  coordinate: Coordinate;
  assignedDroneId: string;
}

export interface Scenario {
  id: string;
  title: string;
  description: string;
  task: MissionTask;
  altitude: number;
  workAreas: ImportedZone[];
  noFlyZones: ImportedZone[];
  segments: WorkSegment[];
  sites: FlightSiteState[];
}

export interface DemoCase {
  id: string;
  scenarioId: string;
  title: string;
  category: string;
  description: string;
  highlights: string[];
  expectedEvidence: string[];
  recommendedObjective: Objective;
  settings?: Partial<PlanningSettings>;
}

export interface SurveySettings {
  directionMode: SweepDirectionMode;
  direction: number;
  stripWidth: number;
  sideOverlap: number;
  forwardOverlap: number;
  maximumGsd: number;
  maximumSegmentDistance: number;
  sensorWidth: number;
  sensorHeight: number;
  focalLength: number;
  imageWidth: number;
  imageHeight: number;
  triggerInterval: number;
  restrictionMargin: number;
}

export interface PlanningSettings {
  altitude: number;
  windSpeed: number;
  windDirection: number;
  ambientTemperature: number;
  deadline: string;
  maximumActiveDrones: string;
  airspaceMinimumAltitude: number;
  airspaceMaximumAltitude: number;
  minimumSeparation: number;
  minimumVerticalSeparation: number;
  verticalDeconflictionStep: number;
  maximumVerticalDeconfliction: number;
  positionUncertainty: number;
  startTimeUncertainty: number;
  speedUncertainty: number;
  flightSiteOccupancy: number;
  turnaroundMinutes: number;
  planningStart: string;
  planningEnd: string;
  turnRadius: number;
  fastHeuristic: boolean;
  productiveRouting: boolean;
}
