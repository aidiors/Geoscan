import type { ZoneLayerPatch } from "../model/objects";
import { Eye, EyeOff, LockKeyhole, UnlockKeyhole, Trash2 } from "lucide-react";
import type { ImportedZone } from "@/shared/api/types";

interface Props {
  zones: ImportedZone[];
  onUpdate: (id: string, patch: ZoneLayerPatch) => void;
  onSelect: (id: string, enabled: boolean) => void;
  onRemove: (id: string) => void;
}

export function ZoneLayers({ zones, onUpdate, onSelect, onRemove }: Props) {
  return <div className="zone-list">{zones.map((zone) => <div className="zone-item" key={zone.zone_id}>
    <span className="zone-selection">
      {zone.zone_type === "work_area" && <input type="checkbox" aria-label={`Включить область ${zone.name}`} checked={zone.enabled !== false} onChange={(event) => onSelect(zone.zone_id, event.target.checked)} />}
      <span><b title={zone.name}>{zone.name}</b><small>{zone.metadata?.geometry_issue ?? zone.metadata?.geometry_warning ?? (zone.zone_type === "work_area" ? "Рабочая область" : "Ограничение")}</small></span>
    </span>
    <div className="zone-layer-actions">
      <button type="button" aria-label={`${zone.visible === false ? "Показать" : "Скрыть"} ${zone.name}`} aria-pressed={zone.visible === false} title="Видимость на карте. Участие в расчёте не меняется" onClick={() => onUpdate(zone.zone_id, { visible: zone.visible === false })}>{zone.visible === false ? <EyeOff size={16} /> : <Eye size={16} />}</button>
      <button type="button" aria-label={`${zone.locked ? "Разблокировать" : "Защитить"} ${zone.name}`} aria-pressed={!!zone.locked} title="Защита от удаления" onClick={() => onUpdate(zone.zone_id, { locked: !zone.locked })}>{zone.locked ? <LockKeyhole size={16} /> : <UnlockKeyhole size={16} />}</button>
      <button type="button" aria-label={`Удалить ${zone.name}`} disabled={zone.locked} onClick={() => onRemove(zone.zone_id)}><Trash2 size={16} /></button>
    </div>
  </div>)}</div>;
}
