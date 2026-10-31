import io
import json
import os
from contextlib import redirect_stdout
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import admin_users
import owner_access
import bot
import database
from test_support import isolate_database


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        isolate_database(self)
        owner_access.set_password("owner-test-password")
        for user_id in range(1, 26):
            database.save_user(user_id, f'user{user_id}@example.test', f'test-password-{user_id}',
                               f'Student {user_id}', str(user_id), 'college', {'test-session': str(user_id)})

    def test_summary_pagination_excludes_passwords_sessions_and_student_identifiers(self):
        total, first = database.user_directory(1)
        _, second = database.user_directory(2)
        self.assertEqual(total, 25)
        self.assertEqual(len(first), 20)
        self.assertEqual(len(second), 5)
        self.assertFalse({u['telegram_id'] for u in first}.intersection(u['telegram_id'] for u in second))
        self.assertEqual(set(first[0]), {'telegram_id', 'email', 'student_name', 'created_at'})
        self.assertIsInstance(first[0]['email'], str)
        self.assertEqual(database.user_directory(999)[1], [])

    def test_cli_masks_password_by_default_and_reveals_only_with_explicit_flag(self):
        for reveal in [False, True]:
            output = io.StringIO()
            args = ['admin_users.py'] + (['--show-passwords'] if reveal else [])
            with patch('sys.argv', args), patch('admin_users.getpass', return_value='owner-test-password'), redirect_stdout(output):
                admin_users.main()
            users = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertEqual(len(users), 25)
            for user in users:
                self.assertEqual('password' in user, reveal)
                self.assertNotIn('cookies', user)
                if reveal:
                    self.assertEqual(user['password'], f"test-password-{user['telegram_id']}")


class OwnerCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        isolate_database(self)
        self.enterContext(patch.dict(os.environ, {'OWNER_TELEGRAM_USERNAME':'test_owner'}))
        owner_access.set_password("owner-test-password")
        self.status = SimpleNamespace(edit_text=AsyncMock())
        self.message = SimpleNamespace(reply_text=AsyncMock(return_value=self.status), message_id=77, text="owner-test-password")
        self.update = SimpleNamespace(effective_chat=SimpleNamespace(type='private', id=42),
            effective_user=SimpleNamespace(id=42, username='test_owner'), effective_message=self.message, message=self.message)
        self.context = SimpleNamespace(args=[], user_data={}, bot=SimpleNamespace(delete_message=AsyncMock()))

    async def test_non_owner_and_unconfigured_owner_cannot_read_directory(self):
        for owner in ['', '7']:
            with patch.dict(os.environ, {'OWNER_TELEGRAM_ID': owner}), patch('bot.database.user_directory') as directory:
                await bot.users_command(self.update, self.context)
                directory.assert_not_called()
        self.message.reply_text.assert_not_awaited()

    async def test_owner_can_see_identifiers_and_passwords_but_not_sessions(self):
        database.save_user(3, 'linked@example.test', 'never-send-this-password', 'Student', 'student', 'college', {'session':'never-send-this-cookie'})
        with patch.dict(os.environ, {'OWNER_TELEGRAM_ID':'42'}):
            self.assertEqual(await bot.users_command(self.update, self.context), bot.OWNER_UNLOCK)
            await bot.receive_owner_password(self.update, self.context)
        self.context.bot.delete_message.assert_awaited_once_with(chat_id=42, message_id=77)
        text = self.status.edit_text.call_args.args[0]
        self.assertIn('linked@example.test', text)
        self.assertIn('Telegram ID: 3', text)
        self.assertIn('Password: never-send-this-password', text)
        self.assertNotIn('never-send-this-cookie', text)

    async def test_owner_directory_is_denied_in_group_chat(self):
        self.update.effective_chat.type = 'group'
        with patch.dict(os.environ, {'OWNER_TELEGRAM_ID':'42'}), patch('bot.database.user_directory') as directory:
            await bot.users_command(self.update, self.context)
            directory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
