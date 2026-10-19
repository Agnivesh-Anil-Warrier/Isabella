import json
import ssl
import urllib.request
import urllib.error
from typing import Optional, Tuple, Dict, Any
from attendance_parser import AttendanceReport, CourseAttendance, SessionRecord

BASE_URL = "https://attendance.iiitkottayam.ac.in"
LOGIN_ENDPOINT = f"{BASE_URL}/api/auth/login"
ATTENDANCE_ENDPOINT = f"{BASE_URL}/api/students/me/attendance"
REFRESH_ENDPOINT = f"{BASE_URL}/api/auth/refresh"

class AttendanceClient:
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None

    def login(self, email: str, password: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Authenticate with https://attendance.iiitkottayam.ac.in/api/auth/login
        Returns: (success, message, token_dict_or_none)
        """
        payload = json.dumps({"email": email.strip(), "password": password}).encode("utf-8")
        req = urllib.request.Request(
            LOGIN_ENDPOINT,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )
        try:
            with urllib.request.urlopen(req, context=self.ssl_context, timeout=15) as res:
                data = json.loads(res.read().decode("utf-8"))
                if "needs2fa" in data:
                    return False, "2FA Required (TOTP challenge not supported in automated batch)", data
                if "needsEmailOtp" in data:
                    return False, "Email OTP Challenge Required", data

                self.access_token = data.get("access")
                self.refresh_token = data.get("refresh")
                return True, "Login successful", data
        except urllib.error.HTTPError as e:
            try:
                err_data = json.loads(e.read().decode("utf-8"))
                err_msg = err_data.get("error") or err_data.get("message") or f"HTTP {e.code}"
            except Exception:
                err_msg = f"HTTP {e.code}: {e.reason}"

            if e.code == 401:
                return False, "Invalid email or password", None
            elif e.code == 429:
                return False, "Rate limited. Try again in 1 minute.", None
            return False, f"Server error: {err_msg}", None
        except Exception as e:
            return False, f"Connection failed: {str(e)}", None

    def fetch_attendance(self, access_token: Optional[str] = None) -> Tuple[bool, str, Optional[AttendanceReport], Optional[dict]]:
        """
        Fetch student attendance report from /api/students/me/attendance
        Returns: (success, message, AttendanceReport_or_None, raw_json_or_None)
        """
        token = access_token or self.access_token
        if not token:
            return False, "Missing access token. Please login first.", None, None

        req = urllib.request.Request(
            ATTENDANCE_ENDPOINT,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )
        try:
            with urllib.request.urlopen(req, context=self.ssl_context, timeout=15) as res:
                raw_json = json.loads(res.read().decode("utf-8"))
                report = self.parse_attendance_json(raw_json)
                return True, "Attendance fetched successfully", report, raw_json
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, "Session expired or invalid token (401)", None, None
            return False, f"HTTP Error {e.code}: {e.reason}", None, None
        except Exception as e:
            return False, f"Failed to fetch attendance: {str(e)}", None, None

    @staticmethod
    def parse_attendance_json(data: dict) -> AttendanceReport:
        report = AttendanceReport()

        # Student Profile
        std = data.get("student") or {}
        report.student_roll = str(std.get("rollNo") or "")
        report.student_name = str(std.get("name") or "")
        branch = str(std.get("branch") or "")
        sem = str(std.get("semester") or "")
        report.student_semester = f"{branch} · Semester {sem}" if branch and sem else (branch or sem)

        # By Course
        for item in data.get("byCourse", []):
            course_obj = item.get("course") if isinstance(item.get("course"), dict) else {}
            code = course_obj.get("code") or item.get("courseCode") or ""
            name = course_obj.get("name") or item.get("courseName") or ""
            attended = int(item.get("present") or item.get("attended") or 0)
            total = int(item.get("total") or 0)
            pct = float(item.get("percentage") or (round((attended / total * 100), 1) if total else 0.0))
            sessions_str = f"{attended} of {total} counted sessions"

            report.courses.append(CourseAttendance(
                code=code,
                name=name,
                attended=attended,
                total=total,
                pct=pct,
                sessions_str=sessions_str
            ))

        # Recent Sessions
        recent_list = data.get("recent") or data.get("recentSessions") or []
        for s in recent_list:
            c_obj = s.get("course") if isinstance(s.get("course"), dict) else {}
            code = c_obj.get("code") or s.get("courseCode") or ""
            cname = c_obj.get("name") or s.get("courseName") or ""
            date = s.get("date") or ""
            start_time = s.get("startTime") or ""
            end_time = s.get("endTime") or ""
            sec = s.get("section") or ""
            venue_parts = [date]
            if start_time and end_time:
                venue_parts.append(f"{start_time} – {end_time}")
            elif start_time:
                venue_parts.append(start_time)
            if sec:
                venue_parts.append(sec)
            datetime_venue = " · ".join(venue_parts)

            status = str(s.get("status") or "Unknown").capitalize()
            if status.upper() == "PRESENT":
                status = "Present"
            elif status.upper() == "ABSENT":
                status = "Absent"

            report.recent_sessions.append(SessionRecord(
                code=code,
                course=cname,
                datetime_venue=datetime_venue,
                status=status
            ))

        return report
