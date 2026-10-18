"""Cooperative, request-local compute limit; an incomplete plan is never returned."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import monotonic

from geoplan.domain.enums import ErrorCode
from geoplan.errors import PlanningError

_deadline: ContextVar[float | None] = ContextVar("planning_deadline", default=None)
PLANNING_SECONDS = 270


class PlanningTimeExceeded(PlanningError):
    def __init__(self) -> None:
        super().__init__(
            ErrorCode.PLANNING_TIME_LIMIT,
            "Расчёт превысил допустимое время. Разделите задание на меньшие пакеты "
            "или включите быстрый расчёт. Непроверенный план не возвращается.",
        )


@contextmanager
def planning_budget(seconds: float = PLANNING_SECONDS) -> Iterator[None]:
    previous = _deadline.get()
    deadline = monotonic() + seconds
    token = _deadline.set(min(previous, deadline) if previous is not None else deadline)
    try:
        check_planning_budget()
        yield
    finally:
        _deadline.reset(token)


def check_planning_budget() -> None:
    deadline = _deadline.get()
    if deadline is not None and monotonic() >= deadline:
        raise PlanningTimeExceeded()


def improvement_allowed(reserve_seconds: float = 60) -> bool:
    deadline = _deadline.get()
    return deadline is None or deadline - monotonic() > reserve_seconds


def bounded_planning[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        with planning_budget():
            try:
                return function(*args, **kwargs)
            except PlanningError:
                check_planning_budget()
                raise

    return run
