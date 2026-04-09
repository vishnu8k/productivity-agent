from typing import List
from models.schemas import Task


async def create_tasks(tasks: List[Task]) -> str:
    task_titles = [t.title for t in tasks]
    return f"[MOCK] Task Manager: {len(task_titles)} tasks created: {', '.join(task_titles)}"


async def update_task_status(task_id: str, status: str) -> str:
    return f"[MOCK] Task Manager: Task {task_id} updated to {status}"


async def get_productivity_score(user_id: str, capacity_used: int) -> dict:
    if capacity_used >= 90:
        score = "A"
        label = "Exceptional"
    elif capacity_used >= 70:
        score = "B"
        label = "Productive"
    elif capacity_used >= 50:
        score = "C"
        label = "Moderate"
    else:
        score = "D"
        label = "Light day"

    return {
        "user_id": user_id,
        "capacity_used": capacity_used,
        "score": score,
        "label": label,
        "mocked": True
    }
