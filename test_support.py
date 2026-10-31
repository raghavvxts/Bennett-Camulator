"""Give storage-using tests a fresh database, without touching linked accounts."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import database


def isolate_database(case):
    folder = case.enterContext(TemporaryDirectory())
    case.enterContext(patch('database.DB_PATH', str(Path(folder) / 'users.db')))
    case.enterContext(patch.dict('os.environ', {}, clear=False))
    os.environ.pop("CREDENTIAL_KEY", None)
    database.init_db()
    return Path(folder)
