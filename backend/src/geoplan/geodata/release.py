"""Bind execution exports to an unchanged, audited server result.

This attests calculation provenance, not firmware compatibility or flight approval.
"""

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass, field
from time import time

from geoplan.domain.enums import ErrorCode, FeasibilityStatus, SafetyAuditStatus
from geoplan.errors import PlanningError
from geoplan.planning.models import PlanResponse

_PROCESS_KEY = secrets.token_bytes(32)


@dataclass(frozen=True)
class PlanReleaseAuthority:
    key: bytes = field(default_factory=lambda: _PROCESS_KEY, repr=False)
    lifetime_seconds: int = 28_800

    @classmethod
    def from_environment(cls) -> PlanReleaseAuthority:
        configured = os.environ.get("GEOPLAN_EXPORT_SIGNING_KEY")
        if configured is None:
            return cls()
        if len(configured.encode()) < 32:
            raise ValueError("GEOPLAN_EXPORT_SIGNING_KEY must contain at least 32 bytes.")
        return cls(key=configured.encode())

    def issue(self, plan: PlanResponse) -> PlanResponse:
        token = None
        if self.eligible(plan):
            expires = int(time()) + self.lifetime_seconds
            token = f"{expires}.{self._signature(plan, expires)}"
        return plan.model_copy(update={"export_authorization": token})

    def verify(self, plan: PlanResponse) -> None:
        token = plan.export_authorization or ""
        try:
            expiry, signature = token.split(".", 1)
            expires = int(expiry)
            valid = (
                int(time()) < expires <= int(time()) + self.lifetime_seconds
                and hmac.compare_digest(signature, self._signature(plan, expires))
                and self.eligible(plan)
            )
        except ValueError, OverflowError:
            valid = False
        if not valid:
            raise PlanningError(
                ErrorCode.EXPORT_RELEASE_REQUIRED,
                "Экспорт в автопилот требует неизменённого проверенного плана этого сервера. "
                "План изменён, не прошёл аудит либо подтверждение истекло. Повторите расчёт. "
                "GIS-экспорт остаётся доступным для анализа.",
            )

    @staticmethod
    def eligible(plan: PlanResponse) -> bool:
        audit = plan.safety_audit
        missions = [m for d in plan.drone_plans for m in d.missions]
        return bool(
            missions
            and audit is not None
            and audit.status is SafetyAuditStatus.PASSED
            and audit.audited_mission_count == len(missions)
            and all(c.status is not SafetyAuditStatus.FAILED for c in audit.checks)
            and plan.feasibility is not None
            and plan.feasibility.status is FeasibilityStatus.FEASIBLE
            and all(d.mission_count == len(d.missions) for d in plan.drone_plans)
        )

    def _signature(self, plan: PlanResponse, expires: int) -> str:
        # Search progress is presentation metadata and may change on cancellation.
        payload = json.dumps(
            plan.model_dump(mode="json", exclude={"export_authorization", "search"}),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hmac.new(self.key, f"{expires}:{payload}".encode(), hashlib.sha256).hexdigest()
