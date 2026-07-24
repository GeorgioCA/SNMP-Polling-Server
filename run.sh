#!/usr/bin/env bash
set -e

VENV_DIR=".venv"
SCRIPT_NAME="snmp_agent.py"
REPORTS_DIR="./incoming_reports"

echo "=================================================="
echo " Starting SNMPv3 Telemetry Agent Setup & Runner "
echo "=================================================="

if [ ! -d "$REPORTS_DIR" ]; then
    echo "[+] Creating monitored directory: $REPORTS_DIR"
    mkdir -p "$REPORTS_DIR"
fi

if ! command -v python3 &> /dev/null; then
    echo "[-] Error: python3 is not installed or not in PATH."
    exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "[+] Creating virtual environment in $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
fi

echo "[+] Activating virtual environment..."
source "$VENV_DIR/bin/activate"

echo "[+] Upgrading pip and essential build tools..."
python -m pip install --upgrade pip setuptools wheel --quiet

echo "[+] Checking and updating dependencies (pysnmp, watchdog)..."
pip install --upgrade pysnmp watchdog --quiet

echo "--------------------------------------------------"
echo "[+] Dependencies up to date. Starting SNMP Server..."
echo "--------------------------------------------------"

exec python "$SCRIPT_NAME"