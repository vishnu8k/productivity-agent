import hashlib
from typing import List, Tuple
from models.schemas import Task, Priority, Difficulty

TRAVEL_KEYWORDS = [
    "trip", "travel", "flight", "fly", "airport", "hotel",
    "vacation", "journey", "pack", "passport", "visa", "cab", "taxi"
]

NEWBORN_KEYWORDS = [
    "newborn", "baby", "infant", "feeding", "diaper", "nappy",
    "pediatric", "nurse", "formula", "crib", "cradle", "new born"
]


def is_travel_context(input_text: str) -> bool:
    text_lower = input_text.lower()
    return any(kw in text_lower for kw in TRAVEL_KEYWORDS)


def is_newborn_context(input_text: str) -> bool:
    text_lower = input_text.lower()
    return any(kw in text_lower for kw in NEWBORN_KEYWORDS)


def make_task_id(title: str) -> str:
    return hashlib.md5(title.lower().encode()).hexdigest()[:12]


def run_travel_agent(input_text: str, existing_tasks: List[Task]) -> Tuple[List[Task], List[str]]:
    prep_tasks = [
        ("Check and pack travel documents", Priority.high, Difficulty.easy),
        ("Pack luggage and essentials", Priority.high, Difficulty.easy),
        ("Confirm transport to airport/station", Priority.high, Difficulty.easy),
        ("Set travel day buffer time", Priority.medium, Difficulty.easy),
    ]

    existing_titles = {t.title.lower() for t in existing_tasks}
    new_tasks = []

    for title, priority, difficulty in prep_tasks:
        if title.lower() not in existing_titles:
            task = Task(
                task_id=make_task_id(title),
                title=title,
                declared_priority=priority,
                effective_priority=priority,
                difficulty=difficulty,
                deadline=None,
                is_protected=False,
                effort_points=0,
                domain_added=True,
            )
            new_tasks.append(task)

    adjustments = []
    if new_tasks:
        adjustments.append(
            f"Travel Agent: added {len(new_tasks)} travel preparation tasks."
        )

    return new_tasks, adjustments


def run_newborn_agent(input_text: str, existing_tasks: List[Task]) -> Tuple[List[Task], List[str]]:
    care_tasks = [
        ("Schedule newborn feeding slots", Priority.high, Difficulty.easy),
        ("Prepare diaper and care supplies", Priority.medium, Difficulty.easy),
        ("Set pediatric check reminder", Priority.medium, Difficulty.easy),
        ("Plan rest windows around baby schedule", Priority.low, Difficulty.easy),
    ]

    existing_titles = {t.title.lower() for t in existing_tasks}
    new_tasks = []

    for title, priority, difficulty in care_tasks:
        if title.lower() not in existing_titles:
            task = Task(
                task_id=make_task_id(title),
                title=title,
                declared_priority=priority,
                effective_priority=priority,
                difficulty=difficulty,
                deadline=None,
                is_protected=False,
                effort_points=0,
                domain_added=True,
            )
            new_tasks.append(task)

    adjustments = []
    if new_tasks:
        adjustments.append(
            f"Newborn Agent: added {len(new_tasks)} newborn care tasks."
        )

    return new_tasks, adjustments


def run_domain_agents(input_text: str, tasks: List[Task]) -> Tuple[List[Task], List[str]]:
    all_new_tasks = []
    all_adjustments = []

    if is_travel_context(input_text):
        new_tasks, adjustments = run_travel_agent(input_text, tasks)
        all_new_tasks.extend(new_tasks)
        all_adjustments.extend(adjustments)

    if is_newborn_context(input_text):
        new_tasks, adjustments = run_newborn_agent(input_text, tasks)
        all_new_tasks.extend(new_tasks)
        all_adjustments.extend(adjustments)

    return all_new_tasks, all_adjustments
