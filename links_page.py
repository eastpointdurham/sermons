"""
The links page (links/index.html): a link-in-bio page that keeps itself current.

Top to bottom: this week's sermon (newest public one), the Sunday line,
upcoming events from Planning Center Calendar, then the buttons in links.json.
build_site.py calls build() on every hourly run.

Events
  Read with a Planning Center personal access token (repo secrets PCO_APP_ID and
  PCO_SECRET). Only events published in Church Center are used: not hidden, not
  link-only, approved. Weekly events (the Sunday service) are left out, because
  the Sunday line already covers them, unless one Sunday has its own name
  ("Advent Begins - Sunday Celebration").
  The repo is public and keeps history, so events.json stores only the name,
  times and Church Center link: no location, description or photo.
  If Planning Center can't be reached, the last good events.json is used.
"""
import base64
import json
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from html import escape

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("America/New_York")
except Exception:          # pragma: no cover - zoneinfo is in 3.9+
    LOCAL_TZ = timezone(timedelta(hours=-5))

LINKS_CONFIG = "links.json"
EVENTS_FILE = "events.json"
PAGE_FILE = os.path.join("links", "index.html")
MAX_EVENTS = 5
HORIZON_DAYS = 60

PCO_URL = ("https://api.planningcenteronline.com/calendar/v2/event_instances"
           "?filter=future&order=starts_at&include=event&per_page=100")


# --- events -----------------------------------------------------------------

def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _weekly(desc):
    """'Every Sunday', 'Every week on Tuesday', 'Every 2 weeks'... but not monthly."""
    d = (desc or "").lower()
    return d.startswith("every") and "month" not in d and "year" not in d


def pick_events(api_json, now=None, limit=MAX_EVENTS, horizon_days=HORIZON_DAYS):
    """Public upcoming events from a Planning Center event_instances response."""
    now = now or datetime.now(timezone.utc)
    parents = {e["id"]: e.get("attributes", {})
               for e in api_json.get("included", []) if e.get("type") == "Event"}
    out, seen = [], set()
    for inst in api_json.get("data", []):
        a = inst.get("attributes", {})
        pid = (((inst.get("relationships") or {}).get("event") or {}).get("data") or {}).get("id")
        ev = parents.get(pid)
        if not ev or not ev.get("visible_in_church_center") or ev.get("link_only"):
            continue
        if ev.get("approval_status", "A") != "A":
            continue
        name = (a.get("name") or ev.get("name") or "").strip()
        if _weekly(a.get("compact_recurrence_description")) and name == (ev.get("name") or "").strip():
            continue
        if not a.get("starts_at") or not a.get("church_center_url"):
            continue
        start, end = _ts(a["starts_at"]), _ts(a.get("ends_at") or a["starts_at"])
        if end < now or start > now + timedelta(days=horizon_days):
            continue
        key = (pid, name)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "name": name,
            "starts_at": a["starts_at"],
            "ends_at": a.get("ends_at") or a["starts_at"],
            "all_day": bool(a.get("all_day_event")),
            "url": a["church_center_url"],
        })
    out.sort(key=lambda e: e["starts_at"])
    return out[:limit]


def fetch_events():
    """Events from Planning Center, or None when there are no credentials or it fails."""
    app_id, secret = os.environ.get("PCO_APP_ID"), os.environ.get("PCO_SECRET")
    if not app_id or not secret:
        print("Links page: no PCO_APP_ID/PCO_SECRET, using the saved events")
        return None
    auth = base64.b64encode(f"{app_id}:{secret}".encode()).decode()
    req = urllib.request.Request(PCO_URL, headers={"Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return pick_events(json.load(r))
    except Exception as e:     # never fail the archive build over events
        print(f"Links page: Planning Center not reachable ({type(e).__name__}), using the saved events")
        return None


def when(ev):
    """'Fri, Nov 20 · 7pm' in Durham time; all-day events show the date only."""
    s = _ts(ev["starts_at"]).astimezone(LOCAL_TZ)
    day = f"{s:%a}, {s:%b} {s.day}"
    if ev.get("all_day"):
        return day
    h = s.hour % 12 or 12
    t = f"{h}:{s:%M}" if s.minute else f"{h}"
    return f"{day} · {t}{'am' if s.hour < 12 else 'pm'}"


# --- page -------------------------------------------------------------------

SUNBURST = (
    '<svg class="mark" viewBox="0 0 1496 822.08" aria-hidden="true"><path d="M1046,667.33c129.84-73.26,'
    '259.69-146.52,389.53-219.79l-74.87-133.76c-131.32,70.99-262.64,141.98-393.97,212.97,75.34-130.35,'
    '150.67-260.69,226.01-391.04l-131.62-78.58c-78.81,127.45-157.63,254.9-236.44,382.35V0s-153.29,0-153.29,'
    '0v440.36c-78.99-127.74-157.99-255.48-236.98-383.22l-131.62,78.58c74.92,129.62,149.84,259.24,224.76,'
    '388.87-129.98-70.27-259.97-140.53-389.95-210.8l-74.87,133.76c129.84,73.26,259.68,146.52,389.53,'
    '219.79H0v153.29h428.66c-.11-3.31-.26-6.6-.26-9.94,0-170.99,143.1-309.61,319.63-309.61s319.63,138.62,'
    '319.63,309.61c0,3.33-.15,6.63-.26,9.94h428.6v-153.29h-450Z"/><path d="M748,582.13l-239.95,239.95'
    'c159.97,0,319.93,0,479.9,0l-239.95-239.95Z"/></svg>'
)

CSS = """
:root{--ink:#1a1a1a;--paper:#f5f5f5;--sage:#778472;--line:#d9d9d4;--muted:#5c5c57}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
  font-family:Archivo,system-ui,-apple-system,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:480px;margin:0 auto;padding:40px 16px 56px}
header{text-align:center;margin-bottom:28px}
.mark{width:72px;height:auto;fill:var(--sage);display:block;margin:0 auto 14px}
h1{font-size:22px;letter-spacing:.14em;text-transform:uppercase;margin:0;font-weight:800}
.sunday{margin:8px 0 0;color:var(--muted);font-size:15px}
h2{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--sage);margin:30px 0 10px;font-weight:700}
a{color:inherit;text-decoration:none}
.sermon{display:block;background:var(--ink);color:var(--paper);border-radius:14px;overflow:hidden}
.sermon img{display:block;width:100%;height:auto;aspect-ratio:16/9;object-fit:cover;background:#333}
.sermon div{padding:14px 16px 16px}
.sermon b{display:block;font-size:19px;line-height:1.25}
.sermon span{display:block;margin-top:4px;font-size:14px;opacity:.75}
.events a{display:flex;gap:12px;align-items:baseline;padding:13px 2px;border-bottom:1px solid var(--line)}
.events a:first-child{border-top:1px solid var(--line)}
.events b{flex:1;font-size:16px;font-weight:600}
.events span{font-size:13px;color:var(--muted);white-space:nowrap}
.btn{display:block;text-align:center;padding:15px 16px;margin:0 0 10px;border:2px solid var(--ink);
  border-radius:999px;font-weight:700;font-size:16px;background:#fff}
.btn:hover,.events a:hover b{color:var(--sage)}
.btn:hover{border-color:var(--sage)}
a:focus-visible{outline:3px solid var(--sage);outline-offset:3px}
footer{text-align:center;margin-top:28px;font-size:13px;color:var(--muted)}
"""


def build_page(sermon, events, config, now=None):
    now = now or datetime.now(timezone.utc)
    events = [e for e in events if _ts(e["ends_at"]) >= now]
    parts = [f'<header>{SUNBURST}<h1>Eastpoint Church</h1>']
    if config.get("sunday"):
        parts.append(f'<p class="sunday">{escape(config["sunday"])}</p>')
    parts.append("</header>")

    if sermon:
        vid = escape(sermon["id"], quote=True)
        thumb = f"https://i.ytimg.com/vi/{vid}/maxresdefault.jpg"
        fallback = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        meta = " · ".join(x for x in (sermon.get("scripture"), sermon.get("preacher")) if x)
        parts.append(
            '<h2>This week\'s sermon</h2>'
            f'<a class="sermon" href="{escape(sermon["url"], quote=True)}">'
            f'<img src="{thumb}" alt="" width="480" height="270" '
            f'onerror="this.onerror=null;this.src=\'{fallback}\'">'
            f'<div><b>{escape(sermon["title"])}</b><span>{escape(meta)}</span></div></a>')

    if events:
        parts.append('<h2>Coming up</h2><nav class="events">')
        for e in events:
            parts.append(f'<a href="{escape(e["url"], quote=True)}"><b>{escape(e["name"])}</b>'
                         f'<span>{escape(when(e))}</span></a>')
        parts.append("</nav>")

    links = config.get("links") or []
    if links:
        parts.append('<h2>Links</h2>')
        for l in links:
            parts.append(f'<a class="btn" href="{escape(l["url"], quote=True)}">{escape(l["label"])}</a>')

    parts.append('<footer>eastpointdurham.com</footer>')
    body = "\n".join(parts)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Eastpoint Church Durham</title>
<meta name="description" content="This week's sermon, upcoming events and links for Eastpoint Church in Durham.">
<meta name="theme-color" content="#1a1a1a">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""


def build(public_sermons):
    """Write links/index.html (and events.json when Planning Center answered)."""
    with open(LINKS_CONFIG, encoding="utf-8") as f:
        config = json.load(f)
    events = fetch_events()
    if events is not None:
        with open(EVENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(events, f, indent=2, ensure_ascii=False)
            f.write("\n")
    elif os.path.exists(EVENTS_FILE):
        with open(EVENTS_FILE, encoding="utf-8") as f:
            events = json.load(f)
    else:
        events = []
    sermon = max(public_sermons, key=lambda s: s["date"]) if public_sermons else None
    os.makedirs(os.path.dirname(PAGE_FILE), exist_ok=True)
    with open(PAGE_FILE, "w", encoding="utf-8") as f:
        f.write(build_page(sermon, events, config))
    print(f"Built {PAGE_FILE} ({len(events)} upcoming event(s))")
