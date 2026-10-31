import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace

from cryptography.fernet import Fernet, InvalidToken
import bot
import database
from day_pass import IST, day_pass_window
from mycamu_api import MyCamuClient
from test_support import isolate_database
from test_day_pass import PortalFixture, NOW, GATE
import mess_menu
from test_mess_menu import FEED, TODAY


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.folder = isolate_database(self)
        self.enterContext(patch.dict(os.environ))
        os.environ.pop('CREDENTIAL_KEY', None)

    def save(self):
        database.save_user(1, 'test@example.test', 'secret-for-test', 'Test', 'student', 'college', {'session': 'cookie-for-test'})

    def test_encrypted_credentials_round_trip(self):
        self.save()
        with database.connect() as conn:
            password, cookies = conn.execute('SELECT password,cookies FROM users').fetchone()
        self.assertTrue(password.startswith('enc:v1:'))
        self.assertNotIn('secret-for-test', password)
        self.assertNotIn('cookie-for-test', cookies)
        self.assertEqual(database.get_user(1)['password'], 'secret-for-test')
        self.assertEqual(database.get_user(1)['cookies'], {'session': 'cookie-for-test'})
        self.assertEqual((self.folder/'.credential.key').stat().st_mode & 0o777, 0o600)

    def test_plaintext_migration_preserves_account(self):
        with database.connect() as conn:
            conn.execute("INSERT INTO users(telegram_id,email,password,cookies) VALUES (1,'test','old-secret','{}')")
        database.init_db()
        self.assertEqual(database.get_user(1)['password'], 'old-secret')
        self.assertNotIn(b'old-secret', Path(database.DB_PATH).read_bytes())
        database.init_db()
        self.assertEqual(database.get_user(1)['password'], 'old-secret')

    def test_wrong_key_rolls_back_and_missing_key_is_not_replaced(self):
        self.save()
        with patch.dict(os.environ, {'CREDENTIAL_KEY': Fernet.generate_key().decode()}):
            with self.assertRaises(InvalidToken):
                database.init_db()
        self.assertEqual(database.get_user(1)['password'], 'secret-for-test')
        (self.folder/'.credential.key').unlink()
        with self.assertRaisesRegex(ValueError, 'key missing'):
            database.init_db()
        self.assertFalse((self.folder/'.credential.key').exists())

    def test_backups_are_readable_and_credentials_encrypted(self):
        self.save()
        backup = database.backup_database()
        from contextlib import closing
        with closing(sqlite3.connect(backup)) as conn:
            stored = conn.execute('SELECT password FROM users').fetchone()[0]
        self.assertTrue(stored.startswith('enc:v1:'))
        self.assertEqual(database.decrypt(stored), 'secret-for-test')

    def test_lock_blocks_another_process_and_releases(self):
        script = "import database\nwith database.pass_operation('shared'): print('acquired')"
        env = {**os.environ, 'CAMULATOR_DATA_DIR': str(self.folder)}
        with database.pass_operation('shared'):
            result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('already running', result.stderr)
        result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_uncertain_state_survives_lock_release(self):
        with database.pass_operation('account'):
            database.uncertain_operation('account', True)
        with database.pass_operation('account'):
            self.assertTrue(database.uncertain_operation('account'))
        database.save_draft(1, {'state': bot.PASS_TIME, 'pass_date': '2026-11-01'})
        self.assertEqual(database.get_draft(1)['pass_date'], '2026-11-01')
        database.delete_user(1)
        self.assertIsNone(database.get_draft(1))


class TimingTests(PortalFixture):
    def test_slow_portal_checks_recalculate_immediate_departure(self):
        original = self.client._request.side_effect
        now = [NOW]
        def request(path, payload):
            if path.endswith('fetch-gatepasses-types'):
                now[0] = NOW + timedelta(minutes=3)
            if path == 'Leave/crtLv':
                self.assertEqual(payload['frdt'][11:16], '12:04')
                self.created = True
                return {'code': 'ONLY_GATEPASS_CRTD'}
            return original(path, payload)
        self.client._request.side_effect = request
        with patch('mycamu_api.day_pass_window', side_effect=lambda d=None: day_pass_window(d, now=now[0])):
            self.client.create_day_pass()
        self.assertEqual(self.creation_payload()['frdt'][11:16], '12:04')

    def test_closing_during_checks_does_not_submit(self):
        calls = [day_pass_window(now=NOW), day_pass_window(now=NOW.replace(hour=19))]
        with patch('mycamu_api.day_pass_window', side_effect=calls):
            result = self.client.create_day_pass()
        self.assertTrue(result['needs_next_day'])
        self.assertFalse(self.created)

    def test_timeout_after_write_requires_reconciliation_before_retry(self):
        import requests
        original = self.client._request.side_effect
        def request(path, payload):
            if path == 'Leave/crtLv':
                raise requests.Timeout()
            return original(path, payload)
        self.client._request.side_effect = request
        self.assertFalse(self.create()['success'])
        self.client._request.reset_mock()
        self.client._request.side_effect = original
        result = self.create()
        self.assertIn('uncertain', result['message'])
        self.assertFalse(self.created)
        self.records.append(dict(GATE))
        self.assertTrue(self.create()['success'])


class AttendanceTests(unittest.TestCase):
    def data(self, percentage=93.5, present=15, total=20):
        return {'OvrAllPrcntg': percentage, 'OvrAllPCnt': present, 'OvrAllCnt': total, 'CurMnthPrcntg': '80.5',
                'subjectList': [{'SubjNm': 'Subject_[test]', 'SubjCd': 'CODE', 'OvrAllPrcntg': percentage,
                                 'prsentCnt': present, 'all': total, 'absentCnt': total-present}]}

    def fetch(self, data):
        client = MyCamuClient()
        client.ensure_authenticated = Mock(return_value=True)
        response = Mock()
        response.status_code = 200
        response.json.return_value = {'output': {'data': data}}
        client.session.post = Mock(return_value=response)
        return client.get_attendance()

    def test_decimal_percentage_does_not_crash_bar(self):
        result = self.fetch(self.data())
        self.assertTrue(result['success'])
        self.assertIn('93.5%', bot.attendance_text(result, 'Student'))

    def test_invalid_values_are_not_presented_as_success(self):
        for value in [None, 'invalid', float('nan'), -1, 101]:
            self.assertFalse(self.fetch(self.data(percentage=value))['success'])
        self.assertFalse(self.fetch({})['success'])

    def test_bunk_advice_uses_counts_instead_of_rounded_percentage(self):
        result = self.fetch(self.data(percentage=75, present=2, total=3))
        self.assertIn('attend 1', result['subjects'][0]['advice'])
        result = self.fetch(self.data(percentage=0, present=0, total=0))
        self.assertIn('No classes', result['subjects'][0]['advice'])


class BotReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        isolate_database(self)
        self.message = SimpleNamespace(reply_text=AsyncMock())
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=3), effective_message=self.message,
            message=self.message, effective_chat=SimpleNamespace(type='private'), callback_query=None)
        self.context = SimpleNamespace(user_data={})

    async def test_group_login_and_global_account_guard(self):
        self.update.effective_chat.type = 'group'
        self.assertEqual(await bot.start_login_step(self.update, self.context), bot.ConversationHandler.END)
        with self.assertRaises(bot.ApplicationHandlerStop):
            await bot.private_chat_only(self.update, self.context)

    async def test_draft_restores_after_restart_without_submitting(self):
        day = datetime.now(IST).date().isoformat()
        database.save_draft(3, {'created_day': day, 'state': bot.PASS_REASON, 'pass_reason': 'Going out',
                               'pass_departure': f'{day}T18:30:00+05:30'})
        with patch('bot.database.get_user', return_value={'student_name': 'Student'}), patch('bot.linked_client') as client:
            self.assertEqual(await bot.pass_command(self.update, self.context), bot.PASS_REASON)
            client.assert_not_called()
        self.assertIn('06:30 PM', self.message.reply_text.call_args.args[0])
        self.assertEqual(self.context.user_data['pass_reason'], 'Going out')

    async def test_long_results_are_split_without_markdown_parsing(self):
        status = SimpleNamespace(edit_text=AsyncMock())
        await bot.show_result(status, self.message, 'A' * 9000, None)
        self.assertEqual(len(status.edit_text.call_args.args[0]), 3800)
        self.assertEqual(self.message.reply_text.call_count, 2)

    async def test_gate_button_runs_portal_request_in_worker_thread(self):
        import threading
        main_thread = threading.get_ident()
        client = Mock()
        def status():
            self.assertNotEqual(threading.get_ident(), main_thread)
            return {'success': True, 'has_active_pass': False}
        client.get_active_pass.side_effect = status
        query = SimpleNamespace(data='btn_check_status', answer=AsyncMock(),
                                edit_message_text=AsyncMock(), message=self.message)
        self.update.callback_query = query
        user = dict(email='test', password='test-only', cookies={}, stu_id='student', in_id='college', student_name='Test')
        with patch('bot.database.get_user', return_value=user), patch('bot.MyCamuClient', return_value=client):
            await bot.handle_button(self.update, self.context)
        client.get_active_pass.assert_called_once()

    async def test_heartbeat_runs_and_is_cancelled_on_shutdown(self):
        app = SimpleNamespace(bot_data={})
        app.bot_data['heartbeat_task'] = asyncio.create_task(bot.bot_heartbeat())
        await asyncio.sleep(0.05)
        self.assertLess(database.heartbeat_age('telegram'), 2)
        await bot.post_shutdown(app)
        self.assertTrue(app.bot_data['heartbeat_task'].cancelled())

    async def test_slow_chat_does_not_block_another_user_or_reorder_its_own_updates(self):
        processor = bot.ChatUpdateProcessor()
        started, release, other_done = asyncio.Event(), asyncio.Event(), asyncio.Event()
        order = []
        async def first():
            order.append('first started')
            started.set()
            await release.wait()
            order.append('first done')
        async def second():
            order.append('second started')
        async def other():
            other_done.set()
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=1))
        another = SimpleNamespace(effective_chat=SimpleNamespace(id=2))
        tasks = [asyncio.create_task(processor.process_update(update, first()))]
        try:
            await asyncio.wait_for(started.wait(), 1)
            tasks += [asyncio.create_task(processor.process_update(update, second())),
                      asyncio.create_task(processor.process_update(another, other()))]
            await asyncio.wait_for(other_done.wait(), 1)
            self.assertNotIn('second started', order)
            release.set()
            await asyncio.gather(*tasks)
            self.assertLess(order.index('first done'), order.index('second started'))
        finally:
            release.set()
            await asyncio.gather(*tasks)


class ApiReliabilityTests(unittest.TestCase):
    def setUp(self):
        isolate_database(self)

    def test_no_default_key_or_mutating_get_and_health_tracks_bot(self):
        from fastapi.testclient import TestClient
        import server
        client = TestClient(server.app)
        with patch('server.API_KEY', None):
            self.assertEqual(client.post('/quick-pass', json={}).status_code, 503)
        self.assertEqual(client.get('/quick-pass').status_code, 405)
        with patch('server.API_KEY', 'test-key'):
            self.assertEqual(client.post('/quick-pass?key=test-key', json={}).status_code, 403)
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'test-only'}):
            self.assertEqual(client.get('/health').status_code, 503)
            database.heartbeat('telegram')
            self.assertEqual(client.get('/health').status_code, 200)


class MenuUpgradesTests(unittest.TestCase):
    def test_full_day_and_finished_service(self):
        client = Mock()
        client.get_mess_menu.return_value = FEED
        result = mess_menu.get_meal_info(client, 'all', now=TODAY)
        for item in ['Pav Bhaji', 'Shahi Paneer', 'Paani Poori', 'Sooji Halwa']:
            self.assertIn(item, result['menu'])
        result = mess_menu.get_meal_info(client, now=TODAY.replace(hour=23))
        self.assertIn('finished', mess_menu.menu_text(result))


class ServiceTests(unittest.TestCase):
    def test_worker_exit_stops_sibling_and_fails_service(self):
        import service
        api, telegram = Mock(), Mock()
        api.poll.side_effect = [None, 1, 1]
        telegram.poll.return_value = None
        stopped = Mock()
        stopped.is_set.side_effect = [False, False]
        with patch('service.signal.signal'), patch('service.threading.Event', return_value=stopped), patch('service.subprocess.Popen', side_effect=[api, telegram]):
            self.assertEqual(service.supervise([['api'], ['telegram']], backup=Mock()), 1)
        telegram.terminate.assert_called_once()
        api.wait.assert_called_once()
        telegram.wait.assert_called_once()

    def test_dotenv_token_reaches_supervisor_without_shell_sourcing(self):
        from tempfile import TemporaryDirectory
        import shutil
        with TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copy(Path(__file__).parent/'service.py', root/'service.py')
            (root/'database.py').write_text('def backup_database(): pass\n')
            (root/'.env').write_text('TELEGRAM_BOT_TOKEN=test-only\n')
            env = {key:value for key,value in os.environ.items() if key != 'TELEGRAM_BOT_TOKEN'}
            script = "import service; service.supervise=lambda commands: print(any('bot.py' in c for c in commands)) or 0; service.main()"
            result = subprocess.run([sys.executable, '-c', script], cwd=root, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('True', result.stdout)


if __name__ == '__main__':
    unittest.main()
