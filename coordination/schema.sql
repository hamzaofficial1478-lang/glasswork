-- THE FILING CABINET
-- One shared store every agent reads from and writes to (through board.py).
-- SQLite to start (zero setup, runs anywhere). Swap to Postgres when you outgrow it.

-- ---- coordination ---------------------------------------------------------

CREATE TABLE IF NOT EXISTS tasks (
  id          TEXT PRIMARY KEY,
  sector      TEXT NOT NULL,           -- IT | Content | SEO | Leads | Report | Security
  title       TEXT NOT NULL,
  detail      TEXT,
  risk        TEXT DEFAULT 'green',    -- green (auto) | amber (needs approval) | red (owner-led)
  status      TEXT DEFAULT 'open',     -- open | in_progress | done | failed | blocked
  owner       TEXT,                    -- which agent claimed it (NULL until claimed)
  result      TEXT,
  created_by  TEXT,
  created_at  INTEGER,
  claimed_at  INTEGER,
  finished_at INTEGER,
  about       TEXT,                    -- the row this exists because of, e.g. 'site_issues:7'
  desk        TEXT                     -- the desk it was posted for, e.g. 'it-fixer'
);

-- Which models are mid-call RIGHT NOW, across every process in the building.
-- providers/load.py writes a row before a call and deletes it after, so a
-- second agent can see that the primary is busy and take the fallback instead
-- of queueing behind it. expires_at is the backstop for a process that dies
-- holding a row.
CREATE TABLE IF NOT EXISTS model_inflight (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  model_id      TEXT NOT NULL,
  credential_id TEXT,
  task          TEXT,
  holder        TEXT,
  started_at    INTEGER,
  expires_at    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_inflight_model ON model_inflight(model_id);

CREATE TABLE IF NOT EXISTS locks (
  resource    TEXT PRIMARY KEY,        -- live_site | publish_pipeline | page:<slug> ...
  holder      TEXT NOT NULL,
  acquired_at INTEGER,
  expires_at  INTEGER,
  -- Which process is holding it. A lease alone cannot tell "an agent is busy"
  -- from "the owner closed the program three hours ago", and only the second
  -- of those actually happens. See board.sweep_dead_processes.
  pid         INTEGER
);

CREATE TABLE IF NOT EXISTS requests (
  id          TEXT PRIMARY KEY,
  from_sector TEXT NOT NULL,
  to_sector   TEXT NOT NULL,
  subject     TEXT,
  status      TEXT DEFAULT 'open',     -- open | claimed | answered | closed
  handler     TEXT,
  created_at  INTEGER,
  -- WHEN it was claimed, so a claim can be told apart from a dead one. A
  -- request stuck at 'claimed' is invisible to the sector that owes the
  -- answer (its inbox lists 'open') while the Overseer counts it as that
  -- sector ignoring the question — which is exactly what ran for nineteen
  -- days. See board.release_dead_request_claims.
  claimed_at  INTEGER
);

CREATE TABLE IF NOT EXISTS request_msgs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id  TEXT NOT NULL,
  author      TEXT NOT NULL,
  body        TEXT,
  at          INTEGER
);

-- ---- the MD's memory & scorecard ------------------------------------------

CREATE TABLE IF NOT EXISTS decisions (
  id          TEXT PRIMARY KEY,
  cycle       TEXT,
  reasoning   TEXT,
  expectation TEXT,
  confidence  REAL,
  outcome     TEXT,                    -- worked | failed | partial (set later by Report)
  created_at  INTEGER,
  scored_at   INTEGER,
  evidence     TEXT,                   -- json: WHICH rows this decision is a claim about
  outcome_note TEXT                    -- why Report scored it the way it did
);

-- ---- who's doing what (Overseer + cost governor) --------------------------

CREATE TABLE IF NOT EXISTS agent_runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  agent       TEXT,
  task_id     TEXT,
  started_at  INTEGER,
  ended_at    INTEGER,
  tokens_in   INTEGER DEFAULT 0,
  tokens_out  INTEGER DEFAULT 0,
  usd_cost    REAL DEFAULT 0,
  -- running | ok | error | killed | stalled
  --
  -- 'stalled' is written by the Overseer (coordination/faults.py), never by the
  -- agent — an agent that could still write a status would not be stalled. It
  -- exists because 'running' is a CLAIM about right now, and a claim nobody
  -- ever retracts becomes a lie: the activity log re-derived "never came back"
  -- from the same dead row every time it was read, so a desk that had long
  -- since recovered still showed as broken. Reaping it turns an eternal
  -- present tense into something that happened once, at a known time.
  status      TEXT,
  note        TEXT,                    -- one plain line: what it actually did
  -- WHICH PROCESS. 'running' is a claim about right now, and the commonest
  -- way it becomes false is the owner closing the window — the process is
  -- gone in an instant while the row says it is working for hours. A pid
  -- turns "how long has this been going" into "is this still running", which
  -- has an answer. See board.sweep_dead_processes.
  pid         INTEGER,
  host        TEXT
);

-- every tool action, from the hooks — nothing happens in the dark
CREATE TABLE IF NOT EXISTS audit_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  agent       TEXT,
  tool        TEXT,
  args        TEXT,
  decision    TEXT,                    -- allowed | blocked
  reason      TEXT,
  at          INTEGER
);

-- ---- SEO ------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS keywords (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  phrase   TEXT, area TEXT, intent TEXT   -- e.g. 'aluminium windows' / 'Al Quoz' / 'buy'
);
CREATE TABLE IF NOT EXISTS rankings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword_id INTEGER, position INTEGER, url TEXT, checked_at INTEGER
);

-- ---- competitors ----------------------------------------------------------

CREATE TABLE IF NOT EXISTS competitors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT, domain TEXT, gbp_reviews INTEGER,
  -- 'suggested' = the SEO desk saw people searching for this firm by name and
  -- is asking whether it counts as a rival. NULL or 'confirmed' = he said yes,
  -- or he added it himself. Only confirmed rivals are ever crawled.
  status TEXT,
  evidence TEXT,                       -- the searches that put it here
  first_seen INTEGER
);
CREATE TABLE IF NOT EXISTS competitor_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  competitor_id INTEGER, sitemap_urls TEXT, reviews INTEGER,
  new_pages TEXT, notes TEXT, taken_at INTEGER
);

-- ---- content --------------------------------------------------------------

CREATE TABLE IF NOT EXISTS content_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT, type TEXT,               -- service_page | case_study | comparison | blog | social
  brief TEXT, draft TEXT, images TEXT,
  -- blocked_on_<sector> means the brief is finished but held for that sector's
  -- call (e.g. blocked_on_seo, waiting on volume/difficulty). It is done work
  -- waiting on someone else, not an idea.
  status TEXT DEFAULT 'idea',          -- idea | briefed | blocked_on_<sector> | drafted | in_review | approved | published
  channel TEXT, created_at INTEGER
);

-- ---- leads ----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT, company TEXT, email TEXT, phone TEXT, socials TEXT,
  source TEXT, area TEXT, segment TEXT,   -- fitout | contractor | developer | villa_owner ...
  email_valid INTEGER DEFAULT 0,
  score INTEGER,
  stage TEXT DEFAULT 'new',            -- new | contacted | replied | qualified | won | lost | nurture
  found_at INTEGER,
  website TEXT,                        -- where the details below were read from
  whatsapp TEXT,                       -- E.164, ONLY from a click-to-chat button they published
  address TEXT,                        -- as they printed it
  contact_kind TEXT,                   -- role inbox | named person | phone only
  evidence TEXT,                       -- JSON: where each detail came from and how
  -- Normalised identity, so the same firm written three ways is one lead.
  -- See board.phone_key / domain_key / company_key.
  phone_key TEXT,                      -- last 9 digits, format-independent
  domain_key TEXT,                     -- their website's registered domain
  company_key TEXT                     -- name with punctuation and LLC/FZE off
);
CREATE INDEX IF NOT EXISTS leads_phone_key ON leads(phone_key);
CREATE INDEX IF NOT EXISTS leads_domain_key ON leads(domain_key);
CREATE INDEX IF NOT EXISTS leads_company_key ON leads(company_key);
CREATE TABLE IF NOT EXISTS outreach_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id INTEGER, channel TEXT, direction TEXT,  -- sent | reply
  body TEXT, reply_class TEXT,          -- interested | not_now | no | question
  needs_owner INTEGER DEFAULT 0, at INTEGER
);

-- ---- site health (IT) -----------------------------------------------------

-- Drafted mail waiting on the owner. The agency does not start cold-mailing
-- real businesses on its own say-so: the FIRST batch is held here for him to
-- read, and approving it arms outreach so later batches go straight out. One
-- confirmation, then autonomous — see pipelines/leads_outreach.py.
CREATE TABLE IF NOT EXISTS outreach_queue (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id    INTEGER,
  company    TEXT,
  to_email   TEXT,
  subject    TEXT,
  body       TEXT,
  service    TEXT,
  audit      TEXT,                   -- JSON: what the Auditor said about it
  status     TEXT DEFAULT 'pending', -- pending | approved | rejected | sent | stale
  queued_by  TEXT,
  queued_at  INTEGER,
  decided_at INTEGER,
  sent_at    INTEGER,
  note       TEXT
);
CREATE INDEX IF NOT EXISTS idx_outq_status ON outreach_queue(status);

CREATE TABLE IF NOT EXISTS site_issues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,                            -- bug | speed | broken_link | form_dead | ssl | security
  severity TEXT, detail TEXT, url TEXT,
  status TEXT DEFAULT 'open',          -- open | fixing | in_review | deployed | closed
  found_by TEXT, found_at INTEGER
);
-- ---- WHAT ACTUALLY CHANGED ON THE WEBSITE ----------------------------------
-- "there should be a section in program where i can see whats changed in
--  website... that's totally blind."
--
-- He was right and it was worse than he thought. Nothing anywhere recorded a
-- change to his site. site_queue held what an agent PROPOSED; site_issues held
-- what was WRONG; neither said a word about what had actually been written,
-- when, by whom, or how to put it back. The only trace was a .bak file on his
-- server with a timestamp in its name.
--
-- TWO KINDS OF ENTRY, AND THE SECOND ONE IS THE USEFUL ONE.
--
--   'agency'   we wrote this. Recorded at the moment of the write, so it is
--              exact: the file, the block, the sizes either side, the backup
--              the server made, and the command that undoes it.
--   'outside'  the file changed and it was not us. Found by fingerprinting
--              every page and noticing the digest moved — so it catches him
--              editing in the file manager, his web designer, a plugin, and a
--              hosting company restoring last week by mistake. He asked to see
--              what changed on his website, not what the agency did to it.
--
-- Append-only, like everything else on the board. A row is never rewritten;
-- undoing a change adds a row saying so.
CREATE TABLE IF NOT EXISTS site_changes (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  at           INTEGER,
  source       TEXT,                 -- agency | outside
  agent        TEXT,                 -- who, when it was us
  action       TEXT,                 -- head_block | page | remove_block | restore
                                     -- | address | canonical | schema
                                     -- | edited | added | gone   (outside)
  page         TEXT,
  block_id     TEXT,
  summary      TEXT,                 -- one plain line, in his language
  bytes_before INTEGER,
  bytes_after  INTEGER,
  backup       TEXT,                 -- the server's own copy of what was there
  undo_cmd     TEXT,                 -- the exact command, or '' if there isn't one
  issue_id     INTEGER,              -- the ticket it came from, when there is one
  ok           INTEGER DEFAULT 1,    -- a refused write is still worth recording
  error        TEXT,
  detail       TEXT                  -- JSON list of plain lines: what actually
                                     -- changed on the page, item by item
);
CREATE INDEX IF NOT EXISTS idx_site_changes_at ON site_changes(at);

-- The last thing we saw each page look like. One row per page, overwritten —
-- this is a comparison point, not a history, and the history is site_changes.
-- Keeping snapshots instead would grow by nine rows a day for ever to answer a
-- question that only ever needs 'is it different from last time'.
CREATE TABLE IF NOT EXISTS site_fingerprints (
  page    TEXT PRIMARY KEY,
  bytes   INTEGER,
  changed TEXT,                      -- the server's own mtime, as it reports it
  digest  TEXT,                      -- sha1 of the content; mtime alone lies,
                                     -- because a touch moves it and a rewrite
                                     -- with the same length does not move bytes
  seen_at INTEGER,
  -- WHAT THE PAGE SAID LAST TIME. A digest tells you a page moved; it cannot
  -- tell you what moved, and "home.html was changed by somebody, 340 bytes
  -- bigger" is not an answer to "was an article posted, was the code changed".
  -- Keeping the last snapshot is what lets the next sweep say. Text and markup
  -- shape only, capped — this is a comparison aid, not a backup.
  snapshot TEXT
);

CREATE TABLE IF NOT EXISTS lighthouse (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT, perf INTEGER, seo INTEGER, a11y INTEGER, best INTEGER, at INTEGER
);

-- ---- queued site changes (read-only mode) --------------------------------
-- When the owner is in read-only mode the site-touching agents record what
-- they WOULD change here instead of changing the site, for the owner to approve.
CREATE TABLE IF NOT EXISTS site_queue (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  agent      TEXT,
  kind       TEXT,                        -- upsert_page | release | rollback | form_test | ...
  detail     TEXT,
  status     TEXT DEFAULT 'pending',      -- pending | approved | rejected
                                          -- | applied  (it is on the site now)
                                          -- | failed   (tried, and could not)
  queued_at  INTEGER,
  decided_at INTEGER,
  -- WHAT HAPPENED AFTER HE SAID YES.
  --
  -- There was nowhere to put it, because nothing ever acted on an approval:
  -- the row went to 'approved' and stopped there for good. He asked what full
  -- access to his website was for, and the honest answer was that his yes went
  -- into a table nobody read. pipelines/it_release.py is what reads it, and
  -- these two are where it writes down how it went.
  applied_at INTEGER,
  note       TEXT                         -- in his words: what was written, or
                                          -- why it could not be
);

-- ---- security -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS security_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,                            -- phishing | scam_lead | injection | intrusion | fraud
  severity TEXT, source TEXT, detail TEXT,
  action TEXT, at INTEGER
);

-- ---- the shelf (shared API cabinet) ---------------------------------------
-- The cabinet holds shared capability APIs (SEO data, stock graphics, email,
-- crawler, image/embedding models). An agent BORROWS a shelf: it takes the
-- shelf's single-holder lock (see the locks table, resource='shelf:<name>'),
-- so only one agent uses a given capability at a time, while other shelves run
-- in parallel. Who is waiting is recorded here; every checkout is logged below.

-- the waiting line for each shelf (the "who's in queue" the owner asked to see)
CREATE TABLE IF NOT EXISTS shelf_queue (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  shelf       TEXT NOT NULL,              -- seo_data | stock_images | email | crawler | llm | image ...
  agent       TEXT NOT NULL,
  sector      TEXT,
  reason      TEXT,                       -- the "strong reason" every borrow must carry
  data_class  TEXT DEFAULT 'internal',   -- internal | external (external => Security screens)
  status      TEXT DEFAULT 'waiting',    -- waiting | granted | cancelled
  enqueued_at INTEGER,
  granted_at  INTEGER
);

-- every checkout — the audit + cost trail (feeds the Overseer's spend cap)
CREATE TABLE IF NOT EXISTS borrows (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  shelf       TEXT NOT NULL,
  item        TEXT,                       -- which model/tool the shelf resolved to
  agent       TEXT NOT NULL,
  sector      TEXT,
  reason      TEXT,
  data_class  TEXT DEFAULT 'internal',
  approval    TEXT DEFAULT 'auto',        -- auto | escalated (needs MD/Security) | denied
  status      TEXT DEFAULT 'active',      -- active | done | denied
  usd_cost    REAL DEFAULT 0,
  granted_at  INTEGER,
  released_at INTEGER
);

-- ---- the owner's daily orders ---------------------------------------------
-- How much of each job the owner wants done per day, set on /setup. The MD
-- plans against these instead of inventing a workload, and the office floor
-- shows today's progress against them. One row per job type.
CREATE TABLE IF NOT EXISTS targets (
  name       TEXT PRIMARY KEY,     -- leads | mails | posts | site_checks
  per_day    INTEGER DEFAULT 0,    -- 0 = not today
  note       TEXT,                 -- the owner's own words for this job
  updated_at INTEGER
);

-- What the owner types for a given day, in his own words: "focus on Sharjah
-- hotels", "no outreach today, I'm on site". The MD reads it before planning
-- and is expected to follow it over its own preference.
CREATE TABLE IF NOT EXISTS daily_orders (
  day        TEXT PRIMARY KEY,     -- YYYY-MM-DD, local time
  orders     TEXT,
  updated_at INTEGER
);

-- ---- the daily digest -----------------------------------------------------

CREATE TABLE IF NOT EXISTS daily_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT, health_score INTEGER,
  sector_scores TEXT,                  -- json: which sector is winning / bleeding
  summary TEXT, actions TEXT, spend_usd REAL, created_at INTEGER
);

-- ---- the sector proposal queue --------------------------------------------
-- A sector knows its own domain better than the MD does. Rather than the MD
-- guessing every sector's work from a handful of hardcoded rules, each sector
-- PROPOSES what it thinks it needs, and the MD ranks and allocates against the
-- owner's daily orders. A proposal is a request for work, never work itself:
-- nothing here is claimable until the MD turns it into a task.
CREATE TABLE IF NOT EXISTS proposals (
  id         TEXT PRIMARY KEY,
  sector     TEXT NOT NULL,        -- who is asking
  title      TEXT NOT NULL,
  detail     TEXT,
  evidence   TEXT,                 -- the row that justifies it; no evidence, no hearing
  needs      TEXT,                 -- shelf it cannot run without (maps, llm, ...)
  urgency    INTEGER DEFAULT 5,    -- 1 highest .. 9 lowest, the SECTOR's own view
  status     TEXT DEFAULT 'open',  -- open | accepted | rejected | superseded
  task_id    TEXT,                 -- set when the MD turns it into real work
  why        TEXT,                 -- the MD's reason, always, accepted or not
  created_at INTEGER,
  decided_at INTEGER
);

-- ---- turn-taking in the other direction -----------------------------------
-- The MD allocates, but it can allocate wrongly. A sector handed work that is
-- not its job says so instead of silently dropping it or doing it badly, and
-- the objection is on the record so the MD's DIRECTION score feels it.
CREATE TABLE IF NOT EXISTS task_objections (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id    TEXT NOT NULL,
  sector     TEXT NOT NULL,        -- who is refusing it
  reason     TEXT NOT NULL,        -- wrong_sector | not_our_call | missing_capability | unclear
  detail     TEXT,
  at         INTEGER
);

-- ---- the owner's orders, per sector ---------------------------------------
-- targets holds HOW MUCH of each job. This holds WHAT the owner wants a given
-- sector to do today, in his own words. The MD must plan around it, and the
-- Critic rejects a plan that ignores a sector the owner gave orders to.
CREATE TABLE IF NOT EXISTS sector_orders (
  day        TEXT NOT NULL,
  sector     TEXT NOT NULL,
  orders     TEXT,
  updated_at INTEGER,
  PRIMARY KEY (day, sector)
);

-- ---- the remembered preflight ---------------------------------------------
-- A saved API key is not a working API key, and proving one costs a real call.
-- The sweep runs when the owner asks for it and the verdict is kept here, so
-- the sector lamps on the office floor can read it without re-testing on every
-- poll. Verdicts only — the channel details carry the ntfy topic, and the topic
-- IS the secret, so it is never written into this row.
CREATE TABLE IF NOT EXISTS preflight_runs (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  at        INTEGER,
  ready     INTEGER,               -- 1 = nothing blocking
  blocking  TEXT,                  -- json: the reasons, in the owner's words
  payload   TEXT                   -- json: the slimmed check, for the lamps
);

-- ---- one press of the Run button ------------------------------------------
-- Kept because a day that half-ran and stopped is the thing you most need to
-- see afterwards, and by then stdout has scrolled away. A run that was killed
-- outright leaves status='running' with nothing behind it; day_run.status()
-- reaps those into 'stalled' rather than showing a run that will never finish.
CREATE TABLE IF NOT EXISTS day_runs (
  id          TEXT PRIMARY KEY,
  started_at  INTEGER,
  ended_at    INTEGER,
  status      TEXT,                -- running | done | partial | failed
                                   -- | stalled  (died and was reaped)
                                   -- | stopped  (he pressed Stop, or the
                                   --             program was closed on it)
  started_by  TEXT,
  note        TEXT,                -- what the owner wants out of today
  forced      INTEGER DEFAULT 0,   -- started despite a blocking check, in writing
  blocking    TEXT,                -- json: what was wrong when he forced it
  only_sectors TEXT,               -- json: the sectors he picked for THIS run;
                                   -- empty means all of them
  steps_total INTEGER DEFAULT 0,
  steps_done  INTEGER DEFAULT 0,
  pid         INTEGER,             -- the process running it; see locks.pid
  host        TEXT                 -- and which machine that pid is on. Stop
                                   -- refuses to kill a pid it cannot place —
                                   -- pid 8814 here is somebody else's there.
);
CREATE TABLE IF NOT EXISTS day_steps (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     TEXT NOT NULL,
  seq        INTEGER,
  label      TEXT,
  module     TEXT,
  status     TEXT,                 -- waiting | running | ok | idle | failed
                                   -- | skipped (the sector is switched off)
                                   -- | stopped (he pressed Stop; a step that
                                   --   never started and a step cut off
                                   --   mid-way are both this, and the summary
                                   --   says which)
  started_at INTEGER,
  ended_at   INTEGER,
  code       INTEGER,
  summary    TEXT,                 -- one plain line: what the step actually did
  lane       TEXT                  -- which lane ran it; lanes run side by side
);

-- ---- where a link to us could come from -----------------------------------
-- One row per OPPORTUNITY, not per link acquired. That is deliberate: a place
-- we pitched and were turned down by is the row worth reviewing, and a table of
-- successes only would quietly hide every miss.
CREATE TABLE IF NOT EXISTS backlinks (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  host       TEXT NOT NULL,
  url        TEXT,
  name       TEXT,
  kind       TEXT,                  -- citation | supplier | association | guest_post | rival_gap
  why        TEXT,                  -- why this one is worth having, in plain words
  local      INTEGER DEFAULT 0,     -- 1 = a UAE source, which is the whole point
  effort     TEXT,                  -- free | form | pitch | paid
  status     TEXT DEFAULT 'idea',   -- idea | pitched | live | rejected | refused
  note       TEXT,
  found_at   INTEGER,
  decided_at INTEGER,
  UNIQUE(host, kind)
);

-- THIRD-PARTY API USAGE.
-- Counts and money are deliberately different kinds of thing and are stored
-- side by side so nobody confuses them. `n` is a fact: we know exactly how many
-- requests we made. `usd` is an ESTIMATE from the owner's own rate in .env,
-- because Zyte's price depends on the target site and the volume discount and
-- there is no single number to hardcode. Only calls the provider actually bills
-- carry money — Zyte invoices successful responses, so a failed render is one
-- request and zero dollars.
--
-- Two shapes of row live here. A COUNTER (renders bought, write operations) is
-- summed. A GAUGE (records currently in the index) is read as the latest row
-- only — summing it would have a nightly re-index of forty pages read as
-- twelve hundred records by month end. See coordination/usage.py.
CREATE TABLE IF NOT EXISTS api_usage (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  at       INTEGER,
  day      TEXT,                    -- local YYYY-MM-DD, for daily allowances
  month    TEXT,                    -- local YYYY-MM, for monthly allowances
  provider TEXT NOT NULL,           -- zyte | algolia | …
  kind     TEXT NOT NULL,           -- render | fetch | write | record | search
  n        INTEGER DEFAULT 0,
  usd      REAL DEFAULT 0,
  note     TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_usage_day   ON api_usage(provider, kind, day);
CREATE INDEX IF NOT EXISTS idx_api_usage_month ON api_usage(provider, kind, month);

-- AEO AND GEO FINDINGS — one row per page per check.
-- AEO is being picked as the answer (AI Overviews, snippets, voice). GEO is
-- being cited as the source (ChatGPT, Perplexity, Gemini). Both are audits of
-- OUR OWN pages, so every row names the sector that owns the fix: the writing
-- is Content's, the markup is IT's, and SEO files but never edits.
--
-- A check with nothing to say about a page — FAQ visibility on a page with no
-- FAQ — writes NO ROW at all. Storing it as a pass would pay a page for having
-- nothing, and storing it as a fail would put a job on the board that does not
-- exist.
CREATE TABLE IF NOT EXISTS aeo_findings (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  url          TEXT NOT NULL,
  family       TEXT,                -- aeo | geo
  check_id     TEXT NOT NULL,
  passed       INTEGER DEFAULT 0,
  weight       INTEGER DEFAULT 1,   -- how much it matters for THIS business
  detail       TEXT,                -- what was found, in plain words
  fix          TEXT,                -- what to do about it
  owner_sector TEXT,                -- Content | IT
  found_at     INTEGER,
  UNIQUE(url, check_id)
);

-- The SEO technical audit's findings, each with its full evidence contract:
-- what was seen and when, fact/inference/hypothesis, why it matters, the
-- action, and the acceptance check the next audit verifies against. The ticket
-- IT works lives in site_issues; this is the evidence behind it. Method taken
-- from BeyondSEO — see pipelines/seo_audit.py.
CREATE TABLE IF NOT EXISTS seo_findings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_at INTEGER, url TEXT, code TEXT, severity TEXT, claim_type TEXT,
  issue_id INTEGER,                    -- site_issues.id, NULL if already on file
  payload TEXT                         -- the whole finding, as JSON
);

-- KEYWORD INTELLIGENCE, BUILT FROM FREE SOURCES.
-- Search Console impressions (the owner's own real data), autocomplete breadth
-- from Google and Bing, the wording his own customers used when they wrote in,
-- and which phrases rivals keep a page for. See pipelines/seo_intel.py.
--
-- Note what is NOT here: a volume column. DataForSEO, Ahrefs and Semrush sell
-- monthly search volume and nothing free supplies it — so rather than leave a
-- column that would be filled with a guess inside a week, the score is built
-- from evidence that can be pointed at, and every row carries the evidence in
-- words. impressions is real because Search Console measured it; everything
-- else is labelled for what it is.
CREATE TABLE IF NOT EXISTS keyword_intel (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  phrase          TEXT NOT NULL UNIQUE,
  area            TEXT, intent TEXT,
  impressions     INTEGER DEFAULT 0,   -- REAL, from Search Console
  clicks          INTEGER DEFAULT 0,
  position        REAL DEFAULT 0,
  striking        INTEGER DEFAULT 0,   -- ranked 5-20 with real impressions
  suggest_breadth INTEGER DEFAULT 0,   -- autocomplete completions, NOT volume
  rival_pages     INTEGER DEFAULT 0,
  in_enquiries    INTEGER DEFAULT 0,   -- his own customers used this wording
  we_have_page    INTEGER DEFAULT 0,
  score           REAL DEFAULT 0,
  evidence        TEXT, sources TEXT, seen_at INTEGER
);

-- WHAT HAS ALREADY BEEN PUT ON THE OWNER'S PHONE.
-- The unique key is the whole table. Without it a pending approval buzzes him
-- every hour until he answers, he mutes the app, and the next alert — the one
-- about the dead contact form — goes unread too. Sending is easy; not sending
-- is the part that keeps the channel worth having. See coordination/notify.py.
CREATE TABLE IF NOT EXISTS notifications (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  key        TEXT NOT NULL UNIQUE,
  urgency    TEXT,                    -- now | today | digest
  title      TEXT, body TEXT,
  first_seen INTEGER, sent_at INTEGER,
  route      TEXT, detail TEXT
);

-- WHAT THE OWNER HAS ALREADY LOOKED AT.
-- This lived in the browser's localStorage, which is wrong for exactly the
-- reason he kept reporting: localStorage is per-origin, so opening the office
-- on localhost and the control room on 127.0.0.1 are two different memories,
-- and the badge he had just cleared came straight back. There is one owner and
-- one agency — where he got to is a fact about the agency, so it belongs in the
-- cabinet with every other fact. One row per thing that can be read.
CREATE TABLE IF NOT EXISTS read_marks (
  what     TEXT PRIMARY KEY,          -- 'activity'
  seen_to  INTEGER NOT NULL,          -- unix seconds; everything at or before is read
  at       INTEGER,                   -- when he marked it
  -- 'Clear' on the activity panel. It HIDES, it does not delete: every event is
  -- derived from a row the work itself wrote, so deleting one would mean
  -- deleting the record of a run, a claim or a sent mail. Everything at or
  -- before this is left out of the list until he asks to see it all again.
  cleared_to INTEGER DEFAULT 0
);

-- WHEN AN AGENT ITSELF BREAKS.
-- Everything else in this cabinet records work. This records the work NOT
-- happening: a run that started and never came back, a run that raised, a task
-- claimed and abandoned, a request nobody answered. Until this table existed a
-- stall was something the screens WORKED OUT and drew in red, and that was all
-- that ever happened to it — no ticket, no sector told, no MD, nothing. The
-- owner watched an agent sit dead for 41 minutes with the whole agency
-- carrying on around it.
--
-- `key` is what makes this a ticket and not a firehose: one row per distinct
-- fault, updated on every scan, so a stall that is still true at the next pass
-- raises its seen_count rather than filing a second ticket. See faults.py.
CREATE TABLE IF NOT EXISTS faults (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  key          TEXT NOT NULL UNIQUE,
  kind         TEXT,             -- stall | crash | abandoned | unanswered | lock
  agent        TEXT,             -- the desk that broke, where there is one
  sector       TEXT,             -- the sector that owns that desk
  routed_to    TEXT,             -- the sector asked to fix it
  severity     TEXT,             -- blocking | degraded | noted
  what         TEXT,             -- one plain line
  detail       TEXT,
  evidence     TEXT,             -- the row this is a claim about
  first_seen   INTEGER, last_seen INTEGER,
  seen_count   INTEGER DEFAULT 1,
  request_id   TEXT,             -- the board request opened with the owning sector
  task_id      TEXT,             -- the ticket posted to whoever can fix it
  escalated_at INTEGER,          -- when the MD was told
  status       TEXT DEFAULT 'open',   -- open | routed | escalated | resolved
  resolved_at  INTEGER, resolution TEXT
);
CREATE INDEX IF NOT EXISTS faults_open ON faults(status, last_seen);

-- WHICH SECTORS THE OWNER HAS TURNED OFF.
-- His agency, his call. He may not want Leads doing cold outreach this month,
-- or Content publishing while he rewrites the service pages, and the honest way
-- to express that is a switch — not deleting agents, and not leaving a sector
-- running while he ignores its output.
--
-- A missing row means ON. That matters: a cabinet built before this table
-- existed, or a sector added later, must default to working rather than to
-- silently doing nothing. Absence is never "off".
CREATE TABLE IF NOT EXISTS sector_switches (
  sector     TEXT PRIMARY KEY,      -- 'Leads', 'SEO', ... matches state.SECTORS
  enabled    INTEGER NOT NULL DEFAULT 1,
  note       TEXT,                  -- why, in his words, if he gave one
  changed_at INTEGER
);

-- ---- THE STOP BUTTON --------------------------------------------------------
-- "there is no any stop button that makes the agents or overseer to understand
-- that the task is stopped by the user."
--
-- Closing the window is not stopping. The process dies, the cabinet goes on
-- saying five agents are mid-task, and the Overseer files faults about work
-- nobody is doing. board.sweep_dead_processes() cleans up after that; this
-- table is the other half — a way to stop a run ON PURPOSE, in writing, while
-- it is still alive to be told.
--
-- A row here is a REQUEST, not an order carried out. The run watches for it and
-- puts its tools down: the step in flight is cut short, the steps behind it
-- never start, and the whole thing closes as 'stopped by you' rather than
-- failed. If the run does not answer within the grace period the button kills
-- the process it recorded and tidies up in its place — see day_run.stop().
--
-- cleared_at is set the moment the run has actually stopped. A row with it
-- still NULL is a stop in progress; that is the only state that means anything
-- to a reader. The scope is a primary key so pressing Stop twice is one stop.
CREATE TABLE IF NOT EXISTS stop_requests (
  scope      TEXT PRIMARY KEY,      -- 'day_run' today; the column is here so
                                    -- a second stoppable thing does not need
                                    -- a second table
  asked_at   INTEGER,
  asked_by   TEXT,                  -- 'owner' from the button, an agent name
                                    -- if the Overseer ever pulls the cord
  why        TEXT,                  -- his words, shown on the run afterwards
  cleared_at INTEGER                -- when the run confirmed it had stopped
);

-- HOW A REQUEST GETS ITS PLACE IN THE QUEUE.
-- Added when the owner asked what happens if five sectors ask the MD at once.
-- The answer was "whichever asked first", which is wrong twice over: an urgent
-- thing waits behind a trivial one, and a trivial one can wait for ever.
--
-- `blocking` is the asker saying it cannot continue without an answer — not a
-- mood, a fact about its own state, and it is the one thing an asker is
-- genuinely the authority on. `urgency` is its own read, kept because it is
-- useful context, but see coordination/triage.py: an asker's claim is never the
-- whole score. Everything else is measured by the receiver.
-- The two columns themselves are added by board._ADDED_COLUMNS, because ALTER
-- TABLE cannot be written idempotently and this file is re-applied on every
-- connection. They are declared there, not here.
