from models.schemas import Difficulty, UserState

EFFORT_POINTS = {
    Difficulty.easy: 1,
    Difficulty.medium: 2,
    Difficulty.hard: 3,
    Difficulty.tough: 4,
}

BASE_CAPACITY = 10
NORMAL_CEILING = 12
ENERGETIC_CEILING = 12
FATIGUED_CEILING = 4
OVERWHELMED_CEILING = 0


def get_effort_points(difficulty: Difficulty) -> int:
    return EFFORT_POINTS.get(difficulty, 1)


def get_daily_ceiling(state: UserState) -> int:
    if state == UserState.overwhelmed:
        return OVERWHELMED_CEILING
    elif state == UserState.fatigued:
        return FATIGUED_CEILING
    elif state in (UserState.energetic, UserState.normal, UserState.constrained):
        return NORMAL_CEILING
    return NORMAL_CEILING


def apply_memory_adjustment(base_ceiling: int, last_feedback: str) -> int:
    if last_feedback and last_feedback.lower() in ("negative", "bad", "too much"):
        adjusted = int(base_ceiling * 0.8)
        return max(adjusted, 1)
    return base_ceiling


def get_capacity_percentage(points_used: int) -> int:
    return int((points_used / BASE_CAPACITY) * 100)
