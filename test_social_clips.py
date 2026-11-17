#!/usr/bin/env python3
"""Offline tests for social_clips.py (no Drive, no model, no ffmpeg)."""
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import social_clips as sc

PASS = FAIL = 0


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

# design ----------------------------------------------------------------------
import tempfile
import reel_design as rd
brand = json.load(open(sc.BRAND_FILE))
fonts = sc.FONTS_DIR
clip = {"n": 1, "start": 10.0, "end": 20.0, "hook": "Hope has a name", "scripture": "Luke 18:1-8 NIV",
        "why": "x", "layout": "fill", "crop_center": 0.46, "sentence_starts": [10.0, 14.0],
        "words": words_from("Hope is not wishful thinking. It is a person.", 10.0)}
with tempfile.TemporaryDirectory() as tmp:
    ov = rd.make_overlay(clip, brand, "fill", fonts, os.path.join(tmp, "o.png"))
    from PIL import Image
    im = Image.open(ov)
    check("overlay is 1080x1920 rgba", im.size == (1080, 1920) and im.mode == "RGBA")
    check("overlay top is drawn", im.getpixel((540, 60))[3] > 0)
    check("overlay middle is clear", im.getpixel((540, 900))[3] == 0)
    close = dict(clip, head_top=0.23,               # this week's framing: head at 23%
                 hook="Two hours on hold, asking one question", scripture="Mark 1:21-28")
    check("close shot gets a smaller hook block; a wide shot keeps the full one",
          rd.hook_variant(close, brand, fonts, "fill") != rd.HOOK_VARIANTS[0]
          and rd.hook_variant(dict(close, head_top=0.4), brand, fonts, "fill") == rd.HOOK_VARIANTS[0])
    ov2 = rd.make_overlay(close, brand, "fill", fonts, os.path.join(tmp, "o2.png"))
    px, white = Image.open(ov2).load(), tuple(int(brand["offwhite"].lstrip("#")[i:i + 2], 16)
                                               for i in (0, 2, 4))
    rows = [y for y in range(0, 1920, 4)
            if any(px[x, y][:3] == white and px[x, y][3] == 255 for x in range(100, 980, 6))]
    check("hook text ends above the head", rows and max(rows) < 0.23 * 1920)
    lst = rd.caption_track(clip, sc.caption_chunks(clip["words"]), brand, fonts, "fill", tmp)
    body = open(lst).read().splitlines()
    durs = [float(l.split()[1]) for l in body if l.startswith("duration")]
    check("caption list is ffconcat", body[0] == "ffconcat version 1.0")
    check("caption durations positive", all(d > 0 for d in durs))
    check("captions cover the clip", abs(sum(durs) - 10.5) < 0.6)
    frames = [l for l in body if l.startswith("file") and "blank" not in l]
    check("one caption frame per word", len(frames) == 9)
    # (editorial) captions stay legible for muted viewers: big enough at a glance,
    # but still calmer (smaller) than the (bold) look's word-highlight captions.
    check("editorial caption size is legible but calmer than bold",
          rd.CAP_SIZE > rd.EDITORIAL_CAP_SIZE >= 54)
    ed_frame = rd.editorial_caption_frame(["Hope", "is", "here"], brand, fonts, 1290)
    check("editorial caption frame draws something", ed_frame.getbbox() is not None)
    # the hook sits below Instagram's top bar, and only when it adds something
    rows_top = [y for y in range(0, 1920, 4)
                if any(im.getpixel((x, y))[:3] == tuple(int(brand["offwhite"].lstrip("#")[i:i + 2], 16)
                                                        for i in (0, 2, 4)) and im.getpixel((x, y))[3] == 255
                       for x in range(100, 980, 6))]
    check("hook text starts below Instagram's top bar", rows_top and min(rows_top) >= rd.HOOK_TOP - 10)
    check("hook shown when it adds to the opening words", rd.shows_hook(clip))
    check("no hook when he opens by saying it", not rd.shows_hook(dict(clip, hook="Hope is not wishful")))
    check("no hook without one", not rd.shows_hook(dict(clip, hook="")))
    plain = rd.make_overlay(dict(clip, hook="Hope is not wishful"), brand, "fill", fonts,
                            os.path.join(tmp, "o3.png"))
    check("no hook: no banner shade at the top", Image.open(plain).getpixel((540, 60))[3] == 0)
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

# the fallback variable font must come out bold and normal width, not thin and wide
_fb = rd.font("bold", 80, fonts) if os.path.exists(os.path.join(fonts, "Archivo.ttf")) else None
if _fb is not None and not rd.using_brand_font(fonts):
    check("fallback font is bold", abs(_fb.getlength("ALL IN") - rd.font("regular", 80, fonts).getlength("ALL IN")) > 5
          and _fb.getlength("MMMM") < 450)

# the drafts folder is named for the sermon title, not the whole YouTube title
_cwd_meta = sc.HERE
with tempfile.TemporaryDirectory() as _d:
    sc.HERE = _d
    json.dump([{"service_date": "2026-09-27", "video_id": "v",
                "title": "Are you all in? | Mark 1:14–20 | Peter Frey | Eastpoint Church Durham"}],
              open(os.path.join(_d, "uploaded_sermons.json"), "w"))
    _m = sc.sermon_meta(__import__("datetime").date(2026, 9, 27))
    sc.HERE = _cwd_meta
check("meta title is the sermon title", _m["title"] == "Are you all in?" and _m["scripture"] == "Mark 1:14–20"
      and _m["preacher"] == "Peter Frey")

# the weekly plan gives options with copy, not a posting schedule
_plan = sc.plan_html({"sermon_big_idea": "Jesus is king"},
                     [{"n": 1, "hook": "Expert or practitioner?", "start": 10.0, "end": 60.0,
                       "file": "a.mp4", "files": {}, "kind": "gospel", "why": "w", "text": "t",
                       "instagram_caption": "IG copy", "facebook_caption": "FB copy",
                       "youtube_title": "YT title #shorts", "youtube_description": "YT desc"}],
                     {"title": "Are you all in?"}, __import__("datetime").date(2026, 9, 27))
check("plan labels reels as options", "Option 1: Expert or practitioner?" in _plan)
check("plan carries each reel's copy", all(x in _plan for x in ("IG copy", "FB copy", "YT title #shorts")))
check("plan has no posting days", not any(d in _plan for d in (">Tue<", ">Thu<", ">Sat<", "Day</th>")))
check("clip prompt asks for no post day", "post_day" not in sc.PROMPT)
check("strategy has no weekly posting rhythm", "Weekly rhythm" not in open(sc.STRATEGY_FILE).read())
_strat = open(sc.STRATEGY_FILE).read()
check("each option gets its own next step", "a different one on each reel option" in _strat)
check("hashtags stay sparing", "At most two local tags" in _strat and "no emoji" in _strat.lower())
check("rubric asks clips to end strong", "Ends on its strongest line" in _strat)
check("using-the-reels still frames posting as options, not a schedule",
      "not a schedule" in _strat and "builds a steadier rhythm" in _strat)

# colour: measured white balance, levels, and one LUT per clip ----------------------
import math as _math
import numpy as _np
import grade as G
for _c in ((0.8, 0.6, 0.5), (0.3, 0.2, 0.15), (0.5, 0.5, 0.5)):
    _back = G.lab_to_rgb(G.rgb_to_lab(_c))
    check("lab round trip", all(abs(a - b) < 1e-3 for a, b in zip(_c, _back)))

def _lab_of(L, C, h):
    return G.lab_to_rgb((L, C * _math.cos(_math.radians(h)), C * _math.sin(_math.radians(h))))

def _hue_L(rgb):
    L, a, b = G.rgb_to_lab(rgb)
    return _math.degrees(_math.atan2(b, a)), L

def _apply(rgb, g):
    return tuple(float(v) for v in G.to_display(G.to_linear(rgb) * _np.array(g)))

check("face in the skin band: no correction", G.skin_gains(_lab_of(62, 16, 55)) is None)
for _label, _h in (("green cast", 95), ("magenta cast", 20), ("blue-ish light", 5)):
    _face = _lab_of(58, 18, _h)
    _g = G.skin_gains(_face)
    check(f"{_label}: gains given", _g is not None)
    _h2, _L2 = _hue_L(_apply(_face, _g))
    check(f"{_label}: hue moves toward skin", abs(_h2 - 55) < abs(_h - 55))
    check(f"{_label}: brightness kept", abs(_L2 - 58) < 3)
    check(f"{_label}: gains capped", all(0.9 - 1e-9 <= k <= 1.1 + 1e-9 for k in _g))
for _L, _C in ((30, 14), (50, 20), (75, 12)):
    check(f"skin tone L{_L} C{_C} left alone", G.skin_gains(_lab_of(_L, _C, 52)) is None)
check("grey face says nothing", G.skin_gains((0.5, 0.5, 0.5)) is None)
_ng = G.neutral_gains((0.62, 0.60, 0.70))                 # a blue-ish white wall
check("neutral gains cut the blue cast", _ng and _ng[2] < 1 and _ng[2] == min(_ng))
check("a pink wall does not overrule a good face",
      G.choose_wb(_lab_of(62, 16, 55), (0.75, 0.55, 0.60))[1] == "face"
      or G.choose_wb(_lab_of(62, 16, 55), (0.75, 0.55, 0.60))[0] is None)
check("neutrals used when they agree with the face",
      G.choose_wb(_lab_of(60, 15, 40), (0.60, 0.60, 0.66))[1] == "neutral")
_b, _w, _gm = G.levels({"p_lo": 0.08, "p_med": 0.30, "p_hi": 0.80, "face": None})
check("flat footage gets its range back", _b > 0.05 and _w <= 0.85 and 0.7 <= _gm <= 1.42)
_b2, _w2, _gm2 = G.levels({"p_lo": 0.0, "p_med": 0.42, "p_hi": 0.99, "face": None})
check("well-exposed footage barely moves", _b2 == 0 and _w2 == 1.0 and abs(_gm2 - 1) < 0.05)
_b3, _w3, _gm3 = G.levels({"p_lo": 0.02, "p_med": 0.2, "p_hi": 0.9, "face": (0.93, 0.8, 0.75)})
check("a bright face is kept off clipping",
      ((float(G.LUMA @ _np.array((0.93, 0.8, 0.75))) - _b3) / (_w3 - _b3)) ** _gm3 <= 0.881)
_lut = G.build_lut(None, 0.0, 1.0, 1.0, size=17)
_grey = _lut[:, :, :][_np.arange(17), _np.arange(17), _np.arange(17)]
check("greys stay neutral", float(_np.abs(_grey - _grey.mean(axis=1, keepdims=True)).max()) < 0.02)
check("tone curve keeps order", bool(_np.all(_np.diff(_grey.mean(axis=1)) > 0)))
_i = [int(round(v * 16)) for v in _lab_of(60, 18, 55)]
_skin_in = tuple(v / 16 for v in _i)                    # the grid point the LUT holds
_skin_out = tuple(float(v) for v in _lut[_i[2], _i[1], _i[0]])
check("skin hue survives the look", abs(_hue_L(_skin_out)[0] - _hue_L(_skin_in)[0]) < 4)
with tempfile.TemporaryDirectory() as _tmp:
    _cube = G.write_cube(G.build_lut((1.04, 0.99, 0.95), 0.03, 0.95, 0.95), os.path.join(_tmp, "g.cube"))
    _lines = open(_cube).read().splitlines()
    check("cube has every point", len(_lines) == 2 + 33 ** 3)
    _ff = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:d=0.2",
                          "-vf", f"lut3d=file='{_cube}':interp=tetrahedral", "-f", "null", "-"],
                         capture_output=True, text=True)
    check("ffmpeg applies the LUT", _ff.returncode == 0)
os.environ["SOCIAL_GRADE"] = "off"
check("grading can be switched off", G.grade_filter("none.mp4", 0, 1, "/tmp")[0] == "")
del os.environ["SOCIAL_GRADE"]

# re-rendering last run's clips: plan + word timings come back from the drafts folder
_drive_files = {"old": None,                       # a deleted folder
                "new": [{"id": "pj", "name": "plan.json"}, {"id": "tw", "name": "transcript_words.json"}]}
def _fake_list(drive, fid):
    if _drive_files.get(fid) is None:
        raise RuntimeError("not found")
    return _drive_files[fid]
def _fake_dl(drive, fid, dest):
    json.dump({"pj": {"clips": [{"start_sentence": 2, "end_sentence": 5, "hook": "h", "files": {"x": "y"},
                                 "start": 1.0, "reframe": {}}]},
               "tw": [{"w": "Hi.", "s": 0.0, "e": 0.4}]}[fid], open(dest, "w"))
_rl, _rd = sc.list_folder, sc.download
sc.list_folder, sc.download = _fake_list, _fake_dl
try:
    with tempfile.TemporaryDirectory() as _t:
        _st = [{"drive_file_id": "S", "drafts_folder_id": "new"}, {"drive_file_id": "S", "drafts_folder_id": "old"}]
        _r = sc.reuse_plan(None, {"id": "S"}, _st, _t)
        check("reuse finds the live folder past a deleted one", _r is not None and _r[2] == "new")
        check("reused clip keeps its sentences", _r[0]["clips"][0]["start_sentence"] == 2
              and _r[0]["clips"][0]["hook"] == "h")
        check("reused clip renders fresh", "files" not in _r[0]["clips"][0] and "start" not in _r[0]["clips"][0])
        check("reused word timings", _r[1][0]["w"] == "Hi.")
        check("nothing to reuse for another sermon", sc.reuse_plan(None, {"id": "T"}, _st, _t) is None)
finally:
    sc.list_folder, sc.download = _rl, _rd

# a title that arrived after the reels were made
_p = {"clips": [{"instagram_caption": 'Line.\n\nFrom "All In: Sunday sermon"\n\n#Durham',
                 "facebook_caption": 'Line.\n\nFrom "Sunday sermon"'}]}
check("captions retitled", sc.retitle_captions(_p, "A Title") == 2)
check("series kept in the credit", 'From "All In: A Title"\n' in _p["clips"][0]["instagram_caption"])
check("credit without a series", _p["clips"][0]["facebook_caption"].endswith('From "A Title"'))
check("no title, captions left alone", sc.retitle_captions(
    {"clips": [{"instagram_caption": 'From "Sunday sermon"'}]}, "") == 0)

# the tight cut ---------------------------------------------------------------
import reel_fx as rfx                                           # noqa: E402

def _w(*spec):
    return [{"w": w, "s": s, "e": e} for w, s, e in spec]

_ws = _w(("So", 0.0, 0.3), ("um,", 0.5, 0.8), ("Jesus", 0.9, 1.3), ("came", 1.35, 1.6),
         ("for", 1.62, 1.8), ("the", 1.82, 1.9), ("the", 2.0, 2.1), ("weary.", 2.15, 2.6),
         ("And,", 4.0, 4.3), ("like,", 4.35, 4.6), ("he", 4.7, 4.8), ("stays.", 4.85, 5.4))
check("fillers, asides and stumbles dropped", sorted(rfx.drop_words(_ws)) == [1, 5, 9])
check("'no, no, no' is not a stumble",
      not rfx.drop_words(_w(("No,", 0, .2), ("no,", .3, .5), ("no.", .6, .8))))
check("'you know,' as an aside", rfx.drop_words(_w(("and,", 0, .2), ("you", .3, .4), ("know,", .45, .6),
                                                    ("it", .7, .8))) == {1, 2})
check("'I like it' kept", not rfx.drop_words(_w(("I", 0, .1), ("like", .15, .3), ("it.", .35, .5))))
_segs = rfx.tight_segments(_ws, 0.0, 5.75, None, fps=30)
_total = sum(e - s for s, e in _segs)
check("tight cut is shorter", _total < 5.75 - 1.5)
check("cuts sit on the frame grid", all(abs(x * 30 - round(x * 30)) < 1e-6 for s in _segs for x in s))
check("segments in order, no overlap", all(a[1] <= b[0] for a, b in zip(_segs, _segs[1:])))
check("the um stays cut, even beside a short 'So'", not any(s < 0.6 < e for s, e in _segs))
check("slivers only beside a removed word",
      len(rfx.tight_segments(_w(("a", 0, .5), ("b", 1.0, 1.2), ("c", 1.7, 3.4)), 0, 3.7)) < 3)
check("a long pause is never put back for a sliver",
      len(rfx.tight_segments(_w(("a", 0, .5), ("b", 1.6, 1.8), ("c", 2.9, 3.4)), 0, 3.7)) == 3)
_tw = rfx.tight_words(_ws, _segs)
check("tight words lose the filler", [w["w"] for w in _tw] == ["So", "Jesus", "came", "for", "the",
                                                               "weary.", "And,", "he", "stays."])
check("tight words in time order", all(a["s"] <= b["s"] for a, b in zip(_tw, _tw[1:])))
check("tight words inside the cut", _tw[-1]["e"] <= _total + 1e-6)
check("whole sentence kept around a cut", any(s <= 2.15 and e >= 2.6 for s, e in _segs))
check("remap: a time in a cut lands on the cut", rfx.remap(3.2, [(0, 2.7), (3.9, 5)]) == 2.7)
check("remap after a cut", abs(rfx.remap(4.0, [(0, 2.7), (3.9, 5)]) - 2.8) < 1e-9)
check("a pause under the threshold stays",
      len(rfx.tight_segments(_w(("a", 0, .5), ("b", .6, 1.0)), 0, 1.3)) == 1)
check("fillers from the verbatim pass fill gaps only",
      len(rfx.merge_fillers(_w(("a", 0, .5), ("b", 1.5, 2)), [(0.7, 1.0), (0.1, 0.4)])) == 3)
_sp = rfx.zoom_spans([(0, 2), (3, 4), (5, 9), (10, 13), (14, 20)], 15)
check("zooms change at most every few seconds",
      all(b - a >= rfx.ZOOM_EVERY - 1e-9 for a, b in _sp) and len(_sp) >= 1)
check("no zoom without cuts", rfx.zoom_filter([]) == "null")
check("zoom: a zoomed copy over the clip, only inside the span",
      rfx.zoom_filter([(3, 6)], 0.2).endswith("overlay=0:0:enable='between(t,3.000,5.983)'"))
check("zoom keeps the frame size", ":crop=1080:1920:" in rfx.zoom_filter([(3, 6)]).replace(",crop", ":crop"))
_wav = os.path.join(tempfile.mkdtemp(), "bed.wav")
rfx.music_bed(_wav, 3.0, seed=2)
import wave as _wave                                            # noqa: E402
with _wave.open(_wav) as _f:
    check("music bed: stereo, 48 kHz, right length",
          (_f.getnchannels(), _f.getframerate(), round(_f.getnframes() / 48000, 1)) == (2, 48000, 3.0))
rfx.sfx_track(_wav, 2.0, at=(0.0,))
with _wave.open(_wav) as _f:
    check("sfx track the right length", round(_f.getnframes() / 48000, 1) == 2.0)

# ElevenLabs music and sound effects -------------------------------------------
import eleven                                                   # noqa: E402
_sent = []


_usage = {"character_count": 2000, "character_limit": 131000}
_scribe = {"words": [{"text": "Jesus", "type": "word", "start": 0.1, "end": 0.5},
                     {"text": " ", "type": "spacing", "start": 0.5, "end": 0.6},
                     {"text": "um,", "type": "word", "start": 0.6, "end": 0.9},
                     {"text": "(laughter)", "type": "audio_event", "start": 1.0, "end": 1.5},
                     {"text": "wept.", "type": "word", "start": 1.6, "end": 2.0}]}


class _Resp:
    def __init__(self, req):
        self.url = req.full_url
        if not self.url.endswith("/user/subscription"):
            _sent.append(req)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        if self.url.endswith("/user/subscription"):
            return json.dumps(_usage).encode()
        if self.url.endswith("/speech-to-text"):
            return json.dumps(_scribe).encode()
        return b"\xff" * 4000


def _fresh_budget():
    eleven._first, eleven._committed = None, 0


_fresh_budget()
with tempfile.TemporaryDirectory() as _d, \
        mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}), \
        mock.patch("urllib.request.urlopen", side_effect=lambda req, timeout=0: _Resp(req)):
    eleven.music(os.path.join(_d, "m.mp3"), 61.4, "gospel")
    _body = json.loads(_sent[-1].data)
    check("music: compose endpoint, key header", _sent[-1].full_url.endswith("/v1/music")
          and _sent[-1].get_header("Xi-api-key") == "test-key")
    check("music: instrumental, reel length", _body["force_instrumental"] is True
          and _body["music_length_ms"] == 61400 and "No drums, no vocals" in _body["prompt"])
    check("music: at least 3 s", json.loads((eleven.music(os.path.join(_d, "m2.mp3"), 1.0), _sent[-1].data)[1])
          ["music_length_ms"] == 3000)
    eleven.sound_effect(os.path.join(_d, "w.mp3"))
    check("sound effect: endpoint and duration", _sent[-1].full_url.endswith("/v1/sound-generation")
          and json.loads(_sent[-1].data)["duration_seconds"] == 1.0)
    # Scribe
    _a = os.path.join(_d, "a.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                    "-t", "60", _a], check=True)
    _w = eleven.transcribe(_a, ["Eastpoint", "Durham", "Eastpoint"])
    _form = _sent[-1].data
    check("Scribe: speech-to-text endpoint, word timings", _sent[-1].full_url.endswith("/v1/speech-to-text")
          and b'name="model_id"\r\n\r\nscribe_v2' in _form and b'name="timestamps_granularity"\r\n\r\nword' in _form)
    check("Scribe: keyterms sent once each", _form.count(b'name="keyterms"') == 2)
    check("Scribe: words only, verbatim (ums kept), timed",
          _w == [{"w": "Jesus", "s": 0.1, "e": 0.5}, {"w": "um,", "s": 0.6, "e": 0.9},
                 {"w": "wept.", "s": 1.6, "e": 2.0}])
    eleven.isolate(_a, os.path.join(_d, "iso.mp3"))
    check("Voice Isolator: audio-isolation endpoint, audio field",
          _sent[-1].full_url.endswith("/v1/audio-isolation") and b'name="audio"; filename=' in _sent[-1].data)
check("costs: Scribe 330, isolator 1000, music 900 credits a minute",
      (eleven.per_minute("transcribe", 60), eleven.per_minute("isolate", 30), eleven.per_minute("music", 120))
      == (330, 500, 1800))
check("keyterms: church, city, preacher, book",
      sc.keyterms({"preacher": "Peter Frey", "scripture": "1 John 4:7"})
      == ["Eastpoint", "Durham", "Eastpoint Church", "Peter Frey", "1 John"])

# the monthly budget: never spend past it, and fall back instead
_fresh_budget()
with tempfile.TemporaryDirectory() as _d, \
        mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key", }), \
        mock.patch.object(eleven, "BUDGET", 0.85), \
        mock.patch("urllib.request.urlopen", side_effect=lambda req, timeout=0: _Resp(req)):
    _usage.update(character_count=110000)                      # 84% of 131k used
    _n = len(_sent)
    try:
        eleven.music(os.path.join(_d, "m.mp3"), 120)             # +1,800 would pass 85%
        check("over budget: no call made", False)
    except eleven.OverBudget:
        check("over budget: no call made", len(_sent) == _n)
    _bed, _ = sc.reel_sound({"n": 1}, 120.0, _d, None, False)
    check("over budget: the reel still gets a bed (generated pad)", _bed.endswith(".wav"))
    _usage.update(character_count=100000)
    _fresh_budget()
    eleven.music(os.path.join(_d, "m1.mp3"), 60)               # 900: fine
    try:                                                        # the account still says 100k...
        for _ in range(20):
            eleven.music(os.path.join(_d, "m2.mp3"), 60)
        check("the run counts its own spending", False)
    except eleven.OverBudget:                                   # ...but this run has spent more
        check("the run counts its own spending", eleven._committed <= 131000 * 0.85 - 100000)
    _usage.update(character_count=2000)
_fresh_budget()
with mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}), \
        mock.patch("urllib.request.urlopen", side_effect=OSError("offline")):
    try:
        eleven.reserve(10, "test")
        check("usage unreadable: do not spend", False)
    except eleven.OverBudget:
        check("usage unreadable: do not spend", True)
_fresh_budget()

# a rerun reuses last run's isolated voice when the cut is the same
with tempfile.TemporaryDirectory() as _d, \
        mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}), \
        mock.patch("eleven.isolate", side_effect=AssertionError("should not be called")) as _iso:
    _src = os.path.join(_d, "clip1t_src.mkv")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=64x64:d=3",
                    "-f", "lavfi", "-i", "sine=f=200:d=3", "-c:v", "libx264", "-c:a", "pcm_s16le",
                    "-shortest", _src], check=True)
    _segs = [(0.0, 1.5), (2.0, 3.5)]
    import hashlib as _h
    _cut = _h.sha1(json.dumps([[0.0, 1.5], [2.0, 3.5]]).encode()).hexdigest()[:8]
    _prev = os.path.join(_d, f"voice reel 1 {_cut}.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=300:d=3", _prev], check=True)
    _ok = sc.isolate_voice(_src, {"n": 1, "_voice_cache": {os.path.basename(_prev): _prev}}, _segs, _d, None)
    check("rerun reuses the isolated voice: no new credits", _ok and not _iso.called)
    check("isolated voice swapped into the cut, same length",
          abs(sc.audio_seconds(_src) - 3.0) < 0.2)
with mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": ""}):
    check("no key: ElevenLabs is skipped", not eleven.available())
    with tempfile.TemporaryDirectory() as _d:
        _bed, _sfx = sc.reel_sound({"n": 1}, 4.0, _d, None, True)
        check("no key: the generated pad and whoosh stand in", _bed.endswith(".wav") and _sfx.endswith(".wav"))
with tempfile.TemporaryDirectory() as _d, \
        mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}), \
        mock.patch("eleven.music", side_effect=RuntimeError("HTTP 401")), \
        mock.patch("eleven.sound_effect", side_effect=RuntimeError("HTTP 401")):
    _bed, _sfx = sc.reel_sound({"n": 1}, 4.0, _d, None, True)
    check("ElevenLabs failing never loses a reel", os.path.exists(_bed) and os.path.exists(_sfx))
with tempfile.TemporaryDirectory() as _d, \
        mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}), \
        mock.patch("eleven.music", side_effect=AssertionError("should not be called")) as _m:
    _old = os.path.join(_d, "music reel 1.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                    "-t", "6", _old], check=True)
    _bed, _ = sc.reel_sound({"n": 1}, 4.0, _d, None, False, cached=_old)
    check("rerun reuses last run's music: no new credits", _bed == _old and not _m.called)
with tempfile.TemporaryDirectory() as _d, \
        mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test-key"}), \
        mock.patch("eleven.music", side_effect=lambda path, *a, **k: open(path, "wb").write(b"x") and path):
    _old = os.path.join(_d, "short.mp3")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                    "-t", "2", _old], check=True)
    _keep = os.path.join(_d, "keep"); os.makedirs(_keep)
    _bed, _ = sc.reel_sound({"n": 2}, 4.0, _d, None, False, cached=_old, keep_dir=_keep)
    check("a reel grown past last run's music gets a new bed, kept for next time",
          _bed != _old and os.path.exists(os.path.join(_keep, "music reel 2.mp3")))

# lengthening a reel ---------------------------------------------------------------
_ss = [{"a": i, "b": i, "start": i * 5.0, "end": i * 5.0 + 4.6, "text": f"Sentence {i}" + ("," if i == 9 else ".")}
       for i in range(30)]
_pl = {"clips": [{"start_sentence": 4, "end_sentence": 8}, {"start_sentence": 15, "end_sentence": 18}]}
_g = sc.extend_clip(_pl, _ss, 1, 88)
_c1 = _pl["clips"][0]
check("reel grows", _g and _g[1] > _g[0])
check("grown reel stays under the cap", _g[1] <= sc.MAX_LEN)
check("never runs into the next reel", _c1["end_sentence"] < 14)
check("ends on a whole thought", _ss[_c1["end_sentence"]]["text"].endswith("."))
check("grows backward when blocked", _c1["start_sentence"] < 4)
_pl2 = {"clips": [{"start_sentence": 4, "end_sentence": 8}]}
sc.extend_clip(_pl2, _ss, 1, 40)
check("stops near the target", _ss[_pl2["clips"][0]["end_sentence"]]["end"] - _ss[4]["start"] <= 40)
check("no such reel", sc.extend_clip(_pl2, _ss, 3, 88) is None)
_res = sc.resolve_clips(_pl, _ss, [{"w": "x", "s": i * 5.0, "e": i * 5.0 + 4.6} for i in range(30)])
check("grown plan keeps every reel", len(_res) == 2)

# hand-set cut points: start and end on given words
_tw = words_from("I have none. But he does the whole church. He meets us. We all need him and more.")
_ts = sc.sentences_from_words(_tw)
for _x in _tw:
    _x["s"] *= 6; _x["e"] *= 6                    # stretch to reel length
_ts = sc.sentences_from_words(_tw)
_tp = {"clips": [{"start_sentence": 0, "end_sentence": 2}]}
_note = sc.trim_clip(_tp, _ts, _tw, 1, "the whole church", "need him")
_rc = sc.resolve_clips(_tp, _ts, _tw)
check("reel starts at the chosen words", _rc and _rc[0]["text"].startswith("the whole church"))
check("reel ends after the chosen words", _rc and _rc[0]["text"].endswith("need him"))
check("missing phrase is reported", "not found" in sc.trim_clip(_tp, _ts, _tw, 1, "no such words"))

# audio: hiss and rumble out before the loudness step ---------------------------------
import audio as AU
_hissy = AU.clean_filter({"floor": -62.0, "speech": -19.0})
check("hiss is denoised", "afftdn=" in _hissy and "nf=-62" in _hissy and "highpass=f=75" in _hissy)
check("pauses get a gentle gate", "agate=" in _hissy and "range=0.25" in _hissy)
_thr = float(_hissy.split("threshold=")[1].split(":")[0])
check("gate sits between hiss and voice", 10 ** (-62 / 20) < _thr < 10 ** (-19 / 20))
check("clean audio only loses rumble", AU.clean_filter({"floor": -95.0, "speech": -20.0}) == "highpass=f=75")
check("no voice above the noise: no denoise", "afftdn" not in AU.clean_filter({"floor": -40.0, "speech": -35.0}))
_nr = lambda f, sp: int(AU.clean_filter({"floor": f, "speech": sp}).split("nr=")[1].split(":")[0])
check("noisier recording, firmer reduction", _nr(-45, -20) >= _nr(-70, -20))
check("reduction stays in range", all(AU.MIN_NR <= _nr(f, -20) <= AU.MAX_NR for f in (-75, -60, -45, -35)))
check("unmeasurable audio still loses rumble", AU.clean_filter(None) == "highpass=f=75")
with tempfile.TemporaryDirectory() as _t:
    _w = os.path.join(_t, "v.wav")          # 2 s of "voice" (a tone) then 2 s of hiss, twice
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=a=0.003:d=8:r=16000",
                    "-f", "lavfi", "-i", "sine=f=220:d=8:r=16000",
                    "-filter_complex", "[1]volume='if(lt(mod(t,4),2),0.3,0)':eval=frame[s];[0][s]amix=2:normalize=0",
                    _w], check=True)
    _lv = AU.measure(_w)
    check("measure finds the hiss floor", -65 < _lv["floor"] < -45)
    check("measure finds the voice", _lv["speech"] > _lv["floor"] + 20)
    _chain = AU.clean_filter(_lv) + ",loudnorm=I=-14:TP=-1.5:LRA=11"
    _ff = subprocess.run(["ffmpeg", "-v", "error", "-i", _w, "-af", _chain, "-f", "null", "-"],
                         capture_output=True, text=True)
    check("ffmpeg accepts the audio chain", _ff.returncode == 0)
_rnn = AU.clean_filter({"floor": -50.0, "speech": -20.0}, "/m/std.rnnn")
check("noisy recording: neural denoise under the voice", "arnndn=m='/m/std.rnnn'" in _rnn)
check("...mixed with the original, so word endings and music survive", f"mix={AU.RNN_MIX}" in _rnn and AU.RNN_MIX <= 0.6)
check("...with no FFT pass stacked on top (the two compound)", "afftdn" not in _rnn)
check("...and a softer expander after it", "range=0.5" in _rnn and "release=500" in _rnn)
_clean = AU.clean_filter({"floor": -75.7, "speech": -29.2}, "/m/std.rnnn")     # the 2026-11-01 sermon
check("clean recording: no neural denoise even with the model", "arnndn" not in _clean)
check("...just a light FFT pass", f"nr={AU.CLEAN_NR}:" in _clean)
check("no model, noisy recording: FFT works harder", _nr(-50, -20) >= AU.MIN_NR + 4)
_m = AU.rnn_model() if os.path.exists(os.path.join(AU.RNN_DIR, f"{AU.RNN_MODEL}.rnnn")) else None
if _m:
    with tempfile.TemporaryDirectory() as _t:
        _ff = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=a=0.003:d=2:r=48000",
                              "-af", AU.clean_filter({"floor": -50.0, "speech": -20.0}, _m), "-f", "null", "-"],
                             capture_output=True, text=True)
        check("ffmpeg runs the neural denoiser", _ff.returncode == 0)
os.environ["AUDIO_CLEAN"] = "off"
check("audio cleaning can be switched off", AU.filters("none.wav")[0] == "")
del os.environ["AUDIO_CLEAN"]

# soundboard audio: found in sync, wrong file refused ------------------------------
import board_audio as BA
with tempfile.TemporaryDirectory() as _t:
    _env = "volume='0.15+0.85*abs(sin(t*3.1)*sin(t*1.7+1)*sin(t*0.53))':eval=frame"
    _bd, _vd, _wr = (os.path.join(_t, n) for n in ("board.wav", "video.mp4", "wrong.wav"))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=a=0.3:d=110:r=48000:s=7",
                    "-af", _env, _bd], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=160x120:r=30:d=90",
                    "-i", _bd, "-f", "lavfi", "-i", "anoisesrc=a=0.02:d=90:r=48000:s=3",
                    "-filter_complex", "[1]atrim=start=12.5,asetpts=PTS-STARTPTS,lowpass=f=4000,volume=0.5[c];"
                    "[c][2]amix=2:duration=shortest:normalize=0[a]",
                    "-map", "0:v", "-map", "[a]", "-t", "90", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", _vd], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", _bd, "-af", "areverse", _wr], check=True)
    _m = BA.measure(_vd, _bd)
    check("board offset found", "offset" in _m and abs(_m["offset"] - 12.5) < 0.01)
    check("no drift invented", "drift" in _m and abs(_m["drift"]) < 1e-4)
    check("wrong board file refused", "error" in BA.measure(_vd, _wr))
    _out, _note = BA.apply(_vd, _bd, _t)
    check("video gets the board audio", _out != _vd and "board audio in" in _note)
    _vcodec = lambda f: subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                        "stream=codec_name,nb_frames", "-of", "csv=p=0", f],
                                       capture_output=True, text=True).stdout.strip()
    check("picture copied, not re-encoded", _vcodec(_out) == _vcodec(_vd))
    _out2, _note2 = BA.apply(_vd, _wr, _t)
    check("camera audio kept for a wrong file", _out2 == _vd and "kept camera audio" in _note2)

# thumbnails -------------------------------------------------------------------
import thumbnail as T
from PIL import ImageChops
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
_shots = [{"name": f"P {d} {x}.jpg"} for d, xs in (("2025-09-28", "abcd"), ("2026-03-08", "ab"),
                                                    ("2026-05-17", "a")) for x in xs]
_picks = [T.pick_photo(_shots, d1 + timedelta(weeks=i))["name"][:12] for i in range(12)]
check("next week is always a different shoot", all(a != b for a, b in zip(_picks, _picks[1:])))
check("every shot comes round", len({T.pick_photo(_shots, d1 + timedelta(weeks=i))["name"]
                                     for i in range(24)}) == len(_shots))
_stage = [(0.18, 0.29, 0.07, 0.95), (0.02, 0.32, 0.09, 0.91), (0.81, 0.18, 0.07, 0.91), (0.49, 0.32, 0.09, 0.91)]
check("preacher photo frames the preacher, not the band", T.speaker_face(_stage)[0] == 0.49)
check("no faces, no speaker", T.speaker_face([]) is None)
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
_cut = T.cutout(T.trim_to_content(wall))
check("lockup background keyed out", _cut.getpixel((2, 2))[3] == 0
      and max(_cut.getchannel("A").getdata()) == 255)
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
    check("full-bleed fills the frame", T._full_bleed(_Im.new("RGB", (3000, 2000), (90, 80, 70)),
                                                       (25, 25, 25)).size == (1280, 720))
    check("no face found is not an error", T.face_box(_Im.new("RGB", (400, 300), (0, 0, 0))) is None)
    check("series look uses the art's ground",
          Image.open(out).convert("RGB").getpixel((20, 700))[0] > 120)
    _g = dict(entry, preacher="Riley Davis", guest=True)
    _pk = {"photo_path": os.path.join(tmp, "p.jpg"), "art_path": os.path.join(tmp, "art.png")}
    T.render(entry, d1, brand, fonts, os.path.join(tmp, "h.jpg"), **_pk)
    T.render(_g, d1, brand, fonts, os.path.join(tmp, "g.jpg"), **_pk)
    _diff = ImageChops.difference(Image.open(os.path.join(tmp, "h.jpg")).convert("RGB"),
                                  Image.open(os.path.join(tmp, "g.jpg")).convert("RGB")).getbbox()
    check("guest week: a label above the title, on the left", bool(_diff) and _diff[0] < 120 and _diff[3] < 620)
    T.render(_g, d1, brand, fonts, os.path.join(tmp, "gn.jpg"))
    check("guest label in the no-photo look too", os.path.exists(os.path.join(tmp, "gn.jpg")))
    T.render(None, d1, brand, fonts, os.path.join(tmp, "n.jpg"))
    check("thumbnail without a planning entry", os.path.exists(os.path.join(tmp, "n.jpg")))


# photo choice: the preacher; community only when the preacher has none
_tree = {"root": [{"id": "pf", "name": "Peter Frey", "mimeType": T.FOLDER_MIME},
                  {"id": "cm", "name": "Community", "mimeType": T.FOLDER_MIME},
                  {"id": "x", "name": "stray.jpg", "mimeType": "image/jpeg"}],
         "pf": [{"id": "p1", "name": "Peter 1.jpg"}],
         "cm": [{"id": "c1", "name": "Band.jpg"}, {"id": "c2", "name": "Hug.jpg"},
                {"id": "c3", "name": "Hands.jpg"}]}
_real_list = sc.list_folder
sc.list_folder = lambda drive, fid: _tree.get(fid, [])
try:
    check("preacher every week", T.choose_photo(None, "root", "Peter Frey", _date(2026, 10, 4))["id"] == "p1"
          and T.choose_photo(None, "root", "Peter Frey", _date(2026, 10, 11))["id"] == "p1")
    check("guest with no folder gets a community photo",
          T.choose_photo(None, "root", "Brentley Wright", _date(2026, 10, 11))["id"].startswith("c"))
    _opts = T.community_options(None, "root", _date(2026, 10, 4))
    check("two community backup options", len(_opts) == 2 and len({o["id"] for o in _opts}) == 2)
    check("backups skip the photo already used",
          all(o["id"] != "c1" for o in T.community_options(None, "root", _date(2026, 10, 4), skip="c1")))
    check("backups change week to week", T.community_options(None, "root", _date(2026, 10, 4))
          != T.community_options(None, "root", _date(2026, 10, 11)))
    check("loose photos in the root are never used",
          T.choose_photo(None, "root", "Nobody", _date(2026, 10, 4))["id"].startswith("c"))
    _tree["cm"] = []
    check("no community folder photos, no backups", T.community_options(None, "root", _date(2026, 10, 4)) == [])
    check("nothing to choose gives no photo", T.choose_photo(None, "root", "Nobody", _date(2026, 10, 4)) is None)
finally:
    sc.list_folder = _real_list


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
import numpy as _np2                                            # noqa: E402
_crop = 0.27                                    # this week's crop: ~27% of the stage wide
_walk = _np2.concatenate([_np2.linspace(0.40, 0.47, 90), _np2.full(60, 0.47)])  # 3 s stroll
_cam = RF.follow(_walk, _crop)
check("camera keeps a slow walker well inside a tight crop",
      _np2.max(_np2.abs(_walk - _cam)) < 0.2 * _crop)
check("camera holds still for small sways",
      _np2.ptp(RF.follow(0.5 + 0.01 * _np2.sin(_np2.arange(300) / 5), _crop)) == 0)
check("no speaker when nobody is seen", RF.pick_speaker([(0, []), (6, [])]) == [])

# wide shots: closer crop, captions in the space above the head
check("face low in a wide crop", RF.face_position(0.55, 0.95) > RF.LOW_FACE)
check("head room kept in a tight crop", abs(RF.face_position(0.40, 0.4) - RF.HEAD_ROOM) < 0.01)
_br = json.load(open("social/brand.json"))
_fd = "social/fonts"
_hi = {"hook": "A hook", "head_top": 0.55}
_lo = {"hook": "A hook", "head_top": 0.20}
for _st in ("bold", "editorial"):
    _y = rd.caption_y(_hi, _br, _fd, "fill", _st)
    check(f"{_st}: captions move above a low head", _y < 0.55 * rd.H - 100)
    check(f"{_st}: captions stay put with no room", rd.caption_y(_lo, _br, _fd, "fill", _st) > 1200)
    check(f"{_st}: unknown head keeps default", rd.caption_y({"hook": "A hook"}, _br, _fd, "fill", _st) > 1200)

import numpy as _np                                             # noqa: E402
check("numpy numbers survive the plan JSON",
      json.loads(json.dumps({"upscale": _np.float32(2.0), "n": _np.int64(3)}, default=sc.json_safe))
      == {"upscale": 2.0, "n": 3})

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
