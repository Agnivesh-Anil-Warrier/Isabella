import os
import re
from dataclasses import dataclass, field
from typing import List, Optional
from bs4 import BeautifulSoup

@dataclass
class CourseAttendance:
    code: str
    name: str
    attended: int
    total: int
    pct: float
    sessions_str: str

    @property
    def missed(self) -> int:
        return max(0, self.total - self.attended)

    @property
    def status_label(self) -> str:
        if self.pct >= 85.0:
            return "EXCELLENT"
        elif self.pct >= 80.0:
            return "SAFE"
        else:
            return "DEFICIT"

    @property
    def buffer_str(self) -> str:
        if self.total == 0:
            return "--"
        if self.pct >= 80.0:
            can_miss = int(self.attended / 0.80) - self.total
            can_miss = max(0, can_miss)
            if can_miss > 0:
                return f"+{can_miss} can miss"
            else:
                return "On border (0)"
        else:
            needed = int(((0.80 * self.total) - self.attended) / 0.20 + 0.999)
            return f"Need +{needed} att."


@dataclass
class SessionRecord:
    code: str
    course: str
    datetime_venue: str
    status: str  # 'Present' or 'Absent'

    @property
    def is_present(self) -> bool:
        return self.status.lower() == 'present'


@dataclass
class DutyLeaveRecord:
    title: str
    details: str
    status: str


@dataclass
class AttendanceReport:
    student_roll: str = ""
    student_name: str = ""
    student_semester: str = ""
    courses: List[CourseAttendance] = field(default_factory=list)
    recent_sessions: List[SessionRecord] = field(default_factory=list)
    duty_leaves: List[DutyLeaveRecord] = field(default_factory=list)

    @property
    def total_attended(self) -> int:
        return sum(c.attended for c in self.courses)

    @property
    def total_sessions(self) -> int:
        return sum(c.total for c in self.courses)

    @property
    def overall_pct(self) -> float:
        if self.total_sessions == 0:
            return 0.0
        return round((self.total_attended / self.total_sessions) * 100, 1)

    @property
    def total_absent(self) -> int:
        return self.total_sessions - self.total_attended


class AttendanceParser:
    @staticmethod
    def parse_html(html: str) -> AttendanceReport:
        soup = BeautifulSoup(html, 'html.parser')
        report = AttendanceReport()

        # 1. Student metadata
        try:
            sec_meta = soup.find('section', class_=lambda c: c and 'p-5' in c)
            if sec_meta:
                divs = sec_meta.find_all('div')
                if len(divs) >= 3:
                    report.student_roll = divs[0].get_text(strip=True)
                    report.student_name = divs[1].get_text(strip=True)
                    report.student_semester = divs[2].get_text(strip=True)
        except Exception:
            pass

        # 2. By Course section
        for sec in soup.find_all('section'):
            h2 = sec.find('h2')
            if not h2:
                continue
            h2_text = h2.get_text(strip=True).lower()

            if 'by course' in h2_text:
                for li in sec.find_all('li'):
                    name_div = li.find('div', class_=re.compile(r'truncate'))
                    full_title = name_div.get_text(strip=True) if name_div else ""
                    parts = [p.strip() for p in full_title.split('·', 1)]
                    code = parts[0] if len(parts) > 1 else ""
                    name = parts[1] if len(parts) > 1 else full_title

                    sessions_div = li.find('div', class_=re.compile(r'text-xs text-slate-500'))
                    sessions_str = sessions_div.get_text(strip=True) if sessions_div else ""
                    m = re.search(r'(\d+)\s+of\s+(\d+)\s+counted\s+sessions', sessions_str)
                    attended = int(m.group(1)) if m else 0
                    total = int(m.group(2)) if m else 0

                    pct_div = li.find('div', class_=re.compile(r'font-semibold.*text-emerald'))
                    pct_str = pct_div.get_text(strip=True) if pct_div else ""
                    if not pct_str:
                        for d in li.find_all(['div', 'span']):
                            if '%' in d.get_text():
                                pct_str = d.get_text(strip=True)
                                break
                    m_pct = re.search(r'([\d\.]+)%', pct_str)
                    pct = float(m_pct.group(1)) if m_pct else round((attended / total * 100) if total else 0.0, 1)

                    report.courses.append(CourseAttendance(
                        code=code,
                        name=name,
                        attended=attended,
                        total=total,
                        pct=pct,
                        sessions_str=sessions_str
                    ))

            elif 'recent sessions' in h2_text:
                for li in sec.find_all('li'):
                    c_div = li.find('div', class_=re.compile(r'truncate'))
                    c_text = c_div.get_text(strip=True) if c_div else ""
                    parts = [p.strip() for p in c_text.split('·', 1)]
                    code = parts[0] if len(parts) > 1 else ""
                    cname = parts[1] if len(parts) > 1 else c_text

                    time_div = li.find('div', class_=re.compile(r'text-xs text-slate-500'))
                    time_str = time_div.get_text(strip=True) if time_div else ""

                    status_span = li.find('span', class_=re.compile(r'rounded'))
                    status_str = status_span.get_text(strip=True) if status_span else "Unknown"

                    report.recent_sessions.append(SessionRecord(
                        code=code,
                        course=cname,
                        datetime_venue=time_str,
                        status=status_str
                    ))

            elif 'duty leaves' in h2_text:
                for li in sec.find_all('li'):
                    title_div = li.find('div', class_=re.compile(r'text-sm font-medium'))
                    t_text = title_div.get_text(strip=True) if title_div else ""
                    details_div = li.find('div', class_=re.compile(r'text-xs text-slate-500'))
                    d_text = details_div.get_text(strip=True) if details_div else ""
                    status_span = li.find('span', class_=re.compile(r'rounded'))
                    s_text = status_span.get_text(strip=True) if status_span else ""
                    report.duty_leaves.append(DutyLeaveRecord(
                        title=t_text,
                        details=d_text,
                        status=s_text
                    ))

        return report

    @classmethod
    def load_from_file(cls, filepath: str) -> AttendanceReport:
        if not os.path.exists(filepath):
            return AttendanceReport()
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            return cls.parse_html(f.read())
