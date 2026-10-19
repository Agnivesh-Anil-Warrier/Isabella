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
import threading
import subprocess
from typing import Dict, List, Optional
from flask import Flask, render_template, request, jsonify, Response, send_from_directory
from dotenv import load_dotenv

from lms_client import LMSClient, sanitize_filename, CourseItem, SectionItem, FileItem

app = Flask(__name__, static_folder="static", template_folder="templates")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Global application state
global_client: Optional[LMSClient] = None
cached_courses: List[CourseItem] = []
download_event_queue: queue.Queue = queue.Queue()
download_active = False

def get_saved_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # Fallback to .env
    load_dotenv()
    u = os.getenv("LMS_USERNAME", "")
    p = os.getenv("LMS_PASSWORD", "")
    y = os.getenv("LMS_DEFAULT_YEAR", "3")
    return {
        "username": u,
        "password": p,
        "default_year": y,
        "remember": bool(u)
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

def get_or_create_client() -> LMSClient:
    global global_client
    if global_client is None:
        global_client = LMSClient()
    return global_client

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/config", methods=["GET"])
def api_get_config():
    cfg = get_saved_config()
    return jsonify({
        "username": cfg.get("username", ""),
        "has_password": bool(cfg.get("password")),
        "default_year": cfg.get("default_year", "3"),
        "remember": cfg.get("remember", False),
        "is_logged_in": global_client.logged_in if global_client else False
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

    # Invalidate cached courses to fetch fresh
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

    cfg = get_saved_config()
    # Preserve existing password if not provided
    if not password and cfg.get("password"):
        password = cfg.get("password")

    save_config(username, password, default_year, remember)
    return jsonify({"success": True, "message": "Settings updated successfully", "default_year": default_year})

@app.route("/api/courses", methods=["GET"])
def api_get_courses():
    global cached_courses
    client = get_or_create_client()

    if not client.logged_in:
        # Check if saved credentials can auto-login
        cfg = get_saved_config()
        if cfg.get("username") and cfg.get("password"):
            ok, _ = client.login(cfg["username"], cfg["password"])
            if not ok:
                return jsonify({"success": False, "message": "Please log in first", "require_login": True}), 401
        else:
            return jsonify({"success": False, "message": "Please log in first", "require_login": True}), 401

    force_refresh = request.args.get("refresh") == "1"
    if not cached_courses or force_refresh:
        cached_courses = client.get_courses()

    # Format courses for frontend
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

    # Clear queue
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
                # Send heartbeat
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n=======================================================")
    print(f"🚀 IIIT Kottayam LMS Web UI is running at:")
    print(f"👉 http://127.0.0.1:{port}")
    print(f"=======================================================\n")
    app.run(host="127.0.0.1", port=port, debug=False)
