"""Cooperative, request-local compute limit; an incomplete plan is never returned."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import monotonic

from geoplan.domain.enums import ErrorCode
from geoplan.errors import PlanningError

_deadline: ContextVar[float | None] = ContextVar("planning_deadline", default=None)
_request_seconds: ContextVar[float | None] = ContextVar("planning_seconds", default=None)
_cancelled: ContextVar[Callable[[], bool] | None] = ContextVar("planning_cancelled", default=None)
PLANNING_SECONDS = 270


class PlanningTimeExceeded(PlanningError):
    def __init__(self) -> None:
        super().__init__(
            ErrorCode.PLANNING_TIME_LIMIT,
            "Расчёт превысил допустимое время. Разделите задание на меньшие пакеты "
            "или включите быстрый расчёт. Непроверенный план не возвращается.",
        )


class PlanningCancelled(PlanningError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.PLANNING_CANCELLED, "Поиск остановлен пользователем.")


@contextmanager
def planning_cancellation(cancelled: Callable[[], bool]) -> Iterator[None]:
    token = _cancelled.set(cancelled)
    try:
        yield
    finally:
        _cancelled.reset(token)


@contextmanager
def planning_budget(seconds: float = PLANNING_SECONDS) -> Iterator[None]:
    previous = _deadline.get()
    deadline = monotonic() + seconds
    token = _deadline.set(min(previous, deadline) if previous is not None else deadline)
    size_token = _request_seconds.set(seconds) if _request_seconds.get() is None else None
    try:
        check_planning_budget()
        yield
    finally:
        _deadline.reset(token)
        if size_token is not None:
            _request_seconds.reset(size_token)


def check_planning_budget() -> None:
    cancelled = _cancelled.get()
    if cancelled is not None and cancelled():
        raise PlanningCancelled()
    deadline = _deadline.get()
    if deadline is not None and monotonic() >= deadline:
        raise PlanningTimeExceeded()


def improvement_allowed(reserve_seconds: float = 60) -> bool:
    cancelled = _cancelled.get()
    if cancelled is not None and cancelled():
        raise PlanningCancelled()
    deadline = _deadline.get()
    size = _request_seconds.get()
    if size is not None:
        reserve_seconds = min(reserve_seconds, size * 0.2)
    return deadline is None or deadline - monotonic() > reserve_seconds


def bounded_planning[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        request = args[0] if args else kwargs.get("request")
        with planning_budget(getattr(request, "planning_time_limit_seconds", PLANNING_SECONDS)):
            try:
                return function(*args, **kwargs)
            except PlanningError:
                check_planning_budget()
                raise

    return run
