"""
Tests for enrich_descriptions.py — the parsing and safety logic, without
calling Anthropic or YouTube.

The property that matters most: videos.update replaces the entire snippet, so
anything we fail to send back is destroyed. Title and category must survive, and
privacy must never be touched.

Run: python test_enrich_descriptions.py
"""
import sys

import enrich_descriptions as E

PASS = FAIL = 0


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"FAIL {label}\n   got:  {got!r}\n   want: {want!r}")


# --- parsing the model's reply ---------------------------------------------

reply = """DESCRIPTION:
Paul kneels before the Father and prays not for easier circumstances but for
strength in the inner being.

TAGS:
Ephesians, Ephesians 3:14-21, prayer, sermon, Eastpoint Church, Durham NC"""

desc, tags = E.parse_model_output(reply)
check("description extracted", desc.startswith("Paul kneels"), True)
check("description drops the label", "DESCRIPTION:" in desc, False)
check("tags extracted", tags[0], "Ephesians")
check("tag count", len(tags), 6)

# No TAGS section at all — must not crash, must still yield a description.
desc2, tags2 = E.parse_model_output("DESCRIPTION:\nJust a description.")
check("missing tags section", (desc2, tags2), ("Just a description.", []))

# The word TAGS appearing inside the prose shouldn't split early — rsplit takes
# the last occurrence.
tricky = "DESCRIPTION:\nHe mentions TAGS: in passing.\n\nTAGS:\nsermon, prayer"
d3, t3 = E.parse_model_output(tricky)
check("splits on the last TAGS", t3, ["sermon", "prayer"])
check("prose mention preserved", "in passing" in d3, True)

check("empty input safe", E.parse_model_output(""), ("", []))


# --- tag budget -------------------------------------------------------------

check("dedupes case-insensitively",
      E.trim_tags(["sermon", "Sermon", "SERMON", "prayer"]), ["sermon", "prayer"])
check("strips commas inside a tag", E.trim_tags(["a,b"]), ["a b"])
check("drops empties", E.trim_tags(["", "  ", "prayer"]), ["prayer"])

many = [f"tag-number-{i:03d}" for i in range(60)]
trimmed = E.trim_tags(many)
check("respects the 460-char budget", len(",".join(trimmed)) <= 460, True)
check("still under YouTube's 500 limit", len(",".join(trimmed)) <= 500, True)
check("keeps something", len(trimmed) > 0, True)

check("single overlong tag is cut to 60", len(E.trim_tags(["x" * 200])[0]), 60)


# --- update_video preserves the rest of the snippet -------------------------

class FakeVideos:
    def __init__(self, snippet, found=True):
        self._snippet = snippet
        self._found = found
        self.updated_body = None

    def list(self, **kwargs):
        self.list_kwargs = kwargs
        items = [{"snippet": self._snippet}] if self._found else []
        return FakeExec({"items": items})

    def update(self, **kwargs):
        self.updated_body = kwargs
        return FakeExec({})


class FakeExec:
    def __init__(self, payload):
        self._payload = payload

    def execute(self):
        return self._payload


class FakeYT:
    def __init__(self, videos):
        self._videos = videos

    def videos(self):
        return self._videos


existing_snippet = {
    "title": "Praying for Immeasurably More | Ephesians 3:14–21 | Peter Frey",
    "categoryId": "29",
    "description": "old placeholder",
    "tags": ["old"],
    "defaultLanguage": "en",
}

fv = FakeVideos(existing_snippet)
ok = E.update_video(FakeYT(fv), "vid123", "new description", ["prayer", "sermon"])
check("reports success", ok, True)

sent = fv.updated_body["body"]["snippet"]
check("title preserved", sent["title"], existing_snippet["title"])
check("category preserved", sent["categoryId"], "29")
check("description replaced", sent["description"], "new description")
check("tags replaced", sent["tags"], ["prayer", "sermon"])
check("only the snippet part is written", fv.updated_body["part"], "snippet")
check("privacy never sent", "status" in fv.updated_body["body"], False)
check("privacy not in snippet", "privacyStatus" in sent, False)

# Missing categoryId should fall back rather than sending nothing.
fv2 = FakeVideos({"title": "T", "description": "", "tags": []})
E.update_video(FakeYT(fv2), "v", "d", ["t"])
check("category falls back", fv2.updated_body["body"]["snippet"]["categoryId"], "29")

# A video the credentials cannot see must not raise.
fv3 = FakeVideos({}, found=False)
check("missing video handled", E.update_video(FakeYT(fv3), "gone", "d", []), False)
check("no update attempted for missing video", fv3.updated_body, None)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
