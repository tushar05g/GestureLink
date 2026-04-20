"""
run.py — Entry point.
Loads .env if present, then starts the app.
Run with: python run.py
"""
import sys
import os

# Load .env if present (before any imports)
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from app import run

if __name__ == "__main__":
    run()