"""Local concurrency tests: no requests are sent to MyCamu or Telegram."""
import asyncio
from threading import Lock
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import requests
import bot
import database
from day_pass import day_pass_window
from mycamu_api import MyCamuClient
from test_day_pass import NOW, DEPARTURE, LEAVE, TYPE
from test_support import isolate_database


class MultiUserTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        isolate_database(self)

    async def run_users(self, count, fail_user=None):
        records, submissions, answers = {}, {}, {}
        for user_id in range(1, count+1):
            database.save_user(user_id, f'parent{user_id}@example.test', f'password-{user_id}',
                               f'Student {user_id}', str(user_id), 'college', {'session': str(user_id)})
            records[str(user_id)] = [dict(LEAVE)] if user_id % 2 == 0 else []
        running, peak = 0, 0
        metrics = Lock()

        def login(client):
            client.account_type = 'parent'
            client.logged_in_id = f'parent-{client.stu_id}'
            self.assertEqual(client.session.cookies.get('session'), client.stu_id)
            self.assertEqual(client.password, f'password-{client.stu_id}')
            self.assertEqual(client.email, f'parent{client.stu_id}@example.test')

        def get_records(client):
            if client.stu_id == str(fail_user):
                raise requests.Timeout()
            return list(records[client.stu_id])

        def request(client, path, payload):
            time.sleep(0.002)
            student = client.stu_id
            if path.endswith('fetch-gatepasses-types'):
                return {'gTypes': [TYPE]}
            if path == 'Leave/crtLv':
                self.assertEqual(payload['PerId'], student)
                self.assertEqual(payload['stuId'], student)
                self.assertEqual(payload['loggedInId'], f'parent-{student}')
                self.assertEqual(payload['rson'], f'reason-{student}')
                self.assertEqual('LvTy' in payload, int(student) % 2 == 1)
                self.assertNotIn(student, submissions)
                submissions[student] = dict(payload)
                records[student].append({'_id': f'gate-{student}', 'isGatepass': True,
                    'passTyCode': 'DOP', 'status': 'SUB', 'frdt': payload['frdt'], 'todt': payload['todt'],
                    'rson': payload['rson']})
                return {'code': 'ONLY_GATEPASS_CRTD'}
            gate = next(record for record in records[student] if record.get('isGatepass'))
            self.assertEqual(payload['id'], f'gate-{student}')
            if path.endswith('change-gatepasses-status'):
                self.assertEqual(payload['PerId'], student)
                self.assertEqual(payload['loggedInId'], f'parent-{student}')
                gate.update(status='APPRVD', prntAp='APPRVD')
                return {'code': 'STATUS_UPDTD_SUCC'}
            if path.endswith('fetch-gatepasses-status'):
                return {'gatepass': dict(gate)}
            self.fail(f'Unexpected portal request: {path}')

        processor = bot.ChatUpdateProcessor()
        async def handle_user(user_id):
            nonlocal running, peak
            user = database.get_user(user_id)
            database.save_draft(user_id, {'pass_reason': f'reason-{user_id}', 'state': bot.PASS_REASON})
            reason = database.get_draft(user_id)['pass_reason']
            client = bot.linked_client(user)
            def submit():
                nonlocal running, peak
                with metrics:
                    running += 1
                    peak = max(peak, running)
                try:
                    return client.create_day_pass(reason=reason, departure_at=DEPARTURE)
                finally:
                    with metrics:
                        running -= 1
            result = await bot.portal_call(submit)
            answers[user_id] = result
            if result.get('success'):
                self.assertEqual(result['pass_id'], f'gate-{user_id}')
                self.assertEqual(result['reused_leave'], user_id % 2 == 0)

        with patch.object(MyCamuClient, '_parent_login', login), patch.object(MyCamuClient, '_get_records', get_records), patch.object(MyCamuClient, '_request', request), patch('mycamu_api.day_pass_window', side_effect=lambda d=None: day_pass_window(d, now=NOW)):
            await asyncio.wait_for(asyncio.gather(*[
                processor.process_update(SimpleNamespace(effective_chat=SimpleNamespace(id=user_id)), handle_user(user_id))
                for user_id in range(1, count+1)]), 30)
        self.assertGreater(peak, 1)
        self.assertLessEqual(peak, bot.PORTAL_WORKERS)
        for user_id in range(1, count+1):
            self.assertEqual(database.get_draft(user_id)['pass_reason'], f'reason-{user_id}')
        return answers, submissions

    async def test_100_independent_users_create_and_approve_without_account_mixups(self):
        answers, submissions = await self.run_users(100)
        self.assertEqual(len(answers), 100)
        self.assertEqual(len(submissions), 100)
        self.assertTrue(all(result.get('success') for result in answers.values()))
        self.assertEqual(len({result['pass_id'] for result in answers.values()}), 100)

    async def test_one_users_portal_failure_does_not_fail_other_users(self):
        answers, submissions = await self.run_users(10, fail_user=4)
        self.assertFalse(answers[4]['success'])
        self.assertNotIn('4', submissions)
        self.assertTrue(all(value.get('success') for key,value in answers.items() if key != 4))



class PersonalShortcutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        isolate_database(self)

    async def test_100_personal_keys_route_to_their_own_accounts(self):
        import httpx
        import server
        from unittest.mock import Mock
        tokens = {}
        for user_id in range(1, 101):
            database.save_user(user_id, f'parent{user_id}@example.test', f'password-{user_id}',
                               f'Student {user_id}', str(user_id), 'college', {})
            tokens[user_id] = database.issue_shortcut_token(user_id)
        def client_for_user(user):
            self.assertEqual(user['email'], f"parent{user['stu_id']}@example.test")
            self.assertEqual(user['password'], f"password-{user['stu_id']}")
            client = Mock()
            client.create_day_pass.return_value = {'success': True, 'student': user['stu_id']}
            return client
        with patch('server.API_KEY', None), patch('server.get_client', side_effect=client_for_user):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://testserver') as api:
                async def request(user_id):
                    result = await api.post('/quick-pass', json={}, headers={'X-API-Key': tokens[user_id]})
                    self.assertEqual(result.status_code, 200)
                    self.assertEqual(result.json()['student'], str(user_id))
                await asyncio.gather(*(request(user_id) for user_id in tokens))
        with database.connect() as conn:
            hashes = {row[0] for row in conn.execute('SELECT token_hash FROM shortcut_tokens')}
        self.assertFalse(hashes.intersection(tokens.values()))

    async def test_rotating_logout_and_switching_accounts_revoke_old_keys(self):
        database.save_user(1, 'one@example.test', 'test-only', 'One', 'one', 'college', {})
        first = database.issue_shortcut_token(1)
        second = database.issue_shortcut_token(1)
        self.assertIsNone(database.shortcut_user(first))
        self.assertEqual(database.shortcut_user(second)['stu_id'], 'one')
        database.save_user(1, 'two@example.test', 'test-only', 'Two', 'two', 'college', {})
        self.assertIsNone(database.shortcut_user(second))
        third = database.issue_shortcut_token(1)
        database.delete_user(1)
        self.assertIsNone(database.shortcut_user(third))


if __name__ == '__main__':
    unittest.main()
