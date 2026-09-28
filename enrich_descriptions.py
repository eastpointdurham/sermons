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
from upload_sermon import description_footer
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
DRY_RUN = os.environ.get("DRY_RUN") == "1"

# A whole sermon is ~8k tokens, so send all of it: the summary should cover
# where the sermon lands, not just how it opens. The cap only guards against a
# runaway transcript (e.g. a recording left running after the service).
TRANSCRIPT_CHARS = 200000


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

Write these things.

DESCRIPTION: 150-250 words. Open with two to four sentences of real substance \
about what this sermon actually argues - the specific text, the central claim, \
the turn it makes. Write plainly, the way a church speaks to its own people. No \
marketing voice, no clickbait, no hype, no emoji, no second-person exhortation. \
Do not invent quotes, statistics, or stories that are not in the transcript. If \
the transcript is too thin to describe honestly, say only what you can support.

{chapters_ask}

TAGS: 10-15 comma-separated tags. Include the scripture book and reference, \
three to five themes actually discussed in this sermon, and the terms sermon, \
Eastpoint Church, Durham NC. Keep the whole comma-separated string under 400 \
characters.

Return exactly this format and nothing else:

DESCRIPTION:
<the description>
{chapters_format}
TAGS:
<comma separated tags>"""

# Added when the caption timings are available.
CHAPTERS_ASK = """

CHAPTERS: YouTube chapters for viewers jumping around the video. 5-8 lines, each "M:SS Title" (or "H:MM:SS Title"), the first at 0:00. Take the times from the [m:ss] markers below; mark real turns: welcome, the scripture reading, each main movement of the sermon, the response. Titles of two to six words, in the sermon's own terms, no numbering.

Timed transcript:
<timed>
{timed}
</timed>"""
CHAPTERS_FORMAT = """
CHAPTERS:
<one chapter per line>
"""



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


def _clock(sec):
    sec = int(sec)
    h, m, s_ = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s_:02d}" if h else f"{m}:{s_:02d}"


def _secs(stamp):
    parts = [int(p) for p in stamp.split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def timed_captions(youtube, video_id):
    """[(start_seconds, text)] from the video's English caption track, or []."""
    import io
    import re
    from googleapiclient.http import MediaIoBaseDownload
    items = youtube.captions().list(part="snippet", videoId=video_id).execute().get("items", [])
    track = None
    for it in items:
        sn = it["snippet"]
        if sn["language"].startswith("en"):
            if sn["trackKind"] in ("standard", "forced"):
                track = it["id"]
                break
            if sn["trackKind"] == "asr" and not track:
                track = it["id"]
    if not track:
        return []
    fh = io.BytesIO()
    dl = MediaIoBaseDownload(fh, youtube.captions().download(id=track, tfmt="vtt"))
    done = False
    while not done:
        _, done = dl.next_chunk()
    return parse_vtt(fh.getvalue().decode("utf-8"))


def parse_vtt(vtt):
    import re
    cues, start, seen = [], None, set()
    for line in vtt.splitlines():
        m = re.match(r"(\d+):(\d{2}):(\d{2})\.\d+\s+-->|(\d{2}):(\d{2})\.\d+\s+-->", line)
        if m:
            g = m.groups()
            start = (int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2])) if g[0] else (int(g[3]) * 60 + int(g[4]))
            continue
        text = re.sub(r"<[^>]+>", "", line).strip()
        if start is None or not text or line.startswith("WEBVTT") or text in seen:
            continue
        seen.add(text)
        cues.append((start, text))
    return cues


def timed_digest(cues, every=20):
    """The captions in ~20-second lines, each led by its [m:ss] time."""
    out, block, t0 = [], [], None
    for t, text in cues:
        if t0 is None:
            t0 = t
        if t - t0 >= every and block:
            out.append(f"[{_clock(t0)}] {' '.join(block)}")
            block, t0 = [], t
        block.append(text)
    if block:
        out.append(f"[{_clock(t0)}] {' '.join(block)}")
    return "\n".join(out)


def parse_chapters(text, end=None):
    """Valid YouTube chapters from the model's lines, or [] when they would not
    work (YouTube needs 0:00 first, at least three, 10+ seconds apart)."""
    import re
    got = []
    for line in (text or "").splitlines():
        m = re.match(r"\s*[-*]?\s*(\d{1,2}(?::\d{2}){1,2})\s*[-–—:]?\s+(.+?)\s*$", line)
        if m:
            got.append((_secs(m.group(1)), m.group(2).strip()))
    got.sort()
    if not got or got[0][0] > 60:
        return []
    got[0] = (0, got[0][1])                    # the first chapter must start at 0:00
    out = [got[0]]
    for t, title in got[1:]:
        if t - out[-1][0] >= 10 and (end is None or t < end - 10):
            out.append((t, title))
    return out if len(out) >= 3 else []


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


def compose(sermon, client, cues=None):
    transcript = (sermon.get("transcript") or "").strip()
    if not transcript:
        return None, None

    timed = timed_digest(cues) if cues else ""
    msg = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,      # room for adaptive thinking before the description
        # If a safety classifier declines, re-run on Anthropic's recommended model.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{
            "role": "user",
            "content": PROMPT.format(
                title=sermon.get("title", ""),
                scripture=sermon.get("scripture", "") or "not specified",
                preacher=sermon.get("preacher", "Peter Frey"),
                transcript=transcript[:TRANSCRIPT_CHARS],
                chapters_ask=CHAPTERS_ASK.format(timed=timed[:TRANSCRIPT_CHARS]) if timed else "",
                chapters_format=CHAPTERS_FORMAT if timed else "",
            ),
        }],
    )
    if msg.stop_reason == "refusal":
        return None, None
    text = "".join(b.text for b in msg.content if b.type == "text")
    chapters = []
    if "CHAPTERS:" in text:
        head, rest = text.split("CHAPTERS:", 1)
        ch, sep, tail = rest.partition("TAGS:")
        chapters = parse_chapters(ch, end=cues[-1][0] if cues else None)
        text = head + sep + tail
    description, tags = parse_model_output(text)
    if not description:
        return None, None

    body = f"{description}\n"
    if chapters:
        body += "\nChapters\n" + "\n".join(f"{_clock(t)} {title}" for t, title in chapters) + "\n"
    if sermon.get("scripture"):
        body += f"\nScripture: {sermon['scripture']}"
    if sermon.get("preacher"):
        body += f"\nPreacher: {sermon['preacher']}"
    body += f"\n\n{description_footer(sermon.get('scripture', ''))}\n"
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
        cues = []
        if youtube is not None:
            try:
                cues = timed_captions(youtube, s["id"])
            except Exception as e:
                print(f"  ! no caption timings, so no chapters: {e}")
        try:
            description, tags = compose(s, client, cues)
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


def redo(video_id):
    """Rewrite one video's description from its captions (e.g. after the format
    changes). Title, category and privacy are kept."""
    youtube = youtube_service()
    snip = youtube.videos().list(part="snippet", id=video_id).execute()["items"][0]["snippet"]
    parts = [p.strip() for p in snip["title"].split("|")]
    cues = timed_captions(youtube, video_id)
    if not cues:
        raise SystemExit(f"No captions for {video_id} yet")
    sermon = {"id": video_id, "title": parts[0],
              "scripture": parts[1] if len(parts) > 1 else "",
              "preacher": parts[2] if len(parts) > 2 else "Peter Frey",
              "transcript": " ".join(t for _, t in cues)}
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    description, tags = compose(sermon, client, cues)
    if not description:
        raise SystemExit("The model returned nothing usable")
    print(description)
    if update_video(youtube, video_id, description, tags):
        print("\ndescription and tags updated (privacy unchanged)")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--video":
        sys.exit(redo(sys.argv[2]))
    sys.exit(main())
