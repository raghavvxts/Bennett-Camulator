import sqlite3
import json
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)
DB_PATH = "users.db"

def init_db():
    """Initializes the SQLite database for multi-user storage."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            password TEXT NOT NULL,
            student_name TEXT,
            stu_id TEXT,
            in_id TEXT,
            cookies TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def save_user(telegram_id: int, email: str, password: str, student_name: str, stu_id: str, in_id: str, cookies: dict):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO users (telegram_id, email, password, student_name, stu_id, in_id, cookies)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (telegram_id, email, password, student_name, stu_id, in_id, json.dumps(cookies)))
    conn.commit()
    conn.close()

def get_user(telegram_id: int) -> Optional[Dict]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT telegram_id, email, password, student_name, stu_id, in_id, cookies FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {
            "telegram_id": row[0],
            "email": row[1],
            "password": row[2],
            "student_name": row[3],
            "stu_id": row[4],
            "in_id": row[5],
            "cookies": json.loads(row[6]) if row[6] else {}
        }
    return None

def update_user_cookies(telegram_id: int, cookies: dict):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET cookies = ? WHERE telegram_id = ?", (json.dumps(cookies), telegram_id))
    conn.commit()
    conn.close()

def delete_user(telegram_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()

# Initialize tables on load
init_db()
