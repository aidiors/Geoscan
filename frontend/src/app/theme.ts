import { useEffect, useState } from "react";

export type Theme = "light" | "dark";

export function useTheme() {
  const [theme, setTheme] = useState<Theme>(() => window.localStorage.getItem("geoplan-theme") === "dark" ? "dark" : "light");
  useEffect(() => {
    document.body.dataset.theme = theme;
    window.localStorage.setItem("geoplan-theme", theme);
  }, [theme]);

  return [theme, setTheme] as const;
}
