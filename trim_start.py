"""
Cut the start of a sermon recording so it opens on the scripture reading.

The camera usually rolls a few minutes early: walk-up, announcements, a song
ending. Before anything is made from the video (YouTube draft, podcast MP3), the
opening is transcribed, Claude picks the sentence where the reading begins
(including its lead-in, "turn with me to Acts 3..."), and everything before it
is dropped. The picture and sound are copied, never re-encoded.

If the reading can't be found, or starts within the first few seconds anyway,
the video is left as it was and the log says why.

TRIM_TO_SCRIPTURE=0 turns this off.
"""

import os
import re
import subprocess

SCAN_MINUTES = float(os.environ.get("TRIM_SCAN_MINUTES", "20"))
WHISPER_MODEL = os.environ.get("TRIM_WHISPER_MODEL", "base.en")
MODEL = os.environ.get("TRIM_MODEL", "claude-sonnet-5-5")
LEAD_IN = 1.0               # seconds kept before the first word of the reading
MIN_CUT = 15.0              # don't bother trimming less than this

PROMPT = """Below are the opening minutes of a church sermon recording, transcribed \
and numbered by segment, with start times in seconds. The planned scripture passage \
is: {scripture}.

Find the segment where the scripture reading begins. Include its lead-in if there \
is one, such as "turn with me to...", "our passage today is...", or "hear the word \
of the Lord". Ignore earlier passing mentions of scripture (announcements, prayers, \
song lyrics) unless the passage is read straight after.

Answer with ONLY the segment number, or -1 if the reading does not begin in these \
segments.

<transcript>
{transcript}
</transcript>"""


def opening_segments(video_path, workdir):
    """[{start, text}] for the first SCAN_MINUTES of the recording."""
    wav = os.path.join(workdir, "opening.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-t", f"{SCAN_MINUTES * 60:.0f}",
                    "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
    from faster_whisper import WhisperModel
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(wav, language="en", vad_filter=True, beam_size=5)
    out = [{"start": round(s.start, 2), "text": s.text.strip()} for s in segments if s.text.strip()]
    os.remove(wav)
    return out


def pick_segment(segments, scripture):
    """Index of the segment where the reading starts, or None."""
    import anthropic
    transcript = "\n".join(f"[{i}] ({s['start']:.0f}s) {s['text']}" for i, s in enumerate(segments))
    msg = anthropic.Anthropic().messages.create(
        model=MODEL, max_tokens=20,
        messages=[{"role": "user", "content": PROMPT.format(
            scripture=scripture or "not given", transcript=transcript)}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return parse_index(text, len(segments))


def parse_index(text, n):
    m = re.search(r"-?\d+", text or "")
    if not m:
        return None
    i = int(m.group(0))
    return i if 0 <= i < n else None


def cut_point(segments, i):
    """Seconds to cut at for segment i, or None when it isn't worth cutting."""
    if i is None:
        return None
    t = max(0.0, segments[i]["start"] - LEAD_IN)
    return t if t >= MIN_CUT else None


def trim(video_path, start, out_path):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", video_path,
                    "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero",
                    out_path], check=True)


def to_scripture(video_path, scripture, workdir):
    """(path, note): a copy of the video starting at the scripture reading, or
    the original path when nothing should be cut. Never raises."""
    if os.environ.get("TRIM_TO_SCRIPTURE", "1") == "0":
        return video_path, ""
    try:
        segments = opening_segments(video_path, workdir)
        if not segments:
            return video_path, "trim: no speech in the opening; video left whole"
        i = pick_segment(segments, scripture)
        start = cut_point(segments, i)
        if start is None:
            why = ("reading not found in the opening" if i is None
                   else f"reading starts at {segments[i]['start']:.0f}s already")
            return video_path, f"trim: {why}; video left whole"
        out = os.path.join(workdir, "trimmed" + os.path.splitext(video_path)[1])
        trim(video_path, start, out)
        m, s = divmod(int(start), 60)
        return out, f"trim: cut the first {m}:{s:02d}; opens on \"{segments[i]['text'][:80]}\""
    except Exception as e:                      # a whole video beats no video
        return video_path, f"trim: failed ({e}); video left whole"
