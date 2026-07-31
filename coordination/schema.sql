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
  finished_at INTEGER
);

CREATE TABLE IF NOT EXISTS locks (
  resource    TEXT PRIMARY KEY,        -- live_site | publish_pipeline | page:<slug> ...
  holder      TEXT NOT NULL,
  acquired_at INTEGER,
  expires_at  INTEGER
);

CREATE TABLE IF NOT EXISTS requests (
  id          TEXT PRIMARY KEY,
  from_sector TEXT NOT NULL,
  to_sector   TEXT NOT NULL,
  subject     TEXT,
  status      TEXT DEFAULT 'open',     -- open | claimed | answered | closed
  handler     TEXT,
  created_at  INTEGER
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
  scored_at   INTEGER
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
  status      TEXT                     -- running | ok | error | killed
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
  name TEXT, domain TEXT, gbp_reviews INTEGER
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
  status TEXT DEFAULT 'idea',          -- idea | briefed | drafted | in_review | approved | published
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
  found_at INTEGER
);
CREATE TABLE IF NOT EXISTS outreach_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id INTEGER, channel TEXT, direction TEXT,  -- sent | reply
  body TEXT, reply_class TEXT,          -- interested | not_now | no | question
  needs_owner INTEGER DEFAULT 0, at INTEGER
);

-- ---- site health (IT) -----------------------------------------------------

CREATE TABLE IF NOT EXISTS site_issues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,                            -- bug | speed | broken_link | form_dead | ssl | security
  severity TEXT, detail TEXT, url TEXT,
  status TEXT DEFAULT 'open',          -- open | fixing | in_review | deployed | closed
  found_by TEXT, found_at INTEGER
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
  status     TEXT DEFAULT 'pending',      -- pending | approved | rejected | applied
  queued_at  INTEGER,
  decided_at INTEGER
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

-- ---- the daily digest -----------------------------------------------------

CREATE TABLE IF NOT EXISTS daily_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT, health_score INTEGER,
  sector_scores TEXT,                  -- json: which sector is winning / bleeding
  summary TEXT, actions TEXT, spend_usd REAL, created_at INTEGER
);
