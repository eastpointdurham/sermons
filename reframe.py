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


def _detect(frame, cascades, prev):
    import cv2
    h, w = frame.shape[:2]
    scale = 960 / w
    small = cv2.resize(frame, (960, int(h * scale)))
    gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
    stage = gray[: int(gray.shape[0] * 0.70)]
    m = max(14, gray.shape[0] // 50)
    found = []
    for c in cascades:
        found += [tuple(f) for f in c.detectMultiScale(stage, 1.08, 5, minSize=(m, m))]
    if not found:
        return None
    cands = [((x + fw / 2) / scale / w, (y + fh / 2) / scale / h, fh / scale / h)
             for x, y, fw, fh in found]
    if prev is not None:               # keep following the same person
        return min(cands, key=lambda c: abs(c[0] - prev[0]) + abs(c[1] - prev[1]))
    return max(cands, key=lambda c: c[2])


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
    cascades = [cv2.CascadeClassifier(cv2.data.haarcascades + n) for n in
                ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml")]
    step = 6                                       # detect 5x a second
    n_frames = int(round(dur * fps))
    obs_t, obs = [], []
    prev = None
    for i, fr in enumerate(_frames(video, start, dur, w, h, fps)):
        if i % step:
            continue
        d = _detect(fr, cascades, prev)
        if d is not None:
            # reject jumps that are really a different face for a single sample
            if prev is None or abs(d[0] - prev[0]) < 0.25:
                obs_t.append(i)
                obs.append(d)
                prev = d
    if len(obs) < 3:
        return None, {"detections": len(obs)}

    t = np.arange(n_frames)
    xs = np.interp(t, obs_t, [o[0] for o in obs])
    ys = np.interp(t, obs_t, [o[1] for o in obs])
    fh = np.median([o[2] for o in obs])

    # zoom: face about 1/12 of frame height, but never enlarge past MAX_UPSCALE
    crop_h_frac = min(1.0, max(OUT_H / MAX_UPSCALE / h, fh * 12))
    crop_h = crop_h_frac * h
    crop_w = crop_h * 9 / 16

    # dead-zone camera on x, then gentle smoothing
    cam = np.empty(n_frames)
    c = xs[0]
    for i in range(n_frames):
        off = xs[i] - c
        if abs(off) > DEAD_ZONE:
            c += (off - np.sign(off) * DEAD_ZONE) * 0.08     # ease toward the target
        cam[i] = c
    cam = _smooth(cam, int(fps * 0.6))
    y_cam = _smooth(ys, int(fps * 1.5))

    cx = np.clip(cam * w, crop_w / 2, w - crop_w / 2)
    cy = np.clip(y_cam * h + crop_h * (0.5 - HEAD_ROOM), crop_h / 2, h - crop_h / 2)
    stats = {"detections": len(obs), "crop_h_frac": round(float(crop_h_frac), 3),
             "upscale": round(OUT_H / crop_h, 2), "x_range": round(float(np.ptp(cam)), 3),
             "mean_cx": round(float(np.mean(cx)) / w, 3)}
    return (cx, cy, crop_h), stats


def render_vertical(video, start, dur, out_path, fallback_cx=0.5):
    """Write the reframed vertical clip (video only). Returns stats."""
    import cv2
    w, h, _ = _probe(video)
    path, stats = plan_path(video, start, dur)
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
    return stats
