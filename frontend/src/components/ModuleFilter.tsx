import { Layers } from "lucide-react";
import { MODULES } from "../lib/modules";
import { cx } from "./ui";

/** Compact module selector used across Overview / Analytics / Live Feed. */
export function ModuleFilter({ value, onChange, className = "" }: {
  value: string;                       // module id or "all"
  onChange: (v: string) => void;
  className?: string;
}) {
  return (
    <label className={cx("flex items-center gap-2 rounded-md border border-border bg-base px-3 py-2", className)}>
      <Layers size={14} className="text-accent" />
      <span className="text-[10px] font-bold uppercase tracking-wider text-faint">Module</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="bg-transparent text-sm text-tx outline-none cursor-pointer">
        <option value="all" className="bg-surface">All Modules</option>
        {MODULES.map((m) => (
          <option key={m.id} value={m.id} className="bg-surface">{m.name}</option>
        ))}
      </select>
    </label>
  );
}
