"""ElevenLabs for the sermon pipeline (ELEVENLABS_API_KEY): Scribe transcription,
Voice Isolator for the reels' voice, and the reels' music bed and whoosh.

Everything here is optional: with no key, if a call fails, or if it would take the
month's usage past the budget, the caller falls back (Whisper, the local audio
clean-up, the generated pad and whoosh), so a week is never lost.

The budget: before each call, the account's own usage for the month is read, and
the call goes ahead only if usage plus its cost stays within ELEVENLABS_BUDGET
(default 0.85) of the plan's monthly credits, so the pipeline can never run the
plan into overage, and leaves room for anything done by hand in ElevenLabs.
"""
import json
import math
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid

API = "https://api.elevenlabs.io/v1"
TIMEOUT = 600

# credits, from ElevenLabs' pricing page (2026-11): per minute of audio, or per generation
COST_PER_MIN = {"transcribe": 330, "isolate": 1000, "music": 900}
COST_SFX = 200
KEYTERMS_SURCHARGE = 1.2
BUDGET = float(os.environ.get("ELEVENLABS_BUDGET", "0.85"))
STT_MODEL = os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v2")

MUSIC_PROMPT = (
    "Instrumental underscore for a short church sermon clip on social media: {mood}. "
    "Soft sustained pads, gentle felt piano and light ambient textures, slow tempo "
    "around 70 BPM, steady and unobtrusive so a spoken voice sits clearly on top. "
    "No drums, no vocals, no big build or drop.")
MOODS = {"gospel": "warm, hopeful and tender",
         "challenge": "quietly determined, with gentle forward motion",
         "story": "reflective and intimate",
         "teaching": "calm, clear and thoughtful"}
WHOOSH_PROMPT = "soft, subtle airy whoosh, a gentle swish for a title appearing, clean, no reverb tail"


class OverBudget(RuntimeError):
    pass


def available():
    return bool(os.environ.get("ELEVENLABS_API_KEY"))


def _headers(extra=None):
    return dict({"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}, **(extra or {}))


# --------------------------------------------------------------------------
# budget
# --------------------------------------------------------------------------

_first = None    # usage the account reported at this run's first call
_committed = 0   # credits this run has spent since then


def usage():
    """(credits used this month, monthly credits) from the account itself."""
    req = urllib.request.Request(f"{API}/user/subscription", headers=_headers())
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    return int(d["character_count"]), int(d["character_limit"])


def reserve(credits, what):
    """Go ahead with a call costing `credits` only if the month stays in budget.
    Raises OverBudget (a RuntimeError, so callers fall back) when it would not, or
    when usage cannot be read: without knowing, we do not spend. The account's
    figure can lag calls made seconds ago, so this run also counts its own."""
    global _first, _committed
    if not available():
        raise OverBudget("no ElevenLabs key")
    try:
        used, limit = usage()
    except Exception as e:
        raise OverBudget(f"could not read ElevenLabs usage ({e}); not spending") from None
    if _first is None:
        _first = used
    used = max(used, _first + _committed)
    if used + credits > limit * BUDGET:
        raise OverBudget(f"{what} needs ~{credits:,} credits; {used:,} of {limit:,} used this "
                         f"month, budget {BUDGET:.0%}")
    _committed += credits
    return credits


def per_minute(kind, seconds, surcharge=1.0):
    return math.ceil(COST_PER_MIN[kind] * seconds / 60 * surcharge)


def audio_seconds(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "csv=p=0", path], capture_output=True, text=True).stdout.strip()
    return float(out or 0)


# --------------------------------------------------------------------------
# requests
# --------------------------------------------------------------------------

def _send(req, path):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="replace")
            if e.code < 500 and e.code != 429:          # a bad request will not improve on retry
                raise RuntimeError(f"ElevenLabs {path}: HTTP {e.code} {detail}") from None
            err = f"HTTP {e.code} {detail}"
        except (urllib.error.URLError, TimeoutError) as e:
            err = str(e)
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"ElevenLabs {path}: {err}")


def _post_json(path, body, out_path):
    req = urllib.request.Request(f"{API}{path}", data=json.dumps(body).encode(), method="POST",
                                 headers=_headers({"Content-Type": "application/json",
                                                   "Accept": "audio/mpeg"}))
    data = _send(req, path)
    if len(data) < 1000:
        raise RuntimeError(f"ElevenLabs {path}: returned an empty file")
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


def _post_multipart(path, fields, file_field, file_path, accept):
    """fields: [(name, value)], repeated names for lists."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                     f"{value}\r\n".encode())
    with open(file_path, "rb") as f:
        payload = f.read()
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
                 f"filename=\"{os.path.basename(file_path)}\"\r\n"
                 f"Content-Type: application/octet-stream\r\n\r\n".encode() + payload + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(f"{API}{path}", data=b"".join(parts), method="POST",
                                 headers=_headers({"Content-Type": f"multipart/form-data; boundary={boundary}",
                                                   "Accept": accept}))
    return _send(req, path)


# --------------------------------------------------------------------------
# the calls
# --------------------------------------------------------------------------

def music(out_path, seconds, kind=""):
    """An instrumental bed about `seconds` long (mp3)."""
    seconds = min(max(seconds, 3.0), 600.0)
    reserve(per_minute("music", seconds), "music")
    prompt = MUSIC_PROMPT.format(mood=MOODS.get(kind, MOODS["gospel"]))
    return _post_json("/music", {"prompt": prompt, "music_length_ms": int(seconds * 1000),
                                 "force_instrumental": True}, out_path)


def sound_effect(out_path, text=WHOOSH_PROMPT, seconds=1.0):
    reserve(COST_SFX, "sound effect")
    return _post_json("/sound-generation", {"text": text, "duration_seconds": seconds,
                                            "prompt_influence": 0.5}, out_path)


def isolate(in_path, out_path):
    """The voice alone, room noise and music taken out (Voice Isolator)."""
    reserve(per_minute("isolate", audio_seconds(in_path)), "voice isolation")
    data = _post_multipart("/audio-isolation", [], "audio", in_path, "audio/mpeg")
    if len(data) < 1000:
        raise RuntimeError("ElevenLabs /audio-isolation: returned an empty file")
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


def transcribe(audio_path, keyterms=()):
    """Scribe, word by word, verbatim (ums kept: the tight cut needs to find them).
    Returns [{"w", "s", "e"}] like the Whisper path."""
    keyterms = [k for k in dict.fromkeys(keyterms) if k and len(k) <= 50 and len(k.split()) <= 5]
    surcharge = KEYTERMS_SURCHARGE if keyterms else 1.0
    reserve(per_minute("transcribe", audio_seconds(audio_path), surcharge), "transcription")
    fields = [("model_id", STT_MODEL), ("language_code", "en"), ("timestamps_granularity", "word"),
              ("tag_audio_events", "false"), ("diarize", "false")]
    fields += [("keyterms", k) for k in keyterms[:100]]
    d = json.loads(_post_multipart("/speech-to-text", fields, "file", audio_path, "application/json"))
    return words_from_scribe(d)


def words_from_scribe(d):
    out = []
    for w in d.get("words", []):
        text = (w.get("text") or "").strip()
        if w.get("type") == "word" and text and w.get("start") is not None and w.get("end") is not None:
            out.append({"w": text, "s": round(float(w["start"]), 2), "e": round(float(w["end"]), 2)})
    return out
