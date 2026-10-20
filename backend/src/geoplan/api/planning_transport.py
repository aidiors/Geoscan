"""Bounded planning execution and cancellable NDJSON delivery of audited incumbents."""

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from queue import Empty, Full, Queue
from threading import BoundedSemaphore, Event, Thread

from fastapi.responses import JSONResponse, StreamingResponse
from starlette.types import Receive, Scope, Send

from geoplan.api.models import ErrorResponse
from geoplan.budget import planning_cancellation
from geoplan.domain.enums import ErrorCode
from geoplan.errors import PlanningError
from geoplan.geodata.release import PlanReleaseAuthority
from geoplan.planning.models import PlanRequest, PlanResponse
from geoplan.planning.service import AuditedPlanningService

logger = logging.getLogger(__name__)


class CancellablePlanningResponse(StreamingResponse):
    def __init__(self, content: Iterator[str], cancelled: Event) -> None:
        super().__init__(
            content,
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
        self.cancelled = cancelled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.cancelled.set()


@dataclass
class PlanningTransport:
    service: AuditedPlanningService
    maximum_concurrent: int = 2
    release_authority: PlanReleaseAuthority = field(default_factory=PlanReleaseAuthority)
    _slots: BoundedSemaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._slots = BoundedSemaphore(self.maximum_concurrent)

    def plan(self, request: PlanRequest) -> PlanResponse:
        if not self._slots.acquire(blocking=False):
            raise PlanningError(
                ErrorCode.PLANNING_BUSY, "Сервис занят расчётами. Повторите запрос позже."
            )
        try:
            return self.release_authority.issue(self.service.plan(request))
        finally:
            self._slots.release()

    def stream(self, request: PlanRequest) -> StreamingResponse | JSONResponse:
        if not self._slots.acquire(blocking=False):
            error = ErrorResponse(
                code=ErrorCode.PLANNING_BUSY,
                message="Сервис занят расчётами. Повторите запрос позже.",
            )
            return JSONResponse(status_code=503, content=error.model_dump(mode="json"))
        cancelled = Event()
        try:
            events = self._events(request, cancelled)
        except Exception:
            self._slots.release()
            raise
        return CancellablePlanningResponse(events, cancelled)

    def _events(self, request: PlanRequest, cancelled: Event) -> Iterator[str]:
        queue: Queue[dict] = Queue(maxsize=2)

        def publish(event: dict) -> None:
            # Latest incumbents replace queued stale ones; a slow reader cannot
            # hold up search or accumulate an unbounded collection of full plans.
            if cancelled.is_set():
                return
            while True:
                try:
                    queue.put_nowait(event)
                    return
                except Full:
                    try:
                        queue.get_nowait()
                    except Empty:
                        pass

        def run() -> None:
            try:
                with planning_cancellation(cancelled.is_set):
                    result = self.service.plan(
                        request,
                        lambda stage, plan: publish(
                            {
                                "kind": "candidate",
                                "stage": stage,
                                "plan": self.release_authority.issue(plan).model_dump(mode="json"),
                            }
                        ),
                    )
                publish(
                    {
                        "kind": "completed",
                        "plan": self.release_authority.issue(result).model_dump(mode="json"),
                    }
                )
            except PlanningError as error:
                payload = ErrorResponse(
                    code=error.code, message=error.message, recommendations=error.recommendations
                )
                publish({"kind": "error", "error": payload.model_dump(mode="json")})
            except Exception:
                logger.exception("Progressive planning failed")
                publish(
                    {
                        "kind": "error",
                        "error": {
                            "code": ErrorCode.PLANNING_FAILED,
                            "message": ("Расчёт завершился ошибкой."),
                        },
                    }
                )
            finally:
                self._slots.release()

        worker = Thread(target=run, name="geoplan-search", daemon=True)
        worker.start()

        def read() -> Iterator[str]:
            try:
                yield json.dumps({"kind": "started"}) + "\n"
                while not cancelled.is_set():
                    try:
                        event = queue.get(timeout=1)
                    except Empty:
                        yield json.dumps({"kind": "heartbeat"}) + "\n"
                        continue
                    yield json.dumps(event, ensure_ascii=False) + "\n"
                    if event["kind"] in ("completed", "error"):
                        break
            finally:
                cancelled.set()

        return read()
