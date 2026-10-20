import { useEffect, useState } from "react";

export function CalculationTimer() {
  const [started] = useState(() => performance.now());
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const timer = window.setInterval(() => setElapsed(Math.floor((performance.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [started]);
  return <span className="calculation-timer" role="timer" aria-label="Время расчёта" aria-live="off" title="Прошедшее время поиска. Оставшееся время не прогнозируется.">
    Считаем {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}
  </span>;
}
