#!/usr/bin/env python3
"""
YouTube thumbnails in the sermon series' look, drawn with Pillow.

1280x720, led by the series art (exported from Canva) and in its own colours
(sampled from that art): a full-bleed photo, shaded into the series' ground on
the left and bottom, with the series lockup top left and the sermon title and
scripture bottom left. The photo is the week's preacher (a community moment
when a guest has no photos); community versions are saved alongside in Drive
as ready-made alternatives. Hand-picked folders only. With no photo: the lockup large
and centred, the title below. With no series art yet, the brand colours and
sunburst stand in.

Everything comes from three Drive folders next to "Sermons" (created on first
use, or set by id):
  Series Graphics    SERIES_GRAPHICS_FOLDER_ID   Canva export per series, named
                                                 after it, e.g. "ALL IN.png"
  Thumbnail Photos   THUMBNAIL_PHOTOS_FOLDER_ID  a subfolder per preacher, named
                                                 as in the planning doc ("Peter
                                                 Frey"), and "Community": approved
                                                 photos only
  Thumbnails         THUMBNAILS_FOLDER_ID        a copy of every thumbnail made

Missing art or photos never stop an upload.

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


COMMUNITY = "Community"


BACKUP_OPTIONS = 2                                  # community thumbnails made alongside


def _photo_folders(drive, photos_root):
    return {_norm(f["name"]): f for f in sc.list_folder(drive, photos_root)
            if f.get("mimeType") == FOLDER_MIME}


def choose_photo(drive, photos_root, preacher, service_date):
    """The thumbnail photo: from Thumbnail Photos/<preacher>, or Thumbnail
    Photos/Community when the preacher has none (a guest). Only these hand-picked
    folders are used."""
    folders = _photo_folders(drive, photos_root)
    for key in (_norm(preacher), _norm(COMMUNITY)):
        if key and key in folders:
            photo = pick_photo(sc.list_folder(drive, folders[key]["id"]), service_date)
            if photo:
                photo["speaker"] = key == _norm(preacher)
                return photo
    return None


def community_options(drive, photos_root, service_date, n=BACKUP_OPTIONS, skip=None):
    """n community photos, rotating weekly, for backup thumbnails."""
    folder = _photo_folders(drive, photos_root).get(_norm(COMMUNITY))
    if not folder:
        return []
    photos = sorted((f for f in sc.list_folder(drive, folder["id"])
                     if IMAGE_RE.search(f["name"]) and f.get("id") != skip),
                    key=lambda f: f["name"])
    if not photos:
        return []
    start = service_date.toordinal() // 7 * n
    return [photos[(start + i) % len(photos)] for i in range(min(n, len(photos)))]


def pick_photo(files, service_date):
    """Rotate through the folder a week at a time, so reruns pick the same photo."""
    photos = sorted((f for f in files if IMAGE_RE.search(f["name"])), key=lambda f: f["name"])
    if not photos:
        return None
    return photos[(service_date.toordinal() // 7) % len(photos)]


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------

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


def cutout(art):
    """The lockup alone: the art's background (and its texture) made transparent,
    so the lettering sits on a photo or new ground without a box around it."""
    from PIL import ImageChops
    art = art.convert("RGBA")
    rgb = art.convert("RGB")
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, _background(rgb))).convert("L")
    alpha = diff.point(lambda v: 0 if v < 28 else 255 if v > 70 else int((v - 28) * 255 / 42))
    art.putalpha(ImageChops.multiply(alpha, art.getchannel("A")))
    return art


def _contain(img, w, h):
    s = min(w / img.width, h / img.height)
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)


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


def _grain(img, amount=6, seed=3):
    """A little film grain so a flat ground reads like printed series art."""
    import random
    from PIL import ImageChops
    rnd = random.Random(seed)
    noise = Image.new("L", (img.width // 2, img.height // 2))
    noise.putdata([128 + rnd.randint(-amount, amount) for _ in range(noise.width * noise.height)])
    noise = noise.resize(img.size).convert("RGB")
    return ImageChops.add(img, noise, scale=1.0, offset=-128)


def faces_in(photo):
    """[(cx, cy, h, score)] for every face in the photo, as fractions; [] if none."""
    try:
        import numpy as np
        import reframe
        det = reframe._load_detector()
        if det[0] == "none":
            return []
        return reframe._detect(np.asarray(photo.convert("RGB"))[:, :, ::-1].copy(), det)
    except Exception:
        return []


def face_box(photo):
    """(cx, cy, h) of the main face as fractions of the photo, or None."""
    faces = faces_in(photo)
    return max(faces, key=lambda f: f[2] * f[3])[:3] if faces else None


def speaker_face(faces):
    """The preacher in a stage photo with the band around: photographers centre
    the one they are shooting, so a large face near the middle wins."""
    return max(faces, key=lambda f: f[2] * (1.2 - abs(f[0] - 0.5) * 1.6)) if faces else None


def _full_bleed(photo, ground, speaker=False):
    """The photo across the whole frame, the main face kept right of the text,
    shaded into the series' ground on the left and along the bottom for type.
    speaker: a preacher photo, so frame the preacher even with others on stage."""
    from PIL import ImageChops, ImageEnhance
    faces = faces_in(photo)
    group = len(faces) >= 3 and not speaker        # a band, a crowd: keep everyone in
    if speaker and faces:
        cx, cy = speaker_face(faces)[:2]
    elif group:
        cx = sum(f[0] for f in faces) / len(faces)
        cy = sum(f[1] for f in faces) / len(faces)
    elif faces:
        cx, cy = max(faces, key=lambda f: f[2] * f[3])[:2]
    else:
        cx, cy = 0.5, 0.45
    target = 0.62 if group else 0.68               # subject this far across, clear of the type
    s = cover = max(TW / photo.width, TH / photo.height)
    if faces and not group:                        # zoom in just enough to move it there
        need = max(target * TW / max(cx, 0.05), (1 - target) * TW / max(1 - cx, 0.05))
        s = min(max(cover, need / photo.width), cover * 1.8)
    big = photo.resize((round(photo.width * s), round(photo.height * s)), Image.LANCZOS)
    x = min(max(0, round(big.width * cx - TW * target)), big.width - TW)
    y = min(max(0, round(big.height * cy - TH * 0.4)), big.height - TH)
    img = ImageEnhance.Color(big.crop((x, y, x + TW, y + TH))).enhance(0.9)
    left = Image.new("L", (TW, 1))
    hold, clear = 0.42, 0.72                       # solid behind the type, then fade out
    for i in range(TW):
        u = min(1.0, max(0.0, (i / TW - hold) / (clear - hold)))
        left.putpixel((i, 0), int(232 * (1 - u * u * (3 - 2 * u))))
    left = left.resize((TW, TH))
    low = Image.linear_gradient("L").resize((TW, TH)).point(lambda v: int(max(0, v - 150) * 1.2))
    return Image.composite(Image.new("RGB", (TW, TH), ground), img, ImageChops.lighter(left, low))


def _title_lines(text, fonts_dir, max_w, sizes, max_lines):
    for size in sizes:
        f = rd.font("bold", size, fonts_dir)
        lines = rd.balanced_wrap(text, f, max_w)
        if len(lines) <= max_lines and all(rd.text_width(l, f) <= max_w for l in lines):
            return f, size, lines
    return f, size, lines


def render(entry, service_date, brand, fonts_dir, out_path, photo_path=None, art_path=None,
           speaker=False):
    """The series art leads: a full-bleed photo with the lockup and title over its
    shaded left side, or, with no photo, the lockup large and centred and the
    title below it."""
    art = Image.open(art_path).convert("RGBA") if art_path else None
    colours = (palette_from_art(art) if art else None) or {
        "ground": rd.hex_rgb(brand["ink"]), "text": rd.hex_rgb(brand["offwhite"]),
        "accent": rd.hex_rgb(brand["sage"])}
    ink, off, accent = colours["ground"], colours["text"], colours["accent"]
    img = _grain(Image.new("RGB", (TW, TH), ink))
    d = ImageDraw.Draw(img)
    entry = entry or {}
    series = entry.get("series") or ""
    title = (entry.get("title") or "Sunday " + service_date.strftime("%B %-d")).upper()
    scripture = (entry.get("scripture") or "").upper()
    lockup = cutout(trim_to_content(art)) if art else None

    def brand_mark(x, y, center=False):
        """No series art yet: the sunburst and the series name in type."""
        mark = rd.sunburst(72, _hex(accent))
        label = rd.font("semibold", 30, fonts_dir)
        name = series_key(series).upper() or "EASTPOINT CHURCH"
        w = mark.width + 20 + rd.text_width(name, label, 4)
        x = int((TW - w) // 2) if center else x
        img.paste(mark, (x, y), mark)
        rd.draw_tracked(d, x + mark.width + 20, y + 50, name, label, accent, tracking=4)
        return mark.height

    if photo_path:
        img = _full_bleed(Image.open(photo_path).convert("RGB"), ink, speaker)
        d = ImageDraw.Draw(img)
        x = 60
        if lockup:
            lk = _contain(lockup, 480, 210)
            img.paste(lk, (x - 14, 52), lk)
        else:
            brand_mark(x, 56)
        f, size, lines = _title_lines(title, fonts_dir, 640, (104, 92, 80, 70, 62), 2)
        lead = int(size * 1.02)
        y = TH - (118 if scripture else 70) - lead * (len(lines) - 1)
        for i, line in enumerate(lines):
            d.text((x, y + i * lead), line, font=f, fill=off, anchor="ls")
        if scripture:
            rd.draw_tracked(d, x + 2, y + lead * (len(lines) - 1) + 56, scripture,
                            rd.font("semibold", 32, fonts_dir), accent, tracking=3)
    else:
        top, bottom = 56, 48
        f, size, lines = _title_lines(title, fonts_dir, 1120, (104, 92, 80, 70, 60), 2)
        lead = int(size * 1.02)
        block = size + lead * (len(lines) - 1) + (58 if scripture else 0)   # title + scripture
        if lockup:                  # the lockup takes whatever height the title leaves
            lk = _contain(lockup, 1000, min(360, TH - top - bottom - block - 44))
            img.paste(lk, ((TW - lk.width) // 2, top), lk)
            y = top + lk.height
        else:
            y = top + 60 + brand_mark(0, top + 60, center=True)
        y = max(y + 40 + size, TH - bottom - block + size)
        for i, line in enumerate(lines):
            tw = rd.text_width(line, f)
            d.text(((TW - tw) / 2, y + i * lead), line, font=f, fill=off, anchor="ls")
        y += lead * (len(lines) - 1)
        if scripture:
            sf = rd.font("semibold", 32, fonts_dir)
            sw = rd.text_width(scripture, sf, 3)
            rd.draw_tracked(d, (TW - sw) / 2, y + 58, scripture, sf, accent, tracking=3)

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
    photo = choose_photo(drive, photos_root, (entry or {}).get("preacher") or "", service_date)
    if photo:
        photo_path = os.path.join(workdir, "photo" + os.path.splitext(photo["name"])[1])
        sc.download(drive, photo["id"], photo_path)
        print(f"    photo: {photo['name']}", flush=True)
    else:
        print("    no thumbnail photos found; series art only", flush=True)

    out = render(entry, service_date, brand, sc.FONTS_DIR,
                 os.path.join(workdir, f"Thumbnail {service_date.isoformat()}.jpg"),
                 photo_path, art_path, speaker=bool(photo and photo.get("speaker")))
    thumbs_folder = _folder(drive, "THUMBNAILS_FOLDER_ID", "Thumbnails", sermons_folder)
    up = sc.upload(drive, out, os.path.basename(out), thumbs_folder, "image/jpeg")

    # community versions as ready-made alternatives; never set on YouTube
    for i, alt in enumerate(community_options(drive, photos_root, service_date,
                                              skip=photo["id"] if photo else None), 1):
        try:
            alt_path = os.path.join(workdir, f"alt{i}" + os.path.splitext(alt["name"])[1])
            sc.download(drive, alt["id"], alt_path)
            name = f"Thumbnail {service_date.isoformat()} (community option {i}).jpg"
            render(entry, service_date, brand, sc.FONTS_DIR, os.path.join(workdir, name),
                   alt_path, art_path)
            sc.upload(drive, os.path.join(workdir, name), name, thumbs_folder, "image/jpeg")
            print(f"    backup option {i}: {alt['name']}", flush=True)
        except Exception as e:
            print(f"    ! backup option {i} skipped: {e}", flush=True)
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
