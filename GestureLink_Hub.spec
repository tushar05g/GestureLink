# -*- mode: python ; coding: utf-8 -*-
# GestureLink_Hub.spec — PyInstaller build configuration for the Hub executable.
#
# Build command:
#   pyinstaller GestureLink_Hub.spec
#
# Output: dist/GestureLink_Hub.exe

import os
from pathlib import Path

ROOT = Path(SPECPATH)

import mediapipe
mediapipe_path = os.path.dirname(mediapipe.__file__)

block_cipher = None

# ─── Hidden imports ───────────────────────────────────────────────────────────
# These are packages that PyInstaller cannot auto-detect because they are
# imported dynamically (e.g. uvicorn's logging config, mediapipe plugins, etc.)
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
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.logging",
    "uvicorn.middleware",
    "uvicorn.middleware.proxy_headers",
    "fastapi",
    "fastapi.responses",
    "fastapi.staticfiles",
    "fastapi.middleware.cors",
    "starlette",
    "starlette.routing",
    "starlette.staticfiles",
    "anyio",
    "anyio._backends._asyncio",
    "anyio._backends._trio",
    # Mediapipe
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
    "pydantic.deprecated",
    "pydantic_core",
    # Network
    "zeroconf",
    "zeroconf._utils",
    "zeroconf._utils.ipaddress",
    "zeroconf._handlers",
    "websockets",
    "websockets.legacy",
    "websockets.legacy.server",
    # Other
    "qrcode",
    "PIL",
    "PIL.Image",
    "httpx",
    "multipart",
    "python_multipart",
    # Project internals
    "src",
    "src.core",
    "src.hub",
    "src.hub.managers",
    "src.core.config",
    "src.core.controller",
    "src.core.shortcuts",
    "src.core.vision",
    "src.core.vision_worker",
    "src.core.utils",
    "src.core.modal_vision",
    "src.core.modes",
]

# ─── Data files to bundle ─────────────────────────────────────────────────────
# Format: (source, dest_folder_inside_bundle)
datas = [
    # Hub Dashboard HTML
    (str(ROOT / "src" / "web" / "hub" / "hub.html"),         "src/web/hub"),
    # Remote client HTML
    (str(ROOT / "src" / "web" / "client" / "remote_client.html"), "src/web/client"),
    # Mobile PWA (built with: npm run build inside src/web/mobile)
    (str(ROOT / "src" / "web" / "mobile" / "dist"),          "src/web/mobile/dist"),
    # AI Model file
    (str(ROOT / "src" / "core" / "models"),                   "src/core/models"),
    # TLS certificates (self-signed, needed for HTTPS)
    (str(ROOT / "cert.pem"),                                  "."),
    (str(ROOT / "key.pem"),                                   "."),
    # .env (ngrok token, etc.)
    (str(ROOT / ".env"),                                      "."),
    # Mediapipe C bindings
    (os.path.join(mediapipe_path, "tasks", "c"),              "mediapipe/tasks/c"),
]

# ─── Analysis ─────────────────────────────────────────────────────────────────
a = Analysis(
    [str(ROOT / "src" / "hub" / "tray.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude large packages we don't need
        "tkinter",
        "scipy",
        "IPython",
        "notebook",
        "PyQt5",
        "PyQt6",
        "wx",
        "PyOpenGL",   # Builder mode only — exclude from Hub for size
        "glfw",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ─── EXE ──────────────────────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="GestureLink_Hub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,           # UPX compression — reduces file size ~20%
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # Hide the terminal window for a true Desktop App feel
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # UAC manifest — forces "Run as Administrator" on Windows
    uac_admin=True,
    # App icon (place a logo.ico in the project root, or remove this line)
    icon=str(ROOT / "logo.ico") if (ROOT / "logo.ico").exists() else None,
    # Embed the UAC manifest
    manifest=str(ROOT / "GestureLink_Hub.manifest"),
    version=None,
)
