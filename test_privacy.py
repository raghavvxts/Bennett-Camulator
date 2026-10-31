import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

import bot
import database
from test_support import isolate_database


class CredentialPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_database(self)

    def test_email_password_and_session_are_encrypted_and_recoverable(self):
        database.save_user(1, 'private@example.test', 'test-private-password', 'Student', 'student', 'college', {'session':'test-private-session'})
        with database.connect() as conn:
            email,password,cookies = conn.execute('SELECT email,password,cookies FROM users').fetchone()
        for value in (email,password,cookies):
            self.assertTrue(value.startswith('enc:v1:'))
        self.assertNotIn('private@example.test', email)
        self.assertEqual(database.get_user(1)['email'], 'private@example.test')
        self.assertEqual(database.get_user(1)['password'], 'test-private-password')

    def test_existing_email_is_migrated_and_live_plaintext_removed(self):
        database.save_user(1, 'private@example.test', 'test-only', 'Student', 'student', 'college', {})
        with database.connect() as conn:
            conn.execute('UPDATE users SET email=? WHERE telegram_id=1', ('private@example.test',))
        database.init_db()
        self.assertEqual(database.get_user(1)['email'], 'private@example.test')
        self.assertNotIn(b'private@example.test', Path(database.DB_PATH).read_bytes())

    def test_database_key_and_backup_permissions(self):
        os.chmod(database.DB_PATH, 0o644)
        os.chmod(self.folder/'.credential.key', 0o644)
        database.init_db()
        backup = database.backup_database()
        self.assertEqual(Path(database.DB_PATH).stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.folder/'.credential.key').stat().st_mode & 0o777, 0o600)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(backup.parent.stat().st_mode & 0o777, 0o700)


class ErrorPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_error_handler_does_not_log_or_echo_sensitive_update(self):
        message = SimpleNamespace(text='private-password', reply_text=AsyncMock())
        update = SimpleNamespace(effective_chat=SimpleNamespace(type='private'), effective_message=message)
        context = SimpleNamespace(error=ValueError('private-password private@example.test'))
        with self.assertLogs(bot.logger, level='ERROR') as logs:
            await bot.handle_error(update, context)
        self.assertNotIn('private-password', '\n'.join(logs.output))
        self.assertNotIn('private@example.test', '\n'.join(logs.output))
        self.assertNotIn('private-password', message.reply_text.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
