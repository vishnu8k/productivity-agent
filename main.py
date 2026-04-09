import logging
import os
import secrets
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from agents.orchestrator import run_orchestrator
from auth.calendar_tokens import (
    build_calendar_authorization_url,
    calendar_is_connected,
    calendar_oauth_enabled,
    complete_calendar_oauth,
    ensure_token_table,
)
from auth.google_identity import GoogleIdentityError, verify_google_credential
from auth.session import (
    clear_authenticated_session,
    get_authenticated_user,
    get_csrf_token,
    get_session_cookie_secure,
    require_authenticated_user,
    require_csrf,
    set_authenticated_session,
)
from models.schemas import DirectScheduleRequest, PlanRequest, ToolResult
from security.rate_limit import InMemoryRateLimiter
from tools.calendar_tool import create_calendar_events

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger(__name__)


def _allowed_origins() -> list[str]:
    configured = os.getenv("ALLOWED_ORIGINS", "")
    origins = [item.strip() for item in configured.split(",") if item.strip()]
    if origins:
        return origins
    return [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    ]


def _session_secret() -> str:
    secret = os.getenv("SESSION_SECRET")
    if secret:
        return secret
    logger.warning("SESSION_SECRET is not configured; using an ephemeral development secret.")
    return secrets.token_urlsafe(32)


def _is_production_env() -> bool:
    return os.getenv("APP_ENV", "development").lower() in {"production", "staging"}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


rate_limiter = InMemoryRateLimiter()


def _rate_limit(request: Request, bucket: str, max_requests: int, window_seconds: int) -> None:
    key = f"{bucket}:{_client_ip(request)}"
    rate_limiter.check(key, max_requests=max_requests, window_seconds=window_seconds)


def _auth_payload(request: Request) -> dict:
    user = get_authenticated_user(request)
    return {
        "authenticated": bool(user),
        "csrfToken": get_csrf_token(request) if user else None,
        "calendarEnabled": calendar_oauth_enabled(),
        "calendarConnected": calendar_is_connected(user.sub) if user and calendar_oauth_enabled() else False,
        "user": user.model_dump() if user else None,
    }


def _handle_internal_error(message: str, exc: Exception) -> None:
    logger.exception(message, exc_info=exc)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="The server hit an unexpected error. Please try again.",
    )


app = FastAPI(
    title="AI Productivity Agent",
    description="Multi-agent productivity assistant powered by Google ADK and Gemini",
    version="2.0.0",
)

app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    session_cookie="productivity_session",
    same_site="lax",
    https_only=get_session_cookie_secure(),
    max_age=60 * 60 * 24 * 7,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)

app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.on_event("startup")
def startup() -> None:
    try:
        ensure_token_table()
    except Exception as exc:  # pragma: no cover - depends on cloud resources
        logger.warning("Calendar token table was not prepared during startup: %s", exc)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://accounts.google.com; "
        "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https://accounts.google.com https://oauth2.googleapis.com; "
        "frame-src https://accounts.google.com; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self' https://accounts.google.com"
    )
    return response


class GoogleCredentialRequest(BaseModel):
    credential: str


@app.get("/")
async def root():
    return FileResponse("frontend/index.html")


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "auth_enabled": bool(os.getenv("GOOGLE_OAUTH_CLIENT_ID")),
        "calendar_oauth_enabled": calendar_oauth_enabled(),
        "model": os.getenv("GEMINI_MODEL"),
    }


@app.get("/auth/config")
async def auth_config():
    return {
        "googleClientId": os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
        "calendarEnabled": calendar_oauth_enabled(),
    }


@app.get("/auth/me")
async def auth_me(request: Request):
    return _auth_payload(request)


@app.post("/auth/google")
async def auth_google(request: Request, payload: GoogleCredentialRequest):
    _rate_limit(request, "auth_google", max_requests=10, window_seconds=60)
    try:
        user = verify_google_credential(payload.credential)
    except GoogleIdentityError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    csrf_token = set_authenticated_session(request, user)
    body = _auth_payload(request)
    body["csrfToken"] = csrf_token
    return body


@app.post("/auth/logout")
async def auth_logout(
    request: Request,
    _: Any = Depends(require_authenticated_user),
):
    require_csrf(request)
    clear_authenticated_session(request)
    return {"authenticated": False}


@app.get("/auth/calendar/start")
async def auth_calendar_start(
    request: Request,
    _: Any = Depends(require_authenticated_user),
):
    _rate_limit(request, "calendar_start", max_requests=10, window_seconds=300)
    try:
        auth_url = build_calendar_authorization_url(request)
    except HTTPException:
        raise
    except Exception as exc:
        _handle_internal_error("Unable to start Google Calendar OAuth.", exc)
    return RedirectResponse(auth_url)


@app.get("/auth/calendar/callback")
async def auth_calendar_callback(request: Request):
    user = get_authenticated_user(request)
    if not user:
        return RedirectResponse("/?calendar=error")
    try:
        complete_calendar_oauth(request, user)
        return RedirectResponse("/?calendar=connected")
    except HTTPException as exc:
        logger.warning("Google Calendar callback failed: %s", exc.detail)
        if _is_production_env():
            return RedirectResponse("/?calendar=error")
        return RedirectResponse(f"/?calendar=error&reason={quote(str(exc.detail))}")
    except Exception as exc:
        logger.exception("Unexpected error during Google Calendar callback.", exc_info=exc)
        if _is_production_env():
            return RedirectResponse("/?calendar=error")
        return RedirectResponse(f"/?calendar=error&reason={quote(str(exc))}")


@app.post("/plan")
async def create_plan(
    request: Request,
    payload: PlanRequest,
    user: Any = Depends(require_authenticated_user),
):
    require_csrf(request)
    _rate_limit(request, "plan", max_requests=25, window_seconds=60)

    payload.user_id = user.sub
    try:
        response = await run_orchestrator(payload)
        response.user_id = user.sub
        return response
    except HTTPException:
        raise
    except Exception as exc:
        _handle_internal_error("Plan creation failed.", exc)


@app.post("/confirm")
async def confirm_actions(
    request: Request,
    payload: PlanRequest,
    user: Any = Depends(require_authenticated_user),
):
    require_csrf(request)
    _rate_limit(request, "confirm", max_requests=15, window_seconds=60)

    if not calendar_oauth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google Calendar integration is not configured yet.",
        )
    if not calendar_is_connected(user.sub):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Connect your Google Calendar before confirming actions.",
        )

    payload.user_id = user.sub
    payload.confirm_actions = True
    try:
        response = await run_orchestrator(payload)
        response.user_id = user.sub
        if response.plan:
            result_message = await create_calendar_events(response.plan, user)
            response.tool_results = [
                ToolResult(
                    tool="google_calendar",
                    status="success",
                    message=result_message,
                )
            ]
        response.actions_proposed = []
        return response
    except HTTPException:
        raise
    except Exception as exc:
        _handle_internal_error("Calendar confirmation failed.", exc)


@app.post("/api/schedule-task")
async def schedule_task_direct(
    request: Request,
    payload: DirectScheduleRequest,
    _: Any = Depends(require_authenticated_user),
):
    require_csrf(request)
    _rate_limit(request, "schedule_task", max_requests=40, window_seconds=60)

    try:
        plan, unscheduled = payload.current_plan, payload.current_unscheduled
        task = next((t for t in unscheduled if t.get("task_id") == payload.task_id), None)
        if not task:
            return {"status": "error", "message": "Task not found"}

        unscheduled = [t for t in unscheduled if t.get("task_id") != payload.task_id]
        day_obj = next((d for d in plan if d.get("day") == payload.target_day), None)

        if not day_obj:
            target_date = (date.today() + timedelta(days=payload.target_day - 1)).isoformat()
            day_obj = {
                "day": payload.target_day,
                "date": target_date,
                "tasks": [],
                "total_effort_points": 0,
                "capacity_percentage": 0,
            }
            plan.append(day_obj)
            plan.sort(key=lambda item: item["day"])

        task["scheduled_day"] = payload.target_day
        task["unscheduled_reason"] = None
        day_obj["tasks"].append(task)
        day_obj["total_effort_points"] = sum(t.get("effort_points", 1) for t in day_obj["tasks"])
        day_obj["capacity_percentage"] = int((day_obj["total_effort_points"] / 10) * 100)

        return {"status": "success", "plan": plan, "unscheduled_tasks": unscheduled}
    except Exception as exc:
        _handle_internal_error("Direct task scheduling failed.", exc)


@app.get("/history/me")
async def get_history(
    request: Request,
    user: Any = Depends(require_authenticated_user),
):
    _rate_limit(request, "history", max_requests=30, window_seconds=60)
    try:
        from memory.bigquery_client import get_user_history

        history = get_user_history(user.sub, days=7)
        return {"user_id": user.sub, "history": history}
    except Exception as exc:
        _handle_internal_error("History lookup failed.", exc)


@app.post("/cold-start")
async def handle_cold_start(
    request: Request,
    body: dict,
    user: Any = Depends(require_authenticated_user),
):
    require_csrf(request)
    _rate_limit(request, "cold_start", max_requests=10, window_seconds=60)

    try:
        from agents.state_agent import parse_cold_start_response
        from memory.bigquery_client import backfill_history

        user_response = body.get("response", "")
        parsed = parse_cold_start_response(user_response)
        state = parsed.get("current_state", "normal")
        capacity = parsed.get("estimated_capacity", 50)
        backfill_history(user.sub, state, capacity)
        return {
            "status": "cold_start_complete",
            "detected_state": state,
            "message": "Great, I have a sense of your recent workload. Now tell me what you need to plan!",
        }
    except Exception as exc:
        _handle_internal_error("Cold-start backfill failed.", exc)


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
