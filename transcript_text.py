"""
Tidy sermon transcript text, and lay it out as a Google Doc.

Used when a transcript is polished (build_site.py) and when it is filed in
Drive (create_drive_docs.py), so both read the same.
"""

import html
import re

# Captions hear the church's name as two words; it is one.
_NAME = re.compile(r"\beast[\s-]?point\b", re.I)
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_RULE = re.compile(r"^\s*([-=_*])\1{2,}\s*$")


def fix_names(text):
    return _NAME.sub("Eastpoint", text or "")


def tidy(text):
    """Plain paragraphs: church name fixed, markdown headings, rules and bold
    markers removed, one blank line between paragraphs."""
    out = []
    for line in fix_names(text).splitlines():
        if _RULE.match(line):
            continue
        line = _HEADING.sub("", line)
        line = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), line)
        out.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def transcript_doc_html(title, scripture, preacher, service_date, transcript):
    """A transcript as a Google Doc: title as a heading, a byline, a divider,
    then the sermon in paragraphs."""
    body = tidy(transcript) or "[Transcript not yet available]"
    # the polish step can title each chunk it cleans ("Sermon Transcript: ...")
    body = re.sub(r"^(sermon )?transcript\b[^\n]*(\n+|$)", "", body, flags=re.I | re.M)
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    byline = " · ".join(x for x in (scripture, preacher, service_date) if x)
    e = html.escape
    return (
        "<html><body>"
        f"<h1>{e(fix_names(title))}</h1>"
        f"<p><i>{e(byline)}</i></p><hr>"
        + "".join(f"<p>{e(p).replace(chr(10), '<br>')}</p>" for p in paras)
        + "</body></html>"
    )
