#!/bin/bash
set -e

echo "🚀 Initializing Bennett Camulator Service..."

# If inside a python virtualenv or directory exists, activate it
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Run SQLite DB initialization
python3 -c "import database; database.init_db()"

# Start Telegram Bot in the background if token is provided
if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
    echo "🤖 Launching Multi-User Telegram Bot..."
    python3 bot.py &
else
    echo "⚠️ TELEGRAM_BOT_TOKEN not found. Running API only."
fi

PORT_TO_USE="${PORT:-8000}"
echo "🌐 Starting FastAPI Server on port $PORT_TO_USE..."
exec uvicorn server:app --host 0.0.0.0 --port "$PORT_TO_USE"
