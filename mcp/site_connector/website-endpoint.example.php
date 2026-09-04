<?php
/**
 * website-endpoint.example.php — the one file that lets the agency read and
 * change YOUR site. Copy it onto your server as /agency-api/index.php.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * READ THIS FIRST
 *
 * Start on read-only (that is the default on /setup) and watch what the agency
 * queues for a few days before you switch it to full access. A publishing agent
 * on a real site is the one part of this that can embarrass you in front of a
 * customer.
 * ─────────────────────────────────────────────────────────────────────────────
 *
 * ON WORDPRESS you do not need this file — the agency speaks the built-in REST
 * API. Create an Application Password (Users → Profile → Application Passwords)
 * and pick the WordPress option on /setup.
 *
 * HOW TO USE THIS FILE (custom / PHP sites)
 *
 *   1. Put it on your server at  /agency-api/index.php
 *   2. Give it a token, ANY ONE of these three (the first one found wins):
 *
 *        a. a file  token.php  next to this one:
 *               <?php return 'paste-your-long-random-token-here';
 *        b. a file  .agency-token  next to this one, the token on one line
 *        c. a server environment variable  GLASSWORK_TOKEN
 *
 *      Make one with:  openssl rand -hex 32
 *
 *      (a) exists because most shared hosting gives you no way to set an
 *      environment variable, which made (c) alone a dead end for the people
 *      most likely to be using this file.
 *
 *   3. Serve it over HTTPS. The token travels in a header, and over plain HTTP
 *      anyone on the same network reads it.
 *   4. On /setup choose "Full access" and enter:
 *         API URL   https://yoursite.ae/agency-api
 *         Token     the same token
 *
 * WHAT IT EXPOSES  (all require the token)
 *
 *   ?do=health              is the site alive
 *   ?do=pages               list the real pages on the site
 *   ?do=pages&page=x.html   read one real page
 *   POST ?do=pages          {"page":"x.html","html":"..."} — write one page
 *   POST ?do=contact        send ONE clearly-labelled test enquiry
 *
 * WHY A QUERY STRING AND NOT /agency-api/health
 *
 * The pretty form needs mod_rewrite and an .htaccess. Apache without one looks
 * for a FILE called 'health', does not find it, and 404s — so on ordinary
 * shared hosting every single call failed while the same code passed on a local
 * PHP server, which falls back to index.php. A query string needs no server
 * configuration anywhere and no host can switch it off. Path routing is still
 * accepted below for anyone who has the rewrite in place.
 *
 * Deliberately NOT exposed: anything that deletes, any raw SQL, any file write
 * outside the site folder or to a non-page extension, and any way to run a
 * command. An agent that gets this token should not be able to take the site
 * down with it.
 */

header('Content-Type: application/json; charset=utf-8');
header('X-Robots-Tag: noindex, nofollow');

function fail(int $code, string $why) {
    http_response_code($code);
    echo json_encode(['ok' => false, 'error' => $why]);
    exit;
}

// ---------------------------------------------------------------------------
// WHERE THE SITE IS
// ---------------------------------------------------------------------------
// This file lives in /agency-api/, so the site is the folder above it. realpath
// is what makes the traversal guard further down meaningful: every path the
// agency asks for is resolved and then checked to be genuinely inside this.
define('SITE_ROOT', realpath(dirname(__DIR__)) ?: dirname(__DIR__));

// Pages the agency may read and write. An allowlist, not a blocklist: a
// blocklist is one forgotten extension away from letting an agent overwrite
// .htaccess or a .env.
const PAGE_EXT = ['html', 'htm', 'php'];

// Never ours, whatever is asked for. Mirrors NOT_OURS_DIRS on the Python side.
const OFF_LIMITS = ['agency-api', 'admin', 'wp-admin', 'includes', 'config',
                    'vendor', 'node_modules', '.git', 'cgi-bin'];

// ---------------------------------------------------------------------------
// THE TOKEN
// ---------------------------------------------------------------------------

function expected_token(): string {
    $here = __DIR__;
    if (is_file("$here/token.php")) {
        $t = @include "$here/token.php";          // <?php return 'xxx';
        if (is_string($t) && trim($t) !== '') {
            return trim($t);
        }
        // or  <?php return ['token' => 'xxx', 'enquiry_to' => 'you@site.ae'];
        if (is_array($t) && !empty($t['token']) && is_string($t['token'])) {
            return trim($t['token']);
        }
        // also accept  <?php $GLASSWORK_TOKEN = 'xxx';
        if (isset($GLASSWORK_TOKEN) && is_string($GLASSWORK_TOKEN)) {
            return trim($GLASSWORK_TOKEN);
        }
    }
    if (is_file("$here/.agency-token")) {
        $t = trim((string)@file_get_contents("$here/.agency-token"));
        if ($t !== '') {
            return $t;
        }
    }
    return trim((string)(getenv('GLASSWORK_TOKEN') ?: ''));
}

/**
 * The token as it arrived. Two headers, because plenty of Apache and CGI setups
 * drop Authorization before PHP ever sees it — which looks exactly like a wrong
 * token and sends you hunting for the wrong problem. The agency sends both.
 */
function sent_token(): string {
    $hdrs = [];
    if (function_exists('apache_request_headers')) {
        foreach ((array)apache_request_headers() as $k => $v) {
            $hdrs[strtolower($k)] = $v;
        }
    }
    foreach ($_SERVER as $k => $v) {
        if (strpos($k, 'HTTP_') === 0) {
            $hdrs[strtolower(str_replace('_', '-', substr($k, 5)))] = $v;
        }
    }
    if (!empty($hdrs['x-agency-token'])) {
        return trim($hdrs['x-agency-token']);
    }
    foreach (['authorization', 'redirect-http-authorization'] as $k) {
        if (!empty($hdrs[$k]) && preg_match('/Bearer\s+(\S+)/i', $hdrs[$k], $m)) {
            return $m[1];
        }
    }
    return '';
}

$expected = expected_token();
if ($expected === '' || strlen($expected) < 32) {
    fail(500, 'No token on the server. Put one in agency-api/token.php as '
            . "<?php return 'your-long-random-token'; — at least 32 characters.");
}
// hash_equals is a constant-time compare: a normal === leaks the token one
// character at a time to anyone willing to measure how long it takes.
if (!hash_equals($expected, sent_token())) {
    fail(401, 'bad or missing token');
}

// ---------------------------------------------------------------------------
// ROUTING — ?do= first, path second
// ---------------------------------------------------------------------------

$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$body   = json_decode(file_get_contents('php://input') ?: '{}', true) ?: [];
$do     = strtolower(trim((string)($_GET['do'] ?? '')));

if ($do === '') {                       // no ?do= — fall back to path routing
    $path  = rtrim(parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/', '/');
    $base  = rtrim(dirname($_SERVER['SCRIPT_NAME'] ?? ''), '/');
    $route = '/' . ltrim(substr($path, strlen($base)), '/');
    $route = preg_replace('#^/index\.php#', '', $route) ?: '/';
    if (preg_match('#^/pages/(.+)$#', $route, $m)) {
        $do = 'pages';
        $_GET['page'] = $m[1];
    } else {
        $do = trim($route, '/');
    }
}

/**
 * A page is a real file inside the site folder, and this is the only way one is
 * ever named. Without the realpath check "../../etc/passwd" is a valid page and
 * the agency's own token becomes a way to read the whole server.
 */
function safe_page(string $raw): string {
    $rel = str_replace('\\', '/', trim($raw));
    $rel = ltrim($rel, '/');
    if ($rel === '') {
        fail(400, 'no page named');
    }
    $bits = array_filter(explode('/', $rel), fn($b) => $b !== '' && $b !== '.');
    if (in_array('..', $bits, true)) {
        fail(400, "$rel climbs out of the website folder");
    }
    foreach ($bits as $i => $b) {
        if ($i < count($bits) - 1 && in_array(strtolower($b), OFF_LIMITS, true)) {
            fail(403, "$rel is inside $b/, which is not the website");
        }
    }
    $ext = strtolower(pathinfo($rel, PATHINFO_EXTENSION));
    if (!in_array($ext, PAGE_EXT, true)) {
        fail(400, "$rel is not a page — only " . implode(', ', PAGE_EXT));
    }
    return implode('/', $bits);
}

/** Resolve to an absolute path and prove it really is inside the site. */
function absolute(string $rel, bool $must_exist = true): string {
    $full = SITE_ROOT . '/' . $rel;
    $real = realpath($full);
    if ($real === false) {
        if ($must_exist) {
            fail(404, "no such page: $rel");
        }
        $real = realpath(dirname($full));      // the folder must exist at least
        if ($real === false || strpos($real . '/', SITE_ROOT . '/') !== 0) {
            fail(400, "cannot write outside the website folder");
        }
        return $full;
    }
    if (strpos($real, SITE_ROOT . '/') !== 0 && $real !== SITE_ROOT) {
        fail(400, "$rel resolves outside the website folder");
    }
    return $real;
}

// ---------------------------------------------------------------------------
// THE FOUR THINGS IT DOES
// ---------------------------------------------------------------------------

if ($do === 'health' && $method === 'GET') {
    echo json_encode(['ok' => true, 'status' => 'up', 'time' => time(),
                      'root' => basename(SITE_ROOT), 'php' => PHP_VERSION,
                      'writable' => is_writable(SITE_ROOT)]);
    exit;
}

if ($do === 'pages' && $method === 'GET' && ($_GET['page'] ?? '') !== '') {
    $rel  = safe_page((string)$_GET['page']);
    $html = @file_get_contents(absolute($rel));
    if ($html === false) {
        fail(500, "could not read $rel — check the file permissions");
    }
    echo json_encode(['ok' => true, 'page' => $rel, 'html' => $html,
                      'bytes' => strlen($html)]);
    exit;
}

if ($do === 'pages' && $method === 'GET') {
    // The real pages, walked from the site folder. Capped: a big site should
    // not turn one listing call into a minute of disk work.
    $found = [];
    $it = new RecursiveIteratorIterator(
        new RecursiveCallbackFilterIterator(
            new RecursiveDirectoryIterator(SITE_ROOT, FilesystemIterator::SKIP_DOTS),
            function ($file) {
                $name = strtolower($file->getFilename());
                if ($file->isDir()) {
                    return !in_array($name, OFF_LIMITS, true) && $name[0] !== '.';
                }
                return in_array(strtolower($file->getExtension()), PAGE_EXT, true);
            }
        )
    );
    foreach ($it as $file) {
        if (!$file->isFile()) {
            continue;
        }
        $rel = ltrim(str_replace('\\', '/', substr($file->getPathname(), strlen(SITE_ROOT))), '/');
        $found[] = ['page' => $rel, 'bytes' => $file->getSize(),
                    'updated' => $file->getMTime()];
        if (count($found) >= 500) {
            break;
        }
    }
    usort($found, fn($a, $b) => strcmp($a['page'], $b['page']));
    echo json_encode(['ok' => true, 'pages' => $found, 'counted' => count($found)]);
    exit;
}

if ($do === 'pages' && $method === 'POST') {
    // The agency names a real page and hands over the whole new file. 'page'
    // and 'html' are what the Python client sends — do not rename them here
    // without changing pipelines/site_api.py, or every write 400s.
    $rel  = safe_page((string)($body['page'] ?? ''));
    $html = (string)($body['html'] ?? '');
    if ($html === '') {
        fail(400, 'a page needs a body — refusing to blank ' . $rel);
    }
    $full = absolute($rel, false);

    // Keep the previous version. An agent writing over your own copy with no
    // way back is exactly the accident worth spending one file on, and the
    // agency's --undo reads these.
    $backup = '';
    if (is_file($full)) {
        $backup = $full . '.' . date('Ymd-His') . '.bak';
        @copy($full, $backup);
    }
    if (@file_put_contents($full, $html) === false) {
        fail(500, "could not write $rel — the web user may not have permission");
    }
    echo json_encode(['ok' => true, 'page' => $rel, 'bytes' => strlen($html),
                      'backup' => $backup ? basename($backup) : null,
                      'updated' => time()]);
    exit;
}

if ($do === 'head' && $method === 'POST') {
    // One marked block inside <head>. This is how the SEO sector's schema and
    // FAQ markup reach a page without rewriting the page: everything the agency
    // adds sits between its own comment markers, so it can find it again and
    // take exactly that back out later.
    $rel = safe_page((string)($body['page'] ?? ''));
    $id  = strtolower(trim((string)($body['id'] ?? '')));
    if (!preg_match('/^[a-z0-9][a-z0-9\-]{0,60}$/', $id)) {
        fail(400, 'a head block needs an id — letters, numbers and hyphens');
    }
    $html = (string)($body['html'] ?? '');
    if ($html === '') {
        fail(400, 'a head block needs a body');
    }
    $full = absolute($rel);
    $src  = (string)@file_get_contents($full);
    if ($src === '') {
        fail(500, "could not read $rel");
    }

    $open  = "<!-- agency:$id -->";
    $close = "<!-- /agency:$id -->";
    $block = "$open\n$html\n$close";
    $pat   = '#' . preg_quote($open, '#') . '.*?' . preg_quote($close, '#') . '#s';

    if (preg_match($pat, $src)) {
        $out = preg_replace($pat, $block, $src, 1);       // already there: replace
    } elseif (preg_match('#</head>#i', $src)) {
        $out = preg_replace('#</head>#i', "$block\n</head>", $src, 1);
    } else {
        // No <head> at all. Refusing beats guessing: silently pasting markup at
        // the top of a fragment is how the agency ends up "succeeding" on files
        // that are not pages.
        fail(422, "$rel has no </head> — refusing to guess where markup belongs");
    }

    $backup = $full . '.' . date('Ymd-His') . '.bak';
    @copy($full, $backup);
    if (@file_put_contents($full, $out) === false) {
        fail(500, "could not write $rel — the web user may not have permission");
    }
    echo json_encode(['ok' => true, 'page' => $rel, 'id' => $id,
                      'backup' => basename($backup), 'bytes' => strlen($out)]);
    exit;
}

if ($do === 'backups' && $method === 'GET') {
    // What can still be put back, newest first. The agency shows these to the
    // owner when it offers to undo something.
    $found = [];
    $it = new RecursiveIteratorIterator(
        new RecursiveCallbackFilterIterator(
            new RecursiveDirectoryIterator(SITE_ROOT, FilesystemIterator::SKIP_DOTS),
            function ($f) {
                $n = strtolower($f->getFilename());
                return $f->isDir()
                    ? (!in_array($n, OFF_LIMITS, true) && $n[0] !== '.')
                    : (substr($n, -4) === '.bak');
            }
        )
    );
    foreach ($it as $f) {
        if (!$f->isFile()) {
            continue;
        }
        $rel = ltrim(str_replace('\\', '/', substr($f->getPathname(), strlen(SITE_ROOT))), '/');
        $found[] = ['backup' => $rel, 'page' => preg_replace('/\.\d{8}-\d{6}\.bak$/', '', $rel),
                    'bytes' => $f->getSize(), 'taken' => $f->getMTime()];
        if (count($found) >= 300) {
            break;
        }
    }
    usort($found, fn($a, $b) => $b['taken'] <=> $a['taken']);
    echo json_encode(['ok' => true, 'backups' => $found, 'counted' => count($found)]);
    exit;
}

if ($do === 'rollback' && $method === 'POST') {
    // Put a page back. Named backup wins; otherwise the newest one for a page.
    $want = trim((string)($body['backup'] ?? ''));
    if ($want !== '') {
        if (!preg_match('/\.\d{8}-\d{6}\.bak$/', $want)) {
            fail(400, 'that is not one of this endpoint\'s backup files');
        }
        $bak  = absolute(ltrim(str_replace('\\', '/', $want), '/'));
        $page = preg_replace('/\.\d{8}-\d{6}\.bak$/', '', $want);
    } else {
        $page = safe_page((string)($body['page'] ?? ''));
        $all  = glob(SITE_ROOT . '/' . $page . '.*.bak') ?: [];
        if (!$all) {
            fail(404, "no backup on file for $page");
        }
        sort($all);
        $bak = end($all);
    }
    $full = absolute(safe_page($page), false);
    $src  = @file_get_contents($bak);
    if ($src === false || @file_put_contents($full, $src) === false) {
        fail(500, 'could not put that page back');
    }
    echo json_encode(['ok' => true, 'page' => $page,
                      'restored_from' => basename($bak), 'bytes' => strlen($src)]);
    exit;
}

if ($do === 'contact' && $method === 'POST') {
    // The daily proof that enquiries still land. It must be obviously a test in
    // the inbox, or you will one day ring one of these back as a real customer.
    $to = getenv('GLASSWORK_ENQUIRY_TO') ?: '';
    if ($to === '' && is_file(__DIR__ . '/token.php')) {
        $cfg = @include __DIR__ . '/token.php';
        if (is_array($cfg) && !empty($cfg['enquiry_to'])) {
            $to = (string)$cfg['enquiry_to'];
        }
    }
    if ($to === '') {
        fail(500, 'Nowhere to send the test. Set GLASSWORK_ENQUIRY_TO, or return '
                . "['token'=>'...','enquiry_to'=>'you@yoursite.ae'] from token.php.");
    }
    $sent = @mail($to, '[AGENCY TEST] enquiry-path check — please ignore',
                  "This is the automated daily check that your contact form still "
                  . "delivers. Nobody is waiting for a reply.\n\nSent: " . date('c') . "\n",
                  'From: no-reply@' . ($_SERVER['HTTP_HOST'] ?? 'localhost'));
    echo json_encode(['ok' => (bool)$sent, 'to' => $to]);
    exit;
}

fail(404, "unknown endpoint '" . htmlspecialchars($do, ENT_QUOTES) . "' — "
        . 'expected health, pages or contact');
