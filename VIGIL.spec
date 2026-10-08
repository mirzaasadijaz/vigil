# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for VIGIL launcher
# Build with:  pyinstaller VIGIL.spec

import sys
from pathlib import Path

block_cipher = None
ROOT = Path(".").resolve()

a = Analysis(
    ["launcher.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Include the built frontend
        ("frontend/dist", "frontend/dist"),
        # Include YOLO model files
        ("yolo11n.pt", "."),
        # Include alembic migrations
        ("alembic.ini", "."),
        ("backend/alembic", "backend/alembic"),
    ],
    hiddenimports=[
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "fastapi",
        "sqlalchemy",
        "sqlalchemy.ext.asyncio",
        "asyncpg",
        "psycopg2",
        "aiosqlite",
        "jose",
        "passlib",
        "bcrypt",
        "multipart",
        "pydantic",
        "pydantic_settings",
        "tkinter",
        "tkinter.ttk",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["torch", "tensorflow", "deepface", "cv2", "ultralytics"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="VIGIL",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # Show console (required for fallback on servers/headless)
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,             # Add .ico file path here if you have one
)
