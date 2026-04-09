from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar.events.owned"]


def main() -> None:
    creds_path = Path("credentials.json")
    if not creds_path.exists():
        raise SystemExit("credentials.json was not found. This helper is only for local development.")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(creds_path),
        scopes=SCOPES,
    )
    credentials = flow.run_local_server(port=0, open_browser=True)
    Path("token.json").write_text(credentials.to_json(), encoding="utf-8")
    print("\nToken saved to token.json for local development only.")
    print("Valid:", credentials.valid)


if __name__ == "__main__":
    main()
