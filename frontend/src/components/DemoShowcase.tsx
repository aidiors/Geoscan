import { CheckCircle2, Clock3, Play, X } from "lucide-react";

import { demoCases } from "../data/demoCases";
import type { PlanResponse } from "../types";

interface DemoShowcaseProps {
  open: boolean;
  selectedCaseId: string;
  plan: PlanResponse | null;
  calculating: boolean;
  onClose: () => void;
  onLoad: (caseId: string) => void;
  onRun: (caseId: string) => void;
}

export function DemoShowcase({
  open,
  selectedCaseId,
  plan,
  calculating,
  onClose,
  onLoad,
  onRun,
}: DemoShowcaseProps) {
  if (!open) return null;
  const selectedCase = demoCases.find((demoCase) => demoCase.id === selectedCaseId) ?? demoCases[0];
  const applicableChecks = plan?.safety_audit?.checks.filter((check) => check.status !== "not_applicable") ?? [];
  const passedChecks = applicableChecks.filter((check) => check.status === "passed").length;

  return (
    <div className="showcase-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="showcase-dialog" role="dialog" aria-modal="true" aria-labelledby="showcase-title">
        <header className="showcase-header">
          <div>
            <span className="eyebrow">Сценарии для демонстрации</span>
            <h2 id="showcase-title">Витрина возможностей GeoPlan</h2>
            <p>Выберите готовый кейс: входные данные, критерий и ограничения загрузятся воспроизводимо.</p>
          </div>
          <button className="showcase-close" type="button" aria-label="Закрыть витрину" onClick={onClose}><X size={19} /></button>
        </header>

        <div className="showcase-layout">
          <nav className="showcase-grid" aria-label="Демонстрационные кейсы">
            {demoCases.map((demoCase) => (
              <button
                className={`showcase-case ${demoCase.id === selectedCase.id ? "active" : ""}`}
                type="button"
                key={demoCase.id}
                aria-pressed={demoCase.id === selectedCase.id}
                onClick={() => onLoad(demoCase.id)}
              >
                <span>{demoCase.category}</span>
                <b>{demoCase.title}</b>
                <small>{demoCase.highlights.slice(0, 2).join(" · ")}</small>
              </button>
            ))}
          </nav>

          <article className="showcase-details">
            <span className="showcase-category">{selectedCase.category}</span>
            <h3>{selectedCase.title}</h3>
            <p>{selectedCase.description}</p>
            <div className="showcase-columns">
              <div>
                <b>Что показываем</b>
                <ul>{selectedCase.highlights.map((item) => <li key={item}>{item}</li>)}</ul>
              </div>
              <div>
                <b>Что должно быть видно</b>
                <ul>{selectedCase.expectedEvidence.map((item) => <li key={item}>{item}</li>)}</ul>
              </div>
            </div>
            {plan && (
              <div className={`showcase-result ${plan.safety_audit?.status === "failed" ? "failed" : ""}`}>
                <CheckCircle2 size={18} />
                <span>
                  <b>Последний расчёт: {plan.metrics.completion_minutes.toFixed(1)} мин · {plan.metrics.total_distance_km.toFixed(1)} км</b>
                  <small>Пройдено применимых проверок: {passedChecks}/{applicableChecks.length}</small>
                </span>
              </div>
            )}
            <div className="showcase-actions">
              <button className="secondary-button" type="button" onClick={() => { onLoad(selectedCase.id); onClose(); }}>Только загрузить</button>
              <button className="primary-button" type="button" disabled={calculating} onClick={() => onRun(selectedCase.id)}>
                {calculating ? <Clock3 size={16} /> : <Play size={16} fill="currentColor" />}
                {calculating ? "Выполняется расчёт…" : "Загрузить и рассчитать"}
              </button>
            </div>
          </article>
        </div>
      </section>
    </div>
  );
}
