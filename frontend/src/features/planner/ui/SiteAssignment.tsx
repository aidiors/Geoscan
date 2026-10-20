import { assignedDroneIds } from "../model/siteAssignments";
import type { DroneProfile, FlightSiteState } from "../model/types";

interface Props {
  site: FlightSiteState;
  drones: DroneProfile[];
  onChange: (ids: string[]) => void;
}

export function SiteAssignment({ site, drones, onChange }: Props) {
  const ids = assignedDroneIds(site);
  const all = ids.length === 0;
  return <details className="site-assignment">
    <summary>{all ? "Весь парк" : `Назначено БВС: ${ids.length}`}</summary>
    <fieldset><legend>БВС для площадки {site.name}</legend>
      <label><input type="checkbox" checked={all} onChange={() => onChange(all ? [drones[0].id] : [])} />Весь парк</label>
      {drones.map((drone) => <label key={drone.id}>
        <input type="checkbox" checked={all || ids.includes(drone.id)}
          disabled={(all && drones.length === 1) || (!all && ids.length === 1 && ids.includes(drone.id))}
          onChange={(event) => onChange(event.target.checked ? [...ids, drone.id] : (all ? drones.map((item) => item.id) : ids).filter((id) => id !== drone.id))} />
        {drone.name}{!drone.enabled && " · отключён"}
      </label>)}
    </fieldset>
    <p className="hint">Разрешённые борта. Оптимизатор выбирает площадку среди доступных каждому БВС.</p>
  </details>;
}
