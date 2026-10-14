# Eastpoint sermon pipeline — notes for Claude Code

Public repo `eastpointdurham/sermons`. GitHub Actions turn each Sunday recording in the
Drive "Sermons" folder into a private YouTube draft, a podcast MP3, a transcript doc, and
2–3 draft vertical reels. Nothing is ever published or posted automatically. See SETUP.md.

## Workflows
- `upload_sermons.yml` → `upload_sermon.py`: Drive → private YouTube draft + MP3.
  Skips recordings dated before `UPLOAD_SINCE` (repo variable, default 2026-10-04).
- `update_sermons.yml` (hourly) → `build_site.py`, `create_drive_docs.py`,
  `enrich_descriptions.py`: transcripts, archive site, YouTube descriptions.
- `social_clips.yml` → `social_clips.py`: runs after the upload workflow. Whisper
  word timings → Claude picks 2–3 clips using `social/strategy.md` → `reframe.py`
  (virtual camera: face tracking, dead-zone pan, max 2.1x upscale) → `reel_design.py`
  (brand graphics in Pillow) → ffmpeg → Drive "Social Drafts/<date> <title>" with each
  reel in two looks, "(editorial)" and "(bold)", plus a Google Doc plan, plan.json,
  and a `Final Cut/` folder (`fcp_export.py`: FCPXML + .srt).

## Brand
`social/brand.json` (ink #1a1a1a, off-white #f5f5f5, sage #778472; "style": both).
Oakes Grotesk is licensed: fonts are downloaded at run time from a private Drive folder
(`SOCIAL_FONTS_FOLDER_ID`). Never commit font files; this repo is public.
Sunburst mark: `social/sunburst.svg`.

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
GOOGLE_YOUTUBE_REFRESH_TOKEN, ANTHROPIC_API_KEY, YOUTUBE_API_KEY.
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
