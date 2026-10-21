"""
scrub.py — take out the characters nobody can see.

Text that comes back from an AI model, or is pasted from one, sometimes carries
characters that take up no space on the screen: zero-width spaces, word
joiners, soft hyphens, a byte-order mark in the middle of a sentence. They do
nothing for a reader, they can break a search for the very words they sit in,
and some tools use them to mark text as machine-written. seomachine's content
scrubber (github.com/TheCraigHewitt/seomachine) takes them out before anything
is published; so does this, for every post the writer drafts and every title
or description the Fix button writes, and the SEO rulebook flags pages that
already have them.

Kept: the joiners Arabic, Persian and emoji need (between those letters only),
and ordinary punctuation. Nothing is reworded here — the writer's own checks
deal with phrasing.
"""
import re

# Always invisible, never needed in a web page's text.
_GONE = re.compile("[​⁠﻿­᠎⁡-⁤⁪-⁯‎‏‪-‮⁦-⁩]")
# Zero-width joiner and non-joiner: needed inside Arabic/Persian words and emoji, not between Latin letters.
_JOINER = re.compile("(?<=[A-Za-z0-9 .,;:!?'\"()-])[‌‍]|[‌‍](?=[A-Za-z0-9 .,;:!?'\"()-])")
# Spaces that look like spaces but are not.
_ODD_SPACE = re.compile("[     　]")


def found(text) -> int:
    """How many invisible characters are in this text."""
    t = text or ""
    return len(_GONE.findall(t)) + len(_JOINER.findall(t))


def scrub(text, spaces=True):
    """The text without characters nobody can see. spaces: odd spaces become plain ones."""
    if not text:
        return text
    t = _GONE.sub("", text)
    t = _JOINER.sub("", t)
    if spaces:
        t = _ODD_SPACE.sub(" ", t)
    return t


def scrub_html(html):
    """The same for a page. Odd spaces are left alone there: in markup a no-break space is often meant."""
    return scrub(html, spaces=False)
