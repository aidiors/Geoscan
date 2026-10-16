# Чек-лист передачи GeoPlan

## Автоматические проверки

- `npm ci`
- `npm run typecheck`
- `npm run lint`
- `npm run check:architecture`
- `npm test`
- `npm run build`
- `npm run test:e2e`
- из `backend/`: `uv sync --locked`, `uv run ruff format --check .`,
  `uv run ruff check .`, `uv run ty check src tests benchmarks scenarios`, `uv run pytest -q`
- из корня: `python scripts/check_architecture.py`
- из корня: `python scripts/check_docs.py`
- `docker compose up --build` и проверка `http://127.0.0.1:8000/health`

GitHub Actions выполняет проверки кода, тесты, E2E и сборку контейнера с
проверкой health endpoint и выдачи веб-интерфейса.

## Ручная приёмка перед отправкой

1. Открыть «Демо-кейсы» и выполнить «Безопасную городскую съёмку».
2. Убедиться, что карта показывает маршрут, метрики заполнены, а независимый аудит пройден.
3. Выполнить «Группу БВС над кварталами» и проверить распределение миссий.
4. Выполнить «Управляемый отказ: шторм» и проверить понятное сообщение вместо опасного плана.
5. Скачать KML, GeoJSON, QGroundControl `.plan` и Mission Planner `.waypoints`.
6. Импортировать задания в целевые версии внешнего ПО, если они доступны на машине приёмки.

## Состав передачи

- исходный код и `uv.lock`/`package-lock.json`;
- `Dockerfile`, `compose.yaml` и `.dockerignore`;
- документация в `docs/`;
- исходные материалы в `reference/` через Git LFS;
- README с локальным и контейнерным запуском.

`dist/` создаётся командой `npm run build` и не хранится в Git. Для передачи
готовой версии используйте Docker-образ либо отдельный архив сборки.
Результаты бенчмарков генерируются отдельно по [инструкции](benchmarks.md).

После свежего клонирования выполнить `git lfs pull`, чтобы получить большие
исходные материалы и шаблон презентации.
