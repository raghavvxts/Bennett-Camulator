#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ -d "venv" ]; then
    source venv/bin/activate
fi
exec python3 service.py
