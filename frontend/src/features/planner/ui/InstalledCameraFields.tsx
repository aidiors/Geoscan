import type { SensorProfile } from "@/shared/api/types";

interface Props {
  sensor: SensorProfile;
  onChange: (sensor: SensorProfile) => void;
}

const dimensions: Array<{ key: keyof SensorProfile; label: string; min: number; max: number; step: number }> = [
  { key: "sensor_width_mm", label: "Ширина матрицы, мм", min: 0.1, max: 200, step: 0.1 },
  { key: "sensor_height_mm", label: "Высота матрицы, мм", min: 0.1, max: 200, step: 0.1 },
  { key: "focal_length_mm", label: "Фокус, мм", min: 0.1, max: 1000, step: 0.1 },
  { key: "image_width_px", label: "Ширина кадра, px", min: 1, max: 200000, step: 1 },
  { key: "image_height_px", label: "Высота кадра, px", min: 1, max: 200000, step: 1 },
  { key: "minimum_trigger_interval_s", label: "Мин. интервал кадров, с", min: 0.01, max: 120, step: 0.01 },
];

export function InstalledCameraFields({ sensor, onChange }: Props) {
  return <div className="field-grid">{dimensions.map(({ key, label, min, max, step }) => (
    <label className="field" key={key}><span>{label}</span>
      <input type="number" min={min} max={max} step={step} value={sensor[key]}
        onChange={(event) => {
          const value = Number(event.target.value);
          if (Number.isFinite(value) && value >= min && value <= max) onChange({ ...sensor, [key]: value });
        }} />
    </label>
  ))}</div>;
}
