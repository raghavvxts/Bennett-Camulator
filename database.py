import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
import hashlib
import secrets
from contextlib import contextmanager, closing

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()
DB_PATH = str(Path(os.getenv('CAMULATOR_DATA_DIR', str(Path(__file__).resolve().parent))).resolve() / 'users.db')


def cipher():
    key = os.getenv('CREDENTIAL_KEY')
    if key:
        return Fernet(key.encode())
    path = Path(DB_PATH).parent / '.credential.key'
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(Fernet.generate_key())
    os.chmod(path, 0o600)
    return Fernet(path.read_bytes().strip())


def encrypt(value):
    return 'enc:v1:' + cipher().encrypt(value.encode()).decode()


def decrypt(value):
    if not value.startswith('enc:v1:'):
        raise ValueError('Unencrypted credential; run database initialization before reading.')
    return cipher().decrypt(value[7:].encode()).decode()


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(DB_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    os.chmod(DB_PATH, 0o600)
    migrated = False
    with connect() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA secure_delete=ON')
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY, email TEXT NOT NULL, password TEXT NOT NULL,
            student_name TEXT, stu_id TEXT, in_id TEXT, cookies TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS pass_drafts (
            telegram_id INTEGER PRIMARY KEY, payload TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS pass_operations (
            account TEXT PRIMARY KEY, owner TEXT, expires REAL DEFAULT 0, uncertain INTEGER DEFAULT 0)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS heartbeat (name TEXT PRIMARY KEY, updated REAL NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS shortcut_tokens (
            token_hash TEXT PRIMARY KEY, telegram_id INTEGER UNIQUE NOT NULL)''')
        rows = conn.execute('SELECT telegram_id,email,password,cookies FROM users').fetchall()
        if any(any(value and value.startswith('enc:v1:') for value in (email,password,cookies)) for _,email,password,cookies in rows):
            if not os.getenv('CREDENTIAL_KEY') and not (Path(DB_PATH).parent / '.credential.key').exists():
                raise ValueError('Credential key missing. Restore the original key; encrypted accounts cannot be reset automatically.')
        cipher()
        for user_id, email, password, cookies in rows:
            # Validate the configured key before committing any migration.
            cookies = cookies or '{}'
            if email.startswith('enc:v1:') and password.startswith('enc:v1:') and cookies.startswith('enc:v1:'):
                decrypt(email)
                decrypt(password)
                decrypt(cookies)
                continue
            email = decrypt(email) if email.startswith('enc:v1:') else email
            password = decrypt(password) if password.startswith('enc:v1:') else password
            cookies = decrypt(cookies) if cookies.startswith('enc:v1:') else cookies
            migrated = True
            conn.execute('UPDATE users SET email=?,password=?,cookies=? WHERE telegram_id=?',
                         (encrypt(email), encrypt(password), encrypt(cookies), user_id))
    if migrated:
        with connect() as conn:
            conn.execute('VACUUM')
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    os.chmod(DB_PATH, 0o600)


def save_user(telegram_id, email, password, student_name, stu_id, in_id, cookies):
    with connect() as conn:
        conn.execute('''INSERT OR REPLACE INTO users
            (telegram_id,email,password,student_name,stu_id,in_id,cookies) VALUES (?,?,?,?,?,?,?)''',
            (telegram_id,encrypt(email),encrypt(password),student_name,stu_id,in_id,encrypt(json.dumps(cookies))))
        conn.execute('DELETE FROM pass_drafts WHERE telegram_id=?', (telegram_id,))
        conn.execute('DELETE FROM shortcut_tokens WHERE telegram_id=?', (telegram_id,))


def get_user(telegram_id):
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute('SELECT * FROM users WHERE telegram_id=?', (telegram_id,)).fetchone()
    if row is None:
        return None
    user = dict(row)
    user['email'] = decrypt(user['email'])
    user['password'] = decrypt(user['password'])
    user['cookies'] = json.loads(decrypt(user['cookies']))
    return user


def user_directory(page=1, page_size=20, include_passwords=False):
    if page < 1 or page_size < 1:
        raise ValueError('Page and page size must be positive.')
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        offset = (page-1)*page_size
        fields = 'telegram_id,email,student_name,created_at' + (',password' if include_passwords else '')
        rows = conn.execute(f'''SELECT {fields} FROM users
            ORDER BY created_at DESC,telegram_id DESC LIMIT ? OFFSET ?''', (page_size,offset)).fetchall() if offset < total else []
    users = [dict(row) for row in rows]
    for user in users:
        user['email'] = decrypt(user['email'])
        if include_passwords:
            user['password'] = decrypt(user['password'])
    return total, users


def update_user_cookies(telegram_id, cookies):
    with connect() as conn:
        conn.execute('UPDATE users SET cookies=? WHERE telegram_id=?', (encrypt(json.dumps(cookies)),telegram_id))


def delete_user(telegram_id):
    with connect() as conn:
        conn.execute('DELETE FROM users WHERE telegram_id=?', (telegram_id,))
        conn.execute('DELETE FROM pass_drafts WHERE telegram_id=?', (telegram_id,))
        conn.execute('DELETE FROM shortcut_tokens WHERE telegram_id=?', (telegram_id,))


def issue_shortcut_token(telegram_id):
    token = secrets.token_urlsafe(32)
    with connect() as conn:
        if not conn.execute('SELECT 1 FROM users WHERE telegram_id=?', (telegram_id,)).fetchone():
            raise ValueError('Link your account first using /start.')
        conn.execute('INSERT OR REPLACE INTO shortcut_tokens VALUES (?,?)',
                     (hashlib.sha256(token.encode()).hexdigest(), telegram_id))
    return token


def shortcut_user(token):
    with connect() as conn:
        row = conn.execute('SELECT telegram_id FROM shortcut_tokens WHERE token_hash=?',
                           (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    return get_user(row[0]) if row else None


def save_draft(telegram_id, payload):
    with connect() as conn:
        conn.execute('INSERT OR REPLACE INTO pass_drafts VALUES (?,?)', (telegram_id,json.dumps(payload)))


def get_draft(telegram_id):
    with connect() as conn:
        row = conn.execute('SELECT payload FROM pass_drafts WHERE telegram_id=?', (telegram_id,)).fetchone()
    return json.loads(row[0]) if row else None


def delete_draft(telegram_id):
    with connect() as conn:
        conn.execute('DELETE FROM pass_drafts WHERE telegram_id=?', (telegram_id,))


@contextmanager
def pass_operation(account):
    owner = uuid.uuid4().hex
    with connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT expires FROM pass_operations WHERE account=?', (account,)).fetchone()
        if row and row[0] > time.time():
            raise ValueError('A pass request for this account is already running. Please wait.')
        conn.execute('''INSERT INTO pass_operations(account,owner,expires) VALUES (?,?,?)
            ON CONFLICT(account) DO UPDATE SET owner=excluded.owner,expires=excluded.expires''',
            (account,owner,time.time()+300))
    try:
        yield account
    finally:
        with connect() as conn:
            conn.execute('UPDATE pass_operations SET owner=NULL,expires=0 WHERE account=? AND owner=?', (account,owner))


def uncertain_operation(account, value=None):
    with connect() as conn:
        if value is not None:
            conn.execute('UPDATE pass_operations SET uncertain=? WHERE account=?', (int(value),account))
        row = conn.execute('SELECT uncertain FROM pass_operations WHERE account=?', (account,)).fetchone()
    return bool(row and row[0])


def heartbeat(name):
    with connect() as conn:
        conn.execute('INSERT OR REPLACE INTO heartbeat VALUES (?,?)', (name,time.time()))


def heartbeat_age(name):
    with connect() as conn:
        row = conn.execute('SELECT updated FROM heartbeat WHERE name=?', (name,)).fetchone()
    return time.time()-row[0] if row else float('inf')


def backup_database():
    folder = Path(DB_PATH).parent / 'backups'
    folder.mkdir(exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    target = folder / time.strftime('users-%Y-%m-%d.sqlite', time.gmtime())
    if not target.exists():
        temporary = target.with_suffix('.tmp')
        fd = os.open(temporary, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(temporary, 0o600)
        with connect() as source, closing(sqlite3.connect(str(temporary))) as destination:
            source.backup(destination)
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    for old in sorted(folder.glob('users-????-??-??.sqlite'))[:-7]:
        old.unlink()
    return target


init_db()
