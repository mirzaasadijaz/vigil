import { createContext, useContext, useEffect, useState } from "react";

export type ThemeId = "dark" | "light" | "cisco" | "nord" | "emerald" | "amethyst";

export interface ThemeMeta {
  id: ThemeId;
  name: string;
  swatch: string;   // accent
  base: string;     // surface preview
  dark: boolean;
}

export const THEMES: ThemeMeta[] = [
  { id: "dark",     name: "Discord Dark", swatch: "#5865f2", base: "#2b2d31", dark: true },
  { id: "light",    name: "Light",        swatch: "#5865f2", base: "#ffffff", dark: false },
  { id: "cisco",    name: "Cisco Blue",   swatch: "#049fd9", base: "#18222e", dark: true },
  { id: "nord",     name: "Midnight",     swatch: "#88c0d0", base: "#353b48", dark: true },
  { id: "emerald",  name: "Emerald",      swatch: "#2bd576", base: "#122019", dark: true },
  { id: "amethyst", name: "Amethyst",     swatch: "#a855f7", base: "#1c1827", dark: true },
];

const STORAGE_KEY = "vigil-theme";

interface ThemeCtx { theme: ThemeId; setTheme: (t: ThemeId) => void; themes: ThemeMeta[]; }
const Ctx = createContext<ThemeCtx>({ theme: "dark", setTheme: () => {}, themes: THEMES });

function applyTheme(t: ThemeId) {
  document.documentElement.dataset.theme = t;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<ThemeId>(() => {
    const saved = (typeof localStorage !== "undefined" && localStorage.getItem(STORAGE_KEY)) as ThemeId | null;
    return saved && THEMES.some((t) => t.id === saved) ? saved : "dark";
  });

  useEffect(() => { applyTheme(theme); }, [theme]);

  const setTheme = (t: ThemeId) => {
    setThemeState(t);
    try { localStorage.setItem(STORAGE_KEY, t); } catch { /* ignore */ }
  };

  return <Ctx.Provider value={{ theme, setTheme, themes: THEMES }}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx);
