#!/usr/bin/env python3
"""
Eastpoint Sermon Archive — Site Builder
Runs via GitHub Actions weekly to keep the search site current.

Reads from: sermons.json (existing transcript cache)
Writes to:  sermons.json (updated cache) + index.html (search site)

Environment variables:
  YOUTUBE_API_KEY  — YouTube Data API v3 key (stored as GitHub Secret)
  CHANNEL_ID       — YouTube channel ID (optional, defaults to Eastpoint's)
"""

import json
import os
import time
from datetime import datetime

try:
    from googleapiclient.discovery import build
except ImportError:
    raise SystemExit("Run: pip install google-api-python-client")

try:
    from youtube_transcript_api import (
        YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
    )
except ImportError:
    raise SystemExit("Run: pip install youtube-transcript-api")


API_KEY    = os.environ.get("YOUTUBE_API_KEY", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "UCu5frCUoNL0rOGCAClqHBFA")
DATA_FILE  = "sermons.json"

if not API_KEY:
    raise SystemExit("YOUTUBE_API_KEY environment variable not set.")


# ── YouTube helpers ────────────────────────────────────────────────────────────

def get_channel_videos(youtube):
    """Return (channel_name, list of video dicts) for all uploads."""
    resp = youtube.channels().list(
        part="contentDetails,snippet", id=CHANNEL_ID
    ).execute()
    if not resp.get("items"):
        raise SystemExit(f"Channel not found: {CHANNEL_ID}")

    channel_name = resp["items"][0]["snippet"]["title"]
    uploads_id   = resp["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    videos, page_token = [], None
    while True:
        pl = youtube.playlistItems().list(
            part="snippet", playlistId=uploads_id,
            maxResults=50, pageToken=page_token
        ).execute()

        for item in pl["items"]:
            s     = item["snippet"]
            title = s["title"]
            if "|" not in title:          # skip non-sermon clips / shorts
                continue
            parts = [p.strip() for p in title.split("|")]
            # Filter out Eastpoint branding from preacher field
            preacher = parts[2] if len(parts) > 2 else "Peter Frey"
            if "Eastpoint" in preacher or "Durham" in preacher:
                preacher = "Peter Frey"

            videos.append({
                "id":          s["resourceId"]["videoId"],
                "title":       parts[0],
                "scripture":   parts[1] if len(parts) > 1 else "",
                "preacher":    preacher,
                "date":        s["publishedAt"][:10],
                "description": s.get("description", "")[:400].replace("\n", " "),
                "url":         f"https://www.youtube.com/watch?v={s['resourceId']['videoId']}",
                "transcript":  None,
            })

        page_token = pl.get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.3)

    return channel_name, videos


def get_transcript(video_id):
    """Download transcript text for a video. Returns str or None."""
    try:
        entries = YouTubeTranscriptApi.get_transcript(video_id)
        return " ".join(e["text"] for e in entries)
    except TranscriptsDisabled:
        return None
    except NoTranscriptFound:
        pass
    except Exception:
        pass

    # Fall back to auto-generated captions
    try:
        tl = YouTubeTranscriptApi.list_transcripts(video_id)
        t  = tl.find_generated_transcript(["en"])
        return " ".join(e["text"] for e in t.fetch())
    except Exception:
        return None


# ── Series detection ───────────────────────────────────────────────────────────

SERIES_RULES = [
    (lambda s: s["scripture"].startswith("John") or s["scripture"].startswith("1 John") or s["scripture"].startswith("3 John"),            "John"),
    (lambda s: "Colossians" in s["scripture"],       "Colossians"),
    (lambda s: "Isaiah" in s["scripture"],           "Isaiah"),
    (lambda s: "Psalm" in s["scripture"],            "Psalms"),
    (lambda s: "Advent" in s["title"] or "Advent" in s["description"], "Advent"),
    (lambda s: "Together" in s["title"],             "Together"),
    (lambda s: any(x in s["title"] for x in ("Pray", "Prayer", "Stillness", "Hearing From God")), "Prayer"),
    (lambda s: any(x in s["title"] for x in ("Easter", "Resurrection", "Palm Sunday", "Pentecost")), "Special"),
]

def infer_series(sermon):
    for rule, name in SERIES_RULES:
        try:
            if rule(sermon):
                return name
        except Exception:
            pass
    return ""


# ── HTML generation ────────────────────────────────────────────────────────────

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Eastpoint Church — Sermon Archive</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#f5f4f1;--surface:#fff;--border:#e2e0da;
  --text:#1a1a18;--muted:#6b6965;
  --accent:#185fa5;--accent-bg:#e6f1fb;--accent-text:#0c447c;
  --tag-bg:#eeecea;--radius:10px;
}}
@media(prefers-color-scheme:dark){{
  :root{{
    --bg:#18181a;--surface:#232325;--border:#333336;
    --text:#e8e6e1;--muted:#8a8884;
    --accent:#4d9de0;--accent-bg:#0c2540;--accent-text:#7ec0f5;
    --tag-bg:#2a2a2c;
  }}
}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}}
header{{background:var(--surface);border-bottom:1px solid var(--border);padding:1.25rem 1.5rem;position:sticky;top:0;z-index:10}}
.hdr{{max-width:820px;margin:0 auto}}
.hdr-top{{display:flex;align-items:baseline;gap:1rem;margin-bottom:.9rem;flex-wrap:wrap}}
h1{{font-size:1rem;font-weight:600}}
.sub{{font-size:.78rem;color:var(--muted)}}
.sw{{position:relative}}
.sw svg{{position:absolute;left:10px;top:50%;transform:translateY(-50%);color:var(--muted);pointer-events:none}}
input[type=search]{{width:100%;padding:.5rem .75rem .5rem 2.2rem;border:1px solid var(--border);border-radius:var(--radius);background:var(--bg);color:var(--text);font-size:.9rem;outline:none;appearance:none}}
input[type=search]:focus{{border-color:var(--accent);box-shadow:0 0 0 3px rgba(24,95,165,.12)}}
input[type=search]::-webkit-search-cancel-button{{-webkit-appearance:none}}
.filters{{display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.7rem}}
.fb{{font-size:.73rem;padding:.22rem .65rem;border:1px solid var(--border);border-radius:20px;background:none;color:var(--muted);cursor:pointer;transition:all .15s}}
.fb:hover{{border-color:var(--accent);color:var(--accent)}}
.fb.on{{background:var(--accent-bg);border-color:var(--accent);color:var(--accent-text);font-weight:500}}
main{{max-width:820px;margin:0 auto;padding:1.25rem 1.5rem}}
.meta{{font-size:.78rem;color:var(--muted);margin-bottom:.9rem}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:1rem 1.2rem;margin-bottom:.7rem}}
.card:hover{{border-color:#bbb9b3}}
.ct{{font-size:.95rem;font-weight:600;line-height:1.4;margin-bottom:.2rem}}
.cm{{font-size:.75rem;color:var(--muted);margin-bottom:.55rem;display:flex;gap:.65rem;flex-wrap:wrap;align-items:center}}
.cd{{font-size:.82rem;color:var(--muted);line-height:1.6;margin-bottom:.7rem}}
.cf{{display:flex;align-items:center;gap:.65rem;flex-wrap:wrap}}
.tag{{font-size:.7rem;background:var(--tag-bg);color:var(--muted);border-radius:4px;padding:.12rem .45rem;white-space:nowrap}}
.sb{{font-size:.68rem;font-weight:500;border-radius:3px;padding:.1rem .4rem;background:var(--accent-bg);color:var(--accent-text)}}
.wl{{font-size:.76rem;color:var(--accent);text-decoration:none;display:inline-flex;align-items:center;gap:.3rem;margin-left:auto}}
.wl:hover{{text-decoration:underline}}
.hit{{background:#fff3a0;color:#4a3800;border-radius:2px;padding:0 1px}}
.tx-hit{{font-size:.78rem;color:var(--muted);line-height:1.6;margin-top:.4rem;margin-bottom:.3rem;border-left:2px solid var(--accent);padding-left:.5rem}}
@media(prefers-color-scheme:dark){{.hit{{background:#4a3800;color:#ffe066}}}}
.empty{{text-align:center;padding:3rem;color:var(--muted);font-size:.9rem}}
.stats{{font-size:.73rem;color:var(--muted);margin-top:.4rem}}
</style>
</head>
<body>
<header>
  <div class="hdr">
    <div class="hdr-top">
      <h1>Eastpoint Church — Sermons</h1>
      <span class="sub" id="total"></span>
    </div>
    <div class="sw">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
      <input type="search" id="q" placeholder="Search titles, scripture, topics, or anything you've preached…" oninput="render()"/>
    </div>
    <div class="filters" id="filters"></div>
  </div>
</header>
<main>
  <div class="meta" id="meta"></div>
  <div id="results"></div>
</main>
<script>
const DATA = {data_json};

const SERIES_ORDER = ["John","Colossians","Isaiah","Advent","Prayer","Together","Psalms","Special"];
const allSeries = [...new Set(DATA.filter(s=>s.series).map(s=>s.series))];
let active = "All";
const withTranscripts = DATA.filter(s=>s.transcript).length;

function fmt(d) {{
  return new Date(d+"T12:00:00").toLocaleDateString("en-US",{{month:"short",day:"numeric",year:"numeric"}});
}}
function esc(s) {{
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}}
function hi(text, q) {{
  if (!q) return esc(text);
  return esc(text).replace(new RegExp("("+q.replace(/[.*+?^${}()|[\\]\\\\]/g,"\\\\$&")+")","gi"),"<span class=hit>$1</span>");
}}
function excerpt(transcript, q, chars=220) {{
  if (!transcript || !q) return "";
  const lo = transcript.toLowerCase();
  const i  = lo.indexOf(q.toLowerCase());
  if (i < 0) return "";
  const start = Math.max(0, i-80);
  const end   = Math.min(transcript.length, i+chars);
  let snip = (start>0?"…":"")+transcript.slice(start,end).trim()+(end<transcript.length?"…":"");
  return hi(snip, q);
}}

function buildFilters() {{
  const ordered = SERIES_ORDER.filter(s=>allSeries.includes(s));
  const rest    = allSeries.filter(s=>!SERIES_ORDER.includes(s));
  const all     = ["All",...ordered,...rest];
  document.getElementById("filters").innerHTML = all.map(s=>
    `<button class="fb${{s===active?" on":""}}" onclick="setFilter('${{s}}')">${{s}}</button>`
  ).join("");
  const txLabel = withTranscripts === DATA.length
    ? `Full transcripts — ${{DATA.length}} sermons`
    : `${{withTranscripts}} of ${{DATA.length}} sermons have transcripts`;
  document.getElementById("total").textContent = txLabel;
}}

function setFilter(f) {{ active=f; buildFilters(); render(); }}

function render() {{
  const raw = document.getElementById("q").value.trim();
  const q   = raw.toLowerCase();

  let list = DATA.filter(s => {{
    if (active !== "All" && s.series !== active) return false;
    if (!q) return true;
    return (
      s.title.toLowerCase().includes(q)       ||
      s.scripture.toLowerCase().includes(q)   ||
      s.preacher.toLowerCase().includes(q)    ||
      s.series.toLowerCase().includes(q)      ||
      s.desc.toLowerCase().includes(q)        ||
      (s.transcript && s.transcript.toLowerCase().includes(q))
    );
  }});

  const meta = document.getElementById("meta");
  meta.textContent = (q || active!=="All")
    ? `${{list.length}} sermon${{list.length!==1?"s":""}} found`
    : "";

  if (!list.length) {{
    document.getElementById("results").innerHTML =
      "<div class=empty>No sermons match your search.</div>";
    return;
  }}

  document.getElementById("results").innerHTML = list.map(s => {{
    const txSnip = excerpt(s.transcript, raw);
    const txHtml = txSnip
      ? `<div class="tx-hit">"…${{txSnip}}…"</div>`
      : "";
    const seriesBadge = s.series ? `<span class="sb">${{esc(s.series)}}</span>` : "";
    return `<div class="card">
  <div class="ct">${{hi(s.title,raw)}}</div>
  <div class="cm"><span>${{fmt(s.date)}}</span><span>${{esc(s.preacher)}}</span>${{seriesBadge}}</div>
  <div class="cd">${{hi(s.desc,raw)}}</div>
  ${{txHtml}}
  <div class="cf">
    <span class="tag">${{esc(s.scripture)}}</span>
    <a class="wl" href="${{s.url}}" target="_blank">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><path d="M19.615 3.184c-3.604-.246-11.631-.245-15.23 0-3.897.266-4.356 2.62-4.385 8.816.029 6.185.484 8.549 4.385 8.816 3.6.245 11.626.246 15.23 0 3.897-.266 4.356-2.62 4.385-8.816-.029-6.185-.484-8.549-4.385-8.816zm-10.615 12.816v-8l8 3.993-8 4.007z"/></svg>
      Watch
    </a>
  </div>
</div>`;
  }}).join("");
}}

buildFilters();
render();
</script>
</body>
</html>
"""


def build_html(channel_name, sermons):
    js_data = []
    for s in sermons:
        js_data.append({
            "id":         s["id"],
            "date":       s["date"],
            "title":      s["title"],
            "scripture":  s.get("scripture", ""),
            "preacher":   s.get("preacher", "Peter Frey"),
            "series":     infer_series(s),
            "desc":       s.get("description", ""),
            "transcript": s.get("transcript") or "",
            "url":        s["url"],
        })
    data_json = json.dumps(js_data, ensure_ascii=False, separators=(",", ":"))
    generated = datetime.now().strftime("%B %d, %Y")
    return HTML_TEMPLATE.format(
        channel_name=channel_name,
        generated=generated,
        data_json=data_json,
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    youtube = build("youtube", "v3", developerKey=API_KEY)

    # Load existing transcript cache
    existing = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            for s in json.load(f):
                existing[s["id"]] = s
        print(f"Loaded {len(existing)} cached sermons from {DATA_FILE}")

    # Fetch current video list
    print("Fetching video list from YouTube…")
    channel_name, videos = get_channel_videos(youtube)
    print(f"Found {len(videos)} sermons on '{channel_name}'")

    # Merge: reuse cached transcripts, fetch only new ones
    new_count = 0
    for v in videos:
        cached = existing.get(v["id"])
        if cached and cached.get("transcript"):
            v["transcript"] = cached["transcript"]
        else:
            print(f"  Fetching transcript: {v['title'][:60]}")
            v["transcript"] = get_transcript(v["id"])
            if v["transcript"]:
                print(f"    ✓ {len(v['transcript'])} chars")
            else:
                print(f"    ✗ no transcript available")
            new_count += 1
            time.sleep(0.5)

    print(f"\nFetched {new_count} new transcripts | "
          f"{sum(1 for v in videos if v['transcript'])} total with transcripts")

    # Save updated cache
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(videos, f, indent=2, ensure_ascii=False)
    print(f"Saved {DATA_FILE}")

    # Build HTML
    html = build_html(channel_name, videos)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("Built index.html")
    print("\nDone.")


if __name__ == "__main__":
    main()
