import { Crosshair, MapPinPlus, MapPinned, Minus, Plus, Trash2 } from "lucide-react";
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
import type { Coordinate, MapView, Point } from "@/shared/geo/types";
import type { DroneProfile, FlightSiteState, SiteRole } from "@/features/planner/model/types";
import type { ImportedZone, PlanResponse, TurnRadiusAnalysisResult } from "@/shared/api/types";
import type { SurveyQualityReport } from "@/shared/api/types";

interface MissionMapProps {
  quality?: SurveyQualityReport | null;
  zones: ImportedZone[];
  sites: FlightSiteState[];
  drones: DroneProfile[];
  plan: PlanResponse | null;
  radiusAnalysis: TurnRadiusAnalysisResult | null;
  selectedDroneId: string;
  pendingSiteRole: SiteRole | null;
  onAddSite: (coordinate: Coordinate) => void;
  onClearZones: () => void;
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
  zones,
  sites,
  drones,
  plan,
  radiusAnalysis,
  selectedDroneId,
  pendingSiteRole,
  onAddSite,
  onClearZones,
}: MissionMapProps) {
  const frameRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const initialFitRef = useRef(false);
  const allCoordinates = useMemo(
    () => [
      ...zones.flatMap((zone) => [...zone.coordinates, ...zone.interior_rings.flat()]),
      ...sites.map((flightSite) => flightSite.coordinate),
      ...(plan?.drone_plans.flatMap((dronePlan) => dronePlan.missions.flatMap((mission) => mission.route)) ?? []),
      ...(plan?.schedule?.resolved_conflicts.map((conflict) => conflict.coordinate) ?? []),
      ...(radiusAnalysis?.common_survey_route ?? []),
    ],
    [plan, radiusAnalysis, sites, zones],
  );
  const [view, setView] = useState<MapView>(() => fitCoordinates(allCoordinates));
  const [mapSize, setMapSize] = useState<MapSize>(DEFAULT_MAP_SIZE);
  const tiles = useMemo(() => visibleTiles(view, mapSize), [mapSize, view]);

  useLayoutEffect(() => {
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
  }, [allCoordinates]);

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

  const feasibility = plan?.feasibility?.message ?? "Маршрут ещё не рассчитан. Зоны и площадки готовы к проверке.";

  return (
    <section className="map-zone">
      <div className="map-title">
        <div><div className="eyebrow"><MapPinned size={15} /> GIS-карта миссий</div><h2>План покрытия территории</h2></div>
        <div className="legend"><span><i className="legend-route" />Съёмка</span><span><i className="legend-transit" />Перелёт</span>{radiusAnalysis?.common_survey_route && <span><i className="legend-radius" />Условный маршрут</span>}<span><i className="legend-target" />Целевая зона</span><span><i className="legend-restricted" />Запретная зона</span></div>
      </div>
      <div ref={frameRef} className={`map-frame ${pendingSiteRole ? "placing-site" : ""}`}>
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
            {tiles.map((tile) => <image key={tile.key} href={tile.href} x={tile.x} y={tile.y} width={256} height={256} />)}
          </g>
          <g className="zone-layer">
            {zones.slice(0, 1_600).map((zone) => <ZoneShape key={zone.zone_id} zone={zone} view={view} mapSize={mapSize} />)}
          </g>
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
                ? "Конфликт маршрутов"
                : conflict.conflict_type === "flight_site"
                  ? "Конфликт площадки"
                  : "Конфликт аварийного коридора";
              return (
                <g
                  key={`${conflict.delayed_drone_id}-${conflict.delayed_segment_id}-${conflict.conflicting_segment_id}-${index}`}
                  transform={`translate(${point.x} ${point.y})`}
                  pointerEvents="none"
                >
                  <circle r={9} fill="#dc2626" fillOpacity={0.18} stroke="#dc2626" strokeWidth={2} />
                  <path d="M -4 -4 L 4 4 M 4 -4 L -4 4" stroke="#dc2626" strokeWidth={2} />
                  <title>{label}: {conflict.nominal_distance_m.toFixed(1)} / {conflict.required_distance_m.toFixed(1)} м · задержка {conflict.applied_delay_minutes.toFixed(1)} мин</title>
                </g>
              );
            })}
          </g>
          <g className="site-layer">
            {sites.map((flightSite) => {
              const point = coordinateToMapPoint(flightSite.coordinate, view, mapSize);
              return (
                <g key={flightSite.site_id} transform={`translate(${point.x} ${point.y})`}>
                  <circle r={8} fill={siteColors[flightSite.role]} stroke="#fff" strokeWidth={2.5} />
                  <text className="site-label" x={12} y={4}>{siteLabels[flightSite.role]}</text>
                </g>
              );
            })}
          </g>
        </svg>
        <div className="map-controls">
          <button type="button" aria-label="Приблизить" onClick={() => setView((current) => zoomAtPoint(current, 1, { x: mapSize.width / 2, y: mapSize.height / 2 }, mapSize))}><Plus size={17} /></button>
          <button type="button" aria-label="Отдалить" onClick={() => setView((current) => zoomAtPoint(current, -1, { x: mapSize.width / 2, y: mapSize.height / 2 }, mapSize))}><Minus size={17} /></button>
          <button type="button" onClick={() => setView(fitCoordinates(allCoordinates, mapSize))}><Crosshair size={15} /> Вписать</button>
          <button type="button" onClick={onClearZones}><Trash2 size={15} /> Очистить зоны</button>
        </div>
        {pendingSiteRole && <div className="placement-hint"><MapPinPlus size={17} /> Укажите точку: {siteLabels[pendingSiteRole]}</div>}
        <div className={`feasibility-card ${plan?.feasibility?.status === "infeasible" ? "danger" : ""}`}><b>Проверка выполнимости</b><span>{feasibility}</span></div>
        <div className="map-attribution">Карта © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a></div>
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
    <path d={polygonPath(zone, view, mapSize)} fill={style.fill} stroke={style.stroke} strokeWidth={2.5} fillRule="evenodd">
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
