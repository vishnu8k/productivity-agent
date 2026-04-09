import os
import json
import re
import uuid
from datetime import date, datetime, timedelta
from typing import List, Optional
from google import genai
from google.genai import types
from dotenv import load_dotenv

from models.schemas import (
    Task, PlanRequest, PlanResponse, UserState,
    ScheduledDay, ActionProposed, Priority, Difficulty
)
from agents.task_agent import extract_tasks, generate_task_id
from agents.state_agent import run_state_agent, needs_cold_start, parse_cold_start_response
from agents.domain_agents import run_domain_agents
from engine.priority import apply_effective_priorities, order_tasks
from engine.state_rules import prune_tasks
from engine.scheduler import schedule_tasks
from engine.capacity import get_effort_points
from memory.bigquery_client import (
    get_user_history, get_latest_record, get_pending_backlog,
    write_daily_record, backfill_history, count_user_history_days
)

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

RESPONSE_PROMPT = """You are a warm, empathetic AI productivity assistant.
Generate a SHORT conversational response based on this plan.

User state: {state}
Tasks scheduled: {scheduled_count} across {days_count} days
Unscheduled tasks: {unscheduled_count}
Adjustments: {adjustments}
User input: {input_text}

Tone rules:
- normal: friendly, practical

Rules:
- Under 80 words
- Do NOT list tasks (UI shows them)
- Acknowledge what was done and why
- Sound human, not like a system message

Return only the message."""

MODIFICATION_PROMPT = """You are a task scheduling assistant. The user wants to modify their plan.

Current plan: {current_plan}
User request: {user_request}
Today: {today}

Determine the modifications needed and return a JSON object containing an array called "modifications":
{{
  "modifications": [
    {{
      "action": "move|reschedule|remove",
      "task_title_hint": "partial task name from user's message",
      "target_day": <day number or null>,
      "target_date": "YYYY-MM-DD or null",
      "response": "friendly confirmation message to user"
    }}
  ]
}}
"""

SUMMARY_PROMPT = """Generate a concise daily summary for this productivity plan.
User: {user_id}, Date: {today}, State: {state}
Scheduled tasks: {tasks}, Capacity used: {capacity}%
Write 2-3 sentences. Be specific. Return only the summary."""


def is_modification_request(text: str) -> bool:
    keywords = [
        "move", "reschedule", "shift", "change", "update", "modify",
        "remove", "delete", "cancel", "postpone", "push to", "bump to",
        "day 1", "day 2", "day 3", "day 4", "day 5", "day 6", "day 7",
        "tomorrow", "next day", "later", "priority", "make it", "instead of",
        "put ", "place "
    ]
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)


def deserialize_plan(plan_data: list) -> List[ScheduledDay]:
    days = []
    for d in plan_data:
        tasks = []
        for t in d.get("tasks", []):
            try: tasks.append(Task(**t))
            except Exception: continue
        days.append(ScheduledDay(
            day=d["day"], date=d.get("date"), tasks=tasks,
            total_effort_points=d.get("total_effort_points", 0),
            capacity_percentage=d.get("capacity_percentage", 0)
        ))
    return days


def serialize_plan(plan: List[ScheduledDay]) -> list:
    return [{
        "day": d.day, "date": d.date, "tasks": [t.dict() for t in d.tasks],
        "total_effort_points": d.total_effort_points,
        "capacity_percentage": d.capacity_percentage
    } for d in plan]


def apply_modifications(current_plan: List[ScheduledDay], unscheduled: List[Task], mods: list) -> tuple:
    if not isinstance(mods, list):
        mods = [mods]

    responses = []
    today_dt = date.today()

    for mod in mods:
        if not isinstance(mod, dict): continue
        action = mod.get("action", "")
        title_hint = mod.get("task_title_hint", "").lower()
        target_day = mod.get("target_day")
        target_date_str = mod.get("target_date")
        
        # Bulletproof date check
        if target_date_str and isinstance(target_date_str, str) and len(target_date_str) == 10 and "-" in target_date_str:
            try:
                t_date = date.fromisoformat(target_date_str)
                target_day = (t_date - today_dt).days + 1
            except Exception: pass
                
        if not target_day: target_day = 1
        actual_start_day = target_day

        all_tasks = [t for d in current_plan for t in d.tasks] + unscheduled
        matched_task = next((t for t in all_tasks if title_hint and title_hint in t.title.lower()), None)

        if not matched_task:
            continue

        base_id = matched_task.task_id.split("-s")[0]
        tasks_to_move = []
        
        for d in current_plan:
            kept_tasks = []
            for t in d.tasks:
                if t.task_id.startswith(base_id): tasks_to_move.append(t)
                else: kept_tasks.append(t)
            d.tasks = kept_tasks
            d.total_effort_points = sum(t.effort_points for t in d.tasks)
            d.capacity_percentage = int((d.total_effort_points / 10) * 100)

        new_unscheduled = []
        for t in unscheduled:
            if t.task_id.startswith(base_id): tasks_to_move.append(t)
            else: new_unscheduled.append(t)
        unscheduled = new_unscheduled

        tasks_to_move.sort(key=lambda x: getattr(x, 'session_number', 0) or 0)

        if action == "remove":
            responses.append(mod.get("response", f"Removed {title_hint}."))
            continue

        if actual_start_day and action in ("move", "reschedule", "shift"):
            required_days = actual_start_day + len(tasks_to_move)
            while len(current_plan) < required_days:
                next_day_num = len(current_plan) + 1
                target_date = (today_dt + timedelta(days=next_day_num - 1)).isoformat()
                current_plan.append(ScheduledDay(
                    day=next_day_num, date=target_date,
                    tasks=[], total_effort_points=0, capacity_percentage=0
                ))
                
            current_plan.sort(key=lambda d: d.day)

            for offset, t in enumerate(tasks_to_move):
                placement_day_num = actual_start_day + offset
                target_obj = next((d for d in current_plan if d.day == placement_day_num), None)
                if target_obj:
                    t.scheduled_day = placement_day_num
                    t.unscheduled_reason = None
                    target_obj.tasks.append(t)
                    target_obj.total_effort_points += t.effort_points
                    target_obj.capacity_percentage = int((target_obj.total_effort_points / 10) * 100)
                    
            responses.append(mod.get("response", f"Moved to Day {actual_start_day}."))
        else:
            unscheduled.extend(tasks_to_move)
            responses.append(mod.get("response", f"Updated {title_hint}."))

    return current_plan, unscheduled, " ".join(responses) if responses else "I couldn't find those tasks to move."

def smart_spread_tasks(tasks: List[Task], state: UserState, last_feedback: str = None) -> tuple:
    from engine.capacity import get_daily_ceiling, apply_memory_adjustment, get_effort_points
    import math as _math
    ceiling = get_daily_ceiling(state)
    ceiling = apply_memory_adjustment(ceiling, last_feedback)
    today = date.today()
    MAX_DAYS = 30
    MAX_TOTAL = 60

    days = [ScheduledDay(day=i+1, date=(today + timedelta(days=i)).isoformat(), tasks=[], total_effort_points=0, capacity_percentage=0) for i in range(MAX_DAYS)]
    unscheduled, total_scheduled = [], 0
    expanded_tasks = []

    for task in tasks:
        if task.work_style == "progressive" and task.spread_days and task.spread_days > 1:
            n = task.spread_days
            session_pts = max(1, _math.ceil(get_effort_points(task.difficulty) / n))
            dl_days = max(1, (date.fromisoformat(task.deadline) - today).days) if task.deadline else None

            for s in range(n):
                import copy
                st = copy.deepcopy(task)
                st.task_id = f"{task.task_id}-s{s+1}"
                st.title = f"{task.title} — Day {s+1} of {n}"
                st.session_number = s + 1
                st.total_sessions = n
                st.effort_points = session_pts
                pref_day = min(s * max(1, dl_days // n), MAX_DAYS - 1) if dl_days else min(s, MAX_DAYS - 1)
                st.scheduled_day = pref_day + 1
                expanded_tasks.append((st, pref_day))
        else:
            pref_day = max(0, min((date.fromisoformat(task.deadline) - today).days - 1, MAX_DAYS - 1)) if task.deadline else 0
            expanded_tasks.append((task, pref_day))

    for task, pref_day in expanded_tasks:
        if total_scheduled >= MAX_TOTAL:
            task.unscheduled_reason = "max_plan_tasks_reached"
            unscheduled.append(task)
            continue

        pts = task.effort_points if task.effort_points > 0 else get_effort_points(task.difficulty)
        placed = False
        for idx in list(range(pref_day, MAX_DAYS)) + list(range(0, pref_day)):
            day = days[idx]
            if day.total_effort_points + pts <= ceiling:
                task.effort_points = pts
                task.scheduled_day = day.day
                day.tasks.append(task)
                day.total_effort_points += pts
                day.capacity_percentage = int((day.total_effort_points / 10) * 100)
                total_scheduled += 1
                placed = True
                break

        if not placed:
            task.unscheduled_reason = "capacity_constraints"
            unscheduled.append(task)

    return [d for d in days if d.tasks], unscheduled


async def run_orchestrator(request: PlanRequest) -> PlanResponse:
    today_dt = date.today()
    today_str = today_dt.isoformat()
    user_id = request.user_id
    input_text = request.input_text

    existing_plan = deserialize_plan(request.current_plan) if request.current_plan else []
    existing_unscheduled = [Task(**t) for t in request.current_unscheduled] if request.current_unscheduled else []
    tasks_preview, needs_clarif_preview, _ = extract_tasks(input_text, today_str)

    history_days = count_user_history_days(user_id)
    has_existing_context = bool(existing_plan or existing_unscheduled or request.current_summary)
    if (
        not has_existing_context
        and needs_cold_start(history_days, input_text, request.state_inputs)
        and (tasks_preview or not needs_clarif_preview)
    ):
        return PlanResponse(
            user_id=user_id, response_text="How are you feeling today?",
            detected_state=UserState.normal, needs_clarification=True, clarification_question="cold_start",
        )

    if existing_plan and is_modification_request(input_text):
        try:
            plan_summary = serialize_plan(existing_plan)
            mod_prompt = MODIFICATION_PROMPT.format(current_plan=json.dumps(plan_summary), user_request=input_text, today=today_str)
            
            # Use strict JSON MIME TYPE to guarantee parsing succeeds
            mod_response = client.models.generate_content(
                model=MODEL, 
                contents=mod_prompt, 
                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json"
                )
            )
            
            mod_data = json.loads(mod_response.text.strip())
            
            # Handle wrapping elegantly
            mods = mod_data.get("modifications", [])
            if not mods:
                mods = mod_data if isinstance(mod_data, list) else [mod_data]
            
            updated_plan, updated_unscheduled, mod_message = apply_modifications(existing_plan, existing_unscheduled, mods)

            # --- HYBRID TRAVEL LOGIC ---
            travel_day_num = None
            travel_match = re.search(r"travel.*?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d+)", input_text.lower())
            
            if "travel" in input_text.lower() and travel_match:
                m_str, d_str = travel_match.group(1), int(travel_match.group(2))
                month_map = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}
                try:
                    t_date = date(today_dt.year, month_map[m_str], d_str)
                    travel_day_num = (t_date - today_dt).days + 1
                    
                    if travel_day_num > 0:
                        for day in updated_plan:
                            if day.day == travel_day_num:
                                bumped_tasks = day.tasks
                                updated_unscheduled.extend(bumped_tasks)
                                
                                travel_task = Task(
                                    task_id=f"travel-{uuid.uuid4().hex[:6]}", title=f"✈️ Travel to Destination",
                                    declared_priority=Priority.high, effective_priority=Priority.high, difficulty=Difficulty.medium, effort_points=10, scheduled_day=travel_day_num
                                )
                                day.tasks = [travel_task]
                                day.total_effort_points = 10
                                day.capacity_percentage = 100
                                
                                if bumped_tasks:
                                    task_names = [t.title for t in bumped_tasks]
                                    mod_message += f"\n\n✨ **Travel Day Set!** I cleared April {d_str} for your travel. This displaced: **{', '.join(task_names)}**.\n\nWhich day(s) would you like me to move these to? I will schedule them wherever you choose, irrespective of capacity limits."
                                else:
                                    mod_message += f"\n\n✨ **Travel Day Set!** I cleared April {d_str} for your travel. Your schedule for that day was already empty."
                                break
                except Exception as e: 
                    print(f"Travel Logic error: {e}")

            # --- DOMAIN AGENT FIX ---
            all_mod_tasks = [t for d in updated_plan for t in d.tasks] + updated_unscheduled
            new_domain_tasks, domain_adjustments = run_domain_agents(input_text, all_mod_tasks)
            if new_domain_tasks:
                updated_unscheduled.extend(new_domain_tasks)
                if domain_adjustments:
                    mod_message += f"\n\n{domain_adjustments[0]}"

            while updated_plan and not updated_plan[-1].tasks:
                updated_plan.pop()

            actions_proposed = []
            if updated_plan and not request.confirm_actions:
                tasks_summary = [{"title": t.title, "priority": str(t.effective_priority)} for d in updated_plan for t in d.tasks]
                actions_proposed.append(ActionProposed(
                    action_type="calendar_create", tool="google_calendar",
                    details={"tasks": tasks_summary, "date": today_str}, status="pending"
                ))

            latest_record = get_latest_record(user_id)
            raw_state = latest_record.get("qualitative_state") if latest_record else "normal"
            if raw_state and "." in raw_state: raw_state = raw_state.split(".")[-1]
            if not raw_state: raw_state = "normal"
            
            return PlanResponse(
                user_id=user_id, response_text=mod_message,
                detected_state=UserState(raw_state),
                plan=updated_plan, unscheduled_tasks=updated_unscheduled,
                adjustments_applied=["Modifications safely applied."],
                actions_proposed=actions_proposed, daily_summary=request.current_summary or "",
            )
        except Exception as e:
            import traceback; traceback.print_exc()
            return PlanResponse(
                user_id=user_id, response_text=f"An internal error occurred while updating: {str(e)}",
                detected_state=UserState.normal, plan=existing_plan, unscheduled_tasks=existing_unscheduled
            )

    tasks, needs_clarif = tasks_preview, needs_clarif_preview
    if not tasks and needs_clarif:
        return PlanResponse(
            user_id=user_id, response_text="Need more details on tasks.", detected_state=UserState.normal,
            needs_clarification=True, plan=existing_plan, unscheduled_tasks=existing_unscheduled
        )

    existing_scheduled_tasks = [t for d in existing_plan for t in d.tasks]
    backlog_tasks = [Task(**item) for item in get_pending_backlog(user_id) if not any(t.task_id == item.get("task_id") for t in existing_scheduled_tasks)]
    
    all_tasks = apply_effective_priorities(existing_scheduled_tasks + backlog_tasks + tasks)
    domain_tasks, domain_adjustments = run_domain_agents(input_text, all_tasks)
    all_tasks += domain_tasks
    
    history = get_user_history(user_id, days=3)
    detected_state = run_state_agent(input_text, request.state_inputs, history, all_tasks)
    kept_tasks, pruned_tasks, prune_adjustments = prune_tasks(all_tasks, detected_state)
    
    latest_record = get_latest_record(user_id)
    scheduled_days, unscheduled = smart_spread_tasks(order_tasks(kept_tasks), detected_state, latest_record.get("feedback") if latest_record else None)
    unscheduled.extend(pruned_tasks)

    resp_prompt = RESPONSE_PROMPT.format(
        state=detected_state, scheduled_count=sum(len(d.tasks) for d in scheduled_days), days_count=len(scheduled_days),
        unscheduled_count=len(unscheduled), adjustments=", ".join(domain_adjustments + prune_adjustments) or "none", input_text=input_text
    )
    
    actions_proposed = []
    if scheduled_days and not request.confirm_actions:
        tasks_summary = [{"title": t.title, "priority": str(t.effective_priority)} for d in scheduled_days for t in d.tasks]
        actions_proposed.append(ActionProposed(action_type="calendar_create", tool="google_calendar", details={"tasks": tasks_summary, "date": today_str}, status="pending"))
    
    return PlanResponse(
        user_id=user_id, response_text=client.models.generate_content(model=MODEL, contents=resp_prompt).text.strip(),
        detected_state=detected_state, plan=scheduled_days, unscheduled_tasks=unscheduled,
        adjustments_applied=domain_adjustments + prune_adjustments, actions_proposed=actions_proposed, daily_summary=""
    )
