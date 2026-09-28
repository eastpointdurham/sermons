"""
Tests for upload_sermon.py metadata generation, run against the real filenames
in the Drive folder and the real lines in the sermon planning doc.

The title format matters: build_site.py parses `Title | Scripture | Preacher`,
so a malformed title silently drops the sermon from the archive.

Run: python test_upload_sermon.py
"""
import sys
from datetime import date

import upload_sermon as U

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL {label}\n   got:  {got!r}\n   want: {want!r}")


# --- filenames actually in the Sermons folder ------------------------------

for name, want_date, want_is in [
    ("Sermon_08-09-26.mp4",              date(2026, 8, 9),  True),
    ("Sermon 07-26-26.mp4",              date(2026, 7, 26), True),
    ("Sermon 6-21-26.mp4",               date(2026, 6, 21), True),
    ("Sermon_05-24-26.mp4",              date(2026, 5, 24), True),
    ("Sermon 5-3-26.mp4",                date(2026, 5, 3),  True),
    ("Copy of Sermon 5-10-26.mp4",       date(2026, 5, 10), False),
    ("Copy of sermon-3-15-26.mov",       date(2026, 3, 15), False),
    ("Sermon Reel 08-09-26.mp4",         date(2026, 8, 9),  False),
    ("Copy of 2-8-26 Baptisms.mp4",      date(2026, 2, 8),  False),
    ("Easter Invite 2026.mov",           None,              False),
    ("Carol Funeral.mp4",                None,              False),
    ("Messy Olympics 2026.mp4",          None,              False),
]:
    check(f"date({name})", U.parse_date_from_filename(name), want_date)
    mime = "video/quicktime" if name.lower().endswith(".mov") else "video/mp4"
    check(f"is_sermon({name})", U.is_sermon_video(name, mime), want_is)

check("non-video rejected", U.is_sermon_video("Sermon 8.9.26.proplaylist", "application/x-zip"), False)


# --- planning-doc line parsing ---------------------------------------------

for line, want_title, want_scripture in [
    ("Aug 9 - Ephesians 3:14-21 - Praying for Immeasurably More",
     "Praying for Immeasurably More", "Ephesians 3:14–21"),
    ("Oct. 19 -  John 4 - Living Water", "Living Water", "John 4"),
    ("Sept. 28 - “The Source of Real Life” – John 1:1-14",
     "The Source of Real Life", "John 1:1–14"),
    ("Mar. 29 - Palm Sunday - Luke 19:28-42", "Palm Sunday", "Luke 19:28–42"),
    ("Nov. 30 - Advent - Hope in the Midst of Darkness  - Isaiah 9:1-7 NIV",
     "Advent - Hope in the Midst of Darkness", "Isaiah 9:1–7"),
    # who is preaching stays out of the title
    ("Oct. 4 - Guest Preacher - Brentley Wright- Acts 3:1-10 - \u201cMore Than What I Asked For.\u201d ",
     "More Than What I Asked For", "Acts 3:1–10"),
    ("May 3 - Isaiah 43 - Guest Preacher (Brentley Wright) The God who Redeems",
     "The God who Redeems", "Isaiah 43"),
    ("Mar. 22 - - Kevin Preaching - Colossians 4:7-18 - Christ in Community",
     "Christ in Community", "Colossians 4:7–18"),
    ("June 14 - James 1:5-7 – “Hearing From God” (Guest Preacher - Chris Hankins)",
     "Hearing From God", "James 1:5–7"),
    ("Oct. 11 - Mark 1:21-34 - ", "", "Mark 1:21–34"),
]:
    hit = U.match_doc_date_line(line)
    assert hit, line
    title, scripture = U.split_entry(line[hit[2]:])
    check(f"title({line[:24]})", title, want_title)
    check(f"scripture({line[:24]})", scripture, want_scripture)

# En-dash normalisation matches the existing archive ('Luke 18:1–8').
check("scripture uses en-dash", U.normalise_scripture("Luke 18:1-8"), "Luke 18:1–8")
check("en-dash left alone", U.normalise_scripture("Luke 18:1–8"), "Luke 18:1–8")


# --- guest preachers, which the old Apps Script version got wrong ----------

for line, want in [
    ("June 14 - James 1:5-7 – “Hearing From God” (Guest Preacher - Chris Hankins)", "Chris Hankins"),
    ("July 19 - Daniel 9:1-19 (Check with Ben for Reading) Guest Preacher - Ben Uthe", "Ben Uthe"),
    ("May 3 - Isaiah 43 - Guest Preacher (Brentley Wright) The God who Redeems", "Brentley Wright"),
    ("Mar. 22 - - Kevin Preaching - Colossians 4:7-18 - Christ in Community", "Kevin"),
    ("Sept. 13 - (Peter out, Kevin Preaching) - John 15 - How do I grow?", "Kevin"),
    ("Aug 9 - Ephesians 3:14-21 - Praying for Immeasurably More", "Peter Frey"),
    ("Oct. 19 -  John 4 - Living Water", "Peter Frey"),
]:
    check(f"preacher({line[:26]})", U.extract_preacher(line), want)


# --- assembled titles -------------------------------------------------------

entry = {"title": "Praying for Immeasurably More", "scripture": "Ephesians 3:14–21",
         "series": '"Teach us to Pray"', "preacher": "Peter Frey"}
title = U.build_title(entry, date(2026, 8, 9))
check("title format",
      title, "Praying for Immeasurably More | Ephesians 3:14–21 | Peter Frey | Eastpoint Church Durham")
check("four segments", title.count("|"), 3)
check("within YouTube limit", len(title) <= 100, True)

guest = {"title": "Hearing From God", "scripture": "James 1:5–7",
         "series": "", "preacher": "Chris Hankins"}
check("guest preacher in title",
      U.build_title(guest, date(2026, 6, 14)),
      "Hearing From God | James 1:5–7 | Chris Hankins | Eastpoint Church Durham")

# A very long title must still keep all three segments, or build_site.py drops it.
longy = {"title": "A" * 140, "scripture": "Luke 18:1–8", "series": "", "preacher": "Peter Frey"}
lt = U.build_title(longy, date(2026, 8, 16))
check("long title truncated", len(lt) <= 100, True)
check("long title keeps the parser's segments", lt.count("|"), 2)
check("church name goes before the sermon title is cut", "Eastpoint" in lt, False)

# The church name shortens a word at a time: Durham, then Church, then Eastpoint.
def _t(n):
    return U.build_title({"title": "T" * n, "scripture": "Luke 18:1–8", "series": "",
                          "preacher": "Peter Frey"}, date(2026, 8, 16))
check("fits: full church name", _t(40).endswith("| Eastpoint Church Durham"), True)
check("drops Durham first", _t(50).endswith("| Eastpoint Church"), True)
check("then Church", _t(58).endswith("| Eastpoint"), True)
check("then Eastpoint", _t(70).endswith("| Peter Frey"), True)
check("sermon title never cut while the name can give way", _t(70).startswith("T" * 70), True)
check("every length within 100", all(len(_t(n)) <= 100 for n in range(0, 160)), True)

# A planning-doc line with scripture but no sermon title yet.
untitled = U.build_title({"title": "", "scripture": "Mark 1:21–34", "series": "",
                          "preacher": "Peter Frey"}, date(2026, 10, 11))
check("untitled uses the date, not the scripture twice", untitled,
      "Sunday Service October 11, 2026 | Mark 1:21–34 | Peter Frey | Eastpoint Church Durham")

# No planning-doc entry at all.
none_title = U.build_title(None, date(2026, 9, 6))
check("fallback keeps all segments", none_title.count("|"), 3)
check("fallback names preacher", none_title.split(" | ")[2], "Peter Frey")


# --- round-trip through build_site.py's parser ------------------------------
# The real check: does what we generate survive what the archive reads?

import os                                    # noqa: E402
os.environ.setdefault("YOUTUBE_API_KEY", "unused")
from test_privacy_handling import FakeYouTube, item        # noqa: E402
from build_site import get_channel_videos                  # noqa: E402

generated = [
    U.build_title(entry, date(2026, 8, 9)),
    U.build_title(guest, date(2026, 6, 14)),
    lt,
    none_title,
]
fake = FakeYouTube([item(f"v{i}", t, "private") for i, t in enumerate(generated)], "x")
_, parsed = get_channel_videos(fake, None)

check("every generated title survives the parser", len(parsed), len(generated))
check("parsed title", parsed[0]["title"], "Praying for Immeasurably More")
check("parsed scripture", parsed[0]["scripture"], "Ephesians 3:14–21")
check("parsed preacher", parsed[0]["preacher"], "Peter Frey")
check("parsed guest preacher", parsed[1]["preacher"], "Chris Hankins")


# --- description and tags ---------------------------------------------------

desc = U.build_description(entry, date(2026, 8, 9))
check("description mentions the date", "August 9, 2026" in desc, True)
check("description carries scripture", "Scripture: Ephesians 3:14–21" in desc, True)
check("description within limit", len(desc) <= 4900, True)
check("description links to plan a visit", "https://www.eastpointdurham.com/visit" in desc, True)
check("description links Instagram", "https://instagram.com/eastpointdurham" in desc, True)
check("description ends with hashtags", desc.splitlines()[-1], "#EastpointChurch #DurhamNC #Ephesians")
check("numbered book hashtag", U.description_footer("1 Corinthians 15:1–6").splitlines()[-1].split()[-1],
      "#1Corinthians")
check("no scripture, no book hashtag", U.description_footer("").splitlines()[-1],
      "#EastpointChurch #DurhamNC")

tags = U.build_tags(entry)
check("tags include scripture", "Ephesians 3:14–21" in tags, True)
check("tags include book", "Ephesians" in tags, True)
check("tags no duplicates", len(tags), len(set(t.lower() for t in tags)))
check("tags within YouTube 500-char budget", len(",".join(tags)) <= 500, True)

# upload cutoff
from datetime import date as _d
check("no cutoff allows all", U.on_or_after_cutoff(_d(2026, 8, 9), ""), True)
check("before cutoff skipped", U.on_or_after_cutoff(_d(2026, 9, 27), "2026-10-04"), False)
check("on cutoff kept", U.on_or_after_cutoff(_d(2026, 10, 4), "2026-10-04"), True)
check("after cutoff kept", U.on_or_after_cutoff(_d(2026, 10, 11), "2026-10-04"), True)

# Planning doc with the newest series on top and a bare "2025" heading above the
# older ones (the doc's layout since Aug 2026).
class _FakeDocs:
    def __init__(self, paras):
        self.paras = paras
    def documents(self):
        return self
    def get(self, documentId):
        return self
    def execute(self):
        content = [{"paragraph": {"elements": [{"textRun": {"content": t + "\n",
                    "textStyle": {"bold": b}}}]}} for t, b in self.paras]
        return {"body": {"content": content}}

_start = U.START_YEAR
U.START_YEAR = 2026
plan = U.read_planning_doc(_FakeDocs([
    ("ALL IN: Following Jesus in the Gospel of Mark", True),
    ("Sept 27 - Mark 1:14-20 - Are you all in?", False),
    ("Dec. 27 - New Years Message", False),
    ("2025", True),
    ("Real Life Jesus", True),
    ("Sept. 28 - “The Source of Real Life” – John 1:1-14", False),
    ("Jan. 4 - Multiplying Disciples Together - Matthew 28:18-20", False),
    ("Aug 16 - Luke 18:1-8  Don’t Lose Heart", False),
]), "x")
U.START_YEAR = _start
check("top section is 2026", plan.get(date(2026, 9, 27), {}).get("title"), "Are you all in?")
check("top section scripture", plan.get(date(2026, 9, 27), {}).get("scripture"), "Mark 1:14–20")
check("top section series", plan.get(date(2026, 9, 27), {}).get("series"),
      "ALL IN: Following Jesus in the Gospel of Mark")
check("year heading resets to 2025", date(2025, 9, 28) in plan, True)
check("rolls into 2026 after heading", date(2026, 1, 4) in plan, True)
check("no stray 2027 dates", any(d.year == 2027 for d in plan), False)

# A one-off sermon inside a series' run is marked "(standalone)" in the doc.
plan2 = U.read_planning_doc(_FakeDocs([
    ("ALL IN: Following Jesus in the Gospel of Mark", True),
    ("Sept 27 - Mark 1:14-20 - Are you all in?", False),
    ("Oct. 4 - Guest Preacher - Brentley Wright- Acts 3:1-10 - \u201cMore Than What I Asked For.\u201d (standalone)", False),
    ("Oct. 11 - Mark 1:21-34 - Authority", False),
]), "x")
check("standalone has no series", plan2[date(2025, 10, 4)]["series"], "")
check("standalone title unchanged", plan2[date(2025, 10, 4)]["title"], "More Than What I Asked For")
check("series resumes after a standalone", plan2[date(2025, 10, 11)]["series"],
      "ALL IN: Following Jesus in the Gospel of Mark")
check("standalone description has no series line",
      "Series:" in U.build_description(plan2[date(2025, 10, 4)], date(2026, 10, 4)), False)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
