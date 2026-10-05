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

if __name__ == "__main__":
    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
