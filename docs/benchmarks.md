# Воспроизведение вычислительных экспериментов

Бенчмарки находятся в `backend/benchmarks/`, общие фабрики геометрии —
в `backend/scenarios/`. Они используют рабочий пакет `geoplan` и не входят
в серверный контейнер. Небольшие проверки их корректности находятся
в `backend/tests/benchmarks/` и выполняются обычным `pytest`.

## Запуск

Из каталога `backend/` после `uv sync --locked`:

```powershell
uv run python -m benchmarks.compare_optimizers
uv run python -m benchmarks.compare_coverage_planners
uv run python -m benchmarks.compare_corridor_planners
uv run python -m benchmarks.run_small_radius_geometry
uv run python -m benchmarks.run_coverage_experiments
```

Последняя команда сохраняет CSV и JSON в `docs/benchmark-results/coverage-*`.
Это локальные артефакты измерений; каталог исключён из Git. Для сравнения
серий фиксируйте коммит, параметры сценария, версии зависимостей и оборудование.
Время выполнения нужно измерять на целевой машине.

## Интерпретация

- Точный решатель служит эталоном распределения для небольших задач.
- Оптимальное распределение по построенным вариантам не доказывает глобальную
  оптимальность геометрии и расписания.
- Синтетические сценарии проверяют поведение модели и не заменяют полётные испытания.
- Структурная проверка экспорта дополняется импортом в применяемую версию
  QGroundControl, Mission Planner или другого целевого ПО.
- Пределы физической модели описаны в [допущениях о манёвренности](maneuverability-assumptions.md),
  семантика высот и времени — в [контракте геоданных](4d-geodata-contract.md).
