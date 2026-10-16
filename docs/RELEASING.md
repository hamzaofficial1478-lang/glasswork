# Putting your program out — the owner's guide

This guide is for your own copy only. It is left out of every copy you give
people, along with the rest of your side of the program (see *What people get*
below). The program is closed source: the repository is private and the
`LICENSE` file says nobody may copy, share or change it without your written
permission.

---

## 1. Once: your Botix Home (about five minutes)

**The program is yours; the websites it looks after are your clients'.**
`worlddealtechnicalservices.com` is a client's website you manage — it is not
the program's, and the program's Home never goes on it (the program refuses
any website it looks after). The Home needs a website that belongs to the
program: a domain you buy for it (for example `yourprogram.com`), or a
subdomain of a website you personally own (`app.yourcompany.com`). Any ordinary
hosting plan with PHP will do — the Home is one PHP file and a few folders.
When you move to a VPS later, the same folder goes on it unchanged.

Your Home is one folder on the program's own website. It does three jobs:

- **A sealed postbox for problem reports.** A report is sealed on the sender's
  computer so that only *your* computer can open it. It waits in the postbox,
  unreadable, until your program collects it, and is then deleted from the
  website.
- **Updates.** Every version you put out is kept there, and every copy asks
  for the newest one.
- **A download page.** The address you give new people to get the program.

**Nothing tracks the people using it.** No copy has a number, nothing counts
the copies in use, and nobody registers. Looking for an update sends nothing
about the person; a report carries only what they wrote, the version and the
kind of computer, and two reports from the same person cannot be tied
together.

How:

1. Click **Reports** in the menu on the left (on a phone: *More → Reports*).
   Type the program's own website (`https://www.yourprogram.com`). If you like,
   add the email that should hear "a report is waiting" (the email never says
   what the report says). Press **Make it**, and type the password you sign in
   with when it asks.
2. Press **Download glasswork-home.zip**. Unzip it (right-click → *Extract All*).
3. In that website's hosting **File Manager**, open its main folder (the one
   with its home page in it, usually `public_html`) and upload the
   **glasswork-home** folder there. If there is one from before, replace it.
4. Back in Reports, press **Check it**. It should say *Your Home answers and
   knows you*, and the room opens.

Your download page is then `https://www.yourprogram.com/glasswork-home/` — the
only link you give people. No client's website is ever named to them.

### Your keys — back them up

Made for you and kept on your computer, in the `.glasswork-publisher` folder in
your Windows user folder (`C:\Users\<you>\.glasswork-publisher`):

- **postbox.key** — opens the reports, and locks your records. Without it,
  nobody (you included) can open your records or waiting reports.
- **signing.key** — signs every version. Every copy refuses an update that is
  not signed with it. Lose it and installed copies cannot be updated again.
- Your **Home key** (in `config/publisher.local.json`) — lets your program
  collect, answer and put versions out. The website holds only a fingerprint
  of it, not the key itself.
- **licence.key** — made when you switch plans on (section 3b). Your Home
  holds it too, to sign each customer's plan. Lose it and you can make a new
  one, but every customer then needs the next version before their key works.

**Copy the `.glasswork-publisher` folder to a USB stick** and keep it safe.
Your records are in the program's `data/publisher` folder; copy that too if you
want a backup of the reports and your answers.

## 2. Each new version

When a round of work is finished, the version number goes up (it is in
`config/product.json`; Claude raises it with each round). Then:

1. Open your program as usual — it brings itself up to date first.
2. **Settings → Publisher → Put out version 0.x.0.** Write a line about what is
   new — people read it in the update message.
3. Tick **Build the Windows installer too** (on your Windows computer; the first
   time it installs Inno Setup and takes ten minutes or so, after that two or three).
4. Press **Put it out**.

Every copy shows *"Version 0.x.0 is ready — Update now"* the next time it opens
(and every six hours while it stays open). Only a version signed with your key
is installed. Their data, websites and settings are never touched, and they can
go back to the previous version from Settings.

A version can only go out once; the next one needs a higher number.

### The website side updates itself too

Each new version also carries the newest **connector file** (for websites
connected with a connector) and the newest **Botix helper plugin** (for
WordPress websites). Nobody uploads either again: the first time Botix talks to
a website after updating, it sends the newer copy, the website checks it is
whole, newer and runnable, keeps the old one, and puts it back if the new one
does not answer. The change list on the Website screen shows it happened.

### Every change works on both kinds of website

Anything that changes or checks a person's website is built for both ways of
connecting — the connector file (PHP and HTML sites) **and** WordPress (its own
REST API plus the Botix helper plugin). The tests hold the program to it:
`pipelines/fixer_tests.py` runs the Fix button on a PHP website and
`pipelines/wp_fix_tests.py` on a stand-in WordPress with the real helper plugin,
and the second fails if a fix is added without a WordPress route. When the
helper needs something new, its version goes up (`const BOTIX_HELPER` in
`paste-into-your-site/botix-helper.php` and `WP_HELPER_NEWEST` in
`pipelines/site_api.py`) so every WordPress site takes it by itself.

## 3. New people: one link

Send them your download page (section 1). They press **Download for Windows**,
open the file, and follow the steps: the terms, where to install the program,
where to keep their work, a desktop shortcut. Everything it needs is inside
that one file — its own Python, its own Node and every library — so nothing
else downloads and no administrator password is needed.

**The blue "Windows protected your PC" box.** The installer is not signed with a
paid certificate, by choice. The first time, people click **More info → Run
anyway**; your download page shows these steps under the button. Updates
installed from inside the program never show it.

## 3b. Plans, keys and payments

"will the program be used as the plan the user buy" — yes, once you switch
plans on. Every version you put out from then on asks for a key the first
time it opens on a computer, and keeps to the plan bought with it, whichever
AI the customer uses. Your own copy never asks.

**Switch it on (once).**

1. Download your Home again (Settings → Publisher → *Download it again*) and
   upload it over the old one — the newest one knows about plans. Your
   hosting's PHP needs *sodium* (PHP 7.2 or newer has it).
2. Open **Customers** and press **Switch plans on**. A licence key is made on
   this computer and given to your Home. Your three plans — Starter, Growth,
   Pro, as on your website — are put there too. Change any number under
   **Plans and payments**.
3. Put a version out (section 2). Copies older than that version never ask
   for a key.

**Stripe, so purchases, upgrades and cancellations arrive by themselves.**

1. In Stripe, make a product for each plan and a monthly price for each
   (with your own AI key, and with Botix AI — six prices). On each price,
   switch on a 14-day trial if you want one.
2. Make a **Payment Link** for each price. Under *After payment*, choose
   *Don't show confirmation page* and paste the **welcome address** from
   Customers → Plans and payments. The buyer lands on a page with their key.
3. In Stripe → Developers → **Webhooks**, add the **webhook address** from the
   same screen, with the events `checkout.session.completed`,
   `customer.subscription.created`, `customer.subscription.updated`,
   `customer.subscription.deleted` and `invoice.payment_failed`. Copy its
   **signing secret** (it starts `whsec_`) into Plans and payments.
4. Under *Which Stripe price is which plan*, paste each price's id
   (`price_…`) and choose its plan.
5. Switch on Stripe's **customer portal** and paste its link: that is where
   customers renew, upgrade and cancel, and the program links to it.

From then on: a purchase makes a customer and a key; an upgrade or downgrade
changes their plan at once (the date is kept as "plan since"); a renewal
moves their paid-until date; a cancellation stops their copy — at once, or at
the end of the month they paid for, whichever you choose under *When a
customer cancels* (if you change it, change the terms to match). A trial is
given once per email address.

**A plan sold by hand** (a bank transfer): Customers → **New customer, paid by
hand**. The key is shown once — pass it on. Add time to it when they pay
again.

**One key, one computer.** A key runs on one computer at a time. Entered on a
second one, it asks to move there; it can move three times a month (you can
change that), and a copied folder running at the same time is refused. If a
customer has a new computer and has used their moves, open them in Customers
and press **Clear their computers**. It is not tied to an internet address:
those change daily and a whole office shares one.

**Without the internet** a copy carries on for three days within its plan,
then pauses until it can check again. Work done offline is counted when it is
back.

**What it cannot do.** The program runs on the customer's own computer. That
stops a key being passed round, which is how plans are lost in practice, but
someone skilled enough to rewrite the program itself could get round any
check on their own machine. On your online office nothing runs on their
machine, so there the plan holds completely — one browser at a time, too.

**Botix AI.** Plans "with Botix AI" need an AI engine you provide. It is not
built yet: until it is, sell the plans with the customer's own AI key.

## 4. The reports

**Reports** is in the menu on your own copy only. Each time you open it, it
collects whatever is waiting in your postbox (or press **Check for new**), opens
it on your computer and files it in your records.

Open a report to read what they wrote, their version, the kind of computer and
(if they ticked it) their support report — which has every key, password,
email, web address and phone number taken out before it leaves their computer.
There is no name unless they wrote one, and an email only if they gave one for
a reply. Choose where it stands (*Read*, *Being worked on*, *Fixed*, *Not
changing*), write an answer, say in which version it is fixed, and press
**Save and send**. Your answer is sealed to that one report; they read it in
their *Help and reports* screen, and if the fix is in a newer version than
theirs they are told to update. If your website does not answer at that
moment, the answer goes the next time you collect.

### What "room hooks" means

The Reports room lives in its own file, `ui/publisher.js`, which only your copy
has. The main screen (`ui/app.html`) has a few small places where that file can
plug itself in — the **room hooks**: a menu entry, a line on the phone's *More*
page, a card in Settings, and the screen itself. On your copy the office says
"this is the developer's copy", the main screen loads `publisher.js`, and the
room appears. On everyone else's copy the file does not exist, so the hooks
stay empty and the room is simply not there.

## 5. What people get — and what they do not

**What they get:** the program itself — the agency, its screens, its own Python
and Node — and the *public* halves of two of your keys: one to check your
signature on updates, one to seal a report to you. A public key can only check
or seal; it cannot sign anything or open anything.

**What they never get:** the Reports room, putting versions out, the Home's own
code, these guides, the agency handbook you use with Claude Code, your client's
own files, your keys, your records, your settings. None of it is in the Setup
or in any update (`coordination/product.py`, `PUBLISHER_ONLY`). On their copy
the office answers "That is not part of this copy" if anything asks for it.

**Nothing in their copy can reach your computer, or anyone else's.** Your
program goes to your website and collects; nothing ever calls in. Each copy's
office listens only on the computer it runs on, and each account on that
computer is locked with its own password.

**About reading the code.** The reports and your records cannot be opened
without your postbox key — not by guessing, not by any computer or AI. The
program's *code* is different: any program that runs on someone's computer
can, with enough effort, be read there, because the computer has to understand
it to run it. That is why nothing secret is in it, and why the licence forbids
copying it.

**Each person sets up their own:** their AI key (they pay that company
directly), their website connection with its own secret, and any maps, Search
Console or email keys — each optional.
