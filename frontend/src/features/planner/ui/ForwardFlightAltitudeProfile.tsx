import type { Mission } from "@/shared/api/types";

export function ForwardFlightAltitudeProfile({ mission }: { mission: Mission }) {
  const heights = mission.route_altitudes_msl_m;
  if (!mission.forward_flight_phases || !heights) return null;
  const times = [0];
  for (const stage of mission.stages) {
    if (stage.coordinates.length > 1 && stage.leg_durations_minutes == null) return null;
    for (let i = 1; i < stage.coordinates.length; i++) {
      const a = stage.coordinates[i - 1], b = stage.coordinates[i];
      if (a.latitude === b.latitude && a.longitude === b.longitude) continue;
      times.push(times.at(-1)! + stage.leg_durations_minutes![i - 1]);
    }
  }
  if (times.length !== heights.length || times.at(-1)! <= 0) return null;
  const lower = heights.reduce((a, b) => Math.min(a, b), Infinity);
  const upper = heights.reduce((a, b) => Math.max(a, b), -Infinity);
  const duration = times.at(-1)!;
  const y = (value: number) => 112 - (value - lower) / Math.max(1, upper - lower) * 72;
  const points = heights.map((height, i) => `${48 + times[i] / duration * 328},${y(height)}`).join(" ");
  return <div className="editor-card">
    <strong>Высота одного самолётного вылета</strong>
    <svg viewBox="0 0 410 155" role="img" aria-label="Самолётный профиль высоты по времени"
      style={{ width:"100%", display:"block" }}>
      <title>Набор на перелёте, съёмка на рабочей высоте, снижение на возврате</title>
      <text x="8" y="17" fontSize="11" fill="currentColor">м MSL</text>
      {[lower, upper].map((value, i) => <g key={i}>
        <line x1="48" x2="376" y1={y(value)} y2={y(value)} stroke="currentColor" opacity="0.15" />
        <text x="40" y={y(value) + 4} fontSize="11" textAnchor="end" fill="currentColor">{Math.round(value)}</text>
      </g>)}
      <polyline points={points} fill="none" stroke="#6d28d9" strokeWidth="2.5" />
      <text x="48" y="132" fontSize="11" fill="currentColor">0</text>
      <text x="376" y="132" fontSize="11" textAnchor="end" fill="currentColor">{duration.toFixed(1)}</text>
      <text x="212" y="151" fontSize="11" textAnchor="middle" fill="currentColor">Время от старта вылета, мин</text>
    </svg>
    <p className="hint">Профиль рассчитан над заданной плоской поверхностью. Фазы набора и снижения входят во время перелётов.</p>
  </div>;
}
