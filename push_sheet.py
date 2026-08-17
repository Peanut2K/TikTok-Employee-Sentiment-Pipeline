"""Push accounts.json into the team Google Sheet.

Tries the service account first. If the Sheet isn't shared with it, falls back
to the desktop OAuth client, which authenticates as you — so it can write to
anything you can already edit, no sharing step needed.

    .venv/bin/python push_sheet.py
"""

import json
import sys
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from scrape import HEADERS, SHEET_KEY, SERVICE_ACCOUNT, write_sheet

HERE = Path(__file__).parent
OAUTH_CLIENT = HERE / "client_secret.json"
OAUTH_TOKEN = HERE / "oauth_token.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def client():
    """Service account if it has access, otherwise browser OAuth as you."""
    creds = Credentials.from_service_account_file(str(SERVICE_ACCOUNT), scopes=SCOPES)
    gc = gspread.authorize(creds)
    try:
        gc.open_by_key(SHEET_KEY).sheet1.acell("A1")
        print("auth: service account")
        return gc
    except gspread.exceptions.APIError as e:
        if "PERMISSION_DENIED" not in str(e) and "403" not in str(e):
            raise
        print("auth: service account has no access, falling back to your Google login")

    # gspread opens a browser once, then reuses the saved token.
    gc = gspread.oauth(
        credentials_filename=str(OAUTH_CLIENT),
        authorized_user_filename=str(OAUTH_TOKEN),
        scopes=SCOPES,
    )
    print("auth: your Google account")
    return gc


def main():
    src = HERE / "accounts.json"
    if not src.exists():
        sys.exit("No accounts.json yet. Run scrape.py first.")

    rows = json.loads(src.read_text(encoding="utf-8"))
    print(f"{len(rows)} accounts to push")

    url = write_sheet(rows, gc=client())
    print(f"Sheet updated: {url}")


if __name__ == "__main__":
    main()
