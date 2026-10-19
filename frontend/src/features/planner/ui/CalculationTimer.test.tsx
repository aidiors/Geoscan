import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CalculationTimer } from "./CalculationTimer";

afterEach(() => vi.useRealTimers());

describe("calculation elapsed time", () => {
  it("continues across result updates and releases its clock on completion", () => {
    vi.useFakeTimers();
    const view = render(<CalculationTimer />);
    act(() => vi.advanceTimersByTime(65_000));
    expect(screen.getByRole("timer")).toHaveTextContent("Считаем 1:05");
    view.rerender(<CalculationTimer />);
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByRole("timer")).toHaveTextContent("Считаем 1:06");
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
