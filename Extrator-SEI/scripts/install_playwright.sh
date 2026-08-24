#!/usr/bin/env bash
# Optional helper to install Playwright browsers locally (development)
set -euo pipefail

# prefer venv python if active
PYTHON=${PYTHON:-python}

echo "Installing Playwright package and browsers..."
${PYTHON} -m pip install --upgrade pip
${PYTHON} -m pip install playwright
# the --with-deps flag installs system dependencies when possible
${PYTHON} -m playwright install --with-deps chromium

echo "Playwright and Chromium installed."
