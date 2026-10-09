# Putting a new version out — the maintainer's guide

Glasswork is open source. New versions are published on the program's GitHub
release page, and each copy stays on its own computer: nothing is collected
about the people using it, and no copy contacts anyone on its own.

---

## 1. Once: make the repository public

People can only download the Setup from the release page if the repository is
public. On GitHub: **Settings → General → Danger Zone → Change repository
visibility → Public**.

Before you do, remember that a public repository shows its **whole history**,
not just today's files. Look through it for anything you would not want public
— for example a client's details in `paste-into-your-site/` or in tests. No
keys, passwords or `.env` files have ever been committed (they are kept out by
`.gitignore`).

## 2. Each new version

1. The version number goes up in `config/product.json` (Claude raises it with
   each round of work) and the change is pushed.
2. GitHub's *windows-installer* check then, on a real Windows machine:
   builds the Setup, installs it silently, opens the program, makes an account,
   adds a website, checks the office reads its encrypted records and wrote
   nothing in the program's folder, and removes it again.
3. Only if all of that passes, it creates the release **v0.x.y** on the release
   page with the Setup attached. A version already there is never replaced —
   the next one needs a higher number.

You can watch it under **Actions → windows-installer**. Each run also keeps the
Setup for two weeks under its *Artifacts*.

## 3. What people do

- **New people:** open the release page, download `glasswork-x.y.z-setup.exe`,
  open it, choose **More info → Run anyway** if Windows asks (the Setup is not
  signed with a paid certificate, by choice), and follow the steps. Everything
  the program needs is inside the Setup.
- **Getting a new version:** *Settings → This program → Check for a new
  version → Download*, then run the new Setup over the old one. Their work is
  kept in its own folder and is not touched.
- **Help:** *Help → Make it* makes a support report with nothing private in
  it. They read it and send it to whoever they choose — you by email, or a new
  issue on the GitHub page.

## 4. What each person sets up themselves

- **Their own account**, encrypted with their own password. No other account
  on the same computer can open it.
- **Their own AI key** (Anthropic, OpenAI or Google), paid to that company
  directly.
- **Their own website connection**, with its own secret: the connector folder
  for a hand-built website, or a WordPress Application Password.
- **Maps, Search Console, email** — their own keys, each optional.

The program walks them through it: **Today** shows *Getting started* until
their business, AI, website and first orders are in place.
