import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { usePlanner } from "./usePlanner";

describe("planner workspace", () => {
  beforeEach(() => window.localStorage.clear());

  it("saves and restores the existing scenario format and selected aircraft", () => {
    const { result } = renderHook(usePlanner);
    const droneId = result.current.drones[0].id;

    act(() => result.current.handleUpdateDrone(droneId, { battery: 73 }));
    act(() => result.current.setSelectedDroneId(droneId));
    act(() => result.current.saveScenario());

    const snapshot = JSON.parse(window.localStorage.getItem("geoplan-scenario")!);
    expect(snapshot.version).toBe(3);
    expect(snapshot.drones.find((drone: { id: string }) => drone.id === droneId).battery).toBe(73);

    act(() => result.current.handleUpdateDrone(droneId, { battery: 42 }));
    act(() => result.current.loadScenario());

    expect(result.current.drones.find((drone) => drone.id === droneId)?.battery).toBe(73);
    expect(result.current.selectedDroneId).toBe(droneId);
    expect(result.current.status).toBe("Сценарий восстановлен — требуется новый расчёт");
  });

  it("preserves all geometry above the former 500-object threshold", () => {
    const { result } = renderHook(usePlanner);
    const source = result.current.scenario.workAreas[0];
    const zones = Array.from({ length: 501 }, (_, index) => ({ ...source, zone_id: `field-${index}` }));
    act(() => result.current.setImportedZones(zones));
    act(() => result.current.saveScenario());
    const snapshot = JSON.parse(window.localStorage.getItem("geoplan-scenario")!);
    expect(snapshot.importedZones).toHaveLength(501);
    expect(snapshot.workAreaSource).toBe("imported");
    act(() => result.current.setImportedZones([]));
    act(() => result.current.loadScenario());
    expect(result.current.importedZones).toHaveLength(501);
    expect(result.current.workAreaSource).toBe("imported");
  });

  it("does not restore demo work when an imported job is cleared, saved and reopened", async () => {
    const { result } = renderHook(usePlanner);
    act(() => result.current.setImportedZones([result.current.scenario.workAreas[0]]));
    act(() => result.current.saveScenario());
    act(() => result.current.loadScenario());
    act(() => result.current.setImportedZones([]));
    expect(result.current.mapZones).toEqual([]);
    act(() => result.current.saveScenario());
    act(() => result.current.handleScenarioChange(result.current.scenarioId));
    expect(result.current.mapZones.length).toBeGreaterThan(0);
    act(() => result.current.loadScenario());
    expect(result.current.mapZones).toEqual([]);
    await act(async () => result.current.handleCalculate());
    expect(result.current.status).toContain("Выберите хотя бы одну рабочую область");
  });

  it("reports storage exhaustion without replacing the last complete snapshot", () => {
    const { result } = renderHook(usePlanner);
    act(() => result.current.saveScenario());
    const previous = window.localStorage.getItem("geoplan-scenario");
    const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("Full", "QuotaExceededError");
    });
    try {
      act(() => result.current.saveScenario());
      expect(result.current.status).toContain("Недостаточно места");
      expect(window.localStorage.getItem("geoplan-scenario")).toBe(previous);
    } finally { write.mockRestore(); }
  });

  it("identifies legacy snapshots whose imported work geometry cannot be recovered", () => {
    const { result } = renderHook(usePlanner);
    act(() => result.current.saveScenario());
    const snapshot = JSON.parse(window.localStorage.getItem("geoplan-scenario")!);
    delete snapshot.workAreaSource;
    window.localStorage.setItem("geoplan-scenario", JSON.stringify(snapshot));
    act(() => result.current.loadScenario());
    expect(result.current.status).toContain("Старое сохранение");
    expect(result.current.status).toContain("демосценарий");
  });

  it("invalidates the result when a drone is edited and preserves the remaining fleet", () => {
    const { result } = renderHook(usePlanner);
    const [first, ...remaining] = result.current.drones;

    act(() => result.current.handleUpdateDrone(first.id, { enabled: false }));

    expect(result.current.drones[0].enabled).toBe(false);
    expect(result.current.drones.slice(1)).toEqual(remaining);
    expect(result.current.plan).toBeNull();
    expect(result.current.status).toBe("Параметры парка изменены — требуется новый расчёт");
  });

  it("adds a model batch and clones the selected aircraft without copying private site bindings", () => {
    const { result } = renderHook(usePlanner);
    act(() => result.current.handleAddDrone("201", 2));
    expect(result.current.drones).toHaveLength(5);
    const added = result.current.drones.slice(-2);
    expect(added.every((drone) => drone.modelId === "201")).toBe(true);
    act(() => result.current.handleUpdateDrone(added[0].id, { battery: 66 }));
    act(() => result.current.setSelectedDroneId(added[0].id));
    act(() => result.current.handleCloneDrone());
    const copied = result.current.drones.at(-1)!;
    expect(copied.battery).toBe(66);
    expect(copied.id).not.toBe(added[0].id);
    expect(result.current.selectedDroneId).toBe(copied.id);
    expect(result.current.sites.some((site) => site.assignedDroneId === copied.id)).toBe(false);
    act(() => result.current.saveScenario());
    act(() => result.current.handleRemoveDrone());
    act(() => result.current.loadScenario());
    expect(result.current.drones.at(-1)?.modelId).toBe("201");
    expect(result.current.drones.at(-1)?.id).toBe(copied.id);
  });
});
