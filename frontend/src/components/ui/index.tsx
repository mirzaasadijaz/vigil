import React, { useEffect, useRef } from "react";

export const cx = (...classes: (string | false | null | undefined)[]) =>
  classes.filter(Boolean).join(" ");

// ─── StatusDot ───────────────────────────────────────────────────────────────
// Discord-style presence/health dot with the "notch" ring punched into the surface.
type DotStatus = "online" | "idle" | "dnd" | "offline";
export function StatusDot({ status = "offline", size = 10, notch = false, pulse = false, className = "" }:
  { status?: DotStatus; size?: number; notch?: boolean; pulse?: boolean; className?: string }) {
  const color: Record<DotStatus, string> = {
    online: "var(--color-online)", idle: "var(--color-idle)",
    dnd: "var(--color-dnd)", offline: "var(--color-faint)",
  };
  return (
    <span
      className={cx("inline-block shrink-0 rounded-full", notch && "dot-notch", pulse && status === "dnd" && "animate-pulse-ring", className)}
      style={{ width: size, height: size, backgroundColor: color[status] }}
    />
  );
}

// ─── Card ────────────────────────────────────────────────────────────────────
interface CardProps extends React.HTMLAttributes<HTMLDivElement> { interactive?: boolean; }
export const Card = React.forwardRef<HTMLDivElement, CardProps>(
  function Card({ children, className = "", interactive = false, ...rest }, ref) {
    return (
      <div
        ref={ref}
        className={cx(
          "rounded-lg border border-border bg-surface shadow-lg shadow-black/20 transition-all duration-200",
          interactive && "hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-xl hover:shadow-black/30",
          className
        )}
        {...rest}
      >
        {children}
      </div>
    );
  }
);

// ─── Button ──────────────────────────────────────────────────────────────────
interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "ghost" | "danger" | "subtle";
  size?: "sm" | "md";
}
export function Button({ children, className = "", variant = "primary", size = "md", ...props }: ButtonProps) {
  const styles =
    variant === "ghost"
      ? "border border-border bg-transparent text-tx hover:bg-white/5"
      : variant === "subtle"
      ? "border border-border bg-white/5 text-tx hover:bg-white/10"
      : variant === "danger"
      ? "bg-dnd/90 text-white hover:bg-dnd"
      : "bg-accent text-white hover:bg-accent-hover font-semibold";
  const sz = size === "sm" ? "px-3 py-1.5 text-xs" : "px-5 py-2.5 text-sm";
  return (
    <button
      className={cx("inline-flex items-center justify-center gap-2 rounded-md transition-all duration-200 active:scale-[0.98] disabled:opacity-50 disabled:cursor-not-allowed", sz, styles, className)}
      {...props}
    >
      {children}
    </button>
  );
}

// ─── Badge ───────────────────────────────────────────────────────────────────
type BadgeTone = "success" | "warning" | "danger" | "info" | "neutral" | "blue";
export function Badge({ children, tone = "neutral" }: { children: React.ReactNode; tone?: BadgeTone }) {
  const styles: Record<BadgeTone, string> = {
    success: "border-online/30 bg-online/10 text-online-text",
    warning: "border-idle/30 bg-idle/10 text-idle-text",
    danger:  "border-dnd/30 bg-dnd/10 text-dnd-text",
    info:    "border-accent-soft/30 bg-accent-soft/10 text-accent-text",
    blue:    "border-accent/40 bg-accent/15 text-accent-text",
    neutral: "border-border bg-white/5 text-muted",
  };
  return (
    <span className={cx("inline-flex items-center rounded-full border px-2.5 py-0.5 text-[10px] font-semibold tracking-widest uppercase", styles[tone])}>
      {children}
    </span>
  );
}

// ─── Input ───────────────────────────────────────────────────────────────────
interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
}
export function Input({ label, error, className = "", ...props }: InputProps) {
  return (
    <div className="flex flex-col gap-1.5">
      {label && <label className="text-xs font-semibold tracking-wider text-muted uppercase">{label}</label>}
      <input
        className={cx(
          "min-h-[44px] w-full rounded-md border border-base bg-base px-4 py-2.5 text-sm text-tx placeholder:text-faint outline-none transition-all duration-200",
          "focus:border-accent focus:ring-2 focus:ring-accent/20",
          error && "border-dnd focus:border-dnd focus:ring-dnd/20",
          className
        )}
        {...props}
      />
      {error && <p className="text-xs text-dnd-text">{error}</p>}
    </div>
  );
}

// ─── Select ──────────────────────────────────────────────────────────────────
interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  options: { value: string; label: string }[];
}
export function Select({ label, options, className = "", ...props }: SelectProps) {
  return (
    <div className="flex flex-col gap-1.5">
      {label && <label className="text-xs font-semibold tracking-wider text-muted uppercase">{label}</label>}
      <select
        className={cx("min-h-[44px] w-full rounded-md border border-base bg-base px-4 py-2.5 text-sm text-tx outline-none transition-all duration-200 focus:border-accent focus:ring-2 focus:ring-accent/20", className)}
        {...props}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value} className="bg-surface">{o.label}</option>
        ))}
      </select>
    </div>
  );
}

// ─── Switch ──────────────────────────────────────────────────────────────────
export function Switch({ checked, onChange, label }: { checked: boolean; onChange: () => void; label?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={onChange}
      className="flex min-h-[44px] w-full items-center justify-between rounded-md border border-border bg-base/60 px-4 py-3 text-left transition-all duration-200 hover:bg-base"
    >
      {label && <span className="text-sm text-tx">{label}</span>}
      <span className={cx("relative inline-flex h-6 w-11 items-center rounded-full transition-colors duration-200", checked ? "bg-accent" : "bg-border")}>
        <span className={cx("inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200", checked ? "translate-x-6" : "translate-x-1")} />
      </span>
    </button>
  );
}

// ─── Spinner ─────────────────────────────────────────────────────────────────
export function Spinner({ size = "md" }: { size?: "sm" | "md" | "lg" }) {
  const s = size === "sm" ? "h-4 w-4" : size === "lg" ? "h-8 w-8" : "h-6 w-6";
  return (
    <svg className={cx("animate-spin text-accent", s)} fill="none" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );
}

// ─── Modal ───────────────────────────────────────────────────────────────────
interface ModalProps { open: boolean; onClose: () => void; title: string; children: React.ReactNode; size?: "lg" | "xl" | "2xl" }
export function Modal({ open, onClose, title, children, size = "lg" }: ModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (open) document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);
  if (!open) return null;
  const maxW = size === "2xl" ? "max-w-2xl" : size === "xl" ? "max-w-xl" : "max-w-lg";
  return (
    <div
      ref={overlayRef}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      onClick={(e) => e.target === overlayRef.current && onClose()}
    >
      <div className={cx("flex max-h-[90vh] w-full flex-col rounded-lg border border-border bg-surface shadow-2xl shadow-black/70", maxW)}>
        <div className="flex items-center justify-between border-b border-border px-6 py-4">
          <h2 className="text-base font-semibold text-th">{title}</h2>
          <button onClick={onClose} className="flex h-8 w-8 items-center justify-center rounded-lg text-muted transition-colors hover:bg-white/10 hover:text-white" aria-label="Close">✕</button>
        </div>
        <div className="overflow-y-auto px-6 py-5">{children}</div>
      </div>
    </div>
  );
}

// ─── Toast ───────────────────────────────────────────────────────────────────
interface ToastProps { message: string; type?: "success" | "error" | "info"; onDismiss: () => void }
export function Toast({ message, type = "info", onDismiss }: ToastProps) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 4000);
    return () => clearTimeout(t);
  }, [onDismiss]);
  const styles = type === "success"
    ? "border-online/40 bg-online/10 text-online-text"
    : type === "error"
    ? "border-dnd/40 bg-dnd/10 text-dnd-text"
    : "border-accent/40 bg-accent/10 text-accent-text";
  return (
    <div className={cx("fixed bottom-6 right-6 z-[100] flex items-center gap-3 rounded-xl border px-4 py-3 text-sm shadow-xl backdrop-blur-md", styles)} role="status" aria-live="polite">
      <span>{message}</span>
      <button onClick={onDismiss} className="ml-2 opacity-60 hover:opacity-100">✕</button>
    </div>
  );
}

// ─── Table ───────────────────────────────────────────────────────────────────
interface Column<T> { key: keyof T | string; header: string; render?: (row: T) => React.ReactNode; className?: string }
interface TableProps<T> { columns: Column<T>[]; data: T[]; keyField: keyof T; emptyMessage?: string }
export function Table<T>({ columns, data, keyField, emptyMessage = "No data" }: TableProps<T>) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-elevated">
            {columns.map((col) => (
              <th key={String(col.key)} className={cx("px-4 py-3 text-left text-xs font-semibold tracking-wider text-muted uppercase", col.className)}>
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-elevated">
          {data.length === 0 ? (
            <tr><td colSpan={columns.length} className="px-4 py-10 text-center text-faint">{emptyMessage}</td></tr>
          ) : (
            data.map((row) => (
              <tr key={String(row[keyField])} className="transition-colors duration-150 hover:bg-white/[0.02]">
                {columns.map((col) => (
                  <td key={String(col.key)} className={cx("px-4 py-3 text-tx", col.className)}>
                    {col.render ? col.render(row) : String((row as any)[col.key] ?? "")}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

// ─── SectionTitle ─────────────────────────────────────────────────────────────
export function SectionTitle({ title, subtitle, action }: { title: string; subtitle?: string; action?: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-end justify-between gap-4">
      <div>
        <h2 className="text-xl font-semibold tracking-tight text-th sm:text-2xl">{title}</h2>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {action && <div>{action}</div>}
    </div>
  );
}

// ─── MetricCard ──────────────────────────────────────────────────────────────
type MetricTone = "accent" | "success" | "warning" | "danger";
export function MetricCard({ label, value, hint, icon, tone = "accent", trend }: {
  label: string; value: string | number; hint?: string; icon?: React.ReactNode;
  tone?: MetricTone; trend?: { dir: "up" | "down" | "flat"; text: string };
}) {
  const iconTint: Record<MetricTone, string> = {
    accent:  "bg-accent/10 text-accent",
    success: "bg-online/10 text-online",
    warning: "bg-idle/10 text-idle",
    danger:  "bg-dnd/10 text-dnd",
  };
  const trendColor = trend?.dir === "up" ? "text-online-text" : trend?.dir === "down" ? "text-dnd-text" : "text-faint";
  return (
    <Card interactive className="relative overflow-hidden p-5">
      <span className={cx("absolute inset-x-0 top-0 h-0.5",
        tone === "accent" ? "bg-accent" : tone === "success" ? "bg-online" : tone === "warning" ? "bg-idle" : "bg-dnd")} />
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-xs font-medium uppercase tracking-wider text-muted">{label}</p>
          <p className="mt-2 text-3xl font-bold tracking-tight text-th font-mono">{value}</p>
          <div className="mt-1.5 flex items-center gap-2">
            {trend && <span className={cx("text-xs font-semibold", trendColor)}>{trend.dir === "up" ? "▲" : trend.dir === "down" ? "▼" : "—"} {trend.text}</span>}
            {hint && <p className="text-xs text-faint truncate">{hint}</p>}
          </div>
        </div>
        {icon && (
          <div className={cx("flex h-11 w-11 shrink-0 items-center justify-center rounded-lg", iconTint[tone])}>
            {icon}
          </div>
        )}
      </div>
    </Card>
  );
}
