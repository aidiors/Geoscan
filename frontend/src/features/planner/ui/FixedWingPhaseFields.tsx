import type { DroneProfile } from "@/features/planner/model/types";
import type { FlightPhaseOperatingProfile } from "@/shared/api/types";

export function FixedWingPhaseFields({ drone, onUpdate }: {
  drone: DroneProfile; onUpdate: (patch: Partial<DroneProfile>) => void;
}) {
  if (drone.airframeType !== "fixed_wing") return null;
  const profile = drone.fixedWingPhaseProfile;
  const fields: Array<[keyof FlightPhaseOperatingProfile, string, number, number]> = [
    ["climb_rate_mps", "Плановый предел набора на ходу, м/с", 0.1, 100],
    ["descent_rate_mps", "Плановый предел снижения на ходу, м/с", 0.1, 100],
    ["climb_energy_multiplier", "Расход при наборе на ходу / крейсерский", 1, 10],
    ["descent_energy_multiplier", "Расход при снижении на ходу / крейсерский", 1, 10],
  ];
  return <div>
    <label className="checkbox-row"><input type="checkbox" checked={profile != null}
      onChange={(event) => onUpdate({ fixedWingPhaseProfile: event.target.checked ? {
        climb_rate_mps: Math.min(2, drone.maximumClimbRate ?? 2),
        descent_rate_mps: Math.min(1, drone.maximumDescentRate ?? 1),
        climb_energy_multiplier: 1, descent_energy_multiplier: 1,
      } : null })} /><span>Учитывать самолётный набор и снижение на ходу</span></label>
    {profile && <div className="field-grid">{fields.map(([key, label, min, max]) =>
      <label className="field" key={key}><span>{label}</span><input type="number" min={min} max={max} step={0.1}
        value={profile[key]} onChange={(event) => onUpdate({ fixedWingPhaseProfile: { ...profile, [key]: Number(event.target.value) } })} /></label>
    )}</div>}
    {profile && <p className="hint">Проверьте начальные значения и задайте радиус разворота. Набор и снижение распределяются по перелётам; при нехватке длины вариант отклоняется. Время не добавляется повторно. Модель плоской MSL-поверхности, без аварийного захода и парашютной посадки. Результат доступен для GIS-проверки; автопилотный экспорт этого режима заблокирован.</p>}
  </div>;
}
