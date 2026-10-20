import { useMapDrawing } from "./useMapDrawing";
import { Crosshair, Map, MapPinPlus, MapPinned, Minus, Mountain, Plus, Trash2 } from "lucide-react";
import { useLayoutEffect, useMemo, useRef, useState } from "react";

import {
  DEFAULT_MAP_SIZE,
  coordinateToMapPoint,
  fitCoordinates,
  mapPointToCoordinate,
  panView,
  visibleTiles,
  zoomAtPoint,
} from "@/shared/geo/map";
import type { MapSize } from "@/shared/geo/map";
import type { Basemap } from "@/shared/geo/basemaps";
import type { Coordinate, MapView, Point } from "@/shared/geo/types";
import type { DroneProfile, FlightSiteState, SiteRole } from "@/features/planner/model/types";
import type { ImportedZone, PlanResponse, TurnRadiusAnalysisResult } from "@/shared/api/types";
import type { SurveyQualityReport } from "@/shared/api/types";

interface MissionMapProps {
  quality?: SurveyQualityReport | null;
  fitKey?: string;
  zones: ImportedZone[];
  sites: FlightSiteState[];
  drones: DroneProfile[];
  plan: PlanResponse | null;
  radiusAnalysis: TurnRadiusAnalysisResult | null;
  selectedDroneId: string;
  pendingSiteRole: SiteRole | null;
  onAddSite: (coordinate: Coordinate) => void;
  onClearZones: () => void;
  onAddZone?: (zone: ImportedZone) => void;
  onCancelSite?: () => void;
}

interface DragState {
  point: Point;
  view: MapView;
}

const siteColors: Record<SiteRole, string> = {
  takeoff: "#2c1558",
  landing: "#0f766e",
  alternate: "#f59e0b",
};

const siteLabels: Record<SiteRole, string> = {
  takeoff: "Взлёт",
  landing: "Посадка",
  alternate: "Резерв",
};

export function MissionMap({
  quality,
  fitKey,
  zones,
  sites,
  drones,
  plan,
  radiusAnalysis,
  selectedDroneId,
  pendingSiteRole,
  onAddSite,
  onClearZones,
  onAddZone,
  onCancelSite,
}: MissionMapProps) {
  const editor = useMapDrawing(onAddZone);
  const editMode = pendingSiteRole !== null || editor.mode === "edit";
  const frameRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const initialFitRef = useRef(false);
  const lastFitKey = useRef(fitKey);
  const allCoordinates = useMemo(
    () => [
      ...zones.filter((zone) => zone.visible !== false).flatMap((zone) => [...zone.coordinates, ...zone.interior_rings.flat()]),
      ...sites.map((flightSite) => flightSite.coordinate),
      ...(plan?.drone_plans.flatMap((dronePlan) => dronePlan.missions.flatMap((mission) => mission.route)) ?? []),
      ...(plan?.schedule?.resolved_conflicts.map((conflict) => conflict.coordinate) ?? []),
      ...(radiusAnalysis?.common_survey_route ?? []),
    ],
    [plan, radiusAnalysis, sites, zones],
  );
  const [view, setView] = useState<MapView>(() => fitCoordinates(allCoordinates));
  const [mapSize, setMapSize] = useState<MapSize>(DEFAULT_MAP_SIZE);
  const [basemap, setBasemap] = useState<Basemap>(() => {
    try { const saved = window.localStorage.getItem("geoplan-basemap"); return saved === "terrain" || saved === "satellite" ? saved : "streets"; }
    catch { return "streets"; }
  });
  const [failedBasemap, setFailedBasemap] = useState<Basemap | null>(null);
  const tiles = useMemo(() => visibleTiles(view, mapSize, basemap), [mapSize, view, basemap]);

  function changeBasemap(next: Basemap): void {
    setBasemap(next);
    setFailedBasemap(null);
    try { window.localStorage.setItem("geoplan-basemap", next); }
    catch { /* The layer remains usable when browser storage is unavailable. */ }
  }

  useLayoutEffect(() => {
    if (lastFitKey.current !== fitKey) {
      lastFitKey.current = fitKey;
      setView(fitCoordinates(allCoordinates, mapSize));
    }
    const frame = frameRef.current;
    if (frame === null) return undefined;

    const observer = new ResizeObserver((entries) => {
      const bounds = entries[0]?.contentRect;
      if (bounds === undefined || bounds.width <= 0 || bounds.height <= 0) return;
      const nextSize = {
        width: Math.round(bounds.width),
        height: Math.round(bounds.height),
      };
      setMapSize((current) =>
        current.width === nextSize.width && current.height === nextSize.height ? current : nextSize,
      );
      if (!initialFitRef.current) {
        initialFitRef.current = true;
        setView(fitCoordinates(allCoordinates, nextSize));
      }
    });

    observer.observe(frame);
    return () => observer.disconnect();
  }, [allCoordinates, fitKey, mapSize]);

  function pointerPoint(clientX: number, clientY: number): Point {
    const svg = svgRef.current;
    if (svg === null) return { x: 0, y: 0 };
    const bounds = svg.getBoundingClientRect();
    return {
      x: ((clientX - bounds.left) / bounds.width) * mapSize.width,
      y: ((clientY - bounds.top) / bounds.height) * mapSize.height,
    };
  }

  function handlePointerDown(event: React.PointerEvent<SVGSVGElement>): void {
    event.preventDefault();
    const point = pointerPoint(event.clientX, event.clientY);
    if (pendingSiteRole !== null) {
      onAddSite(mapPointToCoordinate(point, view, mapSize));
      return;
    }
    if (editMode && editor.drawing) { editor.add(mapPointToCoordinate(point, view, mapSize)); return; }
    dragRef.current = { point, view };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function handlePointerMove(event: React.PointerEvent<SVGSVGElement>): void {
    if (dragRef.current === null) return;
    event.preventDefault();
    const currentPoint = pointerPoint(event.clientX, event.clientY);
    setView(panView(dragRef.current.view, dragRef.current.point, currentPoint));
  }

  function handleWheel(event: React.WheelEvent<SVGSVGElement>): void {
    event.preventDefault();
    const point = pointerPoint(event.clientX, event.clientY);
    setView((current) => zoomAtPoint(current, event.deltaY < 0 ? 1 : -1, point, mapSize));
  }

  const feasibility = plan?.feasibility?.message ?? "Маршрут ещё не рассчитан.";

  return (
    <section className="map-zone">
      <div className="map-title">
        <div><div className="eyebrow"><MapPinned size={15} /> GIS-карта миссий</div><h2>План покрытия территории</h2></div>
        <div className="legend"><span><i className="legend-route" />Съёмка</span><span><i className="legend-transit" />Перелёт</span>{radiusAnalysis?.common_survey_route && <span><i className="legend-radius" />Условный маршрут</span>}<span><i className="legend-target" />Целевая зона</span><span><i className="legend-restricted" />Запретная зона</span></div>
      </div>
        <div className="map-editor-controls">
          <div className="map-mode-switch" role="group" aria-label="Режим карты">
            <button type="button" aria-pressed={!editMode} onClick={() => { editor.changeMode("view"); onCancelSite?.(); }}>Просмотр</button>
            <button type="button" aria-pressed={editMode} onClick={() => editor.changeMode("edit")}>Редактирование</button>
          </div>
          {editMode && !editor.drawing && <button type="button" disabled={!onAddZone} onClick={() => { onCancelSite?.(); editor.begin(); }}>Нарисовать область</button>}
          {editor.drawing && <div className="drawing-controls">
            <label>Название<input aria-label="Название контура" value={editor.name} onChange={(event) => editor.setName(event.target.value)} maxLength={100} placeholder="Новая область" /></label>
            <label>Тип контура<select aria-label="Тип контура" value={editor.type} onChange={(event) => editor.setType(event.target.value as typeof editor.type)}><option value="work_area">Рабочая область</option><option value="no_fly_zone">Запретная зона</option></select></label>
            <p>Отметьте вершины на карте: {editor.vertices.length}. Кнопка «Замкнуть» соединит последнюю вершину с первой.</p>
            <button type="button" disabled={editor.vertices.length < 3} onClick={editor.finish}>Замкнуть контур</button>
            <button type="button" disabled={!editor.vertices.length} onClick={editor.undo}>Убрать вершину</button>
            <button type="button" onClick={editor.cancel}>Отменить контур</button>
            {editor.error && <p role="alert">{editor.error}</p>}
          </div>}
        </div>
      <div ref={frameRef} className={`map-frame ${pendingSiteRole || editor.drawing ? "placing-site" : ""}`}>
        <svg
          ref={svgRef}
          viewBox={`0 0 ${mapSize.width} ${mapSize.height}`}
          preserveAspectRatio="none"
          role="img"
          aria-label="Интерактивная карта зоны обследования"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={() => { dragRef.current = null; }}
          onPointerCancel={() => { dragRef.current = null; }}
          onWheel={handleWheel}
        >
          <rect width={mapSize.width} height={mapSize.height} fill="#d7e2d9" />
          <g className="map-tiles">
            {tiles.map((tile) => <image key={tile.key} href={tile.href} x={tile.x} y={tile.y} width={tile.size} height={tile.size} onError={() => setFailedBasemap(basemap)} />)}
          </g>
          <g className="zone-layer">
            {zones.filter((zone) => zone.visible !== false).slice(0, 1_600).map((zone) => <ZoneShape key={zone.zone_id} zone={zone} view={view} mapSize={mapSize} />)}
          </g>
          {editor.drawing && <g className="drawing-layer" pointerEvents="none">
            <polyline points={points(editor.vertices, view, mapSize)} fill="#8b5cf633" stroke="#7c3aed" strokeWidth={3} strokeDasharray="6 4" />
            {editor.vertices.map((vertex, index) => { const p = coordinateToMapPoint(vertex, view, mapSize); return <g key={index}><circle cx={p.x} cy={p.y} r={5} fill="white" stroke="#7c3aed" strokeWidth={2} /><text x={p.x + 8} y={p.y - 8} fill="#7c3aed">{index + 1}</text></g>; })}
          </g>}
          <g className="quality-layer" pointerEvents="none">
            {quality?.features.flatMap((feature, index) => {
              const polygons = feature.geometry.type === "Polygon"
                ? [feature.geometry.coordinates as number[][][]]
                : feature.geometry.coordinates as number[][][][];
              const color = feature.properties.kind === "footprint" ? "#0d9488" : "#dc2626";
              return polygons.map((polygon, part) => <path key={`quality-${index}-${part}`} fill={color} fillOpacity={feature.properties.kind === "footprint" ? 0.08 : 0.4} stroke={color} strokeWidth={0.7} fillRule="evenodd"
                d={polygon.map((ring) => ring.map(([longitude, latitude], i) => {
                  const point = coordinateToMapPoint({ longitude, latitude }, view, mapSize);
                  return `${i === 0 ? "M" : "L"}${point.x},${point.y}`;
                }).join(" ") + " Z").join(" ")}>
                <title>{feature.properties.kind === "footprint" ? "Плановый отпечаток кадра" : feature.properties.kind === "coverage_gap" ? "Пробел покрытия кадрами" : feature.properties.kind.includes("overlap") ? "Недостаточное перекрытие" : "Нарушение GSD"}</title>
              </path>);
            })}
          </g>
          <g className="route-layer" fill="none" strokeLinecap="round" strokeLinejoin="round">
            {plan?.drone_plans.flatMap((dronePlan) => {
              const drone = drones.find((candidate) => candidate.id === dronePlan.drone_id);
              return dronePlan.missions.flatMap((mission) => mission.stages
                .filter((stage) => stage.coordinates.length >= 2 && stage.stage_type !== "takeoff")
                .map((stage, stageIndex) => {
                  const isSurvey = stage.stage_type === "survey";
                  const isSelected = dronePlan.drone_id === selectedDroneId;
                  return (
                    <polyline
                      key={`${dronePlan.drone_id}-${mission.segment_id}-${stage.stage_type}-${stageIndex}`}
                      points={points(stage.coordinates, view, mapSize)}
                      stroke={drone?.color ?? "#6d28d9"}
                      strokeWidth={isSurvey ? (isSelected ? 6 : 3.5) : (isSelected ? 3.5 : 2.5)}
                      strokeDasharray={isSurvey ? undefined : "9 7"}
                      opacity={isSurvey ? (isSelected ? 1 : 0.78) : (isSelected ? 0.82 : 0.58)}
                    >
                      <title>{drone?.name ?? dronePlan.drone_id}: {isSurvey ? "съёмка" : "перелёт"}</title>
                    </polyline>
                  );
                }));
            })}
            {radiusAnalysis?.common_survey_route && (
              <polyline
                points={points(radiusAnalysis.common_survey_route, view, mapSize)}
                stroke="#f97316"
                strokeWidth={3}
                strokeDasharray="8 5"
                opacity={0.95}
                pointerEvents="none"
              >
                <title>{radiusAnalysis.scenarios.at(-1)?.radius_m === 0 ? "Съёмочная ломаная без ограничения радиуса; не полное задание БВС" : "Условный съёмочный маршрут для наибольшего заданного радиуса; не полное задание БВС"}</title>
              </polyline>
            )}
            {plan?.schedule?.resolved_conflicts.map((conflict, index) => {
              const point = coordinateToMapPoint(conflict.coordinate, view, mapSize);
              const label = conflict.conflict_type === "route"
                ? "Конфликт маршрутов устранён"
                : conflict.conflict_type === "flight_site"
                  ? "Конфликт площадки устранён"
                  : "Конфликт аварийного коридора устранён";
              return (
                <g
                  key={`${conflict.delayed_drone_id}-${conflict.delayed_segment_id}-${conflict.conflicting_segment_id}-${index}`}
                  transform={`translate(${point.x} ${point.y})`}
                  pointerEvents="none"
                >
                  <circle r={9} fill="#0f766e" fillOpacity={0.18} stroke="#0f766e" strokeWidth={2} />
                  <path d="M -4 0 L -1 3 L 5 -4" stroke="#0f766e" strokeWidth={2} />
                  <title>{label}: {conflict.nominal_distance_m.toFixed(1)} / {conflict.required_distance_m.toFixed(1)} м · задержка {conflict.applied_delay_minutes.toFixed(1)} мин</title>
                </g>
              );
            })}
          </g>
          <g className="site-layer">
            {sites.map((flightSite, siteIndex) => {
              const point = coordinateToMapPoint(flightSite.coordinate, view, mapSize);
              const nearbyBefore = sites.slice(0, siteIndex).filter((site) => {
                const other = coordinateToMapPoint(site.coordinate, view, mapSize);
                return Math.hypot(other.x - point.x, other.y - point.y) < 20;
              }).length;
              return (
                <g key={flightSite.site_id} transform={`translate(${point.x} ${point.y})`}>
                  <circle r={8} fill={siteColors[flightSite.role]} stroke="#fff" strokeWidth={2.5} />
                  <title>{flightSite.name} · {siteLabels[flightSite.role]}</title>
                  <text className="site-label" x={12} y={4 + nearbyBefore * 16}>{flightSite.name} · {siteLabels[flightSite.role]}</text>
                </g>
              );
            })}
          </g>
        </svg>
        <div className="map-compass" title="Север сверху. Карта не вращается" aria-label="Компас: север сверху"><b>С</b><span>▲</span></div>
        <div className="map-controls">
          <div className="basemap-switch" role="group" aria-label="Подложка карты">
            <button type="button" aria-pressed={basemap === "streets"} onClick={() => changeBasemap("streets")}><Map size={15} /> Карта</button>
            <button type="button" aria-pressed={basemap === "satellite"} onClick={() => changeBasemap("satellite")}>Спутник</button>
            <button type="button" aria-pressed={basemap === "terrain"} onClick={() => changeBasemap("terrain")} title="Топографическая карта: рельеф, горизонтали и отметки высот"><Mountain size={15} /> Рельеф</button>
          </div>
          <button type="button" aria-label="Приблизить" onClick={() => setView((current) => zoomAtPoint(current, 1, { x: mapSize.width / 2, y: mapSize.height / 2 }, mapSize))}><Plus size={17} /></button>
          <button type="button" aria-label="Отдалить" onClick={() => setView((current) => zoomAtPoint(current, -1, { x: mapSize.width / 2, y: mapSize.height / 2 }, mapSize))}><Minus size={17} /></button>
          <button type="button" aria-label="Показать всё" title="Подобрать масштаб, чтобы показать все видимые зоны, площадки и маршруты" onClick={() => setView(fitCoordinates(allCoordinates, mapSize))}><Crosshair size={15} /></button>
          {editMode && <button type="button" aria-label="Удалить незакреплённые" title="Удалить все зоны без защиты от удаления" onClick={onClearZones}><Trash2 size={15} /></button>}
        </div>
        {failedBasemap === basemap && <div className="map-layer-notice" role="status">Часть карты не загрузилась. Проверьте подключение или выберите другую подложку.</div>}
        {pendingSiteRole && <div className="placement-hint"><MapPinPlus size={17} /> Укажите точку: {siteLabels[pendingSiteRole]}</div>}
        <div className={`feasibility-card ${plan?.feasibility?.status === "infeasible" ? "danger" : ""}`}><b>Проверка выполнимости</b><span>{feasibility}</span></div>
        <div className="map-attribution">{basemap === "satellite" ? <>Tiles © Esri · Maxar · Earthstar Geographics · GIS User Community · <a href="https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer" target="_blank" rel="noreferrer">Источники снимков</a></> : <>Данные © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a>{basemap === "terrain" && <> · SRTM · <a href="https://opentopomap.org/about" target="_blank" rel="noreferrer">OpenTopoMap</a> (<a href="https://creativecommons.org/licenses/by-sa/3.0/" target="_blank" rel="noreferrer">CC BY-SA</a>)</>}</>}</div>
      </div>
    </section>
  );
}

function ZoneShape({ zone, view, mapSize }: { zone: ImportedZone; view: MapView; mapSize: MapSize }) {
  const style = zoneStyle(zone.zone_type);
  if (zone.geometry_type === "point") {
    const point = coordinateToMapPoint(zone.coordinates[0], view, mapSize);
    return <circle cx={point.x} cy={point.y} r={5} fill={style.fill} stroke={style.stroke}><title>{zone.name}</title></circle>;
  }
  if (zone.geometry_type === "line_string") {
    return <polyline points={points(zone.coordinates, view, mapSize)} fill="none" stroke={style.stroke} strokeWidth={3}><title>{zone.name}</title></polyline>;
  }
  return (
    <path d={polygonPath(zone, view, mapSize)} fill={style.fill} stroke={style.stroke} strokeWidth={2.5} fillRule="evenodd" opacity={zone.zone_type === "work_area" && zone.enabled === false ? 0.3 : 1}>
      <title>{zone.name}{zone.altitude_text ? ` · ${zone.altitude_text}` : ""}</title>
    </path>
  );
}

function polygonPath(zone: ImportedZone, view: MapView, mapSize: MapSize): string {
  const rings = [zone.coordinates, ...zone.interior_rings];
  return rings.map((ring) => `${path(ring, view, mapSize)} Z`).join(" ");
}

function path(coordinates: Coordinate[], view: MapView, mapSize: MapSize): string {
  return coordinates.map((coordinate, index) => {
    const point = coordinateToMapPoint(coordinate, view, mapSize);
    return `${index === 0 ? "M" : "L"}${point.x.toFixed(1)} ${point.y.toFixed(1)}`;
  }).join(" ");
}

function points(coordinates: Coordinate[], view: MapView, mapSize: MapSize): string {
  return coordinates.map((coordinate) => {
    const point = coordinateToMapPoint(coordinate, view, mapSize);
    return `${point.x.toFixed(1)},${point.y.toFixed(1)}`;
  }).join(" ");
}

function zoneStyle(zoneType: ImportedZone["zone_type"]): { fill: string; stroke: string } {
  if (zoneType === "no_fly_zone") return { fill: "#f43f5e66", stroke: "#be123c" };
  if (zoneType === "allowed_airspace") return { fill: "#60a5fa44", stroke: "#2563eb" };
  if (zoneType === "obstacle") return { fill: "#f59e0b99", stroke: "#b45309" };
  return { fill: "#22c55e44", stroke: "#15803d" };
}
