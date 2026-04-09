import json
import os
from datetime import datetime, date, timedelta
from typing import Optional
from google.cloud import bigquery
from dotenv import load_dotenv

load_dotenv()

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
DATASET = os.getenv("BIGQUERY_DATASET")
TABLE = os.getenv("BIGQUERY_TABLE")
FULL_TABLE = f"{PROJECT_ID}.{DATASET}.{TABLE}"


def get_client():
    return bigquery.Client(project=PROJECT_ID)


def create_dataset_and_table():
    client = get_client()

    dataset_ref = bigquery.Dataset(f"{PROJECT_ID}.{DATASET}")
    dataset_ref.location = "US"
    try:
        client.create_dataset(dataset_ref, exists_ok=True)
        print(f"Dataset {DATASET} ready.")
    except Exception as e:
        print(f"Dataset error: {e}")

    schema = [
        bigquery.SchemaField("user_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("date", "DATE", mode="REQUIRED"),
        bigquery.SchemaField("daily_capacity_utilized", "INTEGER", mode="NULLABLE"),
        bigquery.SchemaField("qualitative_state", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("pending_backlog", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("recovery_day_index", "INTEGER", mode="NULLABLE"),
        bigquery.SchemaField("daily_summary", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("feedback", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("created_at", "TIMESTAMP", mode="NULLABLE"),
    ]

    table_ref = bigquery.Table(FULL_TABLE, schema=schema)
    try:
        client.create_table(table_ref, exists_ok=True)
        print(f"Table {TABLE} ready.")
    except Exception as e:
        print(f"Table error: {e}")


def get_user_history(user_id: str, days: int = 3) -> list:
    client = get_client()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    query = f"""
        SELECT *
        FROM `{FULL_TABLE}`
        WHERE user_id = @user_id
          AND date >= @cutoff
        ORDER BY date DESC
        LIMIT {days}
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            bigquery.ScalarQueryParameter("cutoff", "STRING", cutoff),
        ]
    )
    try:
        results = client.query(query, job_config=job_config).result()
        return [dict(row) for row in results]
    except Exception as e:
        print(f"BigQuery read error: {e}")
        return []


def get_latest_record(user_id: str) -> Optional[dict]:
    client = get_client()
    query = f"""
        SELECT *
        FROM `{FULL_TABLE}`
        WHERE user_id = @user_id
        ORDER BY date DESC
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
        ]
    )
    try:
        results = list(client.query(query, job_config=job_config).result())
        return dict(results[0]) if results else None
    except Exception as e:
        print(f"BigQuery latest record error: {e}")
        return None


def get_pending_backlog(user_id: str) -> list:
    record = get_latest_record(user_id)
    if not record:
        return []
    try:
        backlog = json.loads(record.get("pending_backlog") or "[]")
        if len(backlog) > 30:
            backlog = sorted(
                backlog,
                key=lambda t: (
                    0 if t.get("effective_priority") == "high" else
                    1 if t.get("effective_priority") == "medium" else 2,
                    t.get("deadline") or "9999-12-31"
                )
            )[:30]
        return backlog
    except Exception:
        return []


def write_daily_record(
    user_id: str,
    state: str,
    capacity_utilized: int,
    pending_backlog: list,
    daily_summary: str,
    feedback: Optional[str] = None,
    recovery_day_index: int = 0
):
    client = get_client()
    today = date.today().isoformat()

    delete_query = f"""
        DELETE FROM `{FULL_TABLE}`
        WHERE user_id = @user_id AND date = @today
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            bigquery.ScalarQueryParameter("today", "STRING", today),
        ]
    )
    try:
        client.query(delete_query, job_config=job_config).result()
    except Exception as e:
        print(f"Delete error (non-fatal): {e}")

    backlog_str = json.dumps(pending_backlog[:30])
    rows = [{
        "user_id": user_id,
        "date": today,
        "daily_capacity_utilized": capacity_utilized,
        "qualitative_state": state,
        "pending_backlog": backlog_str,
        "recovery_day_index": recovery_day_index,
        "daily_summary": daily_summary,
        "feedback": feedback,
        "created_at": datetime.utcnow().isoformat(),
    }]
    errors = client.insert_rows_json(FULL_TABLE, rows)
    if errors:
        print(f"BigQuery write errors: {errors}")
    else:
        print(f"BigQuery record written for {user_id} on {today}")


def backfill_history(user_id: str, state: str, capacity_estimate: int):
    client = get_client()
    today = date.today()
    for i in range(1, 4):
        past_date = (today - timedelta(days=i)).isoformat()
        rows = [{
            "user_id": user_id,
            "date": past_date,
            "daily_capacity_utilized": capacity_estimate,
            "qualitative_state": state,
            "pending_backlog": "[]",
            "recovery_day_index": 0,
            "daily_summary": "Backfilled during cold start.",
            "feedback": None,
            "created_at": datetime.utcnow().isoformat(),
        }]
        try:
            client.insert_rows_json(FULL_TABLE, rows)
            print(f"Backfilled {past_date} for {user_id}")
        except Exception as e:
            print(f"Backfill error for {past_date}: {e}")


def count_user_history_days(user_id: str) -> int:
    client = get_client()
    query = f"""
        SELECT COUNT(DISTINCT date) as day_count
        FROM `{FULL_TABLE}`
        WHERE user_id = @user_id
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
        ]
    )
    try:
        results = list(client.query(query, job_config=job_config).result())
        return results[0]["day_count"] if results else 0
    except Exception:
        return 0
