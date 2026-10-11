"""
Cut a sermon recording so it opens on the scripture reading and ends on the
"Amen" of the closing prayer.

The camera usually rolls a few minutes early: walk-up, announcements, a song
ending. Before anything is made from the video (YouTube draft, podcast MP3), the
opening is transcribed, Claude picks the sentence where the reading begins
(including its lead-in, "turn with me to Acts 3..."), and everything before it
is dropped. The same is done at the end: the closing minutes are transcribed,
Claude picks the "Amen" that closes the prayer after the message, and whatever
follows (a song, announcements) is dropped. The picture and sound are copied,
never re-encoded.

Each end is judged on its own: if the reading or the Amen can't be found, or
sits within a few seconds of the edge anyway, that end is left as it was and the
log says why.

TRIM_TO_SCRIPTURE=0 turns this off.
"""

import os
import re
import subprocess

SCAN_MINUTES = float(os.environ.get("TRIM_SCAN_MINUTES", "20"))
END_SCAN_MINUTES = float(os.environ.get("TRIM_END_SCAN_MINUTES", "12"))
WHISPER_MODEL = os.environ.get("TRIM_WHISPER_MODEL", "base.en")
MODEL = os.environ.get("TRIM_MODEL", "claude-opus-5-5")
LEAD_IN = 0.4               # seconds kept before the first word of the reading
LEAD_OUT = 0.8              # seconds kept after the closing "Amen"
MIN_CUT = 15.0              # don't bother trimming less than this

PROMPT = """Below are the opening minutes of a church sermon recording, transcribed \
and numbered by sentence, with start times in seconds. The planned scripture passage \
is: {scripture}.

Find the sentence where the scripture reading begins. At this church it is \
usually introduced as "Now hear from {book_phrase}"; start on that sentence. If it \
is worded differently ("turn with me to...", "our passage today is..."), start on \
that lead-in instead, but never on the end of a prayer before it ("...in Jesus' \
name. Amen."). Ignore earlier passing mentions of scripture (announcements, prayers, song \
lyrics) unless the passage is read straight after.

Answer with ONLY the sentence number, or -1 if the reading does not begin in these \
sentences.

<transcript>
{transcript}
</transcript>"""

END_PROMPT = """Below are the closing minutes of a church sermon recording, transcribed \
and numbered by sentence, with start times in seconds.

Find the sentence that ends the sermon: the one with the "Amen" that closes the \
prayer at the end of the message. Anything after it (a song, announcements, a benediction, \
an offering) is to be cut. If the preacher prays more than once, pick the prayer \
that closes the message, not one in the middle of it.

Answer with ONLY the sentence number, or -1 if the sermon does not end in these \
sentences.

<transcript>
{transcript}
</transcript>"""


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    return float(out.stdout.strip() or 0)


def opening_segments(video_path, workdir, start=0.0, minutes=None):
    """[{start, end, text}] per sentence for the first SCAN_MINUTES of the recording.
    Sentences, not Whisper's segments: a segment can run "...Amen. Now hear from
    the Gospel of Mark", and the cut belongs in the middle of it."""
    wav = os.path.join(workdir, "opening.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}",
                    "-t", f"{(minutes or SCAN_MINUTES) * 60:.0f}",
                    "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
    from faster_whisper import WhisperModel
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(wav, language="en", vad_filter=True, beam_size=5,
                                   word_timestamps=True)
    words = [{"w": w.word.strip(), "s": w.start + start, "e": w.end + start}
             for seg in segments for w in seg.words or [] if w.word.strip()]
    os.remove(wav)
    return sentences(words)


def sentences(words):
    """Group words into sentences (or ~40-word runs) with their time spans."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        ends = re.search(r"[.?!][\"'”’)]*$", w["w"])
        gap = i + 1 < len(words) and words[i + 1]["s"] - w["e"] > 1.2
        if ends or gap or len(cur) >= 40:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"start": round(c[0]["s"], 2), "end": round(c[-1]["e"], 2),
             "text": " ".join(w["w"] for w in c)} for c in out]


def book_phrase(scripture):
    """How the reading is announced: "the Gospel of Mark", "the book of Acts"."""
    m = re.match(r"\s*((?:[1-3]\s*)?[A-Za-z]+(?:\s+of\s+[A-Za-z]+)?)", scripture or "")
    if not m:
        return "the [book of the Bible]"
    book = m.group(1).strip()
    if book in ("Matthew", "Mark", "Luke", "John"):
        return f"the Gospel of {book}"
    return f"the book of {book}"


def pick_segment(segments, scripture):
    """Index of the sentence where the reading starts, or None."""
    return ask(segments, PROMPT, scripture=scripture or "not given",
               book_phrase=book_phrase(scripture))


def pick_end(segments):
    """Index of the sentence with the closing "Amen", or None."""
    return ask(segments, END_PROMPT)


def ask(segments, prompt, **fields):
    import anthropic
    transcript = "\n".join(f"[{i}] ({s['start']:.0f}s) {s['text']}" for i, s in enumerate(segments))
    msg = anthropic.Anthropic().messages.create(
        model=MODEL, max_tokens=4000, output_config={"effort": "low"},
        messages=[{"role": "user", "content": prompt.format(transcript=transcript, **fields)}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return parse_index(text, len(segments))


def parse_index(text, n):
    m = re.search(r"-?\d+", text or "")
    if not m:
        return None
    i = int(m.group(0))
    return i if 0 <= i < n else None


def cut_point(segments, i):
    """Seconds to cut at for sentence i, or None when it isn't worth cutting.
    A little before its first word, but never back into the sentence before."""
    if i is None:
        return None
    t = max(0.0, segments[i]["start"] - LEAD_IN)
    if i > 0:
        t = max(t, segments[i - 1].get("end", 0.0))
    return t if t >= MIN_CUT else None


def end_point(segments, i, total):
    """Seconds to end at after sentence i, or None when it isn't worth cutting.
    A little after its last word, but never into the sentence after."""
    if i is None:
        return None
    t = segments[i]["end"] + LEAD_OUT
    if i + 1 < len(segments):
        t = min(t, segments[i + 1]["start"])
    return t if total - t >= MIN_CUT else None


def keyframes(path, lo, hi):
    """Video keyframe times between lo and hi seconds."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-read_intervals", f"{max(0.0, lo - 1):.3f}%{hi + 1:.3f}",
                          "-skip_frame", "nokey", "-show_entries", "frame=pts_time",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    ts = [float(x) for x in re.findall(r"[\d.]+", out.stdout)]
    return sorted(t for t in ts if lo <= t <= hi)


def snap_start(target, lo, hi, kfs):
    """A stream copy can only start on a keyframe: the one between lo (end of the
    sentence before) and hi (first word of the reading) nearest the target, or
    None when there is none and the opening has to be re-encoded."""
    inside = [t for t in kfs if lo <= t <= hi]
    return min(inside, key=lambda t: abs(t - target)) if inside else None


def trim(video_path, start, out_path, end=None, reencode=False):
    """Copy from start to end. Copying starts on the keyframe at or before start,
    so callers snap start to one first, or re-encode (frame-exact, slower)."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", video_path]
    if end is not None:
        cmd += ["-t", f"{end - start:.3f}"]
    codec = (["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac",
              "-b:a", "192k"] if reencode else ["-c", "copy"])
    subprocess.run(cmd + ["-map", "0:v:0", "-map", "0:a?"] + codec
                   + ["-avoid_negative_ts", "make_zero", out_path], check=True)


def _clock(t):
    m, s = divmod(int(t), 60)
    return f"{m}:{s:02d}"


def to_scripture(video_path, scripture, workdir):
    """(path, note): a copy of the video from the scripture reading to the closing
    "Amen", or the original path when nothing should be cut. Never raises."""
    if os.environ.get("TRIM_TO_SCRIPTURE", "1") == "0":
        return video_path, ""
    notes, start, end, total, reencode = [], 0.0, None, None, False
    try:
        total = duration(video_path)
        segments = opening_segments(video_path, workdir)
        i = pick_segment(segments, scripture) if segments else None
        start = cut_point(segments, i) or 0.0
        if start:
            lo = segments[i - 1]["end"] if i > 0 else 0.0
            kf = snap_start(start, lo, segments[i]["start"],
                            keyframes(video_path, lo, segments[i]["start"]))
            if kf is None:
                reencode = True                # no keyframe in the gap: cut exactly
            else:
                start = kf
            notes.append(f"cut the first {_clock(start)}; opens on \"{segments[i]['text'][:80]}\""
                         + (" (re-encoded for an exact cut)" if reencode else ""))
        else:
            notes.append("start left whole (" + ("no reading found" if i is None else
                         f"reading starts at {segments[i]['start']:.0f}s already") + ")")
    except Exception as e:                      # a whole video beats no video
        notes.append(f"start left whole (failed: {e})")
    try:
        if total is None:
            raise RuntimeError("no duration")
        tail_from = max(start, total - END_SCAN_MINUTES * 60)
        closing = opening_segments(video_path, workdir, tail_from, END_SCAN_MINUTES)
        j = pick_end(closing) if closing else None
        end = end_point(closing, j, total)
        if end:
            notes.append(f"cut the last {_clock(total - end)}; ends on \"{closing[j]['text'][-80:]}\"")
        else:
            notes.append("end left whole (" + ("no closing Amen found" if j is None else
                         "Amen is at the very end already") + ")")
    except Exception as e:
        end = None
        notes.append(f"end left whole (failed: {e})")
    if not start and end is None:
        return video_path, "trim: " + "; ".join(notes)
    try:
        out = os.path.join(workdir, "trimmed" + os.path.splitext(video_path)[1])
        trim(video_path, start, out, end, reencode)
        return out, "trim: " + "; ".join(notes)
    except Exception as e:
        return video_path, f"trim: failed ({e}); video left whole"
