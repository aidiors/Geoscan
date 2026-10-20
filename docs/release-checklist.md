# Проверка перед выпуском

## Автоматические проверки

Из корня репозитория:

```powershell
npm ci
npm run typecheck
npm run lint
npm run check:architecture
npm test
npm run build
uv sync --directory backend --locked --extra optimization
uv run --directory backend --locked --extra optimization ruff format --check src tests benchmarks scenarios ../scripts
uv run --directory backend --locked --extra optimization ruff check src tests benchmarks scenarios ../scripts
uv run --directory backend --locked --extra optimization ty check src tests benchmarks scenarios ../scripts
uv run --directory backend --locked --extra optimization python ../scripts/check_architecture.py
uv run --directory backend --locked --extra optimization python ../scripts/check_docs.py
uv run --directory backend --locked --extra optimization pytest -q
```

Для E2E предварительно установите браузер: `npx playwright install chrome`,
затем `npm run test:e2e`. Если политика машины запрещает отладочные подключения
Chrome, запуск не считается пройденным; E2E также настроены в CI.

Проверьте сборку Docker, `/health`, интерфейс и сохранение проектов после
перезапуска. Локальный порт задаётся `GEOPLAN_PORT`; данные сервера хранятся
в volume `geoplan-data`. Подробности запуска — в [README](../README.md).

## Ручная приёмка

1. Проверить демо успешного расчёта и отказ при превышении ветра.
2. Импортировать несколько полигонов, включая отверстия, выбрать подмножество;
   убедиться, что демонстрационные маршруты не подмешиваются.
3. Проверить конечный парк, готовые АКБ, ТО и соответствие нагрузки заданию.
4. Проверить навигацию этапов, секундомер, историю улучшений, остановку поиска
   и сохранение проверенного плана.
5. Проверить совместные вылеты, расписание, площадки и независимый аудит.
6. Загрузить DEM, проверить AGL-профиль и восстановление рельефа из проекта.
7. Сохранить проект и ревизию, скачать архив и открыть на другой машине.
8. Пересчитать архивный план; скачать отдельные вылеты и ZIP-пакет.
   Проверить истёкшее/изменённое подтверждение и недоступный автопилотный экспорт
   самолётного профиля. GIS-экспорт проверяется отдельно.
9. Импортировать поддерживаемые задания в фактические версии внешнего ПО,
   проверить высоты, скорости, камеру и последовательность вылетов.

Готовность к эксплуатации требует подтверждённых исходных данных и приёмки
реальных пакетов; модельные ограничения перечислены в [оптимизации](optimization.md).
Команды сравнений — в [бенчмарках](benchmarks.md), результаты и открытые
замечания — в [отчёте приёмки](acceptance.md).

## Передача

В репозитории должны оставаться исходники, lock-файлы, Docker-конфигурация,
актуальные инструкции, исходные материалы и замороженные контрольные входы.
После клонирования выполните `git lfs pull` для материалов Git LFS.
`dist/`, зависимости и результаты новых запусков генерируются локально.
Резервные копии операторских проектов и постоянного volume переносятся отдельно.
