import { useState } from "react";
import type { ImportedZone } from "@/shared/api/types";
import type { DroneProfile, FlightSiteState } from "../model/types";
import type { SitePatch, ZoneLayerPatch } from "../model/objects";
import { SiteAssignment } from "./SiteAssignment";
import { ZoneLayers } from "./ZoneLayers";

interface ZoneProps {
  zone: ImportedZone;
  onUpdate: (id: string, patch: ZoneLayerPatch) => void;
  onSelect: (id: string, enabled: boolean) => void;
  onRemove: (id: string) => void;
}

export function ZoneObjectEditor({ zone, onUpdate, onSelect, onRemove }: ZoneProps) {
  const [name, setName] = useState(zone.name);
  const [saved, setSaved] = useState(false);
  return <>
    <form className="object-edit-form" onSubmit={(event) => {
      event.preventDefault();
      if (!name.trim()) return;
      onUpdate(zone.zone_id, { name: name.trim() });
      setSaved(true);
    }}>
      <label className="field"><span>Название объекта</span><input required maxLength={128} value={name} onChange={(event) => { setName(event.target.value); setSaved(false); }} /></label>
      <button className="secondary-button" type="submit" disabled={!name.trim()}>Применить название</button>
      {saved && <p className="object-feedback" aria-live="polite">Название сохранено</p>}
    </form>
    <p className="hint">ID: {zone.zone_id} · вершин: {zone.coordinates.length} · внутренних контуров: {zone.interior_rings.length}</p>
    <ZoneLayers zones={[zone]} onUpdate={onUpdate} onSelect={onSelect} onRemove={onRemove} />
    <p className="hint">Глаз управляет видимостью на карте; скрытые ограничения продолжают действовать. Замок защищает от удаления. Флажок рабочей области управляет участием в расчёте.</p>
  </>;
}

interface SiteProps {
  site: FlightSiteState;
  drones: DroneProfile[];
  onUpdate: (id: string, patch: SitePatch) => void;
  onAssign: (id: string, ids: string[]) => void;
  onRemove: (id: string) => void;
}

export function SiteObjectEditor({ site, drones, onUpdate, onAssign, onRemove }: SiteProps) {
  const [name, setName] = useState(site.name);
  const [role, setRole] = useState(site.role);
  const [latitude, setLatitude] = useState(String(site.coordinate.latitude));
  const [longitude, setLongitude] = useState(String(site.coordinate.longitude));
  const [saved, setSaved] = useState(false);
  return <>
    <form className="object-edit-form" onChange={() => setSaved(false)} onSubmit={(event) => {
      event.preventDefault();
      const lat = Number(latitude), lon = Number(longitude);
      if (!name.trim() || !latitude.trim() || !longitude.trim() || !Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 85 || Math.abs(lon) > 180) return;
      onUpdate(site.site_id, { name: name.trim(), role, coordinate: { ...site.coordinate, latitude: lat, longitude: lon } });
      setSaved(true);
    }}>
      <label className="field"><span>Название объекта</span><input required maxLength={128} value={name} onChange={(event) => setName(event.target.value)} /></label>
      <label className="field"><span>Роль площадки</span><select aria-label="Роль площадки" value={role} onChange={(event) => setRole(event.target.value as FlightSiteState["role"])}><option value="takeoff">Взлёт</option><option value="landing">Посадка</option><option value="alternate">Резерв</option></select></label>
      <div className="field-grid">
        <label className="field"><span>Широта площадки</span><input required type="number" step="any" min={-85} max={85} value={latitude} onChange={(event) => setLatitude(event.target.value)} /></label>
        <label className="field"><span>Долгота площадки</span><input required type="number" step="any" min={-180} max={180} value={longitude} onChange={(event) => setLongitude(event.target.value)} /></label>
      </div>
      <button className="secondary-button" type="submit" disabled={!name.trim()}>Применить изменения</button>
      {saved && <p className="object-feedback" aria-live="polite">Изменения сохранены</p>}
    </form>
    <div className="site-item"><SiteAssignment site={site} drones={drones} onChange={(ids) => onAssign(site.site_id, ids)} /></div>
    <div className="mini-actions"><button type="button" onClick={() => onRemove(site.site_id)}>Удалить площадку</button></div>
  </>;
}
