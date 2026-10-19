import os
import re
import json
import random
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any

POSITIVE_KEYWORDS = [
    (r'\b(strongly agree|excellent|outstanding|always|definitely|5|high)\b', 50),
    (r'\b(agree|very good|good|frequently|often|yes|4)\b', 40),
    (r'\b(neutral|moderate|average|satisfactory|sometimes|3)\b', 30),
    (r'\b(disagree|poor|fair|rarely|2)\b', 20),
    (r'\b(strongly disagree|very poor|never|no|1|0)\b', 10),
]

@dataclass
class FeedbackOption:
    value: str
    label: str

@dataclass
class FeedbackQuestion:
    name: str
    title: str
    type: str  # 'radio', 'select', 'textarea', 'text'
    options: List[FeedbackOption] = field(default_factory=list)
    required: bool = False

@dataclass
class FeedbackFormSchema:
    action: str
    method: str
    hidden_inputs: Dict[str, str]
    questions: List[FeedbackQuestion]


class FeedbackParser:
    """
    Parses Moodle feedback questionnaires from HTML.
    Supports complete.php structure and extracts questions, types, and options.
    """
    @staticmethod
    def parse_html(html: str) -> Tuple[Optional[FeedbackFormSchema], str]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')

        # Check if already completed
        text_lower = soup.get_text(' ', strip=True).lower()
        if "already completed" in text_lower or "already submitted" in text_lower:
            return None, "ALREADY_COMPLETED"

        form = soup.find('form', id=re.compile(r'feedback_complete_form|feedback_form'))
        if not form:
            form = soup.find('form', action=re.compile(r'/mod/feedback/complete\.php'))

        if not form:
            # Check for link to complete.php
            comp_link = soup.find('a', href=re.compile(r'/mod/feedback/complete\.php'))
            if comp_link:
                return None, f"NEED_NAVIGATE:{comp_link['href']}"
            return None, "NO_FORM_FOUND"

        action = form.get('action', '')
        method = form.get('method', 'POST').upper()

        # Hidden inputs
        hidden_inputs: Dict[str, str] = {}
        for inp in form.find_all('input', type='hidden'):
            name = inp.get('name')
            if name:
                hidden_inputs[name] = inp.get('value', '')

        # Parse questions
        questions: List[FeedbackQuestion] = []
        seen_names = set()

        groups = form.find_all('div', class_=re.compile(r'form-group|feedback_itemlist|fitem'))
        for g in groups:
            # Question label/prompt
            lbl = g.find(['legend', 'label', 'p', 'span'], class_=re.compile(r'col-form-label|word-break|felement'))
            title = ""
            if lbl:
                title = lbl.get_text(' ', strip=True)
                title = re.sub(r'Required field', '', title, flags=re.IGNORECASE).strip()

            is_required = bool(g.find(class_=re.compile(r'text-danger|req|required')))

            radios = g.find_all('input', type='radio')
            textareas = g.find_all('textarea')
            selects = g.find_all('select')

            if radios:
                name = radios[0].get('name')
                if not name or name in seen_names:
                    continue
                seen_names.add(name)

                options: List[FeedbackOption] = []
                for r in radios:
                    val = r.get('value', '')
                    # Label might be parent or via for=id
                    parent_label = r.find_parent('label')
                    opt_lbl = ""
                    if parent_label:
                        opt_lbl = parent_label.get_text(' ', strip=True)
                    else:
                        r_id = r.get('id')
                        if r_id:
                            for_l = g.find('label', attrs={'for': r_id})
                            if for_l:
                                opt_lbl = for_l.get_text(' ', strip=True)
                    options.append(FeedbackOption(value=val, label=opt_lbl.strip()))

                questions.append(FeedbackQuestion(
                    name=name,
                    title=title,
                    type='radio',
                    options=options,
                    required=is_required
                ))

            elif textareas:
                for t in textareas:
                    name = t.get('name')
                    if not name or name in seen_names:
                        continue
                    seen_names.add(name)
                    if not title:
                        lbl_t = g.find('label')
                        if lbl_t:
                            title = lbl_t.get_text(' ', strip=True)
                    questions.append(FeedbackQuestion(
                        name=name,
                        title=title,
                        type='textarea',
                        required=is_required
                    ))

            elif selects:
                for s in selects:
                    name = s.get('name')
                    if not name or name in seen_names or name == 'jump':
                        continue
                    seen_names.add(name)
                    options = [
                        FeedbackOption(value=o.get('value', ''), label=o.get_text(strip=True))
                        for o in s.find_all('option')
                    ]
                    questions.append(FeedbackQuestion(
                        name=name,
                        title=title,
                        type='select',
                        options=options,
                        required=is_required
                    ))

        return FeedbackFormSchema(
            action=action,
            method=method,
            hidden_inputs=hidden_inputs,
            questions=questions
        ), "OK"


class FeedbackAnswerGenerator:
    """
    Solves feedback questions with positive responses.
    Supports:
    1. Gemini AI (using google.genai or REST API) for authentic, varied JSON responses.
    2. Zero-token / ultra-lightweight heuristic engine for instant 100% positive ratings.
    """

    @staticmethod
    def score_option(label: str) -> float:
        text = label.strip().lower()
        # Direct number match (e.g. '5', '4', '10')
        m = re.match(r'^(\d+(?:\.\d+)?)$', text)
        if m:
            try:
                return float(m.group(1)) * 10
            except ValueError:
                pass

        for pattern, weight in POSITIVE_KEYWORDS:
            if re.search(pattern, text, re.IGNORECASE):
                return float(weight)

        return 0.0

    @classmethod
    def select_best_option(cls, options: List[FeedbackOption]) -> str:
        if not options:
            return ""
        scored = []
        for opt in options:
            if opt.value == '0' and not opt.label:
                continue
            s = cls.score_option(opt.label)
            scored.append((s, opt.value))

        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            return scored[0][1]

        return options[0].value

    @staticmethod
    def generate_positive_text_heuristic(title: str, course_name: str = "") -> str:
        title_lower = title.lower()
        course_clean = re.sub(r'^[A-Za-z0-9]+\s*', '', course_name).strip() if course_name else "this course"

        if any(k in title_lower for k in ['strength', 'good', 'best', 'appreciate', 'positive']):
            choices = [
                f"The lectures were clearly structured and core concepts were explained thoroughly with practical examples.",
                f"Great depth of coverage and excellent enthusiasm from the instructor. Interactive discussions made complex topics easy to understand.",
                f"Well-organized course curriculum, timely evaluations, and practical problem-solving sessions throughout.",
                f"Clear presentation of concepts, engaging teaching style, and relevant real-world applications discussed during classes."
            ]
            return random.choice(choices)

        elif any(k in title_lower for k in ['weakness', 'lack', 'drawback', 'negative', 'difficulty', 'disadvantage']):
            return "None"

        elif any(k in title_lower for k in ['suggest', 'improve', 'delivery', 'content', 'recommend']):
            if 'delivery' in title_lower:
                choices = [
                    "The current delivery style and interactive pace are very effective. Continuing this approach is recommended.",
                    "Delivery was clear and engaging throughout. Keep up the active discussion of problems.",
                    "The teaching methodology is great as it is; maintaining the same engaging environment will be wonderful."
                ]
            else:
                choices = [
                    "The course content is comprehensive and up-to-date. Adding more industry case studies and hands-on projects would be a great bonus.",
                    "The syllabus is well-rounded and detailed. More practical demonstrations would further enrich the experience.",
                    "Current content is very well-structured and covers both fundamentals and advanced topics nicely."
                ]
            return random.choice(choices)

        else:
            choices = [
                f"Overall an excellent and rewarding learning experience in {course_clean}.",
                f"Very informative and well-conducted course throughout the semester.",
                f"Great course with valuable insights and practical learning outcomes."
            ]
            return random.choice(choices)

    @classmethod
    def solve_with_gemini(cls, questions: List[FeedbackQuestion], course_name: str, api_key: str) -> Optional[Dict[str, str]]:
        """
        Sends form questions to Gemini to get a positive JSON response.
        """
        if not api_key:
            return None

        prompt_payload = {
            "course": course_name,
            "goal": "Provide positive, appreciative, and constructive answers to this university course evaluation.",
            "rules": [
                "For multiple-choice questions (radio/select), select the value corresponding to the most positive score (e.g. 5/5, strongly agree, excellent).",
                "For textareas, write authentic, polite, and constructive positive responses:",
                "  - For strengths: highlight clarity, structured pace, practical examples, and engaging teaching.",
                "  - For weaknesses, drawbacks, or difficulties: write exactly 'None' (because indicating no weaknesses is the most positive answer).",
                "  - For suggestions: write positive constructive recommendations like 'Keep up the interactive discussions' or 'Add more real-world case studies'.",
                "Return ONLY a JSON dictionary where keys are the question 'name' and values are the answer string."
            ],
            "questions": [
                {
                    "name": q.name,
                    "title": q.title,
                    "type": q.type,
                    "options": [{"value": o.value, "label": o.label} for o in q.options]
                }
                for q in questions
            ]
        }

        # 1. Try google.genai package
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model='gemini-3.5-flash-lite',
                contents=json.dumps(prompt_payload),
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            data = json.loads(resp.text)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass

        # 2. Direct REST fallback to generativelanguage.googleapis.com
        try:
            import requests
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={api_key}"
            req_body = {
                "contents": [{
                    "parts": [{
                        "text": (
                            "You are filling out a university course feedback form. Give the most positive, appreciative answers.\n"
                            "Return valid JSON ONLY mapping question name to answer value/text:\n" +
                            json.dumps(prompt_payload)
                        )
                    }]
                }],
                "generationConfig": {
                    "response_mime_type": "application/json"
                }
            }
            res = requests.post(url, json=req_body, timeout=15)
            if res.status_code == 200:
                out = res.json()
                text = out['candidates'][0]['content']['parts'][0]['text']
                data = json.loads(text)
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass

        return None

    @classmethod
    def generate_answers(
        cls,
        schema: FeedbackFormSchema,
        course_name: str = "",
        api_key: str = "",
        prefer_gemini: bool = True
    ) -> Tuple[Dict[str, str], str]:
        """
        Generate answers for all questions in the schema.
        Returns: (answers_dict, method_used)
        """
        answers: Dict[str, str] = {}
        method_used = "HEURISTIC"

        if prefer_gemini and api_key:
            gemini_result = cls.solve_with_gemini(schema.questions, course_name, api_key)
            if gemini_result:
                answers = gemini_result
                method_used = "GEMINI_AI"

        # Fill in any missing or unanswered questions with smart heuristic
        for q in schema.questions:
            if q.name not in answers or not answers[q.name]:
                if q.type in ('radio', 'select'):
                    answers[q.name] = cls.select_best_option(q.options)
                elif q.type in ('textarea', 'text'):
                    answers[q.name] = cls.generate_positive_text_heuristic(q.title, course_name)

        # Ensure weakness/drawback questions strictly receive "None"
        for q in schema.questions:
            if q.type in ('textarea', 'text'):
                t_lower = q.title.lower()
                if any(k in t_lower for k in ['weakness', 'lack', 'drawback', 'negative', 'difficulty', 'disadvantage']):
                    answers[q.name] = "None"

        return answers, method_used

    @classmethod
    def build_submission_payload(
        cls,
        schema: FeedbackFormSchema,
        answers: Dict[str, str]
    ) -> Dict[str, str]:
        """
        Merges hidden inputs, question answers, and submission action button into POST payload.
        """
        payload: Dict[str, str] = {}
        # 1. Base hidden inputs
        payload.update(schema.hidden_inputs)

        # 2. Answers
        payload.update(answers)

        # 3. Submit trigger
        payload['savevalues'] = 'Submit your answers'
        return payload
