import os
import unittest
from lms_client import LMSClient, sanitize_filename

class TestLMSClientOffline(unittest.TestCase):
    def setUp(self):
        self.client = LMSClient("https://lmsug24.iiitkottayam.ac.in")

    def test_sanitize_filename(self):
        self.assertEqual(sanitize_filename("Unit 1: Introduction? *Yes*"), "Unit 1_ Introduction_ _Yes_")
        self.assertEqual(sanitize_filename("Course/PPTs\\Folder"), "Course_PPTs_Folder")
        self.assertEqual(sanitize_filename("file.pdf..."), "file.pdf")

    def test_extract_course_year(self):
        from lms_client import extract_course_year
        self.assertEqual(extract_course_year("IMA221  Differential Equations"), 2)
        self.assertEqual(extract_course_year("CSS311 Parallel and Distributed Computing"), 3)
        self.assertEqual(extract_course_year("ICS 112 COMPUTER PROGRAMMING"), 1)
        self.assertEqual(extract_course_year("ICS211Design and Analysis of  Algorithms"), 2)
        self.assertEqual(extract_course_year("IEG313 Quantum Computing for Engineers"), 3)
        self.assertIsNone(extract_course_year("Club Election 2025"))

    def test_parse_courses(self):
        with open("courses.html", "r", encoding="utf-8") as f:
            html = f.read()
        courses = self.client.parse_courses_from_html(html)
        self.assertTrue(len(courses) >= 8)
        
        c_map = {c.id: c.name for c in courses}
        self.assertIn("141", c_map)
        self.assertIn("143", c_map)
        self.assertIn("142", c_map)
        self.assertEqual(c_map["141"], "CSS311 Parallel and Distributed Computing")
        self.assertEqual(c_map["143"], "CSE311 Artificial Intelligence")
        print(f"\n[Test] Successfully parsed {len(courses)} courses:")
        for cid, name in c_map.items():
            print(f"  [{cid}] {name}")

    def test_parse_course_sections(self):
        with open("a_course.html", "r", encoding="utf-8") as f:
            html = f.read()
        sections = self.client.parse_course_page_from_html(html, "141")
        self.assertTrue(len(sections) >= 4)
        
        materials_sec = next((s for s in sections if "materials" in s.title.lower()), None)
        self.assertIsNotNone(materials_sec)
        
        # Check folder activities in Materials section
        folders = [a for a in materials_sec.activities if a.type == "folder"]
        self.assertTrue(len(folders) >= 2)
        folder_names = [f.name for f in folders]
        self.assertIn("Course-PPTs", folder_names)
        self.assertIn("E-Books", folder_names)
        print(f"\n[Test] Found {len(sections)} sections in course 141:")
        for s in sections:
            print(f"  Section: {s.title} ({len(s.activities)} activities)")
            for a in s.activities:
                print(f"    - [{a.type.upper()}] {a.name} (id={a.id})")

    def test_parse_folder_files(self):
        with open("materials_folder.html", "r", encoding="utf-8") as f:
            html = f.read()
        files = self.client.parse_folder_files_from_html(html)
        self.assertEqual(len(files), 32)
        print(f"\n[Test] Successfully parsed {len(files)} files in folder:")
        for f in files[:5]:
            print(f"  - {f.name} (forcedownload URL: {f.url[:70]}...)")
        self.assertTrue(any(f.name.endswith(".pptx") for f in files))
        self.assertTrue(any(f.name.endswith(".pdf") for f in files))

if __name__ == "__main__":
    unittest.main()
