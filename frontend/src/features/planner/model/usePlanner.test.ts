import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

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

  it("invalidates the result when a drone is edited and preserves the remaining fleet", () => {
    const { result } = renderHook(usePlanner);
    const [first, ...remaining] = result.current.drones;

    act(() => result.current.handleUpdateDrone(first.id, { enabled: false }));

    expect(result.current.drones[0].enabled).toBe(false);
    expect(result.current.drones.slice(1)).toEqual(remaining);
    expect(result.current.plan).toBeNull();
    expect(result.current.status).toBe("Параметры парка изменены — требуется новый расчёт");
  });
});
