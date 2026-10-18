"""Compatibility exports for the assignment benchmark; solver lives in the application."""

from geoplan.assignment.cp_sat import (
    AssignmentEvaluation,
    CpSatAssignmentResult,
    evaluate_assignment,
    objective_score,
)
from geoplan.assignment.cp_sat import (
    CpSatAssignmentSolver as CpSatAssignmentBenchmark,
)

__all__ = [
    "AssignmentEvaluation",
    "CpSatAssignmentBenchmark",
    "CpSatAssignmentResult",
    "evaluate_assignment",
    "objective_score",
]
