"""
Isabella Telegram Bot Assistant
===============================
A zero-friction, on-the-go mobile assistant for Isabella.
Controls attendance tracking, feedback auto-filling, and LMS course access
directly from your phone via Telegram.

Features:
- /attendance: Live FACTS-H attendance report, percentages & safe bunk margins
- /feedback:   Lists pending course feedback forms
- /autofill:   Auto-fills all pending feedback forms using AI (Gemini)
- /courses:    Lists enrolled semester courses
- Interactive buttons for quick one-tap actions
"""

import os
import sys
import time
import json
import logging
from typing import Dict, Any, Optional

import requests
from dotenv import load_dotenv

from lms_client import LMSClient, CourseItem, FeedbackItem
from attendance_client import AttendanceClient
from attendance_parser import AttendanceParser, AttendanceReport

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("IsabellaBot")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
FEEDBACK_CACHE_PATH = os.path.join(BASE_DIR, "feedback_cache.json")
COMPLETED_FEEDBACK_PATH = os.path.join(BASE_DIR, "completed_feedback.json")


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading config.json: {e}")
    return {}


def save_config(cfg: dict):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving config.json: {e}")


def load_completed_feedback() -> set:
    if os.path.exists(COMPLETED_FEEDBACK_PATH):
        try:
            with open(COMPLETED_FEEDBACK_PATH, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_completed_feedback(completed: set):
    try:
        with open(COMPLETED_FEEDBACK_PATH, "w", encoding="utf-8") as f:
            json.dump(sorted(list(completed)), f, indent=2)
    except Exception:
        pass


class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.session = requests.Session()
        self.lms_client = LMSClient()
        self.att_client = AttendanceClient()

    def send_message(self, chat_id: int, text: str, reply_markup: Optional[dict] = None) -> bool:
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            r = self.session.post(url, json=payload, timeout=15)
            return r.status_code == 200
        except Exception as e:
            logger.error(f"Error sending message to {chat_id}: {e}")
            return False

    def answer_callback(self, callback_id: str, text: str = ""):
        try:
            self.session.post(f"{self.base_url}/answerCallbackQuery", json={
                "callback_query_id": callback_id,
                "text": text
            }, timeout=10)
        except Exception:
            pass

    def get_main_keyboard(self) -> dict:
        return {
            "keyboard": [
                [{"text": "📊 Live Attendance"}, {"text": "📝 Pending Feedback"}],
                [{"text": "⚡ Auto-Fill All Feedback"}, {"text": "📚 My Courses"}],
                [{"text": "⚙️ Status & Settings"}]
            ],
            "resize_keyboard": True,
            "is_persistent": True
        }

    # ------------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------------

    def handle_start(self, chat_id: int):
        cfg = load_config()
        user = cfg.get("username", "Student")
        msg = (
            f"👑 <b>Welcome to Isabella Mobile Assistant, {user}!</b>\n\n"
            "Control your IIIT Kottayam academic suite anytime, directly from Telegram:\n\n"
            "• <b>📊 Live Attendance</b>: Real-time attendance % & bunk calculator\n"
            "• <b>📝 Pending Feedback</b>: Check active course evaluations\n"
            "• <b>⚡ Auto-Fill</b>: AI-powered 5-star feedback submission\n"
            "• <b>📚 My Courses</b>: View enrolled courses & materials\n\n"
            "Use the quick buttons below or type /help for commands."
        )
        self.send_message(chat_id, msg, reply_markup=self.get_main_keyboard())

    def handle_status(self, chat_id: int):
        cfg = load_config()
        lms_user = cfg.get("username", "Not set")
        att_email = cfg.get("attendance_email", "Not set")
        gemini_ok = "✅ Configured" if cfg.get("gemini_api_key") else "⚠️ Not set (heuristic fallback)"
        year = cfg.get("default_year", "3")

        msg = (
            "⚙️ <b>Isabella Configuration Status</b>\n\n"
            f"👤 <b>LMS User</b>: <code>{lms_user}</code>\n"
            f"📅 <b>Default Year</b>: Year {year}\n"
            f"📧 <b>Attendance Email</b>: <code>{att_email}</code>\n"
            f"🤖 <b>Gemini AI Solver</b>: {gemini_ok}\n"
        )
        self.send_message(chat_id, msg)

    def handle_attendance(self, chat_id: int):
        self.send_message(chat_id, "🔄 <i>Connecting to FACTS-H attendance server...</i>")
        cfg = load_config()
        email = cfg.get("attendance_email", "").strip()
        pwd = cfg.get("attendance_password", "").strip()
        token = cfg.get("attendance_access_token", "").strip()

        report: Optional[AttendanceReport] = None
        source_label = "LIVE SERVER"

        # Try existing access token first
        if token:
            ok, msg, rep, raw = self.att_client.fetch_attendance(token)
            if ok and rep:
                report = rep

        # If token expired or missing, re-login
        if not report and email and pwd:
            ok, msg, tokens = self.att_client.login(email, pwd)
            if ok and tokens:
                acc = tokens.get("access", "")
                cfg["attendance_access_token"] = acc
                cfg["attendance_refresh_token"] = tokens.get("refresh", "")
                save_config(cfg)
                ok2, msg2, rep, raw = self.att_client.fetch_attendance(acc)
                if ok2 and rep:
                    report = rep

        # Fallback to local snapshot if offline
        if not report:
            local_html = os.path.join(BASE_DIR, "attendance_portal_afterlogin.html")
            if os.path.exists(local_html):
                try:
                    report = AttendanceParser.load_from_file(local_html)
                    source_label = "OFFLINE CACHE"
                except Exception:
                    pass

        if not report:
            self.send_message(
                chat_id,
                "❌ <b>Attendance sync failed</b>.\nPlease configure attendance email and password in Isabella Settings."
            )
            return

        pct = report.overall_pct or 0.0
        pct_emoji = "🟢" if pct >= 85.0 else ("🟡" if pct >= 80.0 else "🔴")
        status_text = "EXCELLENT" if pct >= 85.0 else ("SAFE" if pct >= 80.0 else "SHORTAGE")

        lines = [
            f"📊 <b>ATTENDANCE REPORT</b> [{source_label}]",
            f"👤 <b>{report.student_roll}</b> — {report.student_name}",
            f"📈 <b>Overall: {pct:.1f}%</b> {pct_emoji} [<code>{status_text}</code>]\n",
            "━━━━━━━━━━━━━━━━━━━━━━"
        ]

        for c in report.courses:
            badge = "🟢" if c.pct >= 85.0 else ("🟡" if c.pct >= 80.0 else "🔴")
            buffer_info = f"<code>{c.buffer_str}</code>" if c.buffer_str else ""
            lines.append(
                f"{badge} <b>{c.code}</b>: {c.pct:.1f}% ({c.attended}/{c.total})\n"
                f"   └ {c.name}\n"
                f"   └ Margin: {buffer_info}"
            )

        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>Last refreshed: {time.strftime('%I:%M %p, %d %b %Y')}</i>")

        self.send_message(chat_id, "\n".join(lines))

    def handle_feedback(self, chat_id: int):
        self.send_message(chat_id, "🔍 <i>Scanning LMS for course feedback forms...</i>")
        cfg = load_config()
        user = cfg.get("username", "")
        pwd = cfg.get("password", "")

        if not self.lms_client.logged_in:
            if user and pwd:
                self.lms_client.login(user, pwd)

        items = []
        if self.lms_client.logged_in:
            courses = self.lms_client.get_courses()
            for c in courses:
                try:
                    forms = self.lms_client.get_course_feedback_forms(c)
                    items.extend(forms)
                except Exception:
                    pass

        # Fallback to cache if none found or not logged in
        if not items and os.path.exists(FEEDBACK_CACHE_PATH):
            try:
                with open(FEEDBACK_CACHE_PATH, "r", encoding="utf-8") as f:
                    for d in json.load(f):
                        items.append(FeedbackItem(
                            id=d.get("id", ""),
                            name=d.get("name", ""),
                            course_id=d.get("course_id", ""),
                            course_name=d.get("course_name", ""),
                            url=d.get("url", ""),
                            course_year=d.get("course_year")
                        ))
            except Exception:
                pass

        completed_ids = load_completed_feedback()
        pending = [f for f in items if f.id not in completed_ids]

        if not pending:
            self.send_message(
                chat_id,
                f"🎉 <b>All feedback forms are complete!</b>\nTotal completed: {len(completed_ids)} forms."
            )
            return

        lines = [
            f"📝 <b>PENDING FEEDBACK FORMS ({len(pending)})</b>\n",
        ]

        inline_buttons = []
        for f in pending[:8]:  # show top 8
            lines.append(f"• <b>{f.course_name}</b>\n  └ Form: {f.name} [ID: #{f.id}]")
            inline_buttons.append([
                {"text": f"⚡ Auto-Fill {f.id}", "callback_data": f"solve_{f.id}"}
            ])

        inline_keyboard = {"inline_keyboard": inline_buttons}
        lines.append("\nTap a button below to auto-fill an individual form, or tap <b>⚡ Auto-Fill All Feedback</b>.")
        self.send_message(chat_id, "\n".join(lines), reply_markup=inline_keyboard)

    def handle_autofill_all(self, chat_id: int):
        self.send_message(chat_id, "🚀 <b>Starting automated feedback submission...</b>\nLogging into LMS...")
        cfg = load_config()
        user = cfg.get("username", "")
        pwd = cfg.get("password", "")
        api_key = cfg.get("gemini_api_key", "")

        if not user or not pwd:
            self.send_message(chat_id, "❌ LMS credentials not configured in Isabella.")
            return

        if not self.lms_client.logged_in:
            ok, msg = self.lms_client.login(user, pwd)
            if not ok:
                self.send_message(chat_id, f"❌ LMS login failed: {msg}")
                return

        courses = self.lms_client.get_courses()
        all_forms = []
        for c in courses:
            try:
                forms = self.lms_client.get_course_feedback_forms(c)
                all_forms.extend(forms)
            except Exception:
                pass

        completed_ids = load_completed_feedback()
        pending = [f for f in all_forms if f.id not in completed_ids]

        if not pending:
            self.send_message(chat_id, "🎉 All feedback forms are already completed!")
            return

        self.send_message(chat_id, f"Found {len(pending)} pending forms. Solving with AI...")

        success_count = 0
        for f in pending:
            try:
                ok, msg, ans, method = self.lms_client.auto_fill_feedback_form(
                    f.url,
                    course_name=f.course_name,
                    api_key=api_key,
                    prefer_gemini=bool(api_key)
                )
                if ok:
                    completed_ids.add(f.id)
                    success_count += 1
                    self.send_message(chat_id, f"✅ <b>Solved</b>: {f.course_name}\n└ Method: {method}")
                else:
                    self.send_message(chat_id, f"⚠️ <b>Failed</b>: {f.course_name}\n└ Error: {msg}")
            except Exception as e:
                self.send_message(chat_id, f"❌ Error on {f.course_name}: {str(e)}")

        save_completed_feedback(completed_ids)
        self.send_message(
            chat_id,
            f"🏆 <b>Batch feedback complete!</b>\nSuccessfully submitted {success_count} of {len(pending)} forms."
        )

    def handle_solve_single(self, chat_id: int, form_id: str):
        self.send_message(chat_id, f"⏳ <i>Auto-filling feedback form #{form_id}...</i>")
        cfg = load_config()
        api_key = cfg.get("gemini_api_key", "")
        user = cfg.get("username", "")
        pwd = cfg.get("password", "")

        if not self.lms_client.logged_in:
            if user and pwd:
                self.lms_client.login(user, pwd)

        url = f"https://lms.iiitkottayam.ac.in/mod/feedback/view.php?id={form_id}"
        ok, msg, ans, method = self.lms_client.auto_fill_feedback_form(
            url,
            course_name=f"Course #{form_id}",
            api_key=api_key,
            prefer_gemini=bool(api_key)
        )
        if ok:
            completed_ids = load_completed_feedback()
            completed_ids.add(form_id)
            save_completed_feedback(completed_ids)
            self.send_message(chat_id, f"✅ <b>Form #{form_id} successfully submitted!</b>\nEngine: {method}")
        else:
            self.send_message(chat_id, f"❌ Failed to submit #{form_id}: {msg}")

    def handle_courses(self, chat_id: int):
        self.send_message(chat_id, "📚 <i>Fetching enrolled courses...</i>")
        cfg = load_config()
        user = cfg.get("username", "")
        pwd = cfg.get("password", "")

        if not self.lms_client.logged_in:
            if user and pwd:
                self.lms_client.login(user, pwd)

        courses = []
        if self.lms_client.logged_in:
            courses = self.lms_client.get_courses()

        if not courses:
            local_html = os.path.join(BASE_DIR, "courses.html")
            if os.path.exists(local_html):
                try:
                    with open(local_html, "r", encoding="utf-8") as f:
                        courses = self.lms_client.parse_courses_from_html(f.read())
                except Exception:
                    pass

        if not courses:
            self.send_message(chat_id, "⚠️ No courses found. Please check LMS login credentials.")
            return

        lines = [f"📚 <b>ENROLLED COURSES ({len(courses)})</b>\n"]
        for c in courses:
            lines.append(f"• <b>{c.name}</b> (Yr {c.year})")
            if c.url:
                lines.append(f"  └ <a href='{c.url}'>Open on LMS</a>")

        self.send_message(chat_id, "\n".join(lines))

    # ------------------------------------------------------------------------
    # Polling Loop
    # ------------------------------------------------------------------------

    def run(self):
        logger.info("🤖 Isabella Telegram Bot is ONLINE and listening for messages...")
        offset = 0

        while True:
            try:
                url = f"{self.base_url}/getUpdates?offset={offset}&timeout=30"
                resp = self.session.get(url, timeout=35)
                if resp.status_code != 200:
                    time.sleep(3)
                    continue

                data = resp.json()
                if not data.get("ok"):
                    time.sleep(3)
                    continue

                for update in data.get("result", []):
                    offset = update["update_id"] + 1

                    # Handle Callback Query (Inline Keyboard Clicks)
                    if "callback_query" in update:
                        cb = update["callback_query"]
                        cb_id = cb["id"]
                        chat_id = cb["message"]["chat"]["id"]
                        data_val = cb.get("data", "")
                        self.answer_callback(cb_id, "Processing...")

                        if data_val.startswith("solve_"):
                            form_id = data_val.replace("solve_", "")
                            self.handle_solve_single(chat_id, form_id)
                        continue

                    # Handle Text Messages
                    if "message" in update and "text" in update["message"]:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        text = msg["text"].strip()

                        if text in ["/start", "/help"]:
                            self.handle_start(chat_id)
                        elif text in ["/attendance", "📊 Live Attendance", "Attendance"]:
                            self.handle_attendance(chat_id)
                        elif text in ["/feedback", "📝 Pending Feedback", "Feedback"]:
                            self.handle_feedback(chat_id)
                        elif text in ["/autofill", "⚡ Auto-Fill All Feedback", "Autofill"]:
                            self.handle_autofill_all(chat_id)
                        elif text in ["/courses", "📚 My Courses", "Courses"]:
                            self.handle_courses(chat_id)
                        elif text in ["/status", "⚙️ Status & Settings"]:
                            self.handle_status(chat_id)
                        else:
                            self.send_message(
                                chat_id,
                                "I didn't recognize that command. Use the buttons below or type /help.",
                                reply_markup=self.get_main_keyboard()
                            )

            except requests.exceptions.RequestException as e:
                logger.warning(f"Network error in poll loop: {e}")
                time.sleep(5)
            except Exception as e:
                logger.error(f"Unexpected error in bot loop: {e}")
                time.sleep(2)


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    load_dotenv()
    cfg = load_config()

    token = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("telegram_bot_token")

    if not token:
        print("\n" + "=" * 65)
        print("🤖 ISABELLA TELEGRAM BOT SETUP")
        print("=" * 65)
        print("\nTo control Isabella from your phone via Telegram:")
        print("1. Open Telegram and search for: @BotFather")
        print("2. Send: /newbot and give it a name (e.g. 'Isabella Assistant')")
        print("3. Copy the HTTP API token that @BotFather gives you.\n")

        token = input("👉 Paste your Telegram Bot Token here: ").strip()
        if token:
            cfg["telegram_bot_token"] = token
            save_config(cfg)
            print("✓ Token saved to config.json!")
        else:
            print("❌ No token provided. Exiting.")
            sys.exit(1)

    bot = TelegramBot(token)
    bot.run()
