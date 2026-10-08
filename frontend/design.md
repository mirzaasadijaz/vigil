# Sentinel AI Design System

This document outlines the core design tokens, typography, and reusable components used in the Sentinel AI SPA (Single Page Application) frontend.

## 1. Color Palette

The application uses a dark mode-first aesthetic with glowing neon accents.

### Core Backgrounds
- **App Background**: A deep slate/navy gradient. Linear gradient from `#020617` (Slate 950) to `#0b1120`, mixed with subtle radial glows of Cyan and Purple at the top corners.
- **Card Background**: `#11161f` with 90% opacity (`bg-[#11161f]/90`).
- **Sidebar Background**: `#090d14` with 95% opacity.

### Accents & Gradients
- **Primary (Cyan)**: 
  - Main: `#22d3ee` (Tailwind `cyan-400`)
  - Hover/Light: `#67e8f9` (Tailwind `cyan-300`)
  - Dark/Borders: `cyan-400/20`, `cyan-400/10`
- **Secondary (Violet/Purple)**: `#8b5cf6` (Tailwind `violet-500`)
- **Success (Emerald)**: `#34d399` (Tailwind `emerald-400`)
- **Warning (Amber)**: `#fbbf24` (Tailwind `amber-400`)
- **Danger/Alert (Rose)**: `#fb7185` (Tailwind `rose-400`)

### Text Colors
- **Primary Text**: `text-white` (`#ffffff`) for headings and active states.
- **Secondary Text**: `text-slate-300` and `text-slate-400` for descriptions and subtitles.
- **Muted/Tertiary**: `text-slate-500` for hints, timestamps, and inactive labels.

---

## 2. Typography

We rely on the system sans-serif font stack (Inter, SF Pro, Roboto) for a clean, technical look.

- **Headings (`h1`, `h2`, `h3`)**: `font-semibold tracking-tight text-white`
  - H1: `text-3xl lg:text-4xl`
  - H2: `text-xl sm:text-2xl`
  - H3: `text-lg`
- **Body Text**: `text-sm text-slate-400`
- **Micro-Labels & Categories**: `text-[11px]` or `text-xs uppercase tracking-wider font-semibold text-slate-500`

---

## 3. Reusable Components

The UI is built from a set of foundational, reusable React components to ensure consistency.

### `Card`
A foundational container for grouping content.
- **Style**: Rounded corners (`rounded-[28px]`), faint cyan/white border (`border-cyan-400/10`), blur backdrop (`backdrop-blur-xl`), subtle hover lift and glow (`hover:-translate-y-0.5 hover:shadow-cyan-500/10`).

### `Button`
Interactive elements with three distinct variants.
- **Default (Primary)**: Solid Cyan background (`bg-cyan-300 text-slate-950`).
- **Ghost**: Transparent with subtle border, turns slightly white on hover (`border-cyan-400/15 bg-white/4 text-slate-100`).
- **Subtle**: Similar to Ghost but slightly more opaque.

### `Badge`
Small status indicators.
- **Tones**: `neutral` (gray/white), `success` (emerald), `warning` (amber), `danger` (rose).
- **Style**: Rounded full, uppercase, very small text (`text-[11px] font-semibold tracking-wider uppercase`).

### `Switch` (Toggle)
Used for enabling/disabling AI parameters and settings.
- **Style**: A pill-shaped container that transitions the toggle thumb smoothly from left (`bg-white`) to right (`bg-cyan-200`) when active.

### `SectionTitle`
Standardized header for sections inside Cards.
- **Structure**: A title, an optional subtitle, and an optional right-aligned action area (like buttons).

### `MetricCard`
Used for top-level statistics (KPIs).
- **Structure**: Uses `Card` base. Displays a Label, large Value, small Hint, and a distinct Icon/Accent box.

---

## 4. SPA Architecture & Folder Structure

To maintain a clean and scalable React codebase, the application is divided into:

```text
frontend/src/
├── components/
│   ├── ui/          # Generic reusable components (Button, Card, Badge, Switch)
│   └── layout/      # Sidebar, TopBar, Main Layout wrapper
├── views/           # Page-level components corresponding to navigation items
│   ├── Overview.tsx
│   ├── Operations.tsx
│   ├── Intelligence.tsx
│   ├── Assets.tsx
│   └── Admin.tsx
├── data/            # Mock data, config objects, and constants (alerts, stats)
└── App.tsx          # Main entry point routing between views
```

## 5. Django Integration Strategy

This SPA is built with Vite. To integrate with the Django backend:
1. The frontend will communicate with Django via REST APIs (Django REST Framework) to fetch live data (e.g., active cameras, alert logs).
2. The compiled Vite assets (`dist/`) can be served by Django static files, or Vite can run on its own port (e.g., `5173`) proxying API requests to Django (e.g., `8000`).
