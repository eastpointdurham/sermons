"""
Swap the camera's sound for the soundboard recording, in sync.

Drop the board recording (WAV, MP3, M4A, AIFF, FLAC) in the Drive "Sermons"
folder with the service date in its name, the same way as the video, e.g.
"Board 10-11-26.wav". Before anything else is made from the video (YouTube
draft, podcast MP3, transcript, reels), the board audio is lined up with the
camera's own audio and replaces it:

  1. coarse: the loudness envelopes of both (10 ms steps) are cross-correlated
     over the whole board recording, so it may start well before the video
     (a full-service recording is fine)
  2. fine: the waveforms are matched to the sample around that point, once
     early and once late in the video, which also measures clock drift between
     camera and board (a frame or two over a sermon) and corrects it
  3. a little camera audio is mixed underneath (BOARD_ROOM_MIX, default 0.2,
     about -14 dB) so laughter and "amens" in the room are not lost

If the two do not clearly match, or the board recording does not cover the
video, the camera audio is kept and the log says why. The picture is copied,
never re-encoded.
"""

import os
import re
import subprocess

import numpy as np

AUDIO_EXT = re.compile(r"\.(wav|mp3|m4a|aac|aiff?|flac)$", re.I)
ENV_RATE = 100              # envelope frames per second
ENV_DECODE = 4000           # Hz, decoding for the envelope (keeps memory low)
FINE_RATE = 8000            # Hz, for sample-level matching
MAX_DRIFT = 5e-4            # camera and board clocks differ by far less than this
ROOM_MIX = float(os.environ.get("BOARD_ROOM_MIX", "0.2"))
MIN_SCORE = 0.25            # normalised correlation needed to trust a match


def is_board_audio(name, mime_type=""):
    return bool(AUDIO_EXT.search(name or "")) or (mime_type or "").startswith("audio/")


def find_board(files, service_date, parse_date):
    """The board recording for service_date among Drive files, or None.
    parse_date: the filename date parser (upload_sermon.parse_date_from_filename)."""
    hits = [f for f in files if is_board_audio(f["name"], f.get("mimeType"))
            and not re.match(r"^copy of ", f["name"], re.I)
            and parse_date(f["name"]) == service_date]
    return max(hits, key=lambda f: f.get("createdTime", "")) if hits else None


def _pcm(path, rate, start=None, dur=None):
    cmd = ["ffmpeg", "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{max(0.0, start):.3f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", path, "-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(out, np.float32).astype(np.float64)


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    return float(out.stdout.strip() or 0)


def envelope(x, rate):
    """Log loudness per 10 ms, standardised: what both microphones agree on."""
    n = rate // ENV_RATE
    frames = x[:len(x) // n * n].reshape(-1, n)
    e = np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-5)
    e = np.diff(e, prepend=e[:1])               # onsets: robust to the rooms' tone
    return (e - e.mean()) / (e.std() + 1e-9)


def _xcorr(a, b):
    """Correlation of b against a at every lag; lag k means b[i] ~ a[i + k]."""
    n = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    c = np.fft.irfft(np.fft.rfft(a, n) * np.conj(np.fft.rfft(b, n)), n)
    lags = np.concatenate([np.arange(0, len(a)), np.arange(-len(b) + 1, 0)])
    vals = np.concatenate([c[:len(a)], c[n - len(b) + 1:]])
    return lags, vals


def coarse_offset(cam_env, board_env):
    """Seconds into the board recording where the video starts, and a score."""
    lags, vals = _xcorr(board_env, cam_env)
    i = int(np.argmax(vals))
    overlap = min(len(cam_env), len(board_env))
    return lags[i] / ENV_RATE, float(vals[i] / overlap)


def fine_offset(video, board, t, guess, span=30.0, search=0.15):
    """Board time matching video time t, to the sample, near board time guess + t."""
    cam = _pcm(video, FINE_RATE, t, span)
    lo = guess + t - search
    brd = _pcm(board, FINE_RATE, lo, span + 2 * search)
    if len(cam) < FINE_RATE or len(brd) < len(cam):
        return None, 0.0
    cam = (cam - cam.mean()) / (cam.std() + 1e-9)
    brd = (brd - brd.mean()) / (brd.std() + 1e-9)
    lags, vals = _xcorr(brd, cam)
    ok = (lags >= 0) & (lags <= len(brd) - len(cam))
    lags, vals = lags[ok], vals[ok]
    i = int(np.argmax(np.abs(vals)))
    return max(0.0, lo) + lags[i] / FINE_RATE - t, float(abs(vals[i]) / len(cam))


def measure(video, board):
    """{"offset": board seconds at video 0, "drift": board s per video s - 1,
    "score", "covered"} or {"error": why}."""
    vd, bd = duration(video), duration(board)
    if vd < 60 or bd < 60:
        return {"error": "recording too short"}
    cam_env = envelope(_pcm(video, ENV_DECODE), ENV_DECODE)
    board_env = envelope(_pcm(board, ENV_DECODE), ENV_DECODE)
    off, score = coarse_offset(cam_env, board_env)
    if score < MIN_SCORE / 2:
        return {"error": f"no clear match (score {score:.2f}) — wrong file?"}
    t1, t2 = min(120.0, vd * 0.1), max(vd * 0.9 - 30, min(120.0, vd * 0.1) + 1)
    o1, s1 = fine_offset(video, board, t1, off)
    o2, s2 = fine_offset(video, board, t2, off)
    if o1 is None or o2 is None or min(s1, s2) < MIN_SCORE:
        return {"error": f"match too weak (scores {s1:.2f}/{s2:.2f})"}
    drift = (o2 - o1) / (t2 - t1)
    if abs(drift) > MAX_DRIFT:
        return {"error": f"early and late do not agree ({o1:.2f}s vs {o2:.2f}s)"}
    offset = o1 - drift * t1
    start, end = offset, offset + vd * (1 + drift)
    covered = (min(end, bd) - max(start, 0.0)) / (end - start)
    return {"offset": round(offset, 4), "drift": drift, "score": round(min(s1, s2), 2),
            "covered": round(covered, 3)}


def mux(video, board, m, out):
    """Write out = video's picture + board audio aligned per measure() (+ a little room)."""
    tempo = 1 + m["drift"]
    chain = []
    if m["offset"] >= 0:
        board_in = ["-ss", f"{m['offset']:.4f}", "-i", board]
        lead = ""
    else:
        board_in = ["-i", board]
        lead = f"adelay={int(round(-m['offset'] * 1000))}:all=1,"
    chain.append(f"[1:a]aresample=48000,{lead}"
                 + (f"atempo={tempo:.7f}," if abs(m["drift"]) > 2e-5 else "")
                 + "aformat=channel_layouts=stereo[b]")
    if ROOM_MIX > 0:
        chain.append(f"[0:a]aresample=48000,aformat=channel_layouts=stereo,volume={ROOM_MIX}[r]")
        chain.append("[b][r]amix=inputs=2:duration=first:normalize=0,"
                     "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]")
    else:
        chain.append("[b]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, *board_in,
                    "-filter_complex", ";".join(chain), "-map", "0:v", "-map", "[a]",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                    "-movflags", "+faststart", out], check=True)


def apply(video, board, workdir):
    """Path of the video with board audio (or the original video), and a log note."""
    if os.environ.get("BOARD_AUDIO", "on").lower() in ("off", "0", "no"):
        return video, "board audio switched off"
    try:
        m = measure(video, board)
        if "error" in m:
            return video, f"kept camera audio: {m['error']}"
        if m["covered"] < 0.9:
            return video, f"kept camera audio: board recording covers only {m['covered']:.0%}"
        out = os.path.join(workdir, "with-board" + os.path.splitext(video)[1])
        mux(video, board, m, out)
        return out, (f"board audio in: offset {m['offset']:+.3f}s, drift "
                     f"{m['drift'] * 1e6:+.0f} ppm, match {m['score']}")
    except Exception as e:                       # never lose an upload over this
        return video, f"kept camera audio: {e}"


def from_drive(drive, folder_id, video, service_date, workdir, list_folder, download, parse_date):
    """apply() with the board recording for service_date fetched from a Drive folder.
    Returns (video path to use, note or None when there is no board recording)."""
    board = find_board(list_folder(drive, folder_id), service_date, parse_date)
    if not board:
        return video, None
    path = os.path.join(workdir, "board" + (os.path.splitext(board["name"])[1] or ".wav"))
    download(drive, board["id"], path)
    out, note = apply(video, path, workdir)
    os.remove(path)
    return out, f"{board['name']}: {note}"
