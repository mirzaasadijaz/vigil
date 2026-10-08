import { Component, type ErrorInfo, type ReactNode } from "react";

/** Keeps a crash in one view from blanking the whole app (sidebar + top bar stay usable). */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[UI] view crashed:", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="rounded-lg border border-border bg-surface p-6">
        <h2 className="text-base font-semibold text-th">This page hit an error</h2>
        <p className="mt-1 text-sm text-muted">Use the sidebar to go elsewhere, or try again.</p>
        <pre className="mt-3 overflow-x-auto rounded-md bg-base p-3 text-xs text-faint">
          {this.state.error.message}
        </pre>
        <button
          onClick={() => this.setState({ error: null })}
          className="mt-3 rounded-md border border-border px-3 py-1.5 text-sm text-tx hover:bg-hover"
        >
          Try again
        </button>
      </div>
    );
  }
}
