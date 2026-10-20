import type { AdvisorResponse, SurveyQualityReport } from "@/shared/api/types";
import { useState } from "react";

interface Props {
  quality: SurveyQualityReport | null;
  advice: AdvisorResponse | null;
  busy: boolean;
  canInspect: boolean;
  onInspect: () => void;
  onAdvise: (allowAltitudeChange: boolean) => void;
  onApply: (index: number) => void;
}

const qualityLabels = { checked: "Проверено в рамках модели", issues: "Есть замечания", partial: "Частичная проверка", not_evaluated: "Проверка не завершена" };
const percent = (value: number | null) => value === null ? "не проверено" : `${value.toFixed(1)}%`;

export function MissionInsights({ quality, advice, busy, canInspect, onInspect, onAdvise, onApply }: Props) {
  const [allowAltitudeChange, setAllowAltitudeChange] = useState(false);
  return <section className="insights-panel" id="mission-quality" aria-label="Качество и выполнимость" tabIndex={-1}>
    <h3>Качество съёмки и варианты выполнения</h3>
    {!quality && <p className="hint">Аудит маршрута и качество съёмки проверяются отдельно. Здесь оцениваются отпечатки кадров, GSD и перекрытие, фактические снимки не загружены.</p>}
    <div className="field-grid">
      <button type="button" disabled={busy || !canInspect} onClick={onInspect}>{busy ? "Проверяем…" : "Проверить отпечатки кадров"}</button>
      <button type="button" disabled={busy} onClick={() => onAdvise(allowAltitudeChange)}>Подобрать условия выполнения</button>
    </div>
    <label className="checkbox-row"><input type="checkbox" checked={allowAltitudeChange} onChange={(event) => setAllowAltitudeChange(event.target.checked)} /><span>Разрешить проверить другую высоту, требования к качеству сохраняются</span></label>
    {quality && <div className="editor-card">
      <b>{qualityLabels[quality.status]}</b>
      {quality.status === "partial" && <p className="quality-caution">Отсутствие замечаний не означает полную проверку: прочитайте допущения ниже.</p>}
      <p>Кадров: {quality.frame_count} · покрытие {percent(quality.covered_percent)} · GSD {quality.maximum_gsd_cm?.toFixed(2) ?? "-"} см/пикс.</p>
      <p>Минимальное продольное: {percent(quality.minimum_forward_overlap_percent)} · боковое: {percent(quality.minimum_side_overlap_percent)}</p>
      <p>Пар проходов между вылетами: {quality.cross_sortie_pairs_checked}</p>
      {quality.issues.map((issue) => <p key={issue} role="alert">{issue}</p>)}
      <details><summary>Границы проверки</summary>{quality.assumptions.map((assumption) => <p className="hint" key={assumption}>{assumption}</p>)}</details>
    </div>}
    {advice && <div className="editor-card">
      <p className="hint">{advice.message}</p>
      {advice.baseline_error && <p>{advice.baseline_error}</p>}
      {advice.variants.length === 0 && <p>В выбранном наборе условий дополнительных вариантов нет.</p>}
      {advice.variants.map((variant, index) => <div className="algorithm-card" key={`${variant.label}-${index}`}>
        <span><b>{variant.label}</b><small>{variant.explanation}</small>
          {variant.plan && <small>Завершение: {variant.plan.metrics.completion_minutes.toFixed(1)} мин · налёт: {variant.plan.metrics.total_flight_minutes.toFixed(1)} мин · БВС: {variant.plan.metrics.active_drone_count}</small>}
          {variant.status === "verified" && <button type="button" onClick={() => onApply(index)}>Применить проверенный вариант</button>}
        </span>
      </div>)}
    </div>}
  </section>;
}
