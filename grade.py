"""
Light and colour for the reels, done the way a colourist would: measure the
clip once, then apply one 3D LUT built for it.

Sanctuary light is rarely neutral or even (gym fluorescents, warm stage LEDs, a
blue screen behind the preacher), and YouTube/phone-grade recordings come out
flat. Per clip, from a handful of frames (one measurement per clip, so nothing
flickers):

  1. white balance in LINEAR light: anchored on near-neutral surfaces (walls,
     whites) when there are enough, with the face as a cross-check; on the face
     alone when there are not. The face only ever corrects HUE: across skin
     tones, skin sits in a narrow band of hue and differs mainly in lightness
     and saturation, so a cast comes out without "correcting" anyone's
     complexion.
  2. levels and exposure from the SCENE (black point, white point, midtones),
     never from the face, capped, with a check that no face clips.
  3. a filmic tone curve: a soft toe and a rolled-off shoulder.
  4. vibrance that lifts muted colour and leaves skin almost alone.
  5. a faint split tone: cool shadows, warm highlights.

All of it is baked into a 33-point .cube LUT that ffmpeg applies in one pass
(lut3d). SOCIAL_GRADE=off leaves the picture alone.
"""

import math
import os

import numpy as np

# skin hue in CIELAB (degrees of the a*b* angle); measured skin across skin tones
# falls roughly between 40 and 70 degrees
SKIN_HUE = 55.0
HUE_OK = 8.0                  # within this of SKIN_HUE: the face needs no help
MIN_CHROMA = 5.0              # a face this grey (mono light, bad exposure) says nothing
WB_STRENGTH = 0.7             # how far toward the measured neutral
MAX_GAIN = 0.10               # never move a channel more than 10% (linear)
LUT_SIZE = 33

# the look: modest by design, so it reads as "well shot", not "filtered"
TONE = 0.22                   # S-curve strength
VIBRANCE = 0.12
SPLIT = (-1.2, 2.0)           # b* push in shadows / highlights (cool / warm)
MID_TARGET = 0.42             # where the scene's median brightness should sit
MAX_EV = 0.33                 # exposure change limit, stops
MID_PULL = 0.35               # how far toward MID_TARGET (a dark set is meant to be dark)


# --------------------------------------------------------------------------
# colour maths (vectorised; sRGB/Rec.709 display 0..1 <-> linear <-> CIELAB D65)
# --------------------------------------------------------------------------

_M = np.array([[0.4124564, 0.3575761, 0.1804375],
               [0.2126729, 0.7151522, 0.0721750],
               [0.0193339, 0.1191920, 0.9503041]])
_MI = np.linalg.inv(_M)
_WHITE = np.array([0.95047, 1.0, 1.08883])
LUMA = np.array([0.2126, 0.7152, 0.0722])


def to_linear(c):
    c = np.asarray(c, dtype=float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def to_display(c):
    c = np.clip(np.asarray(c, dtype=float), 0, None)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)


def _f(t):
    return np.where(t > 216 / 24389, np.cbrt(t), (24389 / 27 * t + 16) / 116)


def _finv(t):
    return np.where(t ** 3 > 216 / 24389, t ** 3, (116 * t - 16) / (24389 / 27))


def rgb_to_lab(rgb):
    xyz = to_linear(rgb) @ _M.T / _WHITE
    fx, fy, fz = _f(xyz[..., 0]), _f(xyz[..., 1]), _f(xyz[..., 2])
    lab = np.stack([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)], axis=-1)
    return tuple(float(v) for v in lab) if lab.ndim == 1 else lab


def lab_to_rgb(lab):
    lab = np.asarray(lab, dtype=float)
    fy = (lab[..., 0] + 16) / 116
    xyz = np.stack([_finv(fy + lab[..., 1] / 500), _finv(fy), _finv(fy - lab[..., 2] / 200)],
                   axis=-1) * _WHITE
    rgb = to_display(xyz @ _MI.T)
    return tuple(float(v) for v in rgb) if rgb.ndim == 1 else rgb


def _hue_chroma(lab):
    L, a, b = lab
    return math.degrees(math.atan2(b, a)), math.hypot(a, b)


# --------------------------------------------------------------------------
# white balance
# --------------------------------------------------------------------------

def _norm_cap(gains):
    """Unit luma gain (brightness stays put), each channel capped."""
    g = np.asarray(gains, dtype=float)
    g = g / float(LUMA @ g)
    return tuple(float(v) for v in np.clip(g, 1 - MAX_GAIN, 1 + MAX_GAIN))


def skin_gains(face_rgb):
    """LINEAR (r, g, b) gains that turn the face's hue toward SKIN_HUE, or None
    when no correction is warranted. face_rgb = the face's mean display colour."""
    L, a, b = rgb_to_lab(face_rgb)
    hue, chroma = _hue_chroma((L, a, b))
    if chroma < MIN_CHROMA or L < 15 or L > 95:
        return None
    off = (hue - SKIN_HUE + 180) % 360 - 180
    if abs(off) <= HUE_OK:
        return None
    t = math.radians(hue - off * WB_STRENGTH)
    target = lab_to_rgb((L, chroma * math.cos(t), chroma * math.sin(t)))
    gains = _norm_cap(to_linear(target) / np.maximum(to_linear(face_rgb), 1e-5))
    return None if max(abs(v - 1) for v in gains) < 0.01 else tuple(round(v, 4) for v in gains)


def neutral_gains(neutral_rgb):
    """LINEAR gains that make the scene's near-neutral surfaces grey (partial)."""
    lin = to_linear(neutral_rgb)
    full = float(LUMA @ lin) / np.maximum(lin, 1e-5)
    gains = _norm_cap(1 + WB_STRENGTH * (full - 1))
    return None if max(abs(v - 1) for v in gains) < 0.01 else tuple(round(v, 4) for v in gains)


def choose_wb(face, neutral):
    """Neutrals first, as long as they leave the face's hue in the skin band (a
    coloured wall is not a white card); otherwise the face."""
    face_g = skin_gains(face) if face else None
    if neutral:
        ng = neutral_gains(neutral)
        if ng is None:
            return None, "neutral"
        if not face:
            return ng, "neutral"
        fixed = to_display(to_linear(face) * np.array(ng))
        hue, chroma = _hue_chroma(rgb_to_lab(fixed))
        if chroma < MIN_CHROMA or abs((hue - SKIN_HUE + 180) % 360 - 180) <= 20:
            return ng, "neutral"
    return face_g, "face" if face_g else "none"


# --------------------------------------------------------------------------
# the LUT
# --------------------------------------------------------------------------

def levels(stats):
    """(black, white, gamma) from the scene's brightness spread, all capped."""
    lo, med, hi = stats["p_lo"], stats["p_med"], stats["p_hi"]
    black = float(np.clip(lo - 0.01, 0.0, 0.06))       # lift haze, never crush
    white = float(np.clip(hi + 0.02, 0.85, 1.0))       # use the range, keep specular room
    m = (med - black) / (white - black)
    gamma = 1.0
    if 0.02 < m < 0.98:
        want = m + MID_PULL * (MID_TARGET - m)
        gamma = float(np.clip(math.log(want) / math.log(m), 2 ** -MAX_EV, 2 ** MAX_EV))
    face = stats.get("face")
    if face:                                           # never push a face into clipping
        fy = float(LUMA @ np.asarray(face))
        fm = np.clip((fy - black) / (white - black), 1e-4, 1)
        if fm ** gamma > 0.88:
            gamma = float(math.log(0.88) / math.log(fm))
    return black, white, gamma


def tone(x, amount=TONE):
    """Filmic S: a soft toe and a rolled-off shoulder around the midtones."""
    x = np.clip(x, 0, 1)
    s = x * x * (3 - 2 * x)
    y = x + amount * (s - x)
    return 0.008 + 0.982 * y                            # print-like black and white


def build_lut(wb, black, white, gamma, size=LUT_SIZE):
    """size^3 display-in, display-out grade as an array [b][g][r][3]."""
    axis = np.linspace(0, 1, size)
    b, g, r = np.meshgrid(axis, axis, axis, indexing="ij")
    rgb = np.stack([r, g, b], axis=-1)
    lin = to_linear(rgb)
    if wb:
        lin = lin * np.array(wb)
    disp = to_display(lin)
    disp = np.clip((disp - black) / (white - black), 0, 1) ** gamma
    disp = tone(disp)
    lab = rgb_to_lab(disp)
    L, A, B = lab[..., 0], lab[..., 1], lab[..., 2]
    hue = np.degrees(np.arctan2(B, A))
    chroma = np.hypot(A, B)
    skin = np.exp(-(((hue - SKIN_HUE + 180) % 360 - 180) / 22.0) ** 2)   # 1 on skin hues
    boost = 1 + VIBRANCE * (1 - np.clip(chroma / 60, 0, 1)) * (1 - 0.8 * skin)
    A, B = A * boost, B * boost
    lum = np.clip(L / 100, 0, 1)
    B = B + SPLIT[0] * (1 - lum) ** 2 + SPLIT[1] * lum ** 2
    out = lab_to_rgb(np.stack([L, A, B], axis=-1))
    return np.clip(out, 0, 1)


def write_cube(lut, path):
    size = lut.shape[0]
    with open(path, "w") as f:
        f.write(f'TITLE "Eastpoint reel grade"\nLUT_3D_SIZE {size}\n')
        for rgb in lut.reshape(-1, 3):                 # red fastest, then green, then blue
            f.write(f"{rgb[0]:.6f} {rgb[1]:.6f} {rgb[2]:.6f}\n")
    return path


# --------------------------------------------------------------------------
# measuring a clip
# --------------------------------------------------------------------------

def analyse(video, start, dur, samples=10):
    """Face colour, near-neutral colour and brightness spread over a few frames."""
    import cv2
    import reframe
    det = reframe._load_detector()
    w, h, _ = reframe._probe(video)
    fps = max(samples / max(dur, 1.0), 0.2)
    faces, neutrals, lumas = [], [], []
    for frame in reframe._frames(video, start, dur, w, h, fps):
        small = cv2.resize(frame, (320, int(320 * h / w)), interpolation=cv2.INTER_AREA)
        rgb = small[:, :, ::-1].reshape(-1, 3) / 255.0
        y = rgb @ LUMA
        lumas.append(y)
        cb, cr = rgb[:, 2] - y, rgb[:, 0] - y
        grey = (np.abs(cb) < 0.03) & (np.abs(cr) < 0.03) & (y > 0.35) & (y < 0.92)
        if grey.mean() > 0.02:
            neutrals.append(rgb[grey].mean(axis=0))
        if det[0] != "none":
            found = reframe._detect(frame, det)
            if found:
                cx, cy, fh = max(found, key=lambda f: f[2] * f[3])[:3]
                half = fh * h * 0.22                   # cheeks and forehead only
                x0, x1 = int(cx * w - half), int(cx * w + half)
                y0, y1 = int(cy * h - half * 1.1), int(cy * h + half * 0.4)
                patch = frame[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
                if patch.size >= 3 * 64:
                    faces.append(patch.reshape(-1, 3).mean(axis=0)[::-1] / 255.0)
    if not lumas:
        return None
    y = np.concatenate(lumas)
    stats = {"p_lo": float(np.percentile(y, 0.5)), "p_med": float(np.percentile(y, 50)),
             "p_hi": float(np.percentile(y, 99.5))}
    enough = max(2, samples // 3)
    stats["face"] = (tuple(float(v) for v in np.median(np.array(faces), axis=0))
                     if len(faces) >= enough else None)
    stats["neutral"] = (tuple(float(v) for v in np.median(np.array(neutrals), axis=0))
                        if len(neutrals) >= enough else None)
    return stats


def grade_filter(video, start, dur, workdir, name="grade"):
    """(ffmpeg filter, info) for one clip; ("", info) when grading is off or the
    clip cannot be measured."""
    if os.environ.get("SOCIAL_GRADE", "on").lower() in ("off", "0", "no"):
        return "", {"grade": "off"}
    try:
        stats = analyse(video, start, dur)
    except Exception as e:                            # never lose a reel over colour
        return "", {"grade": "skipped", "error": str(e)}
    if not stats:
        return "", {"grade": "skipped"}
    wb, source = choose_wb(stats["face"], stats["neutral"])
    black, white, gamma = levels(stats)
    cube = write_cube(build_lut(wb, black, white, gamma), os.path.join(workdir, f"{name}.cube"))
    info = {"grade": "lut", "wb": source, "wb_gains": wb, "black": round(black, 3),
            "white": round(white, 3), "gamma": round(gamma, 3)}
    if stats["face"]:
        info["face_hue"] = round(_hue_chroma(rgb_to_lab(stats["face"]))[0], 1)
    return f"lut3d=file='{cube}':interp=tetrahedral", info
