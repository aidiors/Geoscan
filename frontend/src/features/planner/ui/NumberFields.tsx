export function NumberField({ label, value, min, max, step = 1, onChange }: { label: string; value: number; min?: number; max?: number; step?: number | "any"; onChange: (value: number) => void }) {
  return <label className="field"><span>{label}</span><input type="number" value={value} min={min} max={max} step={step} onChange={(event) => onChange(Number(event.target.value))} /></label>;
}

export function OptionalNumberField({ label, value, min, max, integer = false, onChange }: { label: string; value: number | null; min?: number; max?: number; integer?: boolean; onChange: (value: number | null) => void }) {
  return <label className="field"><span>{label}</span><input type="number" value={value ?? ""} min={min} max={max} step={integer ? 1 : "any"} placeholder="не задано" onChange={(event) => onChange(event.target.value === "" ? null : Number(event.target.value))} /></label>;
}
