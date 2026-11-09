# The backend — plans, keys and payments

Botix Home (`publish-to-your-site/glasswork-home/`, on the program's own
website) is the backend. Customers, plans, keys and payments are managed
there, by the owner's dashboard. **None of this is in the program**: a copy of
Botix only enters a key, shows the customer their plan and keeps to it.

Everything below is in `licences.php`. Answers are JSON. Data is kept in
`glasswork-home/private/licences/`, as PHP files that cannot be read by
address.

## Who may call what

| Caller | Routes | How it proves who it is |
|---|---|---|
| A customer's copy | `licence-activate`, `licence-check`, `licence-use`, `licence-release` | its key and its computer's random number |
| Stripe | `stripe` | Stripe's signature, checked against the webhook secret |
| A buyer, after paying | `welcome` | the Stripe checkout session in the address |
| Anyone | `licence-public` | nothing — it returns only the public half of the plan key |
| The owner's dashboard | `licence-setup`, `licence-settings`, `licence-list`, `licence-new`, `licence-change`, `licence-key`, `licence-unbind` | the Home key: `Authorization: Bearer <key>` (or `X-Home-Key: <key>`) |

The Home key is the `home_key` in the owner's `config/publisher.local.json`;
the Home keeps only its SHA-256 fingerprint. A dashboard keeps it on the
server, never in a page sent to a browser.

All routes are `…/glasswork-home/index.php?do=<route>`.

## The owner's routes (for the dashboard)

**`licence-setup`** — GET: `{ready, public_key, sodium}`. POST `{}`: switches
plans on by making the plan key here (its private half never leaves this
server). POST `{"rotate": true}` replaces it — every customer then needs the
next version before their plan is accepted, so only if the old key leaked.

**`licence-settings`** — GET: the settings. POST any of:

| Field | Meaning |
|---|---|
| `plans` | `{id: {name, websites, post_words, devices, features: ["own_logo"], limits: {leads, mails, posts, checks: {n, per: "day"\|"period", scope: "site"\|"account"}}}}` — the order is the order of rank (for "upgraded" / "downgraded"). Until saved, the website's Starter, Growth and Pro. |
| `prices` | `{"price_…": {plan, variant: "own"\|"ai"}}` — which Stripe price is which plan |
| `stripe_secret` | the webhook signing secret (`whsec_…`); written, never read back (`has_stripe_secret` says whether one is saved) |
| `stripe_api_key` | optional: a Stripe **restricted** key (`rk_…`) with Subscriptions: write, Refunds: write, Invoices: read — used only to undo a plan bought twice by mistake; written, never read back (`has_stripe_api_key`) |
| `cancel_effect` | `"now"` (stop when they cancel) or `"period_end"` (carry on to the end of the paid month) |
| `grace_days`, `moves_per_month`, `offline_days`, `trial_days` | 0–60 |
| `pricing_url`, `portal_url` | shown to customers on their plan card (the plans page; Stripe's customer portal) |
| `mail_from` | if set, the key is also emailed to the buyer from this address (PHP `mail()`) |

The answer also carries `webhook_url` and `welcome_url`, to paste into Stripe.

**`licence-list`** — GET: `{licences: [...], plans}`. Each licence: `id, email,
name, plan, plan_name, variant, status` (trial, active, past_due, cancelled,
expired, trial_used, pending)`, source` (stripe or manual)`, key_hint,
bought_at, plan_since, period_start, period_end, cancel_at, cancelled_at,
computers [{name, first, seen}], moves, websites, today, period, history
[{at, what, plan, note}]`. Times are Unix seconds.

**`licence-new`** — POST `{plan, email, name, days, variant, status: "trial"|"active", note}`:
a plan sold outside Stripe. Returns `key` — shown once; only its fingerprint is kept.

**`licence-change`** — POST `{id, …}` with any of `plan`, `variant`,
`extend_days`, `until` (Unix time), `restart` (a new paid month),
`status: "cancelled"|"active"`, `email`, `note` (goes in the history).

**`licence-key`** — POST `{id}`: a new key; the old one stops at once, everywhere.

**`licence-unbind`** — POST `{id, websites: true|false}`: clears their
computers (a new laptop) and, if asked, the websites counted against the plan.

## Stripe

1. A product for each plan, and a monthly price for each (own AI key and Botix
   AI). A 14-day trial on each price if wanted.
2. A Payment Link for each price; after payment, redirect to `welcome_url`.
3. Developers → Webhooks: `webhook_url`, with the events
   `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted` and
   `invoice.payment_failed`. Its signing secret goes in `stripe_secret`.
4. Each price id in `prices`; Stripe's customer portal link in `portal_url`.
5. Optional, recommended: a restricted key in `stripe_api_key` (see the table above).

Then a purchase makes a customer and a key; an upgrade or downgrade changes the
plan at once (`plan_since`); a renewal moves `period_end`; a cancellation stops
the customer's copy at once or at the end of the paid month (`cancel_effect`).
One trial per email address. Messages without a valid signature, or older than
five minutes, change nothing.

**Bought twice by mistake.** A double click on a buy button opens the same
Stripe page, and Stripe's own Pay button takes one press, so nobody is charged
twice that way. Paying twice — two tabs, or paying again a minute later —
makes two subscriptions; the backend catches it: the same plan (same amount),
the same email, within an hour of the first, while the first is active. The
second gets no key and is marked `duplicate` (`duplicate_of` names the first);
with `stripe_api_key` set it is cancelled and its payment refunded at once,
without it the history says to refund it in Stripe. The buyer's thank-you page
says "You already have this plan" and whether the money is back. A different
plan, or a second one later, is a second plan on purpose and gets its own key.

## What a customer's copy sends

Its key; a random number made when it was installed (nothing about the person
or the machine); the name the customer gave that computer; its date; and for
each job only its kind and a count, with a one-way fingerprint of the website
it was for. Never the lead, the email, the post or the website's address. It
gets back the plan, signed with the plan key, which lets it work offline for
`offline_days`.

## What the program keeps of this

Nothing of the admin side. A release carries only the PUBLIC half of the plan
key, picked up from `licence-public` when the owner puts a version out — with
plans off, there is none, and nobody is asked for a key.
