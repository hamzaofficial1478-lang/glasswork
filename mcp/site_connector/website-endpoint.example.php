<?php
/**
 * website-endpoint.example.php — the small admin API the agency talks to when
 * you give it FULL access to the site.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * READ THIS FIRST: YOU PROBABLY DO NOT NEED THIS FILE YET.
 *
 * The agency has two modes, and the first real test should use the first one:
 *
 *   READ-ONLY (recommended to start)  Nothing goes on your website at all. The
 *     IT Watcher fetches your public pages like any visitor and reports what is
 *     wrong; the site-services reader learns what you sell from your own words.
 *     Every change an agent wants is queued for you to approve by hand. Set the
 *     site on /setup, choose read-only, and you are done.
 *
 *   FULL ACCESS (later)  Only needed when you want agents to PUBLISH pages
 *     rather than hand them to you. That is what this file is for.
 *
 * Do not switch to full access until you have watched read-only for a few days
 * and trust what it queues. A publishing agent on a live site is the one part
 * of this that can embarrass you in front of a customer.
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * ON WORDPRESS you do not need this either — the agency speaks the built-in
 * REST API. Create an Application Password (Users → Profile → Application
 * Passwords) and pick the WordPress option on /setup.
 *
 * HOW TO USE THIS FILE (custom / PHP sites)
 *
 *   1. Copy it onto your server, somewhere NOT web-guessable, e.g.
 *      /agency-api/index.php
 *   2. Generate a long random token and put it in your server environment as
 *      GLASSWORK_TOKEN (do not paste it into this file):
 *         openssl rand -hex 32
 *   3. Make sure it is served over HTTPS. The token travels in a header, and
 *      over plain HTTP anyone on the same network reads it.
 *   4. On /setup choose "Full access" and enter:
 *         API URL   https://yoursite.ae/agency-api
 *         Token     the value of GLASSWORK_TOKEN
 *
 * WHAT IT EXPOSES  (all require the token)
 *
 *   GET  /health          is the site alive
 *   GET  /pages/{slug}    read one page
 *   POST /pages           create or update a page
 *   POST /contact         submit ONE clearly-labelled test enquiry
 *
 * Deliberately NOT exposed: anything that deletes, any raw SQL, any file write
 * outside the pages directory, and any way to run a command. An agent that gets
 * this token should not be able to take the site down with it.
 */

declare(strict_types=1);

header('Content-Type: application/json');
header('X-Content-Type-Options: nosniff');

// ---------------------------------------------------------------------------
// Where pages live. Point this at your own storage: a directory, or swap the
// two helpers at the bottom for your database.
// ---------------------------------------------------------------------------
const PAGES_DIR = __DIR__ . '/../content/pages';

function fail(int $code, string $why): never {
    http_response_code($code);
    echo json_encode(['ok' => false, 'error' => $why]);
    exit;
}

// ---------------------------------------------------------------------------
// AUTH. hash_equals is a constant-time compare: a normal === leaks the token
// one character at a time to anyone willing to measure how long it takes.
// ---------------------------------------------------------------------------
$expected = getenv('GLASSWORK_TOKEN') ?: '';
if ($expected === '' || strlen($expected) < 32) {
    fail(500, 'GLASSWORK_TOKEN is not set on the server, or is too short to be safe.');
}
$sent = '';
if (!empty($_SERVER['HTTP_AUTHORIZATION'])
    && preg_match('/Bearer\s+(\S+)/i', $_SERVER['HTTP_AUTHORIZATION'], $m)) {
    $sent = $m[1];
}
if (!hash_equals($expected, $sent)) {
    fail(401, 'bad or missing token');
}

// ---------------------------------------------------------------------------
// ROUTING
// ---------------------------------------------------------------------------
$path   = rtrim(parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/', '/');
$base   = rtrim(dirname($_SERVER['SCRIPT_NAME'] ?? ''), '/');
$route  = '/' . ltrim(substr($path, strlen($base)), '/');
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$body   = json_decode(file_get_contents('php://input') ?: '{}', true) ?: [];

/**
 * A slug is a name, never a path. Without this, "../../../etc/passwd" is a
 * valid slug and the agency's own token becomes a way to read your whole
 * server. Anything outside a-z, 0-9 and a hyphen is refused outright.
 */
function safe_slug(string $raw): string {
    $slug = strtolower(trim($raw));
    if ($slug === '' || !preg_match('/^[a-z0-9][a-z0-9\-]{0,80}$/', $slug)) {
        fail(400, 'bad slug — letters, numbers and hyphens only');
    }
    return $slug;
}

if ($route === '/health' && $method === 'GET') {
    echo json_encode(['ok' => true, 'status' => 'up', 'time' => time()]);
    exit;
}

if (preg_match('#^/pages/(.+)$#', $route, $m) && $method === 'GET') {
    $slug = safe_slug($m[1]);
    $file = PAGES_DIR . "/$slug.json";
    if (!is_file($file)) {
        fail(404, 'no such page');
    }
    echo file_get_contents($file);
    exit;
}

if ($route === '/pages' && $method === 'POST') {
    $slug = safe_slug((string)($body['slug'] ?? ''));
    $page = [
        'slug'    => $slug,
        'title'   => (string)($body['title'] ?? ''),
        'html'    => (string)($body['html'] ?? ''),
        'meta'    => $body['meta'] ?? [],
        'updated' => time(),
        // Who published it. Keep this: when a page reads oddly in six months
        // you want to know whether a person or an agent wrote it.
        'by'      => 'glasswork-agency',
    ];
    if ($page['title'] === '' || $page['html'] === '') {
        fail(400, 'a page needs a title and a body');
    }
    if (!is_dir(PAGES_DIR) && !mkdir(PAGES_DIR, 0775, true)) {
        fail(500, 'cannot create the pages directory');
    }
    // keep the previous version — an agent publishing over your own copy with
    // no way back is exactly the accident worth spending one file on
    $file = PAGES_DIR . "/$slug.json";
    if (is_file($file)) {
        @copy($file, PAGES_DIR . "/$slug." . date('Ymd-His') . ".bak.json");
    }
    file_put_contents($file, json_encode($page, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));
    echo json_encode(['ok' => true, 'slug' => $slug, 'updated' => $page['updated']]);
    exit;
}

if ($route === '/contact' && $method === 'POST') {
    // The daily proof that enquiries still land. It must be obviously a test in
    // the inbox, or you will one day ring one of these back as a real customer.
    $to      = getenv('GLASSWORK_ENQUIRY_TO') ?: '';
    $subject = '[AGENCY TEST] enquiry-path check — please ignore';
    $msg     = "This is the automated daily check that your contact form still "
             . "delivers. Nobody is waiting for a reply.\n\nSent: " . date('c') . "\n";
    if ($to === '') {
        fail(500, 'GLASSWORK_ENQUIRY_TO is not set');
    }
    $sent = @mail($to, $subject, $msg, 'From: no-reply@' . ($_SERVER['HTTP_HOST'] ?? 'localhost'));
    echo json_encode(['ok' => (bool)$sent]);
    exit;
}

fail(404, 'unknown endpoint');
