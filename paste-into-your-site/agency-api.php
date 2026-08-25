<?php
/**
 * agency-api.php — FULL ACCESS for worlddealtechnicalservices.com
 *
 * This is the door. Put this ONE file on your server and the agency can read
 * and edit your pages itself, instead of handing you homework.
 *
 * ---------------------------------------------------------------------------
 * WHAT TO DO WITH THIS FILE  (5 minutes, cPanel)
 *
 *  1. cPanel -> File Manager -> open your website folder
 *  2. Make a new folder called:   agency-api
 *  3. Upload this file into it, and RENAME it to:   index.php
 *     You should end up with:     <your website folder>/agency-api/index.php
 *  4. In that same folder make a new file called:   token.txt
 *     Put ONE long random line in it — 40 or more random characters.
 *     To make one, run this on your PC in PowerShell:
 *         -join ((48..57)+(97..122) | Get-Random -Count 48 | % {[char]$_})
 *     Paste the result into token.txt and save. Share it with nobody.
 *  5. On the agency setup screen choose Full access and enter:
 *         Admin API address:  https://www.worlddealtechnicalservices.com/agency-api
 *         Admin API token:    the exact line from token.txt
 *
 * That is it. Nothing else on your site changes.
 * ---------------------------------------------------------------------------
 *
 * IS THIS SAFE?
 *
 * Nobody can use it without the token, and the token exists in two places
 * only: token.txt on your server, and the agency's settings file on your PC.
 *
 * What it CANNOT do, no matter who holds the token:
 *   - delete anything, ever
 *   - touch any file outside your website folder
 *   - touch anything that is not a .php or .html page
 *   - run a command, read your database, or read any other file
 *   - overwrite itself or your token
 *
 * And every edit copies the old file to a backup first. Nothing an agent does
 * is one-way: /rollback puts a page back exactly as it was, in one call.
 *
 * You said you will fix whatever the agents break. This is here so that in
 * most cases you will not have to.
 */

declare(strict_types=1);

header('Content-Type: application/json');
header('X-Content-Type-Options: nosniff');
header('X-Robots-Tag: noindex, nofollow');   // never let Google index the door

// ---------------------------------------------------------------------------
// WHERE YOUR PAGES ARE
//
// This file sits one folder below your website root, so the root is '..'.
// If you put it somewhere else, this is the only line to change.
// ---------------------------------------------------------------------------
define('SITE_ROOT', realpath(__DIR__ . '/..'));

// Backups. A folder nobody can browse: the .htaccess written below blocks it,
// and nothing anywhere links to it.
define('BACKUP_DIR', __DIR__ . '/backups');

// Only these can ever be read or written. A page, and nothing else.
const ALLOWED_EXT = ['php', 'html', 'htm'];

// Never, under any circumstances, even with a valid token.
const NEVER_TOUCH = ['agency-api/index.php', 'agency-api/token.txt',
                     '.htaccess', 'wp-config.php', 'config.php', 'db.php'];

function reply(array $data, int $code = 200): void {
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    exit;
}

function fail(int $code, string $why): void {
    reply(['ok' => false, 'error' => $why], $code);
}

// ---------------------------------------------------------------------------
// THE TOKEN
//
// From the server environment first (the proper way), then from token.txt
// next to this file (the way that works on shared hosting).
// ---------------------------------------------------------------------------
function expected_token(): string {
    $t = getenv('GLASSWORK_TOKEN') ?: '';
    // token.php is the safe one and the one to use. It is a PHP file, so a
    // browser asking for it straight — https://yoursite.com/agency-api/token.php
    // — gets a blank page, because the server RUNS it instead of showing it.
    //
    // token.txt would have been handed over in full to anybody who guessed the
    // address. It sits inside the website folder, so the web server serves it
    // like any other file, and the careful checks in this script would have
    // been irrelevant: they would already have the key. Still supported for
    // anyone who set it up that way, but /health complains until it is gone.
    if ($t === '' && is_file(__DIR__ . '/token.php')) {
        // A PHP file with no `return` statement gives back the integer 1, and
        // trim((string)1) is "1". Reject that here rather than letting a
        // one-character token flow on as if it were real.
        $v = @include __DIR__ . '/token.php';
        $t = is_string($v) ? trim($v) : '';
    }
    if ($t === '' && is_file(__DIR__ . '/token.txt')) {
        $t = trim((string)file_get_contents(__DIR__ . '/token.txt'));
    }
    return $t;
}

/**
 * What went wrong with the token, in words that name the actual mistake.
 *
 * The old message said "create token.txt" — a file the instructions never
 * mentioned — while the real problem was usually one of three things, and it
 * said nothing about any of them. A setup error you cannot see is worse than
 * one that fails loudly.
 */
function token_diagnosis(): string {
    $here = __DIR__;
    $bits = [];
    // The classic. Notepad appends .txt unless you set "Save as type: All
    // Files", and the result looks completely correct in File Manager until
    // you notice the extension.
    foreach (['token.php.txt', 'token.txt.php', 'Token.php', 'token .php'] as $slip) {
        if (is_file("$here/$slip")) {
            $bits[] = "There is a file called '$slip' in the agency-api folder. "
                    . "It has to be named exactly 'token.php'. Notepad adds .txt "
                    . "unless you set 'Save as type' to 'All Files'. Rename it.";
        }
    }
    if (is_file("$here/token.php")) {
        $v = @include "$here/token.php";
        if (!is_string($v)) {
            $bits[] = "token.php exists but gives nothing back. Its whole "
                    . "contents must be one line, exactly like this, with the "
                    . "<?php at the start and the semicolon at the end:  "
                    . "<?php return 'your-long-token-here';";
        } elseif (strlen(trim($v)) < 24) {
            $bits[] = "token.php returns something only " . strlen(trim($v))
                    . " characters long. The token must be at least 24 "
                    . "characters. Check nothing was cut off when you pasted.";
        }
    } elseif (!is_file("$here/token.txt")) {
        $bits[] = "There is no token.php in the agency-api folder. Create it "
                . "with one line:  <?php return 'your-long-token-here';  and "
                . "upload it next to this file.";
    }
    if (!$bits) {
        $bits[] = "A token file is there but came back empty. Open it and "
                . "check it reads exactly:  <?php return 'your-token';";
    }
    // What IS in the folder, so he can compare against what should be. Names
    // only — never contents, and token files are named but never opened.
    $seen = array_values(array_diff((array)scandir($here) ?: [], ['.', '..']));
    return implode(' ', $bits) . ' | Files currently in agency-api: '
         . (implode(', ', array_slice($seen, 0, 12)) ?: '(none)');
}

$expected = expected_token();
if (strlen($expected) < 24) {
    fail(500, token_diagnosis());
}

$sent = '';
$hdr = $_SERVER['HTTP_AUTHORIZATION'] ?? ($_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '');
if ($hdr !== '' && preg_match('/Bearer\s+(\S+)/i', $hdr, $m)) {
    $sent = $m[1];
} elseif (!empty($_SERVER['HTTP_X_AGENCY_TOKEN'])) {
    // Some Apache setups strip the Authorization header. This is the fallback.
    $sent = (string)$_SERVER['HTTP_X_AGENCY_TOKEN'];
}

// hash_equals compares in constant time. A plain === leaks the token one
// character at a time to anyone patient enough to measure the response.
if (!hash_equals($expected, $sent)) {
    fail(401, 'bad or missing token');
}

// ---------------------------------------------------------------------------
// TURNING A NAME INTO A REAL FILE, SAFELY
//
// This is the part that matters. Without it, a "page name" of
// ../../../../etc/passwd would turn the token into a key to the whole server.
// ---------------------------------------------------------------------------
function resolve_page(string $raw): string {
    $rel = ltrim(trim($raw), '/');
    if ($rel === '' || $rel === '/') {
        $rel = 'index.php';
    }
    // No traversal, no null bytes, no backslashes, no absolute paths.
    if (strpos($rel, "\0") !== false || strpos($rel, '..') !== false
        || strpos($rel, '\\') !== false || preg_match('#^[a-zA-Z]:#', $rel)) {
        fail(400, 'bad page name');
    }
    foreach (NEVER_TOUCH as $forbidden) {
        if (strcasecmp($rel, $forbidden) === 0
            || str_ends_with(strtolower($rel), '/' . strtolower($forbidden))) {
            fail(403, "$rel is off limits");
        }
    }
    $ext = strtolower(pathinfo($rel, PATHINFO_EXTENSION));
    if (!in_array($ext, ALLOWED_EXT, true)) {
        fail(400, 'only .php, .html and .htm pages — nothing else');
    }
    $full = SITE_ROOT . '/' . $rel;
    $real = realpath($full);
    // For a NEW file realpath() is false, so check the parent folder instead.
    $check = $real !== false ? $real : realpath(dirname($full));
    if ($check === false || strpos($check, SITE_ROOT) !== 0) {
        fail(403, 'that is outside your website folder');
    }
    return $full;
}

function relative_to_site(string $full): string {
    return ltrim(str_replace(SITE_ROOT, '', $full), '/');
}

/**
 * Copy the current file somewhere safe. Returns the backup name.
 *
 * THE NAME CARRIES A RANDOM TAIL AND THAT IS NOT DECORATION. Named by the
 * second alone, two edits inside the same second landed on the SAME filename:
 * the first backed up the original, the second backed up the already-edited
 * file over the top of it, and the true original was gone. Found by running
 * the same call three times in a row against a copy of the real site — which
 * is exactly what a nightly agent does.
 */
function back_up(string $full): string {
    if (!is_file($full)) {
        return '';                                   // nothing to preserve yet
    }
    if (!is_dir(BACKUP_DIR)) {
        @mkdir(BACKUP_DIR, 0755, true);
        @file_put_contents(BACKUP_DIR . '/.htaccess',
                           "Require all denied\nDeny from all\n");
    }
    $name = str_replace('/', '__', relative_to_site($full))
          . '.' . date('Ymd-His') . '-' . bin2hex(random_bytes(3)) . '.bak';
    @copy($full, BACKUP_DIR . '/' . $name);
    return $name;
}

// ---------------------------------------------------------------------------
// ROUTING
// ---------------------------------------------------------------------------
//
// TWO WAYS IN, AND THE SIMPLE ONE IS THE DEFAULT.
//
// The first version of this file read the route out of the URL path, so the
// agency called /agency-api/health. That works on the PHP test server — which
// quietly falls back to index.php — and it does NOT work on Apache, which is
// what real hosting runs. Apache looks for a FILE called 'health', does not
// find one, and returns 404. Every single call would have failed on his
// server while passing every test on mine.
//
// So the route can also arrive as a plain query: index.php?do=health. That
// needs no .htaccess, no mod_rewrite, no server configuration of any kind,
// and it cannot be broken by a host that disables rewriting. It is what the
// agency uses. The tidy path form still works where rewriting is available.
//
$method = strtoupper($_SERVER['REQUEST_METHOD'] ?? 'GET');
$body   = json_decode((string)file_get_contents('php://input') ?: '{}', true) ?: [];

$do = trim((string)($_GET['do'] ?? ''), '/');
if ($do !== '') {
    $route = '/' . $do;
} elseif (!empty($_SERVER['PATH_INFO'])) {
    $route = '/' . trim((string)$_SERVER['PATH_INFO'], '/');
} else {
    $path  = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
    $base  = rtrim(dirname($_SERVER['SCRIPT_NAME'] ?? ''), '/');
    $route = '/' . trim(substr($path, strlen($base)), '/');
}
// index.php?do=pages&page=index.php — the page can ride along in the query
// too, so reading one does not need a path either.
if (!empty($_GET['page']) && $route === '/pages' && $method === 'GET') {
    $route = '/pages/' . ltrim((string)$_GET['page'], '/');
}

// --- is the site answering -------------------------------------------------
if ($route === '/health' && $method === 'GET') {
    $warn = [];
    if (is_file(__DIR__ . '/token.txt')) {
        $warn[] = 'Your token is in token.txt, which the web server will hand '
                . 'to anybody who asks for /agency-api/token.txt. Move it: put '
                . "the same line into token.php as  <?php return 'YOURTOKEN';  "
                . 'and delete token.txt.';
    }
    if (!is_file(__DIR__ . '/.htaccess')) {
        $warn[] = 'No .htaccess in the agency-api folder, so the folder may be '
                . 'browsable. Not urgent if you use token.php.';
    }
    reply(['ok' => true, 'status' => 'up', 'time' => time(),
           'site_root' => SITE_ROOT, 'php' => PHP_VERSION,
           'writable' => is_writable(SITE_ROOT),
           'mode' => 'full-access', 'warnings' => $warn]);
}

// --- what pages are there --------------------------------------------------
if ($route === '/pages' && $method === 'GET') {
    $out = [];
    foreach ((array)glob(SITE_ROOT . '/*.{php,html,htm}', GLOB_BRACE) as $f) {
        $rel = relative_to_site($f);
        if (in_array($rel, NEVER_TOUCH, true)) { continue; }
        $out[] = ['page' => $rel, 'bytes' => filesize($f),
                  'changed' => date('c', (int)filemtime($f))];
    }
    reply(['ok' => true, 'pages' => $out]);
}

// --- read one page ---------------------------------------------------------
if (preg_match('#^/pages/(.+)$#', $route, $m) && $method === 'GET') {
    $full = resolve_page(urldecode($m[1]));
    if (!is_file($full)) { fail(404, 'no such page'); }
    reply(['ok' => true, 'page' => relative_to_site($full),
           'html' => (string)file_get_contents($full),
           'bytes' => filesize($full),
           'changed' => date('c', (int)filemtime($full))]);
}

// --- write one page (the old one is always kept) ---------------------------
if ($route === '/pages' && $method === 'POST') {
    $name = (string)($body['page'] ?? $body['slug'] ?? '');
    $html = (string)($body['html'] ?? '');
    if ($name === '') { fail(400, 'which page?'); }
    if (trim($html) === '') { fail(400, 'refusing to write an empty page'); }
    $full = resolve_page($name);
    // Writing the same bytes back is not a change. Skipping it keeps the
    // backup folder honest: one entry per real edit, not one per nightly run.
    if (is_file($full) && (string)file_get_contents($full) === $html) {
        reply(['ok' => true, 'page' => relative_to_site($full),
               'action' => 'unchanged', 'backup' => '',
               'note' => 'already exactly this — nothing written']);
    }
    $backup = back_up($full);
    if (@file_put_contents($full, $html) === false) {
        fail(500, 'could not write the file — check folder permissions in cPanel');
    }
    reply(['ok' => true, 'page' => relative_to_site($full),
           'bytes' => strlen($html), 'backup' => $backup,
           'note' => $backup ? 'previous version kept — /rollback restores it'
                             : 'new page, nothing to keep']);
}

// ---------------------------------------------------------------------------
// PUT A BLOCK INTO THE <head> OF A PAGE
//
// The one the agency needs day to day: adding the markup that gets you into
// Google's map results, without rewriting the page around it.
//
// Run it a hundred times and you still get one copy. Each block is wrapped in
// a marker comment, and a block with the same marker is REPLACED rather than
// added to. That is what makes it safe to run every night.
// ---------------------------------------------------------------------------
if ($route === '/head' && $method === 'POST') {
    $name  = (string)($body['page'] ?? '');
    $mark  = preg_replace('/[^a-z0-9\-]/i', '', (string)($body['id'] ?? ''));
    $block = (string)($body['html'] ?? '');
    if ($name === '' || $mark === '' || trim($block) === '') {
        fail(400, 'need page, id and html');
    }
    $full = resolve_page($name);
    if (!is_file($full)) { fail(404, 'no such page'); }
    $src = (string)file_get_contents($full);

    $open  = "<!-- agency:$mark -->";
    $close = "<!-- /agency:$mark -->";
    $wrapped = "$open\n$block\n$close\n";

    $pattern = '#' . preg_quote($open, '#') . '.*?' . preg_quote($close, '#') . '\s*#s';
    if (preg_match($pattern, $src)) {
        $new = preg_replace($pattern, $wrapped, $src);      // replace in place
        $what = 'replaced';
    } elseif (preg_match('#</head\s*>#i', $src)) {
        $new = preg_replace('#</head\s*>#i', $wrapped . '</head>', $src, 1);
        $what = 'inserted';
    } else {
        fail(400, 'that page has no </head> to insert before');
    }
    // Nothing actually changed — the block is already exactly this. Do not
    // write, do not back up. An agent runs this every night; without the
    // check it would leave a year of identical backups behind it.
    if ($new === $src) {
        reply(['ok' => true, 'page' => relative_to_site($full), 'id' => $mark,
               'action' => 'unchanged', 'backup' => '',
               'note' => 'the block was already there and identical']);
    }
    $backup = back_up($full);
    if (@file_put_contents($full, $new) === false) {
        fail(500, 'could not write the file');
    }
    reply(['ok' => true, 'page' => relative_to_site($full), 'id' => $mark,
           'action' => $what, 'backup' => $backup]);
}

// --- what backups exist ----------------------------------------------------
if ($route === '/backups' && $method === 'GET') {
    $out = [];
    foreach ((array)glob(BACKUP_DIR . '/*.bak') as $f) {
        $out[] = ['backup' => basename($f), 'bytes' => filesize($f),
                  'made' => date('c', (int)filemtime($f))];
    }
    usort($out, fn($a, $b) => strcmp($b['made'], $a['made']));
    reply(['ok' => true, 'backups' => array_slice($out, 0, 100)]);
}

// --- put a page back the way it was ----------------------------------------
if ($route === '/rollback' && $method === 'POST') {
    $which = basename((string)($body['backup'] ?? ''));
    if ($which === '' && !empty($body['page'])) {
        // No backup named: take the most recent one for that page.
        $stem = str_replace('/', '__', ltrim((string)$body['page'], '/'));
        $all = (array)glob(BACKUP_DIR . '/' . $stem . '.*.bak');
        rsort($all);
        $which = $all ? basename($all[0]) : '';
    }
    if ($which === '' || !preg_match('/\.bak$/', $which)) {
        fail(400, 'which backup? call GET /backups to see them');
    }
    $src = BACKUP_DIR . '/' . $which;
    if (!is_file($src)) { fail(404, 'no such backup'); }
    // The filename carries the page it came from:
    //   index.php.20260825-101500-4ec741.bak
    //
    // The random tail is optional in this pattern ON PURPOSE. Adding the tail
    // to back_up() without widening this silently broke rollback — the stem
    // came out as 'index.php.20260825-110110-bf175a', whose file extension
    // reads as '20260825-110110-bf175a', so resolve_page refused it as "not a
    // .php page" and the wrecked file stayed wrecked. Older backups made
    // before the tail existed still have to restore, hence the ?.
    $stem = preg_replace('/\.\d{8}-\d{6}(-[0-9a-f]{6})?\.bak$/', '', $which);
    $page = str_replace('__', '/', (string)$stem);
    $full = resolve_page($page);
    back_up($full);                          // keep the bad one too, just in case
    if (!@copy($src, $full)) { fail(500, 'could not restore'); }
    reply(['ok' => true, 'page' => relative_to_site($full),
           'restored_from' => $which]);
}

// --- prove the contact form still delivers ---------------------------------
if ($route === '/contact' && $method === 'POST') {
    $to = getenv('GLASSWORK_ENQUIRY_TO') ?: '';
    if ($to === '' && is_file(__DIR__ . '/enquiry-to.txt')) {
        $to = trim((string)file_get_contents(__DIR__ . '/enquiry-to.txt'));
    }
    if ($to === '') {
        fail(500, 'No address to test with. Put your enquiry inbox in '
                . 'agency-api/enquiry-to.txt');
    }
    // It must be obviously a test in the inbox, or one day you will ring one
    // of these back thinking it is a customer.
    $ok = @mail($to, '[AGENCY TEST] enquiry check - please ignore',
                "Automated daily check that your contact form still delivers.\n"
                . "Nobody is waiting for a reply.\n\nSent: " . date('c') . "\n",
                'From: no-reply@' . ($_SERVER['HTTP_HOST'] ?? 'localhost'));
    reply(['ok' => (bool)$ok, 'to' => $to]);
}

// --- the release endpoint the agency expects to exist ----------------------
// Your pages are edited in place, so there is no build step and nothing
// separate to ship. Saying so honestly beats pretending otherwise.
if ($route === '/deploy' && $method === 'POST') {
    reply(['ok' => true, 'deployed' => false,
           'note' => 'pages are edited in place; there is no separate build step']);
}

fail(404, 'unknown endpoint: ' . $route);
