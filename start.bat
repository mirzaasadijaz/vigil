@echo off
TITLE VIGIL — Workplace Intelligence Platform
color 0B

echo.
echo  ██╗   ██╗██╗ ██████╗ ██╗██╗
echo  ██║   ██║██║██╔════╝ ██║██║
echo  ██║   ██║██║██║  ███╗██║██║
echo  ╚██╗ ██╔╝██║██║   ██║██║██║
echo   ╚████╔╝ ██║╚██████╔╝██║███████╗
echo    ╚═══╝  ╚═╝ ╚═════╝ ╚═╝╚══════╝
echo.
echo  Workplace Intelligence Platform
echo  ─────────────────────────────────────
echo.

:: Check if conda is available
where conda >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] conda not found. Please install Anaconda or Miniconda.
    pause
    exit /b 1
)

echo  [1/3] Activating environment...
call conda activate ppe 2>nul
if %errorlevel% neq 0 (
    echo  [ERROR] Could not activate 'ppe' environment.
    echo         Run: conda create -n ppe python=3.11
    pause
    exit /b 1
)

echo  [2/3] Starting VIGIL server on port 8000...
start /B uvicorn backend.main:app --host 127.0.0.1 --port 8000

echo  [3/3] Waiting for server to start...
timeout /t 4 /nobreak >nul

echo  Opening browser...
start http://127.0.0.1:8000

echo.
echo  ✓ VIGIL is running at http://127.0.0.1:8000
echo  Press Ctrl+C to stop the server.
echo.

:: Keep window open and server running
uvicorn backend.main:app --host 127.0.0.1 --port 8000
pause
