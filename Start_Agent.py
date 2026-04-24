import sys
import subprocess
from pathlib import Path
import os

def run():
    project_root = Path(__file__).resolve().parent
    env_path = project_root / ".venv"
    
    # 1. Check if installed (Agents also need a basic venv for dependencies)
    if not env_path.exists():
        print("🚀 First time setup... Preparing your PC for Remote Control.")
        subprocess.run([sys.executable, "src/hub/gui_installer.py"])
        
    # 2. Launch Agent
    print("🛰 Connecting this PC to the GestureLink Network...")
    python_bin = env_path / "bin" / "python" if os.name != "nt" else env_path / "Scripts" / "python.exe"
    
    if os.name == "nt":
        pythonw = env_path / "Scripts" / "pythonw.exe"
        cmd = [str(pythonw if pythonw.exists() else python_bin), "gesturelink.py", "agent"]
    else:
        cmd = [str(python_bin), "gesturelink.py", "agent"]
        
    subprocess.Popen(cmd, cwd=str(project_root))

if __name__ == "__main__":
    run()
