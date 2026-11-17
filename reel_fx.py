"""The "tight" reel: a faster cut of the same clip, to compare with the natural one.

- cuts long pauses, ums and uhs, ", like," and ", you know," asides, and stuttered
  repeats ("the the"); cut points are set from the audio's loudness, so word
  endings are never clipped
- a punch-in zoom at some cuts (every few seconds at most), so the jump cuts read
  as edits rather than glitches
- optionally a soft music bed, ducked under the voice, and a whoosh as the hook
  appears. The music is from Drive "Reel Music" when that folder has tracks
  (only add tracks licensed for social use), otherwise a generated pad.

Whisper leaves most ums out of its transcript, so find_fillers() runs a second,
verbatim pass over the clip with a prompt that makes it write them down.
"""
import os
import re
import subprocess
import wave

import numpy as np

FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "hmm", "mm", "mhm"}
# a repeat of one of these is a stumble ("the the"); "no, no, no" is left alone
STUTTER = {"i", "the", "a", "and", "we", "you", "it", "that", "to", "of", "so", "but",
           "he", "they", "is", "in", "this", "what", "if", "my", "our"}
KEEP_MID = 0.10      # silence left at a cut inside a sentence (seconds)
KEEP_END = 0.22      # ...and at a cut between sentences, so it still breathes
MIN_CUT = 0.15       # a pause shorter than this (beyond what is kept) is left alone
MIN_SEG = 0.5        # a sliver of speech between two cuts looks like a glitch...
MAX_RESTORE = 0.35   # ...so a pause up to this long beside it is put back
ZOOM = 1.12          # punch-in
ZOOM_EVERY = 3.0     # seconds, at least, between zoom changes
MUSIC_DB = -24       # music bed level under the voice, before ducking
VERBATIM_PROMPT = "Um, so, uh, I was like, you know, um, thinking. Uh, yeah, hmm."


def _norm(w):
    return re.sub(r"[^a-z']", "", w.lower())


def drop_words(words):
    """Indexes of the words the tight cut leaves out."""
    drop = set()
    for i, w in enumerate(words):
        n, prev = _norm(w["w"]), words[i - 1]["w"] if i else ""
        aside = not i or prev.endswith(",")
        nxt = words[i + 1] if i + 1 < len(words) else None
        if n in FILLERS:
            drop.add(i)
        elif n == "like" and w["w"].endswith(",") and aside:
            drop.add(i)
        elif (n == "you" and nxt and _norm(nxt["w"]) == "know" and nxt["w"].endswith(",")
              and aside):
            drop.update({i, i + 1})
        elif (n in STUTTER and nxt and _norm(nxt["w"]) == n and not re.search(r"[,.?!]$", w["w"])
              and nxt["s"] - w["e"] < 0.6):
            drop.add(i)
    return drop


def merge_fillers(words, fillers):
    """Add verbatim-pass fillers [(s, e)] as words, where they fall in a gap."""
    extra = []
    for s, e in fillers:
        overlap = sum(max(0.0, min(e, w["e"]) - max(s, w["s"])) for w in words)
        if overlap < 0.5 * (e - s):
            extra.append({"w": "um", "s": s, "e": e, "_filler": True})
    return sorted(words + extra, key=lambda w: w["s"])


# --------------------------------------------------------------------------
# loudness, for cut points
# --------------------------------------------------------------------------

def envelope(path, start, dur, rate=16000):
    """10 ms loudness (dB) of the clip's audio, and a 'quiet' threshold."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                          "-i", path, "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    hop = rate // 100
    n = len(x) // hop
    if n < 10:
        return None
    db = 20 * np.log10(np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-6)
    return {"t0": start, "db": db, "quiet": float(np.percentile(db, 10)) + 10}


def _loud(env, t):
    i = int(round((t - env["t0"]) * 100))
    return 0 <= i < len(env["db"]) and env["db"][i] > env["quiet"]


def refine_end(env, t, limit):
    """Where a word really ends: Whisper's end time, carried on while it is still
    loud (a trailing consonant, a held vowel), never past the next word."""
    limit = min(limit, t + 0.3)
    if env is None:
        return min(t + 0.08, limit)
    while t < limit and _loud(env, t):
        t += 0.01
    return min(t + 0.03, limit)


def refine_start(env, t, limit):
    limit = max(limit, t - 0.2)
    if env is None:
        return max(t - 0.04, limit)
    while t > limit and _loud(env, t - 0.01):
        t -= 0.01
    return max(t - 0.02, limit)


# --------------------------------------------------------------------------
# the cut
# --------------------------------------------------------------------------

def tight_segments(words, start, end, env=None, fps=30.0):
    """[(s, e)] spans of the source to keep, in order, on the frame grid so
    picture and sound stay in sync across every cut."""
    drop = drop_words(words)
    keep = [i for i in range(len(words)) if i not in drop]
    if not keep:
        return [(start, end)]
    first, last = words[keep[0]], words[keep[-1]]
    s0 = start if keep[0] == 0 else max(start, refine_start(env, first["s"], words[keep[0] - 1]["e"]) - 0.08)
    e_last = end if keep[-1] == len(words) - 1 else refine_end(env, last["e"], words[keep[-1] + 1]["s"]) + 0.3
    segs, pause_only = [[s0, None]], []     # pause_only[i]: the cut after segs[i] removes no words
    for a, b in zip(keep, keep[1:]):
        wa, wb = words[a], words[b]
        ea = refine_end(env, wa["e"], words[a + 1]["s"])
        sb = refine_start(env, wb["s"], words[b - 1]["e"])
        gap = KEEP_END if re.search(r"[.?!]$", wa["w"]) else KEEP_MID
        if b == a + 1 and sb - ea - gap < MIN_CUT:
            continue                                    # a natural pause: leave it
        cut_a, cut_b = ea + gap / 2, sb - gap / 2
        if b > a + 1:                                   # never let a dropped word bleed in
            cut_a, cut_b = min(cut_a, words[a + 1]["s"]), max(cut_b, words[b - 1]["e"])
        if cut_b - cut_a < 0.04:
            continue
        segs[-1][1] = cut_a
        segs.append([cut_b, None])
        pause_only.append(b == a + 1)
    segs[-1][1] = min(end, e_last)

    def q(t):
        return round(t * fps) / fps
    segs = [[q(s), q(e)] for s, e in segs]
    # a sliver of speech between two cuts looks like a glitch: put back the
    # shorter pause beside it (a cut that removes an um always stays)
    while True:
        def gap(j):                                     # the cut after segs[j], if it may go
            ok = 0 <= j < len(segs) - 1 and pause_only[j]
            g = segs[j + 1][0] - segs[j][1] if ok else float("inf")
            return g if g <= MAX_RESTORE else float("inf")
        short = [(min(gap(i - 1), gap(i)), i) for i, (s, e) in enumerate(segs)
                 if e - s < MIN_SEG and min(gap(i - 1), gap(i)) < float("inf")]
        if not short:
            break
        _, i = min(short)
        j = i - 1 if gap(i - 1) <= gap(i) else i
        segs[j:j + 2] = [[segs[j][0], segs[j + 1][1]]]
        del pause_only[j]
    return [(s, e) for s, e in segs if e > s]


def remap(t, segs):
    """A source time on the tight timeline (a time inside a cut lands on the cut)."""
    off = 0.0
    for s, e in segs:
        if t < s:
            return off
        if t <= e:
            return off + t - s
        off += e - s
    return off


def tight_words(words, segs):
    drop = drop_words(words)
    out = []
    for i, w in enumerate(words):
        if i in drop or w.get("_filler"):
            continue
        s, e = remap(w["s"], segs), remap(w["e"], segs)
        out.append(dict(w, s=round(s, 3), e=round(max(e, s + 0.05), 3)))
    return out


def cut_points(segs):
    """Where the cuts fall on the tight timeline."""
    t, out = 0.0, []
    for s, e in segs[:-1]:
        t += e - s
        out.append(t)
    return out


def zoom_spans(segs, total):
    """Punch in at some cuts, back out at a later one: at most one change every
    ZOOM_EVERY seconds, so it reads as editing, not shaking."""
    spans, zoomed, last, since = [], False, 0.0, 0.0
    for t in cut_points(segs):
        if t - last < ZOOM_EVERY:
            continue
        if zoomed:
            spans.append((since, t))
        else:
            since = t
        zoomed, last = not zoomed, t
    if zoomed:
        spans.append((since, total))
    return spans


def zoom_filter(spans, head_top=None, fps=30, size=(1080, 1920)):
    """ffmpeg filter for the vertical clip: ZOOM inside spans, centred a little
    above the middle (on the face, when we know where the head is). A zoomed copy
    laid over the clip inside the spans: zoompan stalls any overlay after it."""
    if not spans:
        return "null"
    w, h = size
    zw = int(round(w * ZOOM / 2)) * 2
    on = "+".join(f"between(t,{a:.3f},{b - 0.5 / fps:.3f})" for a, b in spans)
    fy = min(max((head_top if head_top is not None else 0.25) + 0.12, 0.2), 0.5)
    return (f"split[za][zb];[zb]scale={zw}:-2:flags=lanczos,"
            f"crop={w}:{h}:(iw-{w})/2:(ih-{h})*{fy:.2f}[zz];"
            f"[za][zz]overlay=0:0:enable='{on}'")


def build_source(video, segs, out_path):
    """The tight cut of the source, still full frame (reframing comes after), with
    a 10 ms fade at every audio join so cuts never click."""
    t0, t1 = segs[0][0], segs[-1][1]
    parts, labels = [], []
    for i, (s, e) in enumerate(segs):
        a, b, d = s - t0, e - t0, e - s
        parts.append(f"[0:v]trim=start={a:.4f}:end={b:.4f},setpts=PTS-STARTPTS[v{i}];"
                     f"[0:a]atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d=0.01,afade=t=out:st={max(d - 0.01, 0):.4f}:d=0.01[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    graph = ";".join(parts) + ";" + "".join(labels) + f"concat=n={len(segs)}:v=1:a=1[v][a]"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.3f}", "-t", f"{t1 - t0 + 0.2:.3f}",
                    "-i", video, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
                    "-c:a", "pcm_s16le", out_path], check=True)
    return out_path


# --------------------------------------------------------------------------
# the verbatim pass: finding the ums Whisper usually leaves out
# --------------------------------------------------------------------------

_MODEL = None


def find_fillers(video, start, dur, workdir, model_name="small.en"):
    """[(s, e)] source times of ums and uhs in the clip."""
    global _MODEL
    from faster_whisper import WhisperModel
    wav = os.path.join(workdir, "verbatim.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
                    "-i", video, "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
    if _MODEL is None:
        _MODEL = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = _MODEL.transcribe(wav, language="en", word_timestamps=True, beam_size=5,
                                    initial_prompt=VERBATIM_PROMPT, vad_filter=False,
                                    condition_on_previous_text=False)
    out = [(start + w.start, start + w.end) for seg in segments for w in (seg.words or [])
           if _norm(w.word) in FILLERS]
    os.remove(wav)
    return out


# --------------------------------------------------------------------------
# sound: music bed and whoosh (generated, so there is nothing to license)
# --------------------------------------------------------------------------

RATE = 48000
_PROGRESSIONS = [[(0, "maj"), (7, "maj"), (9, "min"), (5, "maj")],      # I V vi IV
                 [(9, "min"), (5, "maj"), (0, "maj"), (7, "maj")],      # vi IV I V
                 [(0, "maj"), (5, "maj"), (9, "min"), (7, "maj")]]      # I IV vi V
_ROOTS = [196.0, 220.0, 233.08, 174.61]                                 # G, A, Bb, F


def _write_wav(path, stereo):
    pcm = (np.clip(stereo, -1, 1) * 32767).astype("<i2")
    with wave.open(path, "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(pcm.tobytes())
    return path


def _lowpass(x, cutoff):
    a = np.exp(-2 * np.pi * cutoff / RATE)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):            # one-pole: warm, cheap
        acc = (1 - a) * v + a * acc
        y[i] = acc
    return y


def music_bed(path, dur, seed=0, chord_s=4.0):
    """A slow, soft pad (a different key and progression each week)."""
    rng = np.random.default_rng(seed)
    prog = _PROGRESSIONS[seed % len(_PROGRESSIONS)]
    root = _ROOTS[seed % len(_ROOTS)]
    n = int(dur * RATE)
    t = np.arange(n) / RATE
    out = np.zeros((n, 2))
    fade = 1.2
    k = 0
    while k * chord_s < dur:
        off, kind = prog[k % len(prog)]
        a, b = k * chord_s - fade / 2, (k + 1) * chord_s + fade / 2
        m = (t >= max(a, 0)) & (t < b)
        tt = t[m] - a
        env = np.minimum(1, np.minimum(tt / fade, (b - a - tt) / fade)) ** 2
        f0 = root * 2 ** (off / 12)
        notes = [f0 / 2, f0, f0 * 2 ** ((4 if kind == "maj" else 3) / 12), f0 * 2 ** (7 / 12), f0 * 2]
        for j, f in enumerate(notes):
            amp = 0.5 if j == 0 else 0.22
            for ch, det in ((0, 0.997), (1, 1.003)):
                ph = rng.uniform(0, 2 * np.pi)
                wave_ = (np.sin(2 * np.pi * f * det * t[m] + ph)
                         + 0.25 * np.sin(4 * np.pi * f * det * t[m] + ph))
                out[m, ch] += amp * env * wave_
        k += 1
    out *= 1 + 0.08 * np.sin(2 * np.pi * 0.15 * t)[:, None]             # slow swell
    edge = np.minimum(1, np.minimum(t / 1.5, (dur - t) / 2.0)).clip(0)
    out *= edge[:, None]
    out /= np.abs(out).max() + 1e-9
    return _write_wav(path, out * 0.5)


def whoosh(dur=0.7, seed=1):
    """Filtered noise rising then falling: a soft swish."""
    rng = np.random.default_rng(seed)
    n = int(dur * RATE)
    t = np.linspace(0, 1, n)
    env = np.sin(np.pi * t ** 0.7) ** 2
    x = rng.standard_normal(n) * env
    lo = _lowpass(x, 2500)
    sw = lo - _lowpass(lo, 300)                   # band-passed
    sw /= np.abs(sw).max() + 1e-9
    return np.stack([sw * (1 - 0.4 * t), sw * (0.6 + 0.4 * t)], axis=1)  # moves left to right


def sfx_track(path, dur, at=(), level=0.35):
    """A track of silence with a whoosh at each time in `at`."""
    out = np.zeros((int(dur * RATE), 2))
    w = whoosh() * level
    for t in at:
        i = int(t * RATE)
        j = min(len(out), i + len(w))
        if 0 <= i < j:
            out[i:j] += w[:j - i]
    return _write_wav(path, out)
