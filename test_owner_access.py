import io
import os
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import admin_users
import bot
import database
import owner_access
from test_support import isolate_database


class OwnerPasswordTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_database(self)

    def test_only_salted_hash_is_stored(self):
        owner_access.set_password('my-owner-password')
        path = self.folder/'.owner-password.hash'
        self.assertNotIn('my-owner-password', path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(owner_access.authenticate('my-owner-password'))
        self.assertFalse(owner_access.authenticate('wrong password'))
        self.assertNotEqual(owner_access.hash_password('my-owner-password'), owner_access.hash_password('my-owner-password'))

    def test_unconfigured_access_and_short_password_fail_closed(self):
        with self.assertRaises(ValueError):
            owner_access.authenticate('anything')
        with self.assertRaises(ValueError):
            owner_access.set_password('short')

    def test_five_failures_lock_access_across_reinitialization(self):
        owner_access.set_password('my-owner-password')
        for _ in range(5):
            self.assertFalse(owner_access.authenticate('wrong-password'))
        database.init_db()
        with self.assertRaisesRegex(ValueError, 'temporarily locked'):
            owner_access.authenticate('my-owner-password')
        with patch('owner_access.time.time', return_value=time.time()+901):
            self.assertTrue(owner_access.authenticate('my-owner-password'))

    def test_environment_hash_supported_without_plaintext_password(self):
        value = owner_access.hash_password('my-owner-password')
        with patch.dict(os.environ, {'OWNER_PASSWORD_HASH':value}):
            self.assertTrue(owner_access.authenticate('my-owner-password'))
            with self.assertRaises(ValueError):
                owner_access.set_password('different-owner-password')

    def test_cli_setup_does_not_print_password_and_wrong_unlock_exports_nothing(self):
        output = io.StringIO()
        with patch('sys.argv', ['admin_users.py','--set-password']), patch('admin_users.getpass', side_effect=['my-owner-password','my-owner-password']), redirect_stdout(output):
            admin_users.main()
        self.assertNotIn('my-owner-password', output.getvalue())
        output = io.StringIO()
        with patch('sys.argv', ['admin_users.py','--show-passwords']), patch('admin_users.getpass', return_value='wrong'), patch('admin_users.database.user_directory') as directory, redirect_stdout(output), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            admin_users.main()
        directory.assert_not_called()
        self.assertEqual(output.getvalue(), '')


class OwnerUnlockTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        isolate_database(self)
        owner_access.set_password('my-owner-password')
        self.message = SimpleNamespace(text='my-owner-password', message_id=1, reply_text=AsyncMock())
        self.update = SimpleNamespace(effective_chat=SimpleNamespace(type='private', id=42), effective_user=SimpleNamespace(id=42, username='test_owner'), effective_message=self.message, message=self.message)
        self.context = SimpleNamespace(args=[], user_data={}, bot=SimpleNamespace(delete_message=AsyncMock()))
        self.enterContext(patch.dict(os.environ, {'OWNER_TELEGRAM_ID':'42', 'OWNER_TELEGRAM_USERNAME':'test_owner'}))

    async def test_owner_id_alone_does_not_decrypt_directory(self):
        with patch('bot.database.user_directory') as directory:
            self.assertEqual(await bot.users_command(self.update, self.context), bot.OWNER_UNLOCK)
            directory.assert_not_called()

    async def test_private_command_is_absent_from_public_menu_help_and_buttons(self):
        app = SimpleNamespace(bot=SimpleNamespace(set_my_commands=AsyncMock()), bot_data={})
        with patch.dict(os.environ, {'OWNER_COMMAND':'private_test_gate'}), patch('bot.bot_heartbeat', new=AsyncMock(return_value=None)):
            await bot.post_init(app)
            await app.bot_data['heartbeat_task']
            commands = app.bot.set_my_commands.call_args.args[0]
            self.assertNotIn('private_test_gate', [command.command for command in commands])
            self.assertNotIn('users', [command.command for command in commands])
            await bot.features_command(self.update, self.context)
            self.assertNotIn('/private_test_gate', self.message.reply_text.call_args.args[0])
            for row in bot.get_main_keyboard().inline_keyboard:
                for button in row:
                    self.assertNotIn('private_test_gate', button.text)
                    self.assertNotIn('private_test_gate', button.callback_data or '')

    async def test_custom_command_name_validation(self):
        with patch.dict(os.environ, {'OWNER_COMMAND':'private_test_gate'}):
            self.assertEqual(bot.owner_command_name(), 'private_test_gate')
        for name in ['pass', 'cancel', '/private', 'Name', 'x'*33, '']:
            with patch.dict(os.environ, {'OWNER_COMMAND':name}), self.assertRaises(ValueError):
                bot.owner_command_name()

    async def test_username_and_numeric_id_must_both_match(self):
        self.assertTrue(bot.is_owner(self.update))
        self.update.effective_user.username = 'someone_else'
        self.update.effective_user.first_name = 'test_owner'
        self.assertFalse(bot.is_owner(self.update))
        self.update.effective_user.username = 'test_owner'
        self.update.effective_user.id = 7
        self.assertFalse(bot.is_owner(self.update))
        self.update.effective_user.id = 42
        self.update.effective_user.username = None
        self.assertFalse(bot.is_owner(self.update))

    async def test_username_setting_accepts_at_prefix_and_case_but_not_missing_configuration(self):
        self.update.effective_user.username = 'TEST_OWNER'
        with patch.dict(os.environ, {'OWNER_TELEGRAM_USERNAME':'@test_owner'}):
            self.assertTrue(bot.is_owner(self.update))
        with patch.dict(os.environ, {'OWNER_TELEGRAM_USERNAME':''}):
            self.assertFalse(bot.is_owner(self.update))

    async def test_incorrect_password_does_not_decrypt_directory(self):
        await bot.users_command(self.update, self.context)
        self.message.text = 'wrong'
        with patch('bot.database.user_directory') as directory:
            await bot.receive_owner_password(self.update, self.context)
            directory.assert_not_called()
        self.assertNotIn('_owner_page', self.context.user_data)

    async def test_expired_request_does_not_decrypt_directory(self):
        self.context.user_data.update(_owner_page=1, _owner_requested_at=time.time()-121)
        with patch('bot.database.user_directory') as directory:
            await bot.receive_owner_password(self.update, self.context)
            directory.assert_not_called()
        self.context.bot.delete_message.assert_awaited_once()

    async def test_failed_message_deletion_cancels_unlock(self):
        from telegram.error import TelegramError
        await bot.users_command(self.update, self.context)
        self.context.bot.delete_message.side_effect = TelegramError('test deletion failure')
        with patch('bot.database.user_directory') as directory, patch('bot.owner_access.authenticate') as authenticate:
            await bot.receive_owner_password(self.update, self.context)
            directory.assert_not_called()
            authenticate.assert_not_called()

    async def test_password_mistakenly_entered_as_command_argument_is_not_echoed(self):
        self.context.args = ['private-owner-password']
        await bot.users_command(self.update, self.context)
        self.assertNotIn('private-owner-password', self.message.reply_text.call_args.args[0])

    async def test_replay_or_changed_telegram_identity_does_not_decrypt(self):
        await bot.users_command(self.update, self.context)
        self.update.effective_user.id = 7
        with patch('bot.database.user_directory') as directory:
            await bot.receive_owner_password(self.update, self.context)
            directory.assert_not_called()
        self.update.effective_user.id = 42
        with patch('bot.database.user_directory') as directory:
            await bot.receive_owner_password(self.update, self.context)
            directory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
