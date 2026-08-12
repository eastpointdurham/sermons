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
    # yt-dlp FIRST — works from GitHub Actions IPs, handles YouTube anti-bot
    try:
        import subprocess, tempfile, glob, re
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                [sys.executable, "-m", "yt_dlp",
                 "--skip-download", "--write-sub", "--write-auto-sub",
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
                if lines:
                    return " ".join(lines)
    except Exception as e:
        print(f"      yt-dlp error: {e}")

    # Fallback: youtube_transcript_api
    try:
        from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled
        api = YouTubeTranscriptApi()
        return " ".join(e.text for e in api.fetch(video_id))
    except Exception as e:
        print(f"      transcript-api error: {e}")

    return None


def polish_transcript(raw_text):
    """Use Claude Haiku to clean up a raw auto-generated transcript into readable prose."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not raw_text:
        return raw_text

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        # Split into ~2500-word chunks so output stays within model limits
        words = raw_text.split()
        chunk_size = 2500
        word_chunks = [words[i:i + chunk_size] for i in range(0, len(words), chunk_size)]

        polished = []
        for chunk_words in word_chunks:
            chunk = " ".join(chunk_words)
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4096,
                messages=[{"role": "user", "content": (
                    "Clean up this auto-generated sermon transcript segment into polished, readable prose.\n\n"
                    "Rules:\n"
                    "- Add proper punctuation and capitalization\n"
                    "- Organize into natural paragraphs (every 4-8 sentences)\n"
                    "- Remove filler words: um, uh, you know, kind of, sort of, like, right, okay so\n"
                    "- Remove false starts and immediate repetitions\n"
                    "- Keep ALL theological content and ideas intact — do not summarize or cut\n"
                    "- Preserve the preacher's natural voice and tone\n"
                    "- Return only the cleaned text with no preamble or commentary\n\n"
                    f"TRANSCRIPT:\n{chunk}"
                )}]
            )
            polished.append(msg.content[0].text.strip())

        return "\n\n".join(polished)

    except Exception as e:
        print(f"      polish error: {e}")
        return raw_text


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


# NOTE: uses single-quoted JS strings throughout to avoid Python escape issues.
HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sermons — Eastpoint Church Durham</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@1,500;1,600&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --cream:#faf6ee;
  --cream-deep:#f3eddf;
  --ink:#1f1c16;
  --ink-soft:#565149;
  --accent:#8a6d3b;
  --accent-light:#b8944f;
  --line:#e2d9c4;
  --surface:#ffffff;
  --tag-bg:#f0e9d8;
  --radius:12px;
}
body{font-family:'Inter',system-ui,sans-serif;background:var(--cream);color:var(--ink);min-height:100vh;-webkit-font-smoothing:antialiased}

/* ---- HEADER ---- */
header{background:var(--cream);border-bottom:1px solid var(--line);padding:2rem 1.5rem 0}
.hdr{max-width:780px;margin:0 auto}
.hdr-top{display:flex;flex-direction:column;align-items:center;text-align:center;gap:.6rem;padding-bottom:1.5rem}
.hdr-eyebrow{font-size:.68rem;font-weight:600;letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}
.hdr-logo{width:52px;height:52px;object-fit:contain}
.hdr-title{font-family:'Playfair Display',Georgia,serif;font-style:italic;font-weight:500;font-size:1.65rem;color:var(--ink);letter-spacing:-.01em;line-height:1.2}
.hdr-sub{font-size:.75rem;color:var(--ink-soft);margin-top:.15rem;letter-spacing:.03em}

/* ---- SEARCH + FILTERS ---- */
.hdr-controls{border-top:1px solid var(--line);padding:1rem 0 .75rem}
.sw{position:relative;margin-bottom:.7rem}
.sw svg{position:absolute;left:13px;top:50%;transform:translateY(-50%);color:var(--ink-soft);pointer-events:none}
input[type=search]{width:100%;padding:.65rem 1rem .65rem 2.6rem;border:1.5px solid var(--line);border-radius:var(--radius);background:var(--surface);color:var(--ink);font-size:.88rem;outline:none;appearance:none;font-family:inherit;transition:border-color .15s,box-shadow .15s}
input[type=search]:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(138,109,59,.12)}
input[type=search]::-webkit-search-cancel-button{-webkit-appearance:none}
input[type=search]::placeholder{color:var(--ink-soft)}
.filters{display:flex;gap:.35rem;flex-wrap:wrap}
.fb{font-size:.7rem;padding:.28rem .75rem;border:1.5px solid var(--line);border-radius:20px;background:none;color:var(--ink-soft);cursor:pointer;font-family:inherit;letter-spacing:.02em;transition:border-color .15s,color .15s,background .15s}
.fb:hover{border-color:var(--accent);color:var(--accent)}
.fb.on{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}

/* ---- MAIN ---- */
main{max-width:780px;margin:0 auto;padding:1.5rem 1.5rem 3rem}
.meta{font-size:.72rem;color:var(--ink-soft);margin-bottom:1rem;letter-spacing:.02em}

/* ---- CARDS ---- */
.card{background:var(--surface);border:1.5px solid var(--line);border-radius:var(--radius);padding:1.3rem 1.4rem;margin-bottom:.7rem;transition:border-color .15s,box-shadow .15s}
.card:hover{border-color:var(--accent);box-shadow:0 2px 12px rgba(138,109,59,.08)}
.ct{font-family:'Playfair Display',Georgia,serif;font-style:italic;font-weight:500;font-size:1.08rem;line-height:1.35;margin-bottom:.3rem;color:var(--ink)}
.cm{font-size:.72rem;color:var(--ink-soft);margin-bottom:.55rem;display:flex;gap:.55rem;flex-wrap:wrap;align-items:center}
.cm-dot{width:3px;height:3px;border-radius:50%;background:var(--line);flex-shrink:0}
.cd{font-size:.82rem;color:var(--ink-soft);line-height:1.7;margin-bottom:.75rem}
.cf{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap}
.tag{font-size:.68rem;background:var(--tag-bg);color:var(--accent);border:1px solid var(--line);border-radius:6px;padding:.18rem .55rem;font-style:italic}
.sb{font-size:.63rem;font-weight:600;border-radius:6px;padding:.18rem .55rem;background:var(--accent);color:#fff;text-transform:uppercase;letter-spacing:.05em}
.wl{font-size:.75rem;color:var(--accent);text-decoration:none;font-weight:600;display:flex;align-items:center;gap:.3rem;border:1.5px solid var(--line);border-radius:8px;padding:.22rem .6rem;transition:border-color .15s,background0.15s}
.wl:hover{border-color:var(--accent);background:var(--tag-bg)}
.tb{font-size:.72rem;color:var(--accent);background:none;border:1.5px solid var(--line);border-radius:8px;padding:.22rem .6rem;cursor:pointer;font-family:inherit;font-weight:600;margin-left:auto;transition:border-color .15s,background .15s}
.tb:hover{border-color:var(--accent);background:var(--tag-bg)}
.hit{background:rgba(138,109,59,.18);color:var(--accent);border-radius:2px;padding:0 2px}
.tx-hit{font-size:.77rem;color:var(--ink-soft);line-height:1.7;margin-top:.45rem;margin-bottom:.35rem;border-left:2.5px solid var(--accent);padding-left:.6rem}
.tx-full{display:none;margin-top:.85rem;padding:.9rem 1rem;background:var(--cream);border-radius:10px;border:1px solid var(--line);font-size:.77rem;line-height:1.9;color:var(--ink-soft);white-space:pre-wrap;max-height:420px;overflow-y:auto}
.tx-full.open{display:block}
.empty{text-align:center;padding:4rem;color:var(--ink-soft);font-size:.88rem}
.empty p{font-family:'Playfair Display',Georgia,serif;font-style:italic;font-size:1.1rem;margin-bottom:.5rem;color:var(--ink)}

/* ---- FOOTER ---- */
footer{text-align:center;padding:2rem 1rem;font-size:.7rem;color:var(--ink-soft);border-top:1px solid var(--line);letter-spacing:.04em}
footer a{color:var(--accent);text-decoration:none}
footer a:hover{text-decoration:underline}
</style>
</head>
<body>
<header>
  <div class="hdr">
    <div class="hdr-top">
      <div class="hdr-eyebrow">Sermon Archive</div>
      <img class="hdr-logo" src="https://res.cloudinary.com/thechurchcov3production/image/fetch/f_auto/https://media.thechurchcoassets.com/accounts/7624/abb18065-1f40-4731-a9b3-4195148ce331-./EPC-ICON-GRN__largepreview__.webp" alt="Eastpoint Church">
      <div>
        <div class="hdr-title">Eastpoint Church</div>
        <div class="hdr-sub">Durham, North Carolina</div>
      </div>
    </div>
    <div class="hdr-controls">
      <div class="sw">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
        <input type="search" id="q" placeholder="Search titles, scripture, or transcript text…" oninput="render()"/>
      </div>
      <div class="filters" id="filters"></div>
    </div>
  </div>
</header>
<main>
  <div class="meta" id="meta"></div>
  <div id="results"></div>
</main>
<footer>
  <span id="footer-count"></span> &nbsp;&middot;&nbsp; <a href="https://eastpointdurham.com" target="_blank">eastpointdurham.com</a>
</footer>
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
  return new Date(d + 'T12:00:00').toLocaleDateString('en-US', {month:'long',day:'numeric',year:'numeric'});
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
  return (start > 0 ? '…' : '') + hi(transcript.slice(start, end).trim(), q) + (end < transcript.length ? '…' : '');
}

function toggleTx(btn) {
  var card = btn.parentNode.parentNode;
  var box = card.querySelector('.tx-full');
  var isOpen = box.classList.toggle('open');
  btn.textContent = isOpen ? 'Hide transcript' : 'Read transcript';
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
  var fc = document.getElementById('footer-count');
  if (fc) {
    fc.textContent = DATA.length + ' sermons' + (withTranscripts ? ' · ' + withTranscripts + ' with transcripts' : '');
  }
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
  var metaEl = document.getElementById('meta');
  metaEl.textContent = (q || active !== 'All')
    ? list.length + ' sermon' + (list.length !== 1 ? 's' : '') + ' found'
    : '';
  if (!list.length) {
    document.getElementById('results').innerHTML = '<div class=empty><p>No sermons found.</p>Try a different search or filter.</div>';
    return;
  }
  var html = '';
  for (var i = 0; i < list.length; i++) {
    var s = list[i];
    var txSnip = excerpt(s.transcript, raw);
    var txHtml = txSnip ? '<div class="tx-hit">' + txSnip + '</div>' : '';
    var badge  = s.series ? '<span class="sb">' + esc(s.series) + '</span>' : '';
    var txBtn  = s.transcript ? '<button class="tb" onclick="toggleTx(this)">Read transcript</button>' : '';
    var txFull = s.transcript ? '<div class="tx-full">' + esc(s.transcript) + '</div>' : '';
    html += '<div class="card">';
    html += '<div class="ct">' + hi(s.title, raw) + '</div>';
    html += '<div class="cm"><span>' + fmt(s.date) + '</span>';
    if (s.preacher) { html += '<span class="cm-dot"></span><span>' + esc(s.preacher) + '</span>'; }
    if (badge) { html += '<span class="cm-dot"></span>' + badge; }
    html += '</div>';
    if (s.desc) { html += '<div class="cd">' + hi(s.desc, raw) + '</div>'; }
    html += txHtml + txFull;
    html += '<div class="cf">';
    if (s.scripture) { html += '<span class="tag">' + esc(s.scripture) + '</span>'; }
    html += '<a class="wl" href="' + s.url + '" target="_blank">Watch →</a>';
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
        if cached and cached.get("transcript") and cached.get("transcript_polished") and "\n\n" in cached.get("transcript", ""):
            # Already fetched and properly polished (has paragraph breaks) — use as-is
            v["transcript"] = cached["transcript"]
            v["transcript_polished"] = True
        elif cached and cached.get("transcript"):
            # Has raw transcript but not yet polished — polish it once
            print(f"  Polishing transcript: {v['title'][:60]}")
            v["transcript"] = polish_transcript(cached["transcript"])
            v["transcript_polished"] = True
            new_count += 1
            time.sleep(0.3)
        else:
            # Need to fetch raw, then polish
            print(f"  Fetching transcript: {v['title'][:60]}")
            raw = get_transcript(v["id"])
            if raw:
                print(f"    Polishing ({len(raw)} chars raw)...")
                v["transcript"] = polish_transcript(raw)
                v["transcript_polished"] = True
            else:
                v["transcript"] = None
                v["transcript_polished"] = False
            status = "OK " + str(len(v["transcript"])) + " chars polished" if v["transcript"] else "no transcript"
            print(f"    {status}")
            new_count += 1
            time.sleep(0.5)

    with_tx = sum(1 for v in videos if v["transcript"])
    print(f"\nFetched {new_count} new | {with_tx}/{len(videos)} total with transcripts")

    # Track truly new sermons (not in previous sermons.json) for Drive doc creation
    new_sermons = [v for v in videos if v["id"] not in existing]
    if new_sermons:
        with open("new_sermons.json", "w", encoding="utf-8") as f:
            json.dump(new_sermons, f, indent=2, ensure_ascii=False)
        print(f"Wrote {len(new_sermons)} new sermon(s) to new_sermons.json")
    elif os.path.exists("new_sermons.json"):
        os.remove("new_sermons.json")

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(videos, f, indent=2, ensure_ascii=False)
    print(f"Saved {DATA_FILE}")

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(build_html(channel_name, videos))
    print("Built index.html\nDone.")


if __name__ == "__main__":
    main()
