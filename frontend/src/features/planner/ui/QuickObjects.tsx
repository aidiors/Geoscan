import { useState } from "react";
import type { ImportedZone } from "@/shared/api/types";
import type { ZoneLayerPatch } from "../model/objects";
import { ZoneLayers } from "./ZoneLayers";

interface Props {
  zones: ImportedZone[];
  onUpdate: (id: string, patch: ZoneLayerPatch) => void;
  onSelect: (id: string, enabled: boolean) => void;
  onRemove: (id: string) => void;
  onManage: () => void;
}

export function QuickObjects({ zones, onUpdate, onSelect, onRemove, onManage }: Props) {
  const [search, setSearch] = useState("");
  const filtered = zones.filter((zone) => `${zone.name} ${zone.zone_id}`.toLowerCase().includes(search.trim().toLowerCase()));
  return <div className="quick-objects" aria-label="Быстрые действия с зонами">
    <label className="field zone-search"><span>Найти область</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Название или ID" /></label>
    <ZoneLayers zones={filtered.slice(0, 6)} onUpdate={onUpdate} onSelect={onSelect} onRemove={onRemove} />
    {!filtered.length && <p className="hint">Зоны не найдены.</p>}
    {filtered.length > 6 && <button type="button" className="quick-more" onClick={onManage}>Все объекты ({filtered.length})</button>}
    <p className="hint">Глаз - видимость, замок - защита от удаления. Флажок включает рабочую область в расчёт.</p>
  </div>;
}
