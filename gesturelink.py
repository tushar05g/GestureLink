#!/usr/bin/env python3
"""
GestureLink — Unified Command Center
Launch and manage your AI-powered gesture control suite.
"""
import sys
import argparse
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.append(str(PROJECT_ROOT))

def main():
    parser = argparse.ArgumentParser(description="GestureLink Suite Control")
    parser.add_argument("command", nargs="?", choices=["hub", "agent", "builder", "tray", "install"], help="Command to run")
    parser.add_argument("--builder", action="store_true", help="Start directly in Builder Mode")
    
    args = parser.parse_args()

    # Add project root to sys.path so imports work
    os.chdir(str(PROJECT_ROOT))

    if args.command == "hub":
        print("[HUB] Starting GestureLink Hub Dashboard...")
        from src.hub.server import start_hub
        start_hub()
    
    elif args.command == "agent":
        print("[AGENT] Starting GestureLink Remote Agent...")
        from src.agent.main import start_agent
        start_agent()
        
    elif args.command == "builder" or args.builder:
        print("[BUILDER] Starting GestureLink 2D Builder Mode...")
        from src.core.app import run_app, AppMode
        run_app(initial_mode=AppMode.BUILDER)
        
    elif args.command == "tray":
        from src.gui.tray import start_tray
        start_tray()
        
    elif args.command == "install":
        from scripts.install_context_menu import install
        install()
        
    else:
        # Default: Start Productivity Mode locally
        print("[VISION] Starting Local Gesture Control...")
        from src.core.app import run_app, AppMode
        run_app(initial_mode=AppMode.PRODUCTIVITY)

if __name__ == "__main__":
    main()
