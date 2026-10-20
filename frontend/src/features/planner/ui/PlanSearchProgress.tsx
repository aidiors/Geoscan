import type { PlanResponse } from "@/shared/api/types";

const labels: Record<string, string> = {
  searching: "Поиск улучшений продолжается", completed: "Поиск завершён",
  time_limit: "Поиск ограничен бюджетом времени", cancelled: "Поиск остановлен",
  stage_time_limit: "Поиск альтернатив ограничен по времени",
  retained_after_error: "Сохранён последний проверенный план",
};

export function PlanSearchProgress({ search }: { search: PlanResponse["search"] }) {
  if (!search || search.checkpoints.length === 0) return null;
  return <section className="search-progress" aria-label="История улучшений плана">
    <b>{labels[search.status] ?? search.status}</b>
    <p>Обновление через {search.elapsed_seconds.toFixed(1)} с · бюджет до {search.time_limit_seconds} с</p>
    <p>Запущено стратегий: {search.pipelines_started ?? 1}</p>
    <table><thead><tr><th>Найден за</th><th>Завершение</th><th>Налёт</th><th>Вылеты</th></tr></thead>
      <tbody>{search.checkpoints.map((point, index) => <tr key={`${index}-${point.elapsed_seconds}`}>
        <td>{point.elapsed_seconds.toFixed(1)} с</td><td>{point.completion_minutes.toFixed(1)} мин</td>
        <td>{point.total_flight_minutes.toFixed(1)} мин</td><td>{point.sortie_count}</td>
      </tr>)}</tbody>
    </table>
    <p className="hint">Показаны улучшения, прошедшие аудит по тем же условиям задания. Можно остановить поиск и использовать найденный план.</p>
  </section>;
}
