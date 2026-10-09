"""
post_quality.py — is a post original, and does it read like a person wrote it?

    from pipelines import post_quality
    post_quality.report(page, pack, others)   # {seo, original, human, verdict}

"the program will write the highly optimized post ... that can pass the google
 ai post scanner means ai plagiarism"

WHAT THIS IS, AND WHAT IT IS NOT

Google does not mark a page down for being written with AI. It marks down
pages that are copied, thin or written for search engines instead of people
(its own guidance on helpful content says exactly this). So the checks here are
the ones that decide whether a post does well, and none of them is a trick to
fool a detector:

  ORIGINAL       no run of eight words in a row lifted from another post on the
                 site, or from the pages Google ranks for the same search (SEO
                 keeps a sampled fingerprint of each page it read — never its
                 text). Over a few per cent shared, it is sent back.
  READS HUMAN    none of the phrases that give machine writing away, sentences
                 of different lengths the way a person writes, headings a reader
                 can scan, the important words in bold, and enough substance.
  SEO            SEO's own check against its keyword plan for the post
                 (pipelines/seo_keywords.check): the main keyword in the title,
                 heading, first lines, a subheading and the description; the
                 supporting keywords; the questions people search, answered.
"""
import hashlib
import json
import re
import statistics

SHINGLE = 8           # words in a row that count as a copied passage
SAMPLE = 4            # keep one shingle in four, chosen by its hash, on both sides
MOST = 1000           # at most this many kept for one page
COPIED = 0.06         # share of a post's passages found elsewhere that counts as copied

_WORD = re.compile(r"[a-z0-9][a-z0-9'’-]*")


def text_of(html) -> str:
    h = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", str(html or ""))
    return " ".join(re.sub(r"<[^>]+>", " ", h).split())


def _hash(words) -> int:
    return int.from_bytes(hashlib.blake2b(" ".join(words).encode("utf-8"), digest_size=8).digest(), "big")


def fingerprint(text, n=SHINGLE) -> list:
    """A sample of a text's eight-word passages, as numbers. Never the text itself."""
    ws = _WORD.findall(str(text or "").lower())
    out = []
    for i in range(0, max(0, len(ws) - n + 1)):
        h = _hash(ws[i:i + n])
        if h % SAMPLE == 0:
            out.append(h)
            if len(out) >= MOST:
                break
    return sorted(set(out))


def originality(html, others) -> dict:
    """{ok, share, like}: how much of this post is found in the other texts named."""
    mine = set(fingerprint(text_of(html)))
    if len(mine) < 5:
        return {"ok": True, "share": 0.0, "like": "", "checked": 0}
    worst, like = 0.0, ""
    for name, fp in (others or {}).items():
        theirs = set(fp if isinstance(fp, (list, set, tuple)) else fingerprint(fp))
        if not theirs:
            continue
        share = len(mine & theirs) / len(mine)
        if share > worst:
            worst, like = share, name
    return {"ok": worst < COPIED, "share": round(100 * worst, 1), "like": like if worst >= 0.01 else "",
            "checked": len(others or {})}


def sentence_variety(text) -> list:
    """People write short sentences and long ones. A page of identical lengths reads as made."""
    sents = [s for s in re.split(r"(?<=[.!?])\s+", str(text or "")) if len(_WORD.findall(s.lower())) >= 3]
    if len(sents) < 12:
        return []
    lengths = [len(_WORD.findall(s.lower())) for s in sents]
    mean = statistics.mean(lengths)
    spread = statistics.pstdev(lengths) / mean if mean else 0
    out = []
    if spread < 0.35:
        out.append(f"the sentences are all about {round(mean)} words long — mix short ones with longer "
                   "ones, the way a person explaining the job would")
    starts = [(_WORD.findall(s.lower()) or [""])[0] for s in sents]
    top = max(set(starts), key=starts.count)
    if starts.count(top) > len(starts) * 0.3 and top not in ("the", "a"):
        out.append(f"too many sentences start with “{top}” — vary how they open")
    return out


def human_voice(page, kind="blog") -> dict:
    from . import content_write
    problems = list(content_write.voice_problems(page, kind)) + sentence_variety(text_of((page or {}).get("html")))
    return {"ok": not problems, "problems": problems}


def report(page, pack=None, others=None, kind="blog") -> dict:
    """Everything the post editor shows, in one answer."""
    from . import seo_keywords
    seo = seo_keywords.check(page, pack or {}, kind) if pack else {"score": None, "items": [], "problems": []}
    original = originality((page or {}).get("html"), others or {})
    human = human_voice(page, kind)
    bits = []
    if seo.get("score") is not None:
        bits.append(f"SEO {seo['score']}/100")
    bits.append("original" if original["ok"] else f"{original['share']}% copied")
    bits.append("reads like a person" if human["ok"] else f"{len(human['problems'])} thing(s) to make it read better")
    return {"seo": seo, "original": original, "human": human, "verdict": " · ".join(bits)}


def problems(page, pack=None, others=None, kind="blog") -> list:
    """What goes back to the writer: copying first, then the voice."""
    r = report(page, pack, others, kind)
    out = []
    if not r["original"]["ok"]:
        out.append(f"{r['original']['share']}% of its passages are found word for word in "
                   f"{r['original']['like'] or 'another page'} — say it in your own words")
    out += [p for p in r["human"]["problems"] if p not in out]
    return out


def others(con, exclude_id=None, pack=None) -> dict:
    """What a post must not copy: every other post on the site, and the pages Google ranks."""
    out = {}
    try:
        rows = con.execute("SELECT id, title, draft FROM content_queue WHERE COALESCE(draft,'')!=''"
                           " AND COALESCE(status,'') NOT IN ('rejected')").fetchall()
    except Exception:
        rows = []
    for cid, title, draft in rows:
        if exclude_id is not None and int(cid) == int(exclude_id):
            continue
        try:
            d = json.loads(draft or "{}")
        except ValueError:
            continue
        html = ((d.get("_for_blog") or {}).get("html") or d.get("html") or "") if isinstance(d, dict) else ""
        fp = fingerprint(text_of(html))
        if fp:
            out[f"your post “{title or cid}”"] = fp
    for c in (pack or {}).get("competitors") or []:
        if c.get("fp"):
            out[c.get("domain") or c.get("url") or "a ranking page"] = c["fp"]
    return out


def extra(page, others_fp) -> list:
    """The two checks the writer did not already have: copying, and sentence variety."""
    out = []
    o = originality((page or {}).get("html"), others_fp)
    if not o["ok"]:
        out.append(f"{o['share']}% of its passages are found word for word in {o['like'] or 'another page'} "
                   "— say it in your own words")
    return out + sentence_variety(text_of((page or {}).get("html")))
