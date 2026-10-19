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
    QFrame, QAbstractItemView, QMessageBox, QFileDialog, QScrollArea
)

from lms_client import LMSClient, sanitize_filename, CourseItem, SectionItem, FileItem

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def get_saved_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "username": "",
        "password": "",
        "default_year": "3",
        "remember": True
    }


def save_config(username: str, password: str, default_year: str = "3", remember: bool = True):
    cfg = {
        "username": username if remember else "",
        "password": password if remember else "",
        "default_year": str(default_year),
        "remember": bool(remember)
    }
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
                sections = self.client.get_course_contents(course.id)
            except Exception as e:
                self.log.emit(f"> [ERR] Failed to fetch contents for {course.name}: {e}", "error")
                stats["failed"] += 1
                continue

            has_materials = False
            for section in sections:
                if self.is_cancelled:
                    break

                sec_folder_name = sanitize_filename(section.name)
                sec_dir = os.path.join(course_dir, sec_folder_name)
                os.makedirs(sec_dir, exist_ok=True)

                # 1. Download Folder modules
                for folder in section.folders:
                    if self.is_cancelled:
                        break
                    has_materials = True
                    target_folder_dir = os.path.join(sec_dir, sanitize_filename(folder.name))
                    os.makedirs(target_folder_dir, exist_ok=True)

                    if self.use_zip:
                        zip_dest = os.path.join(sec_dir, sanitize_filename(f"{folder.name}.zip"))
                        if os.path.exists(zip_dest) and os.path.getsize(zip_dest) > 0:
                            self.log.emit(f"  > [SKIP] ZIP archive exists: {os.path.basename(zip_dest)}", "skipped")
                            stats["skipped"] += 1
                        else:
                            self.log.emit(f"  > [ZIP] Pulling folder archive: {folder.name}...", "info")
                            ok = self.client.download_folder_zip(folder.id, zip_dest)
                            if ok:
                                size_mb = os.path.getsize(zip_dest) / (1024 * 1024)
                                self.log.emit(f"  > [SAVED] {os.path.basename(zip_dest)} ({size_mb:.2f} MB)", "success")
                                stats["downloaded"] += 1
                            else:
                                self.log.emit(f"  > [ERR] ZIP download failed: {folder.name}", "error")
                                stats["failed"] += 1
                    else:
                        try:
                            f_items = self.client.get_folder_files(folder.url)
                            for fi in f_items:
                                if self.is_cancelled:
                                    break
                                file_dest = os.path.join(target_folder_dir, sanitize_filename(fi.name))
                                if os.path.exists(file_dest) and os.path.getsize(file_dest) > 0:
                                    self.log.emit(f"  > [SKIP] File exists: {fi.name}", "skipped")
                                    stats["skipped"] += 1
                                else:
                                    ok = self.client.download_file(fi.url, file_dest)
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

                # 2. Download direct files / resources
                for fitem in section.files:
                    if self.is_cancelled:
                        break
                    has_materials = True
                    fname = sanitize_filename(fitem.name)
                    if not any(fname.lower().endswith(ext) for ext in ['.pdf', '.ppt', '.pptx', '.doc', '.docx', '.zip', '.ipynb', '.py', '.txt', '.png', '.jpg']):
                        fname += ".pdf"

                    file_dest = os.path.join(sec_dir, fname)
                    if os.path.exists(file_dest) and os.path.getsize(file_dest) > 0:
                        self.log.emit(f"  > [SKIP] File exists: {fname}", "skipped")
                        stats["skipped"] += 1
                    else:
                        ok = self.client.download_file(fitem.url, file_dest)
                        if ok:
                            size_mb = os.path.getsize(file_dest) / (1024 * 1024)
                            self.log.emit(f"  > [SAVED] {fname} ({size_mb:.2f} MB)", "success")
                            stats["downloaded"] += 1
                        else:
                            self.log.emit(f"  > [ERR] Download failed: {fname}", "error")
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

        # Blinking cursor state
        self.cursor_state = True
        self.current_status_msg = "> SYSTEM READY • SELECT TARGET MODULES"
        self.cursor_timer = QTimer(self)
        self.cursor_timer.timeout.connect(self.blink_cursor)
        self.cursor_timer.start(530)

        self.apply_retro_terminal_theme()
        self.init_ui()
        self.load_saved_credentials()

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

        self.btn_nav_settings = QPushButton("> 02. ACCOUNT SETTINGS")
        self.btn_nav_settings.setObjectName("navBtn")
        self.btn_nav_settings.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_nav_settings.clicked.connect(lambda: self.switch_page(1))
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

        # Page 1: Settings & Account
        page_settings = self.build_settings_page()
        self.page_stack.addWidget(page_settings)

        root_layout.addWidget(self.page_stack, 1)

    def switch_page(self, index: int):
        self.page_stack.setCurrentIndex(index)
        if index == 0:
            self.btn_nav_downloader.setObjectName("navBtnActive")
            self.btn_nav_settings.setObjectName("navBtn")
        else:
            self.btn_nav_downloader.setObjectName("navBtn")
            self.btn_nav_settings.setObjectName("navBtnActive")
        self.btn_nav_downloader.setStyle(self.btn_nav_downloader.style())
        self.btn_nav_settings.setStyle(self.btn_nav_settings.style())

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
    # Page 1: Settings & Account Builder
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

        # Card 2: Downloads Directory
        dir_card = QFrame()
        dir_card.setObjectName("settingsCard")
        dc_layout = QVBoxLayout(dir_card)
        dc_layout.setContentsMargins(18, 16, 18, 16)
        dc_layout.setSpacing(10)

        dc_title = QLabel("> 02. LOCAL STORAGE REPOSITORY")
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

        # Card 3: Danger Zone
        danger_card = QFrame()
        danger_card.setObjectName("dangerCard")
        dgc_layout = QHBoxLayout(danger_card)
        dgc_layout.setContentsMargins(18, 14, 18, 14)

        danger_text = QVBoxLayout()
        d_title = QLabel("> 03. PURGE LOCAL CREDENTIALS")
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

        self.input_username.setText(username)
        self.input_password.setText(password)
        self.chk_remember.setChecked(remember)

        idx = ["1", "2", "3", "4"].index(default_year) if default_year in ["1", "2", "3", "4"] else 2
        self.combo_default_year.setCurrentIndex(idx)

        # Set default active year pill
        self.set_year_filter(default_year)

        if username and password:
            self.lbl_user_roll.setText(f"UID: {username}")
            self.lbl_user_status.setText("[◌ AUTHENTICATING]")
            self.lbl_user_status.setStyleSheet("color: #4dd8e6; font-size: 11px; font-weight: 700;")
            self.perform_login(auto_fetch=True)
        else:
            self.switch_page(1)  # Focus settings page for login

    def save_settings(self):
        username = self.input_username.text().strip()
        password = self.input_password.text().strip()
        default_year = str(self.combo_default_year.currentIndex() + 1)
        remember = self.chk_remember.isChecked()

        save_config(username, password, default_year, remember)
        QMessageBox.information(self, "SYSTEM_UPDATE", "> LMS CREDENTIALS AND DEFAULT YEAR SAVED.")

    def forget_credentials(self):
        reply = QMessageBox.question(
            self, "PURGE_CONFIRMATION",
            "> ARE YOU SURE YOU WANT TO PURGE STORED CREDENTIALS?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            save_config("", "", "3", remember=False)
            self.input_username.clear()
            self.input_password.clear()
            self.lbl_user_roll.setText("UID: NOT_LOGGED_IN")
            self.lbl_user_status.setText("[○ OFFLINE]")
            self.lbl_user_status.setStyleSheet("color: #5f7a5c; font-size: 11px; font-weight: 700;")
            self.switch_page(1)
            QMessageBox.information(self, "PURGED", "> STORED CREDENTIALS REMOVED FROM DISK.")

    def perform_login(self, auto_fetch=False):
        username = self.input_username.text().strip()
        password = self.input_password.text().strip()

        if not username or not password:
            QMessageBox.warning(self, "AUTH_REQUIRED", "> PLEASE PROVIDE LMS ROLL NUMBER AND PASSWORD.")
            self.switch_page(1)
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
        """
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
