from typing import List, Tuple
from models.schemas import Task, UserState, Priority, Difficulty

OVERLOAD_WORKLOAD_THRESHOLD = 14
OVERLOAD_HIGH_PRIORITY_THRESHOLD = 5

EFFORT_POINTS = {"easy": 1, "medium": 2, "hard": 3, "tough": 4}

FATIGUE_KEYWORDS = [
    "tired", "exhausted", "burnt out", "burnout", "drained",
    "overwhelmed", "stressed", "sick", "ill", "cannot focus",
    "can't focus", "no energy", "low energy"
]

TIME_PRESSURE_KEYWORDS = [
    "urgent", "asap", "deadline", "due today", "due tomorrow",
    "running out of time", "last minute", "critical"
]


def detect_qualitative_state(input_text: str, state_inputs) -> str:
    text_lower = input_text.lower()
    if state_inputs:
        if state_inputs.energy and state_inputs.energy.lower() == "low":
            return UserState.fatigued
        if state_inputs.mental and any(
            kw in state_inputs.mental.lower() for kw in FATIGUE_KEYWORDS
        ):
            return UserState.overwhelmed

    for kw in FATIGUE_KEYWORDS:
        if kw in text_lower:
            return UserState.fatigued

    for kw in TIME_PRESSURE_KEYWORDS:
        if kw in text_lower:
            return UserState.constrained

    return None


def detect_quantitative_state(history: list) -> str:
    if len(history) < 3:
        return None

    capacities = [r.get("daily_capacity_utilized", 0) for r in history[:3]]

    if all(c >= 90 for c in capacities):
        return UserState.overwhelmed

    today_capacity = capacities[0] if capacities else 0
    if today_capacity >= 65 and all(c > 90 for c in capacities[1:3]):
        return UserState.fatigued

    avg = sum(capacities) / len(capacities)
    if avg < 70:
        return UserState.energetic

    return UserState.normal


def detect_state(
    input_text: str,
    state_inputs,
    history: list,
    tasks: List[Task]
) -> UserState:
    qualitative = detect_qualitative_state(input_text, state_inputs)
    if qualitative:
        return qualitative

    workload_score = sum(
        EFFORT_POINTS.get(t.difficulty.value, 1)
        for t in tasks
        if t.effective_priority in (Priority.high, Priority.medium)
    )
    high_count = sum(1 for t in tasks if t.effective_priority == Priority.high)

    if workload_score >= OVERLOAD_WORKLOAD_THRESHOLD or high_count >= OVERLOAD_HIGH_PRIORITY_THRESHOLD:
        return UserState.overwhelmed

    quantitative = detect_quantitative_state(history)
    if quantitative:
        return quantitative

    return UserState.normal


def prune_tasks(tasks: List[Task], state: UserState) -> Tuple[List[Task], List[Task], List[str]]:
    kept = []
    pruned = []
    adjustments = []

    if state == UserState.overwhelmed:
        for task in tasks:
            if task.effective_priority == Priority.high:
                kept.append(task)
            else:
                task.unscheduled_reason = "pruned_by_state"
                pruned.append(task)
        adjustments.append("Overwhelmed state: kept only high-priority tasks.")

    elif state == UserState.fatigued:
        for task in tasks:
            if task.effective_priority == Priority.high:
                kept.append(task)
            elif task.effective_priority == Priority.medium and task.deadline:
                kept.append(task)
            else:
                task.unscheduled_reason = "pruned_by_state"
                pruned.append(task)
        adjustments.append("Fatigued state: removed low-priority and no-deadline medium tasks.")

    elif state == UserState.constrained:
        for task in tasks:
            if task.deadline:
                kept.append(task)
            elif task.effective_priority in (Priority.high, Priority.medium):
                kept.append(task)
            else:
                task.unscheduled_reason = "pruned_by_state"
                pruned.append(task)
        adjustments.append("Constrained state: kept deadline-driven work and still allowed medium/high-priority tasks without deadlines.")

    else:
        kept = tasks

    return kept, pruned, adjustments
