import { useState } from "react";
import { fleetCatalog } from "@/features/planner/model/fleetCatalog";

interface FleetActionsProps {
  count: number;
  onAdd: (modelId: string, count: number) => void;
  onClone: () => void;
  onRemove: () => void;
}

export function FleetActions({ count, onAdd, onClone, onRemove }: FleetActionsProps) {
  const [modelId, setModelId] = useState("201");
  const [quantity, setQuantity] = useState(1);
  return <>
    <div className="field-grid">
      <label>Модель новых бортов<select value={modelId} onChange={(event) => setModelId(event.target.value)}>
        {fleetCatalog.models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
        <option value="custom">Своя конфигурация</option>
      </select></label>
      <label>Количество новых бортов<input type="number" min={1} max={Math.max(1, 100 - count)} step={1} value={quantity} onChange={(event) => setQuantity(Number(event.target.value))} /></label>
    </div>
    <div className="mini-actions">
      <button type="button" disabled={count >= 100 || !Number.isInteger(quantity) || quantity < 1 || count + quantity > 100} onClick={() => onAdd(modelId, quantity)}>＋ Добавить</button>
      <button type="button" disabled={count >= 100} onClick={onClone}>Копировать настройки</button>
      <button type="button" disabled={count <= 1} onClick={onRemove}>Удалить</button>
    </div>
    <p className="hint">Каждая строка — отдельный физический борт. Шаблон задаёт начальные характеристики; проверьте комплектацию, камеру и текущее состояние. Площадки для нового борта назначаются отдельно либо доступны всему парку.</p>
  </>;
}
