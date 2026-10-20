import type { PlanResponse, SurveyQualityReport } from "@/shared/api/types";

export interface MissionWorkflowState {
  plan: PlanResponse | null;
  quality: SurveyQualityReport | null;
  archived: boolean;
  calculating: boolean;
  canInspect: boolean;
}

export function missionWorkflow(state: MissionWorkflowState) {
  const { plan, quality, archived, calculating, canInspect } = state;
  if (archived) return { step: 1, message: "Архив открыт. Пересчитайте план для текущих условий.", action: "calculate" } as const;
  if (calculating) return { step: 1, message: plan ? "Проверенный результат уже на карте. Поиск улучшений продолжается." : "Строим первый проверенный план. Его можно будет сохранить после остановки поиска.", action: "wait" } as const;
  if (!plan) return { step: 0, message: "Выберите рабочие области, парк и требования к съёмке. Затем рассчитайте план.", action: "inputs" } as const;
  if (plan.safety_audit?.status !== "passed" || plan.feasibility?.status !== "feasible") {
    return { step: 1, message: "Условия задания выполнены не полностью. Проверьте причины и рекомендации перед экспортом.", action: "results" } as const;
  }
  if (!quality || quality.status === "not_evaluated") return { step: 2, message: "План прошёл аудит. Отдельно проверьте GSD, перекрытие и покрытие кадрами.", action: canInspect ? "inspect" : "quality" } as const;
  if (quality.status === "issues" || quality.issues.length > 0) return { step: 2, message: "Есть замечания к качеству съёмки. Измените условия и повторите расчёт.", action: "quality" } as const;
  if (quality.status === "partial") return { step: 3, message: "Проверка качества частичная. Изучите допущения перед выгрузкой заданий.", action: "quality" } as const;
  if (!plan.export_authorization) return { step: 3, message: "Качество проверено. Для подтверждения экспорта выполните новый расчёт.", action: "calculate" } as const;
  return { step: 3, message: "Расчётные проверки завершены. Скачайте задания и проверьте их в полётном ПО.", action: "export" } as const;
}
