import hashlib
import json
import os
from typing import List, Tuple
from google import genai
from google.genai import types
from models.schemas import Task, Priority, Difficulty
from dotenv import load_dotenv

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")


def build_extraction_prompt(input_text: str, today: str) -> str:
    return f"""You are a task extraction agent. Extract all tasks from the user input.

For each task return ONLY a JSON array:
[
  {{
    "title": "task description",
    "declared_priority": "high|medium|low or null if not mentioned",
    "difficulty": "easy|medium|hard|tough or null if not mentioned",
    "deadline": "YYYY-MM-DD or null if not mentioned",
    "needs_clarification": true if priority or difficulty is missing,
    "work_style": "single or progressive",
    "spread_days": <integer or null>
  }}
]

Rules:
- Extract ONLY what the user explicitly states
- If the user says "urgent" or "asap" infer priority as high
- If the user says "quick" or "simple" infer difficulty as easy
- If the user says "tomorrow" convert to actual date based on today
- If the user says "next week" set deadline to 7 days from today
- If user specifies spreading the task (e.g. 'spread:3'), set work_style to 'progressive' and spread_days to that number.
- For everything else that is missing set to null
- Return ONLY the JSON array

Today's date: {today}
User input: {input_text}"""


def build_clarification_prompt(tasks_needing_clarification: list) -> str:
    task_names = [t["title"] for t in tasks_needing_clarification]
    missing = []
    for t in tasks_needing_clarification:
        fields = []
        if not t.get("declared_priority"):
            fields.append("priority (high/medium/low)")
        if not t.get("difficulty"):
            fields.append("difficulty (easy/medium/hard/tough)")
        missing.append(f"'{t['title']}': needs {' and '.join(fields)}")

    return f"""You are a friendly productivity assistant. The user added some tasks but didn't specify all required details.

Tasks needing info: {json.dumps(missing)}

Write a SHORT, warm, conversational message asking for the missing details.
- Be friendly and natural, not robotic
- Mention the specific tasks by name
- Keep it under 40 words
- Don't use bullet points or lists
- End with a question

Return only the message text."""


def generate_task_id(title: str, index: int) -> str:
    raw = f"{title.lower().strip()}-{index}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def parse_tasks_from_text(input_text: str) -> list:
    import re
    from datetime import date, timedelta
    tasks = []
    priority_map = {"high": "high", "medium": "medium", "low": "low",
                    "urgent": "high", "asap": "high", "critical": "high"}
    difficulty_map = {"easy": "easy", "medium": "medium", "hard": "hard", "tough": "tough",
                      "simple": "easy", "quick": "easy", "complex": "hard"}

    parts = re.split(r",\s*(?=[^)]*(?:\(|$))", input_text)
    parts = [p.strip() for p in parts if p.strip()]
    today = date.today()

    for part in parts:
        match = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", part)
        if match:
            title = match.group(1).strip()
            attrs_raw = match.group(2)
            attrs = [a.strip().lower() for a in attrs_raw.split(",")]
            priority = next((priority_map[a] for a in attrs if a in priority_map), None)
            difficulty = next((difficulty_map[a] for a in attrs if a in difficulty_map), None)
            deadline = None
            for a in attrs:
                if "tomorrow" in a:
                    deadline = (today + timedelta(days=1)).isoformat()
                elif "today" in a:
                    deadline = today.isoformat()
                else:
                    due_match = re.search(r"due\s+(\d{4}-\d{2}-\d{2})", a)
                    if due_match:
                        deadline = due_match.group(1)
            
            spread_days = None
            work_style = "single"
            for a in attrs:
                spread_match = re.search(r"spread:(\d+)", a)
                if spread_match:
                    spread_days = int(spread_match.group(1))
                    work_style = "progressive"

            if priority and difficulty:
                tasks.append({"title": title, "declared_priority": priority,
                              "difficulty": difficulty, "deadline": deadline,
                              "needs_clarification": False,
                              "work_style": work_style,
                              "spread_days": spread_days})
            elif title:
                tasks.append({"title": title, "declared_priority": None,
                              "difficulty": None, "deadline": deadline,
                              "needs_clarification": True,
                              "work_style": "single", "spread_days": None})
        elif len(part) > 2:
            tasks.append({"title": part, "declared_priority": None,
                          "difficulty": None, "deadline": None,
                          "needs_clarification": True})
    return tasks


def extract_tasks(input_text: str, today: str) -> Tuple[List[Task], List[dict], str]:
    prompt = build_extraction_prompt(input_text, today)

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json"
            )
        )
        raw = response.text.strip().replace("```json", "").replace("```", "").strip()
        extracted = json.loads(raw)
    except Exception as e:
        print(f"Task extraction error: {e}")
        extracted = parse_tasks_from_text(input_text)
        if not extracted:
            return [], [], "I'm having trouble connecting right now. Please try again in a moment."

    tasks = []
    needs_clarification = []

    for i, item in enumerate(extracted):
        title = item.get("title", "").strip()
        if not title:
            continue

        declared_priority = item.get("declared_priority")
        difficulty = item.get("difficulty")
        deadline = item.get("deadline")

        if not declared_priority or not difficulty:
            needs_clarification.append(item)
            continue

        try:
            task = Task(
                task_id=generate_task_id(title, i),
                title=title,
                declared_priority=Priority(declared_priority),
                effective_priority=Priority(declared_priority),
                difficulty=Difficulty(difficulty),
                deadline=deadline if deadline and deadline != "null" else None,
                is_protected=False,
                effort_points=0,
                work_style=item.get("work_style", "single"),
                spread_days=item.get("spread_days")
            )
            tasks.append(task)
        except Exception as e:
            needs_clarification.append(item)

    clarification_message = ""
    if needs_clarification:
        try:
            clari_prompt = build_clarification_prompt(needs_clarification)
            clari_response = client.models.generate_content(
                model=MODEL,
                contents=clari_prompt,
                config=types.GenerateContentConfig(temperature=0.7)
            )
            clarification_message = clari_response.text.strip()
        except Exception:
            clarification_message = f"Could you tell me the priority and difficulty for: {', '.join([t['title'] for t in needs_clarification])}?"

    return tasks, needs_clarification, clarification_message
