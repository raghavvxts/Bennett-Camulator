import unittest
from test_support import isolate_database
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import bot
from day_pass import IST


class PassConversationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        isolate_database(self)
        self.message = SimpleNamespace(reply_text=AsyncMock(), text="Shopping")
        self.query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock(), data="pass_tomorrow_yes")
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=1), effective_message=self.message,
                                      message=self.message, callback_query=None)
        self.context = SimpleNamespace(user_data={})
        self.user = {"email": "parent@example.test", "password": "test-only", "cookies": {},
                     "stu_id": "student", "in_id": "institution", "student_name": "Student"}
        self.lookup = patch("bot.database.get_user", return_value=self.user)
        self.lookup.start()
        self.addCleanup(self.lookup.stop)

    async def test_after_hours_asks_before_tomorrow(self):
        now = datetime(2026, 10, 31, 20, tzinfo=IST)
        with patch("bot.datetime") as clock, patch("bot.linked_client") as client:
            clock.now.return_value = now
            state = await bot.pass_command(self.update, self.context)
            self.assertEqual(state, bot.PASS_TOMORROW)
            self.assertNotIn("pass_departure", self.context.user_data)
            client.assert_not_called()
            self.assertIn("tomorrow", self.message.reply_text.call_args.args[0])

    async def test_confirmation_then_time_and_reason(self):
        now = datetime(2026, 10, 31, 20, tzinfo=IST)
        self.update.callback_query = self.query
        with patch("bot.datetime") as clock:
            clock.now.return_value = now
            self.assertEqual(await bot.pass_tomorrow(self.update, self.context), bot.PASS_TIME)
        self.assertEqual(self.context.user_data["pass_date"], "2026-11-01")
        self.update.callback_query = None
        self.message.text = "09:00"
        with patch("bot.day_pass_window", return_value={"success": True}):
            self.assertEqual(await bot.pass_time(self.update, self.context), bot.PASS_REASON)
        self.assertEqual(self.context.user_data["pass_departure"], "2026-11-01T09:00:00+05:30")

    async def test_cancel_does_not_create_pass(self):
        self.update.callback_query = self.query
        self.query.data = "pass_tomorrow_no"
        with patch("bot.linked_client") as client:
            self.assertEqual(await bot.pass_tomorrow(self.update, self.context), bot.ConversationHandler.END)
            client.assert_not_called()

    async def test_missing_reason_does_not_create_pass(self):
        self.message.text = "   "
        with patch("bot.linked_client") as client:
            self.assertEqual(await bot.pass_reason(self.update, self.context), bot.PASS_REASON)
            client.assert_not_called()

    async def test_close_while_entering_reason_requires_tomorrow_confirmation(self):
        with patch("bot.day_pass_window", return_value={"success": False, "needs_next_day": True, "message": "Tomorrow?"}), patch("bot.linked_client") as client:
            self.assertEqual(await bot.submit_pass(self.update, self.context), bot.PASS_TOMORROW)
            client.assert_not_called()

    async def test_success_uses_verified_result(self):
        departure = datetime(2026, 10, 31, 17, 31, tzinfo=IST)
        result = {"success": True, "status": "Approved", "departure": departure.isoformat(),
                  "return": departure.replace(hour=19, minute=0).isoformat(), "reused_leave": True}
        status = SimpleNamespace(edit_text=AsyncMock())
        self.message.reply_text.return_value = status
        client = Mock()
        client.create_day_pass.return_value = result
        with patch("bot.day_pass_window", return_value={"success": True, "departure": departure}), patch("bot.linked_client", return_value=client):
            self.assertEqual(await bot.submit_pass(self.update, self.context), bot.ConversationHandler.END)
        self.assertEqual(client.create_day_pass.call_args.kwargs["reason"], "Going out")
        self.assertIsNone(client.create_day_pass.call_args.kwargs["departure_at"])
        self.assertIn("Existing leave reused", status.edit_text.call_args.args[0])


class ShortcutTests(unittest.TestCase):
    def test_authenticated_post_forwards_departure_and_reason(self):
        from fastapi.testclient import TestClient
        import server
        client = Mock()
        client.create_day_pass.return_value = {"success": False, "message": "Test response"}
        with patch("server.API_KEY", "test-key"), patch("server.get_client", return_value=client):
            api = TestClient(server.app)
            payload = {"reason": "Shopping", "departure_at": "2026-11-01T09:00:00+05:30"}
            self.assertEqual(api.post("/quick-pass", json=payload).status_code, 403)
            client.create_day_pass.assert_not_called()
            response = api.post("/quick-pass", json=payload, headers={"X-API-Key": "test-key"})
            self.assertEqual(response.status_code, 200)
            args = client.create_day_pass.call_args.kwargs
            self.assertEqual(args["reason"], "Shopping")
            self.assertEqual(args["departure_at"].isoformat(), payload["departure_at"])
            self.assertEqual(api.post("/quick-pass", json={}, headers={"X-API-Key": "test-key"}).status_code, 200)
            self.assertEqual(client.create_day_pass.call_args.kwargs["reason"], "Going out")


if __name__ == "__main__":
    unittest.main()
