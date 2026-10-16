from __future__ import annotations

from enum import StrEnum


class OptimizationCriterion(StrEnum):
    BALANCED = "balanced"
    COMPLETION_TIME = "completion_time"
    TOTAL_FLIGHT_TIME = "total_flight_time"


class FeasibilityStatus(StrEnum):
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"


class RecommendationConfidence(StrEnum):
    EXACT = "exact"
    ESTIMATE = "estimate"


class RecommendationCode(StrEnum):
    ADD_ALTERNATE_SITE = "add_alternate_site"
    ADD_CAPABLE_DRONE = "add_capable_drone"
    ADJUST_MISSION_ALTITUDE = "adjust_mission_altitude"
    ASSIGN_FLIGHT_SITE = "assign_flight_site"
    EXTEND_DEADLINE = "extend_deadline"
    EXTEND_ENDURANCE = "extend_endurance"
    EXTEND_OPERATING_WINDOW = "extend_operating_window"
    INCREASE_ACTIVE_DRONE_LIMIT = "increase_active_drone_limit"
    REDUCE_MISSION_SCOPE = "reduce_mission_scope"
    RECHARGE_BATTERY = "recharge_battery"
    RESCHEDULE_OPERATION = "reschedule_operation"
    REVISE_GEOSPATIAL_CONSTRAINTS = "revise_geospatial_constraints"
    WAIT_FOR_LOWER_WIND = "wait_for_lower_wind"


class SafetyAuditStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class SafetyCheckCode(StrEnum):
    AIRSPACE_COMPLIANCE = "airspace_compliance"
    ALTERNATE_RESERVE = "alternate_reserve"
    BATTERY_RESERVE = "battery_reserve"
    COVERAGE_COMPLETENESS = "coverage_completeness"
    DRONE_SEPARATION = "drone_separation"
    EXPORT_CONSISTENCY = "export_consistency"
    FLIGHT_ENVELOPE = "flight_envelope"
    NO_FLY_CLEARANCE = "no_fly_clearance"
    OBSTACLE_CLEARANCE = "obstacle_clearance"
    SCHEDULE_INTEGRITY = "schedule_integrity"
    VERTICAL_PROFILE = "vertical_profile"
    VERTICAL_SEPARATION = "vertical_separation"


class AssignmentAlgorithm(StrEnum):
    EXACT_BRANCH_AND_BOUND = "exact_branch_and_bound"
    FAST_GREEDY = "fast_greedy"
    MULTISTART_LOCAL_SEARCH = "multistart_local_search"
    MINIMUM_COST = "minimum_cost"
    DEADLINE_AWARE_LOCAL_SEARCH = "deadline_aware_local_search"


class ScheduleAlgorithm(StrEnum):
    PRIORITY_DECONFLICTION = "priority_deconfliction"


class ScheduleConflictType(StrEnum):
    ROUTE = "route"
    FLIGHT_SITE = "flight_site"
    EMERGENCY_CORRIDOR = "emergency_corridor"


class CoverageAlgorithm(StrEnum):
    BOUSTROPHEDON_SWEEP = "boustrophedon_sweep"
    ADAPTIVE_DECOMPOSED_SWEEP = "adaptive_decomposed_sweep"


class CoverageAngleCost(StrEnum):
    GEOMETRIC_DISTANCE = "geometric_distance"
    WIND_ADJUSTED_DURATION = "wind_adjusted_duration"


class SweepDirectionMode(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"


class SweepAngleAlgorithm(StrEnum):
    FIXED_INPUT = "fixed_input"
    MINIMUM_ROTATED_RECTANGLE = "minimum_rotated_rectangle"
    MINIMUM_RECTANGLE_REFINED = "minimum_rectangle_refined"
    CONVEX_HULL_EDGES = "convex_hull_edges"
    GRID_30_DEGREES = "grid_30_degrees"
    GRID_15_DEGREES = "grid_15_degrees"
    GRID_10_DEGREES = "grid_10_degrees"
    GRID_5_DEGREES = "grid_5_degrees"
    COARSE_TO_FINE = "coarse_to_fine"
    EXHAUSTIVE_1_DEGREE = "exhaustive_1_degree"
    PARTITIONED_MULTI_ANGLE = "partitioned_multi_angle"
    ADAPTIVE_DECOMPOSITION = "adaptive_decomposition"
    ADAPTIVE_SECTION_DP = "adaptive_section_dp"
    ADAPTIVE_SECTION_DUBINS = "adaptive_section_dubins"
    ADAPTIVE_FULL_DUBINS = "adaptive_full_dubins"
    ADAPTIVE_HYBRID_ASTAR = "adaptive_hybrid_astar"
    ADAPTIVE_TURN_AWARE = "adaptive_turn_aware"
    PRODUCTION_ADAPTIVE = "production_adaptive"
    GEOMETRY_AWARE = "geometry_aware"


class RouteAlgorithm(StrEnum):
    BIDIRECTIONAL_VISIBILITY_GRAPH = "bidirectional_visibility_graph"


class OptimizationScope(StrEnum):
    GENERATED_ROUTE_ASSIGNMENT = "generated_route_assignment"


class OptimalityStatus(StrEnum):
    PROVEN_OPTIMAL = "proven_optimal"
    HEURISTIC = "heuristic"


class OptimizationMode(StrEnum):
    FAST_HEURISTIC = "fast_heuristic"
    OPTIMIZED = "optimized"


class DubinsPathType(StrEnum):
    LEFT_STRAIGHT_LEFT = "LSL"
    RIGHT_STRAIGHT_RIGHT = "RSR"
    LEFT_STRAIGHT_RIGHT = "LSR"
    RIGHT_STRAIGHT_LEFT = "RSL"
    RIGHT_LEFT_RIGHT = "RLR"
    LEFT_RIGHT_LEFT = "LRL"


class MissionTask(StrEnum):
    RGB = "rgb"
    MULTISPECTRAL = "multispectral"
    THERMAL = "thermal"
    LIDAR = "lidar"
    VIDEO = "video"
    GEOPHYSICAL = "geophysical"


class AutopilotFirmware(StrEnum):
    GENERIC_MAVLINK = "generic_mavlink"
    ARDUPILOT = "ardupilot"
    PX4 = "px4"


class AirframeType(StrEnum):
    FIXED_WING = "fixed_wing"
    QUADROTOR = "quadrotor"
    HEXAROTOR = "hexarotor"
    OCTOROTOR = "octorotor"
    VTOL_FIXED_ROTOR = "vtol_fixed_rotor"
    GENERIC_MULTIROTOR = "generic_multirotor"


class AltitudeReference(StrEnum):
    MSL = "msl"
    AGL = "agl"


class ErrorCode(StrEnum):
    ALTERNATE_LANDING_UNREACHABLE = "alternate_landing_unreachable"
    AIRSPACE_WINDOW_UNAVAILABLE = "airspace_window_unavailable"
    COVERAGE_GENERATION_FAILED = "coverage_generation_failed"
    DRONE_NOT_FOUND = "drone_not_found"
    GSD_REQUIREMENT_UNSATISFIED = "gsd_requirement_unsatisfied"
    FLIGHT_ENVELOPE_EXCEEDED = "flight_envelope_exceeded"
    INVALID_IMPORT = "invalid_import"
    INVALID_REQUEST = "invalid_request"
    NO_CAPABLE_DRONE = "no_capable_drone"
    NO_ELIGIBLE_FLIGHT_SITE = "no_eligible_flight_site"
    NO_EXPORTABLE_MISSIONS = "no_exportable_missions"
    OUTSIDE_ALLOWED_AIRSPACE = "outside_allowed_airspace"
    ROUTE_BLOCKED = "route_blocked"
    SCHEDULE_GENERATION_FAILED = "schedule_generation_failed"
    TERRAIN_DATA_UNAVAILABLE = "terrain_data_unavailable"
    TURN_RADIUS_UNSATISFIED = "turn_radius_unsatisfied"
    TEMPERATURE_LIMIT_EXCEEDED = "temperature_limit_exceeded"
    UNPLANNABLE_SEGMENT = "unplannable_segment"
    VERTICAL_PROFILE_UNSATISFIED = "vertical_profile_unsatisfied"
    WIND_LIMIT_EXCEEDED = "wind_limit_exceeded"


class ValidityMode(StrEnum):
    PERMANENT = "permanent"
    TIME_WINDOW = "time_window"


class ZoneType(StrEnum):
    ALLOWED_AIRSPACE = "allowed_airspace"
    WORK_AREA = "work_area"
    NO_FLY_ZONE = "no_fly_zone"
    OBSTACLE = "obstacle"


class ImportedGeometryType(StrEnum):
    POINT = "point"
    POLYGON = "polygon"
    LINE_STRING = "line_string"


class AltitudeMode(StrEnum):
    ABSOLUTE = "absolute"
    CLAMP_TO_GROUND = "clampToGround"
    RELATIVE_TO_GROUND = "relativeToGround"
    RELATIVE_TO_SEA_FLOOR = "relativeToSeaFloor"
    UNKNOWN = "unknown"


class AirspaceRestrictionType(StrEnum):
    PERMANENT = "permanent"
    PROHIBITED = "prohibited"
    SPECIAL = "special"
    TEMPORARY = "temporary"
    UNKNOWN = "unknown"


class ImportFormat(StrEnum):
    GEOJSON = "geojson"
    KML = "kml"


class MissionStageType(StrEnum):
    TAKEOFF = "takeoff"
    TRANSIT = "transit"
    SURVEY = "survey"
    LANDING = "landing"


class TurnRadiusStatus(StrEnum):
    FEASIBLE = "feasible"
    NO_ROUTE_FOR_SELECTED_SWEEPS = "no_route_for_selected_sweeps"
    NOT_EVALUATED = "not_evaluated"


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
