"""
run.py — Project entry point.
Run with:  python run.py
"""
import sys
import os

# Add the src folder directly to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from app import run

if __name__ == "__main__":
    run()