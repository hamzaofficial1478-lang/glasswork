# Full access — 4 steps, nothing to work out

Your three questions, answered first, because they were all fair.

---

### "Is that PowerShell line the thing I paste into the token file?"

**No.** That was a command you'd *run* to produce a random string. Confusing, and
unnecessary. **I have generated one for you.** It is in step 2 below. Just use it.

### "So I'll have two files called index.php — won't they clash?"

**No, and here is why.** They are in different folders, so they are different
web addresses:

| File on the server | What it is | Address |
|---|---|---|
| `public_html/index.php` | your homepage | `worlddealtechnicalservices.com/` |
| `public_html/agency-api/index.php` | the agency door | `worlddealtechnicalservices.com/agency-api/` |

Same as having a `photo.jpg` in two different folders. Nothing is overwritten,
nothing is replaced, your homepage is not touched. A folder with its own
`index.php` is the completely normal way to do this.

**But you do not have to rename anything.** Step 3 below tells you to upload
the file and rename it. If that still feels risky — skip the rename and use
`agency-api.php` as-is. The address in step 4 just changes slightly. Both work.
I have written both out.

### "What do I upload into agency-api.php?"

**Nothing.** `agency-api.php` is not an empty file you fill in — it *is* the
finished code, already written, already tested. It comes down with `git pull`
and you upload it as a file. You never open it or edit it.

---

## Step 1 — get the files

On your PC:

```powershell
cd "C:\Users\ammar laptops\Desktop\glasswork"
git pull origin claude/code-agency-setup-owmsxv
```

Everything is now in the folder `paste-into-your-site`. Open it in Explorer.

## Step 2 — make the token file

On your PC, open Notepad. Paste in **exactly this one line**:

```php
<?php return '78o5ukqch0y7g2h16l9l0zvoukztc4jkiyzxo6g11unw0vyu';
```

Save it as **`token.php`**.

> In Notepad's Save dialog, set "Save as type" to **All Files** first, or it
> will save as `token.php.txt` and nothing will work.

That long string is your password. It is random and it is yours. Keep the
Notepad window open — you need to type that string again in step 4.

**Why a `.php` file and not a `.txt`?** Because the folder is inside your
website, anyone could type `yoursite.com/agency-api/token.txt` in a browser and
read a text file straight off the screen. A `.php` file gets *run* by the
server instead of shown, so the same request returns a blank page. I tested
both — the `.txt` handed the password over, the `.php` gave nothing.

## Step 3 — put two files on the server

cPanel → **File Manager** → open **`public_html`**

1. Click **+ Folder**, name it `agency-api`, create.
2. Open that new `agency-api` folder.
3. Click **Upload**, and upload **two** files:
   - `agency-api.php` (from the `paste-into-your-site` folder on your PC)
   - `token.php` (the one you just made in Notepad)
4. Back in File Manager, right-click **`agency-api.php`** → **Rename** →
   change it to **`index.php`**

You should now have exactly this:

```
public_html/
├── index.php          <- your homepage, untouched
├── service.php
├── about.php
├── ...
└── agency-api/
    ├── index.php      <- the file you just renamed
    └── token.php      <- your password
```

> **Do not want to rename?** Fine. Leave it called `agency-api.php` and use
> the second address option in step 4 instead. It works identically.

## Step 4 — tell the agency

Open the setup screen, choose **Full access**, and fill in the two boxes:

**Admin API address** — if you renamed the file to `index.php`:
```
https://www.worlddealtechnicalservices.com/agency-api
```

**Admin API address** — if you did NOT rename it:
```
https://www.worlddealtechnicalservices.com/agency-api/agency-api.php
```

**Admin API token** — the long string, and nothing else. Not the `<?php`, not
the quotes, not the semicolon. Just this:
```
78o5ukqch0y7g2h16l9l0zvoukztc4jkiyzxo6g11unw0vyu
```

Press Save.

---

## Now test it

```powershell
python -m pipelines.site_api --health
```

**What you want to see:**
```
Mode    : full  (agents may change the site)
Token   : set

  ok  your site answered. PHP 8.2, folder writable
```

If instead you get:

| It says | What is wrong |
|---|---|
| `read-only mode` | Full access was not saved on the setup screen |
| `refused the token` | The string in the setup box does not match the one in `token.php`. Copy-paste both again — a trailing space is enough to break it |
| `nothing answered at ...` | The address is wrong, or the file is not where you think. Try opening the address in a browser: it should say `{"ok":false,"error":"bad or missing token"}`. If you get a 404, the file is not there |
| `did not answer with JSON` | Your host printed a PHP warning. Send me what it shows |

Then:

```powershell
python -m pipelines.site_api --pages          # every page it can see
python -m pipelines.site_api --plan-schema    # what it WOULD change. Changes nothing
python -m pipelines.site_api --apply-schema   # actually do it
```

Then look at your site. Nothing will look different — that is correct. Check it
worked at <https://search.google.com/test/rich-results>.

If anything goes wrong:

```powershell
python -m pipelines.site_api --backups            # every restore point
python -m pipelines.site_api --undo index.php     # put a page back
```

---

## What I found while testing this, so you know it was actually tried

I built a copy of your seven pages on a real PHP server and drove the whole
thing. Four things broke, all mine, all now fixed:

1. **Every call would have failed on your host.** I was using tidy addresses
   like `/agency-api/health`, which need a setting called mod_rewrite. My test
   server allowed it; Apache — what your host runs — does not, and would have
   returned 404 to everything. It now uses a plain `?do=health`, which works on
   every host with no configuration at all. I re-tested with a server rigged to
   behave the way Apache does.
2. **Two edits in the same second shared one backup filename**, and the second
   one overwrote the original — on the feature whose whole job is undoing.
3. **Fixing that broke rollback**, so a wrecked page stayed wrecked. Only found
   because I wrecked a page on purpose and tried to put it back.
4. **The token file was readable by anyone** who typed its address. Hence
   `token.php` instead of `token.txt` in step 2.

If step 3 or 4 still doesn't make sense, tell me which line and I'll redo it.
