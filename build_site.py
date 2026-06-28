#!/usr/bin/env python3
"""
Eastpoint Sermon Archive -- Site Builder
Environment variables:
  YOUTUBE_API_KEY  -- YouTube Data API v3 key
  CHANNEL_ID       -- YouTube channel ID (optional)
"""

import json, os, time, sys

try:
    from googleapiclient.discovery import build
except ImportError:
    raise SystemExit("Run: pip3 install google-api-python-client")

try:
    from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled
except ImportError:
    raise SystemExit("Run: pip3 install youtube-transcript-api")


API_KEY    = os.environ.get("YOUTUBE_API_KEY", "")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "UCu5frCUoNL0rOGCAClqHBFA")
DATA_FILE  = "sermons.json"

if not API_KEY:
    raise SystemExit("YOUTUBE_API_KEY environment variable not set.")


def get_channel_videos(youtube):
    resp = youtube.channels().list(part="contentDetails,snippet", id=CHANNEL_ID).execute()
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
            s = item["snippet"]
            title = s["title"]
            if "|" not in title:
                continue
            parts    = [p.strip() for p in title.split("|")]
            preacher = parts[2] if len(parts) > 2 else "Peter Frey"
            if any(x in preacher for x in ("Eastpoint", "Durham", "Easter", "Advent")):
                preacher = "Peter Frey"
            videos.append({
                "id":          s["resourceId"]["videoId"],
                "title":       parts[0],
                "scripture":   parts[1] if len(parts) > 1 else "",
                "preacher":    preacher,
                "date":        s["publishedAt"][:10],
                "description": s.get("description", "")[:400].replace("\n", " "),
                "url":         "https://www.youtube.com/watch?v=" + s["resourceId"]["videoId"],
                "transcript":  None,
            })
        page_token = pl.get("nextPageToken")
        if not page_token:
            break
        time.sleep(0.3)
    return channel_name, videos


def get_transcript(video_id):
    try:
        api = YouTubeTranscriptApi()
        return " ".join(e.text for e in api.fetch(video_id))
    except TranscriptsDisabled:
        return None
    except Exception as e:
        print(f"      transcript-api error: {e}")
    try:
        api = YouTubeTranscriptApi()
        tl  = api.list(video_id)
        t   = tl.find_generated_transcript(["en"])
        return " ".join(e.text for e in t.fetch())
    except Exception as e:
        print(f"      auto-caption error: {e}")
    try:
        import subprocess, tempfile, glob, re
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                [sys.executable, "-m", "yt_dlp",
                 "--skip-download", "--write-auto-sub",
                 "--sub-lang", "en", "--sub-format", "vtt",
                 "-o", f"{tmp}/%(id)s",
                 f"https://www.youtube.com/watch?v={video_id}"],
                capture_output=True, text=True, timeout=60
            )
            vtt_files = glob.glob(f"{tmp}/*.vtt")
            if vtt_files:
                raw = open(vtt_files[0]).read()
                lines, seen = [], set()
                for line in raw.splitlines():
                    if "-->" in line or line.startswith("WEBVTT") or not line.strip():
                        continue
                    clean = re.sub(r"<[^>]+>", "", line).strip()
                    if clean and clean not in seen:
                        seen.add(clean)
                        lines.append(clean)
                return " ".join(lines)
    except Exception as e:
        print(f"      yt-dlp error: {e}")
    return None


SERIES_RULES = [
    (lambda s: s["scripture"].startswith("John ") or s["scripture"].startswith("1 John"), "John"),
    (lambda s: "Colossians" in s["scripture"], "Colossians"),
    (lambda s: "Isaiah"     in s["scripture"], "Isaiah"),
    (lambda s: "Psalm"      in s["scripture"], "Psalms"),
    (lambda s: "Advent"  in s["title"] or "Advent"  in s["description"], "Advent"),
    (lambda s: "Together" in s["title"], "Together"),
    (lambda s: any(x in s["title"] for x in ("Pray","Prayer","Stillness","Hearing From God")), "Prayer"),
    (lambda s: any(x in s["title"] for x in ("Resurrection","Palm Sunday","Pentecost")), "Special"),
]

def infer_series(s):
    for rule, name in SERIES_RULES:
        try:
            if rule(s): return name
        except Exception:
            pass
    return ""


# NOTE: This template uses only single-quoted JS strings to avoid Python escape issues.
# HTML attributes inside JS strings use double quotes (safe inside single-quoted JS strings).
HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Eastpoint Church - Sermon Archive</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f5f4f1;--surface:#fff;--border:#e2e0da;
  --text:#1a1a18;--muted:#6b6965;
  --accent:#185fa5;--accent-bg:#e6f1fb;--accent-text:#0c447c;
  --tag-bg:#eeecea;--radius:10px;
}
@media(prefers-color-scheme:dark){:root{
  --bg:#18181a;--surface:#232325;--border:#333336;
  --text:#e8e6e1;--muted:#8a8884;
  --accent:#4d9de0;--accent-bg:#0c2540;--accent-text:#7ec0f5;
  --tag-bg:#2a2a2c;
}}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
header{background:var(--surface);border-bottom:1px solid var(--border);padding:1.25rem 1.5rem;position:sticky;top:0;z-index:10}
.hdr{max-width:820px;margin:0 auto}
.hdr-top{display:flex;align-items:baseline;gap:1rem;margin-bottom:.9rem;flex-wrap:wrap}
h1{font-size:1rem;font-weight:600}
.sub{font-size:.78rem;color:var(--muted)}
.sw{position:relative}
.sw svg{position:absolute;left:10px;top:50%;transform:translateY(-50%);color:var(--muted);pointer-events:none}
input[type=search]{width:100%;padding:.5rem .75rem .5rem 2.2rem;border:1px solid var(--border);border-radius:var(--radius);background:var(--bg);color:var(--text);font-size:.9rem;outline:none;appearance:none}
input[type=search]:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(24,95,165,.12)}
input[type=search]::-webkit-search-cancel-button{-webkit-appearance:none}
.filters{display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.7rem}
.fb{font-size:.73rem;padding:.22rem .65rem;border:1px solid var(--border);border-radius:20px;background:none;color:var(--muted);cursor:pointer}
.fb:hover{border-color:var(--accent);color:var(--accent)}
.fb.on{background:var(--accent-bg);border-color:var(--accent);color:var(--accent-text);font-weight:500}
main{max-width:820px;margin:0 auto;padding:1.25rem 1.5rem}
.meta{font-size:.78rem;color:var(--muted);margin-bottom:.9rem}
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:1rem 1.2rem;margin-bottom:.7rem}
.ct{font-size:.95rem;font-weight:600;line-height:1.4;margin-bottom:.2rem}
.cm{font-size:.75rem;color:var(--muted);margin-bottom:.55rem;display:flex;gap:.65rem;flex-wrap:wrap}
.cd{font-size:.82rem;color:var(--muted);line-height:1.6;margin-bottom:.7rem}
.cf{display:flex;align-items:center;gap:.65rem;flex-wrap:wrap}
.tag{font-size:.7rem;background:var(--tag-bg);color:var(--muted);border-radius:4px;padding:.12rem .45rem}
.sb{font-size:.68rem;font-weight:500;border-radius:3px;padding:.1rem .4rem;background:var(--accent-bg);color:var(--accent-text)}
.wl{font-size:.76rem;color:var(--accent);text-decoration:none}
.wl:hover{text-decoration:underline}
.tb{font-size:.76rem;color:var(--accent);background:none;border:none;cursor:pointer;padding:0;margin-left:auto}
.tb:hover{text-decoration:underline}
.hit{background:#fff3a0;color:#4a3800;border-radius:2px;padding:0 1px}
.tx-hit{font-size:.78rem;color:var(--muted);line-height:1.6;margin-top:.4rem;margin-bottom:.3rem;border-left:2px solid var(--accent);padding-left:.5rem}
.tx-full{display:none;margin-top:.75rem;padding:.75rem;background:var(--bg);border-radius:6px;border:1px solid var(--border);font-size:.78rem;line-height:1.8;color:var(--text);white-space:pre-wrap;max-height:400px;overflow-y:auto}
.tx-full.open{display:block}
@media(prefers-color-scheme:dark){.hit{background:#4a3800;color:#ffe066}}
.empty{text-align:center;padding:3rem;color:var(--muted);font-size:.9rem}
</style>
</head>
<body>
<header>
  <div class="hdr">
    <div class="hdr-top">
      <h1>Eastpoint Church - Sermons</h1>
      <span class="sub" id="total"></span>
    </div>
    <div class="sw">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
      <input type="search" id="q" placeholder="Search titles, scripture, topics, or anything you have preached" oninput="render()"/>
    </div>
    <div class="filters" id="filters"></div>
  </div>
</header>
<main>
  <div class="meta" id="meta"></div>
  <div id="results"></div>
</main>
<script>
var DATA = __DATA_JSON__;
var SERIES_ORDER = ['John','Colossians','Isaiah','Advent','Prayer','Together','Psalms','Special'];
var allSeries = [];
for (var i = 0; i < DATA.length; i++) {
  if (DATA[i].series && allSeries.indexOf(DATA[i].series) < 0) {
    allSeries.push(DATA[i].series);
  }
}
var active = 'All';
var withTranscripts = 0;
for (var i = 0; i < DATA.length; i++) { if (DATA[i].transcript) withTranscripts++; }

function fmt(d) {
  return new Date(d + 'T12:00:00').toLocaleDateString('en-US', {month:'short',day:'numeric',year:'numeric'});
}

function esc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function hi(text, q) {
  if (!q) { return esc(text); }
  var escaped = esc(text);
  var lower = escaped.toLowerCase();
  var ql = q.toLowerCase();
  var out = '';
  var i = 0;
  while (i < escaped.length) {
    var j = lower.indexOf(ql, i);
    if (j < 0) { out += escaped.slice(i); break; }
    out += escaped.slice(i, j) + '<span class=hit>' + escaped.slice(j, j + ql.length) + '</span>';
    i = j + ql.length;
  }
  return out;
}

function excerpt(transcript, q) {
  if (!transcript || !q) { return ''; }
  var lo = transcript.toLowerCase();
  var idx = lo.indexOf(q.toLowerCase());
  if (idx < 0) { return ''; }
  var start = Math.max(0, idx - 80);
  var end = Math.min(transcript.length, idx + 220);
  return (start > 0 ? '...' : '') + hi(transcript.slice(start, end).trim(), q) + (end < transcript.length ? '...' : '');
}

function toggleTx(btn) {
  var card = btn.parentNode.parentNode;
  var box = card.querySelector('.tx-full');
  var isOpen = box.classList.toggle('open');
  btn.textContent = isOpen ? 'Hide transcript' : 'Show transcript';
}

function buildFilters() {
  var ordered = [];
  for (var i = 0; i < SERIES_ORDER.length; i++) {
    if (allSeries.indexOf(SERIES_ORDER[i]) >= 0) { ordered.push(SERIES_ORDER[i]); }
  }
  var rest = [];
  for (var i = 0; i < allSeries.length; i++) {
    if (SERIES_ORDER.indexOf(allSeries[i]) < 0) { rest.push(allSeries[i]); }
  }
  var all = ['All'].concat(ordered).concat(rest);
  var el = document.getElementById('filters');
  el.innerHTML = '';
  for (var i = 0; i < all.length; i++) {
    var btn = document.createElement('button');
    btn.className = all[i] === active ? 'fb on' : 'fb';
    btn.textContent = all[i];
    (function(name) { btn.onclick = function() { setFilter(name); }; })(all[i]);
    el.appendChild(btn);
  }
  document.getElementById('total').textContent = withTranscripts === DATA.length
    ? 'Full transcripts - ' + DATA.length + ' sermons'
    : DATA.length + ' sermons' + (withTranscripts ? ' (' + withTranscripts + ' with transcripts)' : '');
}

function setFilter(f) { active = f; buildFilters(); render(); }

function render() {
  var raw = document.getElementById('q').value.trim();
  var q = raw.toLowerCase();
  var list = [];
  for (var i = 0; i < DATA.length; i++) {
    var s = DATA[i];
    if (active !== 'All' && s.series !== active) { continue; }
    if (!q) { list.push(s); continue; }
    if (s.title.toLowerCase().indexOf(q) >= 0 ||
        s.scripture.toLowerCase().indexOf(q) >= 0 ||
        s.preacher.toLowerCase().indexOf(q) >= 0 ||
        s.series.toLowerCase().indexOf(q) >= 0 ||
        s.desc.toLowerCase().indexOf(q) >= 0 ||
        (s.transcript && s.transcript.toLowerCase().indexOf(q) >= 0)) {
      list.push(s);
    }
  }
  document.getElementById('meta').textContent =
    (q || active !== 'All') ? list.length + ' sermon' + (list.length !== 1 ? 's' : '') + ' found' : '';
  if (!list.length) {
    document.getElementById('results').innerHTML = '<div class=empty>No sermons match your search.</div>';
    return;
  }
  var html = '';
  for (var i = 0; i < list.length; i++) {
    var s = list[i];
    var txSnip = excerpt(s.transcript, raw);
    var txHtml = txSnip ? '<div class="tx-hit">...' + txSnip + '...</div>' : '';
    var badge  = s.series ? '<span class="sb">' + esc(s.series) + '</span>' : '';
    var txBtn  = s.transcript ? '<button class="tb" onclick="toggleTx(this)">Show transcript</button>' : '';
    var txFull = s.transcript ? '<div class="tx-full">' + esc(s.transcript) + '</div>' : '';
    html += '<div class="card">';
    html += '<div class="ct">' + hi(s.title, raw) + '</div>';
    html += '<div class="cm"><span>' + fmt(s.date) + '</span><span>' + esc(s.preacher) + '</span>' + badge + '</div>';
    html += '<div class="cd">' + hi(s.desc, raw) + '</div>';
    html += txHtml + txFull;
    html += '<div class="cf">';
    html += '<span class="tag">' + esc(s.scripture) + '</span>';
    html += '<a class="wl" href="' + s.url + '" target="_blank">Watch</a>';
    html += txBtn;
    html += '</div></div>';
  }
  document.getElementById('results').innerHTML = html;
}

buildFilters();
render();
</script>
</body>
</html>
"""


def build_html(channel_name, sermons):
    js_data = [{
        "id":         s["id"],
        "date":       s["date"],
        "title":      s["title"],
        "scripture":  s.get("scripture", ""),
        "preacher":   s.get("preacher", "Peter Frey"),
        "series":     infer_series(s),
        "desc":       s.get("description", ""),
        "transcript": s.get("transcript") or "",
        "url":        s["url"],
    } for s in sermons]
    data_json = json.dumps(js_data, ensure_ascii=False, separators=(",", ":"))
    return HTML_TEMPLATE.replace("__DATA_JSON__", data_json)


def main():
    youtube = build("youtube", "v3", developerKey=API_KEY)

    existing = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding="utf-8") as f:
            for s in json.load(f):
                existing[s["id"]] = s
        print(f"Loaded {len(existing)} cached sermons from {DATA_FILE}")

    print("Fetching video list from YouTube...")
    channel_name, videos = get_channel_videos(youtube)
    print(f"Found {len(videos)} sermons on '{channel_name}'")

    new_count = 0
    for v in videos:
        cached = existing.get(v["id"])
        if cached and cached.get("transcript"):
            v["transcript"] = cached["transcript"]
        else:
            print(f"  Fetching transcript: {v['title'][:60]}")
            v["transcript"] = get_transcript(v["id"])
            status = "OK " + str(len(v["transcript"])) + " chars" if v["transcript"] else "no transcript"
            print(f"    {status}")
            new_count += 1
            time.sleep(0.5)

    with_tx = sum(1 for v in videos if v["transcript"])
    print(f"\nFetched {new_count} new | {with_tx}/{len(videos)} total with transcripts")

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(videos, f, indent=2, ensure_ascii=False)
    print(f"Saved {DATA_FILE}")

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(build_html(channel_name, videos))
    print("Built index.html\nDone.")


if __name__ == "__main__":
    main()
