from datetime import date
from typing import List
from models.schemas import Task, Priority


def compute_effective_priority(task: Task) -> Priority:
    if task.deadline:
        try:
            deadline_date = date.fromisoformat(task.deadline)
            days_until = (deadline_date - date.today()).days
            if days_until <= 2:
                return Priority.high
        except ValueError:
            pass
    return task.declared_priority


def apply_effective_priorities(tasks: List[Task]) -> List[Task]:
    for task in tasks:
        task.effective_priority = compute_effective_priority(task)
    return tasks


def order_tasks(tasks: List[Task]) -> List[Task]:
    priority_order = {Priority.high: 0, Priority.medium: 1, Priority.low: 2}
    difficulty_order = {"tough": 0, "hard": 1, "medium": 2, "easy": 3}

    def sort_key(task: Task):
        p = priority_order.get(task.effective_priority, 2)
        deadline_val = task.deadline or "9999-12-31"
        d = difficulty_order.get(task.difficulty.value, 3)
        return (p, deadline_val, d)

    return sorted(tasks, key=sort_key)
