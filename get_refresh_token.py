#!/usr/bin/env python3
"""
Mint the refresh tokens the sermon pipeline needs. Run this on your own laptop,
not in CI — it opens a browser for you to consent.

Why this exists: a refresh token carries the scopes you consented to at the
moment it was created, and you cannot widen them later. The existing
GOOGLE_REFRESH_TOKEN was issued with drive.file, which only grants access to
files the app itself created — it cannot read the sermon videos or the planning
doc. So it has to be reissued with broader scope.

Usage:
    pip install google-auth-oauthlib
    python get_refresh_token.py drive
    python get_refresh_token.py youtube

You need the OAuth client's ID and secret — the same ones already in your repo
secrets as GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET. Download the client JSON
from Google Cloud Console ▸ APIs & Services ▸ Credentials, or paste them when
prompted.

Print the token, then put it in GitHub ▸ Settings ▸ Secrets and variables ▸
Actions:
    drive   -> GOOGLE_DRIVE_REFRESH_TOKEN
    youtube -> GOOGLE_YOUTUBE_REFRESH_TOKEN

Treat the output like a password. It grants ongoing access to the account you
consent with — sign in as the account that owns the YouTube channel.
"""
import sys

try:
    from google_auth_oauthlib.flow import InstalledAppFlow
except ImportError:
    raise SystemExit("Run: pip install google-auth-oauthlib")

SCOPE_SETS = {
    # Reads sermon videos and the planning doc; writes the podcast MP3.
    "drive": [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/documents.readonly",
    ],
    # youtube.upload for videos.insert; force-ssl for captions and for updating
    # the description once a transcript exists.
    "youtube": [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.force-ssl",
    ],
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in SCOPE_SETS:
        raise SystemExit(f"Usage: python get_refresh_token.py [{' | '.join(SCOPE_SETS)}]")

    which = sys.argv[1]
    scopes = SCOPE_SETS[which]

    client_id = input("GOOGLE_CLIENT_ID: ").strip()
    client_secret = input("GOOGLE_CLIENT_SECRET: ").strip()
    if not client_id or not client_secret:
        raise SystemExit("Both client id and secret are required.")

    config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    print(f"\nRequesting scopes:\n  " + "\n  ".join(scopes))
    print("\nSign in as the account that owns the sermon Drive folder and the "
          "YouTube channel.\n")

    flow = InstalledAppFlow.from_client_config(config, scopes)
    # access_type=offline + prompt=consent forces a *new* refresh token even if
    # you have consented before; without prompt=consent Google may return none.
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        raise SystemExit(
            "Google did not return a refresh token. Revoke the app at "
            "https://myaccount.google.com/permissions and run this again."
        )

    secret_name = "GOOGLE_DRIVE_REFRESH_TOKEN" if which == "drive" \
        else "GOOGLE_YOUTUBE_REFRESH_TOKEN"

    print("\n" + "=" * 62)
    print(f"Add this as the GitHub Actions secret {secret_name}:\n")
    print(creds.refresh_token)
    print("=" * 62)
    print("\nDo not commit it or paste it into a chat.")


if __name__ == "__main__":
    main()
