import os
import json
import math
import logging
import hashlib
import database
from datetime import datetime
from day_pass import IST, day_pass_window
from typing import Optional, Dict, Tuple
import requests

logger = logging.getLogger(__name__)


class MyCamuClient:
    """
    Bennett University MyCamu Multi-User Client.
    Portal: https://student.bennetterp.camu.in
    """
    BASE_URL = "https://student.bennetterp.camu.in"
    API_URL = "https://student.bennetterp.camu.in/api"

    def __init__(self, email: str = None, password: str = None, cookies: dict = None, stu_id: str = None, in_id: str = None, student_name: str = None):
        self.email = email
        self.password = password
        self.stu_id = stu_id
        self.in_id = in_id
        self.student_name = student_name
        self.logged_in_id = None
        self.account_type = None

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": self.BASE_URL,
            "Referer": f"{self.BASE_URL}/"
        })

        if cookies:
            self.session.cookies.update(cookies)

    @classmethod
    def test_and_fetch_profile(cls, email: str, password: str) -> Tuple[bool, Optional[str], Optional[Dict]]:
        """
        Attempts to authenticate against Bennett portal and extracts student details.
        Returns: (success: bool, error_message: str, user_dict: dict)
        """
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": cls.BASE_URL,
            "Referer": f"{cls.BASE_URL}/"
        })

        login_url = f"{cls.BASE_URL}/login/validate"
        payload = {"Email": email, "pwd": password, "dtype": "w"}

        try:
            res = session.post(login_url, json=payload, timeout=10)
            if res.status_code == 429:
                return False, "Rate limit reached. Please wait 2-3 minutes before trying again.", None
            if res.status_code != 200:
                return False, f"Server responded with HTTP {res.status_code}", None

            res_data = res.json()
            data = res_data.get("output", {}).get("data", {})

            if "code" in data:
                code = data.get("code")
                if code == "ACC_LOCKED":
                    return False, "This account is temporarily locked due to multiple incorrect attempts.", None
                if code == "INVALID_CRED":
                    return False, "Invalid email or password.", None
                return False, data.get("message", code), None

            logindetails = data.get("logindetails", {})
            in_id = logindetails.get("InId")
            students = logindetails.get("Student", [])

            if not students:
                return False, "No student profile found linked to this parent account.", None

            student = students[0]
            stu_id = student.get("StuID")
            student_name = f"{student.get('FNa', '')} {student.get('LNa', '')}".strip()

            return True, None, {
                "student_name": student_name,
                "stu_id": stu_id,
                "in_id": in_id,
                "cookies": session.cookies.get_dict(),
                "logged_in_id": logindetails.get("_id"),
                "account_type": logindetails.get("Type")
            }
        except Exception as e:
            return False, str(e), None

    def verify_session(self) -> bool:
        if not self.stu_id:
            return False
        try:
            res = self.session.post(f"{self.API_URL}/Leave/getAllleave/", json={"StuID": self.stu_id, "isFrmReact": True}, timeout=8)
            output = res.json().get("output", {})
            return res.status_code == 200 and not output.get("errors") and isinstance(output.get("data"), list)
        except Exception:
            return False

    def ensure_authenticated(self) -> bool:
        if self.verify_session():
            return True
        if not self.email or not self.password:
            return False

        ok, _, details = self.test_and_fetch_profile(self.email, self.password)
        if ok and details:
            self.stu_id = details["stu_id"]
            self.in_id = details["in_id"]
            self.student_name = details["student_name"]
            self.logged_in_id = details["logged_in_id"]
            self.account_type = details["account_type"]
            self.session.cookies.update(details["cookies"])
            return True
        return False

    def _request(self, path, payload):
        response = self.session.post(f"{self.API_URL}/{path}", json=payload, timeout=10)
        response.raise_for_status()
        body = response.json()
        output = body.get("output") if isinstance(body, dict) else None
        if not isinstance(output, dict) or output.get("errors"):
            raise ValueError("MyCamu rejected the request; verify the record in the portal before retrying.")
        data = output.get("data")
        expected_type = list if path == "Leave/getAllleave/" else dict
        if not isinstance(data, expected_type):
            raise ValueError("MyCamu returned no data. Verify the portal before retrying.")
        return data

    def _get_records(self):
        if not self.ensure_authenticated():
            raise ValueError("Could not authenticate with MyCamu.")
        records = self._request("Leave/getAllleave/", {"StuID": self.stu_id, "isFrmReact": True})
        if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
            raise ValueError("Could not verify leave and gate-pass records.")
        return records

    def get_leaves(self):
        return self._get_records()

    def get_mess_menu(self):
        if not self.ensure_authenticated():
            raise ValueError("Could not authenticate with MyCamu to fetch today's mess menu.")
        day = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")[datetime.now(IST).weekday()]
        return self._request("mess-management/get-student-menu-list", {
            "stuId": self.stu_id, "InId": self.in_id, "day": day,
        })

    @staticmethod
    def record_status(record):
        value = record.get("status") if record.get("isGatepass") else record.get("Stus")
        return {"SUB": "Submitted", "APPRVD": "Approved", "CMPT": "Completed",
                "CNCL": "Cancelled", "REJ": "Rejected", "EXP": "Expired"}.get(value, value or "Unknown")

    @staticmethod
    def record_type(record):
        return record.get("passTyNme", "Gate Pass") if record.get("isGatepass") else record.get("LvTy", "Leave")

    def get_active_pass(self):
        try:
            records = self._get_records()
            today = datetime.now(IST).date().isoformat()
            gates = [record for record in records if record.get("isGatepass")]
            gates.sort(key=lambda record: bool(record.get("isCOut") and not record.get("isCIn")), reverse=True)
            for record in gates:
                status = self.record_status(record)
                outside = bool(record.get("isCOut") and not record.get("isCIn"))
                start, end = record.get("frdt", ""), record.get("todt", "")
                if outside or (status != "Completed" and start[:10] <= today <= end[:10]):
                    return {"success": True, "has_active_pass": True, "is_outside_campus": outside,
                            "pass": record, "reason": record.get("rson"), "type": self.record_type(record),
                            "status": status, "start": start, "end": end}
            return {"success": True, "has_active_pass": False, "is_outside_campus": False, "pass": None}
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Unable to verify gate status: %s", type(exc).__name__)
            return {"success": False, "message": "Could not verify campus gate status. Check MyCamu before retrying."}

    def get_attendance(self) -> dict:
        """
        Fetches full live attendance statistics including subject-wise breakdown & bunk calculations.
        """
        if not self.ensure_authenticated():
            return {"success": False, "message": "Failed to authenticate."}

        try:
            url = f"{self.API_URL}/Attendance/getDtaForStupage"
            res = self.session.post(url, json={"StuID": self.stu_id}, timeout=8)
            if res.status_code != 200:
                return {"success": False, "message": f"HTTP {res.status_code}"}

            body = res.json().get("output", {})
            if body.get("errors") or not isinstance(body.get("data"), dict):
                raise ValueError("MyCamu could not return attendance data.")
            data = body["data"]
            def number(value, maximum=None):
                result = float(value)
                if not math.isfinite(result) or result < 0 or (maximum is not None and result > maximum):
                    raise ValueError("MyCamu returned invalid attendance values.")
                return result
            def count(value):
                result = number(value)
                if not result.is_integer():
                    raise ValueError("MyCamu returned a non-integer class count.")
                return int(result)
            overall_pct = number(data["OvrAllPrcntg"], 100)
            overall_present = count(data["OvrAllPCnt"])
            overall_total = count(data["OvrAllCnt"])
            month_pct = number(data["CurMnthPrcntg"], 100)

            if overall_present > overall_total:
                raise ValueError("Attended classes exceed total classes.")
            subjects = []
            for s in data.get("subjectList", []):
                name = s.get("SubjNm", "Unknown")
                code = s.get("SubjCd", "")
                pct = number(s["OvrAllPrcntg"], 100)
                p = count(s["prsentCnt"])
                tot = count(s["all"])
                abs_cnt = count(s.get("absentCnt", 0))
                if p > tot:
                    raise ValueError("Attended classes exceed total classes.")

                if tot == 0:
                    advice = "No classes recorded yet."
                elif 4 * p >= 3 * tot:
                    advice = f"🟢 Safe! Can bunk {(4 * p - 3 * tot) // 3} class(es)."
                else:
                    advice = f"🔴 Need to attend {3 * tot - 4 * p} class(es) for 75%."

                subjects.append({
                    "name": name,
                    "code": code,
                    "percentage": pct,
                    "present": p,
                    "total": tot,
                    "absent": abs_cnt,
                    "advice": advice
                })

            return {
                "success": True,
                "overall_pct": overall_pct,
                "overall_present": overall_present,
                "overall_total": overall_total,
                "month_pct": month_pct,
                "subjects": subjects
            }
        except Exception as exc:
            logger.warning("Could not fetch valid attendance: %s", type(exc).__name__)
            return {"success": False, "message": "Could not fetch valid attendance data from MyCamu. Try again later."}

    def _parent_login(self):
        # Linked accounts created by older versions have no stored parent identity.
        ok, error, details = self.test_and_fetch_profile(self.email, self.password)
        if not ok or not details:
            raise ValueError(error or "Parent login failed.")
        self.session.cookies.update(details["cookies"])
        self.stu_id, self.in_id = details["stu_id"], details["in_id"]
        self.student_name = details["student_name"]
        self.logged_in_id, self.account_type = details["logged_in_id"], details["account_type"]
        if self.account_type != "parent" or not self.logged_in_id:
            raise ValueError("A parent account is required to approve a day gate pass.")

    def _approve_gate_pass(self, record):
        pass_id = record.get("_id")
        if not pass_id:
            raise ValueError("Gate pass has no ID; check MyCamu manually.")
        if self.record_status(record) != "Approved" and record.get("prntAp") != "APPRVD":
            result = self._request("gatepasses/change-gatepasses-status", {
                "id": pass_id, "roll": self.account_type, "action": "Approve",
                "PerId": self.stu_id, "loggedInId": self.logged_in_id,
            })
            if result.get("code") != "STATUS_UPDTD_SUCC":
                raise ValueError("Gate pass submitted, but approval was not confirmed. Check MyCamu; do not create another pass.")
        result = self._request("gatepasses/fetch-gatepasses-status", {"id": pass_id})
        gate = result.get("gatepass")
        if not isinstance(gate, dict) or gate.get("_id") != pass_id:
            raise ValueError("Could not verify the gate pass after approval. Check MyCamu before retrying.")
        status = self.record_status({**gate, "isGatepass": True})
        approved = status == "Approved"
        return {"success": approved, "status": status, "pass_id": pass_id,
                "parent_approved": gate.get("prntAp") == "APPRVD",
                "message": "Day Out Pass approved." if approved else "Gate pass exists, but final approval is pending. Check MyCamu before retrying.",
                "departure": gate.get("frdt"), "return": gate.get("todt")}

    def create_day_pass(self, reason="Going out", departure_at=None, ignore_active_warning=False, progress=None):
        self._operation_account = hashlib.sha256((self.email or self.stu_id or "").strip().lower().encode()).hexdigest()
        try:
            with database.pass_operation(self._operation_account):
                result = self._create_day_pass(reason, departure_at, ignore_active_warning, progress)
                if result.get("success"):
                    database.uncertain_operation(self._operation_account, False)
                return result
        except ValueError as exc:
            return {"success": False, "message": str(exc)}

    def _create_day_pass(self, reason=None, departure_at=None, ignore_active_warning=False, progress=None):
        def notify(text):
            if progress:
                try:
                    progress(text)
                except Exception:
                    logger.warning("Could not display pass progress")
        window = day_pass_window(departure_at)
        if not window["success"]:
            return window
        if not isinstance(reason, str) or not reason.strip():
            return {"success": False, "message": "An outing reason is required."}
        if ignore_active_warning:
            return {"success": False, "message": "Gate and overlap checks cannot be bypassed."}
        departure, return_at = window["departure"], window["return"]
        day = departure.date().isoformat()
        try:
            notify("1/3 Checking your leave and gate status…")
            self._parent_login()
            records = self._get_records()
            for record in records:
                if record.get("isGatepass") and record.get("isCOut") and not record.get("isCIn"):
                    return {"success": False, "message": "Your current gate pass is still checked OUT. No new pass was created. Wait until MyCamu marks it Completed; being inside campus alone does not permit another pass."}
            config = self._request("gatepasses/fetch-gatepasses-types", {})
            types = [t for t in config.get("gTypes", []) if t.get("iSDPss") and not t.get("isStuNVsb") and t.get("StFl") == "A"]
            if len(types) != 1:
                raise ValueError("Could not identify the configured Day Out Pass type.")
            pass_type = types[0]
            if pass_type.get("isManDoc"):
                raise ValueError("MyCamu now requires documents for this pass. Apply in the portal.")
            if pass_type.get("enblTm"):
                minimum = datetime.strptime(pass_type["frTm"], "%I:%M %p").time()
                maximum = datetime.strptime(pass_type["toTm"], "%I:%M %p").time()
                if not minimum <= departure.time() < return_at.time() <= maximum:
                    raise ValueError("Requested times are outside MyCamu's current pass window.")
            today = datetime.now(IST).date().isoformat()
            active = [r for r in records if r.get("isGatepass") and
                      self.record_status(r) != "Completed" and
                      any(r.get("frdt", "")[:10] <= date <= r.get("todt", "")[:10]
                          for date in (day, today))]
            if active:
                if len(active) != 1 or active[0].get("passTyCode") != pass_type["passCd"]:
                    raise ValueError("An existing gate pass is not Completed. No new pass was created. Check its status in MyCamu.")
                existing = active[0]
                if not (existing.get("frdt", "")[:10] <= day <= existing.get("todt", "")[:10]) or self.record_status(existing) not in ("Submitted", "Approved"):
                    return {"success": False, "status": self.record_status(existing),
                            "pass_id": existing.get("_id"), "departure": existing.get("frdt"),
                            "return": existing.get("todt"),
                            "message": f"Your existing gate pass is {self.record_status(existing)}. No new pass was created. Another pass can be created only after its status is Completed, regardless of whether you are inside or outside campus."}
                notify("3/3 Reusing your gate pass and checking approval…")
                database.uncertain_operation(self._operation_account, True)
                result = self._approve_gate_pass(active[0])
                result["reused_gate_pass"] = True
                return result
            if database.uncertain_operation(self._operation_account):
                raise ValueError("A previous submission has an uncertain result. No new pass was submitted. Check MyCamu; an operator must reconcile this request before retrying.")
            window = day_pass_window(departure_at)
            if not window["success"]:
                return window
            departure, return_at = window["departure"], window["return"]
            day = departure.date().isoformat()
            has_leave = any(not r.get("isGatepass") and r.get("LvTy") == "Leave" and self.record_status(r) not in ("Rejected", "Cancelled")
                            and r.get("start", "")[:10] <= day <= r.get("end", "")[:10]
                            for r in records)
            payload = {
                "PerTy": "Student", "PerId": self.stu_id, "PerNm": self.student_name,
                "InId": self.in_id, "HstleReq": "true", "GateOutPass": True,
                "Type": self.account_type, "stuId": self.stu_id, "loggedInId": self.logged_in_id,
                "passTy": pass_type["passCd"], "rson": reason.strip(), "atcmnt": [],
                "periods": [], "Attachments": [], "timezoneOffSet": 330,
                # Camu's UI sends local wall time with a literal Z, not converted UTC.
                "frdt": departure.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                "todt": return_at.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            }
            if not has_leave:
                payload.update(LvTy="Leave", Rson=reason.strip(), FrDt=day, ToDt=day)
            notify("2/3 Existing leave found; submitting only the gate pass…" if has_leave else
                   "2/3 Submitting today’s leave and gate pass…")
            database.uncertain_operation(self._operation_account, True)
            output = self._request("Leave/crtLv", payload)
            # A successful HTTP response can contain a refusal, not a created record.
            code = output.get("code")
            messages = {
                "LV_NEEDED": "MyCamu requires a leave for this date. Check existing leaves in the portal.",
                "DUP_GATEPASS_EXISTS": "A gate pass already exists. Check MyCamu before retrying.",
                "LEAVE_MISSING_ON_WORKING_DAY": "MyCamu requires a leave for this working day.",
                "DAY_IS_HOLIDAY": "MyCamu does not allow this leave on a holiday.",
                "SUSPEND_STUDENT": "Student status prevents this request.",
                "STUD_HLD_IN_CLR": "A clearance hold prevents this gate pass.",
                "LEAVE_CANNOT_BE_APPLIED_ATTENDANCE_PRESENT": "Attendance is marked present; MyCamu refused the leave.",
                "WRONG_TIME_RANGE": "The requested times are outside the allowed range.",
                "NOT_WITHIN_TIME": "The requested timing does not match MyCamu's configured periods.",
            }
            if code and code != "ONLY_GATEPASS_CRTD":
                if code in messages:
                    database.uncertain_operation(self._operation_account, False)
                raise ValueError(messages.get(code, f"MyCamu response: {code}. Check the portal before retrying."))
            if not code and not output.get("_id"):
                raise ValueError("MyCamu did not confirm creation. Check the portal before retrying.")
            # Never approve an arbitrary returned leave ID: find the matching gate record.
            matches = [r for r in self._get_records() if r.get("isGatepass") and
                       r.get("_id") not in {old.get("_id") for old in records} and
                       r.get("passTyCode") == pass_type["passCd"] and
                       r.get("frdt", "")[:16] == payload["frdt"][:16] and
                       r.get("todt", "")[:16] == payload["todt"][:16] and r.get("rson") == reason.strip()]
            if len(matches) != 1:
                raise ValueError("Submission sent, but the new gate pass could not be identified. Check MyCamu before retrying.")
            notify("3/3 Gate pass submitted; requesting parent approval and verifying status…")
            result = self._approve_gate_pass(matches[0])
            result["reused_leave"] = has_leave
            return result
        except requests.RequestException:
            return {"success": False, "message": "Portal request failed or timed out. It may have been processed: check MyCamu before retrying."}
        except (ValueError, KeyError, TypeError) as exc:
            return {"success": False, "message": str(exc)}
