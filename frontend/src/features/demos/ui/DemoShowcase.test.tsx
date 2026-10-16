import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DemoShowcase } from "@/features/demos/ui/DemoShowcase";

afterEach(cleanup);

describe("DemoShowcase", () => {
  it("presents curated cases and launches the selected case", () => {
    const load = vi.fn();
    const run = vi.fn();
    render(
      <DemoShowcase
        open
        selectedCaseId="safe-urban-survey"
        plan={null}
        calculating={false}
        onClose={vi.fn()}
        onLoad={load}
        onRun={run}
      />,
    );

    expect(screen.getByRole("dialog", { name: "Витрина возможностей GeoPlan" })).toBeVisible();
    expect(screen.getAllByRole("button", { name: /Управляемый отказ: шторм/ })).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: /Группа БВС над кварталами/ }));
    expect(load).toHaveBeenCalledWith("multi-drone-city");

    fireEvent.click(screen.getByRole("button", { name: "Загрузить и рассчитать" }));
    expect(run).toHaveBeenCalledWith("safe-urban-survey");
  });

  it("does not render while closed", () => {
    render(
      <DemoShowcase
        open={false}
        selectedCaseId="safe-urban-survey"
        plan={null}
        calculating={false}
        onClose={vi.fn()}
        onLoad={vi.fn()}
        onRun={vi.fn()}
      />,
    );

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
