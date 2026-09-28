# Sermon pipeline setup

Four GitHub Actions workflows run the pipeline. Each Sunday recording in the Drive
"Sermons" folder becomes:

1. **Upload Sermons to YouTube** (`upload_sermon.py`): a *private* YouTube draft and a
   podcast MP3 in Drive. Nothing is published.
2. **Update Sermon Archive** (`build_site.py`, hourly): the draft is transcribed and a
   transcript doc is filed. Its YouTube description is rewritten from the transcript.
3. **Make Social Reels** (`social_clips.py`): 4 to 6 draft vertical reels plus a
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
- `Social plan <date>`: captions for Instagram, Facebook and YouTube Shorts, post days.
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
- The speaker crop follows Peter's face. When he moves too much for one vertical
  crop, the reel switches to the framed layout (full shot on an ink background).
  Run the workflow with `layout = framed` to force it for a whole sermon.
- Sharpest results come from 4K recordings: the vertical crop then needs no enlarging.

## Tests

```
python3 test_upload_sermon.py
python3 test_privacy_handling.py
python3 test_enrich_descriptions.py
python3 test_social_clips.py
```
