import { useState } from "react";
import type { Mission } from "@/shared/api/types";

export function TerrainAltitudeProfile({ missions }: { missions: Mission[] }) {
  const [selected, setSelected] = useState(0);
  const available = missions.filter((mission) => (mission.vertical_profile?.length ?? 0) > 0);
  const index = Math.min(selected, Math.max(0, available.length - 1));
  const mission = available[index];
  if (!mission) return null;
  const profile = mission.vertical_profile;
  const distance = profile.at(-1)!.distance_from_start_m;
  let lower = Infinity, upper = -Infinity;
  for (const point of profile) {
    lower = Math.min(lower, point.terrain_elevation_m);
    upper = Math.max(upper, point.target_altitude_msl);
  }
  const x = (d: number) => 48 + d / Math.max(1, distance) * 328;
  const y = (h: number) => 112 - (h - lower) / Math.max(1, upper - lower) * 80;
  // Display a min/max envelope per bucket so narrow terrain peaks remain visible.
  const displayed = profile.flatMap((_, i) => {
    if (i % 100 !== 0) return [];
    const bucket = profile.slice(i, i + 100);
    const lo = bucket.reduce((a, b) => a.terrain_elevation_m < b.terrain_elevation_m ? a : b);
    const hi = bucket.reduce((a, b) => a.terrain_elevation_m > b.terrain_elevation_m ? a : b);
    return [...new Set([bucket[0], lo, hi, bucket.at(-1)!])].sort((a, b) => a.distance_from_start_m - b.distance_from_start_m);
  });
  const points = (terrain: boolean) => displayed.map((p) => `${x(p.distance_from_start_m)},${y(terrain ? p.terrain_elevation_m : p.target_altitude_msl)}`).join(" ");
  return <div className="editor-card">
    <strong>Профиль рельефа и высоты</strong>
    <label className="field"><span>Вылет с DEM</span><select aria-label="Вылет с DEM" value={index} onChange={(event) => setSelected(Number(event.target.value))}>
      {available.map((m, i) => <option key={i} value={i}>{i + 1} · {m.segment_id}</option>)}
    </select></label>
    <svg viewBox="0 0 410 155" role="img" aria-label="Профиль DEM и высоты MSL по расстоянию" style={{ width: "100%", display: "block" }}>
      <text x="8" y="17" fontSize="14" fill="currentColor">м MSL</text>
      {[lower, upper].map((h, i) => <text key={i} x="42" y={y(h) + 4} textAnchor="end" fontSize="14" fill="currentColor">{h.toFixed(0)}</text>)}
      <polyline points={points(true)} fill="none" stroke="#a16207" strokeWidth="2" />
      <polyline points={points(false)} fill="none" stroke="#2563eb" strokeWidth="2" />
      <text x="48" y="132" fontSize="14" fill="currentColor">0</text>
      <text x="376" y="132" fontSize="14" textAnchor="end" fill="currentColor">{(distance / 1000).toFixed(2)}</text>
      <text x="212" y="151" fontSize="14" textAnchor="middle" fill="currentColor">Расстояние от начала вылета, км</text>
    </svg>
    <p className="hint">Минимальный расчётный AGL: {mission.minimum_agl_m?.toFixed(1)} м · набор до {mission.maximum_required_climb_rate_mps?.toFixed(2)} м/с · снижение до {mission.maximum_required_descent_rate_mps?.toFixed(2)} м/с.</p>
    <p className="hint">Коричневый - DEM, синий - заданная высота MSL. {profile.length} отсчётов. Показаны экстремумы групп отсчётов, пики между исходными отсчётами не проверены. Профиль не моделирует динамику автопилота.</p>
  </div>;
}
