import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Header } from "@/app/Header";

describe("Header", () => {
  it("keeps the planning action available and reports its status", () => {
    const calculate = vi.fn();

    render(
      <Header
        status="Сценарий готов"
        revision={3}
        theme="dark"
        calculating={false}
        onOpenShowcase={vi.fn()}
        onCalculate={calculate}
        onToggleTheme={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Рассчитать план" }));

    expect(calculate).toHaveBeenCalledOnce();
    expect(screen.getByRole("status")).toHaveTextContent("Версия плана 3");
    expect(screen.getByRole("button", { name: "Светлая" })).toBeVisible();
  });

  it("disables duplicate submissions while calculation is running", () => {
    render(
      <Header
        status="Выполняется оптимизация"
        revision={1}
        theme="light"
        calculating
        onOpenShowcase={vi.fn()}
        onCalculate={vi.fn()}
        onToggleTheme={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "Рассчитываем…" })).toBeDisabled();
  });
});
