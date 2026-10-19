import { afterEach, describe, expect, it, vi } from "vitest";
import type { PlanRequest } from "@/shared/api/types";
import { createPlan } from "./client";

describe("planning request deadline", () => {
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("keeps a thirty-minute request alive past the old five-minute timeout", async () => {
    vi.useFakeTimers();
    let finish!: (response: Response) => void;
    let signal!: AbortSignal;
    vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => {
      signal = init.signal!;
      return new Promise<Response>((resolve) => { finish = resolve; });
    }));
    const result = createPlan({ planning_time_limit_seconds: 1800 } as PlanRequest);
    await vi.advanceTimersByTimeAsync(301_000);
    expect(signal.aborted).toBe(false);
    finish(new Response("{}", { headers: { "Content-Type": "application/json" } }));
    await expect(result).resolves.toEqual({});
    expect(vi.getTimerCount()).toBe(0);
  });

  it("aborts after the requested budget and delivery grace, and honors manual stop", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => new Promise<Response>(
      (_resolve, reject) => init.signal?.addEventListener("abort", () => reject(new Error("aborted"))),
    )));
    const timed = createPlan({ planning_time_limit_seconds: 60 } as PlanRequest).catch((error: Error) => error);
    await vi.advanceTimersByTimeAsync(90_000);
    expect(await timed).toMatchObject({ code: "planning_time_limit" });
    const controller = new AbortController();
    const stopped = createPlan({ planning_time_limit_seconds: 1800 } as PlanRequest, undefined,
      controller.signal).catch((error: Error) => error);
    controller.abort();
    expect(await stopped).toMatchObject({ code: "planning_cancelled" });
    expect(vi.getTimerCount()).toBe(0);
  });
});
