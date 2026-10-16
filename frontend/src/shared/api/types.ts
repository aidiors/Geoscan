import type { Coordinate } from "@/shared/geo/types";

export interface PayloadCapability {
  payload_id: string;
  name: string;
  supported_tasks: MissionTask[];
}

export type OptimizationCriterion = "completion_time" | "total_flight_time" | "balanced";

export type MissionTask =
  | "rgb"
  | "multispectral"
  | "thermal"
  | "lidar"
  | "video"
  | "geophysical";

export type ZoneType = "work_area" | "no_fly_zone" | "allowed_airspace" | "obstacle";

export type GeometryType = "point" | "polygon" | "line_string";

export type SweepDirectionMode = "automatic" | "manual";

export type AutopilotFirmware = "generic_mavlink" | "ardupilot" | "px4";

export type AirframeType =
  | "fixed_wing"
  | "quadrotor"
  | "hexarotor"
  | "octorotor"
  | "vtol_fixed_rotor"
  | "generic_multirotor";

export interface AutopilotProfile {
  firmware: AutopilotFirmware;
  airframe: AirframeType;
  hover_speed_mps: number;
}

export interface ImportedZone {
  zone_id: string;
  name: string;
  zone_type: ZoneType;
  geometry_type: GeometryType;
  coordinates: Coordinate[];
  interior_rings: Coordinate[][];
  altitude_mode?: string;
  altitude_text?: string | null;
  minimum_altitude_m?: number | null;
  maximum_altitude_m?: number | null;
  altitude_reference?: "msl" | "agl" | null;
  restriction_type?: "permanent" | "prohibited" | "special" | "temporary" | "unknown" | null;
  validity?: {
    mode: "permanent" | "time_window";
    valid_from: string | null;
    valid_to: string | null;
  } | null;
}

export interface WorkSegment {
  segment_id: string;
  distance_km: number;
  route: Coordinate[];
}

export interface MissionStage {
  stage_type: "takeoff" | "transit" | "survey" | "landing";
  coordinates: Coordinate[];
  duration_minutes: number;
}

export interface Mission {
  drone_id: string;
  segment_id: string;
  duration_minutes: number;
  distance_km: number;
  route: Coordinate[];
  altitude_m: number;
  altitude_reference: "msl" | "agl";
  vertical_profile: Array<{
    coordinate: Coordinate;
    distance_from_start_m: number;
    terrain_elevation_m: number;
    target_altitude_msl: number;
    target_agl_m: number;
    required_vertical_rate_mps: number;
  }>;
  minimum_agl_m: number | null;
  minimum_terrain_elevation_m: number | null;
  maximum_terrain_elevation_m: number | null;
  maximum_required_climb_rate_mps: number | null;
  maximum_required_descent_rate_mps: number | null;
  vertical_distance_m: number;
  vertical_energy_equivalent_minutes: number;
  vertical_climb_duration_minutes: number;
  vertical_descent_duration_minutes: number;
  terrain_vertical_uncertainty_m?: number;
  vertical_deconfliction_offset_m?: number;
  cruise_speed_kmh: number;
  effective_speed_kmh: number;
  estimated_battery_percent: number;
  starting_battery_percent: number;
  remaining_battery_percent: number | null;
  battery_reserve_margin_percent: number | null;
  minimum_alternate_margin_percent: number | null;
  critical_alternate_site_id: string | null;
  critical_alternate_route: Coordinate[];
  critical_alternate_start_offset_minutes: number | null;
  critical_alternate_duration_minutes: number | null;
  alternate_check_spacing_m: number | null;
  alternate_evaluated_checkpoint_count: number | null;
  alternate_certification_error_minutes: number | null;
  survey_distance_km: number | null;
  transit_distance_km: number | null;
  survey_duration_minutes: number | null;
  transit_duration_minutes: number | null;
  useful_flight_percent: number | null;
  image_count: number | null;
  computed_gsd_cm: number | null;
  strip_spacing_m: number | null;
  flight_direction_degrees: number | null;
  start_offset_minutes: number | null;
  end_offset_minutes: number | null;
  start_time?: string | null;
  end_time?: string | null;
  stages: MissionStage[];
}

export interface FeasibilityRecommendation {
  code:
    | "add_alternate_site"
    | "add_capable_drone"
    | "adjust_mission_altitude"
    | "assign_flight_site"
    | "extend_deadline"
    | "extend_endurance"
    | "extend_operating_window"
    | "increase_active_drone_limit"
    | "reduce_mission_scope"
    | "reschedule_operation"
    | "revise_geospatial_constraints"
    | "wait_for_lower_wind";
  message: string;
  confidence: "exact" | "estimate";
  parameter: string | null;
  current_value: number | null;
  required_value: number | null;
  unit: string | null;
  affected_ids: string[];
}

export interface MaintenanceReport {
  projected_flights: number;
  projected_flight_hours: number;
  remaining_flights: number | null;
  remaining_hours: number | null;
  maintenance_due_after_plan: boolean;
  message: string;
}

export interface DronePlan {
  drone_id: string;
  mission_count: number;
  total_duration_minutes: number;
  missions: Mission[];
  autopilot_profile: AutopilotProfile;
  maintenance: MaintenanceReport | null;
}

export interface PlanResponse {
  criterion: OptimizationCriterion;
  drone_plans: DronePlan[];
  metrics: {
    completion_minutes: number;
    total_flight_minutes: number;
    total_distance_km: number;
    survey_distance_km: number;
    transit_distance_km: number;
    useful_flight_percent: number;
    average_fleet_utilization_percent: number;
    active_drone_count: number;
    sortie_count: number;
  };
  feasibility: {
    status: "feasible" | "infeasible";
    message: string;
    requested_deadline_minutes: number | null;
    maximum_active_drones: number | null;
    used_drone_count: number;
    estimated_required_drone_count: number | null;
    deadline_margin_minutes: number | null;
    recommendations: FeasibilityRecommendation[];
  } | null;
  optimization: {
    algorithm:
      | "exact_branch_and_bound"
      | "fast_greedy"
      | "multistart_local_search"
      | "minimum_cost"
      | "deadline_aware_local_search";
    coverage_algorithm: "boustrophedon_sweep" | "adaptive_decomposed_sweep";
    route_algorithm: "bidirectional_visibility_graph";
    scope: "generated_route_assignment";
    optimality: "proven_optimal" | "heuristic";
    completion_minutes: number;
    total_flight_minutes: number;
    active_drone_count: number;
    survey_turn_radius_m: number;
    coverage_angle_cost: "geometric_distance" | "wind_adjusted_duration";
    coverage_reference_airspeed_kmh: number | null;
  } | null;
  schedule: {
    algorithm: "priority_deconfliction";
    makespan_minutes: number;
    minimum_separation_m: number;
    minimum_vertical_separation_m?: number;
    vertically_deconflicted_mission_count?: number;
    position_uncertainty_m: number;
    start_time_uncertainty_seconds: number;
    speed_uncertainty_percent: number;
    flight_site_occupancy_seconds: number;
    turnaround_minutes: number;
    resolved_conflict_count: number;
    resolved_conflicts: Array<{
      conflict_type: "route" | "flight_site" | "emergency_corridor";
      delayed_drone_id: string;
      delayed_segment_id: string;
      conflicting_drone_id: string;
      conflicting_segment_id: string;
      coordinate: Coordinate;
      conflict_offset_minutes: number;
      nominal_distance_m: number;
      required_distance_m: number;
      applied_delay_minutes: number;
      applied_vertical_offset_m?: number;
    }>;
  } | null;
  safety_audit?: {
    status: "passed" | "failed";
    audited_mission_count: number;
    checks: Array<{
      code:
        | "airspace_compliance"
        | "alternate_reserve"
        | "battery_reserve"
        | "coverage_completeness"
        | "drone_separation"
        | "export_consistency"
        | "flight_envelope"
        | "no_fly_clearance"
        | "obstacle_clearance"
        | "schedule_integrity"
        | "vertical_profile";
      status: "passed" | "failed" | "not_applicable";
      measured_value: number | null;
      required_value: number | null;
      unit: string | null;
      message: string;
      affected_ids: string[];
    }>;
    assumptions: string[];
  } | null;
}

export interface PlanRequest {
  criterion: OptimizationCriterion;
  drones: Array<{
    drone_id: string;
    name: string;
    cruise_speed_kmh: number;
    maximum_flight_minutes: number;
    initial_charge_percent: number;
    battery_health_percent: number;
    energy_consumption_multiplier: number;
    battery_reserve_percent: number;
    maximum_wind_mps: number;
    supported_tasks: MissionTask[];
    autopilot_profile: AutopilotProfile;
    maintenance_interval_flights: number | null;
    maintenance_interval_hours: number | null;
    completed_flights_since_maintenance: number;
    flight_hours_since_maintenance: number;
    maximum_climb_rate_mps: number | null;
    maximum_descent_rate_mps: number | null;
    minimum_mission_altitude_m: number | null;
    maximum_mission_altitude_m: number | null;
    minimum_mission_altitude_agl_m: number | null;
    maximum_mission_altitude_agl_m: number | null;
    minimum_mission_altitude_msl_m: number | null;
    maximum_mission_altitude_msl_m: number | null;
    minimum_cruise_speed_kmh: number | null;
    maximum_cruise_speed_kmh: number | null;
    maximum_route_distance_km: number | null;
    minimum_operating_temperature_c: number | null;
    maximum_operating_temperature_c: number | null;
    maximum_control_range_km: number | null;
    minimum_turnaround_minutes: number;
    payloads: PayloadCapability[];
  }>;
  segments: WorkSegment[];
  coverage_areas: Array<{
    area_id: string;
    name: string;
    coordinates: Coordinate[];
    interior_rings: Coordinate[][];
  }>;
  survey_parameters: {
    direction_mode: SweepDirectionMode;
    flight_direction_degrees: number;
    side_overlap_percent: number;
    forward_overlap_percent: number;
    maximum_gsd_cm: number;
    strip_width_m: number;
    maximum_segment_distance_km: number;
    sensor: {
      sensor_id: string;
      name: string;
      sensor_width_mm: number;
      sensor_height_mm: number;
      focal_length_mm: number;
      image_width_px: number;
      image_height_px: number;
      minimum_trigger_interval_s: number;
    };
  };
  no_fly_zones: Array<{
    zone_id: string;
    coordinates: Coordinate[];
    interior_rings: Coordinate[][];
    safety_margin_m: number;
    minimum_altitude_m: number | null;
    maximum_altitude_m: number | null;
    altitude_reference: "msl" | "agl";
    validity: ImportedZone["validity"];
  }>;
  obstacles: Array<{
    obstacle_id: string;
    geometry_type: GeometryType;
    coordinates: Coordinate[];
    height_m: number;
    altitude_mode: string;
    safety_radius_m: number;
  }>;
  mission_altitude_m: number;
  altitude_reference?: "msl" | "agl";
  terrain_elevation_samples?: Array<{
    coordinate: Coordinate;
    elevation_m: number;
  }>;
  terrain_source?:
    | {
        source_type: "inline";
        samples: Array<{ coordinate: Coordinate; elevation_m: number }>;
      }
    | { source_type: "geotiff"; content: string; band?: number };
  terrain_vertical_uncertainty_m?: number;
  terrain_sampling_interval_m?: number;
  terrain_max_interpolation_distance_m?: number;
  task: MissionTask;
  departure_sites: ApiFlightSite[];
  landing_sites: ApiFlightSite[];
  alternate_landing_sites: ApiFlightSite[];
  wind: { speed_mps: number; direction_degrees: number };
  ambient_temperature_c: number;
  allowed_airspaces: Array<{
    airspace_id: string;
    coordinates: Coordinate[];
    minimum_altitude_m: number;
    maximum_altitude_m: number;
    altitude_reference: "msl" | "agl";
    validity: ImportedZone["validity"];
  }>;
  maximum_active_drones: number | null;
  completion_deadline_minutes: number | null;
  minimum_separation_m: number;
  minimum_vertical_separation_m?: number;
  vertical_deconfliction_step_m?: number;
  maximum_vertical_deconfliction_m?: number;
  position_uncertainty_m: number;
  start_time_uncertainty_seconds: number;
  speed_uncertainty_percent: number;
  flight_site_occupancy_seconds: number;
  turnaround_minutes: number;
  planning_start_time: string | null;
  planning_end_time: string | null;
  minimum_turn_radius_m: number;
  optimization_mode: "optimized" | "fast_heuristic";
}

export interface TurnRadiusAnalysisRequest {
  coverage: {
    area: PlanRequest["coverage_areas"][number];
    mission_altitude_m: number;
    parameters: PlanRequest["survey_parameters"];
    exclusions: PlanRequest["no_fly_zones"];
  };
  allowed_airspaces: PlanRequest["allowed_airspaces"];
  radii_m: number[];
}

export interface TurnRadiusAnalysisResult {
  area_id: string;
  scenarios: Array<{
    radius_m: number;
    status: "feasible" | "no_route_for_selected_sweeps" | "not_evaluated";
    message: string;
    distance_km: number | null;
    uncovered_area_m2: number | null;
  }>;
  common_survey_route: Coordinate[] | null;
  message: string;
}

export interface ApiFlightSite {
  site_id: string;
  name: string;
  coordinate: Coordinate;
  assigned_drone_ids: string[];
}

export interface ImportResponse {
  source_file_name: string;
  source_format: "geojson" | "kml";
  zones: ImportedZone[];
}

export interface ExportTextResponse {
  file_name: string;
  content: string;
}
