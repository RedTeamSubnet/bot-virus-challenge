#!/bin/bash
set -euo pipefail

echo "INFO: Running bot docker-entrypoint.sh..."

# Activate virtual environment and run the bot script
cd /app

# Add current directory to PYTHONPATH to fix imports
export PYTHONPATH="/app:${PYTHONPATH:-}"

echo "INFO: PYTHONPATH=${PYTHONPATH}"
echo "INFO: Python version: $(python --version)"
echo "INFO: Current directory: $(pwd)"
echo "INFO: Directory contents:"
ls -la

exec python -u main.py
