"""ElevenLabs music and sound effects for the reels (ELEVENLABS_API_KEY).

Everything here is optional: with no key, or if a call fails, the reel falls back
to the generated pad and whoosh in reel_fx, so a week's reels are never lost.
"""
import json
import os
import time
import urllib.error
import urllib.request

API = "https://api.elevenlabs.io/v1"
TIMEOUT = 300

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


def available():
    return bool(os.environ.get("ELEVENLABS_API_KEY"))


def _post(path, body, out_path, params=None):
    url = f"{API}{path}" + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else "")
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
        "xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json",
        "Accept": "audio/mpeg"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r, open(out_path, "wb") as f:
                f.write(r.read())
            if os.path.getsize(out_path) < 1000:
                raise ValueError("ElevenLabs returned an empty file")
            return out_path
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="replace")
            if e.code < 500 and e.code != 429:          # a bad request will not improve on retry
                raise RuntimeError(f"ElevenLabs {path}: HTTP {e.code} {detail}") from None
            err = f"HTTP {e.code} {detail}"
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            err = str(e)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"ElevenLabs {path}: {err}")


def music(out_path, seconds, kind=""):
    """An instrumental bed about `seconds` long (mp3)."""
    prompt = MUSIC_PROMPT.format(mood=MOODS.get(kind, MOODS["gospel"]))
    ms = int(min(max(seconds, 3.0), 600.0) * 1000)
    return _post("/music", {"prompt": prompt, "music_length_ms": ms, "force_instrumental": True},
                 out_path)


def sound_effect(out_path, text=WHOOSH_PROMPT, seconds=1.0):
    return _post("/sound-generation", {"text": text, "duration_seconds": seconds,
                                       "prompt_influence": 0.5}, out_path)
