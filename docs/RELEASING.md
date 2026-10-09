# Putting your program out — the owner's guide

This guide is for your own copy only. It is left out of every copy you give
people, along with the rest of your side of the program (see *What people get*
below).

---

## 1. Once: your Glasswork Home (about five minutes)

Your Home is one folder on your own website. It does three jobs:

- **A sealed postbox.** When someone sends a problem report, registers, or
  their copy says "I'm in use", it is sealed on *their* computer so that only
  *your* computer can open it. It waits in the postbox, unreadable, until your
  program collects it, and is then deleted from the website.
- **Updates.** Every version you put out is kept there, and every copy asks for
  the newest one.
- **A download page.** The address you give new people to get the program.

**Your users and their issues are kept on your computer, not on your website.**
Someone who got into your hosting would find sealed envelopes and nothing else.

How:

1. Click **Your users** in the menu on the left (on a phone: *More → Your
   users*). Type your website (`https://www.worlddealtechnicalservices.com`).
   If you like, add the email that should hear "a report is waiting" (the email
   never says what the report says). Press **Make it**, and type the password
   you sign in with when it asks.
2. Press **Download glasswork-home.zip**. Unzip it (right-click → *Extract All*).
3. In your hosting's **File Manager**, open your website's main folder (the one
   with your home page in it, usually `public_html`) and upload the
   **glasswork-home** folder there. If there is one from before, replace it.
4. Back in Your users, press **Check it**. It should say *Your Home answers and
   knows you*, and the room opens.

Your download page is then `https://www.worlddealtechnicalservices.com/glasswork-home/`.

### Your three keys — back them up

Made for you and kept on your computer, in the `.glasswork-publisher` folder in
your Windows user folder (`C:\Users\<you>\.glasswork-publisher`):

- **postbox.key** — opens what people send you, and locks your records.
  Without it, nobody (you included) can open your records or waiting envelopes.
- **signing.key** — signs every version. Every copy refuses an update that is
  not signed with it. Lose it and installed copies cannot be updated again.
- Your **Home key** (in `config/publisher.local.json`) — lets your program
  collect, answer and put versions out. The website only holds a fingerprint
  of it, not the key itself.

**Copy the `.glasswork-publisher` folder to a USB stick** and keep it safe.
Your records themselves are in the program's `data/publisher` folder; copy that
too if you want a backup of who your users are.

## 2. Each new version

When a round of work is finished, the version number goes up (it is in
`config/product.json`; Claude raises it with each round). Then:

1. Open your program as usual — it brings itself up to date first.
2. **Settings → Publisher → Put out version 0.x.0.** Write a line about what is
   new — people read it in the update message.
3. Tick **Build the Windows installer too** (on your Windows computer; the first
   time it installs Inno Setup and takes ten minutes or so, after that two or three).
4. Press **Put it out**.

Every copy of the program shows *"Version 0.x.0 is ready — Update now"* the
next time it opens. Their data, websites and settings are never touched, and
they can go back to the previous version from Settings if they need to.

A version can only go out once; the next one needs a higher number.

## 3. New people: one link

Send them your download page (section 1). They press **Download for Windows**,
double-click the file, and follow the steps:

1. Welcome, and the terms.
2. **Where to install the program** (the suggested folder is fine).
3. **Where to keep their work** — accounts, websites, leads and settings live
   there, apart from the program, so updating or removing the program never
   touches them.
4. A desktop shortcut, if they want one. Install. *Open Glasswork now.*

**Everything it needs is inside that one file.** The Setup carries its own
Python, its own Node and every library, so nothing else downloads and no
administrator password is needed. The only thing each person adds themselves is
their own AI key (see section 5).

**The blue "Windows protected your PC" box — no subscription needed.** The
installer is not signed with a paid certificate, by choice. The first time,
Windows says it does not recognise the publisher; people click **More info →
Run anyway** and the installer opens as normal. Your download page shows these
steps right under the button. Updates installed from inside the program never
show it — only a fresh download does.

## 4. Your users and their issues

**Your users** is in the menu on your own copy only. Each time you open it, it
collects whatever is waiting in your postbox (or press **Check for new**), opens
it on your computer and files it in your records. It has two tabs.

**People** — every copy of the program in use:

- Each copy has a random number of its own. Once a day it tells you — sealed —
  that number, its version and the kind of computer, nothing else. That is how
  you see how many copies are in use, which version each has, and when each was
  last opened. (A person can switch this off in their *Help and reports*.)
- Their **name, business, email, phone, website and country** appear only if
  they choose **Register your copy**. They can change it or withdraw it; a
  withdrawal removes their details from your records the next time you collect.
- Filters: everyone, registered, with open issues, on an older version. Open
  someone to see their details (email and phone are clickable), their copy, and
  every issue they sent. *Remove this person* deletes them from your records.

**Issues** — every problem report, newest first, with who sent it. Open one to
read what they wrote, the version they have and (if they ticked it) their
support report — which has every key, password, email, web address and phone
number taken out before it leaves their computer. Choose where it stands
(*Read*, *Being worked on*, *Fixed*, *Not changing*), write an answer, say in
which version it is fixed, and press **Save and send**. Your answer is sealed to
that person's copy; they read it in their *Help and reports* screen, and if the
fix is in a newer version than theirs they are told to update. If your website
does not answer at that moment, the answer goes the next time you collect.

## 5. What people get — and what they do not

**What they get:** the program itself — the agency, its screens, its own Python
and Node — and the *public* halves of two of your keys: one to check your
signature on updates, one to seal what they send you. A public key can only
check or seal; it cannot sign anything or open anything.

**What they never get:** your users room, your inbox, putting versions out, the
Home's own code, this guide, your keys, your records, your settings. None of it
is in the Setup or in any update. On their copy the room does not exist, and
the office answers "That is not part of this copy" if anything asks for it.

**Nothing in their copy can reach your computer.** Your program goes to your
website and collects; nothing ever calls in. The office only listens on the
computer it runs on.

**About "nobody can read the code".** The sealed envelopes and your records
cannot be opened without your postbox key — not by guessing, not by any
computer or AI. The program's *code* is different: any program that runs on
someone's computer can, with enough effort, be read on that computer, because
the computer has to understand it to run it. That is why nothing secret is in
it. The code is also currently released under the GNU GPL licence, which lets
people read and share it; if you want it closed, that is a licence decision to
make before launch.

**Each person sets up their own:**

- **AI key.** Each copy thinks with the person's own key (Anthropic, OpenAI or
  Google — the *How your agency thinks* page) and pays that company directly.
  You are never billed for someone else's use.
- **Website connection**, with its own secret — never the same for two people.
  A hand-built (PHP/HTML) website: Settings → Website and connection →
  *Download your connector*, upload the `agency-api` folder once, press Check.
  A WordPress website: nothing to upload — *Users → Profile → Application
  Passwords* in WordPress, make one called "Glasswork", paste it with their
  username.
- **Maps, Search Console, email** — their own keys on the Setup screen, each
  optional.
