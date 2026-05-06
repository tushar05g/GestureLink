#!/usr/bin/env python3
"""
build.py — GestureLink Windows Executable Builder
===================================================
Automates the full build pipeline:
  1. Cleans previous builds
  2. Builds the mobile frontend (npm run build)
  3. Compiles GestureLink_Hub.exe  via PyInstaller
  4. Compiles GestureLink_Agent.exe via PyInstaller
  5. Copies final .exe files to ./release/

Usage:
    python build.py            # Build both Hub and Agent
    python build.py --hub      # Build only Hub
    python build.py --agent    # Build only Agent
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# ── Colours for terminal output ──────────────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
RESET  = "\033[0m"

def log(msg, colour=CYAN):
    print(f"{colour}[BUILD] {msg}{RESET}")

def success(msg):
    print(f"{GREEN}[OK]    {msg}{RESET}")

def warn(msg):
    print(f"{YELLOW}[WARN]  {msg}{RESET}")

def error(msg):
    print(f"{RED}[ERROR] {msg}{RESET}")
    sys.exit(1)


# ─── Step 0: Validation ───────────────────────────────────────────────────────
def check_prerequisites():
    log("Checking prerequisites...")

    # PyInstaller
    try:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            capture_output=True, check=True
        )
        success("PyInstaller found.")
    except (subprocess.CalledProcessError, FileNotFoundError):
        error(
            "PyInstaller not found. Install it with:\n"
            "  venv\\Scripts\\pip install pyinstaller"
        )

    # Node / npm (for mobile build)
    node = shutil.which("npm")
    if not node:
        warn("npm not found — skipping mobile build. Make sure to build it manually.")
        return False
    success("npm found.")
    return True


# ─── Step 1: Clean ────────────────────────────────────────────────────────────
def clean():
    log("Cleaning old builds...")
    for folder in ["build", "dist", "release", "__pycache__"]:
        target = ROOT / folder
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            log(f"  Removed {folder}/")
    success("Clean done.")


# ─── Step 2: Mobile frontend build ────────────────────────────────────────────
def build_mobile():
    mobile_dir = ROOT / "src" / "web" / "mobile"
    dist_dir   = mobile_dir / "dist"

    log("Building mobile frontend (npm run build)...")

    if not (mobile_dir / "package.json").exists():
        warn("No package.json found in src/web/mobile — skipping npm build.")
        return

    try:
        # On Windows, npm is usually npm.cmd, and shell=True is often required
        use_shell = (os.name == "nt")
        subprocess.run(
            ["npm", "install"],
            cwd=str(mobile_dir), check=True, shell=use_shell
        )
        subprocess.run(
            ["npm", "run", "build"],
            cwd=str(mobile_dir), check=True, shell=use_shell
        )
    except subprocess.CalledProcessError as e:
        error(f"npm build failed: {e}")

    if not dist_dir.exists():
        error("Mobile dist/ folder not created — npm build may have failed.")

    success(f"Mobile frontend built -> {dist_dir}")


# ─── Step 3: Convert logo.png → logo.ico ──────────────────────────────────────
def convert_icon():
    logo_png = ROOT / "logo.png"
    logo_ico = ROOT / "logo.ico"

    if logo_ico.exists():
        success("logo.ico already exists — skipping icon conversion.")
        return

    if not logo_png.exists():
        warn("logo.png not found — .exe will not have a custom icon.")
        return

    log("Converting logo.png -> logo.ico...")
    try:
        from PIL import Image
        img = Image.open(logo_png)
        img.save(
            logo_ico,
            format="ICO",
            sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
        )
        success(f"Icon saved to {logo_ico}")
    except Exception as e:
        warn(f"Could not convert icon: {e} — .exe will use default icon.")


# ─── Step 4: PyInstaller build ────────────────────────────────────────────────
def build_pyinstaller(spec_name: str):
    spec_file = ROOT / spec_name
    if not spec_file.exists():
        error(f"Spec file not found: {spec_file}")

    log(f"Running PyInstaller for {spec_name}...")
    try:
        subprocess.run(
            [
                sys.executable, "-m", "PyInstaller",
                str(spec_file),
                "--noconfirm",    # overwrite dist/ without asking
                "--clean",        # clean build cache before building
            ],
            cwd=str(ROOT),
            check=True,
        )
    except subprocess.CalledProcessError as e:
        error(f"PyInstaller failed for {spec_name}: {e}")

    exe_name = spec_name.replace(".spec", ".exe")
    output   = ROOT / "dist" / exe_name
    if not output.exists():
        error(f"Expected output not found: {output}")

    size_mb = output.stat().st_size / (1024 * 1024)
    success(f"Built: {output.name} ({size_mb:.1f} MB)")
    return output


# ─── Step 5: Copy to release/ ─────────────────────────────────────────────────
def copy_to_release(paths: list[Path]):
    release_dir = ROOT / "release"
    release_dir.mkdir(exist_ok=True)

    for p in paths:
        dest = release_dir / p.name
        shutil.copy2(p, dest)
        log(f"  Copied {p.name} -> release/")

    success(f"Release files ready in: {release_dir}")


# ─── Step 6: Create Installer ──────────────────────────────────────────────────
def build_installer():
    inno_script = ROOT / "installer" / "gesturelink.iss"
    if not inno_script.exists():
        warn("Inno Setup script not found in installer/gesturelink.iss")
        return

    iscc_paths = [
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
        shutil.which("ISCC.exe")
    ]

    iscc_exe = None
    for p in iscc_paths:
        if p and Path(p).exists():
            iscc_exe = p
            break

    if not iscc_exe:
        warn("Inno Setup Compiler (ISCC.exe) not found. Skipping installer generation.")
        return

    log("Building Inno Setup Installer...")
    try:
        subprocess.run([iscc_exe, str(inno_script)], check=True)
        success("Installer built successfully in release/ folder.")
    except subprocess.CalledProcessError as e:
        error(f"Failed to build installer: {e}")


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="GestureLink Windows Executable Builder"
    )
    parser.add_argument("--hub",   action="store_true", help="Build only the Hub exe")
    parser.add_argument("--agent", action="store_true", help="Build only the Agent exe")
    parser.add_argument("--skip-clean", action="store_true", help="Skip clean step")
    args = parser.parse_args()

    # Default: build both
    build_hub   = args.hub   or (not args.hub and not args.agent)
    build_agent = args.agent or (not args.hub and not args.agent)

    print(f"\n{CYAN}{'='*50}")
    print(" GestureLink Windows Build System")
    print(f"{'='*50}{RESET}\n")

    has_npm = check_prerequisites()

    if not args.skip_clean:
        clean()

    if has_npm:
        build_mobile()

    convert_icon()

    built_files = []
    if build_hub:
        log("Building GestureLink_Hub.exe ...")
        exe = build_pyinstaller("GestureLink_Hub.spec")
        built_files.append(exe)

    if build_agent:
        log("Building GestureLink_Agent.exe ...")
        exe = build_pyinstaller("GestureLink_Agent.spec")
        built_files.append(exe)

    if built_files:
        copy_to_release(built_files)
        build_installer()

    print(f"\n{GREEN}{'='*50}")
    print(" BUILD COMPLETE!")
    print(f"{'='*50}{RESET}")
    print(f"\n  Your .exe files are in:  {ROOT / 'release'}")
    print("""
  ANTIVIRUS NOTE:
  ---------------
  Windows Defender or other AV software may flag unsigned .exe files.
  This is a false positive. To allow:
    1. Open Windows Security -> Virus & threat protection
    2. Click "Protection history"
    3. Find the blocked item and click "Allow"
    OR:
    - Right-click the .exe -> Properties -> Check "Unblock" at the bottom

  FIRST RUN:
  ----------
  Double-click GestureLink_Hub.exe
  Windows SmartScreen may appear. Click "More info" -> "Run anyway"
""")


if __name__ == "__main__":
    main()
