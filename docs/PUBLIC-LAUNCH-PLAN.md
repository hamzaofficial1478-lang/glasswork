# Going public — the plan

Your six ideas, written up properly, plus the things a public launch needs that
weren't on the list, and the order to build it all in. Nothing here is built
yet. Your own agency keeps running exactly as it does today the whole way
through. When the new version arrives, your current data moves in as your first
website, so nothing is lost.

---

## Decided (8 October)

- **Customers install it on their own computer.** Their data stays with them,
  and you run no servers.
- **Foundations first:** accounts, two websites each, and encryption (phase 1).
- **Any small business, anywhere, from launch.** So phase 2 has to cover
  every trade and country (services, lead types, areas, phone formats,
  languages, and each country's privacy law), not just Gulf trades.

---

## What we are building

A program anyone can install, which runs a small digital agency for their
business: it keeps their website healthy, finds leads, writes content, watches
Google, and asks before it does anything risky. Each person has their own
private account with up to two websites. They pick which AI does the thinking,
and connect their website through a short guided setup. They can also run a
**Website Check** on any website and get a full report.

---

## Your six ideas, polished

### 1. Two websites per account, one at a time

**What the user sees.** The website's name sits at the top of every screen,
with a switcher next to it. Picking the other website changes everything on
screen to that website: its orders, leads, approvals, content, Google results
and reports. The active website also has its own colour, so nobody approves a
change for the wrong site.

**How it works.** Each website gets its own private cabinet (database), its own
website connection, its own Search Console and map keys, and its own business
profile. The account itself holds what belongs to the *person*: the AI engine
and its keys, notifications (Telegram), and the password.

**Rules.**
- One website per run. While a day's run is going, switching is refused with
  "Finish or stop today's run on worlddeal… first".
- Two websites per account to start with. The limit is a single setting, so a
  bigger plan later is a number change, not a rebuild.
- Leads are never shared between websites. A firm collected for website A can
  still be collected for website B, because B is a different business.

### 2. Choose how the program thinks (the "engine") on first run

**What the user sees.** The first screen after making an account asks "How
should your agency think?", with three choices: **Claude**, **OpenAI (GPT /
Codex)** or **Google Gemini**. Each choice offers:

- **An API key.** The user pastes a key, presses **Test**, and sees "Working —
  about $X a day at your settings". They pay the AI company directly for what
  they use.
- **A subscription they already pay for**, but only where that company's own
  rules allow it.

**The honest part about subscriptions.** Anthropic's rules for products like
this one say, word for word: *"Unless previously approved, Anthropic does not
allow third party developers to offer claude.ai login or rate limits for their
products."* So the public version **cannot offer "sign in with your Claude
subscription"**. Claude will be offered by API key. You can apply to Anthropic
for approval if you want subscription sign-in later. OpenAI's and Google's
rules for driving their command-line tools (Codex CLI, Gemini CLI) with a
personal subscription have to be checked the same way before we build that
option. If a company says no, that option doesn't ship. Your own copy keeps
working the way it does today while we build.

**How it works.** Every sector already reaches the AI through one doorway
(`providers/ladder.think`). That doorway gets an adapter for each engine. Each
sector is given a *strong*, *standard* or *light* tier, mapped to that
company's current models, so the Managing Director still gets the strongest
mind and Report the lightest. The red line on spending becomes real for every
user: a daily spend cap per account, shown on the screen, with the program
stopping before it passes it.

**The program's own name.** The product needs its own name and look. Anthropic
allows "*YourName* — Powered by Claude", but not "Claude Code" or anything that
looks like an Anthropic product.

### 3. Each user's own setup

Every account, and every website in it, sets itself up separately: website
address, website connection, Search Console, maps key, email for outreach, and
notifications. Nothing one user saves is visible to another.

Setup becomes a **guided wizard**: one step per screen, a **Test** button on
every step, and plain instructions with pictures for the bits done elsewhere
(your host's File Manager, Search Console).

The website connection gets much easier:
- **PHP websites (like yours):** the user downloads *their own* connector file
  with their secret already inside it, uploads it, and presses **Check**.
  Nothing to copy and paste.
- **WordPress:** connect with a WordPress *Application Password*, which is
  built into WordPress, so no file upload is needed.
- **Wix, Shopify, Squarespace and anything else:** **read-only mode**. The
  agency checks the site, finds leads, writes content and tracks Google, and
  hands the user each website change to paste in themselves.
- **No connection at all** still works in read-only mode, so someone can try
  the program in five minutes.

### 4. Private and encrypted for each user

You called this "end to end encrypted". The accurate name is **private
accounts, encrypted on the computer**. It does what you described: nobody else
using the same program on the same computer can read your data.

- Each person signs in with their own password. Their data lives in their own
  folder, **encrypted**: database, keys, website connection, reports and logs.
- The encryption key is made from their password and only exists while they
  are signed in. Signed out, the folder is unreadable to other users, and to
  anyone who copies the files.
- **Recovery key.** At sign-up the user is shown a recovery key to write down.
  If they forget their password and don't have it, the data can't be
  recovered. That is what makes the encryption real, and the screen says so
  plainly.
- **Honest limits:** while someone is signed in, the program can read their
  data, because it has to do the work. Someone with full administrator control
  of that computer could in principle get at it then. That is true of every
  program.
- **How:** an encrypted database (SQLCipher), with keys protected by a
  password-derived key (scrypt). Before we commit to it, we prove it installs
  and works on Windows and Mac.

### 5. A modern, simpler interface

- **One app, one layout.** A sidebar (Today, Your orders, Waiting on you,
  Leads, Content, Website, On Google, Website Check, Settings) and a top bar
  with the website switcher, engine status, and today's spending.
- **One main action per screen**, said in plain words. Every status is a
  coloured label (Done, Waiting for you, Working, Stuck), and every empty
  screen says what to do next.
- A calm, professional look in place of today's cartoonish one: real
  typography, a proper colour system, light and dark modes, and screens that
  work on a phone (you run it from yours).
- **Design first.** You see and click through a prototype of the main screens
  and approve it before every screen is rebuilt in it.

### 6. Website Check: a report on any website

A separate feature, as you asked. Paste any website address and choose **Quick**
(about 20 pages) or **Full** (up to 200). Each sector checks the site from its
own angle, using the checks the agency already runs on your site, pointed at
someone else's in read-only mode:

| Sector | What it checks |
|---|---|
| IT | speed, mobile, broken links and pages, SSL certificate, uptime |
| SEO | titles, descriptions, headings, canonicals, sitemap and robots, which pages Google can list, structured data, internal links |
| Content | clarity, thin or repeated pages, how fresh it is, trust signals (reviews, real projects, addresses) |
| AI answers | whether ChatGPT, Gemini and Google's AI answers would pick and cite it (FAQs, schema, direct answers) |
| Leads | how easily a visitor can get in touch: contact paths, forms, click-to-call, WhatsApp, calls to action |
| Security | **passive checks only**: HTTPS, security headers, mixed content, what the pages themselves give away |
| Rivals (optional) | the same site side by side with the user's own |

**The report:**
- An overall score out of 100, plus a score for each sector.
- The **top 5 fixes** in plain words, each marked with how much it matters and
  how hard it is.
- The full list of issues, each with its evidence: the page, and what was
  found there.
- What is working well.
- **Progress:** check the same site again later and the report shows what
  improved.
- **Export to PDF** with the user's own logo, so it can be handed to a client.
  That makes it a sales tool as well as a check.

**Rules it keeps:**
- It never changes the site it checks.
- It reads politely: it follows the site's robots.txt, caps the pages, and goes
  at a gentle pace.
- On a site the user doesn't own, security stays **passive**. It looks only at
  what any visitor's browser receives, and never probes or tests for ways in.
  Scanning someone else's website for weaknesses without permission is illegal
  in many countries, including the UAE. "Loopholes" in the report means
  weaknesses that are visible from the outside, explained with their fixes.

---

## What a public launch also needs (not on your list)

1. **A business profile instead of hard-coded glass work in the UAE.** This is
   the biggest hidden job. About 50 files assume a glass and aluminium
   business in the UAE: services, lead types, areas, phone formats, PDPL. Each
   website gets a profile (trade, services, areas served, country, language),
   and everything reads from it. Your profile is pre-filled with today's values,
   so your agency behaves exactly as it does now.
2. **The red lines enforced inside the program.** Today some are guarded by
   Claude Code's hooks, which customers using OpenAI or Gemini won't have.
   Every red line (no deploy without tests, no prices offered without the
   owner, outreach caps, the spending cap, nothing unlogged) has to be enforced
   in the program's own code, with tests.
3. **An installer.** A normal Windows and Mac install, with no Python or Node
   to set up, and updates that install themselves.
4. **Terms of use and a privacy policy**, a product name, and a logo.
5. **A few beta testers** (two or three businesses you know) before a public
   launch.

---

## Progress

- **Phase 1 is built (8 October).**
  - Accounts with a password and a recovery key, and up to two websites each,
    with switching from the office.
  - Each person's data is encrypted on the computer: every website's database,
    settings and keys.
  - **Bring in your current website** copies your agency into your account,
    encrypted. A separate button removes the old, unencrypted copy once you've
    checked everything came across.
  - Until you make an account, the program runs exactly as before.

- **Phase 2 is built (8 October).**
  - Each website has its own business profile: what it does, its country and
    areas, what it sells, and who buys it.
  - A **Your business** page sets the profile, and can draft it from the
    business's own website.
  - Lead collection, phone numbers, map searches, writing, SEO and email all
    follow the profile.
  - Outreach follows each country's email rule.
  - Your website keeps exactly what it had.

- **Phase 3 is built (8 October).**
  - A new account's first stop is **How your agency thinks**: Claude, OpenAI
    or Google Gemini, each with the person's own API key, a **Test** button,
    and "about $X a day" before anything is spent.
  - Each desk gets the strongest, everyday or lightest model of that company.
    The Managing Director, Security and IT get the strongest; Report the
    lightest. Each can be changed. A model that fails steps down a tier.
  - The daily spending limit is checked before every AI call and every paid
    tool, across both websites. A call that would pass it is not made.
  - Keys are kept encrypted in the account and never shown again, not even on
    that page. Changing them later asks for the password, like Setup.
  - Your own copy keeps Claude Code on your Claude plan, exactly as today. A
    new customer's account is not offered that, because of Anthropic's terms.
  - Not built: signing in with a ChatGPT or Gemini subscription. Their terms
    still have to be checked.

- **Phase 4, first half (8 October).**
  - **The new design is a clickable prototype, waiting for your approval:**
    Today, Waiting on you, Your orders, Leads, Content, Website, On Google,
    Website Check, Settings, the setup step that connects a website, dark
    mode, and Today on a phone. No screen is rebuilt until you say yes.
  - **Connecting a website is now a download.** Setup makes a zip for your
    website with the connector and its secret already inside: upload it,
    Extract it, press Check. Full access starts only when Check proves it
    answers. Downloading it again keeps the same secret, so a connector
    already on a website keeps working.
  - **WordPress connects with an Application Password**, made inside
    WordPress, with no file to upload. Pages, posts, structured data and undo
    all go through WordPress's own API. A theme's header and footer cannot be
    changed that way, so those changes are handed to the owner.
  - **Read-only** is one press, for Wix, Shopify, Squarespace, or "not now".
  - Second half, after your yes: the setup wizard and every main screen
    rebuilt in the new design.

- **Phase 4, the new design goes live (8 October).** "the ui was still same
  not as you showed here" — taken as the yes.
  - The office now opens in the new design: Today, Waiting on you, Your
    orders, Leads, Content, Website, On Google, Website Check and Settings,
    with the website switcher, the AI and today's spend at the top. Light or
    dark follows the computer, or the choice in Settings. On a phone it has a
    bottom tab bar.
  - Every screen shows the agency's real records, and the buttons are real:
    yes/not now on approvals, saving today's order for a desk, Run today.
  - The previous office is kept at /classic, with every panel, while the
    remaining screens (Setup, the review page, Your business, the engine
    page) move into the new design.
  - Website Check shows what is coming; it is built in phase 5.

- **Phase 5 is built (8 October): Website Check.**
  - Type any website's address and choose **Quick** (up to 20 pages, a minute
    or two) or **Full** (up to 200 pages, usually under ten minutes). It
    carries on if the screen is closed, shows its progress, and can be
    stopped. One check runs at a time.
  - Six desks look at it and each gives a score out of 100: Speed and health,
    Found on Google, Words and trust, Picked by AI answers, Easy to get in
    touch, and Security from outside. The overall score is their average.
  - **The five fixes that matter most**, chosen for the most good for the
    least effort, each marked with how much it matters and how long it takes.
    Then every finding, desk by desk, with the pages it was seen on and the
    evidence, and what is working well.
  - **Check again** later and the report says what was fixed, what is new, and
    how the score moved.
  - **The report** opens as its own page and downloads as a **PDF** with your
    name and logo at the top. The PDF is made by the Edge or Chrome already on
    the computer; without one, the report page prints to PDF.
  - The rules it keeps: only ever reads (no form is sent), follows the site's
    robots.txt and its Crawl-delay, one page a second at most, and security
    stays passive. It refuses addresses on the computer or its local network.
  - Not built yet: comparing the site side by side with a rival's, and uptime,
    which one visit cannot measure. Speed is the server's answer time, not a
    full phone speed test.

- **Phase 6, the agency floor (9 October).** "make sure the dashboard should
  have a full control ui like it was before the sectors and agents"
  - **Your agency** brings the classic office's floor into the new design: a
    plan of the seven rooms with a lamp for every desk and an arrow for every
    open request between sectors, a card per room listing each desk with its
    state and what it is doing, the shelf, and the requests between desks.
  - A desk that stopped turns red and names the problem: **failed** (its run
    raised an error), **stalled** (it started and never came back), or **stuck
    on a job** (holding work nobody is doing). Counts and filters show at a
    glance what is working, failed or stuck, waiting on you, done or quiet.
  - **Click a desk:** what it is doing or why it stopped, its work today, its
    jobs, a button to put a stuck job back, and a chat with that desk.
  - **Click a room:** switch the sector on or off, whether it can work and
    why, why it scores what it does, today's order for it, its board, its
    requests, its faults and what it made today.
  - **Activity** and **Results** are in the new design too, and the floor has
    Run today, Stop, Pick up where it left off and Clear up.

- **Phase 6, ready for other people (9 October).**
  - **Installers.** Windows: double-click `install\Install-Windows.bat`. Mac:
    `install/Install-Mac.command`. Linux: `bash install/install.sh`. Each
    installs Python and Node if missing (asking first, for this user only),
    keeps the program's libraries in its own folder, and puts it on the
    desktop.
  - **Updates without git.** A downloaded copy checks an update address and
    installs a new version only if your signature matches. People's data,
    keys and settings are never touched, and an update can be undone from
    Settings. Your own git copy keeps updating as it always has.
  - **Terms of use and privacy policy**, written in plain English and marked
    as drafts for a lawyer to check. Sign-up links to them.
  - **The product's name** lives in one place, `config/product.json`
    ("Glasswork" for now, your choice). A customer's account shows that name,
    never yours.
  - **Help and feedback:** a support report to copy and send, with nothing
    private in it.
  - **docs/BETA-TESTING.md:** who to ask, what to give them, what to watch,
    and how to send them an update.
  - Not built yet: a signed .exe and .dmg installer (needs a code-signing
    certificate and a build computer for each system), and a download page.

- **Mail, posts and the floor, refined (9 October).**
  - **Leads has its mail room:** *Your mail* (write one mail for every lead,
    preview, change, send yourself a test, approve), *Send* (tick who gets it;
    retry or delete what did not go), and *Sent mail*: every mail that went,
    readable in full as the lead saw it, deletable one by one or all at once,
    and downloadable as a CSV. A deleted mail's words are wiped, but the agency
    still remembers the business was written to, so nobody is mailed twice.
  - **Content:** choose today's topic from SEO's suggestions (best first, with
    why) or type your own; the writer works with SEO's keyword plan, and every
    post is checked for copying (against your other posts and the pages Google
    ranks for it) and for reading like a person. Every post has Delete.
  - **The post editor:** bold, italic, underline, headings, lists, links on any
    word, pictures, text size, font, colour and highlight, with the SEO score,
    "Original" and "Reads like a person" re-marked as you type. A post on your
    website has Edit and Delete from website; both go through the IT Releaser,
    tests first. Pictures added in the editor wait on your computer until the
    Releaser takes them up with the post.
  - **The floor** draws each agent at a desk instead of a dot, and the shelf is
    now a button beside the filters that glows in moving colours, with the
    number of tools in use, while any tool is being used.
  - The connector file is now version 7 (it can save pictures inside a post).
    Download it again from Setup and upload it once; WordPress sites need
    nothing new.

## The order to build it in

Each phase ends with something working that you can use. Your current agency
keeps running throughout.

| Phase | What gets built | You can then… |
|---|---|---|
| 1 | Accounts with passwords, an encrypted folder per user, up to 2 websites with the switcher. Your current data becomes your account's website 1. | Sign in, add a second website, switch between them |
| 2 | The business profile, and every hard-coded "glass in the UAE" moved into it, for any trade in any country: areas from OpenStreetMap, phone numbers in each country's format, each country's privacy rules for outreach | Set up a website for a completely different trade or country |
| 3 | AI engines: Anthropic, OpenAI and Gemini by API key (plus subscriptions wherever allowed), the first-run choice, spend tracking and the cap | Run the agency on GPT or Gemini instead of Claude |
| 4 | The new design: prototype → your approval → the setup wizard and the main screens rebuilt in it, plus the easier website connection | Set up a new website in a few minutes |
| 5 | Website Check: sector checks, scores, report, PDF | Hand a prospect a report on their website |
| 6 | Installer, updates, terms, branding, beta testers | Launch |

Website Check (phase 5) doesn't depend on phases 1–4. It can be moved earlier
if you'd rather have it to use for sales now. It would then be moved into the
new design when phase 4 lands.
