import type { DroneProfile } from "@/features/planner/model/types";
import type { VerticalTakeoffLandingProfile } from "@/shared/api/types";

export function TakeoffLandingFields({ drone, onUpdate }: {
  drone: DroneProfile;
  onUpdate: (patch: Partial<DroneProfile>) => void;
}) {
  const profile = drone.takeoffLandingProfile;
  const supported = !["fixed_wing", "vtol_fixed_rotor"].includes(drone.airframeType);
  const fields: Array<[keyof VerticalTakeoffLandingProfile, string, number, number]> = [
    ["climb_rate_mps", "Плановый набор, м/с", 0.1, 100],
    ["descent_rate_mps", "Плановое снижение, м/с", 0.1, 100],
    ["climb_energy_multiplier", "Расход на наборе / крейсерский", 1, 10],
    ["descent_energy_multiplier", "Расход на снижении / крейсерский", 1, 10],
  ];
  return <div>
    <label><input type="checkbox" checked={profile != null} disabled={!supported}
      onChange={(event) => onUpdate({ takeoffLandingProfile: event.target.checked ? {
        climb_rate_mps: Math.min(2, drone.maximumClimbRate ?? 2),
        descent_rate_mps: Math.min(1, drone.maximumDescentRate ?? 1),
        climb_energy_multiplier: 1, descent_energy_multiplier: 1,
      } : null })} /> Учитывать вертикальные взлёт и посадку</label>
    {profile && <div className="field-grid">{fields.map(([key, label, min, max]) =>
      <label className="field" key={key}><span>{label}</span><input type="number" min={min} max={max} step={0.1}
        value={profile[key]} onChange={(event) => onUpdate({ takeoffLandingProfile: {
          ...profile, [key]: Number(event.target.value),
        } })} /></label>
    )}</div>}
    <p className="hint">{supported
      ? profile ? "Проверьте начальные значения по вашему режиму полёта. Это плановые скорости, не паспортные максимумы. Коэффициент 1 означает расход как на крейсерском полёте; значения требуют калибровки. Нужна свободная вертикальная колонна над площадкой. Экспорт не изменяет настройки вертикальной скорости автопилота."
        : "Без профиля базовые набор и снижение не входят во время и расход АКБ."
      : "Самолётам и VTOL нужна отдельная модель траектории взлёта, перехода и посадки."}</p>
  </div>;
}
