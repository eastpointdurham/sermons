"""
Virtual camera operator: turns a wide 16:9 stage shot into a vertical 9:16 shot
that follows the speaker.

Instead of one fixed crop for the whole clip, it
  1. finds the speaker's face a few times a second (ignoring the audience in the
     bottom third of the frame),
  2. plans a smooth camera path with a dead zone, so the frame holds still while
     he gestures and glides when he walks, the way a camera operator would,
  3. zooms in only as far as the source resolution allows (a 1080p source is never
     enlarged more than MAX_UPSCALE; a 4K source gets a much tighter shot),
  4. writes a near-lossless vertical intermediate for the rest of the pipeline.
"""

import os
import subprocess

import numpy as np

OUT_W, OUT_H = 1080, 1920
MAX_UPSCALE = float(os.environ.get("SOCIAL_MAX_UPSCALE", "2.1"))
HEAD_ROOM = 0.30          # face centre sits this far down the frame
DEAD_ZONE = 0.07          # of source width: small moves don't move the camera


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout.strip().split(",")
    w, h = int(out[0]), int(out[1])
    n, d = out[2].split("/")
    return w, h, float(n) / float(d)


def _frames(path, start, dur, w, h, fps):
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", path,
           "-an", "-vf", f"fps={fps}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=w * h * 3 * 4)
    size = w * h * 3
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        p.stdout.close()
        p.wait()


YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")
YUNET_PATH = os.path.join(os.path.expanduser("~"), ".cache", "eastpoint",
                          "face_detection_yunet_2023mar.onnx")
DETECT_W = 960            # detection runs on a 960-wide copy of the frame
STAGE = 0.75              # ignore faces below this line (the audience)


def _load_detector():
    """OpenCV's YuNet face detector (finds turned and small faces, few false hits);
    the old Haar cascades if the model can't be fetched."""
    import cv2
    try:
        if not os.path.exists(YUNET_PATH):
            import urllib.request
            os.makedirs(os.path.dirname(YUNET_PATH), exist_ok=True)
            urllib.request.urlretrieve(YUNET_URL, YUNET_PATH)
        return ("yunet", cv2.FaceDetectorYN.create(YUNET_PATH, "", (320, 320), 0.6, 0.3, 50))
    except Exception as e:
        print(f"    ! YuNet unavailable ({e}); using Haar cascades", flush=True)
    try:
        return ("haar", [cv2.CascadeClassifier(cv2.data.haarcascades + n) for n in
                         ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml")])
    except AttributeError:          # OpenCV 5 dropped Haar cascades; requirements pin <5
        return ("none", None)


def _detect(frame, detector):
    """Every face on stage: [(cx, cy, height, score)] as fractions of the frame."""
    import cv2
    kind, det = detector
    h, w = frame.shape[:2]
    scale = DETECT_W / w
    small = cv2.resize(frame, (DETECT_W, int(h * scale)))
    sh = small.shape[0]
    out = []
    if kind == "yunet":
        det.setInputSize((DETECT_W, sh))
        _, faces = det.detect(small)
        for f in (faces if faces is not None else []):
            x, y, fw, fh, score = f[0], f[1], f[2], f[3], f[-1]
            out.append(((x + fw / 2) / DETECT_W, (y + fh / 2) / sh, fh / sh, float(score)))
    else:
        gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        m = max(14, sh // 50)
        for c in det:
            for x, y, fw, fh in c.detectMultiScale(gray, 1.08, 5, minSize=(m, m)):
                out.append(((x + fw / 2) / DETECT_W, (y + fh / 2) / sh, fh / sh, 0.5))
    return [c for c in out if c[1] < STAGE]


def pick_speaker(samples, max_gap=10):
    """Link detections into tracks and return the speaker's: the person seen in
    the most samples, joined across gaps where he turned away or walked.

    samples: [(frame_index, [(cx, cy, h, score), ...]), ...] in time order.
    Returns [(frame_index, (cx, cy, h))] for the chosen person only.
    """
    tracks = []                                     # each: list of (k, i, cand)
    for k, (i, cands) in enumerate(samples):
        taken = set()
        for cand in sorted(cands, key=lambda c: -c[3]):
            best, best_d = None, None
            for t in tracks:
                lk, _, last = t[-1]
                gap = k - lk
                if gap < 1 or gap > max_gap or id(t) in taken:
                    continue
                dx, dy = abs(cand[0] - last[0]), abs(cand[1] - last[1])
                if dx < 0.03 + 0.02 * gap and dy < 0.08 and 0.6 < cand[2] / last[2] < 1.6:
                    if best_d is None or dx < best_d:
                        best, best_d = t, dx
            if best is None:
                best = []
                tracks.append(best)
            best.append((k, i, cand))
            taken.add(id(best))
    if not tracks:
        return []

    def weight(t):
        return sum(c[3] for _, _, c in t)

    main = max(tracks, key=weight)
    chosen = sorted(main)
    # stitch on tracks that pick up where the speaker's left off (he walked
    # faster than the gate, or was hidden for a few seconds)
    for t in sorted((t for t in tracks if t is not main), key=weight, reverse=True):
        if len(t) < 3:
            continue
        ks = {k for k, _, _ in chosen}
        if any(k in ks for k, _, _ in t):
            continue
        before = [c for c in chosen if c[0] < t[0][0]]
        after = [c for c in chosen if c[0] > t[-1][0]]
        near = ((before and abs(before[-1][2][0] - t[0][2][0]) < 0.2) or
                (after and abs(after[0][2][0] - t[-1][2][0]) < 0.2))
        if near:
            chosen = sorted(chosen + t)
    return [(i, c[:3]) for _, i, c in chosen]


def _smooth(vals, radius):
    if radius < 1:
        return vals
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / (radius / 2.0)) ** 2)
    k /= k.sum()
    pad = np.pad(vals, radius, mode="edge")
    return np.convolve(pad, k, mode="valid")


def plan_path(video, start, dur):
    """Per-frame (cx, cy, crop_h) in source pixels, plus stats for the log."""
    import cv2
    w, h, fps = _probe(video)
    fps = 30.0
    detector = _load_detector()
    if detector[0] == "none":
        print("    ! no face detector available; using a centred crop", flush=True)
        return None, {"detections": 0, "no_detector": True}
    step = 6                                       # detect 5x a second
    n_frames = int(round(dur * fps))
    samples = [(i, _detect(fr, detector))
               for i, fr in enumerate(_frames(video, start, dur, w, h, fps)) if i % step == 0]
    track = pick_speaker(samples)
    obs_t = [i for i, _ in track]
    obs = [c for _, c in track]
    seen = sum(1 for _, c in samples if c)
    if len(obs) < 3:
        return None, {"detections": len(obs), "samples": len(samples), "detector": detector[0]}

    t = np.arange(n_frames)
    xs = np.interp(t, obs_t, [o[0] for o in obs])
    ys = np.interp(t, obs_t, [o[1] for o in obs])
    fh = np.median([o[2] for o in obs])

    # zoom: face about 1/12 of frame height, but never enlarge past MAX_UPSCALE
    crop_h_frac = min(1.0, max(OUT_H / MAX_UPSCALE / h, fh * 12))
    crop_h = crop_h_frac * h
    crop_w = crop_h * 9 / 16

    # camera operator on x: hold still while he stays inside the dead zone; once
    # he leaves it, pan until he is centred again (not just back to its edge,
    # which leaves a walking speaker trailing at the side of the frame)
    xs = _smooth(xs, int(fps * 0.3))                         # detection jitter
    cam = np.empty(n_frames)
    c, moving = xs[0], False
    for i in range(n_frames):
        off = xs[i] - c
        if abs(off) > DEAD_ZONE:
            moving = True
        elif abs(off) < 0.01:
            moving = False
        if moving:
            c += off * 0.08                                  # ease toward him
        cam[i] = c
    cam = _smooth(cam, int(fps * 0.4))
    y_cam = _smooth(ys, int(fps * 1.5))

    cx = np.clip(cam * w, crop_w / 2, w - crop_w / 2)
    cy = np.clip(y_cam * h + crop_h * (0.5 - HEAD_ROOM), crop_h / 2, h - crop_h / 2)
    stats = {"detector": detector[0], "samples": len(samples), "with_faces": seen,
             "detections": len(obs), "crop_h_frac": round(float(crop_h_frac), 3),
             "upscale": round(OUT_H / crop_h, 2), "x_range": round(float(np.ptp(cam)), 3),
             "mean_cx": round(float(np.mean(cx)) / w, 3)}
    stats["_samples"], stats["_track"] = samples, track     # for the camera check sheet
    return (cx, cy, crop_h), stats


def camera_sheet(video, start, dur, samples, track, cx, cy, crop_h, out_path, n=8):
    """A contact sheet of the camera's decisions: every face found (grey), the
    one it followed (sage), and the vertical frame it cut (white)."""
    import cv2
    w, h, fps = _probe(video)
    fps = 30.0
    n_frames = len(cx)
    picks = [int((j + 0.5) * n_frames / n) for j in range(n)]
    chosen = dict(track)
    tiles = []
    for i, fr in enumerate(_frames(video, start, dur, w, h, fps)):
        if i not in picks:
            continue
        img = fr.copy()
        near = min(samples, key=lambda s: abs(s[0] - i)) if samples else (i, [])
        for c in near[1]:
            bh = c[2] * h
            x0, y0 = int(c[0] * w - bh / 2), int(c[1] * h - bh / 2)
            cv2.rectangle(img, (x0, y0), (x0 + int(bh), y0 + int(bh)), (160, 160, 160), 3)
        if near[0] in chosen:
            c = chosen[near[0]]
            cv2.circle(img, (int(c[0] * w), int(c[1] * h)), int(c[2] * h * 0.7),
                       (114, 132, 119), 8)
        cw = crop_h * 9 / 16
        cv2.rectangle(img, (int(cx[i] - cw / 2), int(cy[i] - crop_h / 2)),
                      (int(cx[i] + cw / 2), int(cy[i] + crop_h / 2)), (255, 255, 255), 6)
        cv2.putText(img, f"{i / fps:.1f}s", (24, 64), cv2.FONT_HERSHEY_SIMPLEX, 2,
                    (255, 255, 255), 4)
        tiles.append(cv2.resize(img, (480, int(480 * h / w))))
    if not tiles:
        return None
    while len(tiles) % 4:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[r:r + 4]) for r in range(0, len(tiles), 4)]
    cv2.imwrite(out_path, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 82])
    return out_path


def render_vertical(video, start, dur, out_path, fallback_cx=0.5, sheet_path=None):
    """Write the reframed vertical clip (video only). Returns stats. With
    sheet_path, also writes a camera check contact sheet there."""
    import cv2
    w, h, _ = _probe(video)
    path, stats = plan_path(video, start, dur)
    samples, track = stats.pop("_samples", []), stats.pop("_track", [])
    fps = 30.0
    n_frames = int(round(dur * fps))
    if path is None:                               # no face found: centred full-height crop
        crop_h = float(h)
        cx = np.full(n_frames, np.clip(fallback_cx * w, crop_h * 9 / 32, w - crop_h * 9 / 32))
        cy = np.full(n_frames, h / 2)
        stats["fallback"] = True
    else:
        cx, cy, crop_h = path
    crop_w = crop_h * 9 / 16

    enc = subprocess.Popen([
        "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
        "-s", f"{OUT_W}x{OUT_H}", "-r", str(fps), "-i", "-",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "10", "-pix_fmt", "yuv420p", out_path],
        stdin=subprocess.PIPE)
    for i, fr in enumerate(_frames(video, start, dur, w, h, fps)):
        k = min(i, n_frames - 1)
        # sub-pixel crop via an affine warp: smooth pans without 1-px jitter
        sx = OUT_W / crop_w
        m = np.float32([[sx, 0, -(cx[k] - crop_w / 2) * sx],
                        [0, sx, -(cy[k] - crop_h / 2) * sx]])
        out = cv2.warpAffine(fr, m, (OUT_W, OUT_H), flags=cv2.INTER_LANCZOS4,
                             borderMode=cv2.BORDER_REPLICATE)
        if sx > 1.05:                               # restore edge crispness after enlarging
            blur = cv2.GaussianBlur(out, (0, 0), 1.2)
            out = cv2.addWeighted(out, 1.45, blur, -0.45, 0)
        enc.stdin.write(out.tobytes())
    enc.stdin.close()
    enc.wait()
    if enc.returncode:
        raise RuntimeError("reframe encode failed")
    if sheet_path:
        try:
            camera_sheet(video, start, dur, samples, track, cx, cy, crop_h, sheet_path)
        except Exception as e:                      # a debugging aid, never a failure
            print(f"    ! camera sheet skipped: {e}", flush=True)
    return stats
