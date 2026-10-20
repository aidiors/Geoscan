# Воспроизведение проверок и экспериментов

Стенд находится в `backend/benchmarks`, фабрики - в `backend/scenarios`.
Он использует рабочий пакет `geoplan`, сервер не импортирует бенчмарки.
Команды ниже выполняются из корня репозитория. `uv --directory backend`
меняет рабочий каталог процесса, поэтому выходные файлы задаются через `../work`.

## Подготовка

```powershell
uv sync --directory backend --locked --extra optimization
npm ci
uv run --directory backend --locked --extra optimization pytest -q
npm test
```

Подробный набор проверок - в [чек-листе](release-checklist.md).
Генерируемые результаты сохраняйте в игнорируемом `work/`, а не в `docs/`.
Для каждого запуска используйте новый файл или каталог, драйверы, требующие
новый результат, не перезаписывают существующие доказательства.

## Реальные полигоны и полный HTTP pipeline

```powershell
uv run --directory backend --locked --extra optimization python -u -m benchmarks.production_portfolio pipeline --count 8 --algorithms bounded bounded_productive bounded_cp_productive adaptive_cp_productive production --criteria completion_time total_flight_time balanced --deadline 120 --exports --output ../work/customer-8.jsonl
```

- `bounded`: ограниченный поиск направления, без продуктивного пула.
- `bounded_productive`: то же покрытие с продуктивной группировкой.
- `bounded_cp_productive`: группировка с дополнительным CP-SAT.
- `adaptive_cp_productive`: Adaptive-покрытие с группировкой и CP-SAT.
- `production`: текущая конфигурация сервиса.

Источник по умолчанию - `reference/hackathon/Границы полетов.kml`.
`scenarios/customer.py` выбирает воспроизводимые локальные пакеты, используя
синтетические парк, площадку и камеру. Объекты меньше 1 м² исключаются именно
из операционной выборки стенда, импорт API сохраняет их. Другие файлы ограничений и ветер не добавляются автоматически.

`--count` задаёт размер пакета, `--anchors` - точки выборки, `--criteria` -
критерии, `--deadline` - срок выполнения полётов в минутах, а не вычислительный
бюджет. `--exports` включает проверку форматов. JSONL сохраняет завершённые
строки при прерывании, а также исходные хеши и параметры. Частичная серия
не считается полной приёмкой. Peak RSS - максимум процесса, не выделение
памяти одного запроса.

Для изолированного сравнения геометрии:

```powershell
uv run --directory backend --locked --extra optimization python -u -m benchmarks.production_portfolio coverage --count 12 --algorithms minimum_rotated_rectangle bounded_portfolio adaptive --output ../work/customer-coverage.jsonl
```

Геометрическая оценка не заменяет расписание, проверку снимков и экспорт.

## Объединение соседних областей

```powershell
uv run --directory backend --locked --extra optimization python -u -m benchmarks.compare_neighbor_areas --output ../work/neighbors.json --budget 60 --repeats 3
```

Сравниваются одинаковые входные условия с включённым и отключённым
межобластным объединением. Дополнительный Adaptive pipeline отключён в обоих
вариантах, чтобы выделить этот эффект. Проверяются аудит, снимки и экспорты.
Флаги `--customer-source`, `--count` и `--anchor` подключают реальную геометрию,
парк и площадка при этом остаются синтетическими.

## Дополнительные стенды

```powershell
uv run --directory backend --locked --extra optimization python -m benchmarks.final_readiness --repeats 3 --output ../work/readiness
uv run --directory backend --locked --extra optimization python -m benchmarks.verify_freeform_pipeline --output ../work/freeform.json
uv run --directory backend --locked --extra optimization python -m benchmarks.compare_ortools --seconds 2 --seeds 17 42 109 --warm-start --output ../work/ortools.json
uv run --directory backend --locked --extra optimization python -m benchmarks.optimization_scale --output ../work/scale.json --criteria completion_time total_flight_time balanced --modes reference portfolio cross
```

`final_readiness --include-scale` добавляет масштабную часть. В OR-Tools
сравнивайте отдельно назначения и фактические расписания: лучший ответ решателя
до разведения может оказаться хуже по итоговому сроку.

Источники геометрии свободного покрытия - `scenarios/freeform.py` и замороженный
набор `reference/benchmarks/coverage-v6-inputs.json`. Новый запуск воспроизводится по фабрикам и замороженным входам.

## Как оценивать результат

1. Зафиксировать ревизию, хеши входов/исходников, параметры камеры, погоды,
   парка, площадок, ограничения и вычислительный бюджет.
2. Проверить допустимость, аудит и качество, отдельно отметить превышение срока.
3. Сравнить полный налёт, camera-off, подлёт/возврат, срок, вылеты и число БВС.
4. Проверить четыре формата и соответствие каждого экспортируемого вылета плану.
5. Повторить измерения при одинаковом бюджете и без конкурирующей нагрузки.
   Различать геометрическую оценку, назначение, расписание и весь HTTP-запрос.

## Масштаб и нагрузка

Для проверки запустите отдельный сервис с собственным хранилищем проектов.

```powershell
uv run --directory backend --locked --extra optimization python -u -m benchmarks.service_acceptance quality --url http://127.0.0.1:8002 --output ../work/acceptance-quality.jsonl --budget 1800 --stream
uv run --directory backend --locked --extra optimization python -u -m benchmarks.service_acceptance load --url http://127.0.0.1:8002 --output ../work/acceptance-load.jsonl --budget 60 --users 2 4 8
```

`quality` проверяет 100 км², конечный парк и пакеты 32/64/128 исходных полигонов.
`--names` выбирает отдельные сценарии, `--counts` задаёт размеры пакетов.
`load` проверяет параллельные запросы и освобождение слотов после отмены.
`--container` добавляет измерение ресурсов указанного тестового контейнера.

В одном процессе сервиса одновременно выполняются два расчёта.
Запрос сверх этого числа получает HTTP 503 и может быть повторён позже.

## Контрольные данные

[reference/benchmarks](../reference/benchmarks/README.md) содержит
входные данные проверок. Для сравнения версий используйте одинаковые
входы, бюджеты и число повторов. Новые результаты сохраняйте в `work/`.
