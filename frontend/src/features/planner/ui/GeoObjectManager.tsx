import { useState } from "react";
import { ChevronRight, Layers, X } from "lucide-react";
import { useModalDialog } from "@/shared/ui/useModalDialog";
import type { ImportedZone } from "@/shared/api/types";
import type { DroneProfile, FlightSiteState } from "../model/types";
import type { SitePatch, ZoneLayerPatch } from "../model/objects";
import { SiteObjectEditor, ZoneObjectEditor } from "./GeoObjectEditors";

type Category = "all" | "work_area" | "restriction" | "site";
interface Props {
  zones: ImportedZone[];
  sites: FlightSiteState[];
  drones: DroneProfile[];
  onUpdateZone: (id: string, patch: ZoneLayerPatch) => void;
  onSelectZone: (id: string, enabled: boolean) => void;
  onRemoveZone: (id: string) => void;
  onSelectAll: (enabled: boolean) => void;
  onUpdateSite: (id: string, patch: SitePatch) => void;
  onAssignSite: (id: string, ids: string[]) => void;
  onRemoveSite: (id: string) => void;
}

const labels = { work_area: "Рабочая область", no_fly_zone: "Запретная зона", allowed_airspace: "Разрешённое пространство", obstacle: "Препятствие", takeoff: "Взлёт", landing: "Посадка", alternate: "Резерв" };

export function GeoObjectManager(props: Props) {
  const [open, setOpen] = useState(false);
  const dialog = useModalDialog(open);
  const [category, setCategory] = useState<Category>("all");
  const [search, setSearch] = useState("");
  const [selectedKey, setSelectedKey] = useState("");
  const entries = [
    ...props.zones.map((zone) => ({ key: `zone:${zone.zone_id}`, id: zone.zone_id, name: zone.name, label: labels[zone.zone_type], category: zone.zone_type === "work_area" ? "work_area" : "restriction", zone, site: null })),
    ...props.sites.map((site) => ({ key: `site:${site.site_id}`, id: site.site_id, name: site.name, label: labels[site.role], category: "site", zone: null, site })),
  ];
  const visible = entries.filter((entry) => (category === "all" || entry.category === category)
    && `${entry.name} ${entry.id}`.toLowerCase().includes(search.trim().toLowerCase()));
  const selected = visible.find((entry) => entry.key === selectedKey) ?? visible[0];

  return <>
    <button className="fleet-launcher" type="button" aria-haspopup="dialog" onClick={() => setOpen(true)}>
      <Layers size={22} aria-hidden="true" /><span><strong>Управлять объектами</strong><small>Зон: {props.zones.length} · площадок: {props.sites.length}</small></span><ChevronRight size={18} aria-hidden="true" />
    </button>
    <p className="hint">Области, ограничения и площадки: названия, настройки, назначение БВС и удаление. Новые контуры и площадки добавляются на карте.</p>
    <dialog ref={dialog} className="fleet-dialog object-dialog" aria-labelledby="object-dialog-title" onClose={() => setOpen(false)} onClick={(event) => {
      if (event.target !== event.currentTarget) return;
      const bounds = event.currentTarget.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) setOpen(false);
    }}>
      <header className="fleet-dialog-header"><div><span className="eyebrow">Подготовка территории</span><h2 id="object-dialog-title">Зоны и площадки</h2><p>Объектов: {entries.length}</p></div><button className="showcase-close" type="button" aria-label="Закрыть зоны и площадки" onClick={() => setOpen(false)}><X size={20} /></button></header>
      <div className="fleet-dialog-layout">
        <aside className="fleet-browser">
          <label className="field zone-search"><span>Найти объект</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Название или ID" /></label>
          <label className="field"><span>Тип объектов</span><select aria-label="Тип объектов" value={category} onChange={(event) => setCategory(event.target.value as Category)}><option value="all">Все объекты</option><option value="work_area">Рабочие области</option><option value="restriction">Ограничения</option><option value="site">Площадки</option></select></label>
          <nav className="fleet-list" aria-label="Объекты задания">{visible.map((entry) => <button key={entry.key} type="button" data-kind={entry.site ? "site" : "zone"} className={`geo-object-row ${selected?.key === entry.key ? "active" : ""}`} aria-pressed={selected?.key === entry.key} onClick={() => setSelectedKey(entry.key)}><span className="fleet-row-info"><strong>{entry.name}</strong><small>{entry.label}{entry.zone?.locked ? " · защищена" : ""}{entry.zone?.visible === false ? " · скрыта" : ""}{entry.zone?.enabled === false ? " · исключена" : ""}</small></span><ChevronRight size={15} /></button>)}</nav>
          {!visible.length && <p className="hint">Объекты не найдены. Измените фильтр или добавьте объект на карте.</p>}
          <div className="mini-actions"><button type="button" onClick={() => props.onSelectAll(true)}>Выбрать все области</button><button type="button" onClick={() => props.onSelectAll(false)}>Снять выбор областей</button></div>
        </aside>
        <section className="fleet-editor-pane" aria-label="Настройки объекта">
          {selected ? <><div className="fleet-editor-heading"><div><span className="eyebrow">{selected.label}</span><h3>{selected.name}</h3></div></div>
            {selected.zone && <ZoneObjectEditor key={selected.key} zone={selected.zone} onUpdate={(id, patch) => { if (patch.name !== undefined) { setSearch(""); setSelectedKey(`zone:${id}`); } props.onUpdateZone(id, patch); }} onSelect={props.onSelectZone} onRemove={props.onRemoveZone} />}
            {selected.site && <SiteObjectEditor key={selected.key} site={selected.site} drones={props.drones} onUpdate={(id, patch) => { setSearch(""); setSelectedKey(`site:${id}`); props.onUpdateSite(id, patch); }} onAssign={props.onAssignSite} onRemove={props.onRemoveSite} />}
          </> : <p className="hint">Выберите или добавьте объект.</p>}
        </section>
      </div>
      <footer className="fleet-dialog-footer"><p className="hint">Для имени и координат нажмите «Применить». Остальные переключатели применяются сразу. Сохраните черновик или проект для следующей сессии.</p><button className="secondary-button" type="button" onClick={() => setOpen(false)}>Готово</button></footer>
    </dialog>
  </>;
}
