"""
Tests for the unpublished-sermon handling added to build_site.py.

The important property: a sermon uploaded as a private draft must get a
transcript and a Drive doc, but its text must never reach sermons.json or
index.html — this repository is public. Once the sermon is published, it moves
into the archive normally and stops being tracked as private.

Run: python test_privacy_handling.py
"""
import os
import sys

os.environ.setdefault("YOUTUBE_API_KEY", "test-key-not-used")

from build_site import get_channel_videos, partition_videos  # noqa: E402


# --- a stand-in for the googleapiclient service -----------------------------

class FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class FakeYouTube:
    """Minimal fake exposing just channels().list and playlistItems().list."""

    def __init__(self, items, label):
        self._items = items
        self.label = label
        self.parts_requested = []

    def channels(self):
        return self

    def playlistItems(self):
        return self

    def list(self, **kwargs):
        if "playlistId" in kwargs:
            self.parts_requested.append(kwargs.get("part"))
            return FakeRequest({"items": self._items})
        return FakeRequest({
            "items": [{
                "snippet": {"title": "Eastpoint Church"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU_fake"}},
            }]
        })


def item(video_id, title, privacy, published="2026-08-18T12:00:00Z"):
    return {
        "snippet": {
            "title": title,
            "publishedAt": published,
            "description": "d",
            "resourceId": {"videoId": video_id},
        },
        "status": {"privacyStatus": privacy},
    }


PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL {label}\n   got:  {got!r}\n   want: {want!r}")


# --- get_channel_videos -----------------------------------------------------

items = [
    item("aaa", "Praying for Immeasurably More | Ephesians 3:14–21 | Peter Frey", "public"),
    item("bbb", "Don’t Lose Heart | Luke 18:1–8 | Peter Frey", "private"),
    item("ccc", "Hearing From God | James 1:5–7 | Chris Hankins", "public"),
    item("ddd", "no pipe in this title so it is skipped", "public"),
    item("eee", "What is the Gospel? | Romans 1:16 | Eastpoint Church", "unlisted"),
]

api_key_client = FakeYouTube(items, "apikey")
oauth_client = FakeYouTube(items, "oauth")

name, vids = get_channel_videos(api_key_client, oauth_client)

check("channel name", name, "Eastpoint Church")
check("skips titles without a pipe", len(vids), 4)
check("uses the OAuth client when given one", oauth_client.parts_requested, ["snippet,status"])
check("does not use the API-key client", api_key_client.parts_requested, [])
check("requests the status part", oauth_client.parts_requested[0], "snippet,status")

by_id = {v["id"]: v for v in vids}
check("public privacy recorded", by_id["aaa"]["privacy"], "public")
check("private privacy recorded", by_id["bbb"]["privacy"], "private")
check("unlisted privacy recorded", by_id["eee"]["privacy"], "unlisted")
check("guest preacher preserved", by_id["ccc"]["preacher"], "Chris Hankins")
check("church name maps to Peter", by_id["eee"]["preacher"], "Peter Frey")
check("scripture parsed", by_id["aaa"]["scripture"], "Ephesians 3:14–21")

# Falls back to the API-key client when no OAuth credentials exist.
solo = FakeYouTube(items, "apikey-only")
get_channel_videos(solo, None)
check("falls back to API key client", solo.parts_requested, ["snippet,status"])


# --- partition_videos lifecycle --------------------------------------------
_pub, _priv = partition_videos([{"id": "nt", "title": "t", "privacy": "private", "transcript": None}], {})
check("draft without captions is retried next run", _priv, [])
_pub, _priv = partition_videos([{"id": "ok", "title": "t", "privacy": "private", "transcript": "x"}], {})
check("draft with captions is recorded", [p["id"] for p in _priv], ["ok"])


# Week 1: the draft is uploaded. It must not reach the public archive.
for _v in vids:                     # main() has fetched their captions by now
    _v["transcript"] = "words"
pub, priv = partition_videos(vids, {}, today="2026-08-18")
check("week1: only public in archive", sorted(v["id"] for v in pub), ["aaa", "ccc"])
check("week1: drafts tracked", sorted(s["id"] for s in priv), ["bbb", "eee"])
check("week1: no transcript stored for drafts",
      all("transcript" not in s for s in priv), True)

# Week 2: nothing changed. State must be stable, not duplicated.
seen = {s["id"]: s for s in priv}
pub2, priv2 = partition_videos(vids, seen, today="2026-08-19")
check("week2: no duplicates", sorted(s["id"] for s in priv2), ["bbb", "eee"])
check("week2: first-seen date preserved",
      [s["seen"] for s in priv2 if s["id"] == "bbb"], ["2026-08-18"])

# Week 3: Peter publishes 'bbb'. It should enter the archive and leave the
# private list.
items_after = [
    item("aaa", "Praying for Immeasurably More | Ephesians 3:14–21 | Peter Frey", "public"),
    item("bbb", "Don’t Lose Heart | Luke 18:1–8 | Peter Frey", "public"),
    item("ccc", "Hearing From God | James 1:5–7 | Chris Hankins", "public"),
    item("eee", "What is the Gospel? | Romans 1:16 | Eastpoint Church", "unlisted"),
]
_, vids_after = get_channel_videos(FakeYouTube(items_after, "x"), None)
seen2 = {s["id"]: s for s in priv2}
pub3, priv3 = partition_videos(vids_after, seen2, today="2026-08-26")
check("week3: published sermon enters archive",
      sorted(v["id"] for v in pub3), ["aaa", "bbb", "ccc"])
check("week3: published sermon leaves private list",
      sorted(s["id"] for s in priv3), ["eee"])

# A sermon that never existed should not linger.
pub4, priv4 = partition_videos([], {"zzz": {"id": "zzz", "title": "gone"}}, today="2026-08-26")
check("removed video stays tracked, not crashed", [s["id"] for s in priv4], ["zzz"])

# --- /latest redirect page ---------------------------------------------------
from build_site import build_latest_html  # noqa: E402

_older = {"id": "old", "date": "2026-11-01", "title": "Older", "url": "https://www.youtube.com/watch?v=old"}
_newer = {"id": "new", "date": "2026-11-08", "title": "Newer & Best", "url": "https://www.youtube.com/watch?v=new"}
_latest = build_latest_html([_older, _newer])
check("latest: forwards to newest sermon", 'url=https://www.youtube.com/watch?v=new"' in _latest, True)
check("latest: does not point at older sermon", "watch?v=old" in _latest, False)
check("latest: title escaped", "Newer &amp; Best" in _latest, True)
# main() passes only public_videos, so a newer draft never appears here; the
# draft 'bbb' from week 1 must not be linked from a page built off pub.
check("latest: built from public list has no draft",
      "bbb" in build_latest_html([dict(v, date="2026-08-18") for v in pub]), False)
check("latest: empty archive falls back to archive", 'url=../"' in build_latest_html([]), True)

# --- links page: only public Church Center events reach this public repo ----
from datetime import datetime, timezone  # noqa: E402
from links_page import pick_events, build_page, when  # noqa: E402


def _inst(iid, eid, name, start, end, recur="Does not repeat", all_day=False):
    return {"id": iid, "attributes": {
        "name": name, "starts_at": start, "ends_at": end, "all_day_event": all_day,
        "compact_recurrence_description": recur, "location": "Someone's House",
        "church_center_url": f"https://cc.example/calendar/event/{iid}"},
        "relationships": {"event": {"data": {"type": "Event", "id": eid}}}}


def _ev(eid, name, visible=True, link_only=False, status="A"):
    return {"type": "Event", "id": eid, "attributes": {
        "name": name, "visible_in_church_center": visible, "link_only": link_only,
        "approval_status": status, "description": "<p>private address here</p>"}}


_api = {
    "data": [
        _inst("1", "w", "Women's Gathering", "2026-11-21T00:00:00Z", "2026-11-21T02:00:00Z"),
        _inst("2", "s", "Sunday Celebration", "2026-11-22T15:00:00Z", "2026-11-22T16:15:00Z", "Every Sunday"),
        _inst("3", "h", "Members Vision Night", "2026-11-23T21:00:00Z", "2026-11-23T23:00:00Z"),
        _inst("4", "s", "Advent Begins - Sunday Celebration", "2026-11-29T15:00:00Z", "2026-11-29T16:15:00Z", "Every Sunday"),
        _inst("5", "l", "Link-only Thing", "2026-11-30T15:00:00Z", "2026-11-30T16:00:00Z"),
        _inst("6", "p", "Pending Thing", "2026-12-01T15:00:00Z", "2026-12-01T16:00:00Z"),
        _inst("7", "y", "Youth Party", "2026-12-06T05:00:00Z", "2026-12-07T04:59:59Z", all_day=True),
        _inst("8", "f", "Far Future", "2027-06-01T15:00:00Z", "2027-06-01T16:00:00Z"),
    ],
    "included": [_ev("w", "Women's Gathering"), _ev("s", "Sunday Celebration"),
                 _ev("h", "Members Vision Night", visible=False),
                 _ev("l", "Link-only Thing", link_only=True),
                 _ev("p", "Pending Thing", status="P"), _ev("y", "Youth Party"),
                 _ev("f", "Far Future")],
}
_now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
_picked = pick_events(_api, now=_now)
check("events: only public, upcoming, non-weekly",
      [e["name"] for e in _picked],
      ["Women's Gathering", "Advent Begins - Sunday Celebration", "Youth Party"])
check("events: nothing but name, times and link stored",
      sorted({k for e in _picked for k in e}), ["all_day", "ends_at", "name", "starts_at", "url"])
check("events: time shown in Durham time", when(_picked[0]), "Fri, Nov 20 · 7pm")
check("events: all-day shows date only", when(_picked[2]), "Sun, Dec 6")

_page = build_page({"id": "abc", "title": "A & B", "url": "https://youtu.be/abc",
                    "scripture": "Mark 1", "preacher": "Peter Frey", "date": "2026-11-15"},
                   _picked, {"sunday": "Sundays at 10am", "links": [{"label": "Give", "url": "https://x/give"}]},
                   now=_now)
check("page: hidden event absent", "Vision Night" in _page, False)
check("page: no location or description", "House" in _page or "private address" in _page, False)
check("page: sermon title escaped", "A &amp; B" in _page, True)
check("page: links rendered", 'href="https://x/give"' in _page, True)
check("page: past events dropped at render",
      "Women" in build_page(None, _picked, {}, now=datetime(2026, 11, 25, tzinfo=timezone.utc)), False)

if __name__ == "__main__":
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
