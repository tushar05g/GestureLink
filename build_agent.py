import PyInstaller.__main__
import os
from pathlib import Path

def build_agent():
    print("Building GestureLink Agent (standalone remote PC client)...")

    root = Path(__file__).parent

    # remote_server.py lives at project root, not inside src/
    script_path = root / "src" / "remote_server.py"
    if not script_path.exists():
        # Fallback to the dedicated agent entrypoint
        script_path = root / "src" / "agent" / "main.py"

    args = [
        str(script_path),
        "--onefile",               # Single portable file
        "--name=GestureLink_Agent",
        "--clean",
        "--hidden-import=pyautogui",
        "--hidden-import=zeroconf",
        "--hidden-import=zeroconf._utils.ipaddress",
        "--hidden-import=zeroconf._dns",
        "--hidden-import=uvicorn",
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=fastapi",
        "--hidden-import=websockets",
    ]

    PyInstaller.__main__.run(args)

    print("\n" + "="*40)
    print("BUILD COMPLETE!")
    print(f"Agent file: {os.getcwd()}/dist/GestureLink_Agent")
    print("="*40)
    print("\nTo install on Target PC:")
    print("  Linux/Mac : ./GestureLink_Agent --port 8000")
    print("  Windows   : GestureLink_Agent.exe --port 8000")

if __name__ == "__main__":
    build_agent()
