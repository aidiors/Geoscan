import { missionWorkflow } from "@/features/planner/model/missionWorkflow";
import type { MissionWorkflowState } from "@/features/planner/model/missionWorkflow";
import { useState } from "react";

interface Props extends MissionWorkflowState {
  busy: boolean;
  onInspect: () => void;
  onCalculate: () => void;
}

export function MissionWorkflow(props: Props) {
  const state = missionWorkflow(props);
  const [selection, setSelection] = useState<{ index: number; basis: number } | null>(null);
  const selected = selection?.basis === state.step ? selection.index : state.step;
  const links = ["mission-inputs", "mission-results", "mission-quality", "mission-exports"];
  const descriptions = [
    "Задание: области, физические борта, камеры, площадки и требования к съёмке.",
    "Расчёт: итоговые показатели, история улучшений и независимый аудит плана.",
    "Качество: отпечатки кадров, GSD, перекрытие и границы проверки.",
    "Экспорт: индивидуальные задания для выбранного БВС и его вылетов.",
  ];
  function navigate(index: number) {
    setSelection({ index, basis: state.step });
    const target = document.getElementById(links[index]);
    if (!target) return;
    if (target.matches("aside.panel")) target.scrollTo({ top: 0 });
    target.scrollIntoView({ block: "start" });
    target.focus({ preventScroll: true });
  }
  const actions = {
    inputs: ["Настроить задание", "mission-inputs"], results: ["Проверить условия", "mission-results"],
    quality: ["К проверке качества", "mission-quality"], export: ["К экспорту", "mission-exports"],
  } as const;
  const link = state.action in actions ? actions[state.action as keyof typeof actions] : null;
  return <nav className="mission-workflow" aria-label="Этапы подготовки задания">
    <ol>{["Задание", "Расчёт", "Качество", "Экспорт"].map((label, index) => <li key={label} className={index === selected ? "current" : index < state.step ? "done" : ""}>
      <a href={`#${links[index]}`} onClick={(event) => { event.preventDefault(); navigate(index); }} aria-current={index === selected ? "step" : undefined}><span>{index + 1}</span>{" "}{label}</a>
    </li>)}</ol>
    <p>{selected === state.step ? state.message : descriptions[selected]}</p>
    {state.action === "inspect" && <button type="button" disabled={props.busy} onClick={props.onInspect}>{props.busy ? "Проверяем качество…" : "Проверить качество"}</button>}
    {state.action === "calculate" && <button type="button" disabled={props.busy} onClick={props.onCalculate}>Пересчитать план</button>}
    {link && <a className="workflow-action" href={`#${link[1]}`} onClick={(event) => { event.preventDefault(); navigate(links.indexOf(link[1])); }}>{link[0]} →</a>}
  </nav>;
}
