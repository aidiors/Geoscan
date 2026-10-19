import { missionWorkflow } from "@/features/planner/model/missionWorkflow";
import type { MissionWorkflowState } from "@/features/planner/model/missionWorkflow";

interface Props extends MissionWorkflowState {
  busy: boolean;
  onInspect: () => void;
  onCalculate: () => void;
}

export function MissionWorkflow(props: Props) {
  const state = missionWorkflow(props);
  const links = ["mission-inputs", "mission-results", "mission-quality", "mission-exports"];
  const actions = {
    inputs: ["Настроить задание", "mission-inputs"], results: ["Проверить условия", "mission-results"],
    quality: ["К проверке качества", "mission-quality"], export: ["К экспорту", "mission-exports"],
  } as const;
  const link = state.action in actions ? actions[state.action as keyof typeof actions] : null;
  return <nav className="mission-workflow" aria-label="Этапы подготовки задания">
    <ol>{["Задание", "Расчёт", "Качество", "Экспорт"].map((label, index) => <li key={label} className={index < state.step ? "done" : index === state.step ? "current" : ""}>
      <a href={`#${links[index]}`} aria-current={index === state.step ? "step" : undefined}><span>{index + 1}</span>{" "}{label}</a>
    </li>)}</ol>
    <p>{state.message}</p>
    {state.action === "inspect" && <button type="button" disabled={props.busy} onClick={props.onInspect}>{props.busy ? "Проверяем качество…" : "Проверить качество"}</button>}
    {state.action === "calculate" && <button type="button" disabled={props.busy} onClick={props.onCalculate}>Пересчитать план</button>}
    {link && <a className="workflow-action" href={`#${link[1]}`}>{link[0]} →</a>}
  </nav>;
}
