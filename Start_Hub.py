import sys
import subprocess
from pathlib import Path
import os

def run():
    project_root = Path(__file__).resolve().parent
    env_path = project_root / ".venv"
    
    # 1. Check if installed
    if not env_path.exists():
        print("[SETUP] First time setup... Opening GestureLink Installer.")
        # Run installer first
        subprocess.run([sys.executable, "src/hub/gui_installer.py"])
        
    # 2. Launch Hub
    print("[HUB] Starting GestureLink Hub Dashboard...")
    python_bin = env_path / "bin" / "python" if os.name != "nt" else env_path / "Scripts" / "python.exe"
    
    # Use Popen to launch and exit the launcher
    if os.name == "nt":
        # On Windows, pythonw.exe can be used to hide the console
        pythonw = env_path / "Scripts" / "pythonw.exe"
        cmd = [str(pythonw if pythonw.exists() else python_bin), "gesturelink.py", "hub"]
    else:
        cmd = [str(python_bin), "gesturelink.py", "hub"]
        
    subprocess.Popen(cmd, cwd=str(project_root))

if __name__ == "__main__":
    run()
