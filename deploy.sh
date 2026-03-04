#!/bin/bash
# Deploy script: pull latest from GitHub and restart the app on cPanel/Passenger
# Usage: run from your app directory on the server: ./deploy.sh

set -e

echo "=========================================="
echo "  SHERIA CENTRIC - Deploy from GitHub"
echo "=========================================="

# Get the directory where the script lives (app root)
APP_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_ROOT"

echo "[1/3] Current directory: $APP_ROOT"

# Check if this is a git repo
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "[ERROR] Not a git repository. Run 'git clone https://github.com/mbaekimathi/sheria-centric.git .' first."
    exit 1
fi

# Fetch and pull from origin main
echo "[2/3] Pulling latest from origin main..."
git fetch origin
git pull origin main

# Restart Passenger so the app reloads (creates or touches tmp/restart.txt)
if [ -d "tmp" ]; then
    touch tmp/restart.txt
    echo "[3/3] Restart file touched. Passenger will reload the app."
else
    mkdir -p tmp
    touch tmp/restart.txt
    echo "[3/3] Created tmp/restart.txt. Passenger will reload the app."
fi

echo "=========================================="
echo "  Deploy complete."
echo "=========================================="
