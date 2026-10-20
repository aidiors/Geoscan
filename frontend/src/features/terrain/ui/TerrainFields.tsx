import { useState } from "react";
import type { TerrainAsset } from "../model/types";

export function TerrainFields({ terrain, busy, onImport, onChange }: {
  terrain: TerrainAsset | null;
  busy: boolean;
  onImport: (file: File, band: number) => Promise<void>;
  onChange: (asset: TerrainAsset | null) => void;
}) {
  const [band, setBand] = useState(1);
  const info = terrain?.inspection;
  return <div className="editor-card">
    <strong>Рельеф GeoTIFF</strong>
    <label className="field"><span>Канал высот для загрузки</span><input type="number" min="1" max="256" step="1" value={band} disabled={busy}
      onChange={(event) => setBand(Number(event.target.value))} /></label>
    <label className="field"><span>{busy ? "Проверяем DEM…" : "Загрузить DEM (до 8 МиБ)"}</span>
      <input type="file" accept=".tif,.tiff" disabled={busy || !Number.isInteger(band) || band < 1 || band > 256} onChange={(event) => {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (file) void onImport(file, band);
      }} /></label>
    {terrain && info && <>
      <p className="hint">{terrain.name} · канал {terrain.band} · {info.width} × {info.height} · {info.crs}<br />
        Высоты {info.minimum_elevation_m.toFixed(1)}…{info.maximum_elevation_m.toFixed(1)} м · данные в {(info.valid_fraction * 100).toFixed(1)}% ячеек.<br />
        Scale {info.scale}, offset {info.offset}; единицы файла: {info.unit || "не указаны"}.<br />
        Границы WGS84 (запад, юг, восток, север): {info.bounds_wgs84.map((v) => v.toFixed(5)).join(", ")}.</p>
      <label className="checkbox-row"><input type="checkbox" checked={terrain.metresConfirmed} onChange={(event) => onChange({ ...terrain, metresConfirmed: event.target.checked })} />Подтверждаю: высоты после scale/offset заданы в метрах</label>
      <label className="checkbox-row"><input type="checkbox" checked={terrain.mslConfirmed} onChange={(event) => onChange({ ...terrain, mslConfirmed: event.target.checked })} />Подтверждаю: отметки DEM приведены к используемой системе MSL</label>
      <button type="button" onClick={() => onChange(null)}>Удалить DEM</button>
    </>}
    <p className="hint">CRS не подтверждает систему высот. Эллипсоидальные высоты предварительно приведите к MSL; пересчёт геоида здесь не выполняется. DEM должен покрывать все перелёты и площадки. Пропуски NoData не заполняются.</p>
  </div>;
}
