# Two blocks to paste into worlddealtechnicalservices.com

Generated from **your live site** on 25 August 2026, not from a template. Every
number, phone, email and question below was read off your own pages.

This is the one broken item on `python run.py --site`:

```
FAIL  AEO and GEO audit
      7 page(s) audited, score 37.4%. Worst: LocalBusiness markup (7 pages);
      FAQ markup so a machine can find the Q and the A (7 pages)
```

Nothing here changes how your site looks. It adds machine-readable labels that
tell Google and the AI answer engines what your business is, where it is, and
what you answer. That is what gets you into the Dubai map pack and quoted in
ChatGPT and AI Overviews instead of ignored.

---

## 1. `1-localbusiness-every-page.html`

Paste inside `<head>` on **every page** — index, service, about, gallery, blog,
faq, contact. If your pages share a header include, it goes in there once.

**Before you paste, fill in the two fields in CAPITALS.** They are your street
address and emirate, and they were left blank on purpose: they are not on your
website anywhere, so the agency did not have them and would not invent them.
A wrong address here is worse than no address — Google cross-checks it against
your Google Business Profile and a mismatch costs you the map-pack ranking that
this block exists to win.

Everything else was taken off your site: the name, the phone
(+971 56 510 0471), the email, and the three emirates you say you serve.

---

## 2. `2-faq-schema-faq-page.html`

Paste inside `<head>` on **`/faq.php` only**.

Thirteen questions, taken word-for-word off that page. Nothing was rewritten
and nothing was added — marked-up text a visitor cannot actually read on the
page breaks Google's rules and can get the markup ignored or the page
penalised.

**If you edit the wording of a question or answer on the page, edit it here
too.** They have to match.

---

## After pasting

1. Check both blocks at <https://search.google.com/test/rich-results> — paste
   the page URL, not the code.
2. Re-run `python run.py --site`. The AEO score should move from 37.4% up
   sharply, and the LocalBusiness and FAQ lines should go green.
3. In Search Console, request re-indexing for the home page and `/faq.php`.

## What is still missing after this

`Sources cited (6 pages)` — that one is a content job, not a markup job. It
means your service and blog pages state things ("tempered glass is stronger",
"pergolas need X clearance") without pointing at a standard or a manufacturer.
Answer engines cite pages that cite. Content sector's work, not a paste job.
