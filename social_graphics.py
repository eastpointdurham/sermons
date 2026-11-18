"""Weekly sermon graphics, alongside the reels: a "sermon in slides" carousel and a
quote card, as draft images in the week's Drive "Social Drafts" folder.

Claude reads the transcript and picks the words (the preacher's own, lightly
trimmed, never invented); Pillow sets them in the series' colours (sampled from the
series art, as the thumbnails do) and the brand font. Nothing is posted.

  Carousel   1080x1350 (4:5) slides: cover, 4-6 moves of the sermon, the key verse,
             a closing slide with the invitation.
  Quote card 1080x1350 for the feed and 1080x1920 for Stories.
"""
import json
import os
import re

from PIL import Image, ImageDraw

import reel_design as rd
import thumbnail as th

W, H = 1080, 1350
STORY_H = 1920
MARGIN = 96

GRAPHICS_PROMPT = """You are the social media producer for Eastpoint Church in Durham, NC. \
Below is the church's social media strategy, then Sunday's sermon transcript.

<strategy>
{strategy}
</strategy>

Sermon: {title}
Scripture: {scripture}
Preacher: {preacher}

<transcript>
{transcript}
</transcript>

Make two static Instagram/Facebook posts from this sermon.

1. A "sermon in slides" carousel that walks someone who was not in the room through the \
sermon's argument in 6 to 8 slides: a cover that stops the scroll, then the sermon's \
moves in order, one idea per slide, the key verse, and a closing slide that lands on \
what Jesus does. Use the preacher's own words wherever you can, trimmed (drop fillers \
and asides, keep his meaning and phrasing); never put words in his mouth. Headlines \
are short (at most 70 characters). Body lines are optional and at most 160 characters. \
Scripture must be quoted exactly as read aloud in the transcript (that is the NIV), \
trimmed only at a phrase boundary, with its reference ("Mark 3:14 NIV"). No names of \
congregants. No announcements.

2. A quote card: one line from the sermon, verbatim (fillers removed), at most 110 \
characters, that stands on its own for someone who has never been to church. Prefer a \
different line from the carousel cover.

For each, "highlight" is an exact substring of its text (2 to 4 words) to set in the \
accent colour with a hand-drawn underline, or "".

Return ONLY a JSON object, no prose. It must parse as JSON: escape double quotes \
inside strings (\\") and write line breaks as \\n.
{{
  "carousel": {{
    "slides": [
      {{"kind": "cover", "text": "...", "highlight": "..."}},
      {{"kind": "point", "kicker": "one or two words, e.g. The crowd", "text": "...", "body": "..."}},
      {{"kind": "scripture", "text": "exact verse words", "ref": "Mark 3:14 NIV"}},
      {{"kind": "close", "text": "...", "highlight": "..."}}
    ],
    "instagram_caption": "per the strategy's caption rules; next step: save this or send it to a friend; eastpointdurham.com; at most two local hashtags",
    "facebook_caption": "same copy, no hashtags"
  }},
  "quote": {{
    "text": "...",
    "highlight": "...",
    "instagram_caption": "per the caption rules; next step: come Sunday",
    "facebook_caption": "same copy, no hashtags"
  }}
}}"""


def pick_content(transcript_text, meta):
    """Claude chooses the slides and the quote from the sermon's own words."""
    import anthropic
    import social_clips as sc
    prompt = GRAPHICS_PROMPT.format(
        strategy=open(sc.STRATEGY_FILE).read(), title=meta.get("title") or "Sunday sermon",
        scripture=meta.get("scripture", ""), preacher=meta.get("preacher", "Peter Frey"),
        transcript=transcript_text)
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": prompt}]
    for attempt in range(3):
        msg = client.messages.create(model=sc.MODEL, max_tokens=16000,
                                     output_config={"effort": "high"}, messages=messages)
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        try:
            return check_content(sc.parse_plan(text))
        except ValueError as e:
            if attempt == 2:
                raise
            sc.log(f"  graphics plan not usable ({e}, stop: {msg.stop_reason}); asking again")
            if text.strip():                   # show it its own reply and ask for a fix
                messages = [messages[0], {"role": "assistant", "content": text},
                            {"role": "user", "content": f"That was not valid JSON ({e}). Return "
                             "the same content again as ONLY valid JSON: escape every double "
                             "quote inside a string as \\\" and every line break as \\n."}]


def check_content(c):
    slides = c.get("carousel", {}).get("slides") or []
    if len(slides) < 3 or not (c.get("quote") or {}).get("text"):
        raise ValueError("too few slides or no quote")
    for s in slides + [c["quote"]]:
        if s.get("highlight") and s["highlight"] not in s.get("text", ""):
            s["highlight"] = ""
    return c


# --------------------------------------------------------------------------
# look
# --------------------------------------------------------------------------

def series_look(brand, art_path=None):
    """Colours and lockup from the series art; the brand's own when there is none."""
    look = {"ground": rd.hex_rgb(brand["ink"]), "text": rd.hex_rgb(brand["offwhite"]),
            "accent": rd.hex_rgb(brand["sage"]), "lockup": None}
    if art_path and os.path.exists(art_path):
        art = Image.open(art_path)
        pal = th.palette_from_art(art)
        if pal:
            look.update(pal)
            look["lockup"] = th.trim_to_content(th.cutout(art), pad=0.01)
    look["muted"] = tuple(round(t * 0.62 + g * 0.38) for t, g in zip(look["text"], look["ground"]))
    return look


def _hex(c):
    return "#%02x%02x%02x" % tuple(c[:3])


def ground(look, h=H):
    return th._grain(Image.new("RGB", (W, h), look["ground"]), amount=7).convert("RGBA")


def _span(words, highlight):
    """Indexes of the words that make up the highlight phrase."""
    hw = highlight.split()
    if not hw:
        return set()
    norm = lambda w: re.sub(r"[^\w']", "", w.lower())
    for i in range(len(words) - len(hw) + 1):
        if [norm(w) for w in words[i:i + len(hw)]] == [norm(w) for w in hw]:
            return set(range(i, i + len(hw)))
    return set()


def fit_font(text, weight, max_w, max_h, sizes, fonts_dir, leading=1.08):
    """The biggest size at which the text wraps into the box."""
    for size in sizes:
        f = rd.font(weight, size, fonts_dir)
        lines = rd.wrap_words(text.split(), f, max_w)
        if len(lines) * size * leading <= max_h:
            return f, size, lines
    f = rd.font(weight, sizes[-1], fonts_dir)
    return f, sizes[-1], rd.wrap_words(text.split(), f, max_w)


def draw_text(img, x, y, text, weight, size_range, max_w, max_h, look, fonts_dir,
              highlight="", leading=1.08, color=None, underline=True):
    """Set a block of text top-left at (x, y); the highlight words in the accent with
    a hand-drawn underline. Returns the y below the block."""
    text = smart_quotes(text)
    highlight = smart_quotes(highlight)
    f, size, lines = fit_font(text, weight, max_w, max_h, size_range, fonts_dir, leading)
    words = text.split()
    hl = _span(words, highlight)
    draw = ImageDraw.Draw(img)
    space = f.getlength(" ")
    i, base = 0, y + size * 0.82
    strokes = []
    for line in lines:
        cx, run = x, None
        for w in line:
            on = i in hl
            draw.text((cx, base), w, font=f, anchor="ls",
                      fill=look["accent"] if on else (color or look["text"]))
            if on:
                run = (run[0], cx + f.getlength(w)) if run else (cx, cx + f.getlength(w))
            cx += f.getlength(w) + space
            i += 1
        if run:
            strokes.append((run, base))
            if underline and line is not lines[-1]:
                base += size * 0.36            # room for the underline before the next line
        base += size * leading
    if underline:
        for n, ((x0, x1), b) in enumerate(strokes):
            length = int(x1 - x0 + size * 0.2)
            s = rd.brush_stroke(length, max(10, int(size * 0.13)), _hex(look["accent"]), seed=11 + n)
            img.alpha_composite(s, (int(x0 - size * 0.08), int(b + size * 0.12)))
    return int(base - size * 0.82)


def smart_quotes(t):
    t = re.sub(r'(^|[\s(\[])"', "\\1\u201c", t or "")
    t = t.replace('"', "\u201d")
    t = re.sub(r"(\w)'", "\\1\u2019", t)
    return t.replace("'", "\u2018")


def lockup(img, look, width, pos):
    if look.get("lockup") is not None:
        lk = look["lockup"]
        lk = lk.resize((width, round(lk.height * width / lk.width)), Image.LANCZOS)
        img.alpha_composite(lk, pos)
        return lk.height
    return 0


def footer(img, look, fonts_dir, left, right="", h=H):
    f = rd.font("medium", 26, fonts_dir)
    draw = ImageDraw.Draw(img)
    y = h - 84
    draw.line((MARGIN, y - 44, W - MARGIN, y - 44), fill=look["muted"], width=2)
    rd.draw_tracked(draw, MARGIN, y, left.upper(), f, look["muted"], tracking=2)
    if right:
        tw = rd.text_width(right.upper(), f, 2)
        rd.draw_tracked(draw, W - MARGIN - tw, y, right.upper(), f, look["muted"], tracking=2)


def swipe(img, look, fonts_dir, h=H):
    """A small hand-drawn arrow, bottom right: there is more."""
    s = rd.brush_stroke(88, 9, _hex(look["accent"]), seed=5, tail=False)
    img.alpha_composite(s, (W - MARGIN - 120, h - 190))
    d = ImageDraw.Draw(img)
    tip = (W - MARGIN - 22, h - 186)
    d.line((tip[0] - 26, tip[1] - 22, tip[0], tip[1]), fill=look["accent"], width=9)
    d.line((tip[0] - 26, tip[1] + 24, tip[0], tip[1]), fill=look["accent"], width=9)


# --------------------------------------------------------------------------
# slides
# --------------------------------------------------------------------------

def render_slide(slide, n, total, meta, look, fonts_dir):
    img = ground(look)
    kind = slide.get("kind", "point")
    title = meta.get("title") or ""
    tw = W - 2 * MARGIN
    if kind == "cover":
        lockup(img, look, 440, (MARGIN - 10, MARGIN - 10))
        draw_text(img, MARGIN, 330, slide["text"], "bold", range(118, 60, -4), tw, 700,
                  look, fonts_dir, slide.get("highlight", ""), leading=1.04)
        f = rd.font("semibold", 40, fonts_dir)
        d = ImageDraw.Draw(img)
        d.text((MARGIN, H - 250), title, font=f, fill=look["text"], anchor="ls")
        d.text((MARGIN, H - 196), meta.get("scripture", ""), font=rd.font("regular", 34, fonts_dir),
               fill=look["muted"], anchor="ls")
        swipe(img, look, fonts_dir)
        footer(img, look, fonts_dir, "Eastpoint Church Durham", f"1 / {total}")
    elif kind == "scripture":
        d = ImageDraw.Draw(img)
        d.text((MARGIN - 10, 380), "“", font=rd.font("bold", 260, fonts_dir),
               fill=look["accent"], anchor="ls")
        y = draw_text(img, MARGIN, 470, slide["text"], "medium", range(80, 40, -2), tw, 560,
                      look, fonts_dir, leading=1.18)
        rd.draw_tracked(d, MARGIN, y + 70, slide.get("ref", "").upper(),
                        rd.font("semibold", 32, fonts_dir), look["accent"], tracking=3)
        footer(img, look, fonts_dir, title, f"{n} / {total}")
    elif kind == "close":
        y = draw_text(img, MARGIN, 300, slide["text"], "bold", range(104, 56, -4), tw, 520,
                      look, fonts_dir, slide.get("highlight", ""), leading=1.04)
        d = ImageDraw.Draw(img)
        lines = ["Full message: link in bio", "Sundays at 10am", "Oak Grove Elementary, Durham"]
        fy = max(y + 120, H - 470)
        for i, line in enumerate(lines):
            d.text((MARGIN, fy + i * 54), line, font=rd.font("semibold" if i == 0 else "regular", 38,
                                                             fonts_dir),
                   fill=look["text"] if i == 0 else look["muted"], anchor="ls")
        mark = rd.sunburst(64, _hex(look["accent"]))
        img.alpha_composite(mark, (W - MARGIN - 64, fy - 60))
        footer(img, look, fonts_dir, "eastpointdurham.com", f"{n} / {total}")
    else:
        d = ImageDraw.Draw(img)
        kicker = (slide.get("kicker") or "").upper()
        num = f"{slide.get('_point', n - 1):02d}"
        rd.draw_tracked(d, MARGIN, 250, num, rd.font("bold", 34, fonts_dir), look["accent"], 3)
        if kicker:
            rd.draw_tracked(d, MARGIN + 80, 250, kicker, rd.font("semibold", 30, fonts_dir),
                            look["muted"], tracking=3)
        y = draw_text(img, MARGIN, 330, slide["text"], "bold", range(96, 54, -4), tw, 520,
                      look, fonts_dir, slide.get("highlight", ""), leading=1.05)
        if slide.get("body"):
            draw_text(img, MARGIN, y + 56, slide["body"], "regular", range(44, 30, -2), tw - 40,
                      H - y - 300, look, fonts_dir, leading=1.32, color=look["muted"])
        if n < total:
            swipe(img, look, fonts_dir)
        footer(img, look, fonts_dir, title, f"{n} / {total}")
    return img.convert("RGB")


def render_quote(quote, meta, look, fonts_dir, h=H):
    img = ground(look, h)
    tw = W - 2 * MARGIN
    top = 300 if h == H else 640
    d = ImageDraw.Draw(img)
    d.text((MARGIN - 10, top - 20), "“", font=rd.font("bold", 300, fonts_dir),
           fill=look["accent"], anchor="ls")
    y = draw_text(img, MARGIN, top + 20, quote["text"], "bold", range(100, 52, -4), tw, 620,
                  look, fonts_dir, quote.get("highlight", ""), leading=1.06)
    who = meta.get("preacher") or "Eastpoint Church"
    d.text((MARGIN, y + 90), who, font=rd.font("semibold", 38, fonts_dir), fill=look["text"],
           anchor="ls")
    src = " · ".join(x for x in (meta.get("title"), meta.get("scripture")) if x)
    d.text((MARGIN, y + 142), src, font=rd.font("regular", 32, fonts_dir), fill=look["muted"],
           anchor="ls")
    lockup(img, look, 260, (MARGIN - 6, h - 290 if h == H else h - 420))
    footer(img, look, fonts_dir, "Eastpoint Church Durham", "eastpointdurham.com", h=h)
    return img.convert("RGB")


def render_all(content, meta, look, fonts_dir, out_dir, prefix):
    """Write every image; returns [(path, name)]."""
    os.makedirs(out_dir, exist_ok=True)
    out = []
    slides = content["carousel"]["slides"]
    points = 0
    for s in slides:
        if s.get("kind", "point") == "point":
            points += 1
            s["_point"] = points
    for i, s in enumerate(slides, 1):
        name = f"{prefix} carousel {i:02d}.png"
        render_slide(s, i, len(slides), meta, look, fonts_dir).save(os.path.join(out_dir, name))
        out.append(name)
    for h, label in ((H, "quote card.png"), (STORY_H, "quote card (story).png")):
        name = f"{prefix} {label}"
        render_quote(content["quote"], meta, look, fonts_dir, h).save(os.path.join(out_dir, name))
        out.append(name)
    return [(os.path.join(out_dir, n), n) for n in out]


def captions_text(content):
    c, q = content["carousel"], content["quote"]
    return ("CAROUSEL\n\nInstagram\n" + c.get("instagram_caption", "") + "\n\nFacebook\n"
            + c.get("facebook_caption", "") + "\n\n\nQUOTE CARD\n\nInstagram\n"
            + q.get("instagram_caption", "") + "\n\nFacebook\n" + q.get("facebook_caption", "")
            + "\n")


def transcript_text(words):
    """The sermon as plain text, fillers and stumbles out (Claude reads this)."""
    import reel_fx
    drop = reel_fx.drop_words(words)
    return " ".join(w["w"] for i, w in enumerate(words) if i not in drop)


def series_art(drive, service_date, sermons_folder, workdir):
    """The series art for this Sunday, from the planning doc's series name."""
    import social_clips as sc
    try:
        import upload_sermon as us
        series = (us.read_planning_doc(us.docs_service()).get(service_date) or {}).get("series", "")
        art = th.pick_series_art(sc.list_folder(
            drive, th._folder(drive, "SERIES_GRAPHICS_FOLDER_ID", "Series Graphics", sermons_folder)),
            series)
        if art:
            path = os.path.join(workdir, "series_art" + os.path.splitext(art["name"])[1])
            sc.download(drive, art["id"], path)
            return path
        sc.log(f"  graphics: no series art for {series!r}; brand colours")
    except (Exception, SystemExit) as e:          # creds() exits when a token is missing
        sc.log(f"  ! graphics: series art unavailable ({e}); brand colours")
    return None


def plan_html(content, meta, names):
    from html import escape
    c, q = content["carousel"], content["quote"]
    rows = "".join(f"<li>{escape(s.get('text', ''))}"
                   + (f"<br><i>{escape(s['body'])}</i>" if s.get("body") else "")
                   + (f" ({escape(s['ref'])})" if s.get("ref") else "") + "</li>"
                   for s in c["slides"])
    cap = lambda t: escape(t or "").replace("\n", "<br>")
    return (f"<h1>Graphics: {escape(meta.get('title') or 'Sunday sermon')}</h1>"
            "<p>Drafts only: nothing has been posted. Post the carousel images in order as one "
            "post; the quote card has a feed (4:5) and a Stories (9:16) version.</p>"
            f"<h2>Sermon in slides ({len(c['slides'])} images)</h2><ol>{rows}</ol>"
            f"<h3>Instagram</h3><p>{cap(c.get('instagram_caption'))}</p>"
            f"<h3>Facebook</h3><p>{cap(c.get('facebook_caption'))}</p>"
            f"<h2>Quote card</h2><p><b>{escape(q['text'])}</b></p>"
            f"<h3>Instagram</h3><p>{cap(q.get('instagram_caption'))}</p>"
            f"<h3>Facebook</h3><p>{cap(q.get('facebook_caption'))}</p>"
            "<h2>Files</h2><ul>" + "".join(f"<li>{escape(n)}</li>" for n in names) + "</ul>")


def make(drive, words, meta, service_date, folder, workdir, sermons_folder):
    """Choose, render and upload the week's carousel and quote card into the
    drafts folder. Returns the plan doc link."""
    import social_clips as sc
    stamp = service_date.isoformat()
    content = pick_content(transcript_text(words), meta)
    look = series_look(sc.load_brand(), series_art(drive, service_date, sermons_folder, workdir))
    out = os.path.join(workdir, "graphics")
    files = render_all(content, meta, look, sc.FONTS_DIR, out, stamp)
    for path, name in files:
        sc.upload(drive, path, name, folder, "image/png")
    sc.log(f"  graphics: {len(files) - 2} carousel slides and a quote card uploaded")
    with open(os.path.join(out, "graphics.json"), "w") as f:
        json.dump(content, f, indent=2, ensure_ascii=False)
    sc.upload(drive, os.path.join(out, "graphics.json"), "graphics.json", folder, "application/json")
    with open(os.path.join(out, "graphics.html"), "w") as f:
        f.write(plan_html(content, meta, [n for _, n in files]))
    doc = sc.upload(drive, os.path.join(out, "graphics.html"), f"Graphics plan {stamp}", folder,
                    "text/html", as_google_doc=True)
    return doc.get("webViewLink")


if __name__ == "__main__":                 # local preview: content.json art.png out_dir
    import sys
    import social_clips as sc
    content = check_content(json.load(open(sys.argv[1])))
    meta = json.load(open(sys.argv[4])) if len(sys.argv) > 4 else {}
    brand = sc.load_brand()
    look = series_look(brand, sys.argv[2])
    for p, _ in render_all(content, meta, look, sc.FONTS_DIR, sys.argv[3], meta.get("date", "")):
        print(p)
