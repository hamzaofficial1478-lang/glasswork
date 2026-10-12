# The TechDuo website

The `website/` folder is the TechDuo company site: plain files, no server
code, nothing to install. It stays on your computer and is never part of a
copy of Botix.

| Page | What it is |
|---|---|
| `index.html` | TechDuo: who we are, our first product, how we work, contact |
| `botix.html` | Botix: what it does, the opening animation, how a day works, screens, privacy, setup, using it, pricing, questions |
| `privacy.html` | Privacy notice for the website and a summary of Botix's (draft) |
| `terms.html` | Terms for the website and the Botix subscription (draft) |

## Before it goes up

1. **Confirm the prices.** The plans on `botix.html` (Startup, Growth, Pro;
   own AI key or Botix AI) are proposals. Change any number in the
   `<article class="panel plan">` blocks, or tell Claude what to change.
   Delete the `PROPOSED PRICES` note above them once you are happy.
2. **Fill in two lines** at the top of `assets/site.js`:
   - `botix:` the address where Botix runs online, e.g. `'https://app.yourdomain.com'`.
     Every "Start your 14-day trial" button then opens its sign-up page.
     While it is empty, those buttons lead to the Contact section.
   - `email:` the address people write to. While it is empty, Contact says
     the address is coming soon.
3. **Have the drafts read.** `privacy.html` and `terms.html` say "Draft for
   review" at the top. Add your registered company details and remove that
   line once a lawyer has read them.
4. **Botix AI and subscriptions.** The pricing section offers Botix AI and
   a 14-day trial. Both still have to be built in the program (with a payment
   provider) before the site can promise them.

## Putting it on your hosting

Upload everything inside `website/` (the four `.html` files and the `assets`
folder) to the folder your domain shows, keeping the folder structure. Open
the address in a browser and click through every page.

The site loads its fonts from Google Fonts and its 3D drawing code from
cdnjs; nothing else leaves the visitor's browser. There are no cookies,
analytics or trackers, and the test `python -m coordination.website_tests`
checks that this stays true.
