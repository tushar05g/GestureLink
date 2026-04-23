"""
GestureLink Hub - Cross-Platform Smart Installer
Runs on Python 3.10+ on Linux, Windows, or macOS.
- Creates a virtual environment
- Installs all dependencies
- Generates SSL certificates for HTTPS
- Launches the Hub server
"""
import os
import sys
import subprocess
import platform
import venv
from pathlib import Path

MIN_PYTHON = (3, 10)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VENV_DIR = PROJECT_ROOT / ".venv"
IS_WINDOWS = platform.system() == "Windows"
PYTHON_BIN = VENV_DIR / ("Scripts/python.exe" if IS_WINDOWS else "bin/python3")
PIP_BIN    = VENV_DIR / ("Scripts/pip.exe"    if IS_WINDOWS else "bin/pip")

REQUIREMENTS = [
    "fastapi",
    "uvicorn[standard]",
    "python-dotenv",
    "pyautogui",
    "zeroconf",
    "websockets",
    "qrcode[pil]",
    "mediapipe",
    "opencv-python",
    "pystray",
    "Pillow",
    "cryptography",   # for SSL cert generation
    "pyperclip",
]

BANNER = """
╔══════════════════════════════════════╗
║        GestureLink Hub Setup         ║
║     AI-Powered Gesture Controller    ║
╚══════════════════════════════════════╝
"""

def print_step(msg: str) -> None:
    print(f"\n  ▶  {msg}")

def check_python() -> None:
    v = sys.version_info
    if v < MIN_PYTHON:
        print(f"  ✗  Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required. You have {v.major}.{v.minor}")
        print("     Download from: https://www.python.org/downloads/")
        sys.exit(1)
    print(f"  ✓  Python {v.major}.{v.minor}.{v.micro} detected")

def create_venv() -> None:
    if VENV_DIR.exists():
        print(f"  ✓  Virtual environment already exists")
        return
    print_step("Creating virtual environment...")
    venv.create(str(VENV_DIR), with_pip=True)
    print(f"  ✓  Virtual environment created at {VENV_DIR}")

def install_requirements() -> None:
    print_step("Installing dependencies (this may take a few minutes)...")
    subprocess.check_call(
        [str(PIP_BIN), "install", "--quiet", "--upgrade", "pip"],
        cwd=str(PROJECT_ROOT)
    )
    subprocess.check_call(
        [str(PIP_BIN), "install", "--quiet"] + REQUIREMENTS,
        cwd=str(PROJECT_ROOT)
    )
    print(f"  ✓  All dependencies installed")

def generate_ssl_certs() -> None:
    cert = PROJECT_ROOT / "cert.pem"
    key  = PROJECT_ROOT / "key.pem"
    if cert.exists() and key.exists():
        print(f"  ✓  SSL certificates already exist")
        return
    print_step("Generating self-signed SSL certificates...")
    try:
        subprocess.check_call(
            [str(PYTHON_BIN), str(PROJECT_ROOT / "generate_certs.py")],
            cwd=str(PROJECT_ROOT)
        )
        print(f"  ✓  SSL certificates generated (cert.pem, key.pem)")
    except Exception as e:
        print(f"  ⚠  SSL generation failed: {e}. Running in HTTP mode.")

def launch_hub() -> None:
    print_step("Starting GestureLink Hub...")
    print("\n" + "─" * 42)
    print("  Hub is starting. Open your browser to:")
    print("  https://YOUR_IP:8000")
    print("  Your phone will detect it automatically.")
    print("─" * 42 + "\n")
    os.chdir(str(PROJECT_ROOT))
    os.execv(str(PYTHON_BIN), [str(PYTHON_BIN), "-m", "src.hub.server", "--port", "8000"])

def main() -> None:
    print(BANNER)
    check_python()
    create_venv()
    install_requirements()
    generate_ssl_certs()
    launch_hub()

if __name__ == "__main__":
    main()
