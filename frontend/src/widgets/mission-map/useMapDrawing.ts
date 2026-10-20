import { useState } from "react";
import type { Coordinate } from "@/shared/geo/types";
import { SurveyPolygon } from "@/shared/geo/SurveyPolygon";
import type { ImportedZone } from "@/shared/api/types";

export function useMapDrawing(onAdd?: (zone: ImportedZone) => void) {
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [drawing, setDrawing] = useState(false);
  const [vertices, setVertices] = useState<Coordinate[]>([]);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [type, setType] = useState<"work_area" | "no_fly_zone">("work_area");
  function cancel() { setDrawing(false); setVertices([]); setError(""); }
  function finish() {
    try {
      const coordinates = SurveyPolygon.close(vertices);
      onAdd?.({ zone_id: `drawn-${crypto.randomUUID()}`, name: name.trim() || (type === "work_area" ? "Новая область" : "Новый запрет"),
        zone_type: type, geometry_type: "polygon", coordinates, interior_rings: [], enabled: true,
        metadata: { source: "map_editor" } });
      cancel();
      setName("");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "Не удалось создать контур"); }
  }
  return { mode, drawing, vertices, error, name, type, setName, setType, cancel, finish,
    changeMode: (next: "view" | "edit") => { cancel(); setMode(next); },
    begin: () => { cancel(); setDrawing(true); },
    add: (point: Coordinate) => { setVertices((current) => [...current, point]); setError(""); },
    undo: () => { setVertices((current) => current.slice(0, -1)); setError(""); },
  };
}
