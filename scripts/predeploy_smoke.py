import json
import sys
from typing import Any

import requests


def assert_status(response: requests.Response, expected: int, label: str) -> None:
    if response.status_code != expected:
        raise AssertionError(
            f"{label} expected HTTP {expected}, got {response.status_code}: {response.text}"
        )
    print(f"[PASS] {label} -> {response.status_code}")


def assert_json(response: requests.Response, label: str) -> Any:
    try:
        payload = response.json()
    except Exception as exc:  # pragma: no cover
        raise AssertionError(f"{label} did not return JSON: {response.text}") from exc
    print(f"[INFO] {label} JSON keys: {sorted(payload.keys())}")
    return payload


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
    session = requests.Session()

    print(f"Running smoke tests against {base_url}")

    root = session.get(f"{base_url}/", timeout=15)
    assert_status(root, 200, "GET /")

    health = session.get(f"{base_url}/health", timeout=15)
    assert_status(health, 200, "GET /health")
    health_payload = assert_json(health, "GET /health")
    if "status" not in health_payload:
        raise AssertionError("/health payload is missing `status`.")

    auth_config = session.get(f"{base_url}/auth/config", timeout=15)
    assert_status(auth_config, 200, "GET /auth/config")
    auth_payload = assert_json(auth_config, "GET /auth/config")
    if "googleClientId" not in auth_payload:
        raise AssertionError("/auth/config payload is missing `googleClientId`.")

    auth_me = session.get(f"{base_url}/auth/me", timeout=15)
    assert_status(auth_me, 200, "GET /auth/me")
    me_payload = assert_json(auth_me, "GET /auth/me")
    if me_payload.get("authenticated") is not False:
        raise AssertionError("Expected unauthenticated /auth/me before login.")

    unauth_plan = session.post(
        f"{base_url}/plan",
        json={
            "input_text": "Test task (high, easy)",
            "confirm_actions": False,
            "current_plan": [],
            "current_unscheduled": [],
            "current_summary": "",
        },
        timeout=15,
    )
    assert_status(unauth_plan, 401, "POST /plan unauthenticated")

    unauth_history = session.get(f"{base_url}/history/me", timeout=15)
    assert_status(unauth_history, 401, "GET /history/me unauthenticated")

    unauth_calendar = session.get(
        f"{base_url}/auth/calendar/start",
        timeout=15,
        allow_redirects=False,
    )
    assert_status(unauth_calendar, 401, "GET /auth/calendar/start unauthenticated")

    print("\nSmoke tests passed.")
    print("Next step: run the manual Google sign-in and Calendar flow checklist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
