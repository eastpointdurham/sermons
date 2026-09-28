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

# compose(): current models reply with a thinking block before the text, and may
# decline. Use a fake client so no API call is made.
from types import SimpleNamespace as NS


class FakeClaude:
    def __init__(self, content, stop_reason="end_turn"):
        self.reply = NS(content=content, stop_reason=stop_reason)
        self.sent = None
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.sent = kwargs
        return self.reply


long_talk = "Jesus calls fishermen. " * 2000 + "THE ENDING"
sermon = {"title": "Are you all in?", "scripture": "Mark 1:14\u201320",
          "preacher": "Peter Frey", "transcript": long_talk}
fc = FakeClaude([NS(type="thinking", thinking=""),
                 NS(type="text", text="DESCRIPTION:\nA real summary.\n\nTAGS:\nMark, discipleship")])
desc, tags = E.compose(sermon, fc)
check("reads text after thinking block", desc.startswith("A real summary."), True)
check("tags parsed", tags, ["Mark", "discipleship"])
check("whole transcript sent", "THE ENDING" in fc.sent["messages"][0]["content"], True)
check("model is current", fc.sent["model"], E.MODEL)
check("summary keeps the standard footer", "Plan a visit: https://www.eastpointdurham.com/visit" in desc
      and desc.rstrip().endswith("#EastpointChurch #DurhamNC #Mark"), True)
check("refusal fallback on", fc.sent.get("fallbacks"), "default")
check("refusal leaves description alone",
      E.compose(sermon, FakeClaude([], stop_reason="refusal")), (None, None))

# transcript tidying and the Drive doc layout ------------------------------------
import transcript_text as TT
check("church name joined", TT.fix_names("Good morning, East Point Church. East point, east-Point."),
      "Good morning, Eastpoint Church. Eastpoint, Eastpoint.")
check("italic markers stripped", TT.tidy("A prayer from *Every Moment Holy* - 2 * 3"),
      "A prayer from Every Moment Holy - 2 * 3")
check("unrelated words kept", TT.fix_names("the east pointed north"), "the east pointed north")
raw = "# Sermon Transcript: Mark\n\n=====\n\nGood morning, **East Point**.\n\n\n\nSecond para."
check("markdown stripped", TT.tidy(raw), "Sermon Transcript: Mark\n\nGood morning, Eastpoint.\n\nSecond para.")
doc = TT.transcript_doc_html("Are You All In?", "Mark 1:14–20", "Peter Frey", "2026-09-27", raw)
check("doc has a real heading", "<h1>Are You All In?</h1>" in doc, True)
check("doc has no markdown marks", "#" not in doc and "=====" not in doc, True)
check("doc drops the model's own title line", "Sermon Transcript" not in doc, True)
check("doc paragraphs", doc.count("<p>"), 3)
mid = TT.transcript_doc_html("t", "", "", "", "One.\n\n# Sermon Transcript: Part Two\n\nTwo.")
check("model labels dropped", "Cleaned" not in TT.transcript_doc_html("t", "", "", "", "Cleaned Transcript\n\nNow hear the reading."), True)
check("chunk titles dropped mid-transcript", "Part Two" not in mid and mid.count("<p>") == 3, True)
check("doc escapes text", "&lt;b&gt;" in TT.transcript_doc_html("t", "", "", "", "<b>x</b>"), True)
check("doc byline has the service date", "2026-09-27" in doc, True)

import json as _json, os as _os, tempfile as _tf
import create_drive_docs as CD
with _tf.TemporaryDirectory() as _d:
    _cwd = _os.getcwd()
    _os.chdir(_d)
    try:
        with open("uploaded_sermons.json", "w") as _f:
            _json.dump([{"video_id": "RmUByaQNtOw", "service_date": "2026-09-27"}], _f)
        check("doc dated by service date", CD.service_date({"id": "RmUByaQNtOw", "date": "2026-09-28"}),
              "2026-09-27")
        check("unknown video keeps YouTube date", CD.service_date({"id": "zzz", "date": "2026-09-28"}),
              "2026-09-28")
    finally:
        _os.chdir(_cwd)

# chapters -----------------------------------------------------------------------
vtt = """WEBVTT

00:00:01.000 --> 00:00:04.000
Good morning, church.

00:00:04.000 --> 00:00:07.000
Good morning, church.

00:01:30.500 --> 00:01:34.000
<c>Our reading is from Mark 1.</c>

01:02:03.000 --> 01:02:05.000
Amen.
"""
cues = E.parse_vtt(vtt)
check("vtt cues parsed with times", cues, [(1, "Good morning, church."), (90, "Our reading is from Mark 1."),
                                            (3723, "Amen.")])
dig = E.timed_digest([(0, "a"), (5, "b"), (25, "c"), (61, "d")])
check("digest groups by time", dig, "[0:00] a b\n[0:25] c\n[1:01] d")
check("hour clock", E._clock(3723), "1:02:03")
ok = E.parse_chapters("0:00 Welcome\n4:10 The reading\n9:30 Turn and trust\n31:05 Respond")
check("chapters parsed", [t for t, _ in ok], [0, 250, 570, 1865])
check("first chapter forced to 0:00", E.parse_chapters("0:12 Welcome\n5:00 A\n9:00 B")[0][0], 0)
check("too few chapters dropped", E.parse_chapters("0:00 Welcome\n5:00 Reading"), [])
check("chapters closer than 10s dropped", len(E.parse_chapters("0:00 A\n0:05 B\n3:00 C\n6:00 D")), 3)
check("chapters past the end dropped", len(E.parse_chapters("0:00 A\n3:00 B\n6:00 C\n50:00 D", end=900)), 3)
check("junk gives no chapters", E.parse_chapters("no times here"), [])

fc2 = FakeClaude([NS(type="text", text="DESCRIPTION:\nA real summary.\n\nCHAPTERS:\n0:00 Welcome\n"
                                       "3:20 The reading\n8:45 Turn and trust\n\nTAGS:\nMark")])
d4, t4 = E.compose(sermon, fc2, [(0, "hi"), (200, "reading"), (525, "turn"), (2000, "end")])
check("chapters land after the summary", "A real summary.\n\nChapters\n0:00 Welcome\n3:20 The reading\n8:45 Turn and trust"
      in d4, True)
check("chapters asked for when timings exist", "CHAPTERS:" in fc2.sent["messages"][0]["content"], True)
check("chapters not asked for without timings", "CHAPTERS:" not in fc.sent["messages"][0]["content"], True)
check("tags still parsed with chapters", t4, ["Mark"])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
