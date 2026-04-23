#!/bin/bash
# GestureLink Agent — Linux/macOS Launcher
# Copy this folder to the Target PC and run: bash START_LINUX.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║      GestureLink Agent               ║"
echo "  ║   Target PC Remote Control Client    ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# Find Python 3.10+
PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(sys.version_info >= (3,10))" 2>/dev/null)
        if [ "$VER" = "True" ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "  ERROR: Python 3.10 or newer is required."
    echo "  Install with:"
    echo "    Ubuntu/Debian: sudo apt install python3.11"
    echo "    macOS:         brew install python@3.11"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi

echo "  Using: $($PYTHON --version)"
echo ""

"$PYTHON" "$SCRIPT_DIR/install.py"
