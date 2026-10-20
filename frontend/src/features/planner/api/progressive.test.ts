import { describe, expect, it, vi } from "vitest";
import { readPlanningStream } from "./progressive";
import type { PlanResponse } from "@/shared/api/types";

const plan = { safety_audit: { status: "passed" } } as PlanResponse;

function chunks(text: string): Response {
  const bytes = new TextEncoder().encode(text);
  return new Response(new ReadableStream({ start(controller) {
    // Split every multibyte character and JSON boundary deliberately.
    for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
    controller.close();
  } }), { headers: { "Content-Type": "application/x-ndjson" } });
}

describe("audited planning stream", () => {
  it("delivers the first plan before the stream completes and handles split UTF-8", async () => {
    const received = vi.fn();
    let complete!: () => void;
    const encoder = new TextEncoder();
    const response = new Response(new ReadableStream({ start(controller) {
      controller.enqueue(encoder.encode(JSON.stringify({ kind: "candidate", plan }) + "\n"));
      complete = () => { controller.enqueue(encoder.encode(JSON.stringify({ kind: "completed", plan }) + "\n")); controller.close(); };
    } }));
    const result = readPlanningStream(response, received);
    await vi.waitFor(() => expect(received).toHaveBeenCalledOnce());
    complete();
    expect(await result).toEqual(plan);
    await expect(readPlanningStream(chunks(JSON.stringify({kind: "error", error: { code: "unplannable_segment", message: "Нет доступных БВС" }}) + "\n"), vi.fn())).rejects.toThrow("Нет доступных БВС");
  });

  it("retains a delivered result when a later error arrives and rejects failed audits", async () => {
    const received = vi.fn();
    const events = [{ kind: "candidate", plan }, { kind: "error", error: { message: "Поиск прерван" } }];
    await expect(readPlanningStream(chunks(events.map((e) => JSON.stringify(e)).join("\n")), received)).rejects.toThrow("Поиск прерван");
    expect(received).toHaveBeenCalledOnce();
    const invalid = { ...plan, safety_audit: { status: "failed" } };
    const rejected = vi.fn();
    await expect(readPlanningStream(chunks(JSON.stringify({ kind: "candidate", plan: invalid })), rejected)).rejects.toThrow("аудит");
    expect(rejected).not.toHaveBeenCalled();
  });
});
