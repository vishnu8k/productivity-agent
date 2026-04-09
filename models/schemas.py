from pydantic import BaseModel, Field
from typing import Optional, List, Any
from enum import Enum
from datetime import date


class Priority(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class Difficulty(str, Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"
    tough = "tough"


class UserState(str, Enum):
    normal = "normal"
    energetic = "energetic"
    fatigued = "fatigued"
    overwhelmed = "overwhelmed"
    constrained = "constrained"


class Task(BaseModel):
    task_id: str
    title: str
    declared_priority: Priority
    effective_priority: Priority
    difficulty: Difficulty
    deadline: Optional[str] = None
    is_protected: bool = False
    effort_points: int = 0
    scheduled_day: Optional[int] = None
    unscheduled_reason: Optional[str] = None
    domain_added: bool = False
    work_style: str = "single"
    spread_days: Optional[int] = None
    session_number: Optional[int] = None
    total_sessions: Optional[int] = None


class StateInputs(BaseModel):
    energy: Optional[str] = None
    mental: Optional[str] = None


class PlanRequest(BaseModel):
    user_id: Optional[str] = None
    input_text: str
    state_inputs: Optional[StateInputs] = None
    confirm_actions: Optional[bool] = False
    current_plan: Optional[List[Any]] = None
    current_unscheduled: Optional[List[Any]] = None
    current_summary: Optional[str] = None


class ScheduledDay(BaseModel):
    day: int
    date: Optional[str] = None
    tasks: List[Task] = Field(default_factory=list)
    total_effort_points: int = 0
    capacity_percentage: int = 0


class ActionProposed(BaseModel):
    action_type: str
    tool: str
    details: dict
    status: str = "pending"


class ToolResult(BaseModel):
    tool: str
    status: str
    message: str


class PlanResponse(BaseModel):
    user_id: str
    response_text: str
    detected_state: UserState
    plan: List[ScheduledDay] = Field(default_factory=list)
    unscheduled_tasks: List[Task] = Field(default_factory=list)
    adjustments_applied: List[str] = Field(default_factory=list)
    actions_proposed: List[ActionProposed] = Field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: Optional[str] = None
    daily_summary: Optional[str] = None
    tool_results: List[ToolResult] = Field(default_factory=list)


class MemoryRecord(BaseModel):
    user_id: str
    date: str
    daily_capacity_utilized: int
    qualitative_state: str
    pending_backlog: Optional[str] = "[]"
    recovery_day_index: int = 0
    daily_summary: Optional[str] = None
    feedback: Optional[str] = None


class ClarificationResponse(BaseModel):
    user_id: str
    status: str = "needs_clarification"
    question: str

class DirectScheduleRequest(BaseModel):
    task_id: str
    target_day: int
    current_plan: list
    current_unscheduled: list


class AuthenticatedUser(BaseModel):
    sub: str
    email: str
    name: Optional[str] = None
    picture: Optional[str] = None
    email_verified: bool = False

