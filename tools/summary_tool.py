import os
from datetime import date
from dotenv import load_dotenv
from memory.bigquery_client import write_daily_record

load_dotenv()


async def store_daily_summary(
    user_id: str,
    summary: str,
    state: str,
    plan_date: str = None
) -> str:
    try:
        if not plan_date:
            plan_date = date.today().isoformat()

        write_daily_record(
            user_id=user_id,
            state=str(state),
            capacity_utilized=0,
            pending_backlog=[],
            daily_summary=summary,
        )
        return f"Daily summary stored for {user_id} on {plan_date}."
    except Exception as e:
        return f"Summary storage failed: {e}"


async def get_summary_display(summary: str, state: str, plan_date: str) -> dict:
    return {
        "date": plan_date,
        "state": state,
        "summary": summary,
        "stored": True
    }
