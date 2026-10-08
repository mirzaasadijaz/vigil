import { useState } from "react";
import type { FormEvent } from "react";
import { useAuth } from "../contexts/AuthContext";
import { Button, Spinner, cx } from "../components/ui";
import { ThemeSwitcher } from "../components/ThemeSwitcher";
import {
  Camera, ScanFace, BarChart3, BellRing,
  User, Lock, Eye, EyeOff, AlertCircle, CheckCircle2,
} from "lucide-react";

const FEATURES = [
  { icon: Camera, title: "Live Monitoring", desc: "Multi-camera feeds with on-frame AI overlays" },
  { icon: ScanFace, title: "Smart Attendance", desc: "Face recognition check-in / check-out" },
  { icon: BarChart3, title: "Module Analytics", desc: "Head-count, occupancy, idle & more" },
  { icon: BellRing, title: "Real-time Alerts", desc: "Intrusion, unknown faces, safety events" },
];

export default function LoginView() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email, password);
    } catch (err: any) {
      setError(err.message || "Invalid credentials");
    } finally {
      setLoading(false);
    }
  };

  const fieldCls =
    "min-h-[46px] w-full rounded-md border border-base bg-base py-2.5 pl-10 pr-3 text-sm text-tx placeholder:text-faint outline-none transition-all duration-200 focus:border-accent focus:ring-2 focus:ring-accent/25";

  return (
    <div className="grid min-h-dvh bg-base lg:grid-cols-[1.05fr_0.95fr]">
      {/* ── Brand panel (desktop) ───────────────────────────────────────── */}
      <aside className="relative hidden flex-col justify-between overflow-hidden border-r border-border bg-content p-12 lg:flex">
        {/* faint grid + accent glow */}
        <div className="pointer-events-none absolute inset-0 opacity-[0.15] bg-[linear-gradient(var(--color-border)_1px,transparent_1px),linear-gradient(90deg,var(--color-border)_1px,transparent_1px)] bg-[size:48px_48px]" />
        <div className="pointer-events-none absolute -left-24 -top-24 h-96 w-96 rounded-full bg-accent/15 blur-[120px]" />
        <div className="pointer-events-none absolute -bottom-32 right-0 h-96 w-96 rounded-full bg-accent/10 blur-[130px]" />

        <div className="relative flex items-center gap-3">
          <img src="/logo.png" alt="VIGIL" className="h-14 w-14 shrink-0 rounded-2xl object-cover shadow-lg shadow-accent/30" />
          <div>
            <p className="text-base font-bold tracking-[0.3em] text-th">VIGIL</p>
            <p className="text-[10px] tracking-wider text-faint">WORKPLACE INTELLIGENCE</p>
          </div>
        </div>

        <div className="relative">
          <h2 className="max-w-md text-3xl font-bold leading-tight tracking-tight text-th">
            Your workplace,<br /><span className="text-accent">watched in real time.</span>
          </h2>
          <p className="mt-3 max-w-sm text-sm leading-relaxed text-muted">
            A unified AI surveillance platform — recognition, attendance, and safety analytics across every camera.
          </p>
          <div className="mt-8 grid max-w-md grid-cols-1 gap-3 sm:grid-cols-2">
            {FEATURES.map((f) => (
              <div key={f.title} className="flex items-start gap-3 rounded-lg border border-border bg-surface/60 p-3">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-accent/10 text-accent"><f.icon size={16} /></span>
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-tx">{f.title}</p>
                  <p className="text-[11px] leading-snug text-faint">{f.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="relative flex items-center gap-2 text-xs text-muted">
          <CheckCircle2 size={14} className="text-online" />
          <span>All systems operational</span>
          <span className="ml-auto text-faint">VIGIL v2.0</span>
        </div>
      </aside>

      {/* ── Form panel ──────────────────────────────────────────────────── */}
      <main className="relative flex items-center justify-center px-6 py-12">
        <div className="absolute right-5 top-5"><ThemeSwitcher /></div>

        <div className="w-full max-w-sm animate-[fadeIn_0.3s_ease-out]">
          {/* mobile brand */}
          <div className="mb-8 flex flex-col items-center text-center lg:hidden">
            <img src="/logo.png" alt="VIGIL" className="mb-4 h-16 w-16 rounded-2xl object-cover shadow-xl shadow-accent/30" />
            <p className="text-lg font-bold tracking-[0.3em] text-th">VIGIL</p>
          </div>

          <h1 className="text-2xl font-bold tracking-tight text-th">Sign in</h1>
          <p className="mt-1.5 text-sm text-muted">Access the VIGIL control room.</p>

          <form onSubmit={handleSubmit} className="mt-7 flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <label htmlFor="email" className="text-xs font-semibold uppercase tracking-wider text-muted">Email</label>
              <div className="relative">
                <User size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
                <input id="email" name="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
                  placeholder="Enter email" autoComplete="email" autoFocus required className={fieldCls} />
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <label htmlFor="password" className="text-xs font-semibold uppercase tracking-wider text-muted">Password</label>
              <div className="relative">
                <Lock size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
                <input id="password" name="password" type={showPw ? "text" : "password"} value={password}
                  onChange={(e) => setPassword(e.target.value)} placeholder="Enter password"
                  autoComplete="current-password" required className={cx(fieldCls, "pr-10")} />
                <button type="button" onClick={() => setShowPw((v) => !v)} tabIndex={-1}
                  aria-label={showPw ? "Hide password" : "Show password"}
                  className="absolute right-2 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-faint transition-colors hover:text-tx">
                  {showPw ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            {error && (
              <div role="alert" className="flex items-center gap-2 rounded-md border border-dnd/30 bg-dnd/10 px-3 py-2.5 text-sm text-dnd-text animate-[fadeIn_0.2s_ease-out]">
                <AlertCircle size={15} className="shrink-0" />
                <span>{error}</span>
              </div>
            )}

            <Button type="submit" disabled={loading} className="mt-1 w-full min-h-[46px]">
              {loading ? <span className="flex items-center gap-2"><Spinner size="sm" /> Authenticating…</span> : "Sign In"}
            </Button>
          </form>

          <p className="mt-8 text-center text-[10px] uppercase tracking-widest text-faint">
            Authorized personnel only · Secured access
          </p>
        </div>
      </main>
    </div>
  );
}
