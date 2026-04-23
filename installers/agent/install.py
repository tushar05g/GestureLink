"""
GestureLink Agent - Cross-Platform Smart Installer
Runs on Python 3.10+ on Windows, Linux, or macOS.
- Creates a virtual environment
- Installs all dependencies
- Launches the Agent as a system tray process
"""
import os
import sys
import subprocess
import platform
import venv
from pathlib import Path

MIN_PYTHON = (3, 10)
AGENT_ROOT = Path(__file__).resolve().parent.parent.parent
VENV_DIR   = AGENT_ROOT / ".venv_agent"
IS_WINDOWS = platform.system() == "Windows"
PYTHON_BIN = VENV_DIR / ("Scripts/python.exe" if IS_WINDOWS else "bin/python3")
PIP_BIN    = VENV_DIR / ("Scripts/pip.exe"    if IS_WINDOWS else "bin/pip")
AGENT_SCRIPT = AGENT_ROOT / "dist" / "GestureLink_Agent_Package" / "agent.py"

REQUIREMENTS = [
    "fastapi",
    "uvicorn[standard]",
    "pyautogui",
    "zeroconf",
    "websockets",
    "pystray",
    "Pillow",
]

BANNER = """
╔══════════════════════════════════════╗
║      GestureLink Agent Setup         ║
║   Target PC Remote Control Client    ║
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
    print(f"  ✓  Created at {VENV_DIR}")

def install_requirements() -> None:
    print_step("Installing dependencies...")
    subprocess.check_call(
        [str(PIP_BIN), "install", "--quiet", "--upgrade", "pip"],
        cwd=str(AGENT_ROOT)
    )
    subprocess.check_call(
        [str(PIP_BIN), "install", "--quiet"] + REQUIREMENTS,
        cwd=str(AGENT_ROOT)
    )
    print(f"  ✓  All dependencies installed")

def launch_agent() -> None:
    print_step("Starting GestureLink Agent...")
    print("\n" + "─" * 42)
    print("  Agent is running!")
    print("  It will appear in your system tray.")
    print("  Your Hub PC will detect it automatically.")
    print("─" * 42 + "\n")
    os.execv(str(PYTHON_BIN), [str(PYTHON_BIN), str(AGENT_SCRIPT), "--port", "8765"])

def main() -> None:
    print(BANNER)
    check_python()
    create_venv()
    install_requirements()
    launch_agent()

if __name__ == "__main__":
    main()
