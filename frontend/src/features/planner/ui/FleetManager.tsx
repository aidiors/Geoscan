import { useModalDialog } from "@/shared/ui/useModalDialog";
import { useState } from "react";
import { ChevronRight, Plane, Search, X } from "lucide-react";
import { DroneEditor } from "./DroneEditor";
import { FleetActions } from "./FleetActions";
import { fleetCatalog } from "@/features/planner/model/fleetCatalog";
import type { DroneProfile, SurveySettings } from "@/features/planner/model/types";

interface FleetManagerProps {
  drones: DroneProfile[];
  selectedDroneId: string;
  survey: SurveySettings;
  onSelect: (droneId: string) => void;
  onUpdate: (droneId: string, patch: Partial<DroneProfile>) => void;
  onAdd: (modelId: string, count: number) => void;
  onClone: () => void;
  onRemove: () => void;
}

function modelName(drone: DroneProfile): string {
  return fleetCatalog.models.find((model) => model.id === drone.modelId)?.name ?? "Своя конфигурация";
}

export function FleetManager(props: FleetManagerProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const dialog = useModalDialog(open);
  const selected = props.drones.find((drone) => drone.id === props.selectedDroneId) ?? props.drones[0];
  const enabled = props.drones.filter((drone) => drone.enabled).length;
  const visible = props.drones.filter((drone) =>
    `${drone.name} ${drone.id} ${modelName(drone)}`.toLowerCase().includes(search.trim().toLowerCase()));

  return <>
    <button className="fleet-launcher" type="button" aria-haspopup="dialog" onClick={() => setOpen(true)}>
      <Plane size={22} aria-hidden="true" />
      <span><strong>Управлять парком</strong><small>{props.drones.length} БВС · {enabled} в расчёте</small></span>
      <ChevronRight size={18} aria-hidden="true" />
    </button>
    <p className="hint">Выберите борта для задания, проверьте их характеристики и добавьте новые.</p>

    <dialog ref={dialog} className="fleet-dialog" aria-labelledby="fleet-dialog-title" onClose={() => setOpen(false)}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) setOpen(false);
      }}>
      <header className="fleet-dialog-header">
        <div><span className="eyebrow">Подготовка к заданию</span><h2 id="fleet-dialog-title">Парк БВС</h2><p>{enabled} в расчёте из {props.drones.length} · до 100 бортов</p></div>
        <button className="showcase-close" type="button" aria-label="Закрыть парк БВС" onClick={() => setOpen(false)}><X size={20} /></button>
      </header>
      <div className="fleet-dialog-layout">
        <aside className="fleet-browser">
          <label className="field fleet-search"><span><Search size={14} aria-hidden="true" /> Найти БВС</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Название или модель" /></label>
          <nav className="fleet-list" aria-label="Борты парка">
            {visible.map((drone) => <button type="button" key={drone.id}
              className={`fleet-row ${drone.id === selected?.id ? "active" : ""}`} aria-pressed={drone.id === selected?.id}
              onClick={() => props.onSelect(drone.id)}>
              <span className="color-dot" style={{ background: drone.color }} />
              <span className="fleet-row-info"><strong>{drone.name}</strong><small>{modelName(drone)} · заряд {drone.battery}%</small><span className={drone.enabled ? "fleet-enabled" : "fleet-disabled"}>{drone.enabled ? "В расчёте" : "Отключён"}</span></span>
              <ChevronRight size={15} aria-hidden="true" />
            </button>)}
            {visible.length === 0 && <p className="hint">БВС не найдены. Измените запрос.</p>}
          </nav>
          <details className="fleet-add-panel" open>
            <summary>Добавить БВС в парк</summary>
            <FleetActions count={props.drones.length} onAdd={(modelId, count) => { setSearch(""); props.onAdd(modelId, count); }} />
          </details>
        </aside>
        <section className="fleet-editor-pane" aria-label="Настройки выбранного БВС">
          {selected && <>
            <div className="fleet-editor-heading"><div><span className="eyebrow">Настройки борта</span><h3>{selected.name}</h3></div>
              <div className="mini-actions"><button type="button" disabled={props.drones.length >= 100} onClick={() => { setSearch(""); props.onClone(); }}>Копировать настройки</button><button type="button" disabled={props.drones.length <= 1} onClick={props.onRemove}>Удалить БВС</button></div>
            </div>
            <DroneEditor key={selected.id} drone={selected} survey={props.survey} onUpdate={(patch) => props.onUpdate(selected.id, patch)} />
          </>}
        </section>
      </div>
      <footer className="fleet-dialog-footer"><p className="hint">Изменения сразу применяются к заданию. Для работы в другой сессии сохраните черновик или проект.</p><button type="button" className="secondary-button" onClick={() => setOpen(false)}>Готово</button></footer>
    </dialog>
  </>;
}
