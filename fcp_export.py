"""
Final Cut Pro handoff for the social reels.

For each sermon this writes one FCPXML file holding a vertical 1080x1920 project
per reel, cut from the original sermon recording at full quality:

  - the sermon clip, scaled to fill the vertical frame and slid sideways so the
    speaker sits where the automatic crop put him (or fitted inside the frame for
    the "framed" layout)
  - the hook banner as a title for the whole clip, plus the scripture tag
  - the spoken words as caption titles, 2-4 words at a time
  - markers at the start of each sentence, to help trim

Plus an .srt caption file per reel (YouTube and Facebook accept these directly).

The recording itself is not in the FCPXML; Final Cut asks for it on import. Keep
the sermon file where Final Cut can find it (FCP_MEDIA_DIR) or use
File > Relink Files once.
"""

import os
from fractions import Fraction
from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr

W, H = 1080, 1920
BASIC_TITLE_UID = (".../Titles.localized/Bumper:Opener.localized/"
                   "Basic Title.localized/Basic Title.moti")

RATES = {  # nominal fps -> frame duration
    23.976: Fraction(1001, 24000), 24: Fraction(1, 24), 25: Fraction(1, 25),
    29.97: Fraction(1001, 30000), 30: Fraction(1, 30), 50: Fraction(1, 50),
    59.94: Fraction(1001, 60000), 60: Fraction(1, 60),
}


def frame_duration(fps):
    best = min(RATES, key=lambda r: abs(r - fps))
    return RATES[best]


def t(seconds, fd):
    """Seconds -> FCPXML rational time snapped to whole frames of duration fd."""
    frames = round(Fraction(seconds).limit_denominator(100000) / fd)
    v = frames * fd
    return "0s" if v == 0 else f"{v.numerator}/{v.denominator}s"


def rgba(hex_rgb, a=1.0):
    h = hex_rgb.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return f"{r:.4g} {g:.4g} {b:.4g} {a:g}"


def fmt_srt_time(s):
    ms = int(round(s * 1000))
    return f"{ms // 3600000:02d}:{(ms // 60000) % 60:02d}:{(ms // 1000) % 60:02d},{ms % 1000:03d}"


def write_srt(clip, chunks, path):
    t0 = clip["start"]
    lines = []
    for i, chunk in enumerate(chunks, 1):
        s = max(0.0, chunk[0]["s"] - t0)
        e = max(s + 0.3, chunk[-1]["e"] - t0 + 0.15)
        if i < len(chunks):
            e = min(e, chunks[i][0]["s"] - t0)
        lines.append(f"{i}\n{fmt_srt_time(s)} --> {fmt_srt_time(e)}\n"
                     f"{' '.join(w['w'] for w in chunk)}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _title(ref, name, offset, duration, text, style_id, font, size, color,
           pos_y, fd, bold=True, stroke=None, bg=None):
    """A Basic Title connected on lane 1. pos_y in project pixels from centre (+ up)."""
    stroke_attrs = ""
    if stroke:
        stroke_attrs = f' strokeColor="{stroke}" strokeWidth="5"'
    shadow = ' shadowColor="0 0 0 0.6" shadowOffset="4 315" shadowBlurRadius="6"' if not bg else ""
    return (
        f'<title ref="{ref}" lane="1" name={quoteattr(name)} offset="{offset}" '
        f'duration="{duration}" start="3600s">'
        f'<param name="Position" key="9999/999166631/999166633/1/100/101" value="0 {pos_y}"/>'
        f'<param name="Alignment" key="9999/999166631/999166633/2/354/999169573/401" value="1 (Center)"/>'
        f'<text><text-style ref="{style_id}">{escape(text)}</text-style></text>'
        f'<text-style-def id="{style_id}"><text-style font={quoteattr(font)} fontSize="{size}" '
        f'fontFace="{"Bold" if bold else "Regular"}" fontColor="{color}" bold="{1 if bold else 0}" '
        f'alignment="center"{stroke_attrs}{shadow}/></text-style-def>'
        f'</title>'
    )


def build_fcpxml(sermon_name, media_url, src_duration, src_fps, src_w, src_h,
                 clips, brand, font, chunker, event_name):
    """Return FCPXML (1.10) text: one vertical project per clip."""
    fd = frame_duration(src_fps)
    ink, white, gold, cream = (brand[k] for k in ("ink", "offwhite", "gold", "cream"))
    fd_s = f"{fd.numerator}/{fd.denominator}s"

    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<!DOCTYPE fcpxml>',
           '<fcpxml version="1.10">', '<resources>',
           f'<format id="r1" name="FFVideoFormatRateUndefined" frameDuration="{fd_s}" '
           f'width="{W}" height="{H}" colorSpace="1-1-1 (Rec. 709)"/>',
           f'<format id="r2" frameDuration="{fd_s}" width="{src_w}" height="{src_h}" '
           f'colorSpace="1-1-1 (Rec. 709)"/>',
           f'<asset id="r3" name={quoteattr(os.path.splitext(sermon_name)[0])} start="0s" '
           f'duration="{t(src_duration, fd)}" hasVideo="1" format="r2" hasAudio="1" '
           f'audioSources="1" audioChannels="2" audioRate="48000">'
           f'<media-rep kind="original-media" src={quoteattr(media_url)}/></asset>',
           f'<effect id="r4" name="Basic Title" uid="{BASIC_TITLE_UID}"/>',
           '</resources>', '<library>', f'<event name={quoteattr(event_name)}>']

    # source 16:9 conformed to a 9:16 frame with "fill": the source is scaled so
    # its height fills the frame; its width then spans 16/9 * 1920 px.
    fill_width_px = H * src_w / src_h
    for c in clips:
        dur = c["end"] - c["start"]
        d = t(dur, fd)
        sid = 0

        def sty():
            nonlocal sid
            sid += 1
            return f"ts{c['n']}_{sid}"

        if c.get("layout", "fill") == "fill":
            shift_px = (0.5 - c.get("crop_center", 0.5)) * fill_width_px
            # FCPXML position units: the frame height is 100
            pos_x = shift_px / H * 100
            video_adjust = (f'<adjust-conform type="fill"/>'
                            f'<adjust-transform position="{pos_x:.3f} 0"/>')
        else:
            video_adjust = ('<adjust-conform type="fit"/>'
                            '<adjust-transform position="0 -6.25"/>')

        titles = []
        hook = c["hook"].upper()
        at = t(c["start"], fd)          # connected items use the clip's source time
        titles.append(_title("r4", "Hook", at, d, hook, sty(), font, 64,
                             rgba(white), 700, fd, stroke=None))
        if c.get("scripture"):
            titles.append(_title("r4", "Scripture", at, d, c["scripture"], sty(), font,
                                 40, rgba(white), 600, fd))
        for chunk in chunker(c["words"]):
            s = max(0.0, chunk[0]["s"] - c["start"])
            e = min(dur, chunk[-1]["e"] - c["start"] + 0.15)
            if e - s < 0.2:
                continue
            titles.append(_title("r4", "Caption", t(c["start"] + s, fd), t(e - s, fd),
                                 " ".join(w["w"] for w in chunk).upper(), sty(), font, 84,
                                 rgba(white), -340, fd, stroke=rgba(ink)))

        markers = []
        for s in c.get("sentence_starts", []):
            rel = s - c["start"]
            if 0 < rel < dur:
                markers.append(f'<marker start="{t(c["start"] + rel, fd)}" duration="{fd_s}" '
                               f'value="sentence"/>')

        out.append(
            f'<project name={quoteattr("Reel " + str(c["n"]) + " - " + c["hook"])}>'
            f'<sequence format="r1" duration="{d}" tcStart="3600s" tcFormat="NDF" '
            f'audioLayout="stereo" audioRate="48k"><spine>'
            f'<asset-clip ref="r3" name={quoteattr(c["hook"])} offset="3600s" '
            f'start="{t(c["start"], fd)}" duration="{d}" format="r2" tcFormat="NDF">'
            f'<note>{escape(c.get("why", ""))}</note>'
            f'{video_adjust}{"".join(titles)}{"".join(markers)}'
            f'</asset-clip></spine></sequence></project>'
        )

    out += ['</event>', '</library>', '</fcpxml>']
    return "\n".join(out)


def media_url_for(name):
    folder = os.environ.get("FCP_MEDIA_DIR", "/Users/eastpoint/Movies/Sermons")
    return "file://" + quote(os.path.join(folder, name))
