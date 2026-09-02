# Re-uploading `agency-api.php` — replacing one file

You already did the hard part months ago: the folder is there, the token is
there, the setup screen is filled in. **None of that changes.** This is one file
swapped for a newer one, and it takes about three minutes.

## What changed, and why it is worth doing

The old file answered "what pages are on this site?" by looking at the top level
and stopping:

```php
glob(SITE_ROOT . '/*.{php,html,htm}')      // top level only
```

So anything in a folder — `services/`, `blog/`, `includes/` — did not exist as
far as the agency was concerned. Not watched for changes, not audited, not
counted, never written to. That is why you saw 14 pages, and why the only
include it knew about was `includes/header.php`, which it was only finding
because the change-watcher *guesses* at the usual locations. A guess that
sometimes fails to read is what told you your header had vanished from a site
you had not touched.

The new file walks the whole site. Tested on a tree shaped like yours it found
11 pages instead of 5, including `home.php`, `services/*.php` and
`blog/*.php`, while correctly skipping `node_modules`, `uploads` and `assets`.

---

## Step 1 — get the new file onto your PC

PowerShell:

```powershell
cd "C:\Users\ammar laptops\Desktop\glasswork"
git pull origin claude/code-agency-setup-owmsxv
```

The new file is at `paste-into-your-site\agency-api.php`. Open that folder in
Explorer and leave the window open.

## Step 2 — work out which name yours is called

You had a choice when you installed it, and the answer decides what you replace.
Look at the **Admin API address** on your setup screen:

| Your saved address ends in | The file on the server is | Replace |
|---|---|---|
| `/agency-api` | you renamed it | `public_html/agency-api/index.php` |
| `/agency-api/agency-api.php` | you did not rename it | `public_html/agency-api/agency-api.php` |

If you cannot remember and cannot find the setting, open File Manager and look
inside the `agency-api` folder — whichever of the two names is sitting in there
is the one to replace.

## Step 3 — keep the old one, just in case

In **hPanel → File Manager** (Hostinger; it is **cPanel → File Manager** on some
hosts), open `public_html/agency-api`.

Right-click the file from step 2 → **Rename** → put `-old` before the `.php`:

- `index.php` → `index-old.php`
- `agency-api.php` → `agency-api-old.php`

Thirty seconds, and it means a bad upload is one rename away from being undone
rather than a problem. Delete it in a week once you are happy.

## Step 4 — upload the new one

Still inside `public_html/agency-api`:

1. Click **Upload**
2. Choose `paste-into-your-site\agency-api.php` from your PC
3. If your file needs to be called `index.php` (the left-hand row of the table
   in step 2), right-click the file you just uploaded → **Rename** →
   `index.php`

**Do not touch `token.php`.** It is your password and it has not changed.

You should end up with exactly this:

```
public_html/agency-api/
├── index.php          <- the file you just uploaded (or agency-api.php)
├── index-old.php      <- the previous one, kept for a week
└── token.php          <- untouched
```

## Step 5 — prove it worked

PowerShell, in the glasswork folder:

```powershell
python -m pipelines.site_api --health
```

Want to see:

```
Mode    : full  (agents may change the site)
Token   : set
  ok  your site answered. PHP 8.x, folder writable
```

Then the one that actually shows the change:

```powershell
python -m pipelines.site_api --pages
```

**Before**, this listed about 14 files, all at the top level.
**After**, it should list `home.php`, everything in `services/`, everything in
`blog/`, and `includes/header.php` — noticeably more than before.

If the count is the same as before, the upload did not land. Check you replaced
the right one of the two names in step 2.

---

## If something goes wrong

| It says | What happened | Fix |
|---|---|---|
| `404` or `no such page` | wrong file replaced, or wrong folder | Check step 2 again |
| `401` or `token` | you replaced `token.php` by mistake | Put your token file back — it is `<?php return 'yourtoken';` |
| `500` | the upload was cut off part-way | Upload it again; File Manager sometimes truncates on a slow line |
| Page count unchanged | the old file is still live | The file you replaced was not the one being served |

**To undo entirely:** rename the new file to anything, rename `index-old.php`
back to `index.php`. You are exactly where you started, and nothing on your
actual website was ever touched — this folder is a side door, not a page.

---

## What this does not do

It does not change a single page of your website. It does not change how your
site looks, loads or reads. `agency-api` is a separate folder with its own
address that only the agency talks to; a visitor never reaches it and Google
never indexes it.
