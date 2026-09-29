import type { DemoCase } from "../types";

export const demoCases: DemoCase[] = [
  {
    id: "safe-urban-survey",
    scenarioId: "urban-rgb",
    title: "Безопасная городская съёмка",
    category: "Маршрутизация",
    description: "Базовый рассказ о продукте: покрытие квартала, обход запретного объекта и независимая проверка результата.",
    highlights: ["RGB и автоматические галсы", "Обход запретной зоны", "Экспорт в четыре формата"],
    expectedEvidence: ["Маршрут не входит в запрет", "Аудит безопасности пройден", "Сохранён обязательный резерв АКБ"],
    recommendedObjective: "time",
  },
  {
    id: "wind-aware-agro",
    scenarioId: "agro-multispectral",
    title: "Агромониторинг при ветре",
    category: "Полётная модель",
    description: "Показывает подбор направления галсов по времени полного маршрута и учёт ветра по каждому отрезку.",
    highlights: ["Мультиспектральная съёмка", "Ветер 9 м/с", "Автоматическое направление"],
    expectedEvidence: ["Выбран ветрооптимальный угол", "Посчитан полный налёт", "Проверен остаток АКБ"],
    recommendedObjective: "flight",
    settings: { windSpeed: 9, windDirection: 270 },
  },
  {
    id: "thermal-linear-inspection",
    scenarioId: "thermal-inspection",
    title: "Тепловизионная инспекция",
    category: "Разные типы работ",
    description: "Демонстрирует выбор совместимой полезной нагрузки и разные площадки старта и посадки.",
    highlights: ["Тепловизор", "Линейная инфраструктура", "Раздельные старт и посадка"],
    expectedEvidence: ["Выбран совместимый БВС", "Учтён транзит", "Сформированы команды миссии"],
    recommendedObjective: "flight",
  },
  {
    id: "lidar-long-corridor",
    scenarioId: "lidar-corridor",
    title: "Протяжённый LiDAR-коридор",
    category: "Специализированная съёмка",
    description: "Проверяет протяжённую геометрию, фильтрацию парка по нагрузке и безопасный обход аэродромного ограничения.",
    highlights: ["LiDAR-профиль", "Длинный коридор", "Ограничение аэродрома"],
    expectedEvidence: ["Отфильтрован неподходящий парк", "Построен непрерывный маршрут", "Проверены ограничения"],
    recommendedObjective: "time",
  },
  {
    id: "multi-drone-city",
    scenarioId: "multi-urban-districts",
    title: "Группа БВС над кварталами",
    category: "Групповая оптимизация",
    description: "Три рабочих района распределяются по парку, после чего scheduler устраняет пространственно-временные конфликты.",
    highlights: ["Три рабочие зоны", "Несколько БВС", "Робастное расписание"],
    expectedEvidence: ["Работы распределены по флоту", "Конфликты разведены", "Показан срок всего проекта"],
    recommendedObjective: "time",
    settings: { positionUncertainty: 5, startTimeUncertainty: 10, speedUncertainty: 5 },
  },
  {
    id: "minimum-flight-fields",
    scenarioId: "multi-agro-fields",
    title: "Минимальный налёт по агрополям",
    category: "Целевая функция",
    description: "Четыре раздельных поля и две базы позволяют наглядно защитить критерий минимального суммарного налёта.",
    highlights: ["Четыре поля", "Две полевые базы", "Минимизация налёта"],
    expectedEvidence: ["Снижены транзитные перелёты", "Выбраны подходящие базы", "Показан полезный процент маршрута"],
    recommendedObjective: "flight",
  },
  {
    id: "complex-restrictions",
    scenarioId: "complex-restrictions",
    title: "Сложные пересекающиеся запреты",
    category: "Стресс-сценарий",
    description: "Нагруженный кейс с тремя участками и пятью ограничениями для финальной демонстрации маршрутизации и аудита.",
    highlights: ["Три LiDAR-зоны", "Пять ограничений", "Пересекающиеся контуры"],
    expectedEvidence: ["Все зоны обработаны", "Каждый отрезок проверен", "Экспорт совпадает с планом"],
    recommendedObjective: "balanced",
  },
  {
    id: "infeasible-weather",
    scenarioId: "urban-rgb",
    title: "Управляемый отказ: шторм",
    category: "Диагностика",
    description: "Заведомо недопустимый ветер показывает, что система не выпускает опасный маршрут и объясняет оператору причину.",
    highlights: ["Ветер 25 м/с", "Ни один БВС не подходит", "Практическая рекомендация"],
    expectedEvidence: ["План отклонён", "Причина сформулирована явно", "Предложено дождаться меньшего ветра"],
    recommendedObjective: "time",
    settings: { windSpeed: 25, windDirection: 315 },
  },
];

export function demoCaseById(caseId: string): DemoCase | undefined {
  return demoCases.find((demoCase) => demoCase.id === caseId);
}

export function defaultDemoCaseForScenario(scenarioId: string): DemoCase | undefined {
  return demoCases.find((demoCase) => demoCase.scenarioId === scenarioId);
}
