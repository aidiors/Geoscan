import { describe, expect, it } from "vitest";

import { cloneFleet, cloneSites, scenarios } from "@/features/demos/data/scenarios";
import type { PlanningSettings, SurveySettings } from "@/features/planner/model/types";
import { activeNoFlyZones, activeWorkAreas, buildPlanRequest } from "@/features/planner/api/buildPlanRequest";

const survey: SurveySettings = {
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

const settings: PlanningSettings = {
  altitude: 120,
  windSpeed: 5,
  windDirection: 315,
  ambientTemperature: 20,
  deadline: "45",
  maximumActiveDrones: "2",
  airspaceMinimumAltitude: 20,
  airspaceMaximumAltitude: 500,
  minimumSeparation: 50,
  minimumVerticalSeparation: 30,
  verticalDeconflictionStep: 30,
  maximumVerticalDeconfliction: 60,
  positionUncertainty: 5,
  startTimeUncertainty: 3,
  speedUncertainty: 1,
  flightSiteOccupancy: 30,
  turnaroundMinutes: 0,
  planningStart: "",
  planningEnd: "",
  turnRadius: 0,
  fastHeuristic: false,
  productiveRouting: true,
};

describe("plan request", () => {
  it("selects an imported batch without substituting demo geometry or demo segments", () => {
    const scenario = scenarios[0];
    const first = { ...scenario.workAreas[0], zone_id: "first" };
    const second = { ...first, zone_id: "second", enabled: false };
    expect(activeWorkAreas([first, second], scenario)).toEqual([first]);
    expect(activeWorkAreas([{ ...first, enabled: false }], scenario)).toEqual([]);
    const build = (zones: typeof first[]) => buildPlanRequest("flight", scenario.task,
      { ...scenario, segments: [{ segment_id: "demo", distance_km: 1, route: first.coordinates.slice(0, 2) }] },
      cloneFleet(), cloneSites(scenario), zones, survey, settings);
    expect(build([first]).segments).toEqual([]);
    expect(() => build([{ ...first, enabled: false }])).toThrow("Выберите хотя бы одну");
  });
  it("allows comparison with the reference pipeline and keeps fast mode fast", () => {
    const scenario = scenarios[0];
    const build = (overrides: Partial<PlanningSettings>) => buildPlanRequest(
      "flight", scenario.task, scenario, cloneFleet(), cloneSites(scenario), [], survey,
      { ...settings, ...overrides },
    );
    expect(build({ fastHeuristic: false }).assignment_strategy).toBe("automatic");
    expect(build({ fastHeuristic: true }).assignment_strategy).toBe("hybrid");
    expect(build({ productiveRouting: false }).productive_routing).toBe(false);
    expect(build({ productiveRouting: true, fastHeuristic: true }).productive_routing).toBe(false);
    expect(build({ productiveRouting: true, fastHeuristic: false }).productive_routing).toBe(true);
  });
  it("uses imported work and restriction zones instead of demonstration geometry", () => {
    const scenario = scenarios[0];
    const workArea = { ...scenario.workAreas[0], zone_id: "imported-work" };
    const restriction = { ...scenario.noFlyZones[0], zone_id: "imported-restriction" };

    expect(activeWorkAreas([workArea, restriction], scenario)).toEqual([workArea]);
    expect(activeNoFlyZones([workArea, restriction], scenario)).toEqual([restriction]);
  });

  it("builds a balanced server contract with a deadline and fleet limit", () => {
    const scenario = scenarios[0];

    const request = buildPlanRequest(
      "balanced",
      scenario.task,
      scenario,
      cloneFleet(),
      cloneSites(scenario),
      [],
      survey,
      settings,
    );

    expect(request.criterion).toBe("balanced");
    expect(request.completion_deadline_minutes).toBe(45);
    expect(request.maximum_active_drones).toBe(2);
    expect(request.optimization_mode).toBe("optimized");
    expect(request.productive_routing).toBe(true);
    expect(request.minimum_separation_m).toBe(50);
    expect(request.minimum_vertical_separation_m).toBe(30);
    expect(request.vertical_deconfliction_step_m).toBe(30);
    expect(request.maximum_vertical_deconfliction_m).toBe(60);
    expect(request.position_uncertainty_m).toBe(5);
    expect(request.start_time_uncertainty_seconds).toBe(3);
    expect(request.turnaround_minutes).toBe(0);
    expect(request.minimum_turn_radius_m).toBe(0);
    expect(request.ambient_temperature_c).toBe(20);
    expect(request.survey_parameters.direction_mode).toBe("automatic");
    expect(request.drones[0].autopilot_profile).toEqual({
      firmware: "generic_mavlink",
      airframe: "fixed_wing",
      hover_speed_mps: 5,
    });
    expect(request.drones[0].initial_charge_percent).toBe(100);
    expect(request.drones[0].battery_health_percent).toBe(100);
    expect(request.drones[0].energy_consumption_multiplier).toBe(1);
    expect(request.drones[0].minimum_mission_altitude_m).toBeNull();
    expect(request.drones[0].minimum_mission_altitude_agl_m).toBe(100);
    expect(request.drones[0].maximum_mission_altitude_msl_m).toBe(4000);
    const gemini = request.drones.find((drone) => drone.drone_id === "gemini");
    expect(gemini?.maximum_mission_altitude_agl_m).toBe(500);
    expect(gemini?.maximum_mission_altitude_msl_m).toBe(4000);
    expect(request.drones[0].payloads.map((payload) => payload.payload_id)).toEqual([
      "201-rgb",
      "201-pollux",
    ]);
    expect(request.coverage_areas).toHaveLength(scenario.workAreas.length);
    expect(request.departure_sites).toHaveLength(1);
  });

  it("builds selectable scenarios with multiple work and restricted zones", () => {
    const multiZoneScenarios = scenarios.filter((scenario) => scenario.workAreas.length > 1);

    expect(multiZoneScenarios).toHaveLength(3);
    for (const scenario of multiZoneScenarios) {
      const request = buildPlanRequest(
        "time",
        scenario.task,
        scenario,
        cloneFleet(),
        cloneSites(scenario),
        [],
        survey,
        { ...settings, altitude: scenario.altitude, maximumActiveDrones: "" },
      );

      expect(request.coverage_areas).toHaveLength(scenario.workAreas.length);
      expect(request.no_fly_zones).toHaveLength(scenario.noFlyZones.length);
      expect(request.coverage_areas.length).toBeGreaterThan(1);
      expect(request.no_fly_zones.length).toBeGreaterThan(1);
    }
  });

  it("rejects balanced mode without a deadline", () => {
    const scenario = scenarios[0];

    expect(() => buildPlanRequest(
      "balanced",
      scenario.task,
      scenario,
      cloneFleet(),
      cloneSites(scenario),
      [],
      survey,
      { ...settings, deadline: "" },
    )).toThrow("задайте срок");
  });

  it("rejects insufficient side overlap for aerial imaging", () => {
    const scenario = scenarios[0];

    expect(() => buildPlanRequest(
      "time",
      "rgb",
      scenario,
      cloneFleet(),
      cloneSites(scenario),
      [],
      { ...survey, sideOverlap: 49 },
      settings,
    )).toThrow("не менее 50%");
  });

  it("passes an optional positive survey turn radius to the server", () => {
    const scenario = scenarios[0];

    const request = buildPlanRequest(
      "time", scenario.task, scenario, cloneFleet(), cloneSites(scenario), [], survey,
      { ...settings, turnRadius: 10 },
    );

    expect(request.minimum_turn_radius_m).toBe(10);
  });

  it("passes imported airspace validity and an absolute planning start", () => {
    const scenario = scenarios[0];
    const planningStart = "2026-09-27T10:00";
    const airspace = {
      ...scenario.workAreas[0],
      zone_id: "timed-airspace",
      zone_type: "allowed_airspace" as const,
      restriction_type: "temporary" as const,
      validity: {
        mode: "time_window" as const,
        valid_from: "2026-09-27T08:00:00Z",
        valid_to: "2026-09-27T09:00:00Z",
      },
      minimum_altitude_m: 80,
      maximum_altitude_m: 160,
      altitude_reference: "msl" as const,
    };

    const request = buildPlanRequest(
      "time",
      scenario.task,
      scenario,
      cloneFleet(),
      cloneSites(scenario),
      [airspace],
      survey,
      { ...settings, planningStart },
    );

    expect(request.planning_start_time).toBe(new Date(planningStart).toISOString());
    expect(request.allowed_airspaces[0].validity).toEqual(airspace.validity);
    expect(request.allowed_airspaces[0].minimum_altitude_m).toBe(80);
    expect(request.allowed_airspaces[0].maximum_altitude_m).toBe(160);
    expect(request.allowed_airspaces[0].altitude_reference).toBe("msl");
  });

  it("passes timed no-fly metadata and an operating window", () => {
    const scenario = scenarios[0];
    const planningStart = "2026-09-27T10:00";
    const planningEnd = "2026-09-27T18:00";
    const restriction = {
      ...scenario.noFlyZones[0],
      zone_id: "timed-restriction",
      restriction_type: "temporary" as const,
      minimum_altitude_m: 100,
      maximum_altitude_m: 150,
      altitude_reference: "msl" as const,
      validity: {
        mode: "time_window" as const,
        valid_from: "2026-09-27T08:00:00Z",
        valid_to: "2026-09-27T09:00:00Z",
      },
    };

    const request = buildPlanRequest(
      "time",
      scenario.task,
      scenario,
      cloneFleet(),
      cloneSites(scenario),
      [restriction],
      survey,
      { ...settings, planningStart, planningEnd },
    );

    expect(request.planning_end_time).toBe(new Date(planningEnd).toISOString());
    expect(request.no_fly_zones[0]).toMatchObject({
      minimum_altitude_m: 100,
      maximum_altitude_m: 150,
      altitude_reference: "msl",
      validity: restriction.validity,
    });
  });

  it("rejects a temporary imported airspace without a parsed validity window", () => {
    const scenario = scenarios[0];
    const airspace = {
      ...scenario.workAreas[0],
      zone_id: "ambiguous-airspace",
      zone_type: "allowed_airspace" as const,
      restriction_type: "temporary" as const,
      validity: null,
    };

    expect(() => buildPlanRequest(
      "time",
      scenario.task,
      scenario,
      cloneFleet(),
      cloneSites(scenario),
      [airspace],
      survey,
      { ...settings, planningStart: "2026-09-27T10:00" },
    )).toThrow("У временной зоны нет однозначного окна действия");
  });
});
