# Re-uploading `agency-api.php` — the same three minutes, one more time

Sorry. You did this last week and it worked; you have to do it again, and it is
my fault, not yours. Same folder, same file, same steps. Read the next section
so you know what you are fixing, then it is Step 1.

---

## Why again

Making the listing walk your whole site was the right fix — it is why you went
from 14 pages to 32, and why `home.php`, `services/` and `blog/` are finally
visible. But look at what came back in that list of 32:

```
admin/login.php          admin/dashboard.php      admin/settings.php  (…17 files)
config/config.php        config/db.php
```

That is your admin panel and the file holding your database password. They
should never have been on that list. When I went to fix it I found three
things, and the last two were worse than the first:

**1. The listing and the door disagreed.** Reads and writes matched a filename
anywhere in a path, the listing matched only exact names. So `config/db.php`
was correctly refused for reading and cheerfully listed. Names only — nothing
was read.

**2. Hiding a folder is not protecting it.** The skip list only ever applied to
the listing. `admin/login.php` was hidden from the list *and perfectly readable
and writable* to anything holding your token. I proved it: I read your admin
login page and overwrote your dashboard on a test copy.

**3. Your token file was not protected.** The never-touch list has `token.txt`
on it. But `/health` tells you to move your token **out** of `token.txt` and
**into** `token.php`, because `.txt` is readable by anyone who guesses the
address. So the file we tell you to use was the only one a write could replace.
A test write replaced it — which would have locked the agency out of your own
site until you noticed and put it back by hand.

All three are fixed, with one rule that the listing and the door now share, so
they cannot drift apart again. The agency's end refuses these pages too, not
just your server's end — which means **you are already protected as of today's
run**, even before you upload anything. The upload closes it at the server.

---

## Step 1 — get the new file

PowerShell:

```powershell
cd "C:\Users\ammar laptops\Desktop\glasswork"
git pull origin claude/code-agency-setup-owmsxv
```

The file is `paste-into-your-site\agency-api.php`. Open that folder in Explorer
and leave the window open.

## Step 2 — delete `index-old.php` FIRST

This is the new step, and it matters more than the upload does.

Last time you renamed the old file to `index-old.php` and kept it, because I
told you to. That was sensible then. It is a problem now: **it is still a
working door.** A `.php` file in a web folder runs when someone asks for it, so
`…/agency-api/index-old.php` is a complete, token-checking copy of the old API
— the one that will happily hand over your admin panel.

hPanel → File Manager → `public_html/agency-api` → right-click
`index-old.php` → **Delete**.

Your website does not use that file. Nothing links to it. Deleting it changes
nothing except closing the old door.

While you are in there, if you see `agency-api-old.php` or anything else ending
in `-old.php`, delete those too. Same reason.

## Step 3 — replace the live one

Whatever file is left in that folder besides `token.php` is the one being
served. It is one of these two:

| The file in your folder | That is your live API |
|---|---|
| `index.php` | replace this |
| `agency-api.php` | replace this |

1. Click **Upload**
2. Choose `paste-into-your-site\agency-api.php` from your PC
3. If the file you are replacing is called `index.php`, right-click the one you
   just uploaded → **Rename** → `index.php`

File Manager will ask whether to overwrite. Say yes.

**Do not touch `token.php`.** It is your password and it has not changed.

You should end up with exactly two files:

```
public_html/agency-api/
├── index.php     <- the one you just uploaded (or agency-api.php)
└── token.php     <- untouched
```

No `-old` files this time. If the upload goes wrong, `git pull` gives you the
file again — the copy on your server was never the only one.

## Step 4 — prove it worked

```powershell
python -m pipelines.site_api --pages
```

**Before:** 32 pages, including 17 under `admin/` and 2 under `config/`.
**After:** noticeably fewer — around 13 — and **no `admin/` or `config/` line
at all**.

This is the one time a *smaller* number is the right answer. You should still
see `home.php`, everything in `services/`, everything in `blog/`, and
`includes/header.php`. Those are your pages. The ones that vanished were never
pages.

Then check the door is shut:

```powershell
python -m pipelines.site_api --read config/db.php
```

Want to see: `config/db.php is inside config/, which is not the website`.

---

## If something goes wrong

| It says | What happened | Fix |
|---|---|---|
| `404` or `no such page` | the file you replaced was not the live one | Look in the folder again — whichever name is there is the live one |
| `401` or `token` | `token.php` got overwritten | Put it back: `<?php return 'yourtoken';` — the token itself is on your setup screen |
| `500` | the upload was cut off part-way | Upload again; File Manager truncates on a slow line |
| Still lists `admin/` | old file still being served | Check `index-old.php` really is deleted |

**To undo:** there is no `-old` file to fall back to this time, on purpose. If
you need the previous version it is one command away on your PC:

```powershell
git show HEAD~1:paste-into-your-site/agency-api.php > old-api.php
```

Nothing on your actual website is touched either way. `agency-api` is a side
door with its own address — a visitor never reaches it and Google never indexes
it.

---

## What was actually at risk

Straight answer, with the bit I can prove separated from the bit you should
check yourself.

**What I can prove:** your database credentials were never readable. Reads of
`config/db.php` were refused even by the old file — I tested it against a copy
of your file, not by reading the code. The listing exposed filenames, nothing
more.

**What you should check:** whether anything of ours ever wrote into `admin/`.
Every write the agency makes is logged at the moment it happens, so the answer
is one command:

```powershell
python -m coordination.changelog --all
```

Every line should be one of your real pages — `index.php`, `home.php`,
something under `services/` or `blog/`, or `includes/header.php`. If you see
`admin/` or `config/` anywhere in that list, tell me and I will put it back
from the backup the server kept. I do not expect you to find one: nothing in
the agency had a reason to go there, and the only path that reaches those
folders is a page name typed by hand.

What it would have cost if left alone is duller than a break-in: seventeen
admin files in the change-watcher's queue, eating IT's entire daily allowance
re-reading pages nobody was ever going to edit. That, and a rewrite landing
somewhere it had no business being.
