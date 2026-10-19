import os
import sys
import json
import subprocess
from typing import List, Optional, Dict

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtCore import Qt, QThread, Signal, QUrl, QSize, QTimer
from PySide6.QtGui import QFont, QIcon, QColor, QDesktopServices, QCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QComboBox, QProgressBar,
    QTextEdit, QTableWidget, QTableWidgetItem, QHeaderView, QStackedWidget,
    QFrame, QAbstractItemView, QMessageBox, QFileDialog, QScrollArea,
    QDialog, QDialogButtonBox
)

import concurrent.futures
from lms_client import LMSClient, sanitize_filename, CourseItem, SectionItem, FileItem, FeedbackItem
from feedback_solver import (
    FeedbackParser,
    FeedbackAnswerGenerator,
    FeedbackFormSchema,
    FeedbackQuestion
)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
FEEDBACK_CACHE_PATH = os.path.join(os.path.dirname(__file__), "feedback_cache.json")
COMPLETED_FEEDBACK_PATH = os.path.join(os.path.dirname(__file__), "completed_feedback.json")
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def load_completed_feedback_ids() -> set:
    if os.path.exists(COMPLETED_FEEDBACK_PATH):
        try:
            with open(COMPLETED_FEEDBACK_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(str(x) for x in data)
        except Exception:
            pass
    return set()


def save_completed_feedback_ids(completed_ids: set):
    try:
        with open(COMPLETED_FEEDBACK_PATH, "w", encoding="utf-8") as f:
            json.dump(sorted(list(completed_ids)), f, indent=2)
    except Exception:
        pass


def load_cached_feedback() -> List[FeedbackItem]:
    if os.path.exists(FEEDBACK_CACHE_PATH):
        try:
            with open(FEEDBACK_CACHE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return [
                FeedbackItem(
                    id=str(it.get("id")),
                    name=it.get("name", ""),
                    course_id=str(it.get("course_id")),
                    course_name=it.get("course_name", ""),
                    url=it.get("url", ""),
                    course_year=it.get("course_year")
                )
                for it in raw
            ]
        except Exception:
            pass
    return []


def save_cached_feedback(items: List[FeedbackItem]):
    try:
        raw = [
            {
                "id": it.id,
                "name": it.name,
                "course_id": it.course_id,
                "course_name": it.course_name,
                "url": it.url,
                "course_year": it.course_year
            }
            for it in items
        ]
        with open(FEEDBACK_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2)
    except Exception:
        pass


def get_saved_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if "gemini_api_key" not in cfg:
                    cfg["gemini_api_key"] = ""
                return cfg
        except Exception:
            pass
    return {
        "username": "",
        "password": "",
        "default_year": "3",
        "remember": True,
        "gemini_api_key": ""
    }


def save_config(username: str, password: str, default_year: str = "3", remember: bool = True, gemini_api_key: Optional[str] = None):
    cfg = get_saved_config()
    cfg["username"] = username if remember else ""
    cfg["password"] = password if remember else ""
    cfg["default_year"] = str(default_year)
    cfg["remember"] = bool(remember)
    if gemini_api_key is not None:
        cfg["gemini_api_key"] = gemini_api_key
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ============================================================================
# Background Worker Threads for Ultra-Responsive GUI (PySide6)
# ============================================================================

class LoginWorker(QThread):
    finished = Signal(bool, str)

    def __init__(self, client: LMSClient, username: str, password: str):
        super().__init__()
        self.client = client
        self.username = username
        self.password = password

    def run(self):
        success, msg = self.client.login(self.username, self.password)
        self.finished.emit(success, msg)


class FetchCoursesWorker(QThread):
    finished = Signal(list, str)

    def __init__(self, client: LMSClient):
        super().__init__()
        self.client = client

    def run(self):
        try:
            courses = self.client.get_courses()
            self.finished.emit(courses, "")
        except Exception as e:
            self.finished.emit([], str(e))


class ScanFeedbackWorker(QThread):
    progress = Signal(int, int, str)        # current, total, status_text
    found_feedback = Signal(object)         # FeedbackItem
    finished = Signal(list, str)            # feedbacks, error_str

    def __init__(self, client: LMSClient, courses: List[CourseItem]):
        super().__init__()
        self.client = client
        self.courses = courses
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        all_feedback: List[FeedbackItem] = []
        courses_to_scan = list(self.courses)

        # Fallback if no courses provided: check cached feedback or local HTML reference
        if not courses_to_scan:
            cached = load_cached_feedback()
            if cached:
                for item in cached:
                    all_feedback.append(item)
                    self.found_feedback.emit(item)
                self.finished.emit(all_feedback, "")
                return

            local_sample = os.path.join(os.path.dirname(__file__), "a_course.html")
            if os.path.exists(local_sample):
                try:
                    with open(local_sample, "r", encoding="utf-8") as f:
                        html = f.read()
                    c141 = CourseItem(id="141", name="CSS311 Parallel and Distributed Computing", url="https://lmsug24.iiitkottayam.ac.in/course/view.php?id=141", year=3)
                    items = self.client.extract_feedback_forms_from_html(html, c141)
                    for item in items:
                        all_feedback.append(item)
                        self.found_feedback.emit(item)
                    self.finished.emit(all_feedback, "")
                    return
                except Exception:
                    pass

        total_courses = len(courses_to_scan)
        completed_count = 0

        def scan_single_course(course: CourseItem) -> List[FeedbackItem]:
            if self.is_cancelled:
                return []
            try:
                local_sample = os.path.join(os.path.dirname(__file__), "a_course.html")
                if not self.client.logged_in and course.id == "141" and os.path.exists(local_sample):
                    with open(local_sample, "r", encoding="utf-8") as f:
                        return self.client.extract_feedback_forms_from_html(f.read(), course)
                return self.client.get_course_feedback_forms(course)
            except Exception:
                return []

        # Concurrent scan across courses (high speed 6 worker threads)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            future_to_course = {executor.submit(scan_single_course, c): c for c in courses_to_scan}
            for future in concurrent.futures.as_completed(future_to_course):
                if self.is_cancelled:
                    break
                completed_count += 1
                c = future_to_course[future]
                self.progress.emit(completed_count, total_courses, f"> SCANNING [{completed_count}/{total_courses}]: {c.name[:35]}...")
                try:
                    items = future.result()
                    for item in items:
                        all_feedback.append(item)
                        self.found_feedback.emit(item)
                except Exception:
                    pass

        self.finished.emit(all_feedback, "")


class AutoFillFeedbackWorker(QThread):
    progress = Signal(int, int, str)
    log_message = Signal(str, str)
    item_finished = Signal(str, bool, str, str, dict)
    finished = Signal(int, int)

    def __init__(self, client: LMSClient, items: List[FeedbackItem], api_key: str = ""):
        super().__init__()
        self.client = client
        self.items = items
        self.api_key = api_key
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        success_count = 0
        fail_count = 0
        total = len(self.items)

        for idx, fb in enumerate(self.items):
            if self.is_cancelled:
                break

            c_name = fb.course_name or f"Course #{fb.course_id}"
            self.progress.emit(idx + 1, total, f"> AUTO-FILLING [{idx+1}/{total}]: #{fb.id} ({c_name[:25]})...")
            self.log_message.emit(f"> [AUTO-FILL] Processing form #{fb.id} for {c_name}...", "#4dd8e6")

            # Local sample fallback for offline / mock testing when unauthenticated
            local_feedback_html = os.path.join(os.path.dirname(__file__), "feedback.html")
            if not self.client.logged_in and fb.id == "6296" and os.path.exists(local_feedback_html):
                try:
                    with open(local_feedback_html, "r", encoding="utf-8") as f:
                        schema, _ = FeedbackParser.parse_html(f.read())
                    if schema:
                        answers, method = FeedbackAnswerGenerator.generate_answers(
                            schema, course_name=fb.course_name, api_key=self.api_key, prefer_gemini=bool(self.api_key)
                        )
                        self.log_message.emit(f"> [AUTO-FILL] (Offline mode) Evaluated #{fb.id} via {method}: {len(answers)} fields positive [OK]", "#33ff66")
                        self.item_finished.emit(fb.id, True, "Completed (Offline simulated)", method, answers)
                        success_count += 1
                        continue
                except Exception:
                    pass

            try:
                ok, msg, answers, method = self.client.auto_fill_feedback(
                    feedback_url=fb.url,
                    course_name=fb.course_name,
                    api_key=self.api_key,
                    prefer_gemini=bool(self.api_key)
                )

                if ok:
                    success_count += 1
                    ans_count = len(answers)
                    self.log_message.emit(f"> [AUTO-FILL] #{fb.id} submitted via {method} ({ans_count} fields answered positive) [OK]", "#33ff66")
                    self.item_finished.emit(fb.id, True, msg, method, answers)
                else:
                    fail_count += 1
                    self.log_message.emit(f"> [AUTO-FILL] #{fb.id} submission failed: {msg}", "#ff5f56")
                    self.item_finished.emit(fb.id, False, msg, method, answers)

            except Exception as e:
                fail_count += 1
                self.log_message.emit(f"> [AUTO-FILL] Exception on #{fb.id}: {e}", "#ff5f56")
                self.item_finished.emit(fb.id, False, str(e), "ERROR", {})

        self.finished.emit(success_count, fail_count)


class DownloadWorker(QThread):
    progress = Signal(int, int, str)       # current_course, total_courses, status_text
    log = Signal(str, str)                 # text, level ('info', 'success', 'skipped', 'error')
    finished = Signal(dict)                # summary stats

    def __init__(self, client: LMSClient, courses: List[CourseItem], use_zip: bool):
        super().__init__()
        self.client = client
        self.courses = courses
        self.use_zip = use_zip
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        total_courses = len(self.courses)
        stats = {"downloaded": 0, "skipped": 0, "failed": 0}

        self.log.emit(f"> STARTING MATERIAL DOWNLOAD FOR {total_courses} COURSE(S)...", "info")

        for idx, course in enumerate(self.courses, 1):
            if self.is_cancelled:
                self.log.emit("> [ABORT] DOWNLOAD CANCELLED BY USER.", "skipped")
                break

            course_folder_name = sanitize_filename(f"{course.name}")
            course_dir = os.path.join(DOWNLOAD_DIR, course_folder_name)
            os.makedirs(course_dir, exist_ok=True)

            self.progress.emit(idx, total_courses, f"> [{idx}/{total_courses}] {course.name[:35]}...")
            self.log.emit(f"\n> [SCAN] [{idx}/{total_courses}] {course.name}", "info")

            try:
                sections = self.client.get_course_sections(course.id)
            except Exception as e:
                self.log.emit(f"> [ERR] Failed to fetch contents for {course.name}: {e}", "error")
                stats["failed"] += 1
                continue

            has_materials = False
            for section in sections:
                if self.is_cancelled:
                    break

                sec_folder_name = sanitize_filename(section.title)
                sec_dir = os.path.join(course_dir, sec_folder_name)
                os.makedirs(sec_dir, exist_ok=True)

                for act in section.activities:
                    if self.is_cancelled:
                        break

                    if act.type == "folder":
                        has_materials = True
                        target_folder_dir = os.path.join(sec_dir, sanitize_filename(act.name))
                        os.makedirs(target_folder_dir, exist_ok=True)

                        if self.use_zip:
                            zip_dest = os.path.join(sec_dir, sanitize_filename(f"{act.name}.zip"))
                            if os.path.exists(zip_dest) and os.path.getsize(zip_dest) > 0:
                                self.log.emit(f"  > [SKIP] ZIP archive exists: {os.path.basename(zip_dest)}", "skipped")
                                stats["skipped"] += 1
                            else:
                                self.log.emit(f"  > [ZIP] Pulling folder archive: {act.name}...", "info")
                                ok = self.client.download_folder_zip(act.id, sec_dir, act.name, extract=True)
                                if ok:
                                    self.log.emit(f"  > [SAVED] {act.name} extracted", "success")
                                    stats["downloaded"] += 1
                                else:
                                    self.log.emit(f"  > [ERR] ZIP download failed: {act.name}", "error")
                                    stats["failed"] += 1
                        else:
                            try:
                                f_items = self.client.get_folder_files(act.id)
                                for fi in f_items:
                                    if self.is_cancelled:
                                        break
                                    file_dest = os.path.join(target_folder_dir, sanitize_filename(fi.name))
                                    if os.path.exists(file_dest) and os.path.getsize(file_dest) > 0:
                                        self.log.emit(f"  > [SKIP] File exists: {fi.name}", "skipped")
                                        stats["skipped"] += 1
                                    else:
                                        ok = self.client.download_file(fi.url, target_folder_dir, fi.name)
                                        if ok:
                                            size_mb = os.path.getsize(file_dest) / (1024 * 1024)
                                            self.log.emit(f"  > [SAVED] {fi.name} ({size_mb:.2f} MB)", "success")
                                            stats["downloaded"] += 1
                                        else:
                                            self.log.emit(f"  > [ERR] File download failed: {fi.name}", "error")
                                            stats["failed"] += 1
                            except Exception as e:
                                self.log.emit(f"  > [ERR] Folder reading error: {e}", "error")
                                stats["failed"] += 1

                    elif act.type == "resource":
                        has_materials = True
                        dl_path = self.client.download_single_resource(act.id, sec_dir, act.name)
                        if dl_path:
                            size_mb = os.path.getsize(dl_path) / (1024 * 1024) if os.path.exists(dl_path) else 0
                            self.log.emit(f"  > [SAVED] Resource: {act.name} ({size_mb:.2f} MB)", "success")
                            stats["downloaded"] += 1
                        else:
                            self.log.emit(f"  > [ERR] Resource download failed: {act.name}", "error")
                            stats["failed"] += 1

            if not has_materials:
                self.log.emit("  > [INFO] No downloadable files or attachments detected.", "info")

        self.finished.emit(stats)


# ============================================================================
# Main Retro Terminal Desktop Application Window (PySide6)
# ============================================================================

class LMSMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ISABELLA.LMS // RETRO TERMINAL")
        self.resize(1180, 800)
        self.setMinimumSize(960, 680)

        self.client = LMSClient()
        self.all_courses: List[CourseItem] = []
        self.current_filtered_courses: List[CourseItem] = []
        self.selected_year_filter: str = "all"  # "all", "1", "2", "3", "4", "other"
        self.download_worker: Optional[DownloadWorker] = None

        # Feedback Directory state
        self.all_feedback_items: List[FeedbackItem] = []
        self.filtered_feedback_items: List[FeedbackItem] = []
        self.selected_feedback_year_filter: str = "all"
        self.feedback_worker: Optional[ScanFeedbackWorker] = None
        self.completed_feedback_ids: set = load_completed_feedback_ids()
        self.feedback_action_buttons: Dict[str, QPushButton] = {}
        self.autofill_worker: Optional[AutoFillFeedbackWorker] = None
        self.gemini_api_key: str = get_saved_config().get("gemini_api_key", "")

        # Blinking cursor state
        self.cursor_state = True
        self.current_status_msg = "> SYSTEM READY • SELECT TARGET MODULES"
        self.cursor_timer = QTimer(self)
        self.cursor_timer.timeout.connect(self.blink_cursor)
        self.cursor_timer.start(530)

        self.apply_retro_terminal_theme()
        self.init_ui()
        self.load_saved_credentials()
        self.update_ai_mode_badge()

    def blink_cursor(self):
        self.cursor_state = not self.cursor_state
        cursor_char = "█" if self.cursor_state else " "
        self.lbl_progress_status.setText(f"{self.current_status_msg} {cursor_char}")

    def update_status(self, msg: str):
        self.current_status_msg = msg
        cursor_char = "█" if self.cursor_state else " "
        self.lbl_progress_status.setText(f"{msg} {cursor_char}")

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        root_layout = QHBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # --------------------------------------------------------------------
        # 1. Left Sidebar Navigation
        # --------------------------------------------------------------------
        sidebar = QFrame()
        sidebar.setObjectName("sidebarFrame")
        sidebar.setFixedWidth(240)
        s_layout = QVBoxLayout(sidebar)
        s_layout.setContentsMargins(16, 20, 16, 20)
        s_layout.setSpacing(12)

        # Branding
        brand_layout = QHBoxLayout()
        logo_icon = QLabel(">_")
        logo_icon.setStyleSheet("font-size: 20px; font-weight: 800; color: #33ff66; padding: 2px 6px; border: 1px solid #2a3a26; background: #070906;")
        brand_layout.addWidget(logo_icon)

        brand_text_layout = QVBoxLayout()
        brand_title = QLabel("ISABELLA.LMS")
        brand_title.setStyleSheet("font-size: 15px; font-weight: 800; color: #33ff66; letter-spacing: 1px;")
        brand_sub = QLabel("// IIITK_TERMINAL")
        brand_sub.setStyleSheet("font-size: 10px; color: #5f7a5c; font-weight: 600; letter-spacing: 0.5px;")
        brand_text_layout.addWidget(brand_title)
        brand_text_layout.addWidget(brand_sub)
        brand_layout.addLayout(brand_text_layout)
        brand_layout.addStretch()
        s_layout.addLayout(brand_layout)

        s_layout.addSpacing(6)

        # User Profile Status Card
        self.profile_card = QFrame()
        self.profile_card.setObjectName("profileCard")
        p_card_layout = QVBoxLayout(self.profile_card)
        p_card_layout.setContentsMargins(12, 10, 12, 10)
        p_card_layout.setSpacing(4)

        self.lbl_user_roll = QLabel("UID: NOT_LOGGED_IN")
        self.lbl_user_roll.setStyleSheet("font-weight: 700; color: #d7ffd9; font-size: 11px;")

        self.lbl_user_status = QLabel("[○ OFFLINE]")
        self.lbl_user_status.setStyleSheet("color: #5f7a5c; font-size: 11px; font-weight: 700;")
        p_card_layout.addWidget(self.lbl_user_roll)
        p_card_layout.addWidget(self.lbl_user_status)

        s_layout.addWidget(self.profile_card)
        s_layout.addSpacing(6)

        # Sidebar Navigation Buttons
        nav_label = QLabel("// NAVIGATION")
        nav_label.setStyleSheet("font-size: 10px; font-weight: 700; color: #5f7a5c; letter-spacing: 1px;")
        s_layout.addWidget(nav_label)

        self.btn_nav_downloader = QPushButton("> 01. COURSE MATERIALS")
        self.btn_nav_downloader.setObjectName("navBtnActive")
        self.btn_nav_downloader.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_nav_downloader.clicked.connect(lambda: self.switch_page(0))
        s_layout.addWidget(self.btn_nav_downloader)

        self.btn_nav_feedback = QPushButton("> 02. FEEDBACK FORMS")
        self.btn_nav_feedback.setObjectName("navBtn")
        self.btn_nav_feedback.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_nav_feedback.clicked.connect(lambda: self.switch_page(1))
        s_layout.addWidget(self.btn_nav_feedback)

        self.btn_nav_settings = QPushButton("> 03. ACCOUNT SETTINGS")
        self.btn_nav_settings.setObjectName("navBtn")
        self.btn_nav_settings.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_nav_settings.clicked.connect(lambda: self.switch_page(2))
        s_layout.addWidget(self.btn_nav_settings)

        # Sync button
        self.btn_side_sync = QPushButton("[ ⟳ SYNC COURSES ]")
        self.btn_side_sync.setObjectName("syncBtn")
        self.btn_side_sync.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_side_sync.clicked.connect(self.fetch_courses)
        s_layout.addWidget(self.btn_side_sync)

        s_layout.addStretch()

        root_layout.addWidget(sidebar)

        # --------------------------------------------------------------------
        # 2. Main Content Area (Stacked Pages)
        # --------------------------------------------------------------------
        self.page_stack = QStackedWidget()

        # Page 0: Course Materials Dashboard
        page_downloader = self.build_downloader_page()
        self.page_stack.addWidget(page_downloader)

        # Page 1: Feedback Forms Directory
        page_feedback = self.build_feedback_page()
        self.page_stack.addWidget(page_feedback)

        # Page 2: Settings & Account
        page_settings = self.build_settings_page()
        self.page_stack.addWidget(page_settings)

        root_layout.addWidget(self.page_stack, 1)

    def switch_page(self, index: int):
        self.page_stack.setCurrentIndex(index)
        nav_buttons = [self.btn_nav_downloader, self.btn_nav_feedback, self.btn_nav_settings]
        for i, btn in enumerate(nav_buttons):
            if i == index:
                btn.setObjectName("navBtnActive")
            else:
                btn.setObjectName("navBtn")
            btn.setStyle(btn.style())

    # ========================================================================
    # Page 0: Downloader Dashboard Builder
    # ========================================================================
    def build_downloader_page(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(14)

        # Top Bar: Page Title + Open Folder Shortcut
        top_bar = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        lbl_head = QLabel("> COURSE MATERIALS HUB")
        lbl_head.setStyleSheet("background: transparent; font-size: 18px; font-weight: 800; color: #d7ffd9; letter-spacing: 1px;")
        lbl_sub = QLabel("// SELECT TARGET MODULES FOR LOCAL ARCHIVAL DOWNLOAD")
        lbl_sub.setStyleSheet("background: transparent; font-size: 11px; color: #5f7a5c; font-weight: 600;")
        title_box.addWidget(lbl_head)
        title_box.addWidget(lbl_sub)
        top_bar.addLayout(title_box)
        top_bar.addStretch()

        btn_header_open = QPushButton("[ 📂 OPEN FOLDER ]")
        btn_header_open.setObjectName("secondaryBtn")
        btn_header_open.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_header_open.clicked.connect(self.open_downloads_dir)
        top_bar.addWidget(btn_header_open)
        layout.addLayout(top_bar)

        # Stat Cards Grid (3 retro metric cards)
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(12)

        self.card_total_courses = self.create_stat_card("> TOTAL ENROLLED", "0", "[COURSES REGISTERED]")
        stats_layout.addWidget(self.card_total_courses)

        self.card_filter_view = self.create_stat_card("> YEAR FILTER", "ALL", "[ALL COURSES VISIBLE]")
        self.card_filter_view.val_label.setStyleSheet("background: transparent; font-size: 20px; font-weight: 800; color: #ffb000;")
        stats_layout.addWidget(self.card_filter_view)

        self.card_selected_count = self.create_stat_card("> TARGETED", "0", "[READY FOR BATCH EXEC]")
        self.card_selected_count.val_label.setStyleSheet("background: transparent; font-size: 20px; font-weight: 800; color: #4dd8e6;")
        stats_layout.addWidget(self.card_selected_count)

        layout.addLayout(stats_layout)

        # Filter & Search Toolbar
        toolbar = QFrame()
        toolbar.setObjectName("toolbarCard")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(12, 8, 12, 8)
        tb_layout.setSpacing(10)

        # Year Pills Bar
        lbl_year_title = QLabel("FILTER:")
        lbl_year_title.setStyleSheet("background: transparent; color: #5f7a5c; font-weight: 700; font-size: 11px;")
        tb_layout.addWidget(lbl_year_title)

        self.year_pills: Dict[str, QPushButton] = {}
        year_options = [
            ("all", "ALL"),
            ("1", "YR 1"),
            ("2", "YR 2"),
            ("3", "YR 3"),
            ("4", "YR 4")
        ]

        for y_key, y_label in year_options:
            is_active = (y_key == "all")
            btn_pill = QPushButton(f"[■ {y_label} ]" if is_active else f"[ {y_label} ]")
            btn_pill.setObjectName("pillBtnActive" if is_active else "pillBtn")
            btn_pill.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn_pill.clicked.connect(lambda checked=False, k=y_key: self.set_year_filter(k))
            self.year_pills[y_key] = btn_pill
            tb_layout.addWidget(btn_pill)

        tb_layout.addSpacing(12)

        # Search Bar
        lbl_search_ico = QLabel("SEARCH:")
        lbl_search_ico.setStyleSheet("background: transparent; color: #5f7a5c; font-weight: 700; font-size: 11px;")
        tb_layout.addWidget(lbl_search_ico)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("searchInput")
        self.search_input.setPlaceholderText("SEARCH_QUERY (e.g. CSS311, Networks)...")
        self.search_input.textChanged.connect(self.on_search_changed)
        tb_layout.addWidget(self.search_input, 1)

        # Clear Search
        btn_clear_search = QPushButton("[✕]")
        btn_clear_search.setObjectName("iconBtn")
        btn_clear_search.setToolTip("Clear search query")
        btn_clear_search.clicked.connect(lambda: self.search_input.clear())
        tb_layout.addWidget(btn_clear_search)

        layout.addWidget(toolbar)

        # Retro Course Table
        self.course_table = QTableWidget()
        self.course_table.setObjectName("modernTable")
        self.course_table.setColumnCount(4)
        self.course_table.setHorizontalHeaderLabels(["[x]", "ID", "YEAR", "COURSE NAME"])
        self.course_table.setShowGrid(True)
        self.course_table.verticalHeader().setVisible(False)
        self.course_table.verticalHeader().setDefaultSectionSize(40)
        self.course_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.course_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.course_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.course_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.course_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.course_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.course_table.cellClicked.connect(self.on_table_cell_clicked)
        layout.addWidget(self.course_table, 1)

        # Bottom Action Dock
        action_dock = QFrame()
        action_dock.setObjectName("actionDock")
        ad_layout = QHBoxLayout(action_dock)
        ad_layout.setContentsMargins(14, 10, 14, 10)
        ad_layout.setSpacing(12)

        btn_select_all = QPushButton("[ SELECT ALL ]")
        btn_select_all.setObjectName("dockSmallBtn")
        btn_select_all.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_select_all.clicked.connect(self.select_all_courses)
        ad_layout.addWidget(btn_select_all)

        btn_deselect_all = QPushButton("[ DESELECT ALL ]")
        btn_deselect_all.setObjectName("dockSmallBtn")
        btn_deselect_all.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_deselect_all.clicked.connect(self.deselect_all_courses)
        ad_layout.addWidget(btn_deselect_all)

        ad_layout.addStretch()

        self.chk_use_zip = QCheckBox("[ZIP] ARCHIVE MODE (FAST DOWNLOAD)")
        self.chk_use_zip.setChecked(False)
        self.chk_use_zip.setToolTip("Pulls folder modules as compressed .zip archives")
        ad_layout.addWidget(self.chk_use_zip)

        self.btn_cancel = QPushButton("[✕ CANCEL]")
        self.btn_cancel.setObjectName("dangerBtn")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_cancel.clicked.connect(self.cancel_download)
        ad_layout.addWidget(self.btn_cancel)

        self.btn_download = QPushButton("> EXECUTE DOWNLOAD (0) <")
        self.btn_download.setObjectName("primaryDownloadBtn")
        self.btn_download.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_download.clicked.connect(self.start_download)
        ad_layout.addWidget(self.btn_download)

        layout.addWidget(action_dock)

        # Live Terminal Activity Console
        activity_box = QFrame()
        activity_box.setObjectName("activityBox")
        act_layout = QVBoxLayout(activity_box)
        act_layout.setContentsMargins(12, 8, 12, 8)
        act_layout.setSpacing(6)

        # Header with status & percentage
        act_head = QHBoxLayout()
        self.lbl_progress_status = QLabel("> SYSTEM READY • SELECT TARGET MODULES █")
        self.lbl_progress_status.setStyleSheet("color: #33ff66; font-size: 11px; font-weight: 700;")
        act_head.addWidget(self.lbl_progress_status)
        act_head.addStretch()

        self.lbl_progress_percent = QLabel("0% [IDLE]")
        self.lbl_progress_percent.setStyleSheet("color: #4dd8e6; font-weight: 700; font-size: 11px;")
        act_head.addWidget(self.lbl_progress_percent)
        act_layout.addLayout(act_head)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        act_layout.addWidget(self.progress_bar)

        # Console
        self.log_console = QTextEdit()
        self.log_console.setObjectName("logConsole")
        self.log_console.setReadOnly(True)
        self.log_console.setFixedHeight(95)
        act_layout.addWidget(self.log_console)

        layout.addWidget(activity_box)
        return container

    def create_stat_card(self, title: str, main_val: str, subtitle: str) -> QFrame:
        card = QFrame()
        card.setObjectName("statCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 10, 14, 10)
        cl.setSpacing(2)

        t_lbl = QLabel(title)
        t_lbl.setStyleSheet("background: transparent; font-size: 10px; font-weight: 700; color: #5f7a5c; letter-spacing: 0.8px;")
        cl.addWidget(t_lbl)

        v_lbl = QLabel(main_val)
        v_lbl.setObjectName("statValue")
        v_lbl.setStyleSheet("background: transparent; font-size: 20px; font-weight: 800; color: #d7ffd9;")
        cl.addWidget(v_lbl)

        s_lbl = QLabel(subtitle)
        s_lbl.setObjectName("statSubtitle")
        s_lbl.setStyleSheet("background: transparent; font-size: 10px; color: #3d5239; font-weight: 600;")
        cl.addWidget(s_lbl)

        card.val_label = v_lbl
        card.sub_label = s_lbl
        return card

    # ========================================================================
    # Page 1: Course Feedback Directory Builder
    # ========================================================================
    def build_feedback_page(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(14)

        # Top Bar: Page Title + Scan & Open Actions
        top_bar = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        lbl_head = QLabel("> COURSE FEEDBACK DIRECTORY")
        lbl_head.setStyleSheet("background: transparent; font-size: 18px; font-weight: 800; color: #d7ffd9; letter-spacing: 1px;")
        lbl_sub = QLabel("// TRACK AND COMPLETE PENDING SUBJECT & FACULTY EVALUATION FORMS")
        lbl_sub.setStyleSheet("background: transparent; font-size: 11px; color: #5f7a5c; font-weight: 600;")
        title_box.addWidget(lbl_head)
        title_box.addWidget(lbl_sub)
        top_bar.addLayout(title_box)
        top_bar.addStretch()

        self.btn_ai_mode = QPushButton("[ ✦ MODE: SMART HEURISTIC (0 RES) ]")
        self.btn_ai_mode.setObjectName("secondaryBtn")
        self.btn_ai_mode.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_ai_mode.setToolTip("Click to configure Gemini API Key or positive generation mode")
        self.btn_ai_mode.clicked.connect(self.show_ai_config_dialog)
        top_bar.addWidget(self.btn_ai_mode)

        self.btn_autofill_all_feedback = QPushButton("[ ⚡ AUTO-FILL ALL ]")
        self.btn_autofill_all_feedback.setObjectName("primaryBtn")
        self.btn_autofill_all_feedback.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_autofill_all_feedback.setToolTip("Auto-fill all visible feedback forms with positive responses")
        self.btn_autofill_all_feedback.clicked.connect(self.auto_fill_all_feedback)
        top_bar.addWidget(self.btn_autofill_all_feedback)

        self.btn_open_all_feedback = QPushButton("[ 🌐 OPEN ALL ]")
        self.btn_open_all_feedback.setObjectName("secondaryBtn")
        self.btn_open_all_feedback.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_open_all_feedback.setToolTip("Open all visible feedback forms in your browser tabs")
        self.btn_open_all_feedback.clicked.connect(self.open_all_feedback_forms)
        top_bar.addWidget(self.btn_open_all_feedback)

        self.btn_scan_feedback = QPushButton("[ ⟳ SCAN FORMS ]")
        self.btn_scan_feedback.setObjectName("secondaryBtn")
        self.btn_scan_feedback.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_scan_feedback.clicked.connect(self.start_feedback_scan)
        top_bar.addWidget(self.btn_scan_feedback)

        layout.addLayout(top_bar)

        # Stat Cards Grid (3 retro metric cards)
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(12)

        self.card_total_feedback = self.create_stat_card("> FORMS DISCOVERED", "0", "[ACTIVE EVALUATION FORMS]")
        stats_layout.addWidget(self.card_total_feedback)

        self.card_feedback_courses = self.create_stat_card("> EVALUATED SUBJECTS", "0", "[SUBJECTS WITH FORMS]")
        self.card_feedback_courses.val_label.setStyleSheet("background: transparent; font-size: 20px; font-weight: 800; color: #4dd8e6;")
        stats_layout.addWidget(self.card_feedback_courses)

        self.card_feedback_status = self.create_stat_card("> SCAN STATUS", "READY", "[LMS HARVESTER IDLE]")
        self.card_feedback_status.val_label.setStyleSheet("background: transparent; font-size: 20px; font-weight: 800; color: #ffb000;")
        stats_layout.addWidget(self.card_feedback_status)

        layout.addLayout(stats_layout)

        # Filter & Search Toolbar
        toolbar = QFrame()
        toolbar.setObjectName("toolbarCard")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(12, 8, 12, 8)
        tb_layout.setSpacing(10)

        # Year Pills Bar
        lbl_year_title = QLabel("FILTER:")
        lbl_year_title.setStyleSheet("background: transparent; color: #5f7a5c; font-weight: 700; font-size: 11px;")
        tb_layout.addWidget(lbl_year_title)

        self.feedback_year_pills: Dict[str, QPushButton] = {}
        year_options = [
            ("all", "ALL"),
            ("1", "YR 1"),
            ("2", "YR 2"),
            ("3", "YR 3"),
            ("4", "YR 4")
        ]

        for y_key, y_label in year_options:
            is_active = (y_key == "all")
            btn_pill = QPushButton(f"[■ {y_label} ]" if is_active else f"[ {y_label} ]")
            btn_pill.setObjectName("pillBtnActive" if is_active else "pillBtn")
            btn_pill.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn_pill.clicked.connect(lambda checked=False, k=y_key: self.set_feedback_year_filter(k))
            self.feedback_year_pills[y_key] = btn_pill
            tb_layout.addWidget(btn_pill)

        tb_layout.addSpacing(12)

        # Search Bar
        lbl_search_ico = QLabel("SEARCH:")
        lbl_search_ico.setStyleSheet("background: transparent; color: #5f7a5c; font-weight: 700; font-size: 11px;")
        tb_layout.addWidget(lbl_search_ico)

        self.feedback_search_input = QLineEdit()
        self.feedback_search_input.setObjectName("searchInput")
        self.feedback_search_input.setPlaceholderText("SEARCH_FEEDBACK (e.g. CSS311, Parallel, Feedback)...")
        self.feedback_search_input.textChanged.connect(self.filter_and_render_feedback)
        tb_layout.addWidget(self.feedback_search_input, 1)

        # Clear Search
        btn_clear_search = QPushButton("[✕]")
        btn_clear_search.setObjectName("iconBtn")
        btn_clear_search.setToolTip("Clear search query")
        btn_clear_search.clicked.connect(lambda: self.feedback_search_input.clear())
        tb_layout.addWidget(btn_clear_search)

        layout.addWidget(toolbar)

        # Retro Feedback Forms Table
        self.feedback_table = QTableWidget()
        self.feedback_table.setObjectName("modernTable")
        self.feedback_table.setColumnCount(5)
        self.feedback_table.setHorizontalHeaderLabels(["ID", "YEAR", "COURSE NAME", "FEEDBACK FORM TITLE", "ACTIONS"])
        self.feedback_table.setShowGrid(True)
        self.feedback_table.verticalHeader().setVisible(False)
        self.feedback_table.verticalHeader().setDefaultSectionSize(44)
        self.feedback_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.feedback_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.feedback_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.feedback_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.feedback_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.feedback_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.feedback_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.feedback_table, 1)

        # Bottom Action / Status Dock
        action_dock = QFrame()
        action_dock.setObjectName("actionDock")
        ad_layout = QVBoxLayout(action_dock)
        ad_layout.setContentsMargins(14, 10, 14, 10)
        ad_layout.setSpacing(8)

        status_row = QHBoxLayout()
        self.lbl_feedback_action_status = QLabel("> READY • CLICK [⟳ SCAN FEEDBACK FORMS] TO HARVEST EVALUATIONS")
        self.lbl_feedback_action_status.setStyleSheet("color: #33ff66; font-size: 11px; font-weight: 700;")
        status_row.addWidget(self.lbl_feedback_action_status)
        status_row.addStretch()

        ad_layout.addLayout(status_row)

        self.feedback_progress_bar = QProgressBar()
        self.feedback_progress_bar.setValue(0)
        self.feedback_progress_bar.setTextVisible(False)
        self.feedback_progress_bar.setFixedHeight(6)
        ad_layout.addWidget(self.feedback_progress_bar)

        layout.addWidget(action_dock)
        return container

    def set_feedback_year_filter(self, year_key: str):
        self.selected_feedback_year_filter = year_key
        year_names = {"all": "ALL", "1": "YR 1", "2": "YR 2", "3": "YR 3", "4": "YR 4"}
        for y, btn in self.feedback_year_pills.items():
            name = year_names.get(y, y.upper())
            if y == year_key:
                btn.setObjectName("pillBtnActive")
                btn.setText(f"[■ {name} ]")
            else:
                btn.setObjectName("pillBtn")
                btn.setText(f"[ {name} ]")
            btn.setStyle(btn.style())
        self.filter_and_render_feedback()

    def filter_and_render_feedback(self):
        query = self.feedback_search_input.text().strip().lower()
        filtered = []

        for fb in self.all_feedback_items:
            year_match = True
            if self.selected_feedback_year_filter != "all":
                year_match = (str(fb.course_year) == self.selected_feedback_year_filter)

            search_match = True
            if query:
                search_match = (
                    query in fb.name.lower() or
                    query in fb.course_name.lower() or
                    query in fb.id.lower() or
                    query in fb.course_id.lower()
                )

            if year_match and search_match:
                filtered.append(fb)

        self.filtered_feedback_items = filtered
        self.render_feedback_table(filtered)

        # Update Stat Cards
        self.card_total_feedback.val_label.setText(str(len(filtered)))
        unique_courses = {fb.course_id for fb in filtered}
        self.card_feedback_courses.val_label.setText(str(len(unique_courses)))
        self.card_total_feedback.sub_label.setText(f"[{len(filtered)} OF {len(self.all_feedback_items)} VISIBLE]")

    def render_feedback_table(self, items: List[FeedbackItem]):
        self.feedback_table.setRowCount(len(items))
        for row, fb in enumerate(items):
            # 0. Module ID (Amber #ffb000)
            id_item = QTableWidgetItem(f"[#{fb.id}]")
            id_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            id_item.setForeground(QColor("#ffb000"))
            id_item.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.feedback_table.setItem(row, 0, id_item)

            # 1. Year (Green #33ff66 or Muted #5f7a5c)
            year_str = f"YR {fb.course_year}" if fb.course_year else "GEN"
            year_item = QTableWidgetItem(year_str)
            year_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            year_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            year_item.setForeground(QColor("#33ff66" if fb.course_year else "#5f7a5c"))
            self.feedback_table.setItem(row, 1, year_item)

            # 2. Course Name (Cyan #4dd8e6)
            cname_item = QTableWidgetItem(fb.course_name)
            cname_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            cname_item.setForeground(QColor("#4dd8e6"))
            self.feedback_table.setItem(row, 2, cname_item)

            # 3. Feedback Form Title (Pale Phosphor #d7ffd9)
            title_item = QTableWidgetItem(fb.name)
            title_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            title_item.setForeground(QColor("#d7ffd9"))
            self.feedback_table.setItem(row, 3, title_item)

            # 4. Actions Cell Widget
            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(6, 4, 6, 4)
            action_layout.setSpacing(6)

            is_completed = (fb.id in self.completed_feedback_ids)
            btn_autofill = QPushButton("[ ✓ COMPLETED ]" if is_completed else "[ ⚡ AUTO-FILL ]")
            btn_autofill.setObjectName("completedBtn" if is_completed else "autoFillBtn")
            btn_autofill.setEnabled(not is_completed)
            btn_autofill.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn_autofill.setToolTip("Already completed" if is_completed else "Auto-fill and submit with positive answers")
            if not is_completed:
                btn_autofill.clicked.connect(lambda checked=False, item=fb, btn=btn_autofill: self.start_autofill_single(item, btn))
            action_layout.addWidget(btn_autofill)
            self.feedback_action_buttons[fb.id] = btn_autofill

            btn_open = QPushButton("[ 🌐 OPEN ]")
            btn_open.setObjectName("actionBtn")
            btn_open.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn_open.setToolTip(f"Open in browser: {fb.url}")
            btn_open.clicked.connect(lambda checked=False, u=fb.url: self.open_feedback_url(u))
            action_layout.addWidget(btn_open)

            btn_copy = QPushButton("[ 📋 COPY ]")
            btn_copy.setObjectName("dockSmallBtn")
            btn_copy.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn_copy.setToolTip("Copy URL to clipboard")
            btn_copy.clicked.connect(lambda checked=False, u=fb.url: self.copy_feedback_url(u))
            action_layout.addWidget(btn_copy)

            self.feedback_table.setCellWidget(row, 4, action_widget)

    def open_feedback_url(self, url: str):
        QDesktopServices.openUrl(QUrl(url))
        self.append_log(f"> [WEB] Launched feedback form in browser: {url}", "#33ff66")
        self.lbl_feedback_action_status.setText(f"> OPENED: {url}")

    def copy_feedback_url(self, url: str):
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(url)
            self.append_log(f"> [CLIPBOARD] URL copied: {url}", "#4dd8e6")
            self.lbl_feedback_action_status.setText(f"> COPIED URL: {url}")

    def open_all_feedback_forms(self):
        if not self.filtered_feedback_items:
            QMessageBox.information(self, "NO_FORMS", "> NO FEEDBACK FORMS AVAILABLE IN CURRENT VIEW.")
            return

        count = len(self.filtered_feedback_items)
        if count > 8:
            reply = QMessageBox.question(
                self, "CONFIRM_BATCH_OPEN",
                f"> YOU ARE ABOUT TO OPEN {count} FEEDBACK FORMS IN YOUR BROWSER. PROCEED?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        for fb in self.filtered_feedback_items:
            QDesktopServices.openUrl(QUrl(fb.url))
        self.append_log(f"> [WEB] Batch opened {count} feedback form(s) in default browser.", "#33ff66")
        self.lbl_feedback_action_status.setText(f"> OPENED {count} FORM(S) IN BROWSER")

    def start_feedback_scan(self):
        if self.feedback_worker and self.feedback_worker.isRunning():
            return

        courses_to_scan = list(self.all_courses)

        # Fallback if no courses in memory: check courses.html or a_course.html
        if not courses_to_scan:
            courses_html_path = os.path.join(os.path.dirname(__file__), "courses.html")
            if os.path.exists(courses_html_path):
                try:
                    with open(courses_html_path, "r", encoding="utf-8") as f:
                        offline_courses = self.client.parse_courses_from_html(f.read())
                    if offline_courses:
                        courses_to_scan = offline_courses
                        self.all_courses = offline_courses
                        self.card_total_courses.val_label.setText(str(len(offline_courses)))
                        self.filter_and_render_courses()
                except Exception:
                    pass

        if not courses_to_scan:
            a_course_path = os.path.join(os.path.dirname(__file__), "a_course.html")
            if os.path.exists(a_course_path):
                c141 = CourseItem(id="141", name="CSS311 Parallel and Distributed Computing", url="https://lmsug24.iiitkottayam.ac.in/course/view.php?id=141", year=3)
                courses_to_scan = [c141]

        self.btn_scan_feedback.setEnabled(False)
        self.card_feedback_status.val_label.setText("SCANNING")
        self.card_feedback_status.val_label.setStyleSheet("background: transparent; font-size: 20px; font-weight: 800; color: #ffb000;")
        self.feedback_progress_bar.setValue(0)
        self.lbl_feedback_action_status.setText(f"> SCANNING {len(courses_to_scan)} COURSES FOR FEEDBACK FORMS...")
        self.append_log(f"> [FEEDBACK] Starting scan across {len(courses_to_scan)} course(s)...", "#4dd8e6")

        self.feedback_worker = ScanFeedbackWorker(self.client, courses_to_scan)
        self.feedback_worker.progress.connect(self.on_feedback_scan_progress)
        self.feedback_worker.found_feedback.connect(self.on_feedback_form_found)
        self.feedback_worker.finished.connect(self.on_feedback_scan_finished)
        self.feedback_worker.start()

    def on_feedback_scan_progress(self, current: int, total: int, status_msg: str):
        pct = int((current / total) * 100) if total > 0 else 0
        self.feedback_progress_bar.setValue(pct)
        self.lbl_feedback_action_status.setText(status_msg)

    def on_feedback_form_found(self, item: FeedbackItem):
        if not any(f.id == item.id for f in self.all_feedback_items):
            self.all_feedback_items.append(item)
            self.filter_and_render_feedback()

    def on_feedback_scan_finished(self, feedback_list: list, err: str):
        self.btn_scan_feedback.setEnabled(True)
        self.feedback_progress_bar.setValue(100)
        self.card_feedback_status.val_label.setText("COMPLETE")
        self.card_feedback_status.val_label.setStyleSheet("background: transparent; font-size: 20px; font-weight: 800; color: #33ff66;")

        existing_ids = {f.id for f in self.all_feedback_items}
        for fb in feedback_list:
            if fb.id not in existing_ids:
                self.all_feedback_items.append(fb)
                existing_ids.add(fb.id)

        # Cache to disk for instant offline access
        save_cached_feedback(self.all_feedback_items)

        self.filter_and_render_feedback()
        count = len(self.all_feedback_items)
        self.lbl_feedback_action_status.setText(f"> SCAN COMPLETE • {count} FEEDBACK FORM(S) DISCOVERED")
        self.append_log(f"> [FEEDBACK] Scan finished: {count} active feedback forms identified [OK]", "#33ff66")

    def load_initial_feedback_data(self):
        # 1. Load from persistent cache
        cached = load_cached_feedback()
        if cached:
            for item in cached:
                if not any(f.id == item.id for f in self.all_feedback_items):
                    self.all_feedback_items.append(item)
            self.filter_and_render_feedback()
            return

        # 2. Fallback to a_course.html sample
        sample_path = os.path.join(os.path.dirname(__file__), "a_course.html")
        if os.path.exists(sample_path):
            try:
                with open(sample_path, "r", encoding="utf-8") as f:
                    html = f.read()
                course_141 = CourseItem(
                    id="141",
                    name="CSS311 Parallel and Distributed Computing",
                    url="https://lmsug24.iiitkottayam.ac.in/course/view.php?id=141",
                    year=3
                )
                items = self.client.extract_feedback_forms_from_html(html, course_141)
                for item in items:
                    if not any(f.id == item.id for f in self.all_feedback_items):
                        self.all_feedback_items.append(item)
                self.filter_and_render_feedback()
            except Exception:
                pass

    def toggle_gemini_key_visibility(self):
        if self.input_gemini_key.echoMode() == QLineEdit.EchoMode.Password:
            self.input_gemini_key.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle_gemini_key.setText("[🙈]")
        else:
            self.input_gemini_key.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle_gemini_key.setText("[👁]")

    def update_ai_mode_badge(self):
        if not hasattr(self, 'btn_ai_mode'):
            return
        key = (self.gemini_api_key or "").strip()
        if key:
            self.btn_ai_mode.setText("[ ✦ AI: GEMINI 3.5 FLASH LITE (ACTIVE) ]")
            self.btn_ai_mode.setStyleSheet("color: #33ff66; border-color: #33ff66; font-weight: 700;")
        else:
            self.btn_ai_mode.setText("[ ✦ MODE: SMART HEURISTIC (0 RES) ]")
            self.btn_ai_mode.setStyleSheet("color: #9ccf9f; border-color: #2a3a26; font-weight: 700;")

    def show_ai_config_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("ISABELLA // AI FEEDBACK CONFIGURATION")
        dlg.setFixedSize(540, 270)
        dlg.setStyleSheet("""
            QDialog {
                background-color: #0a0c09;
                border: 1px solid #33ff66;
            }
            QLabel {
                background: transparent;
                font-family: 'JetBrains Mono', Consolas, monospace;
            }
        """)

        d_layout = QVBoxLayout(dlg)
        d_layout.setContentsMargins(22, 20, 22, 20)
        d_layout.setSpacing(12)

        t_lbl = QLabel("> CONFIGURE POSITIVE FEEDBACK ENGINE")
        t_lbl.setStyleSheet("color: #33ff66; font-size: 14px; font-weight: 800; letter-spacing: 0.5px;")
        d_layout.addWidget(t_lbl)

        cur_mode = "GEMINI 3.5 FLASH LITE (ACTIVE)" if self.gemini_api_key.strip() else "SMART HEURISTIC (ZERO TOKEN / 100% POSITIVE)"
        mode_lbl = QLabel(f"CURRENT ENGINE: {cur_mode}")
        mode_lbl.setStyleSheet("color: #4dd8e6; font-size: 11px; font-weight: 700;")
        d_layout.addWidget(mode_lbl)

        key_lbl = QLabel("GOOGLE GEMINI API KEY:")
        key_lbl.setStyleSheet("color: #d7ffd9; font-size: 11px; font-weight: 700;")
        d_layout.addWidget(key_lbl)

        key_inp = QLineEdit(self.gemini_api_key)
        key_inp.setObjectName("formInput")
        key_inp.setPlaceholderText("Enter Gemini API Key (or leave empty for 0-resource heuristic mode)...")
        d_layout.addWidget(key_inp)

        info_lbl = QLabel("// TIP: Leaving the API key empty uses the built-in smart heuristic (zero API tokens, zero quota usage, selects 5/5 ratings, and fills polite feedback).")
        info_lbl.setWordWrap(True)
        info_lbl.setStyleSheet("color: #5f7a5c; font-size: 10px; font-style: italic;")
        d_layout.addWidget(info_lbl)

        btn_box = QHBoxLayout()
        btn_box.addStretch()

        btn_save = QPushButton("[ SAVE CONFIG ]")
        btn_save.setObjectName("primaryBtn")
        btn_save.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_save.clicked.connect(dlg.accept)
        btn_box.addWidget(btn_save)

        btn_cancel = QPushButton("[ CANCEL ]")
        btn_cancel.setObjectName("secondaryBtn")
        btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_cancel.clicked.connect(dlg.reject)
        btn_box.addWidget(btn_cancel)

        d_layout.addLayout(btn_box)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_key = key_inp.text().strip()
            self.gemini_api_key = new_key
            if hasattr(self, 'input_gemini_key'):
                self.input_gemini_key.setText(new_key)
            cfg = get_saved_config()
            save_config(
                cfg.get("username", ""),
                cfg.get("password", ""),
                cfg.get("default_year", "3"),
                cfg.get("remember", True),
                gemini_api_key=new_key
            )
            self.update_ai_mode_badge()
            self.append_log(f"> [CONFIG] Feedback engine updated: {'Gemini AI' if new_key else 'Smart Heuristic (0 tokens)'}", "#33ff66")

    def start_autofill_single(self, fb: FeedbackItem, btn: QPushButton):
        if self.autofill_worker and self.autofill_worker.isRunning():
            QMessageBox.warning(self, "BUSY", "> AN AUTO-FILL TASK IS ALREADY IN PROGRESS.")
            return

        btn.setText("[ ⏳ FILLING... ]")
        btn.setEnabled(False)
        self.feedback_progress_bar.setValue(0)
        self.lbl_feedback_action_status.setText(f"> AUTO-FILLING #{fb.id}...")

        self.autofill_worker = AutoFillFeedbackWorker(self.client, [fb], api_key=self.gemini_api_key)
        self.autofill_worker.progress.connect(self.on_autofill_progress)
        self.autofill_worker.log_message.connect(self.append_log)
        self.autofill_worker.item_finished.connect(self.on_autofill_item_finished)
        self.autofill_worker.finished.connect(self.on_autofill_all_finished)
        self.autofill_worker.start()

    def auto_fill_all_feedback(self):
        if self.autofill_worker and self.autofill_worker.isRunning():
            QMessageBox.warning(self, "BUSY", "> AN AUTO-FILL TASK IS ALREADY IN PROGRESS.")
            return

        pending = [f for f in self.filtered_feedback_items if f.id not in self.completed_feedback_ids]
        if not pending:
            QMessageBox.information(self, "NO_PENDING", "> ALL VISIBLE FEEDBACK FORMS ARE ALREADY MARKED COMPLETED.")
            return

        engine_name = "Gemini 3.5 Flash Lite AI" if self.gemini_api_key else "Smart Positive Heuristic (0 tokens)"
        reply = QMessageBox.question(
            self, "CONFIRM_AUTOFILL_ALL",
            f"> AUTO-FILL AND SUBMIT {len(pending)} PENDING FEEDBACK FORM(S)?\n\n"
            f"  • Engine: {engine_name}\n"
            f"  • Options: Top positive rating (5/5, Strongly Agree)\n"
            f"  • Comments: Constructive, appreciative feedback\n\nProceed?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for fb in pending:
            btn = self.feedback_action_buttons.get(fb.id)
            if btn:
                btn.setText("[ ⏳ QUEUED ]")
                btn.setEnabled(False)

        self.btn_autofill_all_feedback.setEnabled(False)
        self.feedback_progress_bar.setValue(0)
        self.lbl_feedback_action_status.setText(f"> BATCH AUTO-FILLING {len(pending)} FORMS...")

        self.autofill_worker = AutoFillFeedbackWorker(self.client, pending, api_key=self.gemini_api_key)
        self.autofill_worker.progress.connect(self.on_autofill_progress)
        self.autofill_worker.log_message.connect(self.append_log)
        self.autofill_worker.item_finished.connect(self.on_autofill_item_finished)
        self.autofill_worker.finished.connect(self.on_autofill_all_finished)
        self.autofill_worker.start()

    def on_autofill_progress(self, current: int, total: int, status_msg: str):
        pct = int((current / total) * 100) if total > 0 else 0
        self.feedback_progress_bar.setValue(pct)
        self.lbl_feedback_action_status.setText(status_msg)

    def on_autofill_item_finished(self, fb_id: str, success: bool, msg: str, method: str, answers: dict):
        btn = self.feedback_action_buttons.get(fb_id)
        if success:
            self.completed_feedback_ids.add(fb_id)
            save_completed_feedback_ids(self.completed_feedback_ids)
            if btn:
                btn.setText("[ ✓ COMPLETED ]")
                btn.setObjectName("completedBtn")
                btn.setEnabled(False)
                btn.setStyle(btn.style())
        else:
            if btn:
                btn.setText("[ ⚠ RETRY ]")
                btn.setEnabled(True)

    def on_autofill_all_finished(self, success_cnt: int, fail_cnt: int):
        self.btn_autofill_all_feedback.setEnabled(True)
        self.feedback_progress_bar.setValue(100)
        self.lbl_feedback_action_status.setText(
            f"> AUTO-FILL FINISHED • {success_cnt} COMPLETED • {fail_cnt} FAILED"
        )
        self.append_log(
            f"> [AUTO-FILL] Finished batch operations: {success_cnt} succeeded, {fail_cnt} failed.",
            "#33ff66" if fail_cnt == 0 else "#ffb000"
        )

    # ========================================================================
    # Page 2: Settings & Account Builder
    # ========================================================================
    def build_settings_page(self) -> QWidget:
        container = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(18)

        # Header
        head_box = QVBoxLayout()
        head_box.setSpacing(2)
        s_title = QLabel("> ACCOUNT & SYSTEM SETTINGS")
        s_title.setStyleSheet("background: transparent; font-size: 18px; font-weight: 800; color: #d7ffd9; letter-spacing: 1px;")
        s_sub = QLabel("// CONFIGURE LMS CREDENTIALS AND LOCAL DISK REPOSITORY")
        s_sub.setStyleSheet("background: transparent; font-size: 11px; color: #5f7a5c; font-weight: 600;")
        head_box.addWidget(s_title)
        head_box.addWidget(s_sub)
        layout.addLayout(head_box)

        # Card 1: Credentials
        cred_card = QFrame()
        cred_card.setObjectName("settingsCard")
        cc_layout = QVBoxLayout(cred_card)
        cc_layout.setContentsMargins(18, 16, 18, 16)
        cc_layout.setSpacing(12)

        cc_title = QLabel("> 01. LMS AUTHENTICATION")
        cc_title.setStyleSheet("font-size: 13px; font-weight: 800; color: #33ff66; letter-spacing: 0.5px;")
        cc_layout.addWidget(cc_title)

        # Username
        cc_layout.addWidget(QLabel("ROLL_NUMBER / USER_ID:"))
        self.input_username = QLineEdit()
        self.input_username.setObjectName("formInput")
        self.input_username.setPlaceholderText("e.g. 2024BCS0256")
        cc_layout.addWidget(self.input_username)

        # Password
        cc_layout.addWidget(QLabel("LMS_PASSWORD:"))
        pass_row = QHBoxLayout()
        self.input_password = QLineEdit()
        self.input_password.setObjectName("formInput")
        self.input_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_password.setPlaceholderText("ENTER LMS PASSWORD")
        pass_row.addWidget(self.input_password, 1)

        self.btn_toggle_pw = QPushButton("[👁]")
        self.btn_toggle_pw.setObjectName("iconBtn")
        self.btn_toggle_pw.setToolTip("Toggle password visibility")
        self.btn_toggle_pw.clicked.connect(self.toggle_password_visibility)
        pass_row.addWidget(self.btn_toggle_pw)
        cc_layout.addLayout(pass_row)

        # Default Year
        cc_layout.addWidget(QLabel("DEFAULT_ACADEMIC_YEAR:"))
        self.combo_default_year = QComboBox()
        self.combo_default_year.setObjectName("formCombo")
        self.combo_default_year.addItems(["YEAR 1 (FRESHMEN)", "YEAR 2 (SOPHOMORES)", "YEAR 3 (JUNIORS)", "YEAR 4 (SENIORS)"])
        cc_layout.addWidget(self.combo_default_year)

        # Remember
        self.chk_remember = QCheckBox("[REMEMBER] PERSIST CREDENTIALS LOCALLY IN CONFIG.JSON")
        self.chk_remember.setChecked(True)
        cc_layout.addWidget(self.chk_remember)

        # Actions
        btn_row = QHBoxLayout()
        self.btn_save_settings = QPushButton("[ SAVE CONFIG ]")
        self.btn_save_settings.setObjectName("primaryBtn")
        self.btn_save_settings.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_save_settings.clicked.connect(self.save_settings)
        btn_row.addWidget(self.btn_save_settings)

        self.btn_login_now = QPushButton("[ AUTHENTICATE NOW ]")
        self.btn_login_now.setObjectName("secondaryBtn")
        self.btn_login_now.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_login_now.clicked.connect(self.perform_login)
        btn_row.addWidget(self.btn_login_now)

        btn_row.addStretch()
        cc_layout.addLayout(btn_row)

        layout.addWidget(cred_card)

        # Card 2: AI-Powered Feedback Assistant
        ai_card = QFrame()
        ai_card.setObjectName("settingsCard")
        aic_layout = QVBoxLayout(ai_card)
        aic_layout.setContentsMargins(18, 16, 18, 16)
        aic_layout.setSpacing(10)

        aic_title = QLabel("> 02. AI-POWERED FEEDBACK ASSISTANT")
        aic_title.setStyleSheet("font-size: 13px; font-weight: 800; color: #33ff66; letter-spacing: 0.5px;")
        aic_layout.addWidget(aic_title)

        aic_desc = QLabel("// CONFIGURE GOOGLE GEMINI FOR CUSTOM POSITIVE RESPONSES OR USE ZERO-TOKEN HEURISTIC:")
        aic_desc.setStyleSheet("color: #5f7a5c; font-size: 11px;")
        aic_layout.addWidget(aic_desc)

        ai_input_row = QHBoxLayout()
        lbl_ai_key = QLabel("GEMINI_API_KEY:")
        lbl_ai_key.setStyleSheet("color: #d7ffd9; font-size: 11px; font-weight: 700;")
        ai_input_row.addWidget(lbl_ai_key)

        self.input_gemini_key = QLineEdit()
        self.input_gemini_key.setObjectName("formInput")
        self.input_gemini_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_gemini_key.setPlaceholderText("Paste Gemini API Key (or leave empty for instant 0-resource heuristic)")
        ai_input_row.addWidget(self.input_gemini_key, 1)

        self.btn_toggle_gemini_key = QPushButton("[👁]")
        self.btn_toggle_gemini_key.setObjectName("dockSmallBtn")
        self.btn_toggle_gemini_key.clicked.connect(self.toggle_gemini_key_visibility)
        ai_input_row.addWidget(self.btn_toggle_gemini_key)

        aic_layout.addLayout(ai_input_row)

        ai_hint = QLabel("// NOTE: When Gemini API Key is empty, Isabella uses built-in smart heuristics (instant, 0 tokens, selects 5/5 & writes polite feedback).")
        ai_hint.setStyleSheet("color: #5f7a5c; font-size: 10px; font-style: italic;")
        aic_layout.addWidget(ai_hint)

        layout.addWidget(ai_card)

        # Card 3: Downloads Directory
        dir_card = QFrame()
        dir_card.setObjectName("settingsCard")
        dc_layout = QVBoxLayout(dir_card)
        dc_layout.setContentsMargins(18, 16, 18, 16)
        dc_layout.setSpacing(10)

        dc_title = QLabel("> 03. LOCAL STORAGE REPOSITORY")
        dc_title.setStyleSheet("font-size: 13px; font-weight: 800; color: #33ff66; letter-spacing: 0.5px;")
        dc_layout.addWidget(dc_title)

        dc_desc = QLabel("// TARGET DIRECTORY FOR DOWNLOADED LECTURE MATERIALS:")
        dc_desc.setStyleSheet("color: #5f7a5c; font-size: 11px;")
        dc_layout.addWidget(dc_desc)

        path_box = QHBoxLayout()
        self.lbl_download_path = QLabel(os.path.abspath(DOWNLOAD_DIR))
        self.lbl_download_path.setStyleSheet(
            "background: #070906; padding: 8px 12px; border-radius: 2px; color: #ffb000; font-family: 'JetBrains Mono', Consolas, monospace; border: 1px solid #2a3a26;"
        )
        path_box.addWidget(self.lbl_download_path, 1)

        btn_open_folder_settings = QPushButton("[ 📂 OPEN IN EXPLORER ]")
        btn_open_folder_settings.setObjectName("secondaryBtn")
        btn_open_folder_settings.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_open_folder_settings.clicked.connect(self.open_downloads_dir)
        path_box.addWidget(btn_open_folder_settings)

        dc_layout.addLayout(path_box)
        layout.addWidget(dir_card)

        # Card 4: Danger Zone
        danger_card = QFrame()
        danger_card.setObjectName("dangerCard")
        dgc_layout = QHBoxLayout(danger_card)
        dgc_layout.setContentsMargins(18, 14, 18, 14)

        danger_text = QVBoxLayout()
        d_title = QLabel("> 04. PURGE LOCAL CREDENTIALS")
        d_title.setStyleSheet("font-weight: 800; color: #ff5f56; font-size: 12px; letter-spacing: 0.5px;")
        d_desc = QLabel("// CLEARS SAVED ROLL NUMBER AND PASSWORD FROM THIS MACHINE.")
        d_desc.setStyleSheet("color: #5f7a5c; font-size: 11px;")
        danger_text.addWidget(d_title)
        danger_text.addWidget(d_desc)
        dgc_layout.addLayout(danger_text)
        dgc_layout.addStretch()

        self.btn_forget = QPushButton("[ PURGE CREDENTIALS ]")
        self.btn_forget.setObjectName("dangerBtn")
        self.btn_forget.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_forget.clicked.connect(self.forget_credentials)
        dgc_layout.addWidget(self.btn_forget)

        layout.addWidget(danger_card)
        layout.addStretch()

        scroll.setWidget(inner)
        main_box = QVBoxLayout(container)
        main_box.setContentsMargins(0, 0, 0, 0)
        main_box.addWidget(scroll)
        return container

    # ========================================================================
    # Event Handlers & Core Features
    # ========================================================================
    def open_downloads_dir(self):
        target = os.path.abspath(DOWNLOAD_DIR)
        os.makedirs(target, exist_ok=True)
        self.append_log(f"> [FS] Opening repository: {target}", "#4dd8e6")

        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer.exe", os.path.normpath(target)])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(target))
        except Exception as e:
            try:
                os.startfile(target)
            except Exception:
                QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def toggle_password_visibility(self):
        if self.input_password.echoMode() == QLineEdit.EchoMode.Password:
            self.input_password.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle_pw.setText("[🙈]")
        else:
            self.input_password.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle_pw.setText("[👁]")

    def load_saved_credentials(self):
        cfg = get_saved_config()
        username = cfg.get("username", "")
        password = cfg.get("password", "")
        default_year = cfg.get("default_year", "3")
        remember = cfg.get("remember", True)
        gemini_key = cfg.get("gemini_api_key", "")

        self.input_username.setText(username)
        self.input_password.setText(password)
        self.chk_remember.setChecked(remember)
        self.gemini_api_key = gemini_key
        if hasattr(self, 'input_gemini_key'):
            self.input_gemini_key.setText(gemini_key)

        idx = ["1", "2", "3", "4"].index(default_year) if default_year in ["1", "2", "3", "4"] else 2
        self.combo_default_year.setCurrentIndex(idx)

        # Set default active year pill
        self.set_year_filter(default_year)
        self.set_feedback_year_filter("all")

        # Preload offline/cached reference feedback forms
        self.load_initial_feedback_data()

        # If offline/unauthenticated and courses.html exists, seed courses catalog
        if not self.all_courses:
            courses_html = os.path.join(os.path.dirname(__file__), "courses.html")
            if os.path.exists(courses_html):
                try:
                    with open(courses_html, "r", encoding="utf-8") as f:
                        offline_courses = self.client.parse_courses_from_html(f.read())
                    if offline_courses:
                        self.all_courses = offline_courses
                        self.card_total_courses.val_label.setText(str(len(offline_courses)))
                        self.filter_and_render_courses()
                except Exception:
                    pass

        if username and password:
            self.lbl_user_roll.setText(f"UID: {username}")
            self.lbl_user_status.setText("[◌ AUTHENTICATING]")
            self.lbl_user_status.setStyleSheet("color: #4dd8e6; font-size: 11px; font-weight: 700;")
            self.perform_login(auto_fetch=True)
        else:
            self.switch_page(2)  # Focus settings page for login

    def save_settings(self):
        username = self.input_username.text().strip()
        password = self.input_password.text().strip()
        default_year = str(self.combo_default_year.currentIndex() + 1)
        remember = self.chk_remember.isChecked()
        gemini_key = self.input_gemini_key.text().strip() if hasattr(self, 'input_gemini_key') else self.gemini_api_key
        self.gemini_api_key = gemini_key

        save_config(username, password, default_year, remember, gemini_api_key=gemini_key)
        self.update_ai_mode_badge()
        QMessageBox.information(self, "SYSTEM_UPDATE", "> LMS CREDENTIALS, DEFAULT YEAR, AND AI CONFIG SAVED.")

    def forget_credentials(self):
        reply = QMessageBox.question(
            self, "PURGE_CONFIRMATION",
            "> ARE YOU SURE YOU WANT TO PURGE STORED CREDENTIALS?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            save_config("", "", "3", remember=False, gemini_api_key="")
            self.input_username.clear()
            self.input_password.clear()
            self.gemini_api_key = ""
            if hasattr(self, 'input_gemini_key'):
                self.input_gemini_key.clear()
            self.update_ai_mode_badge()
            self.lbl_user_roll.setText("UID: NOT_LOGGED_IN")
            self.lbl_user_status.setText("[○ OFFLINE]")
            self.lbl_user_status.setStyleSheet("color: #5f7a5c; font-size: 11px; font-weight: 700;")
            self.switch_page(2)
            QMessageBox.information(self, "PURGED", "> STORED CREDENTIALS REMOVED FROM DISK.")

    def perform_login(self, auto_fetch=False):
        username = self.input_username.text().strip()
        password = self.input_password.text().strip()

        if not username or not password:
            QMessageBox.warning(self, "AUTH_REQUIRED", "> PLEASE PROVIDE LMS ROLL NUMBER AND PASSWORD.")
            self.switch_page(2)
            return

        self.btn_login_now.setEnabled(False)
        self.update_status("> AUTHENTICATING WITH IIITK LMS...")
        self.append_log(f"> [AUTH] Attempting login for UID: {username}...", "#4dd8e6")

        self.login_worker = LoginWorker(self.client, username, password)
        self.login_worker.finished.connect(lambda ok, msg: self.on_login_finished(ok, msg, username, password, auto_fetch))
        self.login_worker.start()

    def on_login_finished(self, ok: bool, msg: str, username: str, password: str, auto_fetch: bool):
        self.btn_login_now.setEnabled(True)

        if ok:
            self.lbl_user_roll.setText(f"UID: {username}")
            self.lbl_user_status.setText("[● ONLINE]")
            self.lbl_user_status.setStyleSheet("color: #33ff66; font-size: 11px; font-weight: 700;")
            self.append_log(f"> [AUTH] Authenticated successfully as {username} [OK]", "#33ff66")

            if self.chk_remember.isChecked():
                default_year = str(self.combo_default_year.currentIndex() + 1)
                save_config(username, password, default_year, remember=True)

            self.switch_page(0)
            self.fetch_courses()
        else:
            self.lbl_user_status.setText("[✕ AUTH_FAILED]")
            self.lbl_user_status.setStyleSheet("color: #ff5f56; font-size: 11px; font-weight: 700;")
            self.append_log(f"> [ERR] Login rejected: {msg}", "#ff5f56")
            if not auto_fetch:
                QMessageBox.critical(self, "AUTH_FAILED", f"> COULD NOT AUTHENTICATE WITH LMS:\n{msg}")

    def fetch_courses(self):
        self.btn_side_sync.setEnabled(False)
        self.update_status("> QUERYING LMS API FOR ENROLLED COURSES...")
        self.append_log("> [SYNC] Fetching enrolled course catalog...", "#4dd8e6")

        self.courses_worker = FetchCoursesWorker(self.client)
        self.courses_worker.finished.connect(self.on_courses_fetched)
        self.courses_worker.start()

    def on_courses_fetched(self, courses: list, err: str):
        self.btn_side_sync.setEnabled(True)
        if err:
            self.append_log(f"> [ERR] Course discovery error: {err}", "#ff5f56")
            return

        self.all_courses = courses
        self.card_total_courses.val_label.setText(str(len(courses)))
        self.append_log(f"> [SYNC] Catalog loaded: {len(courses)} courses discovered [OK]", "#33ff66")
        self.filter_and_render_courses()

        # Automatically discover and update feedback forms for all courses in background
        self.start_feedback_scan()

    def set_year_filter(self, year_key: str):
        self.selected_year_filter = year_key
        year_names = {"all": "ALL", "1": "YR 1", "2": "YR 2", "3": "YR 3", "4": "YR 4"}
        for y, btn in self.year_pills.items():
            name = year_names.get(y, y.upper())
            if y == year_key:
                btn.setObjectName("pillBtnActive")
                btn.setText(f"[■ {name} ]")
            else:
                btn.setObjectName("pillBtn")
                btn.setText(f"[ {name} ]")
            btn.setStyle(btn.style())

        self.filter_and_render_courses()

    def on_search_changed(self):
        self.filter_and_render_courses()

    def filter_and_render_courses(self):
        query = self.search_input.text().strip().lower()
        filtered = []

        for c in self.all_courses:
            year_match = True
            if self.selected_year_filter != "all":
                year_match = (str(c.year) == self.selected_year_filter)

            search_match = True
            if query:
                search_match = query in c.name.lower() or query in c.id.lower()

            if year_match and search_match:
                filtered.append(c)

        self.current_filtered_courses = filtered

        # Update Filter Stat Card
        filter_text = "ALL" if self.selected_year_filter == "all" else f"YEAR {self.selected_year_filter}"
        self.card_filter_view.val_label.setText(filter_text)
        self.card_filter_view.sub_label.setText(f"[{len(filtered)} COURSES MATCHING]")

        self.render_course_table(filtered)

    def render_course_table(self, courses: List[CourseItem]):
        self.course_table.setRowCount(len(courses))
        for row, c in enumerate(courses):
            # 0. Checkbox
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            chk.setCheckState(Qt.CheckState.Checked)
            chk.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.course_table.setItem(row, 0, chk)

            # 1. Course Code (Amber #ffb000)
            id_item = QTableWidgetItem(f"[#{c.id}]")
            id_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            id_item.setForeground(QColor("#ffb000"))
            id_item.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
            self.course_table.setItem(row, 1, id_item)

            # 2. Year (Green #33ff66 or Muted #5f7a5c)
            year_str = f"YR {c.year}" if c.year else "GEN"
            year_item = QTableWidgetItem(year_str)
            year_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            year_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            year_item.setForeground(QColor("#33ff66" if c.year else "#5f7a5c"))
            self.course_table.setItem(row, 2, year_item)

            # 3. Course Title (Phosphor Pale Green #d7ffd9)
            name_item = QTableWidgetItem(c.name)
            name_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            name_item.setForeground(QColor("#d7ffd9"))
            self.course_table.setItem(row, 3, name_item)

        self.update_selection_metrics()

    def on_table_cell_clicked(self, row: int, col: int):
        # Clicking anywhere on the row toggles the checkbox
        item = self.course_table.item(row, 0)
        if item:
            new_state = Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked
            item.setCheckState(new_state)
            self.update_selection_metrics()

    def select_all_courses(self):
        for row in range(self.course_table.rowCount()):
            item = self.course_table.item(row, 0)
            if item:
                item.setCheckState(Qt.CheckState.Checked)
        self.update_selection_metrics()

    def deselect_all_courses(self):
        for row in range(self.course_table.rowCount()):
            item = self.course_table.item(row, 0)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked)
        self.update_selection_metrics()

    def update_selection_metrics(self):
        selected_courses = self.get_selected_courses()
        count = len(selected_courses)
        self.card_selected_count.val_label.setText(str(count))
        self.card_selected_count.sub_label.setText(f"[{count} OF {len(self.current_filtered_courses)} TARGETED]")
        self.btn_download.setText(f"> EXECUTE DOWNLOAD ({count}) <")

    def get_selected_courses(self) -> List[CourseItem]:
        selected = []
        for row in range(self.course_table.rowCount()):
            chk = self.course_table.item(row, 0)
            if chk and chk.checkState() == Qt.CheckState.Checked:
                if row < len(self.current_filtered_courses):
                    selected.append(self.current_filtered_courses[row])
        return selected

    def start_download(self):
        selected_courses = self.get_selected_courses()
        if not selected_courses:
            QMessageBox.warning(self, "NO_TARGETS", "> SELECT AT LEAST ONE COURSE MODULE.")
            return

        self.btn_download.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setValue(0)
        self.lbl_progress_percent.setText("0% [RUNNING]")
        self.update_status(f"> EXECUTING PIPELINE: {len(selected_courses)} MODULE(S)")

        use_zip = self.chk_use_zip.isChecked()
        self.download_worker = DownloadWorker(self.client, selected_courses, use_zip)
        self.download_worker.progress.connect(self.on_download_progress)
        self.download_worker.log.connect(self.on_download_log)
        self.download_worker.finished.connect(self.on_download_finished)
        self.download_worker.start()

    def cancel_download(self):
        if self.download_worker and self.download_worker.isRunning():
            self.download_worker.cancel()
            self.btn_cancel.setEnabled(False)
            self.update_status("> ABORTING DOWNLOAD PIPELINE...")

    def on_download_progress(self, current: int, total: int, status_text: str):
        percent = int((current / total) * 100) if total > 0 else 0
        self.progress_bar.setValue(percent)
        self.lbl_progress_percent.setText(f"{percent}% [EXEC]")
        self.update_status(status_text)

    def on_download_log(self, text: str, level: str):
        color_map = {
            "info": "#4dd8e6",      # Cyan
            "success": "#33ff66",   # Green
            "skipped": "#ffb000",   # Amber
            "error": "#ff5f56"      # Red
        }
        color = color_map.get(level, "#d7ffd9")
        self.append_log(text, color)

    def on_download_finished(self, stats: dict):
        self.progress_bar.setValue(100)
        self.lbl_progress_percent.setText("100% [DONE]")
        self.btn_download.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.update_status("> PIPELINE COMPLETED [ALL MODULES PROCESSED]")

        summary = (
            f"> BATCH DOWNLOAD COMPLETED\n\n"
            f"  • DOWNLOADED: {stats.get('downloaded', 0)}\n"
            f"  • PREVIOUSLY SAVED: {stats.get('skipped', 0)}\n"
            f"  • FAILED: {stats.get('failed', 0)}\n\n"
            f"  • DESTINATION: {DOWNLOAD_DIR}"
        )
        self.append_log(f"\n{summary}", "#33ff66")

        reply = QMessageBox.information(
            self, "DOWNLOAD_COMPLETE",
            f"{summary}\n\n> OPEN LOCAL REPOSITORY IN EXPLORER?",
            QMessageBox.StandardButton.Open | QMessageBox.StandardButton.Ok
        )
        if reply == QMessageBox.StandardButton.Open:
            self.open_downloads_dir()

    def append_log(self, text: str, color_hex: str = "#d7ffd9"):
        html_line = f"<span style='color: {color_hex};'>{text}</span>"
        self.log_console.append(html_line)
        sb = self.log_console.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ========================================================================
    # Retro Terminal Green Phosphor Design System (QSS)
    # ========================================================================
    def apply_retro_terminal_theme(self):
        theme_qss = """
        /* Global Canvas & Font */
        QMainWindow {
            background-color: #0a0c09;
        }
        QWidget {
            color: #d7ffd9;
            font-family: 'JetBrains Mono', 'IBM Plex Mono', 'Consolas', 'Courier New', monospace;
            font-size: 13px;
        }
        QStackedWidget, QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {
            background: transparent;
            background-color: transparent;
        }
        QLabel {
            background: transparent;
            background-color: transparent;
            color: #d7ffd9;
        }
        QFrame {
            background: transparent;
            background-color: transparent;
        }

        /* Sidebar */
        #sidebarFrame {
            background-color: #070906;
            border-right: 1px solid #1f2a1c;
        }
        #profileCard {
            background-color: #0f130e;
            border: 1px solid #2a3a26;
            border-radius: 2px;
        }

        /* Navigation Buttons */
        #navBtn, #navBtnActive {
            text-align: left;
            padding: 10px 14px;
            border-radius: 2px;
            font-size: 12px;
            font-weight: 700;
            font-family: 'JetBrains Mono', 'IBM Plex Mono', 'Consolas', monospace;
            letter-spacing: 0.5px;
        }
        #navBtn {
            background-color: transparent;
            color: #5f7a5c;
            border: 1px solid transparent;
        }
        #navBtn:hover {
            background-color: #0f130e;
            border: 1px solid #2a3a26;
            color: #9ccf9f;
        }
        #navBtnActive {
            background-color: #33ff66;
            color: #0a0c09;
            border: 1px solid #33ff66;
        }

        /* Action & Sync Buttons */
        #actionBtn, #syncBtn, #dockSmallBtn {
            background-color: transparent;
            border: 1px solid #2a3a26;
            border-radius: 2px;
            padding: 8px 12px;
            color: #9ccf9f;
            font-weight: 700;
            font-size: 11px;
            letter-spacing: 0.5px;
        }
        #actionBtn:hover, #syncBtn:hover, #dockSmallBtn:hover {
            background-color: rgba(51, 255, 102, 0.08);
            border-color: #33ff66;
            color: #33ff66;
        }

        #autoFillBtn {
            background-color: rgba(51, 255, 102, 0.12);
            border: 1px solid #33ff66;
            border-radius: 2px;
            padding: 6px 12px;
            color: #33ff66;
            font-weight: 700;
            font-size: 11px;
            letter-spacing: 0.5px;
        }
        #autoFillBtn:hover {
            background-color: #33ff66;
            color: #0a0c09;
        }
        #autoFillBtn:disabled {
            background-color: transparent;
            border-color: #1f2a1c;
            color: #3d5239;
        }

        #completedBtn {
            background-color: rgba(51, 255, 102, 0.05);
            border: 1px solid #1f2a1c;
            border-radius: 2px;
            padding: 6px 12px;
            color: #5f7a5c;
            font-weight: 700;
            font-size: 11px;
        }

        /* Metric Stat Cards */
        #statCard {
            background-color: #0f130e;
            border: 1px solid #2a3a26;
            border-radius: 2px;
        }
        #statCard QLabel {
            background: transparent;
            background-color: transparent;
        }

        /* Toolbar & Panels */
        #toolbarCard, #actionDock, #activityBox, #settingsCard {
            background-color: #0f130e;
            border: 1px solid #2a3a26;
            border-radius: 2px;
        }
        #toolbarCard QLabel, #actionDock QLabel, #activityBox QLabel, #settingsCard QLabel {
            background: transparent;
            background-color: transparent;
        }
        #dangerCard {
            background-color: rgba(255, 95, 86, 0.06);
            border: 1px solid #ff5f56;
            border-radius: 2px;
        }
        #dangerCard QLabel {
            background: transparent;
            background-color: transparent;
        }

        /* Pill Buttons */
        #pillBtn, #pillBtnActive {
            padding: 6px 14px;
            border-radius: 2px;
            font-size: 11px;
            font-weight: 700;
            font-family: 'JetBrains Mono', 'IBM Plex Mono', 'Consolas', monospace;
            letter-spacing: 0.5px;
        }
        #pillBtn {
            background-color: transparent;
            color: #5f7a5c;
            border: 1px solid #2a3a26;
        }
        #pillBtn:hover {
            border-color: #33ff66;
            color: #9ccf9f;
            background-color: rgba(51, 255, 102, 0.04);
        }
        #pillBtnActive {
            background-color: rgba(51, 255, 102, 0.12);
            color: #33ff66;
            border: 1px solid #33ff66;
        }

        /* Inputs & ComboBox */
        #searchInput, #formInput, #formCombo {
            background-color: #070906;
            border: 1px solid #2a3a26;
            border-radius: 2px;
            padding: 8px 12px;
            color: #d7ffd9;
            font-family: 'JetBrains Mono', 'IBM Plex Mono', 'Consolas', monospace;
            font-size: 12px;
        }
        #searchInput:focus, #formInput:focus, #formCombo:focus {
            border: 1px solid #33ff66;
            background-color: #0a0e08;
            color: #33ff66;
        }

        /* Primary Action Buttons */
        #primaryDownloadBtn, #primaryBtn {
            background-color: #33ff66;
            color: #0a0c09;
            font-weight: 800;
            font-size: 12px;
            padding: 10px 20px;
            border-radius: 2px;
            border: 1px solid #33ff66;
            letter-spacing: 0.8px;
            text-transform: uppercase;
        }
        #primaryDownloadBtn:hover, #primaryBtn:hover {
            background-color: transparent;
            border: 1px solid #33ff66;
            color: #33ff66;
        }
        #primaryDownloadBtn:disabled, #primaryBtn:disabled {
            background-color: #141a12;
            color: #3d5239;
            border-color: #2a3a26;
        }

        /* Secondary Buttons */
        #secondaryBtn {
            background-color: transparent;
            border: 1px solid #2a3a26;
            color: #9ccf9f;
            font-weight: 700;
            padding: 8px 16px;
            border-radius: 2px;
            letter-spacing: 0.5px;
        }
        #secondaryBtn:hover {
            background-color: rgba(51, 255, 102, 0.08);
            border-color: #33ff66;
            color: #33ff66;
        }

        /* Danger Buttons */
        #dangerBtn {
            background-color: transparent;
            border: 1px solid #ff5f56;
            border-radius: 2px;
            color: #ff5f56;
            font-weight: 700;
            padding: 8px 16px;
            letter-spacing: 0.5px;
        }
        #dangerBtn:hover {
            background-color: #ff5f56;
            color: #0a0c09;
        }

        #iconBtn {
            background: transparent;
            border: 1px solid transparent;
            color: #5f7a5c;
            font-size: 13px;
            padding: 4px;
        }
        #iconBtn:hover {
            color: #33ff66;
            border: 1px solid #2a3a26;
        }

        /* Retro Terminal Table */
        #modernTable {
            background-color: #070906;
            alternate-background-color: #0b0e0a;
            border: 1px solid #2a3a26;
            border-radius: 2px;
            color: #d7ffd9;
            selection-background-color: #142013;
            selection-color: #33ff66;
            outline: none;
            gridline-color: #1b2618;
        }
        #modernTable::item {
            padding: 8px 12px;
            border-bottom: 1px solid #162013;
        }
        #modernTable::item:hover {
            background-color: #121c11;
        }
        QHeaderView::section {
            background-color: #050604;
            color: #5f7a5c;
            padding: 9px 12px;
            border: none;
            border-bottom: 1px solid #2a3a26;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.8px;
            font-family: 'JetBrains Mono', 'IBM Plex Mono', 'Consolas', monospace;
        }

        /* CheckBox Indicator */
        QCheckBox {
            color: #9ccf9f;
            font-size: 12px;
            background: transparent;
        }
        QCheckBox::indicator {
            width: 15px;
            height: 15px;
            border-radius: 2px;
            border: 1px solid #2a3a26;
            background-color: #070906;
        }
        QCheckBox::indicator:hover {
            border-color: #33ff66;
        }
        QCheckBox::indicator:checked {
            background-color: #33ff66;
            border-color: #33ff66;
        }

        /* Progress Bar */
        QProgressBar {
            background-color: #070906;
            border: 1px solid #2a3a26;
            border-radius: 2px;
            height: 6px;
        }
        QProgressBar::chunk {
            background-color: #33ff66;
            border-radius: 0px;
        }

        /* Console */
        #logConsole {
            background-color: #050604;
            border: 1px solid #2a3a26;
            border-radius: 2px;
            font-family: 'JetBrains Mono', 'IBM Plex Mono', 'Consolas', monospace;
            font-size: 12px;
            color: #33ff66;
            line-height: 1.4;
        }

        /* Custom Scrollbar */
        QScrollBar:vertical {
            background: #070906;
            width: 8px;
            border-radius: 2px;
            border-left: 1px solid #1f2a1c;
        }
        QScrollBar::handle:vertical {
            background: #2a3a26;
            border-radius: 2px;
            min-height: 20px;
        }
        QScrollBar::handle:vertical:hover {
            background: #33ff66;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }

        /* Modal Dialogs & Message Boxes (Retro Terminal Dark) */
        QDialog, QMessageBox {
            background-color: #0a0c09;
            border: 1px solid #2a3a26;
            color: #d7ffd9;
        }
        QMessageBox QLabel {
            color: #d7ffd9;
            background: transparent;
            font-size: 12px;
            font-family: 'JetBrains Mono', 'IBM Plex Mono', 'Consolas', monospace;
            padding: 6px;
        }
        QDialog QPushButton, QMessageBox QPushButton {
            background-color: transparent;
            border: 1px solid #2a3a26;
            border-radius: 2px;
            color: #9ccf9f;
            font-weight: 700;
            font-size: 11px;
            padding: 8px 18px;
            min-width: 80px;
            font-family: 'JetBrains Mono', 'IBM Plex Mono', 'Consolas', monospace;
            letter-spacing: 0.5px;
        }
        QDialog QPushButton:hover, QMessageBox QPushButton:hover {
            background-color: #33ff66;
            color: #0a0c09;
            border-color: #33ff66;
        }
        """
        app_inst = QApplication.instance()
        if app_inst:
            app_inst.setStyleSheet(theme_qss)
        self.setStyleSheet(theme_qss)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Retro Terminal Font (JetBrains Mono / IBM Plex Mono / Consolas)
    font = QFont("JetBrains Mono", 10)
    font.setStyleHint(QFont.StyleHint.Monospace)
    app.setFont(font)

    window = LMSMainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
