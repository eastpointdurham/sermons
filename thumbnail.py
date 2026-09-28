#!/usr/bin/env python3
"""
YouTube thumbnails in the sermon series' look, drawn with Pillow.

1280x720 in the current series' look: the series art (exported from Canva) top
left, and its own colours (sampled from that art) for the ground, the sermon
title in Oakes Grotesk caps, the brush underline and the scripture. An approved
photo sits on the right, fading into the ground. With no series art yet, the
brand colours stand in (ink, off-white, sage).

Everything comes from three Drive folders next to "Sermons" (created on first
use, or set by id):
  Series Graphics    SERIES_GRAPHICS_FOLDER_ID   Canva export per series, named
                                                 after it, e.g. "ALL IN.png"
  Thumbnail Photos   THUMBNAIL_PHOTOS_FOLDER_ID  approved photos; a subfolder
                                                 named after a series is used
                                                 for that series
  Thumbnails         THUMBNAILS_FOLDER_ID        a copy of every thumbnail made

Missing art or photos never stop an upload: the thumbnail falls back to the
brand look (sunburst, series name in type).

Backfill one sermon that is already on YouTube:
  python thumbnail.py 2026-09-27
"""

import os
import re
import sys
import tempfile

from PIL import Image, ImageDraw

import reel_design as rd
import social_clips as sc

TW, TH = 1280, 720
MARGIN = 72
TEXT_W = 640                  # left column
PHOTO_X = 560                 # photo starts here and fades in over FADE px
FADE = 300

IMAGE_RE = re.compile(r"\.(png|jpe?g|webp)$", re.I)
FOLDER_MIME = "application/vnd.google-apps.folder"


# --------------------------------------------------------------------------
# choosing assets
# --------------------------------------------------------------------------

def _norm(s):
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


def series_key(series):
    """'ALL IN: Following Jesus in the Gospel of Mark' -> 'all in'."""
    return _norm(re.split(r"\s[:\-–—|]\s|:", series or "", maxsplit=1)[0])


def matches_series(name, series):
    key, stem = series_key(series), _norm(os.path.splitext(name)[0])
    return bool(key and stem) and (stem.startswith(key) or key.startswith(stem))


def pick_series_art(files, series):
    hits = [f for f in files if IMAGE_RE.search(f["name"]) and matches_series(f["name"], series)]
    # the shortest name is the plainest match ("ALL IN.png" over "ALL IN old.png")
    return min(hits, key=lambda f: (len(f["name"]), f["name"])) if hits else None


def pick_photo(files, service_date):
    """Rotate through the folder a week at a time, so reruns pick the same photo."""
    photos = sorted((f for f in files if IMAGE_RE.search(f["name"])), key=lambda f: f["name"])
    if not photos:
        return None
    return photos[(service_date.toordinal() // 7) % len(photos)]


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------

def _cover(img, w, h, focus_y=0.4):
    """Scale to cover w x h, cropping around the upper-middle (where faces are)."""
    s = max(w / img.width, h / img.height)
    img = img.resize((max(w, round(img.width * s)), max(h, round(img.height * s))), Image.LANCZOS)
    x = (img.width - w) // 2
    y = min(max(0, round(img.height * focus_y - h / 2)), img.height - h)
    return img.crop((x, y, x + w, y + h))


def trim_to_content(art, pad=0.04):
    """Crop a full-frame design (e.g. a 16:9 wallpaper) down to its lockup: drop
    the margins that match the background colour sampled at the corners."""
    from PIL import ImageChops
    rgb = art.convert("RGB")
    bg = _background(rgb)
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg)).convert("L")
    box = diff.point(lambda v: 255 if v > 40 else 0).getbbox()
    if not box:
        return art
    px, py = int(rgb.width * pad), int(rgb.height * pad)
    box = (max(0, box[0] - px), max(0, box[1] - py),
           min(rgb.width, box[2] + px), min(rgb.height, box[3] + py))
    return art.crop(box)


def _contain(img, w, h):
    s = min(w / img.width, h / img.height)
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)


def _fit_title(text, fonts_dir, max_w, max_lines=3):
    for size in (96, 88, 80, 72, 64, 58, 52):
        f = rd.font("bold", size, fonts_dir)
        lines = rd.balanced_wrap(text, f, max_w)
        if len(lines) <= max_lines and all(rd.text_width(l, f) <= max_w for l in lines):
            return f, size, lines
    return f, size, lines


def _lum(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _sat(c):
    return (max(c) - min(c)) / 255


def _background(rgb):
    corners = [rgb.getpixel(p) for p in ((0, 0), (rgb.width - 1, 0),
                                         (0, rgb.height - 1), (rgb.width - 1, rgb.height - 1))]
    return tuple(sorted(c[i] for c in corners)[1] for i in range(3))


def palette_from_art(art):
    """The series' own colours: ground = the art's background, text = the
    lockup colour furthest from it, accent = the lockup's saturated colour.
    Sampled from the lockup pixels only, so thin type and marks on a big
    wallpaper still count. None if there is too little contrast to set type."""
    rgb = Image.new("RGB", art.size, (255, 255, 255))
    rgb.paste(art, mask=art.getchannel("A") if art.mode == "RGBA" else None)
    rgb = rgb.resize((480, round(480 * rgb.height / rgb.width)))
    ground = _background(rgb)
    ink = [p for p in rgb.getdata()
           if max(abs(a - b) for a, b in zip(p, ground)) > 40]
    if len(ink) < 50:
        return None
    swatch = Image.new("RGB", (len(ink), 1))
    swatch.putdata(ink)
    q = swatch.quantize(6, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    cols = [tuple(pal[i * 3:i * 3 + 3]) for n, i in q.getcolors() if n >= len(ink) * 0.01]
    text = max(cols, key=lambda c: abs(_lum(c) - _lum(ground)))
    if abs(_lum(text) - _lum(ground)) < 90:        # not enough contrast to set type
        return None
    # accent: the typical colour of the lockup's saturated pixels (arrows,
    # underlines), which quantizing would blend into the grey anti-aliasing
    groups = {}
    for p in ink:
        if _sat(p) > 0.2 and max(abs(a - b) for a, b in zip(p, text)) > 60:
            groups.setdefault(tuple(c // 40 for c in p), []).append(p)
    best = max(groups.values(), key=len, default=[])
    if len(best) >= len(ink) * 0.01:
        accent = tuple(sum(p[i] for p in best) // len(best) for i in range(3))
    else:
        accent = text
    return {"ground": ground, "text": text, "accent": accent}


def _hex(c):
    return "#%02x%02x%02x" % c


def render(entry, service_date, brand, fonts_dir, out_path, photo_path=None, art_path=None):
    art = Image.open(art_path).convert("RGBA") if art_path else None
    colours = (palette_from_art(art) if art else None) or {
        "ground": rd.hex_rgb(brand["ink"]), "text": rd.hex_rgb(brand["offwhite"]),
        "accent": rd.hex_rgb(brand["sage"])}
    ink, off, sage = colours["ground"], colours["text"], colours["accent"]
    img = Image.new("RGB", (TW, TH), ink)

    if photo_path:
        photo = _cover(Image.open(photo_path).convert("RGB"), TW - PHOTO_X, TH)
        mask = Image.new("L", photo.size, 255)
        md = ImageDraw.Draw(mask)
        for x in range(FADE):
            u = x / FADE
            md.line([(x, 0), (x, TH)], fill=int(255 * u * u * (3 - 2 * u)))
        img.paste(photo, (PHOTO_X, 0), mask)
    else:
        mark = rd.sunburst(760, _hex(sage))
        mark.putalpha(mark.getchannel("A").point(lambda a: a * 30 // 100))
        img.paste(mark, (TW - 520, (TH - mark.height) // 2), mark)

    d = ImageDraw.Draw(img)
    y = MARGIN
    series = (entry or {}).get("series") or ""
    if art:
        art = _contain(trim_to_content(art), 440, 160)
        img.paste(art, (MARGIN, y), art)
        y += art.height + 40
    else:
        mark = rd.sunburst(64, _hex(sage))
        img.paste(mark, (MARGIN, y), mark)
        if series:
            label = rd.font("semibold", 28, fonts_dir)
            rd.draw_tracked(d, MARGIN + 84, y + 44, series_key(series).upper(), label,
                            sage, tracking=4)
        y += 64 + 48

    title = ((entry or {}).get("title") or (entry or {}).get("scripture")
             or "Sunday " + service_date.strftime("%B %-d")).upper()
    f, size, lines = _fit_title(title, fonts_dir, TEXT_W)
    lead = int(size * 1.04)
    y += size
    for line in lines:
        d.text((MARGIN, y), line, font=f, fill=off, anchor="ls")
        y += lead
    y -= lead
    widest = max(rd.text_width(l, f) for l in lines)
    brush = rd.brush_stroke(int(min(widest, TEXT_W) * 0.8), 10, _hex(sage), seed=11)
    img.paste(brush, (MARGIN - 6, y + 14), brush)
    y += 14 + brush.height + 46

    scripture = (entry or {}).get("scripture")
    if scripture:
        rd.draw_tracked(d, MARGIN, y, scripture.upper(), rd.font("semibold", 34, fonts_dir),
                        sage, tracking=3)

    if y < TH - MARGIN - 60:                       # room left for the sign-off
        foot = rd.font("medium", 22, fonts_dir)
        rd.draw_tracked(d, MARGIN, TH - MARGIN + 10, "EASTPOINT CHURCH  ·  DURHAM", foot,
                        off, tracking=4)

    img.save(out_path, "JPEG", quality=90, optimize=True)   # YouTube limit is 2 MB
    return out_path


# --------------------------------------------------------------------------
# Drive + YouTube
# --------------------------------------------------------------------------

def _folder(drive, env, name, sermons_folder):
    if os.environ.get(env):
        return os.environ[env]
    parent = drive.files().get(fileId=sermons_folder, fields="parents",
                               supportsAllDrives=True).execute(
                                   num_retries=sc.DRIVE_RETRIES)["parents"][0]
    return sc.ensure_folder(drive, name, parent)


def make(drive, entry, service_date, sermons_folder, workdir):
    """Build the thumbnail for one sermon; returns (local path, Drive link)."""
    try:
        sc.fetch_fonts(drive)
    except Exception as e:                          # fall back to Archivo/DejaVu
        print(f"    ! could not fetch brand fonts: {e}", flush=True)
    brand = sc.load_brand()
    series = (entry or {}).get("series") or ""

    art_path = photo_path = None
    art = pick_series_art(sc.list_folder(
        drive, _folder(drive, "SERIES_GRAPHICS_FOLDER_ID", "Series Graphics", sermons_folder)),
        series)
    if art:
        art_path = os.path.join(workdir, "art" + os.path.splitext(art["name"])[1])
        sc.download(drive, art["id"], art_path)
    else:
        print(f"    ! no series art for {series_key(series) or 'this sermon'!r} "
              f"in Series Graphics; using the brand mark", flush=True)

    photos_root = _folder(drive, "THUMBNAIL_PHOTOS_FOLDER_ID", "Thumbnail Photos", sermons_folder)
    items = sc.list_folder(drive, photos_root)
    sub = next((f for f in items if f.get("mimeType") == FOLDER_MIME
                and matches_series(f["name"], series)), None)
    photo = pick_photo(sc.list_folder(drive, sub["id"]) if sub else items, service_date)
    if photo:
        photo_path = os.path.join(workdir, "photo" + os.path.splitext(photo["name"])[1])
        sc.download(drive, photo["id"], photo_path)
    else:
        print("    ! Thumbnail Photos is empty; thumbnail has no photo", flush=True)

    out = render(entry, service_date, brand, sc.FONTS_DIR,
                 os.path.join(workdir, f"Thumbnail {service_date.isoformat()}.jpg"),
                 photo_path, art_path)
    up = sc.upload(drive, out, os.path.basename(out),
                   _folder(drive, "THUMBNAILS_FOLDER_ID", "Thumbnails", sermons_folder),
                   "image/jpeg")
    return out, up.get("webViewLink")


def set_on_youtube(youtube, video_id, path):
    from googleapiclient.http import MediaFileUpload
    youtube.thumbnails().set(videoId=video_id,
                             media_body=MediaFileUpload(path, mimetype="image/jpeg")).execute()


def add_thumbnail(drive, youtube, entry, service_date, video_id, sermons_folder):
    """Make and set a thumbnail. Never raises: a thumbnail is not worth a lost upload."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path, link = make(drive, entry, service_date, sermons_folder, tmp)
            print(f"  thumbnail in Drive: {link}", flush=True)
            set_on_youtube(youtube, video_id, path)
            print("  thumbnail set on the draft", flush=True)
    except Exception as e:
        print(f"  ! thumbnail not set: {e}", flush=True)
        if "forbidden" in str(e).lower() or "403" in str(e):
            print("    (custom thumbnails need a phone-verified channel: "
                  "youtube.com/verify)", flush=True)


def main(argv):
    """Backfill: thumbnail.py YYYY-MM-DD, for a sermon already uploaded."""
    import upload_sermon as U
    from datetime import date
    if len(argv) != 2:
        raise SystemExit("Usage: python thumbnail.py YYYY-MM-DD")
    service_date = date.fromisoformat(argv[1])
    rec = next((s for s in U.load_state() if s.get("service_date") == argv[1]), None)
    if not rec:
        raise SystemExit(f"No upload recorded for {argv[1]} in uploaded_sermons.json")
    entry = U.read_planning_doc(U.docs_service()).get(service_date)
    add_thumbnail(U.drive_service(), U.youtube_service(), entry, service_date,
                  rec["video_id"], U.SERMON_FOLDER_ID)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
