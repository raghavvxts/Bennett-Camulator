import unittest
from test_support import isolate_database
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import requests
from day_pass import IST, day_pass_window
from mycamu_api import MyCamuClient


NOW = datetime(2026, 10, 31, 12, tzinfo=IST)
DEPARTURE = NOW.replace(hour=17, minute=31)
GATE = {"_id": "gate-1", "isGatepass": True, "passTy": "type-id", "passTyCode": "DOP", "status": "SUB",
        "frdt": "2026-10-31T17:31:00.000Z", "todt": "2026-10-31T19:00:00.000Z", "rson": "Shopping"}
LEAVE = {"_id": "leave-1", "LvTy": "Leave", "Stus": "Submitted",
         "start": "2026-10-31T00:00:00.000Z", "end": "2026-10-31T00:00:00.000Z"}
TYPE = {"_id": "type-id", "passCd": "DOP", "iSDPss": True, "StFl": "A", "isManDoc": False,
        "enblTm": True, "frTm": "6:00 AM", "toTm": "7:00 PM"}


class WindowTests(unittest.TestCase):
    def test_current_time_rounds_forward_and_returns_at_seven(self):
        window = day_pass_window(now=NOW)
        self.assertEqual(window["departure"], NOW + timedelta(minutes=1))
        self.assertEqual(window["return"], NOW.replace(hour=19))

    def test_after_seven_requires_next_day_choice(self):
        for hour, minute in [(19, 0), (23, 59), (18, 59)]:
            self.assertTrue(day_pass_window(now=NOW.replace(hour=hour, minute=minute))["needs_next_day"])

    def test_before_six_and_next_day_boundaries(self):
        self.assertEqual(day_pass_window(now=NOW.replace(hour=2))["departure"].hour, 6)
        tomorrow = NOW + timedelta(days=1)
        self.assertTrue(day_pass_window(tomorrow.replace(hour=6), now=NOW)["success"])
        for value in [tomorrow.replace(hour=5), tomorrow.replace(hour=19), NOW,
                      NOW + timedelta(days=2), NOW.replace(tzinfo=None)]:
            self.assertFalse(day_pass_window(value, now=NOW)["success"])


class PortalFixture(unittest.TestCase):
    def setUp(self):
        isolate_database(self)
        self.window = patch("mycamu_api.day_pass_window", side_effect=lambda d=None: day_pass_window(d, now=NOW))
        self.window.start()
        self.addCleanup(self.window.stop)
        self.client = MyCamuClient(email="parent@example.test", stu_id="student", in_id="institution")
        self.client.logged_in_id = "parent-id"
        self.client.account_type = "parent"
        self.client._parent_login = Mock()
        self.records = [dict(LEAVE)]
        self.created = False
        self.approved = False
        self.code = "ONLY_GATEPASS_CRTD"
        self.client._get_records = Mock(side_effect=lambda: self.records + ([dict(GATE)] if self.created else []))
        self.client._request = Mock(side_effect=self.request)

    def request(self, path, payload):
        if path.endswith("fetch-gatepasses-types"):
            return {"gTypes": [TYPE]}
        if path == "Leave/crtLv":
            self.created = True
            return {"code": self.code}
        if path.endswith("change-gatepasses-status"):
            self.assertEqual(payload, {"id": "gate-1", "roll": "parent", "action": "Approve",
                                       "PerId": "student", "loggedInId": "parent-id"})
            self.approved = True
            return {"code": "STATUS_UPDTD_SUCC"}
        if path.endswith("fetch-gatepasses-status"):
            return {"gatepass": {**GATE, "prntAp": "APPRVD", "status": "APPRVD" if self.approved else "SUB"}}
        self.fail(f"Unexpected endpoint {path}")

    def create(self, **kwargs):
        return self.client.create_day_pass(reason="Shopping", departure_at=DEPARTURE, **kwargs)

    def creation_payload(self):
        return next(call.args[1] for call in self.client._request.call_args_list if call.args[0] == "Leave/crtLv")


class PortalTests(PortalFixture):
    def test_existing_leave_gate_only_then_approve_and_verify(self):
        result = self.create()
        self.assertTrue(result["success"])
        self.assertTrue(result["reused_leave"])
        payload = self.creation_payload()
        self.assertNotIn("LvTy", payload)
        self.assertNotIn("FrDt", payload)
        self.assertEqual(payload["passTy"], "DOP")
        self.assertEqual(payload["frdt"][:16], "2026-10-31T17:31")
        self.assertEqual(payload["todt"][:16], "2026-10-31T19:00")
        self.assertEqual(payload["atcmnt"], [])
        self.assertTrue(self.approved)

    def test_missing_leave_creates_real_leave_with_same_dates(self):
        self.records = []
        self.code = None
        original = self.client._request.side_effect
        def request(path, payload):
            if path == "Leave/crtLv":
                self.created = True
                return {"_id": "leave-new"}
            return original(path, payload)
        self.client._request.side_effect = request
        self.assertTrue(self.create()["success"])
        payload = self.creation_payload()
        self.assertEqual(payload["LvTy"], "Leave")
        self.assertEqual(payload["FrDt"], payload["ToDt"])
        self.assertEqual(payload["FrDt"], "2026-10-31")
        self.assertEqual(payload["Rson"], "Shopping")
        self.assertEqual(payload["PerId"], "student")
        self.assertNotEqual(payload["LvTy"], "LEAVE_OUT_PASS")

    def test_completed_gate_pass_does_not_block_second_outing(self):
        self.records.append({**GATE, "_id": "old", "status": "CMPT", "isCOut": True, "isCIn": True})
        self.assertTrue(self.create()["success"])

    def test_checked_in_but_not_completed_does_not_create_another_pass(self):
        self.records.append({**GATE, "status": "APPRVD", "isCOut": True, "isCIn": True})
        self.approved = True
        result = self.create()
        self.assertTrue(result["reused_gate_pass"])
        self.assertFalse(self.created)

    def test_other_noncompleted_statuses_never_allow_new_creation(self):
        for status in ["In Progress", "EXP", "CNCL", "REJ", "UNRECOGNIZED"]:
            with self.subTest(status=status):
                self.records = [LEAVE, {**GATE, "status": status, "isCIn": True}]
                result = self.create()
                self.assertFalse(result["success"])
                self.assertIn("Completed", result["message"])
                self.assertFalse(self.created)
                self.assertFalse(self.approved)

    def test_current_uncompleted_pass_also_blocks_new_pass_for_tomorrow(self):
        self.records.append({**GATE, "status": "APPRVD"})
        with patch("mycamu_api.datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            result = self.client.create_day_pass(reason="Going out", departure_at=DEPARTURE+timedelta(days=1))
        self.assertFalse(result["success"])
        self.assertIn("Completed", result["message"])
        self.assertFalse(self.created)

    def test_status_check_does_not_report_all_clear_for_uncompleted_gate(self):
        self.records.append({**GATE, "status": "In Progress", "isCIn": True})
        with patch("mycamu_api.datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            result = self.client.get_active_pass()
        self.assertTrue(result["has_active_pass"])

    def test_old_cancelled_or_rejected_leave_does_not_cover_today(self):
        for leave in [{**LEAVE, "Stus": "Cancelled"}, {**LEAVE, "Stus": "Rejected"},
                      {**LEAVE, "start": "2026-10-30T00:00:00Z", "end": "2026-10-30T00:00:00Z"}]:
            self.records, self.created = [leave], False
            self.assertTrue(self.create()["success"])
            self.assertEqual(self.creation_payload()["LvTy"], "Leave")
            self.client._request.reset_mock()

    def test_outside_campus_blocks_even_with_completed_status(self):
        self.records.append({**GATE, "status": "CMPT", "isCOut": True, "isCIn": False})
        self.assertFalse(self.create()["success"])
        self.client._request.assert_not_called()

    def test_submitted_gate_is_approved_without_duplicate(self):
        self.records.append(dict(GATE))
        result = self.create()
        self.assertTrue(result["success"])
        self.assertTrue(result["reused_gate_pass"])
        self.assertFalse(self.created)

    def test_approved_gate_is_reused_without_another_approval(self):
        self.records.append({**GATE, "status": "APPRVD"})
        self.approved = True
        self.assertTrue(self.create()["success"])
        self.assertFalse(any(c.args[0].endswith("change-gatepasses-status") for c in self.client._request.call_args_list))

    def test_parent_approved_but_pending_final_status_does_not_repeat_approval(self):
        self.records.append({**GATE, "prntAp": "APPRVD"})
        result = self.create()
        self.assertFalse(result["success"])
        self.assertTrue(result["parent_approved"])
        self.assertFalse(self.approved)

    def test_api_error_code_is_never_success(self):
        for code in ["LV_NEEDED", "DUP_GATEPASS_EXISTS", "DAY_IS_HOLIDAY", "UNKNOWN_CODE"]:
            with self.subTest(code=code):
                self.created, self.approved, self.code = False, False, code
                self.assertFalse(self.create()["success"])
                self.assertFalse(self.approved)

    def test_unknown_gate_creation_is_not_approved(self):
        self.client._get_records = Mock(return_value=[LEAVE])
        self.assertFalse(self.create()["success"])
        self.assertFalse(self.approved)

    def test_approval_requires_final_status(self):
        original = self.client._request.side_effect
        def request(path, payload):
            if path.endswith("fetch-gatepasses-status"):
                return {"gatepass": {**GATE, "prntAp": "APPRVD", "status": "SUB"}}
            return original(path, payload)
        self.client._request.side_effect = request
        result = self.create()
        self.assertFalse(result["success"])
        self.assertTrue(result["parent_approved"])

    def test_reason_and_bypass_validation(self):
        self.assertFalse(self.client.create_day_pass(reason="  ", departure_at=DEPARTURE)["success"])
        self.assertFalse(self.create(ignore_active_warning=True)["success"])
        self.client._parent_login.assert_not_called()

    def test_records_failure_never_creates_pass(self):
        self.client._get_records.side_effect = requests.Timeout()
        self.assertFalse(self.create()["success"])
        self.client._request.assert_not_called()

    def test_normalize_separate_status_fields(self):
        self.assertEqual(self.client.record_status(GATE), "Submitted")
        self.assertEqual(self.client.record_status(LEAVE), "Submitted")
        self.assertEqual(self.client.record_status({**GATE, "status": "CMPT"}), "Completed")

    def test_gate_status_prioritizes_student_scanned_out(self):
        self.records += [dict(GATE), {**GATE, "_id": "older", "isCOut": True, "isCIn": False}]
        with patch("mycamu_api.datetime", wraps=datetime) as clock:
            clock.now.return_value = NOW
            result = self.client.get_active_pass()
        self.assertTrue(result["is_outside_campus"])
        self.assertEqual(result["pass"]["_id"], "older")

    def test_gate_status_failure_is_not_all_clear(self):
        self.client._get_records.side_effect = requests.Timeout()
        self.assertFalse(self.client.get_active_pass()["success"])

    def test_malformed_legacy_leave_is_not_reused(self):
        self.records = [{**LEAVE, "LvTy": "LEAVE_OUT_PASS"}]
        self.assertTrue(self.create()["success"])
        self.assertEqual(self.creation_payload()["LvTy"], "Leave")

    def test_transport_checks_error_envelope_even_on_http_200(self):
        client = MyCamuClient()
        response = Mock()
        client.session.post = Mock(return_value=response)
        for body in [{"output": {"errors": {"message": "Rejected"}}},
                     {"output": {"data": None}}, {"output": {"data": []}}, []]:
            response.json.return_value = body
            with self.subTest(body=body), self.assertRaises(ValueError):
                client._request("Leave/crtLv", {})


if __name__ == "__main__":
    unittest.main()
