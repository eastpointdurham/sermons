#!/usr/bin/env python3
"""
Turn each new Sunday sermon into draft social reels + a content plan.

Runs in GitHub Actions right after the existing sermon pipeline (upload_sermon.py
puts the recording on YouTube as a private draft). For each new sermon it:

  1. downloads the recording from Drive (the "Sermons" folder, plus any extra
     folders in SOCIAL_EXTRA_FOLDER_IDS),
  2. transcribes it with word-level timestamps (faster-whisper),
  3. asks Claude to pick 2-3 standalone moments using social/strategy.md,
  4. renders each moment as a 1080x1920 reel in Eastpoint's brand style
     (hook banner, word-by-word captions, scripture tag, end card),
  5. uploads the reels, a Google Doc content plan and plan.json to a dated
     folder inside the Drive "Social Drafts" folder.

Nothing is ever posted. A person reviews the drafts in Drive first.

Environment:
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_DRIVE_REFRESH_TOKEN   Drive read/write
  ANTHROPIC_API_KEY
  SERMON_FOLDER_ID             main Sermons folder (same as upload_sermon.py)
  SOCIAL_EXTRA_FOLDER_IDS      comma-separated extra folders to watch (optional)
  SOCIAL_DRAFTS_FOLDER_ID      parent folder for drafts (created under the
                               Sermons folder's parent if unset)
  SOCIAL_MODEL                 Claude model (default claude-sonnet-4-5)
  WHISPER_MODEL                faster-whisper model (default small.en)
  SOCIAL_MAX_AGE_DAYS          only sermons this recent (default 10)
  SOCIAL_ONLY_FILE_ID          process exactly this Drive file (manual runs)
  SOCIAL_LAYOUT                fill | framed (default fill)
  SOCIAL_CROP_X                -0.5..0.5 manual horizontal crop nudge (default: auto face)
  DRY_RUN                      "1" = select + write plan locally, upload nothing
  LOCAL_VIDEO                  path to a local video (testing; skips Drive download)
  LOCAL_OUT                    write outputs to this folder instead of Drive (testing)
  SOCIAL_WORDS_JSON            reuse a saved word-timing file (testing; skips whisper)
  SOCIAL_PLAN_JSON             reuse a saved plan (testing; skips the model call)
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "social_clips.json")
STRATEGY_FILE = os.path.join(HERE, "social", "strategy.md")
BRAND_FILE = os.path.join(HERE, "social", "brand.json")
FONTS_DIR = os.path.join(HERE, "social", "fonts")

MODEL = os.environ.get("SOCIAL_MODEL", "claude-sonnet-4-5")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small.en")
MAX_AGE_DAYS = int(os.environ.get("SOCIAL_MAX_AGE_DAYS", "10"))
LAYOUT = os.environ.get("SOCIAL_LAYOUT", "fill")
DRY_RUN = os.environ.get("DRY_RUN") == "1"

W, H = 1080, 1920
MIN_LEN, MAX_LEN = 20.0, 88.0          # Facebook Reels cap at 90 s
END_CARD_SECONDS = 2.0


def log(*a):
    print(*a, flush=True)


def load_brand():
    with open(BRAND_FILE) as f:
        return json.load(f)


DEFAULT_FONTS_FOLDER = "1lkJHdXUGK_CXZVnLl9ZfWv6hc_IVGGhf"   # Drive: Oakes-Grotesk font files


def fetch_fonts(drive):
    """Download the licensed brand fonts from the private Drive folder. They are
    never committed: this repository is public."""
    folder = os.environ.get("SOCIAL_FONTS_FOLDER_ID", DEFAULT_FONTS_FOLDER)
    os.makedirs(FONTS_DIR, exist_ok=True)
    got = 0
    for f in list_folder(drive, folder):
        name = f["name"]
        if not re.search(r"\.(otf|ttf)$", name, re.I) or re.search(r"italic", name, re.I):
            continue
        dest = os.path.join(FONTS_DIR, name)
        if not os.path.exists(dest):
            download(drive, f["id"], dest)
        got += 1
    log(f"  {got} brand font file(s) ready")


def font_families(folder):
    fams = set()
    if not os.path.isdir(folder):
        return fams
    for name in os.listdir(folder):
        if not re.search(r"\.(otf|ttf)$", name, re.I):
            continue
        try:
            out = subprocess.run(["fc-scan", "--format", "%{family}\n", os.path.join(folder, name)],
                                 capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            continue
        for line in out.splitlines():
            fams.update(x.strip() for x in line.split(",") if x.strip())
    return fams


def resolve_font(brand):
    """The brand font if its files are present, else the fallback, else libass default."""
    fams = font_families(FONTS_DIR)
    for want in (brand["font_family"], brand.get("fallback_font", "Archivo")):
        if want in fams:
            return want
    log(f"  ! {brand['font_family']} not found in {FONTS_DIR}; using the default font")
    return brand["font_family"]


def probe_video(path):
    """(width, height, fps, duration) of the first video stream."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate:format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    st = j["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    return int(st["width"]), int(st["height"]), float(num) / float(den), float(j["format"]["duration"])


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return []


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------
# Drive
# --------------------------------------------------------------------------

def drive_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    c = Credentials(
        None,
        refresh_token=os.environ["GOOGLE_DRIVE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=c, cache_discovery=False)


# Retries for transient network errors (dropped TLS connections, 5xx, rate limits).
DRIVE_RETRIES = 5


def list_folder(drive, folder_id):
    out, token = [], None
    while True:
        resp = drive.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="nextPageToken, files(id,name,mimeType,size,createdTime,parents)",
            pageSize=200, supportsAllDrives=True, includeItemsFromAllDrives=True,
            pageToken=token,
        ).execute(num_retries=DRIVE_RETRIES)
        out += resp.get("files", [])
        token = resp.get("nextPageToken")
        if not token:
            return out


def download(drive, file_id, dest):
    from googleapiclient.http import MediaIoBaseDownload
    req = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(dest, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req, chunksize=64 * 1024 * 1024)
        done = False
        while not done:
            status, done = dl.next_chunk(num_retries=DRIVE_RETRIES)
            if status:
                log(f"    {int(status.progress() * 100)}%")


def ensure_folder(drive, name, parent_id):
    q = (f"name = '{name.replace(chr(39), chr(92) + chr(39))}' and '{parent_id}' in parents "
         "and mimeType = 'application/vnd.google-apps.folder' and trashed = false")
    found = drive.files().list(q=q, fields="files(id)", supportsAllDrives=True,
                               includeItemsFromAllDrives=True).execute(num_retries=DRIVE_RETRIES).get("files", [])
    if found:
        return found[0]["id"]
    return drive.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
        fields="id", supportsAllDrives=True).execute(num_retries=DRIVE_RETRIES)["id"]


def upload(drive, path, name, parent_id, mime, as_google_doc=False):
    """Upload into parent_id. A file already there under the same name gets a new
    version instead of a duplicate (re-runs replace last week's drafts; Drive
    keeps the old one in the file's version history, and links stay the same)."""
    from googleapiclient.http import MediaFileUpload
    q = (f"name = '{name.replace(chr(92), chr(92) * 2).replace(chr(39), chr(92) + chr(39))}' "
         f"and '{parent_id}' in parents and trashed = false")
    found = drive.files().list(q=q, fields="files(id)", supportsAllDrives=True,
                               includeItemsFromAllDrives=True).execute(
                                   num_retries=DRIVE_RETRIES).get("files", [])
    media = MediaFileUpload(path, mimetype=mime, resumable=True)
    if found:
        return drive.files().update(fileId=found[0]["id"], media_body=media,
                                    fields="id,webViewLink", supportsAllDrives=True
                                    ).execute(num_retries=DRIVE_RETRIES)
    body = {"name": name, "parents": [parent_id]}
    if as_google_doc:
        body["mimeType"] = "application/vnd.google-apps.document"
    return drive.files().create(body=body, media_body=media, fields="id,webViewLink",
                                supportsAllDrives=True).execute(num_retries=DRIVE_RETRIES)


# --------------------------------------------------------------------------
# which sermons
# --------------------------------------------------------------------------

def find_new_sermons(drive, state):
    from upload_sermon import is_sermon_video, parse_date_from_filename
    done = {s["drive_file_id"] for s in state}
    done_dates = {s["service_date"] for s in state}
    folders = [os.environ.get("SERMON_FOLDER_ID", "1SCMlaqWua24gPPU-Z7yF19pisehp3xu_")]
    folders += [f.strip() for f in os.environ.get("SOCIAL_EXTRA_FOLDER_IDS", "").split(",") if f.strip()]

    only = os.environ.get("SOCIAL_ONLY_FILE_ID")
    candidates = {}
    for folder in folders:
        for f in list_folder(drive, folder):
            if only and f["id"] != only:
                continue
            if not is_sermon_video(f["name"], f.get("mimeType")):
                continue
            d = parse_date_from_filename(f["name"])
            if not only and (f["id"] in done or d.isoformat() in done_dates):
                continue
            if not only and (date.today() - d).days > MAX_AGE_DAYS:
                continue
            f["service_date"] = d
            # one recording per Sunday: the first folder listed wins, then the
            # smaller file (an edited export beats a raw camera file)
            prev = candidates.get(d)
            if prev is None or (prev["_folder"] != folders[0] and folder == folders[0]) or (
                    prev["_folder"] == folder and int(f.get("size", 0)) < int(prev.get("size", 0))):
                f["_folder"] = folder
                candidates[d] = f
    return [candidates[d] for d in sorted(candidates)]


def sermon_meta(service_date):
    """Title / scripture from the YouTube upload log, when present."""
    path = os.path.join(HERE, "uploaded_sermons.json")
    if os.path.exists(path):
        for s in json.load(open(path)):
            if s.get("service_date") == service_date.isoformat():
                # the YouTube title is "Title | Scripture | Preacher | Eastpoint Church Durham"
                parts = [p.strip() for p in s.get("title", "").split("|")]
                return {"title": parts[0], "youtube_id": s.get("video_id", ""),
                        "scripture": parts[1] if len(parts) > 1 else "",
                        "preacher": parts[2] if len(parts) > 2 else ""}
    return {"title": "", "youtube_id": "", "scripture": "", "preacher": ""}


# --------------------------------------------------------------------------
# transcription
# --------------------------------------------------------------------------

def transcribe(video_path, workdir):
    wav = os.path.join(workdir, "audio.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video_path, "-vn", "-ac", "1",
                    "-ar", "16000", wav], check=True)
    from faster_whisper import WhisperModel
    log(f"  transcribing with {WHISPER_MODEL}…")
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(wav, language="en", word_timestamps=True,
                                   vad_filter=True, beam_size=5)
    words = []
    for seg in segments:
        for w in seg.words or []:
            t = w.word.strip()
            if t:
                words.append({"w": t, "s": round(w.start, 2), "e": round(w.end, 2)})
    os.remove(wav)
    log(f"  {len(words)} words")
    return words


def sentences_from_words(words):
    """Group words into sentences (or ~40-word runs) with their time spans."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        ends = re.search(r"[.?!][\"'”’)]*$", w["w"])
        gap = i + 1 < len(words) and words[i + 1]["s"] - w["e"] > 1.2
        if ends or gap or len(cur) >= 40:
            out.append({"a": cur[0], "b": cur[-1]})
            cur = []
    if cur:
        out.append({"a": cur[0], "b": cur[-1]})
    for s in out:
        s["start"] = words[s["a"]]["s"]
        s["end"] = words[s["b"]]["e"]
        s["text"] = " ".join(words[k]["w"] for k in range(s["a"], s["b"] + 1))
    return out


def fmt_t(t):
    return f"{int(t // 60)}:{int(t % 60):02d}"


# --------------------------------------------------------------------------
# moment selection
# --------------------------------------------------------------------------

PROMPT = """You are the social media producer for Eastpoint Church in Durham, NC. \
Below is the church's social media strategy, then a timed transcript of Sunday's \
sermon split into numbered sentences.

<strategy>
{strategy}
</strategy>

Sermon: {title}
Service date: {service_date}
Preacher: {preacher}

<transcript>
{transcript}
</transcript>

Choose {n_min} to {n_max} clips for short vertical reels, following the strategy's \
clip selection rubric exactly. Each clip is a contiguous run of sentences, from \
start_sentence to end_sentence inclusive, lasting {min_len:.0f} to {max_len:.0f} \
seconds (use the timestamps). Clips must not overlap. Prefer fewer, stronger clips \
over filler.

Return ONLY a JSON object, no prose, in this shape:
{{
  "sermon_big_idea": "one sentence",
  "clips": [
    {{
      "start_sentence": 12,
      "end_sentence": 19,
      "hook": "banner text, max 42 characters, no ending period",
      "scripture": "reference shown on screen if the clip quotes or explains a passage, e.g. Luke 18:1-8 NIV, else empty string",
      "why": "one sentence: why this works for someone who has never been to church",
      "scores": {{"hook": 1-5, "standalone": 1-5, "gospel": 1-5, "emotional_truth": 1-5, "shareability": 1-5}},
      "kind": "gospel | practical | story | skeptic-question",
      "instagram_caption": "per the strategy caption rules: question/statement line, 1-2 short paragraphs, From \\"<series>: <title>\\" line, one next step (different on each clip), eastpointdurham.com, at most two local hashtags",
      "facebook_caption": "same copy, no hashtags, may add one warm sentence of invitation",
      "youtube_title": "under 70 characters, a searchable question or phrase, end with #shorts",
      "youtube_description": "2 sentences + 'Full message: {youtube_link}'"
    }}
  ]
}}
Order clips strongest first. Captions must follow the voice and caption rules. Never \
put words in the preacher's mouth: hooks and captions describe what he actually said."""


def select_moments(sentences, meta, service_date):
    import anthropic
    strategy = open(STRATEGY_FILE).read()
    transcript = "\n".join(
        f"[{i}] ({fmt_t(s['start'])}-{fmt_t(s['end'])}) {s['text']}" for i, s in enumerate(sentences))
    yt = meta.get("youtube_id")
    prompt = PROMPT.format(
        strategy=strategy, title=meta.get("title") or "Sunday sermon",
        service_date=service_date.isoformat(), preacher=meta.get("preacher", "Peter Frey"),
        transcript=transcript, n_min=2, n_max=3, min_len=MIN_LEN, max_len=MAX_LEN,
        youtube_link=f"https://youtu.be/{yt}" if yt else "link in bio")
    client = anthropic.Anthropic()
    msg = client.messages.create(model=MODEL, max_tokens=8000,
                                 messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return parse_plan(text)


def parse_plan(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("model returned no JSON")
    return json.loads(m.group(0))


def resolve_clips(plan, sentences, words):
    """Turn sentence ranges into padded times; drop or trim anything out of bounds."""
    clips, taken = [], []
    for c in plan.get("clips", []):
        a, b = int(c["start_sentence"]), int(c["end_sentence"])
        if not (0 <= a <= b < len(sentences)):
            continue
        def cut(a, b):                               # first and last word of the reel
            wa, wb = sentences[a]["a"], sentences[b]["b"]
            if c.get("start_word") is not None:      # hand-set cut points (trim_clip)
                wa = min(max(int(c["start_word"]), sentences[a]["a"]), sentences[a]["b"])
            if c.get("end_word") is not None and b == int(c["end_sentence"]):
                wb = min(max(int(c["end_word"]), sentences[b]["a"]), sentences[b]["b"])
            return wa, wb
        # trim trailing sentences until under the cap
        while b > a and words[cut(a, b)[1]]["e"] - words[cut(a, b)[0]]["s"] > MAX_LEN:
            b -= 1
        wa, wb = cut(a, b)
        start = max(0.0, words[wa]["s"] - 0.15)
        end = words[wb]["e"] + 0.35
        if end - start < MIN_LEN * 0.75 or end - start > MAX_LEN + 1:
            log(f"  skip clip {a}-{b}: {end - start:.1f}s")
            continue
        if any(start < t_end and end > t_start for t_start, t_end in taken):
            log(f"  skip clip {a}-{b}: overlaps")
            continue
        taken.append((start, end))
        c.update({"start_sentence": a, "end_sentence": b, "start": round(start, 2),
                  "end": round(end, 2),
                  "words": [w for w in words[wa:wb + 1]],
                  "text": " ".join(w["w"] for w in words[wa:wb + 1]),
                  "sentence_starts": [max(start, sentences[k]["start"]) for k in range(a, b + 1)]})
        clips.append(c)
    return clips


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def caption_chunks(words, max_words=4, max_chars=18):
    """2-4 word chunks, broken at punctuation and pauses."""
    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        text = " ".join(x["w"] for x in cur)
        nxt = words[i + 1] if i + 1 < len(words) else None
        brk = (len(cur) >= max_words or len(text) >= max_chars
               or re.search(r"[.,?!;:]$", w["w"])
               or (nxt and nxt["s"] - w["e"] > 0.45))
        if brk or nxt is None:
            chunks.append(cur)
            cur = []
    return chunks


def face_positions(video_path, start, end, samples=12):
    """Horizontal face positions (0..1) sampled across the clip."""
    try:
        import cv2
    except ImportError:
        return []
    if not hasattr(cv2, "CascadeClassifier"):
        return []
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    profile = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")
    cap = cv2.VideoCapture(video_path)
    xs = []
    for k in range(samples):
        t = start + (end - start) * (k + 0.5) / samples
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        scale = 1080 / h if h > 1080 else 1.0
        if scale != 1.0:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
            h, w = frame.shape[:2]
        gray = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        # the speaker is on stage: ignore the bottom third (audience heads)
        stage = gray[: int(h * 0.66)]
        m = max(18, h // 45)
        faces = list(cascade.detectMultiScale(stage, 1.08, 5, minSize=(m, m)))
        if not faces:
            faces = list(profile.detectMultiScale(stage, 1.08, 5, minSize=(m, m)))
        if faces:
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            xs.append((fx + fw / 2) / w)
    cap.release()
    return xs


def choose_framing(video_path, start, end):
    """(layout, crop centre). Falls back to the framed layout when the speaker
    moves too far for one fixed vertical crop to keep him in shot."""
    xs = sorted(face_positions(video_path, start, end))
    if len(xs) < 3:
        return "fill", 0.5
    med = xs[len(xs) // 2]
    near = [x for x in xs if abs(x - med) <= 0.2]      # drop stray detections
    if len(near) < 3:
        return "fill", 0.5
    lo, hi = near[0], near[-1]
    if hi - lo > 0.26:                 # a vertical crop spans ~0.32 of a 16:9 frame
        return "framed", 0.5
    return "fill", (lo + hi) / 2


STYLES = ("editorial", "bold")


def styles_to_render(brand):
    """SOCIAL_STYLE (workflow input) or brand.json "style": editorial | bold | both."""
    want = (os.environ.get("SOCIAL_STYLE") or brand.get("style") or "both").strip().lower()
    if want == "both":
        return list(STYLES)
    if want not in STYLES:
        raise SystemExit(f"style must be editorial, bold or both, got {want!r}")
    return [want]


def render_clip(video_path, clip, brand, out_path, workdir, layout, style="editorial"):
    import reel_design as rd
    dur = clip["end"] - clip["start"]
    ink = brand["ink"].lstrip("#")
    if layout == "fill":
        vertical = os.path.join(workdir, f"clip{clip['n']}_vertical.mp4")
        if not os.path.exists(vertical):            # shared by both styles
            import reframe
            sheet = os.path.join(os.path.dirname(out_path), f"camera check reel {clip['n']}.jpg")
            stats = reframe.render_vertical(video_path, clip["start"], dur, vertical,
                                            sheet_path=sheet)
            clip["reframe"] = stats
            clip["crop_center"] = stats.get("mean_cx", 0.5)
            clip["head_top"] = stats.get("head_top")       # captions may sit above the head
            log(f"    camera: {stats}")
        src, src_ss, base = vertical, 0.0, "null"
    else:
        src, src_ss = video_path, clip["start"]
        base = (f"scale={W}:-2:flags=lanczos,"
                f"pad={W}:{H}:0:(oh-ih)/2+150:0x{ink}")
    clip["layout"] = layout
    if "_grade" not in clip:                        # shared by both looks
        import grade
        clip["_grade"], clip["grade"] = grade.grade_filter(src, src_ss, dur, workdir,
                                                           f"clip{clip['n']}_grade")
        log(f"    colour: {clip['grade']}")
    if "_audio" not in clip:                        # hiss out before the loudness step
        import audio
        clip["_audio"], clip["audio"] = audio.filters(video_path, clip["start"], dur)
        log(f"    audio: {clip['audio']}")
    if clip["_grade"]:                              # before the pad: the ink frame stays ink
        base = f"{clip['_grade']},{base}"

    overlay = rd.make_overlay(clip, brand, layout, FONTS_DIR,
                              os.path.join(workdir, f"clip{clip['n']}_{style}_overlay.png"), style)
    chunks = (caption_chunks(clip["words"], max_words=3, max_chars=16) if style == "editorial"
              else caption_chunks(clip["words"]))
    captions = rd.caption_track(clip, chunks, brand, FONTS_DIR, layout, workdir, style)
    graph = (f"[0:v]{base},setsar=1,fps=30[v];"
             f"[1:v]format=rgba,fade=t=in:st=0:d=0.45:alpha=1[o];"
             f"[v][o]overlay=0:0[vo];"
             f"[2:v]format=rgba,fps=30[c];"
             f"[vo][c]overlay=0:0:eof_action=repeat,trim=duration={dur:.3f},"
             f"format=yuv420p[out]")
    main = os.path.join(workdir, f"clip{clip['n']}_{style}_main.mp4")
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-ss", f"{src_ss:.3f}", "-t", f"{dur:.3f}", "-i", src,
        "-loop", "1", "-t", f"{dur:.3f}", "-i", overlay,
        "-f", "concat", "-safe", "0", "-i", captions,
        "-ss", f"{clip['start']:.3f}", "-t", f"{dur:.3f}", "-i", video_path,
        "-filter_complex", graph, "-map", "[out]", "-map", "3:a:0",
        "-af", ",".join(f for f in (clip["_audio"], "loudnorm=I=-14:TP=-1.5:LRA=11",
                                     "aresample=48000") if f),
        "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-profile:v", "high",
        "-tune", "film", "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest", main],
        check=True)

    card = os.path.join(workdir, f"endcard_{style}.mp4")
    if not os.path.exists(card):
        render_end_card(brand, card, workdir, style)

    lst = os.path.join(workdir, f"clip{clip['n']}_{style}.txt")
    with open(lst, "w") as f:
        f.write(f"file '{main}'\nfile '{card}'\n")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                    "-c", "copy", "-movflags", "+faststart", out_path], check=True)
    os.remove(main)


def render_end_card(brand, out_path, workdir, style="editorial"):
    import reel_design as rd
    png = rd.make_end_card(brand, FONTS_DIR, os.path.join(workdir, f"endcard_{style}.png"), style)
    ink = brand["ink"].lstrip("#")
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-loop", "1", "-framerate", "30", "-t", f"{END_CARD_SECONDS}", "-i", png,
        "-f", "lavfi", "-t", f"{END_CARD_SECONDS}", "-i", "anullsrc=r=48000:cl=stereo",
        "-vf", f"fade=t=in:st=0:d=0.35:color=0x{ink},format=yuv420p",
        "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-profile:v", "high",
        "-tune", "film", "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest", out_path],
        check=True)


# --------------------------------------------------------------------------
# content plan
# --------------------------------------------------------------------------

def plan_html(plan, clips, meta, service_date):
    def esc(s):
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
    rows = []
    rows.append(f"<h1>Social plan: {esc(meta.get('title') or 'Sermon')} ({service_date:%b %-d, %Y})</h1>")
    rows.append(f"<p><b>Big idea:</b> {esc(plan.get('sermon_big_idea', ''))}</p>")
    rows.append("<p>Drafts only, and options rather than a schedule: post whichever reels "
                "you like, whenever suits, with the copy given for each. Each reel comes in two "
                "looks: (editorial) calm captions, and (bold) hook banner and brush stroke. Post "
                "whichever fits the moment; delete the other.</p>")
    rows.append("<table border='1' cellpadding='6'><tr><th>Option</th><th>File</th><th>Hook</th>"
                "<th>Length</th><th>Kind</th><th>Why</th></tr>")
    for c in clips:
        rows.append(f"<tr><td>{c.get('n', '')}</td><td>{esc(' / '.join(c.get('files', {}).values()) or c['file'])}</td>"
                    f"<td>{esc(c['hook'])}</td><td>{c['end'] - c['start']:.0f}s</td>"
                    f"<td>{esc(c.get('kind'))}</td><td>{esc(c.get('why'))}</td></tr>")
    rows.append("</table>")
    for c in clips:
        rows.append(f"<h2>Option {c.get('n', '')}: {esc(c['hook'])}</h2>")
        rows.append(f"<p><i>{esc(' / '.join(c.get('files', {}).values()) or c['file'])} · {fmt_t(c['start'])}–{fmt_t(c['end'])} in the sermon"
                    f"{' · ' + esc(c['scripture']) if c.get('scripture') else ''}</i></p>")
        rows.append(f"<p><b>Instagram</b><br>{esc(c.get('instagram_caption'))}</p>")
        rows.append(f"<p><b>Facebook</b><br>{esc(c.get('facebook_caption'))}</p>")
        rows.append(f"<p><b>YouTube Shorts title</b><br>{esc(c.get('youtube_title'))}</p>")
        rows.append(f"<p><b>YouTube description</b><br>{esc(c.get('youtube_description'))}</p>")
        rows.append(f"<p><b>What he says</b><br>{esc(c['text'])}</p>")
    return "<html><body>" + "\n".join(rows) + "</body></html>"


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def extend_clip(plan, sentences, n, target):
    """Lengthen clip n (1-based, as numbered in the drafts) toward target seconds:
    add the following sentences while they fit, end on one that finishes a
    thought, never run into another clip; grow backward if forward is blocked.
    Returns (old seconds, new seconds) or None."""
    clips = plan.get("clips", [])
    if not 1 <= n <= len(clips):
        return None
    target = min(float(target), MAX_LEN)
    c = clips[n - 1]
    a, b = int(c["start_sentence"]), int(c["end_sentence"])
    others = [(int(o["start_sentence"]), int(o["end_sentence"]))
              for i, o in enumerate(clips) if i != n - 1]

    def free(k):
        return 0 <= k < len(sentences) and all(not lo - 1 <= k <= hi + 1 for lo, hi in others)

    def span(x, y):
        return sentences[y]["end"] - sentences[x]["start"] + 0.5

    old = span(a, b)
    end = b
    while free(end + 1) and span(a, end + 1) <= target:
        end += 1
    while end > b and not re.search(r"[.?!][\"'”’)]*$", sentences[end]["text"].strip()):
        end -= 1                                        # finish on a whole thought
    start = a
    if span(start, end) < target - 8:                   # still short: begin a little earlier
        while free(start - 1) and span(start - 1, end) <= target:
            start -= 1
    c["start_sentence"], c["end_sentence"] = start, end
    return round(old, 1), round(span(start, end), 1)


def _find_phrase(words, phrase, near):
    """(first, last) word index of phrase in words, the occurrence nearest `near` seconds."""
    want = re.findall(r"[a-z0-9']+", phrase.lower())
    norm = [re.sub(r"[^a-z0-9']", "", w["w"].lower()) for w in words]
    hits = [i for i in range(len(words) - len(want) + 1) if want and norm[i:i + len(want)] == want]
    if not hits:
        return None
    i = min(hits, key=lambda i: abs(words[i]["s"] - near))
    return i, i + len(want) - 1


def trim_clip(plan, sentences, words, n, start_at="", end_at=""):
    """Set reel n to begin at the words start_at and/or end after the words end_at
    (phrases from the sermon, matched nearest the reel). Returns a note for the log."""
    clips = plan.get("clips", [])
    if not 1 <= n <= len(clips):
        return f"no reel {n}"
    c = clips[n - 1]
    near = sentences[int(c["start_sentence"])]["start"]
    sentence_of = lambda j: next(k for k, s in enumerate(sentences) if s["a"] <= j <= s["b"])
    notes = []
    for phrase, key, pick in ((start_at, "start", 0), (end_at, "end", 1)):
        if not phrase:
            continue
        hit = _find_phrase(words, phrase, near)
        if not hit:
            notes.append(f"! {phrase!r} not found")
            continue
        j = hit[pick]
        c[f"{key}_word"], c[f"{key}_sentence"] = j, sentence_of(j)
        notes.append(f"{key} at {words[j]['s']:.1f}s ({phrase!r})")
    return "; ".join(notes)


def reuse_plan(drive, sermon, state, workdir):
    """(plan, words, folder id) from this sermon's latest drafts folder that still
    holds plan.json and transcript_words.json, so a re-render (e.g. after a
    design change) keeps the same clips and replaces the same files. None if
    there is nothing to reuse."""
    for rec in reversed(state):
        if rec.get("drive_file_id") != sermon["id"] or not rec.get("drafts_folder_id"):
            continue
        try:
            files = {f["name"]: f for f in list_folder(drive, rec["drafts_folder_id"])}
        except Exception:
            continue                                    # folder deleted
        if "plan.json" in files and "transcript_words.json" in files:
            paths = {}
            for name in ("plan.json", "transcript_words.json"):
                paths[name] = os.path.join(workdir, "reuse_" + name)
                download(drive, files[name]["id"], paths[name])
            plan = json.load(open(paths["plan.json"]))
            words = json.load(open(paths["transcript_words.json"]))
            for c in plan.get("clips", []):             # render fresh; keep the choice
                for k in ("start", "end", "files", "file", "drive_id", "drive_ids", "reframe",
                          "grade", "audio", "crop_center", "head_top", "text", "sentence_starts"):
                    c.pop(k, None)
            return plan, words, rec["drafts_folder_id"]
    log("  ! nothing to reuse for this sermon; choosing clips afresh")
    return None


def process(drive, sermon, brand, state):
    service_date = sermon["service_date"]
    meta = sermon_meta(service_date)
    log(f"\n=== {sermon['name']} ({service_date})  {meta.get('title', '')}")
    if drive is not None:
        try:
            fetch_fonts(drive)
        except Exception as e:                      # never lose a week over a font
            log(f"  ! could not fetch brand fonts: {e}")
    brand = dict(brand, font_family=resolve_font(brand))
    log(f"  font: {brand['font_family']}")
    stamp = service_date.isoformat()

    with tempfile.TemporaryDirectory() as tmp:
        if os.environ.get("LOCAL_VIDEO"):
            video = os.environ["LOCAL_VIDEO"]
        else:
            ext = os.path.splitext(sermon["name"])[1] or ".mp4"
            video = os.path.join(tmp, "sermon" + ext)
            log("  downloading…")
            download(drive, sermon["id"], video)

        reused = (reuse_plan(drive, sermon, state, tmp)
                  if drive is not None and os.environ.get("SOCIAL_REUSE_PLAN") == "1" else None)
        if reused:                                      # same clips, re-rendered
            plan, words, reuse_folder = reused
            log("  re-rendering last run's clips (plan and word timings from Drive)")
        elif os.environ.get("SOCIAL_WORDS_JSON"):       # testing: skip transcription
            words = json.load(open(os.environ["SOCIAL_WORDS_JSON"]))
        else:
            words = transcribe(video, tmp)
        sentences = sentences_from_words(words)

        if reused:
            if os.environ.get("SOCIAL_EXTEND_CLIP") and (os.environ.get("SOCIAL_START_AT")
                                                         or os.environ.get("SOCIAL_END_AT")):
                n = int(os.environ["SOCIAL_EXTEND_CLIP"])
                log(f"  reel {n}: " + trim_clip(plan, sentences, words, n,
                                                 os.environ.get("SOCIAL_START_AT", ""),
                                                 os.environ.get("SOCIAL_END_AT", "")))
            elif os.environ.get("SOCIAL_EXTEND_CLIP"):
                n = int(os.environ["SOCIAL_EXTEND_CLIP"])
                grown = extend_clip(plan, sentences, n, float(os.environ.get("SOCIAL_EXTEND_TO", MAX_LEN)))
                log(f"  reel {n} lengthened: {grown[0]}s -> {grown[1]}s" if grown
                    else f"  ! no reel {n} to lengthen")
        elif os.environ.get("SOCIAL_PLAN_JSON"):        # testing: skip the model call
            plan = json.load(open(os.environ["SOCIAL_PLAN_JSON"]))
        else:
            plan = select_moments(sentences, meta, service_date)
        clips = resolve_clips(plan, sentences, words)
        log(f"  {len(clips)} clips selected")

        out_dir = os.path.join(tmp, "out")
        os.makedirs(out_dir)
        styles = styles_to_render(brand)
        for n, c in enumerate(clips, 1):
            c["n"] = n
            slug = re.sub(r"[^a-z0-9]+", "-", c["hook"].lower()).strip("-")[:40]
            c["files"] = {}
            for style in styles:
                name = f"{stamp} reel {n} - {slug}" + (f" ({style})" if len(styles) > 1 else "") + ".mp4"
                log(f"  rendering {n}/{len(clips)} {style}: {c['hook']} ({c['end'] - c['start']:.0f}s)")
                render_clip(video, c, brand, os.path.join(out_dir, name), tmp,
                            c.get("layout") or LAYOUT, style)
                c["files"][style] = name
            c["file"] = next(iter(c["files"].values()))
            c.pop("_framed_once", None)

        # Final Cut handoff: full-quality projects + captions for finishing
        import fcp_export
        fcp_dir = os.path.join(out_dir, "Final Cut")
        os.makedirs(fcp_dir)
        sw, sh, sfps, sdur = probe_video(video)
        for c in clips:
            srt_name = re.sub(r" \((editorial|bold)\)", "", c["file"]).replace(".mp4", ".srt")
            fcp_export.write_srt(c, caption_chunks(c["words"]), os.path.join(fcp_dir, srt_name))
        with open(os.path.join(fcp_dir, f"{stamp} reels.fcpxml"), "w", encoding="utf-8") as f:
            f.write(fcp_export.build_fcpxml(
                sermon["name"], fcp_export.media_url_for(sermon["name"]), sdur, sfps, sw, sh,
                clips, brand, brand["font_family"], caption_chunks,
                f"Reels {stamp} {meta.get('title') or ''}".strip()))

        public = [{k: v for k, v in c.items() if k != "words" and not k.startswith("_")}
                  for c in clips]
        plan_json = {"service_date": stamp, "title": meta.get("title"),
                     "youtube_id": meta.get("youtube_id"),
                     "sermon_big_idea": plan.get("sermon_big_idea"), "clips": public,
                     "generated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        with open(os.path.join(out_dir, "plan.json"), "w") as f:
            json.dump(plan_json, f, indent=2, ensure_ascii=False)
        with open(os.path.join(out_dir, "plan.html"), "w") as f:
            f.write(plan_html(plan, clips, meta, service_date))
        with open(os.path.join(out_dir, "transcript_words.json"), "w") as f:
            json.dump(words, f)

        if DRY_RUN or os.environ.get("LOCAL_OUT"):
            dest = os.environ.get("LOCAL_OUT", os.path.join(HERE, "social_out"))
            os.makedirs(dest, exist_ok=True)
            import shutil
            for name in os.listdir(out_dir):
                target = os.path.join(dest, name)
                if os.path.isdir(target):
                    shutil.rmtree(target)
                shutil.move(os.path.join(out_dir, name), target)
            log(f"  [local] wrote {dest}")
            return None

        # Rendering takes most of an hour; Google drops the idle connection by then
        # (ssl.SSLEOFError on the next request), so start the uploads on a fresh one.
        drive = drive_service()
        parent = os.environ.get("SOCIAL_DRAFTS_FOLDER_ID")
        if not parent:
            sermons_parent = drive.files().get(
                fileId=os.environ.get("SERMON_FOLDER_ID", "1SCMlaqWua24gPPU-Z7yF19pisehp3xu_"),
                fields="parents", supportsAllDrives=True).execute(num_retries=DRIVE_RETRIES)["parents"][0]
            parent = ensure_folder(drive, "Social Drafts", sermons_parent)
        folder_name = f"{stamp} {meta.get('title') or 'Sermon'}"[:120]
        folder = reused[2] if reused else ensure_folder(drive, folder_name, parent)
        for c in clips:
            c["drive_ids"] = {}
            for style, name in c["files"].items():
                up = upload(drive, os.path.join(out_dir, name), name, folder, "video/mp4")
                c["drive_ids"][style] = up["id"]
                log(f"  uploaded {name}")
            c["drive_id"] = next(iter(c["drive_ids"].values()))
        doc = upload(drive, os.path.join(out_dir, "plan.html"), f"Social plan {stamp}", folder,
                     "text/html", as_google_doc=True)
        for c, p in zip(clips, public):
            p["drive_id"] = c["drive_id"]
            p["drive_ids"] = c["drive_ids"]
        with open(os.path.join(out_dir, "plan.json"), "w") as f:
            json.dump(plan_json, f, indent=2, ensure_ascii=False)
        upload(drive, os.path.join(out_dir, "plan.json"), "plan.json", folder, "application/json")
        upload(drive, os.path.join(out_dir, "transcript_words.json"), "transcript_words.json",
               folder, "application/json")
        fcp_folder = ensure_folder(drive, "Final Cut", folder)
        for name in sorted(os.listdir(fcp_dir)):
            mime = "application/xml" if name.endswith(".fcpxml") else "application/x-subrip"
            upload(drive, os.path.join(fcp_dir, name), name, fcp_folder, mime)
        for name in sorted(os.listdir(out_dir)):
            if name.startswith("camera check"):
                upload(drive, os.path.join(out_dir, name), name, folder, "image/jpeg")
        log(f"  plan: {doc.get('webViewLink')}")

    state.append({"drive_file_id": sermon["id"], "drive_file_name": sermon["name"],
                  "service_date": stamp, "drafts_folder_id": folder, "clips": len(clips),
                  "plan_doc": doc.get("webViewLink")})
    save_state(state)
    return folder


def main():
    brand = load_brand()
    state = load_state()
    if os.environ.get("LOCAL_VIDEO"):
        from upload_sermon import parse_date_from_filename
        name = os.path.basename(os.environ["LOCAL_VIDEO"])
        d = parse_date_from_filename(name) or date.today()
        process(None, {"id": "local", "name": name, "service_date": d}, brand, state)
        return 0

    drive = drive_service()
    todo = find_new_sermons(drive, state)
    if not todo:
        log("No new sermons for social clips.")
        return 0
    # one per run keeps each job well inside the runner's time limit
    process(drive, todo[-1], brand, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
