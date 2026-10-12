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

1. **Make your Glasswork Home** — *Reports* in the menu on your own copy. It
   makes your keys, and gives you one folder to upload to the program's own
   website (never a client's). Steps: `docs/RELEASING.md`, section 1.
2. **Choose the product's name.** It is "Glasswork" for now, in
   `config/product.json`. Changing it there changes it everywhere.
3. **Put the first version out** — *Settings → Publisher → Put it out*, with
   the Windows installer ticked (on your Windows computer). Testers download it
   from your Home's download page.

## What the tester does

1. **Download the Setup** from your Home's download page and open it (on a
   Mac, run it from source — see the README). If Windows says it protected
   their PC: **More info → Run anyway**.
2. **Open it** and make an account. Write down the recovery key.
3. **Choose how the agency thinks** (Claude, OpenAI or Gemini) with their own
   key, and set a small daily limit, such as $2.
4. **Fill in Your business,** or let it draft from their website.
5. **Connect their website**, or choose read-only.
6. **Run today** and watch **Your agency**: the floor shows every desk, and a
   red desk says why it stopped.
7. **Try Website Check** on their own site and on a competitor's.

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

**Help and reports → Report a problem.** It is sealed on their computer so only
yours can open it, and you read it in **Reports**. Your answer appears in their
Help screen. Nothing else is ever sent: no count of copies, no number for their
copy, no registration.

If they tick it, the report carries a support report — the version, which
desks failed and why, open faults — with every key, password, email address,
phone number, web address and lead detail taken out. They can read it first.

## Sending a new version

1. Change `version` in `config/product.json` (for example `0.10.0` → `0.10.1`)
   and push. (Claude does this with each round of work.) GitHub's Windows
   check builds the Setup, installs it, opens it and removes it again, so a
   broken one is caught before anyone sees it.
2. On your own copy: **Settings → Publisher → Put it out.**
3. Every tester's copy shows *"Version 0.10.1 is ready — Update now"*. Only a
   version signed with your key is installed; their work is kept, and they can
   go back to the version before from Settings.

## Good to know

- **Windows shows "Windows protected your PC"** for the Setup, because it is
  not signed with a paid certificate. **More info → Run anyway** opens it.
- **Mac and Linux** run the program from source (see the README).
- **Nothing installs itself.** The program asks your Home whether a new version
  is out — a plain question with nothing about them in it — and installs it
  only when they press Update now.
