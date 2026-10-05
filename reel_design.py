"""
Eastpoint reel graphics, drawn with Pillow in the brand's own type.

Matches the church's design language: ink (#1a1a1a) grounds, off-white
Oakes Grotesk set in bold caps, the sage sunburst mark, and a hand-drawn sage
brush stroke under the key line. Everything is drawn here (not by a subtitle
renderer) so the letterforms, tracking and spacing are exactly the brand's.

Outputs used by social_clips.py:
  overlay PNG      static top block (sunburst, hook, brush underline, scripture)
                   plus soft ink scrims for legibility
  caption PNGs     one transparent frame per spoken word; the current word sits
                   on a sage highlight, the others are off-white
  end card PNG     ink card: sunburst, SUNDAYS AT 10AM, brush, wordmark, address
"""

import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1080, 1920
HERE = os.path.dirname(os.path.abspath(__file__))
SUNBURST_SVG = os.path.join(HERE, "social", "sunburst.svg")


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# --------------------------------------------------------------------------
# fonts
# --------------------------------------------------------------------------

WEIGHT_FILES = {
    "bold": ["Oakes-Grotesk-Bold.otf", "Archivo-Bold.ttf", "Archivo.ttf"],
    "semibold": ["Oakes-Grotesk-Semi-Bold.otf", "Archivo-SemiBold.ttf", "Archivo.ttf"],
    "medium": ["Oakes-Grotesk-Medium.otf", "Archivo-Medium.ttf", "Archivo.ttf"],
    "regular": ["Oakes-Grotesk-Regular.otf", "Archivo-Regular.ttf", "Archivo.ttf"],
}
SYSTEM_FALLBACK = {
    "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "semibold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "medium": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
}
_font_cache = {}


def font(weight, size, fonts_dir):
    key = (weight, size, fonts_dir)
    if key in _font_cache:
        return _font_cache[key]
    path = None
    for name in WEIGHT_FILES[weight]:
        p = os.path.join(fonts_dir, name)
        if os.path.exists(p):
            path = p
            break
    path = path or SYSTEM_FALLBACK[weight]
    f = ImageFont.truetype(path, size)
    if path.endswith("Archivo.ttf"):          # variable font: pick the weight
        want = {"weight": {"bold": 700, "semibold": 600, "medium": 500, "regular": 400}[weight],
                "width": 100}
        try:                                   # axes in the font's own order (Weight, Width)
            f.set_variation_by_axes([want.get(a["name"].decode().lower(), a["default"])
                                     for a in f.get_variation_axes()])
        except Exception:
            pass
    _font_cache[key] = f
    return f


def using_brand_font(fonts_dir):
    return os.path.exists(os.path.join(fonts_dir, WEIGHT_FILES["bold"][0]))


# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------

def text_width(txt, f, tracking=0):
    if not txt:
        return 0
    return f.getlength(txt) + tracking * (len(txt) - 1)


def draw_tracked(draw, x, y, txt, f, fill, tracking=0, anchor="ls"):
    """Draw left-to-right with extra letter spacing; (x, y) is the left baseline."""
    if tracking == 0:
        draw.text((x, y), txt, font=f, fill=fill, anchor=anchor)
        return
    for ch in txt:
        draw.text((x, y), ch, font=f, fill=fill, anchor=anchor)
        x += f.getlength(ch) + tracking


def wrap_words(words, f, max_w, tracking=0):
    lines, cur = [], []
    for w in words:
        trial = " ".join(cur + [w])
        if cur and text_width(trial, f, tracking) > max_w:
            lines.append(cur)
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def balanced_wrap(text, f, max_w, tracking=0):
    """Wrap into the fewest lines, then even out their lengths (headline style)."""
    words = text.split()
    n = len(wrap_words(words, f, max_w, tracking))
    if n <= 1:
        return [" ".join(words)]
    best, best_score = None, None
    # try every split into n lines (small n; hooks are short)
    def splits(ws, k):
        if k == 1:
            yield [ws]
            return
        for i in range(1, len(ws) - k + 2):
            for rest in splits(ws[i:], k - 1):
                yield [ws[:i]] + rest
    for cand in splits(words, n):
        widths = [text_width(" ".join(l), f, tracking) for l in cand]
        if max(widths) > max_w:
            continue
        score = max(widths) - min(widths)
        if best_score is None or score < best_score:
            best, best_score = cand, score
    return [" ".join(l) for l in (best or wrap_words(words, f, max_w, tracking))]


# --------------------------------------------------------------------------
# brand marks
# --------------------------------------------------------------------------

def sunburst(width, color_hex):
    import cairosvg
    import io
    svg = open(SUNBURST_SVG, encoding="utf-8").read().replace("#778472", color_hex)
    png = cairosvg.svg2png(bytestring=svg.encode(), output_width=width)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def _smooth_noise(rnd, n, knots=6):
    pts = [rnd.uniform(-1, 1) for _ in range(knots + 1)]
    out = []
    for i in range(n):
        u = i / max(1, n - 1) * knots
        k = min(int(u), knots - 1)
        f = u - k
        f = f * f * (3 - 2 * f)
        out.append(pts[k] * (1 - f) + pts[k + 1] * f)
    return out


def brush_stroke(length, thickness, color_hex, seed=7, tail=True):
    """A hand-drawn dry-brush swash in the style of the brand art: a fat stroke
    that starts blunt and tapers out, ragged dry edges, and a thinner second
    pass underneath."""
    rnd = random.Random(seed)
    s = 3                                            # supersample
    Lc = length * s
    Tc = thickness * s
    Wc, Hc = int(Lc + 60 * s), int(Tc * 6 + 60 * s)
    img = Image.new("L", (Wc, Hc), 0)
    d = ImageDraw.Draw(img)

    def stroke(x0, x1, y_mid, bow, thick, bristles, blunt_start, taper_end, seed_off, rise=0.0):
        r = random.Random(seed * 101 + seed_off)
        steps = 160
        wob = _smooth_noise(r, steps + 1, knots=5)
        wid = _smooth_noise(r, steps + 1, knots=7)
        for b in range(bristles):
            v = b / (bristles - 1) - 0.5              # -0.5 (top) .. 0.5 (bottom)
            edge = abs(v) * 2                          # 0 centre .. 1 edge
            alpha = int(255 * (0.65 + 0.35 * r.random()) * (1 - 0.35 * edge ** 3))
            width = max(1, int(s * (1.4 + 2.0 * r.random())))
            # outer bristles run out early (ragged, dry ends) and skip patches
            end_u = 1 - (r.random() ** 2) * (0.45 * edge + 0.05)
            start_u = (r.random() ** 3) * 0.06 * edge
            gaps = []
            if edge > 0.45:
                for _ in range(r.randint(0, 3)):
                    g0 = r.uniform(0.15, 0.9)
                    gaps.append((g0, g0 + r.uniform(0.02, 0.09)))
            seg = []
            for i in range(steps + 1):
                u = i / steps
                inside = start_u <= u <= end_u and not any(a <= u <= c for a, c in gaps)
                if not inside:
                    if len(seg) > 1:
                        d.line(seg, fill=alpha, width=width, joint="curve")
                    seg = []
                    continue
                if u < blunt_start:
                    t = 0.55 + 0.45 * (u / blunt_start) ** 0.5
                elif u > 1 - taper_end:
                    t = ((1 - u) / taper_end) ** 0.75
                else:
                    t = 1.0
                t *= 1 + 0.18 * wid[i]
                x = x0 + (x1 - x0) * u
                y = (y_mid + bow * math.sin(math.pi * u) + rise * (u - 0.5)
                     + wob[i] * thick * 0.18 + v * thick * t + r.uniform(-0.3, 0.3) * s)
                seg.append((x, y))
            if len(seg) > 1:
                d.line(seg, fill=alpha, width=width, joint="curve")

    pad = 30 * s
    y0 = Hc / 2 - Tc * 0.9
    stroke(pad, pad + Lc, y0, -Tc * 0.45, Tc * 1.35, 70, 0.04, 0.3, 1, rise=-Tc * 0.3)
    if tail:
        stroke(pad + Lc * 0.07, pad + Lc * 0.72, y0 + Tc * 1.9, Tc * 0.15, Tc * 0.75, 40,
               0.03, 0.35, 2, rise=-Tc * 0.2)
    img = img.filter(ImageFilter.GaussianBlur(s * 0.45)).resize((Wc // s, Hc // s), Image.LANCZOS)
    col = Image.new("RGBA", img.size, hex_rgb(color_hex) + (0,))
    col.putalpha(img)
    return col.crop(col.getbbox())


def vertical_scrim(height, color_hex, top_alpha, bottom_alpha):
    grad = Image.new("L", (1, height))
    for y in range(height):
        u = y / max(1, height - 1)
        # ease so the fade reads natural rather than banded
        e = u * u * (3 - 2 * u)
        grad.putpixel((0, y), int(top_alpha + (bottom_alpha - top_alpha) * e))
    grad = grad.resize((W, height))
    img = Image.new("RGBA", (W, height), hex_rgb(color_hex) + (0,))
    img.putalpha(grad)
    return img


# --------------------------------------------------------------------------
# reel overlay
# --------------------------------------------------------------------------

HOOK_SIZE = 76
HOOK_MAX_W = 900
CAP_SIZE = 82
CAP_MAX_W = 940


def hook_block_height(hook, fonts_dir, scripture):
    f = font("bold", HOOK_SIZE, fonts_dir)
    lines = balanced_wrap(hook.upper(), f, HOOK_MAX_W)
    return 150 + len(lines) * int(HOOK_SIZE * 1.02) + 70 + (54 if scripture else 0)


def make_overlay(clip, brand, layout, fonts_dir, out_path, style="bold"):
    ink, white, sage = brand["ink"], brand["offwhite"], brand["sage"]
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if style == "editorial":
        # Let the teaching carry it: no banner, no logo. Just a whisper of shade
        # behind the caption line so white type holds on light backgrounds.
        mid = caption_y(clip, brand, fonts_dir, layout, style) - 70   # shade follows the captions
        band = vertical_scrim(520, ink, 0, 70)
        canvas.alpha_composite(band, (0, max(0, mid - 520)))
        canvas.alpha_composite(vertical_scrim(520, ink, 70, 0), (0, mid))
        canvas.save(out_path)
        return out_path

    if layout == "fill":
        canvas.alpha_composite(vertical_scrim(720, ink, 235, 0), (0, 0))
        canvas.alpha_composite(vertical_scrim(760, ink, 0, 200), (0, H - 760))

    y = 92
    sb = sunburst(132, sage)
    canvas.alpha_composite(sb, ((W - sb.width) // 2, y))
    y += sb.height + 34

    d = ImageDraw.Draw(canvas)
    f = font("bold", HOOK_SIZE, fonts_dir)
    lines = balanced_wrap(clip["hook"].upper(), f, HOOK_MAX_W)
    lead = int(HOOK_SIZE * 1.02)
    ascent = f.getmetrics()[0]
    widest = 0
    for i, line in enumerate(lines):
        lw = text_width(line, f)
        widest = max(widest, lw)
        d.text(((W - lw) / 2, y + ascent + i * lead), line, font=f, fill=hex_rgb(white), anchor="ls")
    y += ascent + (len(lines) - 1) * lead + 26

    br = brush_stroke(min(W - 120, widest * 1.08), 11, sage, seed=len(clip["hook"]))
    canvas.alpha_composite(br, (int((W - br.width) / 2), int(y)))
    y += br.height + 30

    if clip.get("scripture"):
        fs = font("medium", 30, fonts_dir)
        ref = clip["scripture"].upper()
        tw = text_width(ref, fs, 7)
        draw_tracked(d, (W - tw) / 2, y + 30, ref, fs, hex_rgb(white), 7)

    canvas.save(out_path)
    return out_path


# --------------------------------------------------------------------------
# captions
# --------------------------------------------------------------------------

EDITORIAL_CAP_SIZE = 56          # legible at a glance on mute; still calmer than (bold)


def editorial_caption_frame(words, brand, fonts_dir, cap_y):
    """Small, calm, centred caps; no highlight. One short phrase at a time."""
    white, ink = hex_rgb(brand["offwhite"]), hex_rgb(brand["ink"])
    f = font("bold", EDITORIAL_CAP_SIZE, fonts_dir)
    text = " ".join(w.upper() for w in words)
    lines = wrap_words(text.split(), f, 820, tracking=1)
    lead = int(EDITORIAL_CAP_SIZE * 1.25)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d, ds = ImageDraw.Draw(img), ImageDraw.Draw(shadow)
    top = cap_y - len(lines) * lead / 2 + f.getmetrics()[0] * 0.8
    for i, line in enumerate(lines):
        t = " ".join(line)
        x = (W - text_width(t, f, 1)) / 2
        draw_tracked(ds, x, top + i * lead + 2, t, f, ink + (150,), 1)
        draw_tracked(d, x, top + i * lead, t, f, white + (255,), 1)
    shadow = shadow.filter(ImageFilter.GaussianBlur(5))
    shadow.alpha_composite(img)
    return shadow


def caption_frame(words, active, brand, fonts_dir, cap_y):
    """One transparent frame: the chunk's words, the active one on a sage mark."""
    white, sage, ink = hex_rgb(brand["offwhite"]), hex_rgb(brand["sage"]), hex_rgb(brand["ink"])
    f = font("bold", CAP_SIZE, fonts_dir)
    caps = [w.upper() for w in words]
    lines = wrap_words(caps, f, CAP_MAX_W)
    lead = int(CAP_SIZE * 1.3)
    ascent, descent = f.getmetrics()
    cap_h = f.getbbox("H")[3] - f.getbbox("H")[1]
    space = f.getlength(" ")

    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ds = ImageDraw.Draw(shadow)
    d = ImageDraw.Draw(img)
    top = cap_y - (len(lines) * lead) / 2
    k = 0
    for li, line in enumerate(lines):
        lw = text_width(" ".join(line), f)
        x = (W - lw) / 2
        base = top + li * lead + ascent * 0.9
        for word in line:
            ww = f.getlength(word)
            if k == active:
                pad_x, pad_y = 16, 14
                d.rounded_rectangle([x - pad_x, base - cap_h - pad_y, x + ww + pad_x, base + pad_y],
                                    radius=12, fill=sage + (255,))
            else:
                ds.text((x, base + 3), word, font=f, fill=ink + (190,), anchor="ls")
            d.text((x, base), word, font=f, fill=white + (255,), anchor="ls")
            x += ww + space
            k += 1
    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    shadow.alpha_composite(img)
    return shadow


EDITORIAL_TOP = 260      # under Instagram's own top bar when there is no hook banner


def caption_y(clip, brand, fonts_dir, layout, style="bold"):
    """Vertical centre of the captions. Default: the lower third. When the camera
    reports a band of empty backdrop above the speaker's head big enough for two
    caption lines, the captions sit there instead (over the backdrop, not over
    the speaker or the front row)."""
    if style == "editorial":
        default, block = (1290 if layout == "fill" else 1450), 2 * int(EDITORIAL_CAP_SIZE * 1.25)
        top = EDITORIAL_TOP
    else:
        default, block = (1330 if layout == "fill" else 1560), 2 * int(CAP_SIZE * 1.3)
        top = hook_block_height(clip.get("hook", ""), fonts_dir, clip.get("scripture")) + 20
    head = clip.get("head_top")
    if layout != "fill" or head is None:
        return default
    head_px = head * H - 40                        # clear of the hair
    if head_px - top < block + 60:
        return default
    return int(round((top + head_px) / 2))


def caption_track(clip, chunks, brand, fonts_dir, layout, workdir, style="bold"):
    """Write caption PNGs and an ffconcat list; returns the list path."""
    cap_y = caption_y(clip, brand, fonts_dir, layout, style)
    t0, dur = clip["start"], clip["end"] - clip["start"]
    blank = os.path.join(workdir, f"c{clip['n']}_blank.png")
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)

    events = []          # (start, end, path)
    for ci, chunk in enumerate(chunks):
        chunk_end = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else chunk[-1]["e"] + 0.4
        chunk_end = min(chunk_end, chunk[-1]["e"] + 0.6)
        if style == "editorial":            # one frame per phrase, not per word
            s, e = chunk[0]["s"] - t0, chunk_end - t0
            if e > s:
                p = os.path.join(workdir, f"c{clip['n']}_{ci:03d}.png")
                editorial_caption_frame([x["w"] for x in chunk], brand, fonts_dir, cap_y).save(p)
                events.append((max(0.0, s), min(dur, e), p))
            continue
        for wi, w in enumerate(chunk):
            s = w["s"] - t0
            e = (chunk[wi + 1]["s"] if wi + 1 < len(chunk) else chunk_end) - t0
            if e <= s:
                continue
            p = os.path.join(workdir, f"c{clip['n']}_{ci:03d}_{wi:02d}.png")
            caption_frame([x["w"] for x in chunk], wi, brand, fonts_dir, cap_y).save(p, optimize=False)
            events.append((max(0.0, s), min(dur, e), p))

    lst = os.path.join(workdir, f"c{clip['n']}_captions.ffconcat")
    t = 0.0
    with open(lst, "w") as fh:
        fh.write("ffconcat version 1.0\n")
        for s, e, p in events:
            if s > t + 0.001:
                fh.write(f"file '{blank}'\nduration {s - t:.3f}\n")
                t = s
            if e > t:
                fh.write(f"file '{p}'\nduration {e - t:.3f}\n")
                t = e
        fh.write(f"file '{blank}'\nduration {max(0.05, dur - t + 0.5):.3f}\n")
        fh.write(f"file '{blank}'\n")
    return lst


# --------------------------------------------------------------------------
# end card
# --------------------------------------------------------------------------

def make_end_card(brand, fonts_dir, out_path, style="bold"):
    ink, white, sage = brand["ink"], brand["offwhite"], brand["sage"]
    img = Image.new("RGBA", (W, H), hex_rgb(ink) + (255,))
    d = ImageDraw.Draw(img)
    if style == "editorial":
        sb = sunburst(150, sage)
        img.alpha_composite(sb, ((W - sb.width) // 2, 800))
        fw = font("medium", 34, fonts_dir)
        for i, (txt, tr, a) in enumerate([("EASTPOINT CHURCH", 12, 255),
                                          ("SUNDAYS AT 10AM  \u00b7  EAST DURHAM", 6, 200)]):
            tw = text_width(txt, fw, tr)
            draw_tracked(d, (W - tw) / 2, 1010 + i * 62, txt, fw, hex_rgb(white) + (a,), tr)
        img.convert("RGB").save(out_path)
        return out_path
    y = 640
    sb = sunburst(300, sage)
    img.alpha_composite(sb, ((W - sb.width) // 2, y))
    y += sb.height + 60

    f = font("bold", 104, fonts_dir)
    for line in ("SUNDAYS", "AT 10AM"):
        lw = text_width(line, f)
        d.text(((W - lw) / 2, y + f.getmetrics()[0]), line, font=f, fill=hex_rgb(white), anchor="ls")
        y += int(104 * 1.0)
    y += 40
    br = brush_stroke(620, 13, sage, seed=3)
    img.alpha_composite(br, ((W - br.width) // 2, y))
    y += br.height + 70

    fw = font("medium", 34, fonts_dir)
    word = "EASTPOINT CHURCH"
    tw = text_width(word, fw, 12)
    draw_tracked(d, (W - tw) / 2, y + 30, word, fw, hex_rgb(white), 12)
    y += 90
    fr = font("regular", 34, fonts_dir)
    for line in brand["end_card"][2:]:
        lw = text_width(line, fr)
        d.text(((W - lw) / 2, y + 30), line, font=fr, fill=hex_rgb(white) + (205,), anchor="ls")
        y += 52
    img.convert("RGB").save(out_path)
    return out_path
