#!/bin/bash
# Smart Building Management System - Linux Installation Script

set -e

# Set working directory to the project root
cd "$(dirname "$0")/.."

echo "=========================================================="
echo "Installing Smart Building Management System (Linux)"
echo "=========================================================="

# 1. System Requirements
echo "[1/4] Checking system dependencies..."
if command -v apt-get >/dev/null 2>&1; then
    echo "Installing system packages via apt..."
    sudo apt-get update -y || true
    sudo apt-get install -y python3 python3-pip python3-venv sqlite3 || true
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] python3 is required. Please install Python 3.10+."
    exit 1
fi

# 2. Virtual Environment
echo "[2/4] Setting up Python virtual environment..."
python3 -m venv venv 2>/dev/null || python3 -m venv .venv 2>/dev/null || true

# Determine venv path
if [ -f "venv/bin/python" ]; then
    PY_BIN="venv/bin/python"
elif [ -f ".venv/bin/python" ]; then
    PY_BIN=".venv/bin/python"
else
    PY_BIN="python3"
fi

# 3. Python Dependencies
echo "[3/4] Installing Python requirements..."
"$PY_BIN" -m pip install --upgrade pip || true
"$PY_BIN" -m pip install -r requirements.txt

# 4. Directories setup
echo "[4/4] Creating necessary directories..."
mkdir -p uploads

echo "=========================================================="
echo "Installation Complete!"
echo "=========================================================="
echo "To start the application, run the following command:"
echo "  bash linux/run.sh"
echo "=========================================================="
