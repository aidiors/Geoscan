import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ImportedZone, PlanResponse, TurnRadiusAnalysisResult } from "@/shared/api/types";
import { ResultsPanel } from "@/features/planner/ui/ResultsPanel";

afterEach(cleanup);

const workAreas: ImportedZone[] = [{
  zone_id: "field-1",
  name: "Поле 1",
  zone_type: "work_area",
  geometry_type: "polygon",
  coordinates: [
    { longitude: 37, latitude: 55 },
    { longitude: 37.01, latitude: 55 },
    { longitude: 37.01, latitude: 55.01 },
  ],
  interior_rings: [],
}];

const plan: PlanResponse = {
  criterion: "completion_time",
  drone_plans: [],
  metrics: {
    completion_minutes: 10,
    total_flight_minutes: 10,
    total_distance_km: 5,
    survey_distance_km: 4,
    transit_distance_km: 1,
    useful_flight_percent: 80,
    average_fleet_utilization_percent: 80,
    active_drone_count: 1,
    sortie_count: 1,
  },
  feasibility: null,
  optimization: null,
  schedule: null,
};

describe("ResultsPanel radius assumptions", () => {
  it("shows actionable feasibility recommendations and their confidence", () => {
    const infeasiblePlan: PlanResponse = {
      ...plan,
      feasibility: {
        status: "infeasible",
        message: "План превышает срок на 5 минут.",
        requested_deadline_minutes: 15,
        maximum_active_drones: 1,
        used_drone_count: 1,
        estimated_required_drone_count: 2,
        deadline_margin_minutes: -5,
        recommendations: [{
          code: "extend_deadline",
          message: "Увеличьте срок минимум до 20 минут.",
          confidence: "exact",
          parameter: "completion_deadline_minutes",
          current_value: 15,
          required_value: 20,
          unit: "minutes",
          affected_ids: [],
        }],
      },
    };

    render(<ResultsPanel plan={infeasiblePlan} drones={[]} selectedDroneId="201" exporting={false} workAreas={workAreas} radiusAnalysis={null} analyzingRadii={false} onAnalyzeRadii={vi.fn()} onSelectDrone={vi.fn()} onExport={vi.fn()} />);

    expect(screen.getByText("Как сделать план выполнимым")).toBeVisible();
    expect(screen.getByText("Увеличьте срок минимум до 20 минут.")).toBeVisible();
    expect(screen.getByText("проверено пересчётом")).toBeVisible();
  });

  it("shows robust separation uncertainty in the schedule summary", () => {
    const robustPlan: PlanResponse = {
      ...plan,
      schedule: {
        algorithm: "priority_deconfliction",
        makespan_minutes: 10.7,
        minimum_separation_m: 50,
        position_uncertainty_m: 10,
        start_time_uncertainty_seconds: 3,
        speed_uncertainty_percent: 1,
        flight_site_occupancy_seconds: 30,
        turnaround_minutes: 0,
        resolved_conflict_count: 1,
        resolved_conflicts: [],
      },
    };

    render(<ResultsPanel plan={robustPlan} drones={[]} selectedDroneId="201" exporting={false} workAreas={workAreas} radiusAnalysis={null} analyzingRadii={false} onAnalyzeRadii={vi.fn()} onSelectDrone={vi.fn()} onExport={vi.fn()} />);

    expect(screen.getByText("Расписание вылетов")).toBeVisible();
    expect(screen.getByText(/позиция ±10 м · старт ±3 с/)).toBeVisible();
  });

  it("shows the independent plan safety audit", () => {
    const auditedPlan: PlanResponse = {
      ...plan,
      safety_audit: {
        status: "failed",
        audited_mission_count: 2,
        checks: [{
          code: "drone_separation",
          status: "failed",
          measured_value: 12,
          required_value: 50,
          unit: "m",
          message: "Minimum separation is below the requirement.",
          affected_ids: ["201:field-1", "401:field-2"],
        }],
        assumptions: ["Constant wind."],
      },
    };

    render(<ResultsPanel plan={auditedPlan} drones={[]} selectedDroneId="201" exporting={false} workAreas={workAreas} radiusAnalysis={null} analyzingRadii={false} onAnalyzeRadii={vi.fn()} onSelectDrone={vi.fn()} onExport={vi.fn()} />);

    expect(screen.getByText("Независимый аудит плана")).toBeVisible();
    expect(screen.getByText("Обнаружены нарушения - план нельзя выпускать")).toBeVisible();
    expect(screen.getByText("Разведение БВС")).toBeVisible();
  });

  it("shows battery use and margin above the mandatory reserve", () => {
    const batteryPlan: PlanResponse = {
      ...plan,
      drone_plans: [{
        drone_id: "201",
        mission_count: 1,
        total_duration_minutes: 21,
        autopilot_profile: {
          firmware: "generic_mavlink",
          airframe: "fixed_wing",
          hover_speed_mps: 5,
        },
        maintenance: null,
        missions: [{
          drone_id: "201",
          segment_id: "field-1",
          duration_minutes: 21,
          distance_km: 10,
          route: [
            { longitude: 37, latitude: 55 },
            { longitude: 37.01, latitude: 55.01 },
          ],
          altitude_m: 120,
          altitude_reference: "msl",
          vertical_profile: [],
          minimum_agl_m: null,
          minimum_terrain_elevation_m: null,
          maximum_terrain_elevation_m: null,
          maximum_required_climb_rate_mps: null,
          maximum_required_descent_rate_mps: null,
          vertical_distance_m: 0,
          vertical_energy_equivalent_minutes: 0,
          vertical_climb_duration_minutes: 0,
          vertical_descent_duration_minutes: 0,
          cruise_speed_kmh: 60,
          effective_speed_kmh: 28.57,
          estimated_battery_percent: 35,
          starting_battery_percent: 100,
          remaining_battery_percent: 65,
          battery_reserve_margin_percent: 45,
          minimum_alternate_margin_percent: 30,
          critical_alternate_site_id: "reserve",
          critical_alternate_route: [],
          critical_alternate_start_offset_minutes: null,
          critical_alternate_duration_minutes: null,
          alternate_check_spacing_m: 500,
          alternate_evaluated_checkpoint_count: 21,
          alternate_certification_error_minutes: 0.1,
          survey_distance_km: 8,
          transit_distance_km: 2,
          survey_duration_minutes: 18,
          transit_duration_minutes: 3,
          useful_flight_percent: 80,
          image_count: 100,
          computed_gsd_cm: 3,
          strip_spacing_m: 40,
          flight_direction_degrees: 90,
          start_offset_minutes: 0,
          end_offset_minutes: 21,
          stages: [],
        }],
      }],
    };

    render(<ResultsPanel plan={batteryPlan} drones={[]} selectedDroneId="201" exporting={false} workAreas={workAreas} radiusAnalysis={null} analyzingRadii={false} onAnalyzeRadii={vi.fn()} onSelectDrone={vi.fn()} onExport={vi.fn()} />);

    expect(screen.getByText(/100%.*35%.*45/)).toBeVisible();
    expect(screen.getByText(/Аварийный уход: минимум 30 п\.п\. сверх резерва/)).toBeVisible();
    expect(screen.getByRole("button", { name: "GeoJSON" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "QGroundControl" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Mission Planner" })).toBeDisabled();
  });

  it("labels a positive radius as applying to the complete route", () => {
    const curvedPlan: PlanResponse = {
      ...plan,
      optimization: {
        algorithm: "exact_branch_and_bound",
        coverage_algorithm: "boustrophedon_sweep",
        route_algorithm: "bidirectional_visibility_graph",
        scope: "generated_route_assignment",
        optimality: "proven_optimal",
        completion_minutes: 10,
        total_flight_minutes: 10,
        active_drone_count: 1,
        survey_turn_radius_m: 10,
        coverage_angle_cost: "wind_adjusted_duration",
        coverage_reference_airspeed_kmh: 55,
      },
    };

    render(<ResultsPanel plan={curvedPlan} drones={[]} selectedDroneId="201" exporting={false} workAreas={workAreas} radiusAnalysis={null} analyzingRadii={false} onAnalyzeRadii={vi.fn()} onSelectDrone={vi.fn()} onExport={vi.fn()} />);

    expect(screen.getByText(/Развороты, транзит и подход к посадке рассчитаны для радиуса 10 м/)).toBeVisible();
    expect(screen.getByText(/Направление галсов выбрано по времени с учётом ветра · опорная скорость 55 км\/ч/)).toBeVisible();
  });

  it("offers an optional zero-radius check by default", () => {
    const analyze = vi.fn();

    render(<ResultsPanel plan={plan} drones={[]} selectedDroneId="201" exporting={false} workAreas={workAreas} radiusAnalysis={null} analyzingRadii={false} onAnalyzeRadii={analyze} onSelectDrone={vi.fn()} onExport={vi.fn()} />);
    expect(screen.getByText(/0 м отключает ограничение/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Проверить диапазон" }));

    expect(analyze).toHaveBeenCalledWith("field-1", 0, 0);
  });

  it("submits the chosen area and radius range after planning", () => {
    const analyze = vi.fn();

    render(<ResultsPanel plan={plan} drones={[]} selectedDroneId="201" exporting={false} workAreas={workAreas} radiusAnalysis={null} analyzingRadii={false} onAnalyzeRadii={analyze} onSelectDrone={vi.fn()} onExport={vi.fn()} />);
    fireEvent.change(screen.getByRole("spinbutton", { name: "От, м" }), { target: { value: "20" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "До, м" }), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: "Проверить диапазон" }));

    expect(analyze).toHaveBeenCalledWith("field-1", 20, 60);
  });

  it("shows conditional result and rejects an invalid range", () => {
    const result: TurnRadiusAnalysisResult = {
      area_id: "field-1",
      scenarios: [
        { radius_m: 15, status: "feasible", message: "Проверено", distance_km: 4, uncovered_area_m2: 0 },
        { radius_m: 27.5, status: "feasible", message: "Проверено", distance_km: 5, uncovered_area_m2: 0 },
        { radius_m: 40, status: "feasible", message: "Проверено", distance_km: 6, uncovered_area_m2: 0 },
      ],
      common_survey_route: null,
      message: "Это не проверка всего задания.",
    };

    render(<ResultsPanel plan={plan} drones={[]} selectedDroneId="201" exporting={false} workAreas={workAreas} radiusAnalysis={result} analyzingRadii={false} onAnalyzeRadii={vi.fn()} onSelectDrone={vi.fn()} onExport={vi.fn()} />);
    fireEvent.change(screen.getByRole("spinbutton", { name: "От, м" }), { target: { value: "15" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "До, м" }), { target: { value: "40" } });
    expect(screen.getByText("Это не проверка всего задания.")).toBeVisible();
    fireEvent.change(screen.getByRole("spinbutton", { name: "От, м" }), { target: { value: "100" } });

    expect(screen.getByRole("button", { name: "Проверить диапазон" })).toBeDisabled();
  });

  it("hides a result after the radius range changes", () => {
    const result: TurnRadiusAnalysisResult = {
      area_id: "field-1",
      scenarios: [
        { radius_m: 15, status: "feasible", message: "Проверено", distance_km: 4, uncovered_area_m2: 0 },
        { radius_m: 27.5, status: "feasible", message: "Проверено", distance_km: 5, uncovered_area_m2: 0 },
        { radius_m: 40, status: "feasible", message: "Проверено", distance_km: 6, uncovered_area_m2: 0 },
      ],
      common_survey_route: null,
      message: "Результат для старого диапазона.",
    };

    render(<ResultsPanel plan={plan} drones={[]} selectedDroneId="201" exporting={false} workAreas={workAreas} radiusAnalysis={result} analyzingRadii={false} onAnalyzeRadii={vi.fn()} onSelectDrone={vi.fn()} onExport={vi.fn()} />);
    fireEvent.change(screen.getByRole("spinbutton", { name: "От, м" }), { target: { value: "15" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "До, м" }), { target: { value: "40" } });
    expect(screen.getByText("Результат для старого диапазона.")).toBeVisible();

    fireEvent.change(screen.getByRole("spinbutton", { name: "От, м" }), { target: { value: "20" } });

    expect(screen.queryByText("Результат для старого диапазона.")).not.toBeInTheDocument();
    expect(screen.queryByText("15 м")).not.toBeInTheDocument();
  });

  it("explains when smaller radii pass but the maximum radius does not", () => {
    const result: TurnRadiusAnalysisResult = {
      area_id: "field-1",
      scenarios: [
        { radius_m: 15, status: "feasible", message: "Маршрут построен", distance_km: 4, uncovered_area_m2: 0 },
        { radius_m: 27.5, status: "no_route_for_selected_sweeps", message: "Разворот не построен", distance_km: null, uncovered_area_m2: null },
        { radius_m: 40, status: "no_route_for_selected_sweeps", message: "Разворот не построен", distance_km: null, uncovered_area_m2: null },
      ],
      common_survey_route: null,
      message: "Общий маршрут для всего диапазона не найден.",
    };

    render(<ResultsPanel plan={plan} drones={[]} selectedDroneId="201" exporting={false} workAreas={workAreas} radiusAnalysis={result} analyzingRadii={false} onAnalyzeRadii={vi.fn()} onSelectDrone={vi.fn()} onExport={vi.fn()} />);
    fireEvent.change(screen.getByRole("spinbutton", { name: "От, м" }), { target: { value: "15" } });
    fireEvent.change(screen.getByRole("spinbutton", { name: "До, м" }), { target: { value: "40" } });

    expect(screen.getByText(/Частичный результат: маршрут построен для некоторых меньших радиусов, но не для наибольшего заданного радиуса/)).toBeVisible();
    expect(screen.getByText("4 км · построен")).toBeVisible();
    expect(screen.getAllByText("Для этих галсов не найден")).toHaveLength(2);
  });
});
