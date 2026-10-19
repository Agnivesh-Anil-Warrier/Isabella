import os
import re
import sys
import json
import zipfile
import urllib.parse
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import requests
from bs4 import BeautifulSoup

def sanitize_filename(name: str) -> str:
    r"""
    Remove or replace invalid filesystem characters (especially for Windows).
    Invalid: < > : " / \ | ? *
    """
    # Replace invalid chars with underscore or space
    clean = re.sub(r'[<>:"/\\|?*]', '_', name)
    # Strip trailing spaces or dots (invalid in Windows filenames)
    clean = clean.strip('. ')
    return clean if clean else 'unnamed'

@dataclass
class FileItem:
    name: str
    url: str
    relative_path: str = ""
    size_bytes: int = 0

@dataclass
class ActivityItem:
    id: str
    name: str
    type: str  # 'folder', 'resource', 'assign', 'url', etc.
    url: str
    files: List[FileItem] = field(default_factory=list)

@dataclass
class SectionItem:
    id: str
    title: str
    activities: List[ActivityItem] = field(default_factory=list)

def extract_course_year(name: str) -> Optional[int]:
    r"""
    Extract the year of study from the course name.
    The first digit after the 3-letter code indicates the year of study (1, 2, 3, or 4).
    Examples:
      'IMA221 Differential Equations' -> 2
      'CSS311 Parallel and Distributed Computing' -> 3
      'ICS 112 COMPUTER PROGRAMMING' -> 1
    """
    m = re.search(r'\b[A-Za-z]{3}\s*([1-4])\d{2}', name)
    if m:
        return int(m.group(1))
    return None

@dataclass
class CourseItem:
    id: str
    name: str
    url: str
    year: Optional[int] = None
    sections: List[SectionItem] = field(default_factory=list)


class LMSClient:
    """
    Client for interacting with IIIT Kottayam LMS (Moodle).
    Supports logging in, parsing courses, modules, folders, and downloading files.
    """
    def __init__(self, base_url: str = "https://lmsug24.iiitkottayam.ac.in", debug: bool = False):
        self.base_url = base_url.rstrip('/')
        self.debug = debug
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        })
        self.logged_in = False
        self.sesskey: Optional[str] = None

    def login(self, username: str, password: str) -> Tuple[bool, str]:
        """
        Log into the LMS using username and password.
        Returns (success: bool, message: str)
        """
        login_url = f"{self.base_url}/login/index.php"
        try:
            # 1. Fetch login page to retrieve logintoken (CSRF token)
            resp = self.session.get(login_url, timeout=15)
            if resp.status_code != 200:
                return False, f"Failed to load login page (status code {resp.status_code})"

            soup = BeautifulSoup(resp.text, 'html.parser')
            token_input = soup.find('input', {'name': 'logintoken'})
            if not token_input or not token_input.get('value'):
                return False, "Could not find CSRF logintoken on login page"

            logintoken = token_input['value']

            # 2. Submit credentials
            payload = {
                'anchor': '',
                'logintoken': logintoken,
                'username': username,
                'password': password
            }

            post_resp = self.session.post(login_url, data=payload, timeout=20)
            post_soup = BeautifulSoup(post_resp.text, 'html.parser')

            # Check for error alert on the returned page
            err = post_soup.find('div', class_=re.compile(r'alert-danger|loginerrors')) or post_soup.find(class_='alert-danger')
            if err:
                err_text = err.get_text(separator=' ', strip=True)
                return False, f"Login failed: {err_text}"

            # If still on login/index.php or login form is present, login failed
            if '/login/index.php' in post_resp.url or post_soup.find('form', id='login'):
                # Check for any alert or error text
                err_box = post_soup.find(['div', 'span', 'p'], class_=re.compile(r'alert|error|loginerrors'))
                err_text = err_box.get_text(separator=' ', strip=True) if err_box else "Invalid credentials or login rejected"
                return False, f"Login failed: {err_text}"

            # Login succeeded (redirected to /my/ or similar authenticated page)
            self.logged_in = True
            self.sesskey = self.extract_sesskey(post_resp.text)

            # Ensure we have sesskey
            if not self.sesskey:
                try:
                    r = self.session.get(f"{self.base_url}/my/", timeout=10)
                    self.sesskey = self.extract_sesskey(r.text)
                except Exception:
                    pass

            return True, "Login successful"

        except Exception as e:
            return False, f"Network or connection error during login: {str(e)}"

    def extract_sesskey(self, html: str) -> Optional[str]:
        """
        Extract Moodle sesskey from HTML, M.cfg, hidden inputs, or logout link.
        """
        if not html:
            return None
        # 1. From M.cfg = { ... "sesskey": "..." }
        m = re.search(r'"sesskey"\s*:\s*"([^"]+)"', html)
        if m:
            return m.group(1)
        # 2. From hidden input name="sesskey" value="..."
        m = re.search(r'name=["\']sesskey["\']\s+value=["\']([^"\']+)["\']', html)
        if m:
            return m.group(1)
        m = re.search(r'value=["\']([^"\']+)["\']\s+name=["\']sesskey["\']', html)
        if m:
            return m.group(1)
        # 3. From logout link ?sesskey=...
        m = re.search(r'logout\.php\?sesskey=([a-zA-Z0-9]+)', html)
        if m:
            return m.group(1)
        return None

    def parse_courses_from_html(self, html_content: str) -> List[CourseItem]:
        """
        Parse courses from HTML content of /my/courses.php, /my/, or AJAX response.
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        courses_dict: Dict[str, CourseItem] = {}

        for a in soup.find_all('a', href=True):
            href = a['href']
            if '/course/view.php?id=' in href:
                match = re.search(r'id=(\d+)', href)
                if not match:
                    continue
                cid = match.group(1)
                text = a.get_text(separator=' ', strip=True)
                # Clean up Moodle tags
                text = re.sub(r'Course is starred|Course name|Select course', '', text, flags=re.IGNORECASE)
                text = re.sub(r'\s+', ' ', text).strip()
                
                # Keep the longest descriptive name found for the course ID
                if cid not in courses_dict or len(text) > len(courses_dict[cid].name):
                    clean_url = f"{self.base_url}/course/view.php?id={cid}"
                    courses_dict[cid] = CourseItem(
                        id=cid,
                        name=text or f"Course {cid}",
                        url=clean_url,
                        year=extract_course_year(text)
                    )

        return sorted(list(courses_dict.values()), key=lambda c: c.id)

    def get_courses(self) -> List[CourseItem]:
        """
        Fetch enrolled courses via Moodle AJAX web services, profile, or HTML pages.
        """
        courses_dict: Dict[str, CourseItem] = {}

        if not self.sesskey:
            try:
                r = self.session.get(f"{self.base_url}/my/", timeout=10)
                self.sesskey = self.extract_sesskey(r.text)
                if self.debug:
                    print(f"[DEBUG] Extracted sesskey from /my/: {self.sesskey}")
            except Exception as e:
                if self.debug:
                    print(f"[DEBUG] Error fetching /my/ for sesskey: {e}")

        # 1. Try Moodle AJAX web service: core_course_get_enrolled_courses_by_timeline_classification
        if self.sesskey:
            ajax_url = f"{self.base_url}/lib/ajax/service.php?sesskey={self.sesskey}&info=core_course_get_enrolled_courses_by_timeline_classification"
            payload = [{
                "index": 0,
                "methodname": "core_course_get_enrolled_courses_by_timeline_classification",
                "args": {
                    "offset": 0,
                    "limit": 0,
                    "classification": "all",
                    "sort": "fullname"
                }
            }]
            try:
                if self.debug:
                    print(f"[DEBUG] Requesting core_course_get_enrolled_courses_by_timeline_classification...")
                r = self.session.post(ajax_url, json=payload, timeout=15)
                if self.debug:
                    print(f"[DEBUG] AJAX response status: {r.status_code}")
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list) and len(data) > 0 and not data[0].get("error"):
                        course_list = data[0].get("data", {}).get("courses", [])
                        if self.debug:
                            print(f"[DEBUG] AJAX found {len(course_list)} courses in timeline classification")
                        for c in course_list:
                            cid = str(c.get("id"))
                            name = c.get("fullname") or c.get("shortname") or f"Course {cid}"
                            viewurl = c.get("viewurl") or f"{self.base_url}/course/view.php?id={cid}"
                            courses_dict[cid] = CourseItem(
                                id=cid,
                                name=name.strip(),
                                url=viewurl,
                                year=extract_course_year(name)
                            )
                        if courses_dict:
                            return sorted(list(courses_dict.values()), key=lambda x: x.id)
            except Exception as e:
                if self.debug:
                    print(f"[DEBUG] AJAX core_course error: {e}")

            # 2. Try theme_degrade_mycourses_html (specific to IIIT Kottayam's theme)
            theme_ajax_url = f"{self.base_url}/lib/ajax/service.php?sesskey={self.sesskey}&info=theme_degrade_mycourses_html"
            theme_payload = [{
                "index": 0,
                "methodname": "theme_degrade_mycourses_html",
                "args": {}
            }]
            try:
                if self.debug:
                    print(f"[DEBUG] Requesting theme_degrade_mycourses_html...")
                r = self.session.post(theme_ajax_url, json=theme_payload, timeout=15)
                if self.debug:
                    print(f"[DEBUG] theme_degrade status: {r.status_code}")
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list) and len(data) > 0 and not data[0].get("error"):
                        html_snippet = data[0].get("data", {}).get("html", "")
                        if html_snippet:
                            courses = self.parse_courses_from_html(html_snippet)
                            if self.debug:
                                print(f"[DEBUG] theme_degrade parsed {len(courses)} courses")
                            if courses:
                                return courses
            except Exception as e:
                if self.debug:
                    print(f"[DEBUG] theme_degrade error: {e}")

        # 3. Try /user/profile.php (Moodle displays all enrolled courses on user profile)
        try:
            if self.debug:
                print(f"[DEBUG] Checking /user/profile.php...")
            r = self.session.get(f"{self.base_url}/user/profile.php", timeout=15)
            if r.status_code == 200:
                courses = self.parse_courses_from_html(r.text)
                if self.debug:
                    print(f"[DEBUG] /user/profile.php parsed {len(courses)} courses")
                if courses:
                    return courses
        except Exception as e:
            if self.debug:
                print(f"[DEBUG] profile.php error: {e}")

        # 4. Fallback to /my/courses.php
        try:
            if self.debug:
                print(f"[DEBUG] Checking /my/courses.php...")
            r = self.session.get(f"{self.base_url}/my/courses.php", timeout=15)
            if r.status_code == 200:
                courses = self.parse_courses_from_html(r.text)
                if self.debug:
                    print(f"[DEBUG] /my/courses.php parsed {len(courses)} courses")
                if courses:
                    return courses
        except Exception as e:
            if self.debug:
                print(f"[DEBUG] courses.php error: {e}")

        # 5. Fallback to /my/
        try:
            if self.debug:
                print(f"[DEBUG] Checking /my/...")
            r = self.session.get(f"{self.base_url}/my/", timeout=15)
            if r.status_code == 200:
                courses = self.parse_courses_from_html(r.text)
                if self.debug:
                    print(f"[DEBUG] /my/ parsed {len(courses)} courses")
                if courses:
                    return courses
        except Exception as e:
            if self.debug:
                print(f"[DEBUG] /my/ error: {e}")

        return []

    def parse_course_page_from_html(self, html_content: str, course_id: str = "") -> List[SectionItem]:
        """
        Parse sections and activities (folders, resources, assignments) from course page HTML.
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        sections: List[SectionItem] = []

        # Find section containers (topics / weeks / generic section list items)
        sec_elements = soup.select('.course-content ul.topics > li, .course-content ul.weeks > li, li.section')
        if not sec_elements:
            sec_elements = soup.find_all(['li', 'div'], class_=re.compile(r'\bsection\b'))

        for idx, sec in enumerate(sec_elements):
            sec_id = sec.get('id') or str(idx)
            # Find section title
            sec_title_el = sec.find(['h3', 'h4', 'span'], class_=re.compile(r'sectionname|section-title'))
            sec_name = sec_title_el.get_text(strip=True) if sec_title_el else f"Section {idx}"
            
            section_item = SectionItem(id=sec_id, title=sec_name)

            # Find all activity items in section
            act_elements = sec.select('.activity, li.activityinstance, div.activityitem')
            for act in act_elements:
                link = act.find('a', href=True)
                if not link:
                    continue
                href = link['href']
                
                # Determine activity type
                act_type = "unknown"
                if '/mod/folder/' in href:
                    act_type = "folder"
                elif '/mod/resource/' in href:
                    act_type = "resource"
                elif '/mod/assign/' in href:
                    act_type = "assign"
                elif '/mod/url/' in href:
                    act_type = "url"
                else:
                    # Ignore forums, quizzes, feedback unless requested
                    continue

                # Extract activity ID
                m = re.search(r'id=(\d+)', href)
                act_id = m.group(1) if m else ""

                # Extract activity name
                name_el = act.find(class_=re.compile(r'instancename|activityname')) or link
                # Clean text: remove type badges like 'Folder', 'File', 'Assignment'
                raw_name = name_el.get_text(separator=' ', strip=True) if name_el else link.get_text(strip=True)
                clean_name = re.sub(r'\b(Folder|File|Assignment|URL)\b', '', raw_name, flags=re.IGNORECASE).strip()
                if not clean_name:
                    clean_name = f"{act_type.capitalize()} {act_id}"

                act_item = ActivityItem(id=act_id, name=clean_name, type=act_type, url=href)
                section_item.activities.append(act_item)

            if section_item.activities or sec_name != f"Section {idx}":
                sections.append(section_item)

        return sections

    def get_course_sections(self, course_id: str) -> List[SectionItem]:
        """
        Fetch course page and parse sections and activities.
        """
        course_url = f"{self.base_url}/course/view.php?id={course_id}"
        resp = self.session.get(course_url, timeout=20)
        if resp.status_code != 200:
            return []
        return self.parse_course_page_from_html(resp.text, course_id)

    def parse_folder_files_from_html(self, html_content: str) -> List[FileItem]:
        """
        Parse files inside a folder activity page.
        """
        soup = BeautifulSoup(html_content, 'html.parser')
        files: List[FileItem] = []
        seen_urls = set()

        for a in soup.find_all('a', href=True):
            href = a['href']
            # Match pluginfile.php links that are not favicons/themes
            if 'pluginfile.php' in href and not any(k in href for k in ['favicon', 'theme', 'pix.php']):
                if href in seen_urls:
                    continue
                seen_urls.add(href)

                # Get clean name from link text or URL
                name = a.get_text(strip=True)
                if not name or 'google.com/viewer' in href:
                    # If empty text or viewer link, try to parse from pluginfile URL
                    if 'mod_folder/content/' in href:
                        url_path = href.split('mod_folder/content/')[1].split('?')[0]
                        name = urllib.parse.unquote(os.path.basename(url_path))
                    else:
                        continue

                # Relative path inside the folder
                rel_path = ""
                if 'mod_folder/content/' in href:
                    raw_rel = href.split('mod_folder/content/')[1].split('?')[0]
                    # Format: 0/path/to/file.ext -> remove leading '0/'
                    parts = raw_rel.split('/')
                    if len(parts) > 2:
                        rel_path = os.path.join(*[urllib.parse.unquote(p) for p in parts[1:-1]])

                # Ensure forcedownload=1 is included so it downloads directly
                if 'forcedownload=1' not in href and '?' in href:
                    href += '&forcedownload=1'
                elif 'forcedownload=1' not in href:
                    href += '?forcedownload=1'

                files.append(FileItem(name=name, url=href, relative_path=rel_path))

        return files

    def get_folder_files(self, folder_id: str) -> List[FileItem]:
        """
        Fetch folder page and parse file items.
        """
        folder_url = f"{self.base_url}/mod/folder/view.php?id={folder_id}"
        resp = self.session.get(folder_url, timeout=20)
        if resp.status_code != 200:
            return []
        return self.parse_folder_files_from_html(resp.text)

    def download_folder_zip(self, folder_id: str, target_dir: str, folder_name: str, extract: bool = False) -> Optional[str]:
        """
        Download the entire folder as a single ZIP archive using Moodle's download_folder.php.
        Optionally extract it directly into target_dir.
        """
        zip_url = f"{self.base_url}/mod/folder/download_folder.php?id={folder_id}"
        clean_folder = sanitize_filename(folder_name)
        dest_dir = os.path.join(target_dir, clean_folder)
        os.makedirs(dest_dir, exist_ok=True)

        zip_path = os.path.join(dest_dir, f"{clean_folder}.zip")

        try:
            resp = self.session.get(zip_url, stream=True, timeout=30)
            if resp.status_code != 200:
                return None

            with open(zip_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=16384):
                    if chunk:
                        f.write(chunk)

            if extract:
                try:
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        zip_ref.extractall(dest_dir)
                    os.remove(zip_path)
                except Exception as e:
                    print(f"Warning: Extraction failed for {zip_path}: {e}")

            return dest_dir
        except Exception as e:
            print(f"Error downloading folder zip {folder_id}: {e}")
            return None

    def download_single_resource(self, resource_id: str, target_dir: str, default_name: str = "") -> Optional[str]:
        """
        Download a single resource (e.g. /mod/resource/view.php?id=...)
        Moodle may redirect directly to pluginfile.php or embed it in an HTML page.
        """
        res_url = f"{self.base_url}/mod/resource/view.php?id={resource_id}"
        os.makedirs(target_dir, exist_ok=True)
        try:
            resp = self.session.get(res_url, stream=True, timeout=30)
            if resp.status_code != 200:
                return None

            # Check Content-Type
            content_type = resp.headers.get('Content-Type', '').lower()
            if 'text/html' in content_type:
                # Embedded page: search for pluginfile.php link inside HTML
                soup = BeautifulSoup(resp.text, 'html.parser')
                link = soup.find('a', href=lambda h: h and 'pluginfile.php' in h)
                if link:
                    file_url = link['href']
                    file_name = link.get_text(strip=True) or default_name
                    return self.download_file(file_url, target_dir, file_name)
                # Check object or iframe
                obj = soup.find(['object', 'iframe', 'embed'], src=True) or soup.find('object', data=True)
                if obj:
                    src = obj.get('src') or obj.get('data')
                    if src:
                        return self.download_file(src, target_dir, default_name)
                return None
            else:
                # Direct file stream
                # Determine filename from Content-Disposition header
                filename = default_name
                cd = resp.headers.get('Content-Disposition', '')
                if 'filename=' in cd:
                    match = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';\r\n]+)', cd)
                    if match:
                        filename = urllib.parse.unquote(match.group(1))

                if not filename:
                    filename = f"resource_{resource_id}"

                filename = sanitize_filename(filename)
                target_file = os.path.join(target_dir, filename)

                # Skip if already exists and size matches
                remote_size = int(resp.headers.get('Content-Length', 0))
                if os.path.exists(target_file) and remote_size > 0:
                    if os.path.getsize(target_file) == remote_size:
                        return target_file

                with open(target_file, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=16384):
                        if chunk:
                            f.write(chunk)

                return target_file
        except Exception as e:
            print(f"Error downloading resource {resource_id}: {e}")
            return None

    def download_file(self, file_url: str, target_dir: str, filename: Optional[str] = None) -> Optional[str]:
        """
        Download a file from direct URL (e.g. pluginfile.php) to target_dir.
        Avoids re-downloading if already present with matching size.
        """
        os.makedirs(target_dir, exist_ok=True)
        try:
            resp = self.session.get(file_url, stream=True, timeout=30)
            if resp.status_code != 200:
                return None

            # Determine filename
            if not filename:
                cd = resp.headers.get('Content-Disposition', '')
                if 'filename=' in cd:
                    match = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';\r\n]+)', cd)
                    if match:
                        filename = urllib.parse.unquote(match.group(1))
            
            if not filename:
                parsed = urllib.parse.urlparse(file_url)
                filename = urllib.parse.unquote(os.path.basename(parsed.path))

            if not filename:
                filename = "downloaded_file"

            filename = sanitize_filename(filename)
            target_path = os.path.join(target_dir, filename)

            remote_size = int(resp.headers.get('Content-Length', 0))
            if os.path.exists(target_path) and remote_size > 0:
                if os.path.getsize(target_path) == remote_size:
                    # Already up to date
                    return target_path

            with open(target_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=16384):
                    if chunk:
                        f.write(chunk)

            return target_path
        except Exception as e:
            print(f"Error downloading file {file_url}: {e}")
            return None
