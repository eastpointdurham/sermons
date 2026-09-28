#!/usr/bin/env python3
"""
Upload new sermon recordings from Google Drive to YouTube as private drafts,
and drop a podcast-ready MP3 back into Drive.

One download feeds both outputs. Nothing is ever published: every upload is
privacyStatus=private for a human to review in YouTube Studio.

Once the draft exists on the channel, the hourly update_sermons.yml job picks it
up (it lists private uploads via OAuth), transcribes it, and files the transcript
in Drive — so the description can be enriched before anyone hits publish.

Environment:
  GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET      OAuth client
  GOOGLE_DRIVE_REFRESH_TOKEN                   Drive + Docs read/write
  GOOGLE_YOUTUBE_REFRESH_TOKEN                 YouTube upload
  SERMON_FOLDER_ID / PODCAST_FOLDER_ID / PLANNING_DOC_ID
  PLANNING_DOC_START_YEAR                      year of the doc's first dated line (default 2025)
  MAX_PER_RUN                                  default 1
  DRY_RUN                                      "1" to plan without uploading
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

STATE_FILE = "uploaded_sermons.json"

CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
DRIVE_TOKEN   = os.environ.get("GOOGLE_DRIVE_REFRESH_TOKEN")
YT_TOKEN      = os.environ.get("GOOGLE_YOUTUBE_REFRESH_TOKEN")

SERMON_FOLDER_ID  = os.environ.get("SERMON_FOLDER_ID", "1SCMlaqWua24gPPU-Z7yF19pisehp3xu_")
PODCAST_FOLDER_ID = os.environ.get("PODCAST_FOLDER_ID", "1azEoTNVdrd1oLnBAsCMZqNby4T4417qz")
PLANNING_DOC_ID   = os.environ.get("PLANNING_DOC_ID", "1LZDGqW9G9uNWCCwMjv8u3kwQHlE6azpLQz96ynHOM6o")
START_YEAR        = int(os.environ.get("PLANNING_DOC_START_YEAR", "2025"))
MAX_PER_RUN       = int(os.environ.get("MAX_PER_RUN", "1"))
DRY_RUN           = os.environ.get("DRY_RUN") == "1"
# Custom thumbnails (thumbnail.py). Off: the team designs its own. Set the
# AUTO_THUMBNAILS repository variable to "1" to turn them back on.
AUTO_THUMBNAILS   = os.environ.get("AUTO_THUMBNAILS") == "1"
# Recordings dated before this are ignored, so turning the uploader on does not
# re-upload sermons that already went to YouTube by hand. YYYY-MM-DD.
UPLOAD_SINCE      = os.environ.get("UPLOAD_SINCE", "")

CHURCH_NAME = "Eastpoint Church"
DEFAULT_PREACHER = "Peter Frey"
YOUTUBE_CATEGORY_ID = "29"   # Nonprofits & Activism

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents.readonly",
]
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------

def creds(refresh_token, scopes, label):
    if not all([CLIENT_ID, CLIENT_SECRET, refresh_token]):
        raise SystemExit(
            f"Missing OAuth credentials for {label}. Needs GOOGLE_CLIENT_ID, "
            f"GOOGLE_CLIENT_SECRET and the {label} refresh token."
        )
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=scopes,
    )


def drive_service():
    return build("drive", "v3", credentials=creds(DRIVE_TOKEN, DRIVE_SCOPES, "Drive"),
                 cache_discovery=False)


def docs_service():
    return build("docs", "v1", credentials=creds(DRIVE_TOKEN, DRIVE_SCOPES, "Drive"),
                 cache_discovery=False)


def youtube_service():
    return build("youtube", "v3", credentials=creds(YT_TOKEN, YOUTUBE_SCOPES, "YouTube"),
                 cache_discovery=False)


# --------------------------------------------------------------------------
# filename + planning doc parsing
# (ported from the Apps Script prototype, same test cases)
# --------------------------------------------------------------------------

def parse_date_from_filename(name):
    base = re.sub(r"\.[A-Za-z0-9]+$", "", name)
    m = re.search(r"(?:^|[^0-9])(\d{1,2})[-._/](\d{1,2})[-._/](\d{2,4})(?![0-9])", base)
    if not m:
        return None
    mo, da, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if yr < 100:
        yr += 2000
    try:
        return date(yr, mo, da)
    except ValueError:
        return None


def is_sermon_video(name, mime_type):
    if not (mime_type or "").startswith("video/"):
        return False
    if re.match(r"^copy of ", name, re.I):
        return False
    if re.search(r"\breels?\b", name, re.I):
        return False
    if re.search(r"\b(clip|short|promo|invite|teaser|trailer|bumper)\b", name, re.I):
        return False
    # No \b around "sermon": "Sermon_08-09-26" uses underscores, which are word chars.
    if not re.search(r"sermon", name, re.I):
        return False
    return parse_date_from_filename(name) is not None


def on_or_after_cutoff(service_date, since):
    """True when there is no cutoff, or the service date is on/after it."""
    if not since:
        return True
    try:
        return service_date >= date.fromisoformat(since)
    except ValueError:
        raise SystemExit(f"UPLOAD_SINCE must be YYYY-MM-DD, got {since!r}")


def match_doc_date_line(text):
    m = re.match(
        r"^\**\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s*\**\s*(\d{1,2})\b",
        text.strip(), re.I)
    if not m:
        return None
    month = MONTHS.get(m.group(1).lower())
    if not month:
        return None
    return month, int(m.group(2)), len(m.group(0))


SCRIPTURE_RE = re.compile(
    r"((?:[1-3]\s*)?(?:[A-Z][a-z]+)\.?\s+\d+(?::\d+(?:\s*[-–—]\s*\d+)?)?(?:\s*[-–—]\s*\d+:\d+)?)")

GUEST_RE = re.compile(
    r"(?:guest\s+preacher\s*[-–—:]?\s*|(\b[A-Z][a-z]+\s+[A-Z][a-z]+\b)\s+preaching)",
    re.I)


def extract_preacher(raw_line):
    """Pull a guest preacher's name out of a planning-doc line, if named.

    Handles 'Guest Preacher - Chris Hankins', 'Kevin Preaching',
    '(Peter out, Kevin Preaching)'. Returns the default preacher otherwise.
    """
    text = raw_line.replace("*", "")

    m = re.search(r"guest\s+preacher\s*[-–—:(]*\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", text, re.I)
    if m:
        return m.group(1).strip()

    m = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+[Pp]reaching\b", text)
    if m:
        return m.group(1).strip()

    return DEFAULT_PREACHER


def normalise_scripture(s):
    """Match the archive's existing convention: en-dash in verse ranges."""
    return re.sub(r"(\d)\s*-\s*(\d)", "\\1\u2013\\2", s).strip()


def split_entry(rest):
    s = re.sub(r"\*+", "", rest)
    s = re.sub(r"^[\s\-–—:]+", "", s).strip()
    s = re.sub(r"\b(NIV|ESV|NLT|MSG|KJV|NASB|CSB)\b", "", s).strip()

    scripture = ""
    m = SCRIPTURE_RE.search(s)
    if m and m.group(1).split()[0].rstrip(".").lower() not in MONTHS:
        scripture = re.sub(r"\s+", " ", m.group(1)).strip()
        s = s[:m.start(1)] + " " + s[m.end(1):]

    # who is preaching is not part of the title (extract_preacher reads it)
    s = re.sub(r"guest\s+preacher\s*[-–—:]*\s*(\([^)]*\)|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)?",
               " ", s, flags=re.I)
    s = re.sub(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+[Pp]reaching\b", " ", s)

    title = re.sub(r"\([^)]*\)", " ", s)
    title = title.replace("\u201c", "").replace("\u201d", "").replace('"', "")
    title = re.sub(r"\s*[-–—]\s*", " - ", title)
    title = re.sub(r"(\s-\s*)+-", " -", title)              # "a - - b" left by removals
    title = re.sub(r"^[\s\-–—:]+|[\s\-–—:]+$", "", title)
    title = re.sub(r"\s{2,}", " ", title).strip()
    title = re.sub(r"(?<!\.)\.$", "", title)                  # "Asked For." but keep "..."
    return title, normalise_scripture(scripture)


def read_planning_doc(docs, doc_id=None):
    """Return {date: {title, scripture, series, preacher}} for every dated line.

    Years are implicit in the doc, so we walk it in order and roll forward each
    time the month goes backwards. START_YEAR anchors the first dated line; a
    line that is only a year (the doc's "**2025**" heading) resets it, since
    newer series are added at the top of the doc.
    """
    doc = docs.documents().get(documentId=doc_id or PLANNING_DOC_ID).execute()

    entries, year, prev_month, series = {}, START_YEAR, None, ""

    for element in doc.get("body", {}).get("content", []):
        para = element.get("paragraph")
        if not para:
            continue
        runs = para.get("elements", [])
        text = "".join(r.get("textRun", {}).get("content", "") for r in runs).strip()
        if not text:
            continue

        heading = re.fullmatch(r"(20\d\d)", text.replace("*", "").strip())
        if heading:
            year, prev_month = int(heading.group(1)), None
            continue

        hit = match_doc_date_line(text)
        if not hit:
            bold = [r.get("textRun", {}).get("textStyle", {}).get("bold")
                    for r in runs if r.get("textRun", {}).get("content", "").strip()]
            if bold and all(bold) and 5 < len(text) < 200:
                series = re.sub(r"\([^)]*\)", " ", text.replace("*", ""))
                series = re.sub(r"\s{2,}", " ", series).strip(" :-–—")
            continue

        month, day, consumed = hit
        if prev_month is not None and month < prev_month:
            year += 1
        prev_month = month

        title, scripture = split_entry(text[consumed:])
        try:
            key = date(year, month, day)
        except ValueError:
            continue
        if key not in entries:
            entries[key] = {
                "title": title,
                "scripture": scripture,
                "series": series,
                "preacher": extract_preacher(text),
            }
    return entries


# --------------------------------------------------------------------------
# metadata
# --------------------------------------------------------------------------

TITLE_SUFFIX = "Eastpoint Church Durham"
MAX_TITLE = 100                      # YouTube's limit
# When a title runs long, the church name gives way a word at a time before
# the sermon title is shortened.
SUFFIX_FALLBACKS = ("Eastpoint Church Durham", "Eastpoint Church", "Eastpoint", None)


def build_title(entry, service_date):
    """`Title | Scripture | Preacher | Eastpoint Church Durham`. build_site.py
    parses the first three segments. Over 100 characters, drop "Durham", then
    "Church", then "Eastpoint", and only then shorten the sermon title."""
    if entry and entry["title"]:
        head = entry["title"]
    else:       # not the scripture: it is already the next segment
        head = "Sunday Service " + service_date.strftime("%B %-d, %Y")

    # Always emit the three segments build_site.py reads. It takes parts[1] as
    # the scripture and parts[2] as the preacher; with only two segments it would
    # take the preacher's name as the scripture reference and then fall back to
    # the default preacher — wrong on both counts.
    scripture = entry["scripture"] if entry and entry["scripture"] else ""
    parts = [head, scripture, entry["preacher"] if entry else DEFAULT_PREACHER]

    for suffix in SUFFIX_FALLBACKS:
        title = " | ".join(parts + ([suffix] if suffix else []))
        if len(title) <= MAX_TITLE:
            return title

    # Still too long with no church name: trim the sermon title only.
    room = MAX_TITLE - (len(title) - len(head)) - 1
    parts[0] = head[:max(room, 10)].rstrip()
    return " | ".join(parts)[:MAX_TITLE]


def build_description(entry, service_date):
    pretty = service_date.strftime("%B %-d, %Y")
    lines = []
    if entry and entry["title"]:
        lines.append(entry["title"])
    lines.append(f"Preached {pretty} at {CHURCH_NAME} in Durham, NC.")
    lines.append("")
    if entry and entry["scripture"]:
        lines.append(f"Scripture: {entry['scripture']}")
    if entry and entry["series"]:
        lines.append(f"Series: {entry['series']}")
    lines += ["", f"{CHURCH_NAME} · Durham, NC · https://eastpointdurham.com"]
    return "\n".join(lines)[:4900]


def build_tags(entry):
    tags = ["sermon", CHURCH_NAME, "Durham NC", "church", "Christian"]
    if entry:
        if entry["series"]:
            tags.append(re.sub(r'["\']', "", entry["series"])[:40])
        if entry["scripture"]:
            tags.append(entry["scripture"])
            book = re.match(r"((?:[1-3]\s*)?[A-Z][a-z]+)", entry["scripture"])
            if book:
                tags.append(book.group(1))
    out, seen, total = [], set(), 0
    for t in tags:
        t = t.replace(",", " ").strip()[:60]
        if not t or t.lower() in seen or total + len(t) + 1 > 480:
            continue
        seen.add(t.lower())
        out.append(t)
        total += len(t) + 1
    return out


# --------------------------------------------------------------------------
# drive + youtube io
# --------------------------------------------------------------------------

def list_folder(drive, folder_id):
    out, token = [], None
    while True:
        resp = drive.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="nextPageToken, files(id,name,mimeType,size,createdTime)",
            pageSize=200, supportsAllDrives=True, includeItemsFromAllDrives=True,
            pageToken=token,
        ).execute()
        out.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            return out


def download(drive, file_id, dest):
    req = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(dest, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req, chunksize=64 * 1024 * 1024)
        done, last = False, -1
        while not done:
            status, done = dl.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                if pct >= last + 20:
                    print(f"    download {pct}%", flush=True)
                    last = pct


def upload_to_youtube(youtube, path, title, description, tags):
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": YOUTUBE_CATEGORY_ID,
        },
        "status": {
            "privacyStatus": "private",          # draft, for human review
            "selfDeclaredMadeForKids": False,
            "embeddable": True,
        },
    }
    media = MediaFileUpload(path, chunksize=32 * 1024 * 1024, resumable=True,
                            mimetype="video/*")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response, last = None, -1
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            if pct >= last + 20:
                print(f"    upload {pct}%", flush=True)
                last = pct
    return response["id"]


def extract_audio(video_path, mp3_path):
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", video_path, "-vn", "-ac", "1", "-b:a", "96k",
        "-codec:a", "libmp3lame", mp3_path,
    ], check=True)


def upload_file(drive, path, name, folder_id, mime):
    return drive.files().create(
        body={"name": name, "parents": [folder_id]},
        media_body=MediaFileUpload(path, mimetype=mime, resumable=True),
        fields="id,webViewLink", supportsAllDrives=True,
    ).execute()


# --------------------------------------------------------------------------

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def main():
    state = load_state()
    done_ids = {s["drive_file_id"] for s in state}

    drive = drive_service()
    videos = [f for f in list_folder(drive, SERMON_FOLDER_ID)
              if is_sermon_video(f["name"], f.get("mimeType"))]
    videos.sort(key=lambda f: f["createdTime"])

    todo = [v for v in videos if v["id"] not in done_ids
            and on_or_after_cutoff(parse_date_from_filename(v["name"]), UPLOAD_SINCE)]
    if not todo:
        print("No new sermon videos.")
        return 0

    print(f"{len(todo)} new sermon video(s); processing up to {MAX_PER_RUN}.")
    plan = read_planning_doc(docs_service())
    print(f"Planning doc: {len(plan)} dated entries.")

    youtube = None if DRY_RUN else youtube_service()

    for video in todo[:MAX_PER_RUN]:
        service_date = parse_date_from_filename(video["name"])
        entry = plan.get(service_date)
        if not entry:
            print(f"  ! No planning-doc line for {service_date} — generic title. "
                  f"Fix it in YouTube Studio before publishing.")
        elif not entry["scripture"]:
            print(f"  ! No scripture reference for {service_date}; the title will "
                  f"have an empty middle segment. Fix in Studio before publishing.")
        if entry and not entry["title"]:
            print(f"  ! No sermon title in the planning doc for {service_date}; using "
                  f"\"Sunday Service ...\". Fix in Studio before publishing.")

        title = build_title(entry, service_date)
        description = build_description(entry, service_date)
        tags = build_tags(entry)
        size_gb = int(video.get("size", 0)) / 1e9

        print(f"\n=== {video['name']} ({size_gb:.2f} GB)")
        print(f"    title: {title}")
        print(f"    tags:  {', '.join(tags)}")

        if DRY_RUN:
            print("    [dry run — nothing uploaded]")
            print("    description:")
            for line in description.splitlines():
                print("      " + line)
            continue

        stem = f"Sermon {service_date.isoformat()}"
        with tempfile.TemporaryDirectory() as tmp:
            ext = os.path.splitext(video["name"])[1] or ".mp4"
            video_path = os.path.join(tmp, "input" + ext)
            mp3_path = os.path.join(tmp, f"{stem}.mp3")

            print("  downloading from Drive…", flush=True)
            download(drive, video["id"], video_path)

            print("  uploading to YouTube (private)…", flush=True)
            video_id = upload_to_youtube(youtube, video_path, title, description, tags)
            print(f"  draft: https://studio.youtube.com/video/{video_id}/edit")

            print("  extracting audio…", flush=True)
            extract_audio(video_path, mp3_path)
            print(f"  audio {os.path.getsize(mp3_path) / 1e6:.1f} MB", flush=True)
            os.remove(video_path)          # runners only have so much disk

            up = upload_file(drive, mp3_path, f"{stem}.mp3", PODCAST_FOLDER_ID, "audio/mpeg")
            print(f"  mp3 in Drive: {up.get('webViewLink')}")

        if AUTO_THUMBNAILS:                    # paused: the team makes its own
            import thumbnail                   # never raises; logs and moves on
            thumbnail.add_thumbnail(drive, youtube, entry, service_date, video_id,
                                    SERMON_FOLDER_ID)

        state.append({
            "drive_file_id": video["id"],
            "drive_file_name": video["name"],
            "service_date": service_date.isoformat(),
            "video_id": video_id,
            "title": title,
        })
        save_state(state)

    remaining = len(todo) - min(len(todo), MAX_PER_RUN)
    if remaining:
        print(f"\n{remaining} still pending; the next run picks them up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
