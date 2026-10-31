"""Additional password check for human access to the account directory."""
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import time

import database


def hash_password(password):
    if len(password) < 12:
        raise ValueError('Use an owner password with at least 12 characters.')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f'scrypt${salt.hex()}${digest.hex()}'


def configured_hash():
    value = os.getenv('OWNER_PASSWORD_HASH', '').strip()
    path = Path(database.DB_PATH).parent / '.owner-password.hash'
    if not value and path.exists():
        os.chmod(path, 0o600)
        value = path.read_text().strip()
    if not value:
        raise ValueError('Owner password is not configured. Run python admin_users.py --set-password in your private server console.')
    parts = value.split('$')
    try:
        valid = len(parts) == 3 and parts[0] == 'scrypt' and len(bytes.fromhex(parts[1])) == 16 and len(bytes.fromhex(parts[2])) == 32
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Owner password configuration is invalid.')
    return parts


def authenticate(password):
    _, salt, expected = configured_hash()
    now = time.time()
    with database.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT failures,window_start,locked_until FROM owner_auth WHERE id=1').fetchone()
        failures, start, locked = row or (0, now, 0)
        if locked > now:
            raise ValueError('Owner access is temporarily locked. Try again in 15 minutes.')
        if now-start >= 900:
            failures, start = 0, now
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
        success = hmac.compare_digest(digest, bytes.fromhex(expected))
        failures = 0 if success else failures+1
        conn.execute('INSERT OR REPLACE INTO owner_auth VALUES (1,?,?,?)',
                     (failures, start, now+900 if failures >= 5 else 0))
    return success


def set_password(password):
    if os.getenv('OWNER_PASSWORD_HASH'):
        raise ValueError('OWNER_PASSWORD_HASH is set in the environment; update that setting instead of creating a local override.')
    value = hash_password(password)
    path = Path(database.DB_PATH).parent / '.owner-password.hash'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(value+'\n')
