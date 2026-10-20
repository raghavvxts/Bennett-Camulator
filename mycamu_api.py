import os
import json
import math
import logging
from datetime import datetime
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
                "cookies": session.cookies.get_dict()
            }
        except Exception as e:
            return False, str(e), None

    def verify_session(self) -> bool:
        if not self.stu_id:
            return False
        try:
            res = self.session.post(f"{self.API_URL}/Leave/getAllleave/", json={"StuID": self.stu_id}, timeout=8)
            return res.status_code == 200
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
            self.session.cookies.update(details["cookies"])
            return True
        return False

    def get_leaves(self) -> list:
        if not self.ensure_authenticated():
            return []
        try:
            res = self.session.post(f"{self.API_URL}/Leave/getAllleave/", json={"StuID": self.stu_id}, timeout=8)
            if res.status_code == 200:
                return res.json().get("output", {}).get("data", [])
        except Exception as e:
            logger.error(f"Error fetching leaves: {e}")
        return []

    def get_active_pass(self) -> dict:
        """
        Determines if student is currently clocked outside campus or has an active leave scheduled for today.
        Leaves in the past (completed) are ignored.
        """
        leaves = self.get_leaves()
        today = datetime.now().date()

        for l in leaves:
            status = l.get("Stus", "")
            if status in ["Rejected", "Cancelled"]:
                continue

            start_str = l.get("start", "")[:10]
            end_str = l.get("end", "")[:10]

            try:
                start_dt = datetime.strptime(start_str, "%Y-%m-%d").date() if start_str else None
                end_dt = datetime.strptime(end_str, "%Y-%m-%d").date() if end_str else None
            except Exception:
                start_dt = end_dt = None

            actual_out = l.get("actualOutTime")
            actual_in = l.get("actualInTime")

            # 1. Physical gate check: scanned OUT and NOT scanned back IN
            if actual_out and not actual_in:
                return {
                    "has_active_pass": True,
                    "is_outside_campus": True,
                    "pass": l,
                    "reason": l.get("Rson"),
                    "type": l.get("LvTy"),
                    "status": status,
                    "start": start_str,
                    "end": end_str,
                    "message": "Student is physically checked OUT of campus through the security gate."
                }

            # 2. Check if a leave is active TODAY
            if start_dt and end_dt and start_dt <= today <= end_dt:
                if status in ["Approved", "In Progress", "In-Progress", "Submitted"]:
                    return {
                        "has_active_pass": True,
                        "is_outside_campus": False,
                        "pass": l,
                        "reason": l.get("Rson"),
                        "type": l.get("LvTy"),
                        "status": status,
                        "start": start_str,
                        "end": end_str,
                        "message": f"Active {l.get('LvTy')} is currently active for today."
                    }

        return {"has_active_pass": False, "is_outside_campus": False, "pass": None}

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

            data = res.json().get("output", {}).get("data", {})
            overall_pct = data.get("OvrAllPrcntg", 0)
            overall_present = data.get("OvrAllPCnt", 0)
            overall_total = data.get("OvrAllCnt", 0)
            month_pct = data.get("CurMnthPrcntg", 0)

            subjects = []
            for s in data.get("subjectList", []):
                name = s.get("SubjNm", "Unknown")
                code = s.get("SubjCd", "")
                pct = s.get("OvrAllPrcntg", 0)
                p = s.get("prsentCnt", 0)
                tot = s.get("all", 0)
                abs_cnt = s.get("absentCnt", 0)

                # Safe / Bunk Calculation
                if pct >= 75:
                    bunks = math.floor((p - 0.75 * tot) / 0.75) if tot > 0 else 0
                    advice = f"🟢 Safe! Can bunk {max(0, bunks)} class(es)."
                else:
                    needed = math.ceil((0.75 * tot - p) / 0.25) if tot > 0 else 0
                    advice = f"🔴 Need to attend {needed} class(es) for 75%."

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
        except Exception as e:
            return {"success": False, "message": str(e)}

    def create_day_pass(self, reason: str = "Day Out", ignore_active_warning: bool = False) -> dict:
        if not self.ensure_authenticated():
            return {"success": False, "message": "Authentication failed. Please verify credentials."}

        active = self.get_active_pass()
        if active["has_active_pass"] and active.get("is_outside_campus") and not ignore_active_warning:
            return {
                "success": False,
                "is_blocked_by_active_pass": True,
                "message": (
                    f"⚠️ You are currently recorded as checked OUT of campus at the security gate.\n\n"
                    f"Please check back in at the campus gate before applying for a new pass."
                ),
                "active_pass": active["pass"]
            }

        now = datetime.now()
        date_str = now.strftime("%d-%b-%Y")
        time_str = now.strftime("%H:%M")

        # Crucial fix: omit PtAb unless half-day leave to prevent SESSION_NOT_DEFINE
        payload = {
            "PerTy": "Student",
            "PerId": self.stu_id,
            "PerNm": self.student_name,
            "InId": self.in_id,
            "LvTy": "LEAVE_OUT_PASS",
            "Rson": reason,
            "FrDt": date_str,
            "ToDt": date_str,
            "periods": [],
            "Attachments": []
        }

        try:
            res = self.session.post(f"{self.API_URL}/Leave/crtLv", json=payload, timeout=10)
            if res.status_code == 200:
                res_json = res.json()
                output = res_json.get("output", {}).get("data", {})
                errors = res_json.get("output", {}).get("errors")

                if output and isinstance(output, dict):
                    code = output.get("code")
                    if code == "DAY_IS_HOLIDAY":
                        return {
                            "success": False,
                            "message": "⚠️ Bennett ERP Notice: Today is listed as a Holiday on the academic calendar. Day Out Passes cannot be applied on official campus holidays."
                        }
                    elif code == "LEAVE_CANNOT_BE_APPLIED_ATTENDANCE_PRESENT":
                        return {
                            "success": False,
                            "message": "⚠️ Attendance is already marked as Present for today. Day Out Pass cannot be applied."
                        }
                    elif code == "SUSPEND_STUDENT":
                        return {
                            "success": False,
                            "message": "⚠️ Student status prevents applying for leave/outpass."
                        }

                if errors and isinstance(errors, dict) and errors.get("message"):
                    return {"success": False, "message": f"MyCamu Error: {errors.get('message')}"}

                return {
                    "success": True,
                    "message": f"✅ Day Out Pass created successfully for {self.student_name}!",
                    "date": date_str,
                    "created_time": time_str,
                    "reason": reason,
                    "data": output
                }
            else:
                return {"success": False, "message": f"HTTP {res.status_code}: {res.text}"}
        except Exception as e:
            return {"success": False, "message": str(e)}
