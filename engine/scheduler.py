from typing import List, Tuple
from datetime import date, timedelta
from models.schemas import Task, ScheduledDay, UserState
from engine.capacity import get_daily_ceiling, get_effort_points, apply_memory_adjustment
import math

MAX_DAYS = 30
MAX_TOTAL_TASKS = 60


def schedule_tasks(
    tasks: List[Task],
    state: UserState,
    last_feedback: str = None
) -> Tuple[List[ScheduledDay], List[Task]]:

    ceiling = get_daily_ceiling(state)
    ceiling = apply_memory_adjustment(ceiling, last_feedback)

    today = date.today()
    days = []
    for i in range(MAX_DAYS):
        days.append(ScheduledDay(
            day=i + 1,
            date=(today + timedelta(days=i)).isoformat(),
            tasks=[],
            total_effort_points=0,
            capacity_percentage=0
        ))

    unscheduled = []
    total_scheduled = 0

    for task in tasks:
        if total_scheduled >= MAX_TOTAL_TASKS:
            task.unscheduled_reason = "max_plan_tasks_reached"
            unscheduled.append(task)
            continue

        points = get_effort_points(task.difficulty)
        placed = False

        for day in days:
            if day.total_effort_points + points <= ceiling:
                task.effort_points = points
                task.scheduled_day = day.day
                day.tasks.append(task)
                day.total_effort_points += points
                day.capacity_percentage = int(
                    (day.total_effort_points / 10) * 100
                )
                total_scheduled += 1
                placed = True
                break

        if not placed:
            task.unscheduled_reason = "capacity_constraints"
            unscheduled.append(task)

    scheduled_days = [d for d in days if d.tasks]
    return scheduled_days, unscheduled
