from datetime import date, timedelta
from typing import List

from googleapiclient.discovery import build

from auth.calendar_tokens import load_calendar_credentials
from models.schemas import AuthenticatedUser, ScheduledDay


def _next_day_iso(day_iso: str) -> str:
    parsed = date.fromisoformat(day_iso)
    return (parsed + timedelta(days=1)).isoformat()


def get_calendar_service(user: AuthenticatedUser):
    credentials = load_calendar_credentials(user)
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


async def create_calendar_events(
    scheduled_days: List[ScheduledDay],
    user: AuthenticatedUser,
    calendar_id: str = "primary",
) -> str:
    service = get_calendar_service(user)
    created = 0

    for day in scheduled_days:
        for task in day.tasks:
            if not day.date:
                continue
            priority = str(task.effective_priority)
            event = {
                "summary": task.title,
                "description": (
                    f"Planned by Productivity Agent\n"
                    f"Priority: {priority}\n"
                    f"Difficulty: {task.difficulty}\n"
                    f"Effort: {task.effort_points} pts"
                ),
                "start": {
                    "date": day.date,
                    "timeZone": "UTC",
                },
                "end": {
                    "date": _next_day_iso(day.date),
                    "timeZone": "UTC",
                },
                "colorId": (
                    "11" if priority == "high" else
                    "5" if priority == "medium" else "9"
                ),
            }
            service.events().insert(calendarId=calendar_id, body=event).execute()
            created += 1

    return f"Created {created} Google Calendar event{'s' if created != 1 else ''} for {user.email}."
