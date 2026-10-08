@echo off
TITLE Building VIGIL EXE
echo.
echo  Building VIGIL.exe...
echo  This takes 2-3 minutes.
echo.

call conda activate ppe
pip install pyinstaller -q

pyinstaller VIGIL.spec --clean --noconfirm

if %errorlevel% == 0 (
    echo.
    echo  ============================================
    echo   SUCCESS! EXE created at: dist\VIGIL.exe
    echo  ============================================
    echo.
    explorer dist
) else (
    echo.
    echo  [ERROR] Build failed. Check output above.
)
pause
