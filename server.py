import os
import sys

# Configure UTF-8 stdout on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import json
import time
import queue
import socket
import threading
import subprocess
from typing import Dict, List, Optional
from flask import Flask, render_template, request, jsonify, Response, send_from_directory, send_file
from dotenv import load_dotenv

from lms_client import LMSClient, sanitize_filename, CourseItem, SectionItem, FileItem, FeedbackItem
from attendance_client import AttendanceClient
from attendance_parser import AttendanceParser, AttendanceReport
from feedback_solver import FeedbackParser, FeedbackAnswerGenerator

app = Flask(__name__, static_folder="static", template_folder="templates")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
FEEDBACK_CACHE_PATH = os.path.join(os.path.dirname(__file__), "feedback_cache.json")
COMPLETED_FEEDBACK_PATH = os.path.join(os.path.dirname(__file__), "completed_feedback.json")
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
DEFAULT_ATTENDANCE_PATH = os.path.join(os.path.dirname(__file__), "attendance_portal_afterlogin.html")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Global application state
global_client: Optional[LMSClient] = None
cached_courses: List[CourseItem] = []
cached_feedback: List[FeedbackItem] = []
download_event_queue: queue.Queue = queue.Queue()
download_active = False


def get_local_ip() -> str:
    """Find the local network IPv4 address for phone access."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_saved_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    load_dotenv()
    return {
        "username": os.getenv("LMS_USERNAME", ""),
        "password": os.getenv("LMS_PASSWORD", ""),
        "default_year": os.getenv("LMS_DEFAULT_YEAR", "3"),
        "remember": bool(os.getenv("LMS_USERNAME")),
        "gemini_api_key": os.getenv("GEMINI_API_KEY", ""),
        "attendance_email": "",
        "attendance_password": "",
        "attendance_access_token": "",
        "attendance_refresh_token": ""
    }


def save_config(
    username: str,
    password: str,
    default_year: str = "3",
    remember: bool = True,
    gemini_api_key: Optional[str] = None,
    attendance_email: Optional[str] = None,
    attendance_password: Optional[str] = None,
    attendance_access_token: Optional[str] = None,
    attendance_refresh_token: Optional[str] = None
):
    cfg = get_saved_config()
    cfg["username"] = username if remember else ""
    cfg["password"] = password if remember else ""
    cfg["default_year"] = str(default_year)
    cfg["remember"] = bool(remember)
    if gemini_api_key is not None:
        cfg["gemini_api_key"] = gemini_api_key
    if attendance_email is not None:
        cfg["attendance_email"] = attendance_email
    if attendance_password is not None:
        cfg["attendance_password"] = attendance_password
    if attendance_access_token is not None:
        cfg["attendance_access_token"] = attendance_access_token
    if attendance_refresh_token is not None:
        cfg["attendance_refresh_token"] = attendance_refresh_token
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def load_completed_feedback_ids() -> set:
    if os.path.exists(COMPLETED_FEEDBACK_PATH):
        try:
            with open(COMPLETED_FEEDBACK_PATH, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_completed_feedback_ids(completed_ids: set):
    try:
        with open(COMPLETED_FEEDBACK_PATH, "w", encoding="utf-8") as f:
            json.dump(sorted(list(completed_ids)), f, indent=2)
    except Exception:
        pass


def load_cached_feedback_items() -> List[FeedbackItem]:
    if os.path.exists(FEEDBACK_CACHE_PATH):
        try:
            with open(FEEDBACK_CACHE_PATH, "r", encoding="utf-8") as f:
                raw_list = json.load(f)
                return [
                    FeedbackItem(
                        id=d.get("id", ""),
                        name=d.get("name", ""),
                        course_id=d.get("course_id", ""),
                        course_name=d.get("course_name", ""),
                        url=d.get("url", ""),
                        course_year=d.get("course_year")
                    )
                    for d in raw_list
                ]
        except Exception:
            pass
    return []


def save_cached_feedback_items(items: List[FeedbackItem]):
    try:
        raw_list = [
            {
                "id": f.id,
                "name": f.name,
                "course_id": f.course_id,
                "course_name": f.course_name,
                "url": f.url,
                "course_year": f.course_year
            }
            for f in items
        ]
        with open(FEEDBACK_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(raw_list, f, indent=2)
    except Exception:
        pass


def get_or_create_client() -> LMSClient:
    global global_client
    if global_client is None:
        global_client = LMSClient()
    return global_client


def serialize_attendance_report(report: AttendanceReport, source: str = "LIVE SERVER") -> dict:
    return {
        "success": True,
        "source": source,
        "student_roll": report.student_roll or "STUDENT",
        "student_name": report.student_name or "STUDENT",
        "student_semester": report.student_semester or "CSE · Semester 5",
        "total_attended": report.total_attended,
        "total_sessions": report.total_sessions,
        "total_absent": report.total_absent,
        "overall_pct": report.overall_pct,
        "courses": [
            {
                "code": c.code,
                "name": c.name,
                "attended": c.attended,
                "total": c.total,
                "pct": c.pct,
                "missed": c.missed,
                "status_label": c.status_label,
                "buffer_str": c.buffer_str
            }
            for c in report.courses
        ],
        "recent_sessions": [
            {
                "code": s.code,
                "course": s.course,
                "datetime_venue": s.datetime_venue,
                "status": s.status,
                "is_present": s.is_present
            }
            for s in report.recent_sessions
        ]
    }


# ============================================================================
# Routes & API Endpoints
# ============================================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET"])
def api_get_config():
    cfg = get_saved_config()
    client = get_or_create_client()
    return jsonify({
        "username": cfg.get("username", ""),
        "has_password": bool(cfg.get("password")),
        "default_year": cfg.get("default_year", "3"),
        "remember": cfg.get("remember", False),
        "gemini_api_key": cfg.get("gemini_api_key", ""),
        "attendance_email": cfg.get("attendance_email", ""),
        "has_attendance_password": bool(cfg.get("attendance_password")),
        "is_logged_in": client.logged_in if client else False,
        "local_ip": get_local_ip()
    })


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    global global_client, cached_courses
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    remember = bool(data.get("remember", True))
    default_year = str(data.get("default_year", "3"))

    cfg = get_saved_config()
    if not password and cfg.get("password") and username == cfg.get("username"):
        password = cfg.get("password")

    if not username or not password:
        return jsonify({"success": False, "message": "Username and password are required."}), 400

    client = get_or_create_client()
    success, msg = client.login(username, password)

    if not success:
        return jsonify({"success": False, "message": msg}), 401

    if remember:
        save_config(username, password, default_year, remember=True)
    else:
        save_config("", "", default_year, remember=False)

    cached_courses = []
    return jsonify({
        "success": True,
        "message": "Login successful",
        "username": username,
        "default_year": default_year
    })


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    global global_client, cached_courses
    global_client = LMSClient()
    cached_courses = []
    return jsonify({"success": True, "message": "Logged out successfully"})


@app.route("/api/settings/update", methods=["POST"])
def api_update_settings():
    data = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    default_year = str(data.get("default_year", "3"))
    remember = bool(data.get("remember", True))
    gemini_key = data.get("gemini_api_key")
    att_email = data.get("attendance_email")
    att_password = data.get("attendance_password")

    cfg = get_saved_config()
    if not password and cfg.get("password"):
        password = cfg.get("password")
    if not att_password and cfg.get("attendance_password"):
        att_password = cfg.get("attendance_password")

    save_config(
        username,
        password,
        default_year,
        remember,
        gemini_api_key=gemini_key,
        attendance_email=att_email,
        attendance_password=att_password
    )
    return jsonify({"success": True, "message": "Settings updated successfully", "default_year": default_year})


# ----------------------------------------------------------------------------
# 1. Course Materials & Downloads
# ----------------------------------------------------------------------------

@app.route("/api/courses", methods=["GET"])
def api_get_courses():
    global cached_courses
    client = get_or_create_client()

    if not client.logged_in:
        cfg = get_saved_config()
        if cfg.get("username") and cfg.get("password"):
            ok, _ = client.login(cfg["username"], cfg["password"])
            if not ok:
                local_html = os.path.join(os.path.dirname(__file__), "courses.html")
                if os.path.exists(local_html):
                    try:
                        with open(local_html, "r", encoding="utf-8") as f:
                            cached_courses = client.parse_courses_from_html(f.read())
                    except Exception:
                        pass
        else:
            local_html = os.path.join(os.path.dirname(__file__), "courses.html")
            if os.path.exists(local_html):
                try:
                    with open(local_html, "r", encoding="utf-8") as f:
                        cached_courses = client.parse_courses_from_html(f.read())
                except Exception:
                    pass

    force_refresh = request.args.get("refresh") == "1"
    if client.logged_in and (not cached_courses or force_refresh):
        cached_courses = client.get_courses()

    result = []
    for c in cached_courses:
        result.append({
            "id": c.id,
            "name": c.name,
            "year": c.year,
            "url": c.url
        })

    return jsonify({
        "success": True,
        "courses": result,
        "total": len(result)
    })


@app.route("/api/course/<course_id>/preview", methods=["GET"])
def api_course_preview(course_id: str):
    client = get_or_create_client()
    if not client.logged_in:
        cfg = get_saved_config()
        if cfg.get("username") and cfg.get("password"):
            ok, _ = client.login(cfg["username"], cfg["password"])
            if not ok:
                return jsonify({"success": False, "message": "Not authenticated"}), 401
        else:
            return jsonify({"success": False, "message": "Not authenticated"}), 401

    try:
        sections = client.get_course_sections(course_id)
        formatted_sections = []
        total_activities = 0
        for s in sections:
            acts = []
            for a in s.activities:
                total_activities += 1
                acts.append({
                    "id": a.id,
                    "name": a.name,
                    "type": a.type,
                    "url": a.url
                })
            formatted_sections.append({
                "id": s.id,
                "title": s.title,
                "activities": acts
            })

        return jsonify({
            "success": True,
            "course_id": course_id,
            "sections": formatted_sections,
            "total_activities": total_activities
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


def run_download_worker(client: LMSClient, selected_course_ids: List[str], use_zip: bool):
    global download_active
    download_active = True

    try:
        courses_to_download = [c for c in cached_courses if c.id in selected_course_ids]
        total_courses = len(courses_to_download)

        send_sse_event({
            "type": "start",
            "total_courses": total_courses,
            "message": f"Starting download for {total_courses} course(s)..."
        })

        total_downloaded = 0
        total_skipped = 0
        total_failed = 0

        for c_idx, course in enumerate(courses_to_download, 1):
            send_sse_event({
                "type": "course_start",
                "course_index": c_idx,
                "total_courses": total_courses,
                "course_id": course.id,
                "course_name": course.name
            })

            sections = client.get_course_sections(course.id)
            clean_course_name = sanitize_filename(course.name)
            course_dir = os.path.join(DOWNLOAD_DIR, clean_course_name)

            for sec in sections:
                clean_sec_name = sanitize_filename(sec.title)
                for act in sec.activities:
                    if act.type == "folder":
                        act_dir = os.path.join(course_dir, clean_sec_name, sanitize_filename(act.name))
                        send_sse_event({
                            "type": "folder_scan",
                            "folder_name": act.name,
                            "section": sec.title
                        })

                        if use_zip:
                            res = client.download_folder_zip(act.id, os.path.join(course_dir, clean_sec_name), act.name, extract=True)
                            if res:
                                total_downloaded += 1
                                send_sse_event({
                                    "type": "file_success",
                                    "filename": f"{act.name}.zip (Extracted)",
                                    "folder": act.name
                                })
                            else:
                                total_failed += 1
                                send_sse_event({
                                    "type": "file_error",
                                    "filename": f"{act.name}.zip",
                                    "error": "Failed to download folder ZIP"
                                })
                        else:
                            files = client.get_folder_files(act.id)
                            for f in files:
                                target_sub = os.path.join(act_dir, f.relative_path) if f.relative_path else act_dir
                                final_p = os.path.join(target_sub, sanitize_filename(f.name))

                                if os.path.exists(final_p):
                                    total_skipped += 1
                                    send_sse_event({
                                        "type": "file_skipped",
                                        "filename": f.name,
                                        "folder": act.name
                                    })
                                    continue

                                send_sse_event({
                                    "type": "file_downloading",
                                    "filename": f.name,
                                    "folder": act.name
                                })

                                dl_p = client.download_file(f.url, target_sub, f.name)
                                if dl_p:
                                    total_downloaded += 1
                                    size_mb = os.path.getsize(dl_p) / (1024 * 1024)
                                    send_sse_event({
                                        "type": "file_success",
                                        "filename": f.name,
                                        "folder": act.name,
                                        "size_mb": round(size_mb, 2)
                                    })
                                else:
                                    total_failed += 1
                                    send_sse_event({
                                        "type": "file_error",
                                        "filename": f.name,
                                        "error": "Download failed"
                                    })

                    elif act.type == "resource":
                        target_sec = os.path.join(course_dir, clean_sec_name)
                        send_sse_event({
                            "type": "file_downloading",
                            "filename": act.name,
                            "folder": sec.title
                        })
                        dl_p = client.download_single_resource(act.id, target_sec, act.name)
                        if dl_p:
                            total_downloaded += 1
                            size_mb = os.path.getsize(dl_p) / (1024 * 1024)
                            send_sse_event({
                                "type": "file_success",
                                "filename": os.path.basename(dl_p),
                                "folder": sec.title,
                                "size_mb": round(size_mb, 2)
                            })
                        else:
                            total_failed += 1
                            send_sse_event({
                                "type": "file_error",
                                "filename": act.name,
                                "error": "Download failed"
                            })

        send_sse_event({
            "type": "completed",
            "downloaded": total_downloaded,
            "skipped": total_skipped,
            "failed": total_failed,
            "download_dir": DOWNLOAD_DIR
        })

    except Exception as e:
        send_sse_event({
            "type": "error",
            "message": str(e)
        })
    finally:
        download_active = False


def send_sse_event(event_data: dict):
    download_event_queue.put(event_data)


@app.route("/api/download/start", methods=["POST"])
def api_start_download():
    global download_active
    if download_active:
        return jsonify({"success": False, "message": "A download is already in progress"}), 409

    client = get_or_create_client()
    if not client.logged_in:
        return jsonify({"success": False, "message": "Not authenticated"}), 401

    data = request.json or {}
    course_ids = data.get("course_ids", [])
    use_zip = bool(data.get("use_zip", False))

    if not course_ids:
        return jsonify({"success": False, "message": "No courses selected"}), 400

    while not download_event_queue.empty():
        try:
            download_event_queue.get_nowait()
        except Exception:
            break

    thread = threading.Thread(target=run_download_worker, args=(client, course_ids, use_zip), daemon=True)
    thread.start()
    return jsonify({"success": True, "message": "Download started"})


@app.route("/api/download/progress")
def api_download_progress():
    def event_stream():
        while True:
            try:
                event = download_event_queue.get(timeout=25)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") in ["completed", "error"]:
                    break
            except queue.Empty:
                yield f": heartbeat\n\n"

    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/api/open-downloads", methods=["POST"])
def api_open_downloads():
    try:
        if sys.platform == "win32":
            os.startfile(DOWNLOAD_DIR)
        else:
            subprocess.Popen(["xdg-open", DOWNLOAD_DIR])
        return jsonify({"success": True, "path": DOWNLOAD_DIR})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ----------------------------------------------------------------------------
# 2. Course Feedback Forms Directory & Auto-Solver
# ----------------------------------------------------------------------------

@app.route("/api/feedback", methods=["GET"])
def api_get_feedback():
    items = load_cached_feedback_items()
    completed_ids = list(load_completed_feedback_ids())
    cfg = get_saved_config()

    if not items:
        a_course_path = os.path.join(os.path.dirname(__file__), "a_course.html")
        if os.path.exists(a_course_path):
            try:
                with open(a_course_path, "r", encoding="utf-8") as f:
                    dummy_course = CourseItem(id="141", name="CSS311 Parallel and Distributed Computing", url="", year=3)
                    client = get_or_create_client()
                    parsed = client.extract_feedback_forms_from_html(f.read(), dummy_course)
                    items.extend(parsed)
                    save_cached_feedback_items(items)
            except Exception:
                pass

    return jsonify({
        "success": True,
        "feedback": [
            {
                "id": f.id,
                "name": f.name,
                "course_id": f.course_id,
                "course_name": f.course_name,
                "url": f.url,
                "course_year": f.course_year,
                "is_completed": f.id in completed_ids
            }
            for f in items
        ],
        "completed_ids": completed_ids,
        "gemini_active": bool(cfg.get("gemini_api_key"))
    })


@app.route("/api/feedback/scan", methods=["POST"])
def api_scan_feedback():
    client = get_or_create_client()
    if not client.logged_in:
        cfg = get_saved_config()
        if cfg.get("username") and cfg.get("password"):
            client.login(cfg["username"], cfg["password"])

    if not client.logged_in:
        return jsonify({"success": False, "message": "Please log in to LMS first"}), 401

    try:
        courses = client.get_courses()
        all_found: List[FeedbackItem] = []
        for c in courses:
            try:
                forms = client.get_course_feedback_forms(c)
                all_found.extend(forms)
            except Exception:
                pass

        if all_found:
            save_cached_feedback_items(all_found)

        completed_ids = list(load_completed_feedback_ids())
        return jsonify({
            "success": True,
            "total_found": len(all_found),
            "feedback": [
                {
                    "id": f.id,
                    "name": f.name,
                    "course_id": f.course_id,
                    "course_name": f.course_name,
                    "url": f.url,
                    "course_year": f.course_year,
                    "is_completed": f.id in completed_ids
                }
                for f in all_found
            ]
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/feedback/autofill", methods=["POST"])
def api_autofill_feedback():
    data = request.json or {}
    fb_id = str(data.get("id", ""))
    fb_url = data.get("url", "")
    course_name = data.get("course_name", "")

    if not fb_url:
        return jsonify({"success": False, "message": "Feedback form URL is required"}), 400

    client = get_or_create_client()
    if not client.logged_in:
        cfg = get_saved_config()
        if cfg.get("username") and cfg.get("password"):
            client.login(cfg["username"], cfg["password"])

    if not client.logged_in:
        return jsonify({"success": False, "message": "Please log in to LMS first"}), 401

    cfg = get_saved_config()
    api_key = cfg.get("gemini_api_key", "")

    ok, msg, answers, method = client.auto_fill_feedback(
        feedback_url=fb_url,
        course_name=course_name,
        api_key=api_key,
        prefer_gemini=bool(api_key)
    )

    if ok and fb_id:
        completed = load_completed_feedback_ids()
        completed.add(fb_id)
        save_completed_feedback_ids(completed)

    return jsonify({
        "success": ok,
        "message": msg,
        "method": method,
        "answers": answers,
        "id": fb_id
    })


@app.route("/api/feedback/toggle-complete", methods=["POST"])
def api_toggle_feedback_complete():
    data = request.json or {}
    fb_id = str(data.get("id", ""))
    completed = bool(data.get("completed", True))

    if not fb_id:
        return jsonify({"success": False, "message": "Feedback ID is required"}), 400

    s = load_completed_feedback_ids()
    if completed:
        s.add(fb_id)
    else:
        s.discard(fb_id)
    save_completed_feedback_ids(s)
    return jsonify({"success": True, "id": fb_id, "is_completed": completed})


# ----------------------------------------------------------------------------
# 3. Facts-H Attendance Portal
# ----------------------------------------------------------------------------

@app.route("/api/attendance", methods=["GET"])
def api_get_attendance():
    cfg = get_saved_config()
    email = cfg.get("attendance_email", "").strip()
    token = cfg.get("attendance_access_token", "").strip()

    if token:
        client = AttendanceClient()
        ok, msg, report, raw = client.fetch_attendance(token)
        if ok and report:
            return jsonify(serialize_attendance_report(report, source="LIVE SERVER (attendance.iiitkottayam.ac.in)"))

    password = cfg.get("attendance_password", "").strip()
    if email and password:
        client = AttendanceClient()
        ok, msg, tokens = client.login(email, password)
        if ok and tokens:
            acc = tokens.get("access", "")
            ref = tokens.get("refresh", "")
            save_config(
                cfg.get("username", ""),
                cfg.get("password", ""),
                default_year=cfg.get("default_year", "3"),
                remember=cfg.get("remember", True),
                gemini_api_key=cfg.get("gemini_api_key", ""),
                attendance_email=email,
                attendance_password=password,
                attendance_access_token=acc,
                attendance_refresh_token=ref
            )
            ok2, msg2, report, raw = client.fetch_attendance(acc)
            if ok2 and report:
                return jsonify(serialize_attendance_report(report, source="LIVE SERVER (attendance.iiitkottayam.ac.in)"))

    if os.path.exists(DEFAULT_ATTENDANCE_PATH):
        try:
            report = AttendanceParser.load_from_file(DEFAULT_ATTENDANCE_PATH)
            return jsonify(serialize_attendance_report(report, source=f"OFFLINE CACHE ({os.path.basename(DEFAULT_ATTENDANCE_PATH)})"))
        except Exception as e:
            return jsonify({"success": False, "message": f"Error loading offline attendance: {str(e)}"}), 500

    return jsonify({"success": False, "message": "Attendance credentials or snapshot not available."}), 404


@app.route("/api/attendance/sync", methods=["POST"])
def api_sync_attendance():
    data = request.json or {}
    cfg = get_saved_config()
    email = data.get("email") or cfg.get("attendance_email", "").strip()
    password = data.get("password") or cfg.get("attendance_password", "").strip()

    if not email or not password:
        return jsonify({"success": False, "message": "Attendance portal email and password required."}), 400

    client = AttendanceClient()
    ok, msg, tokens = client.login(email, password)
    if not ok or not tokens:
        return jsonify({"success": False, "message": f"Authentication rejected by FACTS-H: {msg}"}), 401

    acc = tokens.get("access", "")
    ref = tokens.get("refresh", "")
    save_config(
        cfg.get("username", ""),
        cfg.get("password", ""),
        default_year=cfg.get("default_year", "3"),
        remember=cfg.get("remember", True),
        gemini_api_key=cfg.get("gemini_api_key", ""),
        attendance_email=email,
        attendance_password=password,
        attendance_access_token=acc,
        attendance_refresh_token=ref
    )

    ok2, msg2, report, raw = client.fetch_attendance(acc)
    if ok2 and report:
        return jsonify(serialize_attendance_report(report, source="LIVE SERVER (attendance.iiitkottayam.ac.in)"))
    else:
        return jsonify({"success": False, "message": f"Login succeeded but attendance fetch failed: {msg2}"}), 500


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    local_ip = get_local_ip()

    print("\n" + "=" * 65)
    print("🚀 Isabella Mobile & Desktop Web Suite is RUNNING!")
    print(f"👉 Local Desktop URL:   http://127.0.0.1:{port}")
    print(f"📱 Android / LAN URL:    http://{local_ip}:{port}")
    print("   (Open the Android URL in your phone browser on the same Wi-Fi)")
    print("=" * 65 + "\n")

    # Bind to 0.0.0.0 so phone/tablet on same network can access
    app.run(host="0.0.0.0", port=port, debug=False)
