"""
Clean sermon audio before it is made loud.

Room recordings carry a steady hiss (mic preamp, camera, HVAC) about 40 dB
under the voice. On its own it is quiet; loudness normalisation for social
(-14 LUFS) or a podcast (-16 LUFS) then raises it with everything else and
lifts the pauses most, so the hiss swells between sentences. So, before the
loudness step, measured on the clip itself:

  1. high-pass at 75 Hz: rumble (HVAC, stage floor, handling) out
  2. a clean recording (hiss 35 dB or more under the voice, as the Sunday camera
     audio is): a light FFT pass tracking the measured floor, nothing more
  3. a noisy one: RNNoise (ffmpeg arnndn), a small neural network trained to
     keep speech and drop everything else, so it takes hiss out UNDER the voice
     too. Its model (~300 KB) is fetched at run time; without it (no network)
     a firmer FFT pass stands in.
  4. a gentle downward expander in the pauses, so they don't swell; words are
     never gated

RNNoise is kept for noisy recordings only, and mixed with 40% of the original,
because it also takes the soft ends of words (trailing consonants, a dropped
syllable) and any music under the voice. Measured on the 2026-11-01 sermon,
the old chain (RNNoise at 0.9, then FFT, then the expander on top) cut the
softest 10% of word endings by about 7 dB and the pauses by 31 dB; the clean
path costs them under 1 dB. RNNoise followed by FFT compounds, so the noisy
path has no FFT pass.

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
RNN_MODEL = os.environ.get("AUDIO_RNN_MODEL", "std")
RNN_URL = "https://raw.githubusercontent.com/richardpl/arnndn-models/master/{}.rnnn"
RNN_DIR = os.path.join(os.path.expanduser("~"), ".cache", "eastpoint")
RNN_MIX = 0.6               # 40% original: keeps word endings and music under the voice
RNN_BELOW_SNR = 35          # dB voice over hiss: only a recording noisier than this gets RNNoise
CLEAN_NR = 10               # dB, the light FFT pass for a clean recording


def rnn_model():
    """Path to the RNNoise model, downloaded once; None if it cannot be had."""
    path = os.path.join(RNN_DIR, f"{RNN_MODEL}.rnnn")
    if not os.path.exists(path):
        try:
            import urllib.request
            os.makedirs(RNN_DIR, exist_ok=True)
            urllib.request.urlretrieve(RNN_URL.format(RNN_MODEL), path + ".part")
            if os.path.getsize(path + ".part") < 50_000:
                return None
            os.replace(path + ".part", path)
        except Exception:
            return None
    return path


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


def clean_filter(levels, model=None):
    """The ffmpeg audio filters to run before the loudness step, from measure().
    model = path to an RNNoise model (rnn_model()), or None."""
    parts = [f"highpass=f={HIGHPASS_HZ}"]
    if not levels:
        return parts[0]
    floor, speech = levels["floor"], levels["speech"]
    snr = speech - floor
    if floor > -80 and snr > 12:                      # there is hiss to take out, and a voice above it
        thr = 10 ** ((floor + 0.4 * snr) / 20)       # well below speech, above the hiss
        if model and snr < RNN_BELOW_SNR:
            parts.append(f"arnndn=m='{model}':mix={RNN_MIX}")
            # RNNoise already lowered the word endings: a softer, slower expander
            # so it doesn't push them down a second time
            parts.append(f"agate=threshold={thr:.6f}:ratio=2:range=0.5:"
                         f"attack=20:release=500:knee=4")
            return ",".join(parts)
        if snr >= RNN_BELOW_SNR:
            nr = CLEAN_NR
        else:                                          # noisy, and no model to hand
            nr = int(np.clip(MAX_NR - max(0.0, snr - 25) * 0.4, MIN_NR + 4, MAX_NR))
        nf = int(np.clip(floor, -80, -20))
        parts.append(f"afftdn=nr={nr}:nf={nf}:tn=1")
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
    model = rnn_model()
    info["denoise"] = "rnnoise" if model else "fft"
    return clean_filter(levels, model), info
