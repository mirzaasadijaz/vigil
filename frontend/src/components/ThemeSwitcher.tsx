import { useEffect, useRef, useState } from "react";
import { Palette, Check, Sun, Moon } from "lucide-react";
import { useTheme } from "../contexts/ThemeContext";
import { cx } from "./ui";

export function ThemeSwitcher() {
  const { theme, setTheme, themes } = useTheme();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onClick = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onClick); document.removeEventListener("keydown", onKey); };
  }, []);

  const current = themes.find((t) => t.id === theme);

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        title="Change theme"
        aria-label="Change theme"
        className="flex min-h-[40px] items-center gap-2 rounded-md border border-border bg-base px-3 py-2 text-xs text-muted transition-colors hover:text-tx hover:border-accent/40"
      >
        <Palette size={14} className="text-accent" />
        <span className="hidden sm:inline font-medium text-tx">{current?.name ?? "Theme"}</span>
        <span className="h-3 w-3 rounded-full ring-1 ring-white/20" style={{ backgroundColor: current?.swatch }} />
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-60 origin-top-right rounded-lg border border-border bg-floating p-2 shadow-2xl shadow-black/50 animate-[fadeIn_0.15s_ease-out]">
          <p className="px-2 py-1.5 text-[10px] font-bold uppercase tracking-widest text-faint">Appearance</p>
          <div className="space-y-0.5">
            {themes.map((t) => {
              const active = t.id === theme;
              return (
                <button
                  key={t.id}
                  onClick={() => { setTheme(t.id); setOpen(false); }}
                  className={cx(
                    "flex w-full items-center gap-3 rounded-md px-2 py-2 text-left text-sm transition-colors",
                    active ? "bg-overlay text-th" : "text-muted hover:bg-hover hover:text-tx"
                  )}
                >
                  <span className="relative flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-white/10" style={{ backgroundColor: t.base }}>
                    <span className="h-3 w-3 rounded-full" style={{ backgroundColor: t.swatch }} />
                  </span>
                  <span className="flex-1 font-medium">{t.name}</span>
                  {t.dark ? <Moon size={12} className="text-faint" /> : <Sun size={12} className="text-idle" />}
                  {active && <Check size={14} className="text-accent" />}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
