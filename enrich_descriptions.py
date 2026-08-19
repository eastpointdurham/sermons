#!/usr/bin/env python3
"""
Write a real YouTube description and tags for each newly-transcribed sermon.

This closes the loop that made the original problem awkward: the transcript only
exists after the video is on YouTube, so the upload gets a plain description
built from the sermon planning doc, and this step replaces it once the sermon
has actually been transcribed.

Runs right after build_site.py, reading the same new_sermons.json that
create_drive_docs.py consumes — so the transcript is in hand without re-fetching,
and without unreleased sermon text ever being committed to this public repo.

Only the snippet is updated. Privacy is never touched: a private draft stays
private until a human publishes it.

Required secrets:
  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
  GOOGLE_YOUTUBE_REFRESH_TOKEN     needs youtube.force-ssl
  ANTHROPIC_API_KEY
"""

import json
import os
import sys

try:
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
except ImportError:
    raise SystemExit("Run: pip install google-api-python-client google-auth")

try:
    import anthropic
except ImportError:
    raise SystemExit("Run: pip install anthropic")


NEW_SERMONS_FILE = "new_sermons.json"
CHURCH_LINE = "Eastpoint Church · Durham, NC · https://eastpointdurham.com"
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
DRY_RUN = os.environ.get("DRY_RUN") == "1"

# Enough transcript for the model to work from without paying for a whole hour
# of speech on every run.
TRANSCRIPT_CHARS = 14000


def youtube_service():
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_YOUTUBE_REFRESH_TOKEN")
    if not all([client_id, client_secret, refresh_token]):
        raise SystemExit(
            "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and "
            "GOOGLE_YOUTUBE_REFRESH_TOKEN must all be set."
        )
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/youtube.force-ssl"],
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


PROMPT = """You are writing the YouTube description for a sermon preached at \
Eastpoint Church, a church in Durham, North Carolina.

Sermon title: {title}
Scripture: {scripture}
Preacher: {preacher}

Transcript (may be truncated):
<transcript>
{transcript}
</transcript>

Write two things.

DESCRIPTION: 150-250 words. Open with two to four sentences of real substance \
about what this sermon actually argues - the specific text, the central claim, \
the turn it makes. Write plainly, the way a church speaks to its own people. No \
marketing voice, no clickbait, no hype, no emoji, no second-person exhortation. \
Do not invent quotes, statistics, or stories that are not in the transcript. If \
the transcript is too thin to describe honestly, say only what you can support.

TAGS: 10-15 comma-separated tags. Include the scripture book and reference, \
three to five themes actually discussed in this sermon, and the terms sermon, \
Eastpoint Church, Durham NC. Keep the whole comma-separated string under 400 \
characters.

Return exactly this format and nothing else:

DESCRIPTION:
<the description>

TAGS:
<comma separated tags>"""


def parse_model_output(text):
    """Pull the description and tags back out of the model's reply."""
    description, tags = "", []
    if "TAGS:" in text:
        desc_part, tag_part = text.rsplit("TAGS:", 1)
        tags = [t.strip() for t in tag_part.replace("\n", " ").split(",") if t.strip()]
    else:
        desc_part = text
    description = desc_part.replace("DESCRIPTION:", "", 1).strip()
    return description, tags


def trim_tags(tags, budget=460):
    out, seen, total = [], set(), 0
    for t in tags:
        t = t.replace(",", " ").strip()[:60]
        if not t or t.lower() in seen:
            continue
        if total + len(t) + 1 > budget:
            break
        seen.add(t.lower())
        out.append(t)
        total += len(t) + 1
    return out


def compose(sermon, client):
    transcript = (sermon.get("transcript") or "").strip()
    if not transcript:
        return None, None

    msg = client.messages.create(
        model=MODEL,
        max_tokens=1200,
        messages=[{
            "role": "user",
            "content": PROMPT.format(
                title=sermon.get("title", ""),
                scripture=sermon.get("scripture", "") or "not specified",
                preacher=sermon.get("preacher", "Peter Frey"),
                transcript=transcript[:TRANSCRIPT_CHARS],
            ),
        }],
    )
    description, tags = parse_model_output(msg.content[0].text)
    if not description:
        return None, None

    body = f"{description}\n"
    if sermon.get("scripture"):
        body += f"\nScripture: {sermon['scripture']}"
    if sermon.get("preacher"):
        body += f"\nPreacher: {sermon['preacher']}"
    body += f"\n\n{CHURCH_LINE}\n"
    return body[:4900], trim_tags(tags)


def update_video(youtube, video_id, description, tags):
    """Replace the snippet, preserving title and category. Privacy untouched."""
    current = youtube.videos().list(part="snippet", id=video_id).execute()
    items = current.get("items", [])
    if not items:
        print(f"  ! video {video_id} not found or not visible to these credentials")
        return False
    snippet = items[0]["snippet"]

    youtube.videos().update(
        part="snippet",
        body={
            "id": video_id,
            "snippet": {
                # videos.update replaces the whole snippet, so every field we
                # want to survive has to be sent back.
                "title": snippet["title"],
                "categoryId": snippet.get("categoryId", "29"),
                "description": description,
                "tags": tags,
                "defaultLanguage": snippet.get("defaultLanguage", "en"),
            },
        },
    ).execute()
    return True


def main():
    if not os.path.exists(NEW_SERMONS_FILE):
        print("No new_sermons.json — nothing to enrich.")
        return 0

    with open(NEW_SERMONS_FILE, encoding="utf-8") as f:
        sermons = json.load(f)

    todo = [s for s in sermons if s.get("transcript")]
    skipped = len(sermons) - len(todo)
    if skipped:
        print(f"{skipped} new sermon(s) have no transcript yet — leaving their "
              f"descriptions alone for now.")
    if not todo:
        return 0

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    youtube = None if DRY_RUN else youtube_service()

    for s in todo:
        print(f"\n=== {s.get('title', '?')} ({s['id']})")
        try:
            description, tags = compose(s, client)
        except Exception as e:
            print(f"  ! could not compose description: {e}")
            continue

        if not description:
            print("  ! model returned nothing usable; leaving as-is")
            continue

        print(f"  tags: {', '.join(tags)}")
        if DRY_RUN:
            print("  [dry run] description:")
            for line in description.splitlines():
                print("    " + line)
            continue

        try:
            if update_video(youtube, s["id"], description, tags):
                print("  description and tags updated (still private)")
        except Exception as e:
            print(f"  ! YouTube update failed: {e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
