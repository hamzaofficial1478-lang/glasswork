# Read this one. The last version was written for a programmer. Sorry.

## First — what is this actually about?

You make sign boards. So think about it this way.

Your website is a workshop with no sign on it. A **customer** who walks past can
look in the window, see the glass and the aluminium, and work out what you do.
That part is fine — your site reads well to a human.

But **Google is not a customer walking past.** Google is more like a clerk at
the Chamber of Commerce filling in a form about your business. Name. Address.
Phone. Trade. Hours. Areas you cover. If nobody fills that form in, the clerk
either guesses or leaves you off the list.

The two files in this folder are that form, filled in, written in the language
Google reads.

**No customer ever sees this.** It is invisible on the page — like the label on
the back of a panel. Nothing about how your site looks changes. Not one pixel.

**What you get for it:** you start showing up in the map results when someone in
Dubai searches "glass partition near me", and ChatGPT starts naming your company
instead of somebody else's when someone asks it who does aluminium work in
Dubai. Right now you are invisible to both, and that is the single biggest thing
wrong with your site.

---

## Second — your question: "how do I connect in full access mode?"

**You are already connected.** That happened when you saved your website address
on the setup screen. That is why the check ran and told you real things about
your real pages.

There are two modes and here is the honest difference:

| | **Read-only** (you are here) | **Full access** |
|---|---|---|
| How it reads your site | Like any visitor, from outside | Same |
| Can it find problems? | Yes, all of them | Yes, same |
| Can it change your site? | **No.** It writes the change down and hands it to you | **Yes.** It edits the page itself |
| What you have to do | Paste things in yourself | Approve on Telegram, it does the rest |
| What it needs from you | Just your website address | One file uploaded to your server, plus a password |
| Risk | None. It cannot touch anything | An agent can put something live on your site |

You are not missing anything by being in read-only. The agency sees exactly the
same problems either way. The only difference is **who does the typing** — you,
or it.

**My honest advice: stay read-only this week.** Do the paste below by hand
first. It takes five minutes and it is the highest-value thing on the whole
list. Once you have seen the score jump and you trust what it is telling you,
then we turn on full access and it stops handing you homework.

When you want full access, say so and I will walk you through it one step at a
time — it is one file onto your server and one password, nothing more.

---

## Third — the five-minute job

You need to get into your website's files. Almost every UAE host gives you
**cPanel**. If you have a web developer who set the site up, forward this
section to them and they will do it in two minutes.

### If you use cPanel

1. Log in to cPanel (usually `yoursite.com/cpanel` or a link from your host).
2. Click **File Manager**.
3. Open the folder called `public_html`. That is your website.

### Job 1 — the business card. Goes on every page.

Open the file **`1-localbusiness-every-page.html`** from this folder.

**Before you copy it, fix two things.** Near the middle you will see:

```
"streetAddress": "YOUR STREET / UNIT",
"addressLocality": "YOUR EMIRATE e.g. Dubai",
```

Replace those with your real workshop address, exactly as it appears on your
Google Business Profile. For example:

```
"streetAddress": "Warehouse 12, Al Quoz Industrial Area 3",
"addressLocality": "Dubai",
```

Keep the quote marks and the comma. Only change the words between the quotes.

**I left those blank on purpose.** Your address is nowhere on your website, so
the agency does not know it, and it will not invent one. A wrong address here is
worse than no address — Google checks it against your Google listing and a
mismatch loses you the map ranking this is meant to win.

Now copy the whole file, from `<script` to `</script>`.

In File Manager, open each of these and paste it just **above** the line that
says `</head>`:

- `index.php`
- `service.php`
- `about.php`
- `gallery.php`
- `blog.php`
- `faq.php`
- `contact.php`

`</head>` is near the top of every file. Paste the block on the line directly
before it. Save each one.

**I checked your live pages** — each one has exactly one `</head>`, written
exactly like that, so it is easy to find. Use File Manager's **Search** or press
Ctrl+F in the editor and type `</head>`.

I also checked whether your seven pages share a single header file, which would
have made this one edit instead of seven. **They do not** — each page has its
own `<head>` section with different content in it. So it is seven edits, sorry.
If your developer built it differently to how it looks from outside and there IS
a shared `header.php`, then one edit in that file does all seven.

### Job 2 — the questions. Goes on the FAQ page only.

Open **`2-faq-schema-faq-page.html`**. Copy the whole thing. Paste it into
`faq.php` only, in the same place — just above `</head>`. Save.

Nothing to edit in this one. It is your own thirteen questions and answers,
copied word for word off your FAQ page. I did not rewrite a single word,
because Google penalises you if the marked-up text does not match what a
visitor can actually read.

**If you ever change the wording of a question on that page, change it here
too.** They have to stay identical.

---

## Fourth — checking it worked

1. Go to <https://search.google.com/test/rich-results>
2. Paste in `https://www.worlddealtechnicalservices.com/` and press Test.
   It should say it found **LocalBusiness**.
3. Do it again with `https://www.worlddealtechnicalservices.com/faq.php`.
   It should say it found **FAQ**.

Then on your computer:

```
python run.py --site
```

The line that currently says **FAIL — AEO and GEO audit, score 37.4%** should
turn green and the score should jump a long way.

Send me a photo of the result and I will tell you what is next.

---

## One more thing, and it is bigger than this

While reading your pages I noticed something worth more than all the markup:

**You have no page for any single service.** You have `/service.php`, which
lists everything you do on one page. So when somebody in Dubai searches
"glass partition installation Dubai" — there is no page of yours for Google to
put at the top. Same for "pergola Dubai", "shower glass Dubai", "sign board
Dubai".

Each of those is a real search that a real customer types before they call
somebody. Right now that customer finds one of your competitors.

That is a bigger job than pasting two files, and it is the thing I would do
next. Do the paste first, then let us talk about it.
