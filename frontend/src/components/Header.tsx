import { LayoutGrid, Moon, Play, Route, Sun } from "lucide-react";

import type { Theme } from "../types";

interface HeaderProps {
  status: string;
  revision: number;
  theme: Theme;
  calculating: boolean;
  onOpenShowcase: () => void;
  onCalculate: () => void;
  onToggleTheme: () => void;
}

export function Header({
  status,
  revision,
  theme,
  calculating,
  onOpenShowcase,
  onCalculate,
  onToggleTheme,
}: HeaderProps) {
  return (
    <header className="app-header">
      <div className="brand">
        <span className="brand-mark"><Route size={21} /></span>
        <span><b>GeoPlan</b><small>Планировщик авиационных работ</small></span>
      </div>
      <div className="header-status" role="status" aria-live="polite">
        <span className="status-dot" />
        <span>{status}</span>
        <span className="header-separator">•</span>
        <span>Версия плана {revision}</span>
      </div>
      <div className="header-actions">
        <button className="theme-button showcase-button" type="button" onClick={onOpenShowcase}>
          <LayoutGrid size={17} />
          <span>Демо-кейсы</span>
        </button>
        <button className="theme-button" type="button" onClick={onToggleTheme}>
          {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
          <span>{theme === "dark" ? "Светлая" : "Тёмная"}</span>
        </button>
        <button className="primary-button calculate-button" type="button" onClick={onCalculate} disabled={calculating}>
          <Play size={17} fill="currentColor" />
          {calculating ? "Рассчитываем…" : "Рассчитать план"}
        </button>
      </div>
    </header>
  );
}
