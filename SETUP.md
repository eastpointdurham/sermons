# Sermon pipeline setup

Four GitHub Actions workflows run the pipeline. Each Sunday recording in the Drive
"Sermons" folder becomes:

1. **Upload Sermons to YouTube** (`upload_sermon.py`): a *private* YouTube draft and a
   podcast MP3 in Drive. Nothing is published.
2. **Update Sermon Archive** (`build_site.py`, hourly): the draft is transcribed and a
   transcript doc is filed. Its YouTube description is rewritten from the transcript.
3. **Make Social Reels** (`social_clips.py`): 2 to 3 draft vertical reels, each in two looks, plus a
   content plan, saved to Drive › Social Drafts › `<date> <title>`. Nothing is posted.

## One-time setup

### 1. Pushing from this Mac

GitHub no longer accepts account passwords for `git push`. Create a token once at
GitHub › Settings › Developer settings › Personal access tokens › Tokens (classic) ›
Generate new token, with the **repo** and **workflow** scopes. Paste it at the
`Password:` prompt; macOS Keychain remembers it.

### 2. Mint the two refresh tokens (on your Mac, signed in as the channel owner)

```
pip3 install google-auth-oauthlib
python3 get_refresh_token.py drive     # -> GOOGLE_DRIVE_REFRESH_TOKEN
python3 get_refresh_token.py youtube   # -> GOOGLE_YOUTUBE_REFRESH_TOKEN
```

Add both at GitHub › eastpointdurham/sermons › Settings › Secrets and variables ›
Actions › **New repository secret**. `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and
`ANTHROPIC_API_KEY` are already there.

### 3. Optional repository variables (same page, Variables tab)

| Variable | Default | What it does |
| --- | --- | --- |
| `UPLOAD_SINCE` | `2026-10-04` | Uploader ignores recordings dated earlier, so sermons already on YouTube are not duplicated |
| `SOCIAL_DRAFTS_FOLDER_ID` | a "Social Drafts" folder created next to the Sermons folder | Where draft reels land |

### 4. First runs

- Actions › **Upload Sermons to YouTube** › Run workflow with `dry_run = true`. Check
  the printed title and description.
- Actions › **Make Social Reels** › Run workflow (leave inputs blank) to make reels
  for the most recent sermon, or paste a Drive file id to redo a specific one.

## What each Sunday produces

Drive › Social Drafts › `<date> <title>`:
- `<date> reel N - <hook>.mp4`: finished drafts, 1080×1920, in the brand look (ink
  scrims, sage sunburst, Oakes Grotesk hook with a sage brush underline, live captions
  with the spoken word on a sage highlight, ink end card). Encoded high (CRF 16) so
  Instagram's recompression has the most to work with.
- `Social plan <date>`: each reel as an option, with ready-to-use copy for Instagram,
  Facebook and YouTube Shorts. No posting schedule: post whichever fit, whenever suits.
- `Final Cut/<date> reels.fcpxml`: one vertical project per reel, cut from the original
  recording at full quality, with the crop, hook, scripture and caption titles and
  sentence markers. Import it (File › Import › XML), point Final Cut at the sermon file
  when asked (File › Relink Files), polish, and export. `.srt` caption files sit
  alongside for YouTube or Facebook.

## Tuning the reels

- `social/strategy.md` is what the clip picker reads. Keep it in step with the
  strategy doc.
- `social/brand.json` holds the colours and the end-card text; `reel_design.py` draws
  every graphic.
- Brand fonts: the Oakes Grotesk files are downloaded at run time from the private
  Drive font folder (never committed; this repo is public). Point the
  `SOCIAL_FONTS_FOLDER_ID` variable elsewhere if the folder moves.
- Looks: `"style"` in `social/brand.json` is `both` (every reel rendered as
  "(editorial)" and "(bold)"; post whichever fits), or `editorial` / `bold` for one.
- Camera: `reframe.py` works like a camera operator. It follows the speaker's face,
  holds still for small moves, glides when he walks, and zooms in only as far as the
  source allows (`SOCIAL_MAX_UPSCALE`, default 2.1x for 1080p recordings). Run the
  workflow with `layout = framed` to show the full wide shot instead.
- Colour: `grade.py` measures each clip once (the speaker's face, neutral surfaces,
  the brightness spread) and applies one LUT: white balance anchored on neutrals and
  the face (hue only, so skin tones are never lightened or darkened), cleaner blacks,
  a capped exposure lift, a soft filmic curve and skin-protected vibrance. Set the
  `SOCIAL_GRADE` environment variable to `off` to skip it.
- Audio: `audio.py` measures each clip's noise floor, then removes rumble (75 Hz
  high-pass) and hiss (an FFT denoiser set from that floor) and softens the pauses,
  before the loudness step, so normalising no longer swells the hiss between
  sentences. The podcast MP3 gets the same clean-up plus podcast loudness (-16 LUFS).
  Set `AUDIO_CLEAN` to `off` to skip it. The YouTube upload is the original file.
- Re-rendering a week after a design change: Actions › **Make Social Reels** › Run
  workflow with the sermon's Drive file id and `reuse_plan = true`. The same clips are
  rebuilt and replace the files in the same folder (Drive keeps the old versions).
- Sharpest results come from 4K recordings: the vertical crop then needs no enlarging.

## Thumbnails

On (the `AUTO_THUMBNAILS` repository variable is `1`; set it to anything else to
pause). `thumbnail.py` makes each draft's 1280×720 thumbnail, led by the series art
and in its colours: a full-bleed photo shaded into the series' ground on the left,
the series lockup top left, the sermon title and scripture bottom left. The photo
is the week's preacher; two community versions are saved next to it in Drive ›
Thumbnails as "(community option 1/2)", ready to swap in by hand in YouTube Studio.

It reads three Drive folders next to "Sermons":

- **Series Graphics**: when a series starts, save its key art from Canva
  (Share › Google Drive), named after the series (e.g. `ALL IN.png`).
- **Thumbnail Photos**: `Peter Frey` (one folder per preacher, named as in the
  planning doc) and `Community`. Only photos in these folders are ever used, so put
  in approved shots only: people who are happy to be on YouTube, no children as the
  focus. A guest with no folder gets a community photo.
- **Thumbnails**: a copy of every thumbnail made.

YouTube only accepts custom thumbnails from a phone-verified channel. To remake one:
Actions › **Make Sermon Thumbnail** › Run workflow with the Sunday's date.

## Tests

```
python3 test_upload_sermon.py
python3 test_privacy_handling.py
python3 test_enrich_descriptions.py
python3 test_social_clips.py
```
