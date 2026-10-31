import unittest
from copy import deepcopy
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace

from day_pass import IST
from mycamu_api import MyCamuClient
import mess_menu
import bot


TODAY = datetime(2026, 10, 31, 17, 30, tzinfo=IST)
FEED = {"curntDte": "2026-10-31T12:00:00.000Z", "facNme": "Ground Floor", "oMealList": [
    {"mealTm": "Breakfast 07:30 AM - 09:45 AM", "msCde": "Breakfast(Sat)", "msNme": "Pav Bhaji\nBanana"},
    {"mealTm": "Lunch 12:00 PM - 03:00 PM", "msCde": "Lunch(Sat)", "msNme": "Aloo Palak\nShahi Paneer"},
    {"mealTm": "Snack 5:00 PM - 06:00 PM", "msCde": "Snack(Sat)", "msNme": "Paani Poori\nMango Tang"},
    {"mealTm": "Dinner 08:00 PM - 10:00 PM", "msCde": "Dinner(Sat)", "msNme": "Mix Veg\nSooji Halwa"},
]}


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.get_mess_menu.return_value = deepcopy(FEED)

    def test_live_dishes_and_portal_times_for_current_meal(self):
        result = mess_menu.get_meal_info(self.client, now=TODAY)
        self.assertTrue(result["success"])
        self.assertIn("Paani Poori", result["menu"])
        self.assertIn("5:00 PM - 06:00 PM", result["menu"])
        self.assertEqual(result["facility"], "Ground Floor")

    def test_selected_meal_uses_live_menu(self):
        result = mess_menu.get_meal_info(self.client, "dinner", now=TODAY)
        self.assertIn("Sooji Halwa", result["menu"])
        self.assertNotIn("Paani Poori", result["menu"])

    def test_between_services_selects_next_published_meal(self):
        result = mess_menu.get_meal_info(self.client, now=TODAY.replace(hour=19))
        self.assertIn("Dinner 08:00 PM", result["menu"])

    def test_daily_updates_are_refetched(self):
        mess_menu.get_meal_info(self.client, "snacks", now=TODAY)
        self.client.get_mess_menu.return_value["oMealList"][2]["msNme"] = "Updated college snack"
        result = mess_menu.get_meal_info(self.client, "snacks", now=TODAY)
        self.assertIn("Updated college snack", result["menu"])
        self.assertEqual(self.client.get_mess_menu.call_count, 2)

    def test_failure_has_no_hardcoded_fallback(self):
        self.client.get_mess_menu.side_effect = ConnectionError()
        result = mess_menu.get_meal_info(self.client, now=TODAY)
        self.assertFalse(result["success"])
        self.assertNotIn("menu", result)

    def test_old_date_missing_meal_or_empty_feed_not_shown_as_today(self):
        for field, value in [("curntDte", "2026-10-30T12:00:00Z"), ("oMealList", []),
                             ("oMealList", FEED["oMealList"][:2])]:
            feed = deepcopy(FEED)
            feed[field] = value
            self.client.get_mess_menu.return_value = feed
            self.assertFalse(mess_menu.get_meal_info(self.client, "snacks", now=TODAY)["success"])

    def test_ist_date_is_used_at_midnight(self):
        self.client.get_mess_menu.return_value["curntDte"] = "2026-10-30T19:00:00Z"
        self.assertTrue(mess_menu.get_meal_info(self.client, now=TODAY.replace(hour=0))["success"])

    def test_request_scoped_to_linked_student_and_ist_day(self):
        client = MyCamuClient(stu_id="friend-student", in_id="college")
        client.ensure_authenticated = Mock(return_value=True)
        client._request = Mock(return_value=FEED)
        with patch("mycamu_api.datetime", wraps=datetime) as clock:
            clock.now.return_value = TODAY
            client.get_mess_menu()
        client._request.assert_called_once_with("mess-management/get-student-menu-list", {
            "stuId": "friend-student", "InId": "college", "day": "SAT"})

    def test_leave_history_request_is_scoped_to_linked_student(self):
        client = MyCamuClient(stu_id="friend-student")
        client.ensure_authenticated = Mock(return_value=True)
        client._request = Mock(return_value=[])
        self.assertEqual(client.get_leaves(), [])
        client._request.assert_called_once_with("Leave/getAllleave/", {"StuID": "friend-student", "isFrmReact": True})


class MenuCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_unlinked_user_is_asked_to_link_account(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42), message=message)
        with patch("bot.database.get_user", return_value=None), patch("bot.linked_client") as client:
            await bot.menu_command(update, SimpleNamespace())
            client.assert_not_called()
        self.assertIn("Link your parent account", message.reply_text.call_args.args[0])

    async def test_command_fetches_linked_users_live_feed_and_plain_text(self):
        status = SimpleNamespace(edit_text=AsyncMock())
        message = SimpleNamespace(reply_text=AsyncMock(return_value=status))
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42), message=message)
        client = Mock()
        result = {"success": True, "day": "Saturday", "time": "05:30 PM IST", "facility": "Ground Floor", "menu": "Paani Poori"}
        with patch("bot.database.get_user", return_value={"stu_id": "friend-student"}), patch("bot.linked_client", return_value=client), patch("bot.mess_menu.get_meal_info", return_value=result) as fetch:
            await bot.menu_command(update, SimpleNamespace())
            fetch.assert_called_once_with(client)
        self.assertIn("Paani Poori", status.edit_text.call_args.args[0])
        self.assertNotIn("parse_mode", status.edit_text.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
