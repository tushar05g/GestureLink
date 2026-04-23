import os
import subprocess
import sys
from pathlib import Path

def run_command(command, cwd=None):
    print(f"🚀 Running: {command}")
    try:
        subprocess.run(command, shell=True, check=True, cwd=cwd)
    except subprocess.CalledProcessError as e:
        print(f"❌ Error: Command failed with exit code {e.returncode}")
        sys.exit(1)

def main():
    base_dir = Path(__file__).resolve().parent
    mobile_dir = base_dir / "src" / "web" / "mobile"
    
    print("\n--- 📱 GestureLink Mobile Build Orchestrator ---")
    
    # 1. Check if node_modules exists
    if not (mobile_dir / "node_modules").exists():
        print("📦 Installing dependencies...")
        run_command("npm install", cwd=mobile_dir)
    
    # 2. Build Web Assets
    print("\n📦 Building Web Assets (Vite)...")
    run_command("npm run build", cwd=mobile_dir)
    
    # 3. Sync with Capacitor
    print("\n🔄 Syncing with Android Project...")
    run_command("npx cap sync", cwd=mobile_dir)
    
    print("\n" + "="*50)
    print("✅ BUILD COMPLETE!")
    print("="*50)
    print("\nYour Android project is now ready.")
    print("\nTo generate the final APK:")
    print("1. Open 'Android Studio'")
    print(f"2. Open the folder: {mobile_dir / 'android'}")
    print("3. Wait for Gradle sync to finish.")
    print("4. Go to: Build > Build Bundle(s) / APK(s) > Build APK(s)")
    print("\nAlternatively, run this Python command to open Android Studio automatically:")
    print(f"   npx cap open android (from {mobile_dir})")
    print("="*50)

if __name__ == "__main__":
    main()
