import type { ImportedZone } from "@/shared/api/types";

export function mergeZones(current: ImportedZone[], incoming: ImportedZone[]): ImportedZone[] {
  const used = new Set(current.map((zone) => zone.zone_id));
  return [...current, ...incoming.map((zone) => {
    let id = zone.zone_id;
    let ordinal = 2;
    while (used.has(id)) id = `${zone.zone_id.slice(0, 48)}-import-${ordinal++}`;
    used.add(id);
    return id === zone.zone_id ? zone : {
      ...zone, zone_id: id,
      metadata: { ...zone.metadata, source_zone_id: zone.metadata?.source_zone_id ?? zone.zone_id },
    };
  })];
}
