# Eastpoint sermon pipeline — notes for Claude Code

Public repo `eastpointdurham/sermons`. GitHub Actions turn each Sunday recording in the
Drive "Sermons" folder into a private YouTube draft, a podcast MP3, a transcript doc, and
2–3 draft vertical reels. Nothing is ever published or posted automatically. See SETUP.md.

## Workflows
- `upload_sermons.yml` → `upload_sermon.py`: Drive → private YouTube draft + MP3.
  Skips recordings dated before `UPLOAD_SINCE` (repo variable, default 2026-10-04).
- `update_sermons.yml` (hourly) → `build_site.py`, `create_drive_docs.py`,
  `enrich_descriptions.py`: transcripts, archive site, YouTube descriptions.
  Also `/latest` (forwards to the newest public sermon) and `/links` (`links_page.py`:
  link-in-bio page with this week's sermon, upcoming public Church Center events, and
  the buttons in `links.json`). Events need PCO_APP_ID/PCO_SECRET; `events.json` keeps
  only name, times and Church Center link (no locations: public repo).
- `social_clips.yml` → `social_clips.py`: runs after the upload workflow. Whisper
  word timings → Claude picks 2–3 clips using `social/strategy.md` → `reframe.py`
  (virtual camera: face tracking, dead-zone pan, max 2.1x upscale) → `reel_design.py`
  (brand graphics in Pillow) → ffmpeg → Drive "Social Drafts/<date> <title>": one
  version of each reel, bold and cut tight (`reel_fx.py`: pauses, ums and stumbles
  cut, punch-in zooms), with a music bed ducked under the voice (Drive "Reel Music" via SOCIAL_MUSIC_FOLDER_ID first, licensed tracks only,
  else ElevenLabs, else a generated pad). No end card, no Final Cut export. Plus a
  Google Doc plan, plan.json and transcript_words.json. The workflow's `cut` input
  (natural | both) and `style` in brand.json bring back the other versions.

## ElevenLabs (`eleven.py`, ELEVENLABS_API_KEY, Creator plan)
- Scribe transcribes each sermon once, in the reels run (verbatim, so it also finds
  the ums); Whisper is the fallback. `build_site.py` reads that transcript from the
  drafts folder for the transcript doc, site and YouTube description, ahead of
  YouTube's auto captions.
- Voice Isolator cleans each tight reel's voice (a trace of the room mixed back in).
- Music beds for the reels (no whoosh: it was distracting).
- Not used on the full sermon audio (podcast MP3 / YouTube): Voice Isolator is
  1,000 credits a minute, ~170k a month for 40-minute sermons, more than the plan.
- Budget: every call first reads the month's usage and is skipped (with the free
  fallback) if it would pass ELEVENLABS_BUDGET (default 0.85) of the plan. Reruns
  reuse the week's music and isolated voice from the drafts folder: no new credits.
  About 21k credits a week (Scribe ~16k, isolator ~3k, music ~2.5k).

## Brand
`social/brand.json` (ink #1a1a1a, off-white #f5f5f5, sage #778472; "style": bold).
Oakes Grotesk is licensed: fonts are downloaded at run time from a private Drive folder
(`SOCIAL_FONTS_FOLDER_ID`). Never commit font files; this repo is public.
Sunburst mark: `social/sunburst.svg`.

## Series bumper
If Drive "Series Graphics" holds a video named "<series> bumper.mp4" (e.g. "All In
bumper.mp4"), `upload_sermon.py` lays it over the opening of the YouTube video in the
finishing encode: the sermon's sound plays underneath, the last frame holds 1 s, then
it dissolves into the sermon over 1 s. Matched by the planning doc's series name;
podcast MP3 and reels are untouched. SERMON_BUMPER=off skips it.

## Thumbnails
`thumbnail.py`, settled 2026-09-28 after several rounds; keep changes within this layout.
Full-bleed photo, series lockup top left (background keyed out), title + scripture
bottom left, colours from the series art. The YouTube draft always gets the preacher
(Drive "Thumbnail Photos/<preacher>"); two community versions ("Thumbnail
Photos/Community") go to Drive "Thumbnails" as backups and are never set on YouTube.
Community is the guest-week fallback only. Rejected: side-panel preacher layout,
three-tile community strip, brand-colour-only design.
Only use photos from those hand-picked folders. Some people have asked not to appear
online; that list is kept privately, never in this repo. Ask Peter before adding photos.

## Secrets
GOOGLE_CLIENT_ID/SECRET (OAuth client "Eastpoint Sermons", Desktop, project
silver-ivy-500800-m9, Internal), GOOGLE_DRIVE_REFRESH_TOKEN (= GOOGLE_REFRESH_TOKEN),
GOOGLE_YOUTUBE_REFRESH_TOKEN, ANTHROPIC_API_KEY, YOUTUBE_API_KEY, ELEVENLABS_API_KEY,
PCO_APP_ID/PCO_SECRET (Planning Center personal access token, links page events).
Mint tokens with `python3 get_refresh_token.py drive|youtube`.

## Rules
- Never commit media, fonts, tokens, or unpublished sermon text (public repo).
  `Claude outputs/`, `social/fonts/`, `social_out/` are gitignored.
- Keep `opencv-python-headless<5` (OpenCV 5 removed the Haar face detector).
- Run all four `test_*.py` scripts before pushing.
- Never paste sermon text, member names or private details into GitHub issues or
  commit messages; the repo is public.
- Never print or share secret values. Collaborators run workflows with the repo's
  secrets and never need the tokens themselves.
- Local render test without Drive/Claude/Whisper:
  `LOCAL_VIDEO=clip.mp4 SOCIAL_WORDS_JSON=words.json SOCIAL_PLAN_JSON=plan.json LOCAL_OUT=out python3 social_clips.py`
- The weekly social strategy lives in a Claude Doc ("Eastpoint Social Media Strategy:
  The Front Door"); keep `social/strategy.md` in step with it.
