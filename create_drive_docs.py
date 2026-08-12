#!/usr/bin/env python3
"""
Create Google Drive transcript and outline docs for new sermons.

Required GitHub secrets:
  GOOGLE_CLIENT_ID     - OAuth2 client ID
  GOOGLE_CLIENT_SECRET - OAuth2 client secret
  GOOGLE_REFRESH_TOKEN - OAuth2 refresh token (from get_refresh_token.py)
  ANTHROPIC_API_KEY    - For Claude outline generation
"""

import json, os, sys

try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaInMemoryUpload
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
except ImportError:
    raise SystemExit("Run: pip install google-api-python-client google-auth")

try:
    import anthropic
except ImportError:
    raise SystemExit("Run: pip install anthropic")


TRANSCRIPTS_FOLDER = "1ge3-D-cI6pBKrcSt9qIaHdZjA_xYq2w8"
OUTLINES_FOLDER    = "1AaOauQiHdwNsmseZdZQnhqZRqxTPnnuY"
NEW_SERMONS_FILE   = "new_sermons.json"


def get_drive_service():
    client_id     = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN")
    if not all([client_id, client_secret, refresh_token]):
        raise SystemExit("GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN must all be set")
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )
    creds.refresh(Request())
    return build("drive", "v3", credentials=creds)


def create_gdoc(drive, title, content, folder_id):
    media = MediaInMemoryUpload(content.encode("utf-8"), mimetype="text/plain")
    file = drive.files().create(
        body={
            "name": title,
            "mimeType": "application/vnd.google-apps.document",
            "parents": [folder_id],
        },
        media_body=media,
        fields="id",
    ).execute()
    return file["id"]


def build_transcript_content(sermon):
    return "\n".join([
        sermon["title"],
        sermon.get("scripture", ""),
        f"{sermon.get('preacher', 'Peter Frey')} | {sermon['date']}",
        "",
        "=" * 60,
        "",
        sermon.get("transcript") or "[Transcript not yet available]",
    ])


def generate_outline(sermon):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""Generate a one-page sermon outline for this sermon. Use exactly this format:

{sermon['title'].upper()}
{sermon.get('scripture', '')} | {sermon.get('preacher', 'Peter Frey')} | {sermon['date']}

BIG IDEA
Scripture: "[key verse]" (Reference)

[One-sentence big idea - the central claim or invitation of the sermon]

MAIN POINTS / STRUCTURE

1. [First major movement] (verse reference if applicable)
   2-3 sentence summary.

2. [Second major movement]
   2-3 sentence summary.

3. [Third major movement, if present]
   2-3 sentence summary.

KEY ILLUSTRATIONS

- [Illustration name]: 1-2 sentence description and its point.

- [Illustration name]: Brief description.

APPLICATION / CALL TO ACTION

[1-3 sentences on the specific invitation or next steps for the congregation.]

---

Sermon transcript:

{sermon.get('transcript') or '[No transcript available]'}"""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


def main():
    if not os.path.exists(NEW_SERMONS_FILE):
        print("No new_sermons.json - nothing to process.")
        return

    with open(NEW_SERMONS_FILE, encoding="utf-8") as f:
        new_sermons = json.load(f)

    if not new_sermons:
        print("No new sermons to process.")
        return

    print(f"Processing {len(new_sermons)} new sermon(s)...")
    drive = get_drive_service()

    for s in new_sermons:
        date  = s["date"]
        title = s["title"]
        doc_title = f"{date} - {title}"
        print(f"\n  {doc_title}")

        # Transcript doc
        try:
            content = build_transcript_content(s)
            doc_id  = create_gdoc(drive, doc_title, content, TRANSCRIPTS_FOLDER)
            print(f"    Transcript: {doc_id}")
        except Exception as e:
            print(f"    Transcript error: {e}")

        # Outline doc
        try:
            outline    = generate_outline(s)
            outline_id = create_gdoc(drive, f"{doc_title} (Outline)", outline, OUTLINES_FOLDER)
            print(f"    Outline:    {outline_id}")
        except Exception as e:
            print(f"    Outline error: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()
