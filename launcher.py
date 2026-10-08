"""
VIGIL Launcher — starts the FastAPI server and opens the browser.
This file is the PyInstaller entry point; it is NOT part of the server itself.
"""

import sys
import os
import subprocess
import webbrowser
import threading
import time
import socket
from pathlib import Path

# ── Resolve project root ──────────────────────────────────────────────────────
def _find_project_root() -> Path:
    """Walk up from the exe/script location to find the directory with backend/."""
    start = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent.resolve()
    for candidate in [start, start.parent, start.parent.parent]:
        if (candidate / "backend").is_dir():
            return candidate
    return start

BASE_DIR = _find_project_root()

HOST = "127.0.0.1"
PORT = 8000
URL  = f"http://{HOST}:{PORT}"

# ── Try to import tkinter for GUI; fall back to console mode ──────────────────
try:
    import tkinter as tk
    from tkinter import font as tkfont
    HAS_TK = True
except ImportError:
    HAS_TK = False


# ─── Find Python interpreter ──────────────────────────────────────────────────
def find_python() -> str:
    """Return the path to the Python executable in the 'ppe' conda env."""
    username = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    candidates = [
        # Windows — Anaconda
        Path(f"C:/Users/{username}/anaconda3/envs/ppe/python.exe"),
        Path(f"C:/Users/{username}/Anaconda3/envs/ppe/python.exe"),
        Path(f"C:/ProgramData/Anaconda3/envs/ppe/python.exe"),
        # Windows — Miniconda
        Path(f"C:/Users/{username}/miniconda3/envs/ppe/python.exe"),
        Path(f"C:/ProgramData/miniconda3/envs/ppe/python.exe"),
        # Linux / Mac — Anaconda
        Path.home() / "anaconda3" / "envs" / "ppe" / "bin" / "python",
        Path.home() / "miniconda3" / "envs" / "ppe" / "bin" / "python",
        Path("/opt/anaconda3/envs/ppe/bin/python"),
        Path("/home/ubuntu/anaconda3/envs/ppe/bin/python"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return sys.executable  # last resort: same Python that runs this script


def wait_for_server(timeout: int = 30) -> bool:
    """Poll until uvicorn is accepting connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=1):
                return True
        except OSError:
            time.sleep(0.4)
    return False


# ─── GUI Launcher ─────────────────────────────────────────────────────────────
class VIGILApp:
    BG      = "#050B14"
    SURFACE = "#0C1424"
    BORDER  = "#1C3252"
    BLUE    = "#3B82F6"
    TEXT    = "#E2EBF8"
    MUTED   = "#3D566F"

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.server_ready = False

        self.root = tk.Tk()
        self.root.title("VIGIL")
        self.root.geometry("380x260")
        self.root.resizable(False, False)
        self.root.configure(bg=self.BG)
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        self._build_ui()
        threading.Thread(target=self._start_server, daemon=True).start()
        self.root.mainloop()

    def _build_ui(self):
        # Logo area
        logo_frame = tk.Frame(self.root, bg=self.BG)
        logo_frame.pack(pady=(28, 0))

        canvas = tk.Canvas(logo_frame, width=48, height=48, bg=self.BG, highlightthickness=0)
        canvas.pack()
        canvas.create_oval(0, 0, 48, 48, fill=self.BLUE, outline="")
        canvas.create_text(24, 24, text="VI", fill="white", font=("Arial", 14, "bold"))

        tk.Label(self.root, text="VIGIL", bg=self.BG, fg=self.TEXT,
                 font=("Arial", 15, "bold")).pack(pady=(8, 0))
        tk.Label(self.root, text="Workplace Intelligence Platform",
                 bg=self.BG, fg=self.MUTED, font=("Arial", 9)).pack()

        # Status
        self.status_var = tk.StringVar(value="Starting server…")
        tk.Label(self.root, textvariable=self.status_var, bg=self.BG, fg=self.MUTED,
                 font=("Arial", 9)).pack(pady=(18, 4))

        # Progress bar
        style = tk.ttk.Style()
        style.theme_use("default")
        style.configure("V.Horizontal.TProgressbar",
                        troughcolor=self.SURFACE, background=self.BLUE,
                        bordercolor=self.BORDER, lightcolor=self.BLUE, darkcolor=self.BLUE)
        self.progress = tk.ttk.Progressbar(
            self.root, style="V.Horizontal.TProgressbar",
            mode="indeterminate", length=280)
        self.progress.pack()
        self.progress.start(12)

        # Buttons
        btn_frame = tk.Frame(self.root, bg=self.BG)
        btn_frame.pack(pady=18)

        self.open_btn = tk.Button(
            btn_frame, text="Open Browser", state=tk.DISABLED,
            command=self._open_browser,
            bg=self.BLUE, fg="white", relief="flat",
            font=("Arial", 10, "bold"), padx=18, pady=7, cursor="hand2",
            activebackground="#2563EB", activeforeground="white")
        self.open_btn.grid(row=0, column=0, padx=6)

        tk.Button(
            btn_frame, text="Quit", command=self.quit_app,
            bg=self.SURFACE, fg=self.MUTED, relief="flat",
            font=("Arial", 10), padx=18, pady=7, cursor="hand2",
            activebackground=self.BORDER, activeforeground=self.TEXT
        ).grid(row=0, column=1, padx=6)

    def _start_server(self):
        python = find_python()
        env = os.environ.copy()
        env["PYTHONPATH"] = str(BASE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        self.proc = subprocess.Popen(
            [python, "-m", "uvicorn", "backend.main:app",
             "--host", HOST, "--port", str(PORT)],
            cwd=str(BASE_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        ready = wait_for_server(30)
        self.root.after(0, self._on_server_ready if ready else self._on_server_failed)

    def _on_server_ready(self):
        self.server_ready = True
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)
        self.status_var.set(f"Running at {URL}")
        self.open_btn.configure(state=tk.NORMAL)
        webbrowser.open(URL)

    def _on_server_failed(self):
        self.progress.stop()
        self.status_var.set("Failed to start. Check console for errors.")

    def _open_browser(self):
        webbrowser.open(URL)

    def quit_app(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.root.destroy()


# ─── Console fallback ─────────────────────────────────────────────────────────
def console_mode():
    python = find_python()
    print(f"\n  VIGIL — Workplace Intelligence Platform")
    print(f"  ----------------------------------------")
    print(f"  Python : {python}")
    print(f"  URL    : {URL}\n")
    print("  Starting server…")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(BASE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [python, "-m", "uvicorn", "backend.main:app",
         "--host", HOST, "--port", str(PORT)],
        cwd=str(BASE_DIR),
        env=env,
    )
    if wait_for_server(30):
        print(f"  Server ready. Opening browser…")
        webbrowser.open(URL)
    else:
        print("  Server did not start in time.")

    print("  Press Ctrl+C to stop.\n")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


# ─── Entry point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if HAS_TK:
        try:
            import tkinter.ttk as _ttk
            tk.ttk = _ttk
            # Verify display is available before creating window
            test = tk.Tk()
            test.destroy()
            VIGILApp()
        except Exception:
            console_mode()
    else:
        console_mode()
