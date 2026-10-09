# Beta testing: two or three businesses before anyone else

The point of a beta is to watch the program meet people who are not you. You
know where everything is; they do not. Every question they ask is a screen
that needs a better sentence.

## Who to ask

Pick two or three businesses you know and trust, and who will tell you the
truth:

1. **One in your own trade, here.** They will judge whether the agency
   understands the work.
2. **One in a completely different trade.** A dentist, a bakery, a garage.
   This tests the business profile: everything that used to assume glass in
   the UAE.
3. **If you can, one in another country.** This tests phone numbers, areas,
   and each country's email rules.

Tell them plainly: it is a test, it will have rough edges, and their data stays
on their own computer.

## Before you hand it over (once)

1. **Make your signing key**, on your own computer, and keep it outside the
   program folder:

       python -m coordination.updates --new-key C:\Users\you\glasswork-signing.key

   It prints an `update_key`. Put that into `config/product.json`.
2. **Choose where updates live.** Any https address you control works, such as
   GitHub Releases or your own hosting. Put the address of `manifest.json` into
   `update_url` in `config/product.json`.
3. **Put your support email** into `support_email` in the same file, so the
   report screen tells testers where to send things.
4. **Choose the product's name.** It is "Glasswork" for now, in
   `config/product.json`. Changing it there changes it everywhere.
5. **Make the first release** (see "Sending an update" below) and give each
   tester the zip.

## What the tester does

1. **Unzip the folder** somewhere they will keep it, such as Documents.
2. **Install:**
   - Windows: double-click `install\Install-Windows.bat`.
   - Mac: double-click `install/Install-Mac.command`. If the Mac refuses,
     right-click it, choose Open, then Open again.

   It installs Python and Node if they are missing (it asks first), sets
   everything up inside the folder, and puts the program on the desktop.
3. **Open it** from the desktop and make an account. Write down the recovery
   key.
4. **Choose how the agency thinks** (Claude, OpenAI or Gemini) with their own
   key, and set a small daily limit, such as $2.
5. **Fill in Your business,** or let it draft from their website.
6. **Connect their website**, or choose read-only.
7. **Run today** and watch **Your agency**: the floor shows every desk, and a
   red desk says why it stopped.
8. **Try Website Check** on their own site and on a competitor's.

## What to watch for in the first week

- Where did they get stuck, and what did they expect to see instead?
- Did any desk go red? What did it say, and did the reason make sense?
- Did anything go out that they did not expect? (Nothing should go out
  without their yes. If something did, that is the most important bug there
  is.)
- How much did a day cost, against what the engine page estimated?
- Did the leads, pages and fixes look like the work of someone who knows
  their trade?

## How they report a problem

**Settings → This program → Help and feedback → Make a support report → Copy
the report**, then paste it into an email to you.

The report lists the program's version, which desks failed and why, open
faults, and what stops any sector from running. It has no keys, passwords,
email addresses, phone numbers, web addresses or lead details in it, and they
can read every line before copying it.

## Sending an update

1. Change `version` in `config/product.json` (for example `0.6.0` → `0.6.1`)
   and commit.
2. Make the release:

       python -m coordination.updates --make-release C:\releases\0.6.1 --key C:\Users\you\glasswork-signing.key --base-url https://your-address/releases/0.6.1 --notes "What changed, in one line"

   This writes the zip and `manifest.json`. Only files tracked in git go into
   the zip, never `.env` or anyone's data.
3. Upload both files to the address you chose, and put the new `manifest.json`
   where `update_url` points.
4. Testers press **Settings → This program → Check for updates → Install**.
   The program installs it only if your signature matches. Their data and
   settings are not touched, and **Go back to the previous version** undoes it.

Never share the signing key, and never put it in the program folder. Anyone
with it can sign an update that every copy will trust.

## Not built yet

- **A signed Windows .exe and Mac .dmg installer.** Today's installer is a
  script that sets Python and Node up for the user. A real installer that
  carries everything inside it needs a code-signing certificate and a build
  computer for each system. Without signing, Windows and macOS warn people off
  a downloaded program.
- **A download page.** Testers get the zip from you directly.
- **Updates installing themselves.** This is deliberate: the program says an
  update is out and the person presses Install.
