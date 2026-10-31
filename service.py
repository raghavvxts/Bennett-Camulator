"""Supervise the API and optional Telegram worker as one service."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from dotenv import load_dotenv

load_dotenv()
import database


def supervise(commands, backup=database.backup_database):
    stopped = threading.Event()
    def stop(signum, frame):
        stopped.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    children = []
    next_backup = 0
    try:
        for command in commands:
            children.append(subprocess.Popen(command))
        while not stopped.is_set():
            if any(child.poll() is not None for child in children):
                return 1
            if time.monotonic() >= next_backup:
                backup()
                next_backup = time.monotonic() + 86400
            stopped.wait(1)
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def main():
    os.chdir(Path(__file__).resolve().parent)
    commands = [[sys.executable, '-m', 'uvicorn', 'server:app', '--host', '0.0.0.0', '--port', os.getenv('PORT', '8000')]]
    if os.getenv('TELEGRAM_BOT_TOKEN'):
        commands.append([sys.executable, 'bot.py'])
    return supervise(commands)


if __name__ == '__main__':
    sys.exit(main())
