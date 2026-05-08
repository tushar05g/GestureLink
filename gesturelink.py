#!/usr/bin/env python3
"""
GestureLink — Unified Command Center
Launch and manage your AI-powered gesture control suite.
"""
import sys
import os
import multiprocessing

# CRITICAL: Fix for PyInstaller + Multiprocessing
if __name__ == "__main__":
    multiprocessing.freeze_support()

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import argparse
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

def run_command(cmd_args, cwd=None):
    try:
        subprocess.run([sys.executable] + cmd_args, cwd=cwd or str(PROJECT_ROOT))
    except KeyboardInterrupt:
        print("\nExiting...")
    except Exception as e:
        print(f"Error: {e}")

def main():
    parser = argparse.ArgumentParser(description="GestureLink Suite Control")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # HUB
    subparsers.add_parser("hub", help="Launch the GestureLink Hub (Dashboard)")
    
    # AGENT
    subparsers.add_parser("agent", help="Launch the GestureLink Agent (Remote PC)")
    
    # TRAY
    subparsers.add_parser("tray", help="Launch the System Tray controller")

    # INSTALL
    subparsers.add_parser("install", help="Run the cross-platform installer")

    # BUILDER (Placeholder)
    subparsers.add_parser("builder", help="Research mode: Builder Mode (Research Phase)")

    args = parser.parse_args()

    if args.command == "hub":
        print("[HUB] Starting GestureLink Hub...")
        run_command(["-m", "src.hub.server"])
    
    elif args.command == "agent":
        print("[AGENT] Starting GestureLink Agent...")
        run_command(["-m", "src.agent.main"])
        
    elif args.command == "tray":
        print("[TRAY] Starting GestureLink Tray...")
        run_command(["src/tray_hub.py"])
        
    elif args.command == "install":
        print("[INSTALL] Opening Installer...")
        try:
            # Try GUI first for non-tech users
            run_command(["src/hub/gui_installer.py"])
        except Exception:
            # Fallback to terminal
            run_command(["installers/hub/install.py"])
        
    elif args.command == "builder":
        print("[BUILDER] Builder Mode is currently in Research Phase. See builder_mode_research.md")
        
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
