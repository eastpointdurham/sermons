"""
Clean sermon audio before it is made loud.

Room recordings carry a steady hiss (mic preamp, camera, HVAC) about 40 dB
under the voice. On its own it is quiet; loudness normalisation for social
(-14 LUFS) or a podcast (-16 LUFS) then raises it with everything else and
lifts the pauses most, so the hiss swells between sentences. So, before the
loudness step, measured on the clip itself:

  1. high-pass at 75 Hz: rumble (HVAC, stage floor, handling) out
  2. FFT denoise set from the measured noise floor, tracking it as it drifts
  3. a gentle downward expander in the pauses (at most -12 dB), so they don't
     swell; words are never gated

AUDIO_CLEAN=off leaves the audio as it was.
"""

import os
import subprocess

import numpy as np

HIGHPASS_HZ = 75
MAX_NR = 18                 # dB; more starts to sound watery
MIN_NR = 8
GATE_RANGE = 0.25           # -12 dB at most in the pauses
FRAME = 800                 # 50 ms at 16 kHz


def measure(path, start=0.0, dur=None):
    """Noise floor and speech level (dBFS, 50 ms frames) of a stretch of audio."""
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}"]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-i", path, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]
    x = np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, np.float32)
    if len(x) < FRAME * 20:
        return None
    frames = x[:len(x) // FRAME * FRAME].reshape(-1, FRAME)
    db = 20 * np.log10(np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1)) + 1e-9)
    return {"floor": float(np.percentile(db, 10)), "speech": float(np.percentile(db, 95))}


def clean_filter(levels):
    """The ffmpeg audio filters to run before the loudness step, from measure()."""
    parts = [f"highpass=f={HIGHPASS_HZ}"]
    if not levels:
        return parts[0]
    floor, speech = levels["floor"], levels["speech"]
    snr = speech - floor
    if floor > -80 and snr > 12:                      # there is hiss to take out, and a voice above it
        nr = int(np.clip(MAX_NR - (snr - 30) * 0.3, MIN_NR, MAX_NR))   # noisier -> firmer
        nf = int(np.clip(floor, -80, -20))
        parts.append(f"afftdn=nr={nr}:nf={nf}:tn=1")
        thr = 10 ** ((floor + 0.4 * snr) / 20)       # well below speech, above the hiss
        parts.append(f"agate=threshold={thr:.6f}:ratio=2:range={GATE_RANGE}:"
                     f"attack=10:release=300:knee=4")
    return ",".join(parts)


def filters(path, start=0.0, dur=None):
    """(filter string, info). "" + info when cleaning is off or cannot be measured."""
    if os.environ.get("AUDIO_CLEAN", "on").lower() in ("off", "0", "no"):
        return "", {"audio": "off"}
    try:
        levels = measure(path, start, dur)
    except Exception as e:                            # never lose a reel or an MP3 over this
        return f"highpass=f={HIGHPASS_HZ}", {"audio": "highpass only", "error": str(e)}
    info = {"audio": "clean"}
    if levels:
        info.update({k: round(v, 1) for k, v in levels.items()})
    return clean_filter(levels), info
