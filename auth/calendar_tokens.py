import json
import logging
import os
import secrets
from datetime import datetime
from typing import Optional

from cryptography.fernet import Fernet
from dotenv import load_dotenv
from fastapi import HTTPException, Request, status
from google.auth.transport.requests import Request as GoogleRequest
from google.cloud import bigquery
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from auth.session import SESSION_CALENDAR_STATE_KEY
from models.schemas import AuthenticatedUser

logger = logging.getLogger(__name__)

load_dotenv()

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
DATASET = os.getenv("AUTH_BIGQUERY_DATASET") or os.getenv("BIGQUERY_DATASET")
TOKEN_TABLE = os.getenv("GOOGLE_OAUTH_TOKEN_TABLE", "user_oauth_tokens")
CALENDAR_SCOPES = [
    os.getenv(
        "GOOGLE_CALENDAR_SCOPE",
        "https://www.googleapis.com/auth/calendar.events.owned",
    )
]


def calendar_oauth_enabled() -> bool:
    return all(
        [
            PROJECT_ID,
            DATASET,
            os.getenv("GOOGLE_OAUTH_CLIENT_ID"),
            os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),
            os.getenv("GOOGLE_OAUTH_REDIRECT_URI"),
            os.getenv("USER_TOKEN_ENCRYPTION_KEY"),
        ]
    )


def _full_table_name() -> str:
    if not PROJECT_ID or not DATASET:
        raise RuntimeError("BigQuery configuration is incomplete.")
    return f"{PROJECT_ID}.{DATASET}.{TOKEN_TABLE}"


def _client() -> bigquery.Client:
    if not PROJECT_ID:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for calendar token storage.")
    return bigquery.Client(project=PROJECT_ID)


def _cipher() -> Fernet:
    key = os.getenv("USER_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("USER_TOKEN_ENCRYPTION_KEY is not configured.")
    return Fernet(key.encode("utf-8"))


def _encrypt_token_blob(token_json: str) -> str:
    return _cipher().encrypt(token_json.encode("utf-8")).decode("utf-8")


def _decrypt_token_blob(token_blob: str) -> str:
    return _cipher().decrypt(token_blob.encode("utf-8")).decode("utf-8")


def ensure_token_table() -> None:
    if not calendar_oauth_enabled():
        return

    client = _client()
    dataset_ref = bigquery.Dataset(f"{PROJECT_ID}.{DATASET}")
    dataset_ref.location = os.getenv("BIGQUERY_LOCATION", "US")
    client.create_dataset(dataset_ref, exists_ok=True)

    schema = [
        bigquery.SchemaField("user_sub", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("email", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("provider", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("scopes", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("token_json_encrypted", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("created_at", "TIMESTAMP", mode="NULLABLE"),
        bigquery.SchemaField("updated_at", "TIMESTAMP", mode="NULLABLE"),
    ]
    table = bigquery.Table(_full_table_name(), schema=schema)
    client.create_table(table, exists_ok=True)


def _oauth_client_config() -> dict:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
    redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
    if not client_id or not client_secret or not redirect_uri:
        raise RuntimeError("Google OAuth is not fully configured.")
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def _build_flow(state: Optional[str] = None) -> Flow:
    flow = Flow.from_client_config(
        _oauth_client_config(),
        scopes=CALENDAR_SCOPES,
        state=state,
    )
    flow.redirect_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
    return flow


def build_calendar_authorization_url(request: Request) -> str:
    if not calendar_oauth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Calendar integration is not configured yet.",
        )

    state = secrets.token_urlsafe(24)
    request.session[SESSION_CALENDAR_STATE_KEY] = state
    flow = _build_flow(state=state)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    return auth_url


def store_calendar_credentials(user: AuthenticatedUser, credentials: Credentials) -> None:
    ensure_token_table()
    client = _client()
    now = datetime.utcnow().isoformat()

    delete_query = f"""
        DELETE FROM `{_full_table_name()}`
        WHERE user_sub = @user_sub AND provider = 'google_calendar'
    """
    delete_job = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_sub", "STRING", user.sub),
        ]
    )
    client.query(delete_query, job_config=delete_job).result()

    rows = [
        {
            "user_sub": user.sub,
            "email": user.email,
            "provider": "google_calendar",
            "scopes": json.dumps(sorted(list(credentials.scopes or CALENDAR_SCOPES))),
            "token_json_encrypted": _encrypt_token_blob(credentials.to_json()),
            "created_at": now,
            "updated_at": now,
        }
    ]
    errors = client.insert_rows_json(_full_table_name(), rows)
    if errors:
        raise RuntimeError(f"Unable to store calendar credentials: {errors}")


def _load_token_row(user_sub: str) -> Optional[dict]:
    if not calendar_oauth_enabled():
        return None
    ensure_token_table()
    query = f"""
        SELECT *
        FROM `{_full_table_name()}`
        WHERE user_sub = @user_sub AND provider = 'google_calendar'
        ORDER BY updated_at DESC
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("user_sub", "STRING", user_sub),
        ]
    )
    rows = list(_client().query(query, job_config=job_config).result())
    return dict(rows[0]) if rows else None


def calendar_is_connected(user_sub: str) -> bool:
    return _load_token_row(user_sub) is not None


def load_calendar_credentials(user: AuthenticatedUser) -> Credentials:
    row = _load_token_row(user.sub)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Connect your Google Calendar first.",
        )

    token_json = _decrypt_token_blob(row["token_json_encrypted"])
    token_info = json.loads(token_json)
    credentials = Credentials.from_authorized_user_info(token_info, CALENDAR_SCOPES)

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(GoogleRequest())
        store_calendar_credentials(user, credentials)

    return credentials


def complete_calendar_oauth(request: Request, user: AuthenticatedUser) -> None:
    stored_state = request.session.get(SESSION_CALENDAR_STATE_KEY)
    incoming_state = request.query_params.get("state")
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Google Calendar authorization failed: {error}",
        )
    if not stored_state or stored_state != incoming_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The Google Calendar authorization state was invalid.",
        )
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google Calendar did not return an authorization code.",
        )

    flow = _build_flow(state=stored_state)
    flow.fetch_token(code=code)
    credentials = flow.credentials
    if not credentials.refresh_token:
        logger.warning("Google Calendar OAuth finished without a refresh token for %s", user.email)

    store_calendar_credentials(user, credentials)
    request.session.pop(SESSION_CALENDAR_STATE_KEY, None)
