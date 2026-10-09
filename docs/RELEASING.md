# Putting your program out — the owner's guide

Everything here is done from your own copy of the program — **Your users** in
the menu, and **Settings → Publisher**. Your copy is the one the program is made
from; everyone else's copy is installed from the Setup.exe you put out.

---

## 1. Once: your Glasswork Home (about five minutes)

Your Home is one folder on your own website. It does three jobs:

- **Reports** — when someone using the program presses *Help and reports → Send
  the report*, it arrives here, and in your email if you give one.
- **Updates** — every version you put out is kept here, and every copy of the
  program asks it once a day whether there is a newer one.
- **A download page** — the address you give new people to get the program.

How:

1. Click **Your users** in the menu on the left (on a phone: *More → Your
   users*). Type your website (`https://www.worlddealtechnicalservices.com`)
   and the email that should hear about new reports. Press **Make it**, and
   type the password you sign in with when it asks.
2. Press **Download glasswork-home.zip**. Unzip it (right-click → *Extract All*).
3. In your hosting's **File Manager**, open your website's main folder (the one
   with your home page in it, usually `public_html`) and upload the
   **glasswork-home** folder there.
4. Back in Your users, press **Check it**. It should say *Your Home answers and
   knows you*, and the room opens.

(The same thing is in **Settings → Publisher**, with *Make my Glasswork Home*.
After updating the program, close it and open it again if Your users is not in
the menu.)

Your download page is then `https://www.worlddealtechnicalservices.com/glasswork-home/`.

Two keys are made for you and kept on your computer:

- **Your Home key** (in `glasswork-home/home-key.php` on the website, and in
  `config/publisher.local.json` on your computer). It is how the Home knows it
  is you. Nobody else can read a report or put out a version without it.
- **Your signing key** (`.glasswork-publisher/signing.key` in your Windows user
  folder). Every version is signed with it, and every copy of the program
  refuses an update that is not. **Back it up somewhere safe** (a USB stick).
  If you lose it, copies already installed cannot be updated again — people
  would have to reinstall.

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

Send them your download page (step 1). They press **Download for Windows**,
double-click the file, and follow the steps:

1. Welcome, and the terms.
2. **Where to install the program** (the suggested folder is fine).
3. **Where to keep their work** — accounts, websites, leads and settings live
   there, apart from the program, so updating or removing the program never
   touches them.
4. A desktop shortcut, if they want one. Install. *Open Glasswork now.*

Nothing else needs installing: the program carries its own Python and Node,
and needs no administrator password.

**The blue "Windows protected your PC" box — no subscription needed.** The
installer is not signed with a paid certificate, by choice. The first time,
Windows says it does not recognise the publisher; people click **More info →
Run anyway** and the installer opens as normal. Your download page shows these
steps right under the button. Because every version is a new file to Windows,
the box can appear again for each new Setup.exe — but updates installed from
inside the program never show it, only a fresh download does.

## 4. Your users and their issues

**Your users** is always in the menu on your own copy (the one the program is
made from — copies installed from your Setup.exe do not have it). Until your
Home is on your website, it walks you through section 1; after that it has two
tabs. What it shows lives on your website's Home, so it is the same on any
computer you set up as the publisher.

**People** — every copy of the program in use:

- Each copy has a random number of its own. When it looks for updates it tells
  your Home that number, its version and the kind of computer — nothing else.
  That is how you see how many copies are in use, which version each has, and
  when each was last opened. (A person can switch this off in their *Help and
  reports* screen.)
- Their **name, business, email, phone, website and country** appear only if
  they choose **Register your copy** (offered once on their Today screen, and
  always in *Help and reports*). They can change it or withdraw it at any time.
- Filters: everyone, registered, with open issues, on an older version. Open
  someone to see their details (email and phone are clickable), their copy, and
  every issue they sent. *Remove this person* deletes their details from your Home.
- The tiles at the top: copies in use this week, registered, open issues, and
  how many are on the newest version.

**Issues** — every problem report, newest first, with who sent it. Open one to
read what they wrote, the version they have and (if they ticked it) their
support report — which has every key, password, email, web address and phone
number taken out before it leaves their computer. Choose where it stands
(*Read*, *Being worked on*, *Fixed*, *Not changing*), write an answer, and if
it is fixed say in which version. They see your answer in their *Help and
reports* screen, and if the fix is in a newer version than theirs they are told
to update. A new report is also emailed to you, if you gave an email.

## 5. What each person sets up themselves

**Their own AI key.** Each person's copy thinks with their own key (Anthropic,
OpenAI or Google — the *How your agency thinks* page) and pays that company
directly. Nothing is shared, and you are never billed for someone else's use.

**Their own website connection**, with its own secret — never the same for two
people:

- **A website built by hand (PHP or HTML)** — Settings → Website and connection
  → *Download your connector*. The zip holds the connector file and a secret
  made just for that person. They upload the `agency-api` folder once, next to
  their home page, and press Check.
- **A WordPress website** — nothing to upload. In WordPress, *Users → Profile
  → Application Passwords*, they make a password called "Glasswork" and paste it
  with their username. WordPress keeps it separate from their real password and
  they can take it back at any time. The program reads and changes pages and
  posts through WordPress's own REST API; WordPress keeps every old version of a
  page as a revision, which is how a change is undone. What WordPress keeps out
  of reach — a theme's header and footer, a page builder's own storage — is
  handed to them as a short instruction instead.

**Maps, Search Console, email** — each person's own keys on the Setup screen,
each optional.
