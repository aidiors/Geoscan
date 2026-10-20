import { ApiError } from "@/shared/api/http";
import type { PlanRequest, PlanResponse } from "@/shared/api/types";

interface PlanningEvent {
  kind: "started" | "heartbeat" | "candidate" | "completed" | "error";
  plan?: PlanResponse;
  error?: { code?: string; message?: string; recommendations?: Array<{ message: string }> };
}

export async function readPlanningStream(
  response: Response, onPlan: (plan: PlanResponse) => void,
): Promise<PlanResponse> {
  if (!response.ok) {
    let error: PlanningEvent["error"];
    try { error = await response.json() as PlanningEvent["error"]; }
    catch { throw new ApiError(`Сервис вернул ошибку ${response.status}.`); }
    throw new ApiError(error?.message ?? "Расчёт недоступен.", error?.code ?? null);
  }
  if (response.headers.get("Content-Type")?.includes("application/json")) {
    return await response.json() as PlanResponse;
  }
  if (!response.body) throw new ApiError("Сервер не передал результаты расчёта.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = "";
  let completed: PlanResponse | undefined;
  let delivered = false;
  function consume(line: string): void {
    if (!line.trim()) return;
    let event: PlanningEvent;
    try { event = JSON.parse(line) as PlanningEvent; }
    catch { throw new ApiError("Сервер передал некорректное сообщение поиска."); }
    if (event.kind === "error") throw new ApiError(event.error?.message ?? "Ошибка расчёта.",
      event.error?.code ?? null, event.error?.recommendations?.map((item) => item.message) ?? []);
    if (event.kind === "candidate") {
      if (!event.plan || event.plan.safety_audit?.status !== "passed") throw new ApiError("Промежуточный план не прошёл аудит.");
      onPlan(event.plan);
      delivered = true;
    }
    if (event.kind === "completed") {
      if (!event.plan) throw new ApiError("Отсутствует итоговый план.");
      completed = event.plan;
    }
  }
  try {
    while (true) {
      const { value, done } = await reader.read();
      pending += decoder.decode(value, { stream: !done });
      const lines = pending.split("\n");
      pending = lines.pop() ?? "";
      for (const line of lines) consume(line);
      if (done) break;
    }
    consume(pending);
    if (!completed) throw new ApiError(delivered
      ? "Связь прервана, последний проверенный план сохранён."
      : "Связь прервана до получения проверенного плана.");
    return completed;
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export async function requestProgressivePlan(request: PlanRequest,
  onPlan: (plan: PlanResponse) => void, signal: AbortSignal): Promise<PlanResponse> {
  const response = await fetch("/v1/plans", {
    method: "POST", headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" },
    body: JSON.stringify(request), signal,
  });
  return readPlanningStream(response, onPlan);
}
