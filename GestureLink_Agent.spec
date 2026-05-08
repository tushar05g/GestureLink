# -*- mode: python ; coding: utf-8 -*-
# GestureLink_Agent.spec — PyInstaller build configuration for the Agent executable.
#
# Build command:
#   pyinstaller GestureLink_Agent.spec
#
# Output: dist/GestureLink_Agent.exe

import os
from pathlib import Path

ROOT = Path(SPECPATH)

import mediapipe
mediapipe_path = os.path.dirname(mediapipe.__file__)

block_cipher = None

hidden_imports = [
    # FastAPI / Uvicorn
    "uvicorn",
    "uvicorn.main",
    "uvicorn.config",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.lifespan",
    "uvicorn.lifespan.off",
    "uvicorn.lifespan.on",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.logging",
    "fastapi",
    "fastapi.responses",
    "fastapi.middleware.cors",
    "starlette",
    "starlette.routing",
    "anyio",
    "anyio._backends._asyncio",
    # Mediapipe (needed for optional camera on Agent)
    "mediapipe",
    "mediapipe.python._framework_bindings",
    "mediapipe.tasks",
    "mediapipe.tasks.c",
    "mediapipe.tasks.python",
    "mediapipe.tasks.python.vision",
    "mediapipe.tasks.python.core",
    "mediapipe.tasks.python.core.mediapipe_c_bindings",
    # Computer vision
    "cv2",
    "numpy",
    # Pydantic
    "pydantic",
    "pydantic_core",
    # Network
    "zeroconf",
    "zeroconf._utils",
    "zeroconf._utils.ipaddress",
    "zeroconf._handlers",
    "websockets",
    # Project internals
    "src",
    "src.core",
    "src.agent",
    "src.core.config",
    "src.core.controller",
    "src.core.shortcuts",
    "src.core.vision",
    "src.core.vision_worker",
    "src.core.utils",
    "src.core.modal_vision",
]

datas = [
    # AI Model (for Agent camera mode)
    (str(ROOT / "src" / "core" / "models"), "src/core/models"),
    # Mediapipe C bindings
    (os.path.join(mediapipe_path, "tasks", "c"), "mediapipe/tasks/c"),
    # SSL certificates
    (str(ROOT / "cert.pem"), "."),
    (str(ROOT / "key.pem"), "."),
]

a = Analysis(
    [str(ROOT / "src" / "agent" / "tray.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "scipy",
        "IPython",
        "notebook",
        "PyQt5",
        "PyQt6",
        "wx",
        "PyOpenGL",
        "glfw",
        "qrcode",
    ],
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
    name="GestureLink_Agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # Hide the terminal window to run silently in the background
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,     # Force "Run as Administrator"
    icon=str(ROOT / "logo.ico") if (ROOT / "logo.ico").exists() else None,
    manifest=str(ROOT / "GestureLink_Agent.manifest"),
    version=None,
)
