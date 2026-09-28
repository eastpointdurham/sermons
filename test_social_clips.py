#!/usr/bin/env python3
"""Offline tests for social_clips.py (no Drive, no model, no ffmpeg)."""
import json
import os
import sys
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import social_clips as sc

PASS = FAIL = 0


def Fraction_ok(v):
    from fractions import Fraction
    return float(Fraction(v.rstrip("s") or "0"))


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {name}")


def words_from(text, start=0.0, step=0.4):
    out, t = [], start
    for w in text.split():
        out.append({"w": w, "s": round(t, 2), "e": round(t + step * 0.9, 2)})
        t += step
    return out


# sentences --------------------------------------------------------------
ws = words_from("Where do I start? The good news begins with creation. He makes all things new.")
ss = sc.sentences_from_words(ws)
check("three sentences", len(ss) == 3)
check("sentence text", ss[1]["text"] == "The good news begins with creation.")
check("sentence times", ss[0]["start"] == 0.0 and ss[2]["end"] == ws[-1]["e"])

gap = words_from("no punctuation here", 0) + words_from("after a long pause", 5.0)
check("pause splits", len(sc.sentences_from_words(gap)) == 2)

run = words_from(" ".join(["word"] * 95))
check("long run capped at 40 words", all(s["b"] - s["a"] < 40 for s in sc.sentences_from_words(run)))

# resolve_clips ----------------------------------------------------------
long_words = words_from(" ".join(f"Sentence number {i} is here." for i in range(60)), step=0.5)
sents = sc.sentences_from_words(long_words)          # each ~2.5 s
plan = {"clips": [
    {"start_sentence": 2, "end_sentence": 17, "hook": "a"},     # ~40 s ok
    {"start_sentence": 10, "end_sentence": 20, "hook": "overlap"},
    {"start_sentence": 20, "end_sentence": 59, "hook": "too long"},
    {"start_sentence": 55, "end_sentence": 99, "hook": "out of range"},
    {"start_sentence": 1, "end_sentence": 2, "hook": "too short"},
]}
clips = sc.resolve_clips(plan, sents, long_words)
hooks = [c["hook"] for c in clips]
check("keeps good clip", "a" in hooks)
check("drops overlap", "overlap" not in hooks)
check("trims long clip under cap", any(c["hook"] == "too long" and c["end"] - c["start"] <= sc.MAX_LEN + 1 for c in clips))
check("drops out of range", "out of range" not in hooks)
check("drops too short", "too short" not in hooks)
check("words attached", all(c["words"] and c["words"][0]["s"] >= c["start"] for c in clips))
check("text starts at sentence", clips[0]["text"].startswith("Sentence number 2 "))

# caption chunks -------------------------------------------------------------
chunks = sc.caption_chunks(words_from("But I want us to see this morning that the good news of Jesus extends far beyond."))
check("chunks 1-4 words", all(1 <= len(c) <= 4 for c in chunks))
check("chunks fit width", all(len(" ".join(w["w"] for w in c)) <= 18 + 12 for c in chunks))
check("chunks keep every word", sum(len(c) for c in chunks) == 17)
check("break after comma", [w["w"] for w in sc.caption_chunks(words_from("Yes, and amen"))[0]] == ["Yes,"])

# parse_plan ---------------------------------------------------------------------
p = sc.parse_plan('Here you go:\n```json\n{"clips": [{"start_sentence": 1, "end_sentence": 3}]}\n```')
check("parse fenced json", p["clips"][0]["end_sentence"] == 3)
try:
    sc.parse_plan("no json at all")
    check("parse raises", False)
except ValueError:
    check("parse raises", True)

# design + Final Cut handoff --------------------------------------------------
import tempfile
import reel_design as rd
import fcp_export as fx
brand = json.load(open(sc.BRAND_FILE))
fonts = sc.FONTS_DIR
clip = {"n": 1, "start": 10.0, "end": 20.0, "hook": "Hope is here", "scripture": "Luke 18:1-8 NIV",
        "why": "x", "layout": "fill", "crop_center": 0.46, "sentence_starts": [10.0, 14.0],
        "words": words_from("Hope is not wishful thinking. It is a person.", 10.0)}
with tempfile.TemporaryDirectory() as tmp:
    ov = rd.make_overlay(clip, brand, "fill", fonts, os.path.join(tmp, "o.png"))
    from PIL import Image
    im = Image.open(ov)
    check("overlay is 1080x1920 rgba", im.size == (1080, 1920) and im.mode == "RGBA")
    check("overlay top is drawn", im.getpixel((540, 60))[3] > 0)
    check("overlay middle is clear", im.getpixel((540, 900))[3] == 0)
    lst = rd.caption_track(clip, sc.caption_chunks(clip["words"]), brand, fonts, "fill", tmp)
    body = open(lst).read().splitlines()
    durs = [float(l.split()[1]) for l in body if l.startswith("duration")]
    check("caption list is ffconcat", body[0] == "ffconcat version 1.0")
    check("caption durations positive", all(d > 0 for d in durs))
    check("captions cover the clip", abs(sum(durs) - 10.5) < 0.6)
    frames = [l for l in body if l.startswith("file") and "blank" not in l]
    check("one caption frame per word", len(frames) == 9)
    card = rd.make_end_card(brand, fonts, os.path.join(tmp, "e.png"))
    check("end card ink background", Image.open(card).getpixel((20, 20)) == (26, 26, 26))
    b = rd.brush_stroke(600, 12, brand["sage"], seed=1)
    check("brush stroke drawn", b.width > 500 and b.getbbox() is not None)
    # Line breaks depend on the font (Oakes Grotesk in CI, Archivo/DejaVu elsewhere),
    # so check the shape: fewest lines, every line fits, no word lost.
    hook = "THE GOSPEL DOESN'T START AT CHRISTMAS"
    hf = rd.font("bold", rd.HOOK_SIZE, fonts)
    wrapped = rd.balanced_wrap(hook, hf, rd.HOOK_MAX_W)
    check("balanced hook wrap",
          " ".join(wrapped) == hook
          and len(wrapped) == len(rd.wrap_words(hook.split(), hf, rd.HOOK_MAX_W))
          and all(rd.text_width(l, hf) <= rd.HOOK_MAX_W for l in wrapped))

    fcpx = fx.build_fcpxml("Sermon-9-20-26.mov", fx.media_url_for("Sermon-9-20-26.mov"), 2086.6, 24,
                          1920, 1080, [clip], brand, "Oakes Grotesk", sc.caption_chunks, "Reels")
    from xml.dom import minidom
    dom = minidom.parseString(fcpx.encode())
    check("fcpxml well formed", dom.documentElement.tagName == "fcpxml")
    ac = dom.getElementsByTagName("asset-clip")[0]
    check("clip starts at source time", ac.getAttribute("start") == "10s" or ac.getAttribute("start") == "240/24s" or ac.getAttribute("start") == "10/1s")
    check("clip duration 10s", ac.getAttribute("duration") in ("10s", "10/1s", "240/24s"))
    titles = dom.getElementsByTagName("title")
    check("hook + scripture + captions", len(titles) == 2 + len(sc.caption_chunks(clip["words"])))
    check("titles anchored in source time", all(Fraction_ok(t.getAttribute("offset")) >= 10 for t in titles))
    check("vertical format", 'width="1080" height="1920"' in fcpx)
    check("markers at sentences", len(dom.getElementsByTagName("marker")) == 1)
    srt = os.path.join(tmp, "a.srt")
    fx.write_srt(clip, sc.caption_chunks(clip["words"]), srt)
    check("srt starts at zero-based time", open(srt).read().startswith("1\n00:00:00,"))

# find_new_sermons ----------------------------------------------------------------------
today = date.today()
d1 = (today - timedelta(days=1)).strftime("%m-%d-%y")
d2 = (today - timedelta(days=30)).strftime("%m-%d-%y")
main_files = [
    {"id": "big", "name": f"sermon_{d1}_A006_C009.mov", "mimeType": "video/quicktime", "size": "10000000000", "createdTime": "x"},
    {"id": "small", "name": f"Sermon-{d1}.mov", "mimeType": "video/quicktime", "size": "2000000000", "createdTime": "x"},
    {"id": "old", "name": f"Sermon {d2}.mp4", "mimeType": "video/mp4", "size": "1", "createdTime": "x"},
    {"id": "reel", "name": f"Sermon Reel {d1}.mp4", "mimeType": "video/mp4", "size": "1", "createdTime": "x"},
]
extra_files = [{"id": "wes", "name": f"{d1.replace('-', '.')} Sermon.mp4", "mimeType": "video/mp4", "size": "1", "createdTime": "x"}]
with mock.patch.object(sc, "list_folder", side_effect=lambda drive, fid: main_files if fid == "MAIN" else extra_files), \
        mock.patch.dict(os.environ, {"SERMON_FOLDER_ID": "MAIN", "SOCIAL_EXTRA_FOLDER_IDS": "WES", "SOCIAL_ONLY_FILE_ID": ""}):
    todo = sc.find_new_sermons(None, [])
    check("one per Sunday", len(todo) == 1)
    check("main folder, smaller export wins", todo and todo[0]["id"] == "small")
    todo = sc.find_new_sermons(None, [{"drive_file_id": "zzz", "service_date": (today - timedelta(days=1)).isoformat()}])
    check("date already done", todo == [])
    with mock.patch.dict(os.environ, {"SOCIAL_ONLY_FILE_ID": "old"}):
        todo = sc.find_new_sermons(None, [])
        check("manual re-run ignores age", [t["id"] for t in todo] == ["old"])

# thumbnails -------------------------------------------------------------------
import thumbnail as T
from datetime import date as _date
series = "ALL IN: Following Jesus in the Gospel of Mark"
check("series key", T.series_key(series) == "all in")
check("art name matches series", T.matches_series("ALL IN.png", series))
check("art name with suffix matches", T.matches_series("All In - 16x9.jpg", series))
check("other series art ignored", not T.matches_series("Colossians.png", series))
check("no series matches nothing", not T.matches_series("ALL IN.png", ""))
art_files = [{"name": "Colossians.png"}, {"name": "ALL IN old.png"}, {"name": "ALL IN.png"},
             {"name": "ALL IN notes.txt"}]
check("plainest art picked", T.pick_series_art(art_files, series)["name"] == "ALL IN.png")
photos = [{"name": f"{i:02d}.jpg"} for i in range(5)] + [{"name": "readme.txt"}]
d1 = _date(2026, 9, 27)
check("photo pick is stable", T.pick_photo(photos, d1) == T.pick_photo(list(reversed(photos)), d1))
check("photo rotates weekly", T.pick_photo(photos, d1) != T.pick_photo(photos, _date(2026, 10, 4)))
check("no photos -> None", T.pick_photo([{"name": "x.txt"}], d1) is None)
from PIL import Image as _Im, ImageDraw as _Dr
two = _Im.new("RGBA", (400, 200), (242, 232, 218, 255))
_Dr.Draw(two).rectangle((0, 120, 400, 200), fill=(142, 74, 73, 255))
pal = T.palette_from_art(two)
check("palette ground is the dark colour", pal and pal["ground"][0] < 160 and pal["text"][0] > 230)
wall = _Im.new("RGBA", (1920, 1080), (30, 30, 30, 255))
_wd = _Dr.Draw(wall)
_wd.rectangle((700, 450, 1200, 560), fill=(236, 234, 228, 255))     # thin lockup
_wd.rectangle((560, 470, 680, 480), fill=(80, 132, 132, 255))       # teal mark
wp = T.palette_from_art(wall)
check("wallpaper: ground is the background", wp and wp["ground"] == (30, 30, 30))
check("wallpaper: text is the lockup's off-white", wp and min(wp["text"]) > 220)
check("wallpaper: accent is the teal mark", wp and wp["accent"][1] > wp["accent"][0] + 30)
tw, th = T.trim_to_content(wall).size
check("wallpaper trimmed to its lockup", tw < 900 and th < 250)
check("flat art gives no palette", T.palette_from_art(_Im.new("RGBA", (50, 50), (90, 90, 90, 255))) is None)
with tempfile.TemporaryDirectory() as tmp:
    entry = {"title": "Are you all in?", "scripture": "Mark 1:14\u201320", "series": series}
    two.save(os.path.join(tmp, "art.png"))
    _Im.new("RGB", (3000, 2000), (120, 100, 90)).save(os.path.join(tmp, "p.jpg"))
    for label, kw in (("brand look", {}),
                      ("series look", {"photo_path": os.path.join(tmp, "p.jpg"),
                                       "art_path": os.path.join(tmp, "art.png")})):
        out = T.render(entry, d1, brand, fonts, os.path.join(tmp, "t.jpg"), **kw)
        im = Image.open(out)
        check(f"thumbnail {label} is 1280x720", im.size == (1280, 720))
        check(f"thumbnail {label} under 2 MB", os.path.getsize(out) < 2_000_000)
    check("series look uses the art's ground",
          Image.open(out).convert("RGB").getpixel((20, 700))[0] > 120)
    T.render(None, d1, brand, fonts, os.path.join(tmp, "n.jpg"))
    check("thumbnail without a planning entry", os.path.exists(os.path.join(tmp, "n.jpg")))


class _BrokenDrive:
    def files(self):
        raise RuntimeError("drive down")


T.add_thumbnail(_BrokenDrive(), None, {"series": series}, d1, "vid", "folder")
check("thumbnail failure never raises", True)

# camera: following the speaker ------------------------------------------------
import reframe as RF
samples = []
for k in range(100):
    i = k * 6
    cands = []
    x = 0.30 + 0.004 * k                                    # preacher walks right
    if not 40 <= k < 48:                                    # ...and turns away for a while
        cands.append((x, 0.35, 0.05, 0.9))
    cands.append((0.80, 0.62, 0.04, 0.7))                   # someone in the front row
    if k == 0:
        cands.append((0.10, 0.30, 0.20, 0.8))               # a big one-frame false hit
    samples.append((i, cands))
track = RF.pick_speaker(samples)
xs = [c[0] for _, c in track]
check("camera follows the preacher, not the front row", all(x < 0.75 for x in xs))
check("camera ignores the one-frame false face", all(x > 0.2 for x in xs))
check("camera keeps him across the gap", min(xs) < 0.35 and max(xs) > 0.65)
check("no speaker when nobody is seen", RF.pick_speaker([(0, []), (6, [])]) == [])

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
