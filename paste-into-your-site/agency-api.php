<?php
/**
 * agency-api.php — FULL ACCESS for YOUR-WEBSITE  (version 9)
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
 *         Admin API address:  https://YOUR-WEBSITE/agency-api
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
 *   - remove a page without keeping a copy first — /pages-remove backs the
 *     page up before taking it away, and /rollback puts it straight back;
 *     your home page can never be removed at all
 *   - touch any file outside your website folder
 *   - touch anything that is not a .php or .html page — except that it may
 *     save a blog post's picture into the folder your blog's pictures are in,
 *     and it may write two plain-text files Google and AI assistants read at
 *     the top of your site: robots.txt and llms.txt (plain text only, a copy
 *     kept first, and never a robots.txt that shuts Google out of everything)
 *   - run a command, or read any other file
 *   - read or change your database, except your blog's posts table: it may add
 *     a post to it, and take away only a post it added itself
 *   - overwrite itself or your token
 *
 * And every edit copies the old file to a backup first. Nothing an agent does
 * is one-way: /rollback puts a page back exactly as it was, in one call, and
 * /blog-remove takes away a post the agency added.
 *
 * YOUR BLOG (you said yes to this: "add the posts to my backend direct")
 * The agency adds a post using the same database connection your own blog
 * page uses — it finds the file blog-details.php already includes. Your
 * database password never leaves your server and is never sent anywhere.
 * If it cannot find that file, make agency-api/blog-db.php with one line:
 *   <?php return ['host' => 'localhost', 'name' => 'DBNAME', 'user' => 'DBUSER', 'pass' => 'DBPASS'];
 *
 * You said you will fix whatever the agents break. This is here so that in
 * most cases you will not have to.
 */

declare(strict_types=1);

header('Content-Type: application/json');
header('X-Content-Type-Options: nosniff');
header('X-Robots-Tag: noindex, nofollow');   // never let Google index the door

// Version 9. The program reads this from /health and uses what each version
// can do — an older copy of this file still works, just without the newer parts.
//
// WHAT 9 ADDED — "how much data travels ... expand the api reading, writing and
// the data carrier abilities":
//   packed both ways      answers are gzip-packed when the agency asks (a page of
//                         HTML is usually 5 to 10 times smaller on the wire), and
//                         a big change can arrive packed too, which also keeps
//                         it under your host's upload limit
//   many changes, one go  /pages-batch writes up to 25 pages in one request, and
//                         all of them or none: if one cannot be written, every
//                         page the batch already changed is put back
//   robots.txt, llms.txt  the two text files at the top of your site, written
//                         the same careful way as a page
//   kinder to your host   a change waits a few seconds at most for another change
//                         to the same page, then says "busy" instead of holding
//                         your website's workers; reading many pages stops after
//                         a few seconds and hands back the rest for next time;
//                         the list of pages is remembered for a minute instead of
//                         walking your folders on every call
const CONNECTOR = 9;

// CLEAN ANSWERS, EVEN ON A NOISY HOST. Shared hosting often prints PHP notices
// and warnings straight into the page, and one line of that above the JSON
// means the agency cannot read the answer at all. Everything PHP prints by
// itself is caught here and thrown away, and a crash still answers in JSON.
@ini_set('display_errors', '0');
ob_start();
register_shutdown_function(function () {
    $e = error_get_last();
    if ($e && in_array($e['type'], [E_ERROR, E_PARSE, E_CORE_ERROR, E_COMPILE_ERROR], true)) {
        while (ob_get_level() > 0) { ob_end_clean(); }
        if (!headers_sent()) { http_response_code(500); header('Content-Type: application/json'); }
        echo json_encode(['ok' => false, 'error' => 'your website hit a PHP error: '
                          . substr((string)$e['message'], 0, 300)]);
    }
});

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

// ...and these two text files, at the top of the site only, by exact name.
// robots.txt tells search engines what to read and where the sitemap is;
// llms.txt is the newer, plain summary of a site that AI assistants read.
const ROOT_TEXT = ['robots.txt', 'llms.txt'];
const TEXT_MAX = 256 * 1024;

// The most pages one batch of changes may carry (/pages-batch).
const BATCH_MAX = 25;
// No single read keeps one of your website's workers busy for long: after a
// few seconds it answers with what it has, and names the rest for next time.
const READ_BUDGET = 4.0;

// Never, under any circumstances, even with a valid token.
//
// token.php is on this list now and it is the important addition. /health
// tells you to move your token OUT of token.txt and INTO token.php, and only
// token.txt was ever protected — so the file we tell you to use was the one
// file a write could quietly replace, locking the agency out of your own site
// until you went and looked at it by hand.
const NEVER_TOUCH = ['agency-api/index.php', 'agency-api/token.txt',
                     'agency-api/token.php', 'token.php',
                     '.htaccess', 'wp-config.php', 'config.php', 'db.php'];

// Folders that are not the public website, and are therefore not the agency's
// business — not to list, not to read, not to write. A visitor never loads
// these; there is no version of "improve the site" that involves editing them.
//
// The first two are why this list exists. Walking the site turned up seventeen
// files of the owner's ADMIN PANEL and two CONFIG files holding his database
// credentials. Blocking a couple of filenames was never enough: rename
// db.php to database.php and the old rule waved it through.
//
// The rest are folders that hold no pages at all — dependency trees, version
// control, uploads, caches, static assets. Skipping them keeps the listing
// honest and keeps a big media tree from turning one request into a minute of
// disk walking.
const OFF_LIMIT_DIRS = ['admin', 'config', 'private', 'logs', 'tmp',
                        'agency-api', '.git', '.svn', 'node_modules', 'vendor',
                        'wp-content', 'wp-includes', 'wp-admin', 'cache',
                        'uploads', 'backups', 'assets', 'images', 'img',
                        'fonts', 'css', 'js'];

/**
 * Is this page off limits? ONE rule, used by the listing and by every read and
 * every write, because the two disagreeing is exactly what went wrong.
 *
 * The listing used to test in_array($rel, NEVER_TOUCH, true) — an exact match
 * — while reads and writes tested the same name at the END of a path. So
 * 'config/db.php' was correctly refused for reading and cheerfully LISTED,
 * which put the owner's database credentials on a screen by name and dropped
 * his admin panel into the change-watcher's queue.
 *
 * And the folder rule lived only in the listing, so admin/login.php was hidden
 * from the list and still readable and writable to anyone holding the token.
 * Hidden is not protected. Both halves are here now, in one function, so the
 * door and the window cannot drift apart again.
 */
function is_forbidden(string $rel): bool {
    foreach (NEVER_TOUCH as $forbidden) {
        if (strcasecmp($rel, $forbidden) === 0
            || str_ends_with(strtolower($rel), '/' . strtolower($forbidden))) {
            return true;
        }
    }
    // Any folder on the path, not just the first — blog/admin/notes.php is as
    // much somebody's admin panel as admin/notes.php is.
    $parts = explode('/', strtolower($rel));
    array_pop($parts);                       // the filename is not a folder
    foreach ($parts as $folder) {
        if (in_array($folder, OFF_LIMIT_DIRS, true)) { return true; }
    }
    return false;
}

function reply(array $data, int $code = 200): void {
    while (ob_get_level() > 0) { ob_end_clean(); }
    http_response_code($code);
    // A page saved in an old encoding (not UTF-8) made json_encode give back
    // nothing at all, and the agency saw an empty answer. Odd bytes are shown
    // as a placeholder instead, and the page says it is not clean text
    // ('text_ok'), so the agency reads it but never rewrites it.
    $flags = JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE
           | (defined('JSON_INVALID_UTF8_SUBSTITUTE') ? JSON_INVALID_UTF8_SUBSTITUTE : 0);
    $json = (string)json_encode($data, $flags);
    // PACKED WHEN ASKED. An answer carrying pages is mostly HTML, which gzip
    // shrinks five to ten times — less to carry, and less time your server
    // spends sending it. Only when the agency says it can unpack it, only when
    // it is worth it, and never twice (a host already packing everything is
    // left to do it).
    $accept = (string)($_SERVER['HTTP_ACCEPT_ENCODING'] ?? '');
    if (strlen($json) > 1024 && stripos($accept, 'gzip') !== false && function_exists('gzencode')
        && !ini_get('zlib.output_compression') && !headers_sent()) {
        $packed = gzencode($json, 5);
        if (is_string($packed)) {
            header('Content-Encoding: gzip');
            header('Vary: Accept-Encoding');
            echo $packed;
            exit;
        }
    }
    echo $json;
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
function page_or_why(string $raw): array {
    $rel = ltrim(trim($raw), '/');
    if ($rel === '' || $rel === '/') {
        $rel = 'index.php';
    }
    // No traversal, no null bytes, no backslashes, no absolute paths.
    if (strpos($rel, "\0") !== false || strpos($rel, '..') !== false
        || strpos($rel, '\\') !== false || preg_match('#^[a-zA-Z]:#', $rel)) {
        return ['', 400, 'bad page name'];
    }
    if (is_forbidden($rel)) { return ['', 403, "$rel is off limits"]; }
    if (in_array($rel, ROOT_TEXT, true)) {
        return [SITE_ROOT . '/' . $rel, 200, ''];
    }
    $ext = strtolower(pathinfo($rel, PATHINFO_EXTENSION));
    if (!in_array($ext, ALLOWED_EXT, true)) {
        return ['', 400, 'only .php, .html and .htm pages — nothing else'];
    }
    $full = SITE_ROOT . '/' . $rel;
    $real = realpath($full);
    // For a NEW file realpath() is false, so check the parent folder instead.
    $check = $real !== false ? $real : realpath(dirname($full));
    if ($check === false || strpos($check, SITE_ROOT) !== 0) {
        return ['', 403, 'that is outside your website folder'];
    }
    return [$full, 200, ''];
}

function resolve_page(string $raw): string {
    [$full, $code, $why] = page_or_why($raw);
    if ($full === '') { fail($code, $why); }
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
    ensure_backup_dir();
    $name = str_replace('/', '__', relative_to_site($full))
          . '.' . date('Ymd-His') . '-' . bin2hex(random_bytes(3)) . '.bak';
    @copy($full, BACKUP_DIR . '/' . $name);
    return $name;
}

function ensure_backup_dir(): void {
    if (!is_dir(BACKUP_DIR)) {
        @mkdir(BACKUP_DIR, 0755, true);
        @file_put_contents(BACKUP_DIR . '/.htaccess',
                           "Require all denied\nDeny from all\n");
    }
}

/**
 * ONE CHANGE TO A PAGE AT A TIME. Two of the agency's tasks changing the same
 * page together each read it, each changed their copy, and the second write
 * quietly threw the first one's change away. Every change now holds the page
 * until it is finished; the other waits its turn. Let go when the answer is
 * sent.
 */
//
// A SHORT WAIT, NOT AN ENDLESS ONE (version 9). A change used to wait for as
// long as the other change took, and every waiting request is one of your
// website's few workers doing nothing — on shared hosting there may be only a
// handful, and visitors need them too. Now it waits a few seconds at most and
// then answers "busy", and the agency tries again a moment later.
const HOLD_WAIT = 8;
function hold_page(string $full) {
    ensure_backup_dir();
    $h = @fopen(BACKUP_DIR . '/.hold-' . md5($full), 'c');
    if (!$h) { return $h; }
    $until = microtime(true) + HOLD_WAIT;
    while (true) {
        $would = 0;
        if (@flock($h, LOCK_EX | LOCK_NB, $would)) { break; }
        if (!$would) { break; }          // this server cannot lock files at all: carry on as before
        if (microtime(true) >= $until) {
            fail(423, 'another change to ' . relative_to_site($full) . ' is still being made — '
                    . 'nothing was changed; the agency tries again in a moment');
        }
        usleep(100000);
    }
    return $h;
}

// The list of pages is remembered for a minute (see /pages), and forgotten the
// moment anything is written, so it is never out of date about the agency's own
// changes.
const LIST_CACHE_TTL = 60;
function list_cache_file(): string { return BACKUP_DIR . '/.list-cache.json'; }
function forget_listing(): void { @unlink(list_cache_file()); }

/**
 * Why this text is not fit to be robots.txt or llms.txt — '' when it is.
 * Plain text only: nothing a server would run, nothing a browser would run.
 */
function text_file_problem(string $rel, string $text): string {
    if (!in_array($rel, ROOT_TEXT, true)) { return ''; }
    if (strlen($text) > TEXT_MAX) { return "$rel would be larger than 256 KB"; }
    if (strpos($text, "\0") !== false || preg_match('/<\?|<script|<\/?html/i', $text)) {
        return "$rel must be plain text — no code of any kind";
    }
    if (preg_match('//u', $text) !== 1) { return "$rel must be UTF-8 text"; }
    if ($rel === 'robots.txt' && shuts_everyone_out($text)) {
        return 'refusing a robots.txt that shuts every search engine out of the whole website';
    }
    return '';
}

/** Does this robots.txt tell every crawler to read nothing at all? */
function shuts_everyone_out(string $robots): bool {
    $groups = preg_split('/\n(?=\s*user-agent\s*:)/i', str_replace("\r", '', $robots));
    foreach ((array)$groups as $g) {
        if (preg_match('/^\s*user-agent\s*:\s*\*\s*$/im', $g)
            && preg_match('/^\s*disallow\s*:\s*\/\s*$/im', $g)
            && !preg_match('/^\s*allow\s*:\s*\/\S/im', $g)) {
            return true;
        }
    }
    return false;
}

/**
 * WRITE THE WHOLE PAGE OR NONE OF IT. A write cut off half-way — a timeout, a
 * full disk, the host stopping the script — used to leave half a page on the
 * live site. The new page is written beside it first and swapped in whole, so
 * a visitor sees the old page or the new one, never a broken one.
 */
function write_whole(string $full, string $data): bool {
    $tmp = dirname($full) . '/.agency-' . bin2hex(random_bytes(4)) . '.tmp';
    if (@file_put_contents($tmp, $data, LOCK_EX) !== strlen($data)) {
        @unlink($tmp);
        return false;
    }
    @chmod($tmp, is_file($full) ? (fileperms($full) & 0777) : 0644);
    if (@rename($tmp, $full)) { return true; }
    // A few Windows hosts will not swap over an existing file.
    @unlink($tmp);
    return @file_put_contents($full, $data, LOCK_EX) === strlen($data);
}

// What a page's text is, as a fingerprint: the agency compares it with the one
// it read, so it can tell a page has changed under it before writing.
function fingerprint(string $s): string { return hash('sha256', $s); }

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
// A body larger than the host allows reaches PHP as nothing at all, which used
// to surface as a baffling "which page?". Said plainly now, with the limit.
$raw_body = (string)file_get_contents('php://input');
if ($method === 'POST' && (int)($_SERVER['CONTENT_LENGTH'] ?? 0) > 0 && $raw_body === '') {
    fail(413, 'that was more than your host lets a website receive in one go (post_max_size is '
            . ini_get('post_max_size') . ') — nothing was changed');
}
// A big change can arrive packed (version 9): it is unpacked here, with a
// ceiling so a small packed request can never unpack into something huge.
if ($raw_body !== '' && stripos((string)($_SERVER['HTTP_CONTENT_ENCODING'] ?? ''), 'gzip') !== false) {
    $plain = function_exists('gzdecode') ? @gzdecode($raw_body, 48 * 1024 * 1024) : false;
    if (!is_string($plain)) {
        fail(400, 'the request arrived packed but could not be unpacked — nothing was changed');
    }
    $raw_body = $plain;
}
// A write carries on to the end even if the line drops half-way, so a batch
// is never left part-done by a dropped connection.
if ($method === 'POST') { @ignore_user_abort(true); }
$body = $raw_body === '' ? [] : json_decode($raw_body, true);
if (!is_array($body)) {
    fail(400, 'the request arrived incomplete (it was not whole JSON) — nothing was changed');
}

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
    // A half-written page left by a script the host stopped mid-way.
    foreach (array_merge((array)glob(SITE_ROOT . '/.agency-*.tmp'), (array)glob(SITE_ROOT . '/*/.agency-*.tmp')) as $t) {
        if (is_file($t) && filemtime($t) < time() - 3600) { @unlink($t); }
    }
    reply(['ok' => true, 'status' => 'up', 'time' => time(),
           'site_root' => SITE_ROOT, 'php' => PHP_VERSION,
           'writable' => is_writable(SITE_ROOT),
           'mode' => 'full-access', 'warnings' => $warn,
           'connector' => CONNECTOR,
           'can' => ['pages', 'head', 'rollback', 'blog', 'remove', 'pictures', 'batch', 'safe-write', 'fingerprint',
                     'gzip', 'batch-write', 'text-files', 'short-holds'],
           'packing' => function_exists('gzencode') && function_exists('gzdecode'),
           'limits' => ['batch_max' => BATCH_MAX, 'read_budget_seconds' => READ_BUDGET,
                        'post_max_size' => (string)ini_get('post_max_size'),
                        'upload_max_filesize' => (string)ini_get('upload_max_filesize'),
                        'memory_limit' => (string)ini_get('memory_limit'),
                        'max_execution_time' => (int)ini_get('max_execution_time')]]);
}

// --- what pages are there --------------------------------------------------
//
// THE TOP LEVEL IS NOT THE WEBSITE.
//
// "why home.php and other pages and tasks are not getting traced?"
//
// Because this listed SITE_ROOT/*.{php,html,htm} and stopped there. Anything
// in a folder — services/, blog/, includes/ — did not exist as far as the
// agency was concerned: not watched for changes, not audited, not counted,
// never written to. The one include it did see, includes/header.php, was only
// there because site_watch guesses at the usual locations, and a guess that
// sometimes fails to read is what told him his header had vanished.
//
// So it walks the site now. The skips are the folders that are never pages and
// would swamp the list — dependency trees, version control, uploads and caches
// — plus anything NEVER_TOUCH already forbids. The depth and count caps are
// there so a site with a huge media tree cannot turn one request into a
// minute of disk walking.
// The folders to walk past are OFF_LIMIT_DIRS, defined up top with the rest of
// the safety rules. The listing does not keep its own copy of that list any
// more — a second copy is a second thing to forget to update.
const MAX_PAGES = 400;
const MAX_DEPTH = 4;

function list_pages(string $dir, string $prefix = '', int $depth = 0): array {
    if ($depth > MAX_DEPTH) { return []; }
    $out = [];
    $entries = @scandir($dir);
    if ($entries === false) { return []; }
    foreach ($entries as $name) {
        if ($name === '.' || $name === '..' || $name[0] === '.') { continue; }
        $full = $dir . '/' . $name;
        $rel  = $prefix === '' ? $name : $prefix . '/' . $name;
        if (is_dir($full)) {
            if (in_array(strtolower($name), OFF_LIMIT_DIRS, true)) { continue; }
            $out = array_merge($out, list_pages($full, $rel, $depth + 1));
            if (count($out) >= MAX_PAGES) { break; }
            continue;
        }
        $ext = strtolower(pathinfo($name, PATHINFO_EXTENSION));
        if (!in_array($ext, ['php', 'html', 'htm'], true)) { continue; }
        if (is_forbidden($rel)) { continue; }
        $out[] = ['page' => $rel, 'bytes' => filesize($full),
                  'changed' => date('c', (int)filemtime($full))];
        if (count($out) >= MAX_PAGES) { break; }
    }
    return $out;
}

function text_files(): array {
    $out = [];
    foreach (ROOT_TEXT as $t) {
        $f = SITE_ROOT . '/' . $t;
        $out[] = ['file' => $t, 'exists' => is_file($f), 'bytes' => is_file($f) ? filesize($f) : 0,
                  'changed' => is_file($f) ? date('c', (int)filemtime($f)) : ''];
    }
    return $out;
}

if ($route === '/pages' && $method === 'GET') {
    // Remembered for a minute: walking every folder on every call was the
    // single heaviest thing the agency asked of a small host.
    $cache = list_cache_file();
    if (empty($_GET['fresh']) && is_file($cache) && filemtime($cache) > time() - LIST_CACHE_TTL) {
        $kept = json_decode((string)@file_get_contents($cache), true);
        if (is_array($kept) && isset($kept['pages'])) {
            $kept['cached'] = true;
            $kept['files'] = text_files();
            reply($kept);
        }
    }
    $out = list_pages(SITE_ROOT);
    $answer = ['ok' => true, 'pages' => $out, 'counted' => count($out),
               'capped' => count($out) >= MAX_PAGES];
    ensure_backup_dir();
    @file_put_contents($cache, json_encode($answer, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE), LOCK_EX);
    $answer['files'] = text_files();
    reply($answer);
}

// --- take a page away (a copy is always kept first) ---------------------------
//
// "add a delete button that will delete the posts from the website" — pages the
// agency put up as posts of their own, before posts went into your blog. The
// page is copied to backups/ first, exactly as an edit is, so /rollback brings
// it back. Your home page is never removed.
if ($route === '/pages-remove' && $method === 'POST') {
    $name = (string)($body['page'] ?? '');
    if ($name === '') { fail(400, 'which page?'); }
    $full = resolve_page($name);
    $rel = relative_to_site($full);
    if (in_array(strtolower($rel), ['index.php', 'home.php', 'index.html', 'index.htm', 'default.php'], true)) {
        fail(403, 'your home page is never removed');
    }
    if (!is_file($full)) {
        reply(['ok' => true, 'page' => $rel, 'action' => 'already gone', 'backup' => '']);
    }
    $hold = hold_page($full);
    $backup = back_up($full);
    if ($backup === '' || !is_file(BACKUP_DIR . '/' . $backup)) {
        fail(500, 'could not keep a copy first, so the page was left where it is');
    }
    if (!@unlink($full)) { fail(500, 'could not remove the file — check the folder permissions'); }
    forget_listing();
    reply(['ok' => true, 'page' => $rel, 'action' => 'removed', 'backup' => $backup,
           'note' => 'a copy is kept — /rollback puts it back']);
}

// --- read one page ---------------------------------------------------------
if (preg_match('#^/pages/(.+)$#', $route, $m) && $method === 'GET') {
    $full = resolve_page(urldecode($m[1]));
    if (!is_file($full)) { fail(404, 'no such page'); }
    $html = (string)file_get_contents($full);
    reply(['ok' => true, 'page' => relative_to_site($full),
           'html' => $html, 'sha256' => fingerprint($html),
           'text_ok' => preg_match('//u', $html) === 1,
           'bytes' => filesize($full),
           'changed' => date('c', (int)filemtime($full))]);
}

// --- read many pages in one go ----------------------------------------------
//
// Reading forty pages one request at a time was forty knocks on the door, one
// a second, and every other task queued behind them — and forty knocks is what
// a host's firewall counts. One request now carries up to sixty pages, as much
// as fits in a few megabytes; what did not fit comes back named, for the next.
const MANY_MAX = 60;
const MANY_BYTES = 6 * 1024 * 1024;
if ($route === '/pages-many' && $method === 'POST') {
    $names = array_slice(array_values(array_filter((array)($body['pages'] ?? []), 'is_string')), 0, 200);
    $out = [];
    $more = [];
    $total = 0;
    $t0 = microtime(true);
    foreach ($names as $i => $name) {
        if (count($out) >= MANY_MAX || $total >= MANY_BYTES
            || ($out && microtime(true) - $t0 > READ_BUDGET)) { $more = array_slice($names, $i); break; }
        [$full, $code, $why] = page_or_why($name);
        if ($full === '') { $out[] = ['page' => $name, 'ok' => false, 'status' => $code, 'error' => $why]; continue; }
        if (!is_file($full)) { $out[] = ['page' => $name, 'ok' => false, 'status' => 404, 'error' => 'no such page']; continue; }
        $html = (string)file_get_contents($full);
        $total += strlen($html);
        $out[] = ['page' => relative_to_site($full), 'asked' => $name, 'ok' => true, 'html' => $html,
                  'sha256' => fingerprint($html), 'bytes' => strlen($html),
                  'text_ok' => preg_match('//u', $html) === 1,
                  'changed' => date('c', (int)filemtime($full))];
    }
    reply(['ok' => true, 'pages' => $out, 'more' => $more]);
}

// --- write one page (the old one is always kept) ---------------------------
if ($route === '/pages' && $method === 'POST') {
    $name = (string)($body['page'] ?? $body['slug'] ?? '');
    $html = (string)($body['html'] ?? '');
    if ($name === '') { fail(400, 'which page?'); }
    if (trim($html) === '') { fail(400, 'refusing to write an empty page'); }
    $full = resolve_page($name);
    $problem = text_file_problem(relative_to_site($full), $html);
    if ($problem !== '') { fail(400, $problem . ' — nothing was changed'); }
    $hold = hold_page($full);
    $now = is_file($full) ? (string)file_get_contents($full) : null;
    // Writing the same bytes back is not a change. Skipping it keeps the
    // backup folder honest: one entry per real edit, not one per nightly run.
    if ($now !== null && $now === $html) {
        reply(['ok' => true, 'page' => relative_to_site($full),
               'action' => 'unchanged', 'backup' => '', 'sha256' => fingerprint($html),
               'note' => 'already exactly this — nothing written']);
    }
    // CHANGED SINCE IT WAS READ? The agency sends the fingerprint of the page
    // it based this change on. If the page is no longer that — you edited it,
    // or another task did — it is left alone rather than overwritten.
    $expect = strtolower(trim((string)($body['expect_sha'] ?? '')));
    if ($expect !== '' && $now !== null && fingerprint($now) !== $expect) {
        reply(['ok' => false, 'changed' => true, 'page' => relative_to_site($full),
               'sha256' => fingerprint($now),
               'error' => 'the page changed on your website after the agency read it, so it was '
                        . 'left exactly as it is — the agency reads it again before changing it'], 409);
    }
    $backup = back_up($full);
    if (!write_whole($full, $html)) {
        fail(500, 'could not write the file — check folder permissions in cPanel');
    }
    forget_listing();
    reply(['ok' => true, 'page' => relative_to_site($full),
           'bytes' => strlen($html), 'backup' => $backup, 'sha256' => fingerprint($html),
           'note' => $backup ? 'previous version kept — /rollback restores it'
                             : 'new page, nothing to keep']);
}

// --- write many pages in one go, all or none (version 9) --------------------
//
// A whole-site change — one email address put right on every page, say — used
// to be one request per page, each its own knock on the door, and if the line
// dropped half-way the site was left half old and half new. Now it is one
// request carrying up to 25 pages, and it is all of them or none of them:
//
//   every page is checked first   names, sizes, and that each page is still the
//                                 one the agency read (fingerprints) — one page
//                                 changed under it and nothing is written
//   every old page is kept        a copy in backups/, exactly as for one page
//   if one write fails            every page this batch already changed is put
//                                 back as it was, before the answer goes back
if ($route === '/pages-batch' && $method === 'POST') {
    $changes = array_values(array_filter((array)($body['changes'] ?? []), 'is_array'));
    if (!$changes) { fail(400, 'no changes were sent'); }
    if (count($changes) > BATCH_MAX) { fail(413, 'at most ' . BATCH_MAX . ' changes in one go — nothing was changed'); }
    $plan = [];
    $total = 0;
    foreach ($changes as $i => $c) {
        $name = (string)($c['page'] ?? '');
        $html = (string)($c['html'] ?? '');
        if ($name === '') { fail(400, 'change ' . ($i + 1) . ': which page? — nothing was changed'); }
        if (trim($html) === '') { fail(400, "change " . ($i + 1) . " ($name): refusing to write an empty page — nothing was changed"); }
        [$full, $code, $why] = page_or_why($name);
        if ($full === '') { fail($code, "change " . ($i + 1) . " ($name): $why — nothing was changed"); }
        $rel = relative_to_site($full);
        $problem = text_file_problem($rel, $html);
        if ($problem !== '') { fail(400, $problem . ' — nothing was changed'); }
        if (isset($plan[$full])) { fail(400, "$rel is in this batch twice — nothing was changed"); }
        $total += strlen($html);
        $plan[$full] = ['rel' => $rel, 'html' => $html,
                        'expect' => strtolower(trim((string)($c['expect_sha'] ?? '')))];
    }
    if ($total > MANY_BYTES) { fail(413, 'more than 6 MB of pages in one go — nothing was changed'); }
    // Always the same order, so two batches can never each hold a page the
    // other is waiting for.
    ksort($plan);
    $holds = [];
    foreach (array_keys($plan) as $full) { $holds[] = hold_page($full); }
    $stale = [];
    foreach ($plan as $full => $p) {
        $plan[$full]['now'] = is_file($full) ? (string)file_get_contents($full) : null;
        $now = $plan[$full]['now'];
        if ($p['expect'] !== '' && $now !== null && fingerprint($now) !== $p['expect']) {
            $stale[] = ['page' => $p['rel'], 'sha256' => fingerprint($now)];
        }
    }
    if ($stale) {
        reply(['ok' => false, 'changed' => true, 'stale' => $stale,
               'error' => 'some pages changed on your website after the agency read them, so nothing '
                        . 'was written — the agency reads them again before changing them'], 409);
    }
    $done = [];
    $out = [];
    foreach ($plan as $full => $p) {
        if ($p['now'] !== null && $p['now'] === $p['html']) {
            $out[] = ['page' => $p['rel'], 'ok' => true, 'action' => 'unchanged', 'backup' => '',
                      'sha256' => fingerprint($p['html']), 'bytes' => strlen($p['html'])];
            continue;
        }
        $backup = back_up($full);
        $kept = $p['now'] === null || ($backup !== '' && is_file(BACKUP_DIR . '/' . $backup));
        if (!$kept || !write_whole($full, $p['html'])) {
            foreach (array_reverse($done) as $d) {
                if ($d['before'] === null) { @unlink($d['full']); } else { write_whole($d['full'], $d['before']); }
            }
            forget_listing();
            fail(500, 'could not ' . ($kept ? 'write ' : 'keep a copy of ') . $p['rel']
                    . ' — every page this batch had already changed was put back, so nothing changed');
        }
        $done[] = ['full' => $full, 'before' => $p['now']];
        $out[] = ['page' => $p['rel'], 'ok' => true, 'action' => $p['now'] === null ? 'new' : 'written',
                  'backup' => $backup, 'sha256' => fingerprint($p['html']), 'bytes' => strlen($p['html'])];
    }
    forget_listing();
    reply(['ok' => true, 'pages' => $out, 'written' => count($done)]);
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
    $hold = hold_page($full);
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
               'action' => 'unchanged', 'backup' => '', 'sha256' => fingerprint($src),
               'note' => 'the block was already there and identical']);
    }
    $backup = back_up($full);
    if (!write_whole($full, $new)) {
        fail(500, 'could not write the file');
    }
    forget_listing();
    reply(['ok' => true, 'page' => relative_to_site($full), 'id' => $mark,
           'action' => $what, 'backup' => $backup, 'sha256' => fingerprint($new)]);
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
    $hold = hold_page($full);
    back_up($full);                          // keep the bad one too, just in case
    $old = (string)file_get_contents($src);
    if (!write_whole($full, $old)) { fail(500, 'could not restore'); }
    forget_listing();
    reply(['ok' => true, 'page' => relative_to_site($full),
           'restored_from' => $which, 'sha256' => fingerprint($old)]);
}

// ---------------------------------------------------------------------------
// YOUR BLOG
//
// "yes add the posts to my backend direct"
//
// Your posts live in your database and blog-details.php shows them in your
// theme. A post saved as a file of its own sat outside all of that, so these
// three calls put a post where your own backend would:
//
//   GET  ?do=blog              which table holds your posts, and its columns
//   GET  ?do=blog&page=SLUG    is there already a post at that slug
//   POST ?do=blog              add one post (and save its picture)
//   POST ?do=blog-remove       take away a post THIS FILE added — no other
//
// ONE TABLE. The table is worked out here, on your server, from the query your
// own blog-details.php runs — never taken from the request — so a token can
// never be used to reach any other table. Nothing here reads a password,
// sends one anywhere, or runs anything but the insert, the delete of its own
// row, and the reads needed to check them.
// ---------------------------------------------------------------------------

// The posts this file added, so it can take away its own and nothing else.
// Kept in the backups folder, which nobody can browse.
const BLOG_RECORD = BACKUP_DIR . '/blog-posts.json';
const BLOG_PAGES = ['blog-details.php', 'blog-detail.php', 'blog_details.php', 'single-blog.php',
                    'blog-single.php', 'post.php', 'article.php', 'news-details.php', 'blog.php',
                    'blogs.php'];
const CONNECTION_GUESSES = ['config.php', 'db.php', 'database.php', 'connection.php', 'conn.php',
                            'connect.php', 'dbconnect.php', 'db_connect.php', 'db_config.php',
                            'includes/db.php', 'includes/config.php', 'includes/connection.php',
                            'includes/database.php', 'inc/db.php', 'inc/config.php',
                            'config/db.php', 'config/database.php', 'config/config.php',
                            'admin/config.php', 'admin/db.php', 'admin/connection.php',
                            'admin/includes/db.php', 'admin/includes/config.php'];
// A picture is only ever saved into your blog's own picture folder, and never
// into any of these, whatever that folder turns out to be.
const IMAGE_NEVER_DIRS = ['admin', 'config', 'private', 'logs', 'tmp', 'agency-api', '.git', '.svn',
                          'node_modules', 'vendor', 'backups', 'cache', 'wp-admin', 'wp-includes'];
const IMAGE_MAX = 8 * 1024 * 1024;
const SECRET_COLUMN = '/pass|token|secret|hash|salt|api_?key/i';

function site_src(string $rel): string {
    $f = SITE_ROOT . '/' . $rel;
    return is_file($f) ? (string)file_get_contents($f) : '';
}

function includes_in(string $src): array {
    preg_match_all('/(?:include|require)(?:_once)?\s*\(?\s*(?:__DIR__\s*\.\s*)?[\'"]([^\'"]+\.php)[\'"]/i',
                   $src, $m);
    return $m[1] ?? [];
}

function clean_rel(string $rel): string {
    $rel = ltrim(str_replace('\\', '/', trim($rel)), '/');
    while (str_starts_with($rel, './')) { $rel = substr($rel, 2); }
    return $rel;
}

// A connection file connects and nothing else. One that sends visitors to a
// login page would end this request, so it is never opened.
function looks_like_connection(string $src): bool {
    return (bool)preg_match('/new\s+PDO\s*\(|mysqli_connect\s*\(|new\s+mysqli\s*\(/i', $src)
        && !preg_match('/header\s*\(\s*[\'"]\s*location/i', $src);
}

$GLOBALS['agency_connecting'] = '';
register_shutdown_function(function () {
    // A connection file that calls die() on a failed connection ends the
    // request mid-way. Say so in words instead of leaving half a reply.
    if (!empty($GLOBALS['agency_connecting'])) {
        while (ob_get_level() > 0) { ob_end_clean(); }
        http_response_code(500);
        echo json_encode(['ok' => false, 'error' => 'your site\'s database connection file ('
            . basename((string)$GLOBALS['agency_connecting']) . ') stopped while connecting, '
            . 'so nothing was changed. Your blog may be having database trouble right now.']);
    }
});

function open_connection(string $full) {
    $here = getcwd();
    @chdir(dirname($full));
    $GLOBALS['agency_connecting'] = $full;
    $handle = (static function (string $__file) {
        ob_start();
        try {
            include $__file;
        } catch (Throwable $e) {
            ob_end_clean();
            return null;
        }
        ob_end_clean();
        foreach (get_defined_vars() as $v) {
            if ($v instanceof PDO || $v instanceof mysqli) { return $v; }
        }
        // Some sites only define the details and connect later.
        if (defined('DB_HOST') && defined('DB_NAME') && defined('DB_USER')) {
            $pass = defined('DB_PASS') ? constant('DB_PASS')
                  : (defined('DB_PASSWORD') ? constant('DB_PASSWORD') : '');
            try {
                return new PDO('mysql:host=' . constant('DB_HOST') . ';dbname=' . constant('DB_NAME')
                               . ';charset=utf8mb4', (string)constant('DB_USER'), (string)$pass);
            } catch (Throwable $e) {
                return null;
            }
        }
        return null;
    })($full);
    $GLOBALS['agency_connecting'] = '';
    if ($here !== false) { @chdir($here); }
    return $handle;
}

final class BlogDb {
    public $h;
    public string $via;
    public function __construct($h, string $via) {
        $this->h = $h;
        $this->via = $via;
        if ($h instanceof PDO) { $h->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION); }
    }
    public function driver(): string {
        return $this->h instanceof PDO ? (string)$this->h->getAttribute(PDO::ATTR_DRIVER_NAME) : 'mysql';
    }
    public function rows(string $sql, array $args = []): array {
        if ($this->h instanceof PDO) {
            $st = $this->h->prepare($sql);
            $st->execute(array_values($args));
            return $st->fetchAll(PDO::FETCH_ASSOC);
        }
        $st = $this->h->prepare($sql);
        if (!$st) { throw new RuntimeException((string)$this->h->error); }
        if ($args) {
            $vals = array_values($args);
            $st->bind_param(str_repeat('s', count($vals)), ...$vals);
        }
        if (!$st->execute()) { throw new RuntimeException((string)$st->error); }
        $res = $st->get_result();
        return $res ? $res->fetch_all(MYSQLI_ASSOC) : [];
    }
    public function run(string $sql, array $args = []): string {
        if ($this->h instanceof PDO) {
            $st = $this->h->prepare($sql);
            $st->execute(array_values($args));
            return (string)$this->h->lastInsertId();
        }
        $st = $this->h->prepare($sql);
        if (!$st) { throw new RuntimeException((string)$this->h->error); }
        if ($args) {
            $vals = array_values($args);
            $st->bind_param(str_repeat('s', count($vals)), ...$vals);
        }
        if (!$st->execute()) { throw new RuntimeException((string)$st->error); }
        return (string)$this->h->insert_id;
    }
}

function db_problem(Throwable $e): string {
    // The database's own words, minus anything that looks like a login.
    $msg = preg_replace('/(user|password|using password)[^,;)]*/i', '$1 …', $e->getMessage());
    return substr((string)$msg, 0, 240);
}

function blog_db(): BlogDb {
    $own = __DIR__ . '/blog-db.php';
    if (is_file($own)) {
        $cfg = @include $own;
        if (is_array($cfg) && !empty($cfg['name'])) {
            try {
                $pdo = new PDO('mysql:host=' . ($cfg['host'] ?? 'localhost') . ';dbname=' . $cfg['name']
                               . ';charset=utf8mb4', (string)($cfg['user'] ?? ''), (string)($cfg['pass'] ?? ''));
                return new BlogDb($pdo, 'agency-api/blog-db.php');
            } catch (Throwable $e) {
                fail(500, 'agency-api/blog-db.php is there but did not connect: ' . db_problem($e));
            }
        }
    }
    // The file your own blog page pulls in — then what THAT pulls in — then
    // the usual names. Opened here, on your server; its contents go nowhere.
    $queue = [];
    foreach (BLOG_PAGES as $p) {
        foreach (includes_in(site_src($p)) as $inc) { $queue[] = clean_rel($inc); }
    }
    $seen = [];
    for ($i = 0; $i < count($queue) && $i < 20; $i++) {
        $rel = $queue[$i];
        if (isset($seen[$rel]) || strpos($rel, '..') !== false) { continue; }
        $seen[$rel] = true;
        foreach (includes_in(site_src($rel)) as $inc) { $queue[] = clean_rel($inc); }
    }
    foreach (array_unique(array_merge(array_keys($seen), CONNECTION_GUESSES)) as $rel) {
        if (strpos($rel, '..') !== false || str_starts_with(strtolower($rel), 'agency-api/')
            || preg_match('/token/i', $rel)) { continue; }
        $full = realpath(SITE_ROOT . '/' . $rel);
        if ($full === false || strpos($full, SITE_ROOT) !== 0 || !is_file($full)) { continue; }
        if (!looks_like_connection((string)file_get_contents($full))) { continue; }
        $h = open_connection($full);
        if ($h) { return new BlogDb($h, basename($rel)); }
    }
    fail(422, 'Could not find the database connection your blog uses. Make '
            . "agency-api/blog-db.php with one line:  <?php return ['host' => 'localhost', "
            . "'name' => 'DBNAME', 'user' => 'DBUSER', 'pass' => 'DBPASS'];  using the details "
            . 'from hPanel → Databases. It stays on your server.');
}

function safe_name(string $t): string {
    if (!preg_match('/^[A-Za-z0-9_]+$/', $t)) { fail(400, 'bad table or column name'); }
    return $t;
}

function q_name(BlogDb $db, string $t): string {
    return $db->driver() === 'mysql' ? '`' . safe_name($t) . '`' : '"' . safe_name($t) . '"';
}

function all_tables(BlogDb $db): array {
    if ($db->driver() === 'sqlite') {
        return array_column($db->rows("SELECT name FROM sqlite_master WHERE type='table'"), 'name');
    }
    return array_map(fn($r) => (string)array_values($r)[0], $db->rows('SHOW TABLES'));
}

function columns_of(BlogDb $db, string $t): array {
    $out = [];
    if ($db->driver() === 'sqlite') {
        foreach ($db->rows('PRAGMA table_info(' . q_name($db, $t) . ')') as $c) {
            $pk = (int)$c['pk'] === 1;
            $out[] = ['name' => $c['name'], 'type' => strtolower((string)$c['type']),
                      'null' => !(int)$c['notnull'] && !$pk, 'key' => $pk ? 'PRI' : '',
                      'default' => $c['dflt_value'],
                      'auto' => $pk && stripos((string)$c['type'], 'int') !== false];
        }
        return $out;
    }
    foreach ($db->rows('SHOW COLUMNS FROM ' . q_name($db, $t)) as $c) {
        $out[] = ['name' => $c['Field'], 'type' => strtolower((string)$c['Type']),
                  'null' => strtoupper((string)$c['Null']) === 'YES', 'key' => (string)$c['Key'],
                  'default' => $c['Default'],
                  'auto' => stripos((string)$c['Extra'], 'auto_increment') !== false];
    }
    return $out;
}

/** [table, columns] — the posts table your blog page reads, worked out here. */
function blog_table(BlogDb $db): array {
    $all = all_tables($db);
    $by_slug = $named = [];
    foreach (BLOG_PAGES as $p) {
        $src = site_src($p);
        if (preg_match_all('/\bFROM\s+[`"]?([A-Za-z0-9_]+)[`"]?([^;"\']{0,200})/i', $src, $m, PREG_SET_ORDER)) {
            foreach ($m as $hit) {
                if (stripos($hit[2], 'slug') !== false) { $by_slug[] = $hit[1]; } else { $named[] = $hit[1]; }
            }
        }
    }
    $guess = array_filter($all, fn($t) => preg_match('/blog|post|article|news/i', $t)
                                          && !preg_match('/categor|comment|tag|like|view|user|admin|meta|image|gallery/i', $t));
    $order = array_values(array_unique(array_merge($by_slug, $named, $guess)));
    foreach ($order as $t) {
        if (!in_array($t, $all, true)) { continue; }
        $cols = columns_of($db, $t);
        $names = implode(' ', array_column($cols, 'name'));
        if (preg_match('/slug/i', $names) && preg_match('/content|body|description|detail|text|article/i', $names)) {
            return [$t, $cols];
        }
    }
    fail(422, 'Connected to your database, but could not tell which table holds your blog '
            . 'posts — none has a slug and a content column.');
}

function pk_of(array $cols): string {
    foreach ($cols as $c) { if ($c['key'] === 'PRI') { return $c['name']; } }
    return '';
}

function slug_col(array $cols): string {
    foreach ($cols as $c) { if (preg_match('/slug/i', $c['name'])) { return $c['name']; } }
    return '';
}

/** How your post page shows each column: a folder in front of a picture, text escaped or not. */
function blog_display(): array {
    $page = '';
    foreach (BLOG_PAGES as $p) {
        if (is_file(SITE_ROOT . '/' . $p) && !preg_match('/^blogs?\.php$/', $p)) { $page = $p; break; }
    }
    $src = $page ? site_src($page) : '';
    $list = site_src('blog.php') . site_src('blogs.php');
    $var = '\$\w+(?:\s*\[\s*["\'](\w+)["\']\s*\]|->(\w+))';
    $prefix = [];
    if (preg_match_all('/src\s*=\s*["\']([^"\'<>]*)<\?(?:php\s+echo\s+|=\s*)(?:\w+\s*\(\s*)*' . $var . '/i',
                       $src . $list, $m, PREG_SET_ORDER)) {
        foreach ($m as $hit) {
            $col = ($hit[2] ?? '') !== '' ? $hit[2] : ($hit[3] ?? '');
            if ($col !== '' && !isset($prefix[$col])) { $prefix[$col] = $hit[1]; }
        }
    }
    // A folder put in front of the picture through a variable or a constant:
    //   $img = 'admin/uploads/' . $blog['image'];   define('UPLOADS', 'uploads/');
    if (preg_match_all('/=\s*["\']([^"\'<>]*\/)["\']\s*\.\s*' . $var . '/i', $src . $list, $m, PREG_SET_ORDER)) {
        foreach ($m as $hit) {
            $col = ($hit[2] ?? '') !== '' ? $hit[2] : ($hit[3] ?? '');
            if ($col !== '' && !isset($prefix[$col])) { $prefix[$col] = $hit[1]; }
        }
    }
    if (preg_match_all('/define\s*\(\s*["\'](\w+)["\']\s*,\s*["\']([^"\']*)["\']/i', $src . $list, $dm, PREG_SET_ORDER)) {
        foreach ($dm as $d) {
            if (preg_match_all('/\b' . preg_quote($d[1], '/') . '\s*\.\s*' . $var . '/i', $src . $list, $m, PREG_SET_ORDER)) {
                foreach ($m as $hit) {
                    $col = ($hit[1] ?? '') !== '' ? $hit[1] : ($hit[2] ?? '');
                    if ($col !== '' && !isset($prefix[$col])) { $prefix[$col] = $d[2]; }
                }
            }
        }
    }
    // HOW THE ARTICLE IS SHOWN — in the body only. Nearly every post page also
    // prints the article, stripped of its tags, into the <head> for Google;
    // reading that as "this page shows the article as plain text" sent posts
    // in as text with no headings. The <head> and tag attributes are left out.
    $body = preg_replace('/<head\b.*?<\/head>/is', '', $src);
    $body = preg_replace('/\b(?:content|alt|title|value)\s*=\s*"[^"]*"/i', '', (string)$body);
    $escaped = $raw = [];
    if (preg_match_all('/(?:htmlspecialchars|htmlentities|strip_tags)\s*\(\s*' . $var . '/i', (string)$body, $m, PREG_SET_ORDER)) {
        foreach ($m as $hit) { $escaped[] = ($hit[1] ?? '') !== '' ? $hit[1] : ($hit[2] ?? ''); }
    }
    if (preg_match_all('/(?:echo|print|<\?=)\s*(?:(?:html_entity_decode|htmlspecialchars_decode|nl2br|stripslashes|trim)\s*\(\s*)*' . $var . '/i',
                       (string)$body, $m, PREG_SET_ORDER)) {
        foreach ($m as $hit) { $raw[] = ($hit[1] ?? '') !== '' ? $hit[1] : ($hit[2] ?? ''); }
    }
    $param = preg_match('/\$_GET\s*\[\s*["\'](\w+)["\']\s*\]/', $src, $pm) ? $pm[1] : 'slug';
    return ['page' => $page, 'param' => $param, 'prefix' => $prefix,
            'escaped' => array_values(array_unique(array_filter($escaped))),
            'raw' => array_values(array_unique(array_filter($raw)))];
}

function blog_record(): array {
    $r = is_file(BLOG_RECORD) ? json_decode((string)file_get_contents(BLOG_RECORD), true) : [];
    return is_array($r) ? $r : [];
}

function save_record(array $r): void {
    if (!is_dir(BACKUP_DIR)) {
        @mkdir(BACKUP_DIR, 0755, true);
        @file_put_contents(BACKUP_DIR . '/.htaccess', "Require all denied\nDeny from all\n");
    }
    @file_put_contents(BLOG_RECORD, json_encode(array_values($r), JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES));
}

function fetch_picture(string $url): string {
    $host = (string)parse_url($url, PHP_URL_HOST);
    $ip = $host !== '' ? gethostbyname($host) : '';
    if ($ip === '' || !filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_NO_PRIV_RANGE | FILTER_FLAG_NO_RES_RANGE)) {
        return '';
    }
    if (function_exists('curl_init')) {
        $ch = curl_init($url);
        curl_setopt_array($ch, [CURLOPT_RETURNTRANSFER => true, CURLOPT_FOLLOWLOCATION => true,
                                CURLOPT_MAXREDIRS => 3, CURLOPT_TIMEOUT => 25,
                                CURLOPT_PROTOCOLS => CURLPROTO_HTTP | CURLPROTO_HTTPS,
                                CURLOPT_USERAGENT => 'agency-api (saving a blog picture)']);
        $data = curl_exec($ch);
        curl_close($ch);
        return is_string($data) ? $data : '';
    }
    $ctx = stream_context_create(['http' => ['timeout' => 25, 'follow_location' => 1, 'max_redirects' => 3]]);
    return (string)@file_get_contents($url, false, $ctx, 0, IMAGE_MAX + 1);
}

/**
 * Save a post's picture into the blog's picture folder. Returns [path from the
 * site root, ''] — or ['', why] when it could not, so the post still goes in.
 *
 * "the picture could not be fetched" stopped whole posts: this server was
 * asked to download the picture itself, and some hosts do not let a website
 * reach out to the internet. The agency now sends the picture with the post
 * ('data'), and a picture that still cannot be saved leaves the cover empty
 * with the reason — it never stops the post.
 */
function save_picture(array $img): array {
    $dir = clean_rel((string)($img['dir'] ?? ''));
    $dir = rtrim($dir, '/');
    if (strpos($dir, '..') !== false || strpos($dir, "\0") !== false) { return ['', 'bad picture folder']; }
    foreach (explode('/', strtolower($dir)) as $part) {
        if ($part !== '' && in_array($part, IMAGE_NEVER_DIRS, true)) {
            return ['', "the agency never saves anything into $dir"];
        }
    }
    // His blog's pictures may live under admin/, which is never written to.
    // Then a post's picture goes in a folder of its own, made once.
    if ($dir === 'agency-pictures' && !is_dir(SITE_ROOT . '/agency-pictures')) {
        @mkdir(SITE_ROOT . '/agency-pictures', 0755);
    }
    $real = realpath(SITE_ROOT . ($dir === '' ? '' : '/' . $dir));
    if ($real === false || strpos($real, SITE_ROOT) !== 0 || !is_dir($real)) {
        return ['', "the picture folder $dir is not there"];
    }
    $data = '';
    $why = '';
    if (!empty($img['data'])) {
        $data = (string)base64_decode((string)$img['data'], true);
        if ($data === '') { $why = 'the picture sent with the post was damaged'; }
    }
    $from = trim((string)($img['from'] ?? ''));
    if ($data === '' && $from !== '') {
        if (preg_match('#^https?://#i', $from)) {
            $data = fetch_picture($from);
            if ($data === '') { $why = 'this server could not download it from ' . (string)parse_url($from, PHP_URL_HOST); }
        } else {
            $rel = clean_rel($from);
            $src = realpath(SITE_ROOT . '/' . rawurldecode($rel));
            $data = ($src !== false && strpos($src, SITE_ROOT) === 0 && strpos($rel, '..') === false
                     && preg_match('/\.(jpe?g|png|webp|gif)$/i', $src) && is_file($src))
                    ? (string)file_get_contents($src) : '';
            if ($data === '') { $why = "no picture at $rel on your site"; }
        }
    }
    if ($data === '') { return ['', $why ?: 'no picture was given']; }
    if (strlen($data) > IMAGE_MAX) { return ['', 'the picture is larger than 8 MB']; }
    $info = @getimagesizefromstring($data);
    $ext = ['image/jpeg' => 'jpg', 'image/png' => 'png', 'image/webp' => 'webp', 'image/gif' => 'gif']
           [$info['mime'] ?? ''] ?? '';
    if ($ext === '') { return ['', 'what came back was not a picture']; }
    $base = trim(preg_replace('/[^a-z0-9]+/', '-', strtolower((string)($img['name'] ?? 'post'))), '-');
    $file = substr($base ?: 'post', 0, 60) . '-' . bin2hex(random_bytes(3)) . '.' . $ext;
    if (@file_put_contents($real . '/' . $file, $data) === false) {
        return ['', "could not save the picture into $dir — check that folder's permissions"];
    }
    return [($dir === '' ? '' : $dir . '/') . $file, ''];
}

// --- a picture for inside a post ----------------------------------------------
// POST ?do=picture {data, name}: one picture added in the post editor, saved
// into agency-pictures/ beside your pages (made once) and nowhere else.
if ($route === '/picture' && $method === 'POST') {
    [$path, $why] = save_picture(['dir' => 'agency-pictures', 'data' => (string)($body['data'] ?? ''),
                                  'name' => (string)($body['name'] ?? 'picture')]);
    if ($path === '') { fail(422, $why ?: 'the picture could not be saved'); }
    reply(['ok' => true, 'path' => $path]);
}

if (in_array($route, ['/blog', '/blog-remove'], true)) {
    $db = blog_db();
    try {
        [$table, $cols] = blog_table($db);
        $pk = pk_of($cols);
        $slugc = slug_col($cols);
        $names = array_column($cols, 'name');

        if ($route === '/blog' && $method === 'GET') {
            $slug = trim((string)($_GET['page'] ?? ''));
            if ($slug !== '') {
                $hit = $db->rows('SELECT ' . ($pk ? q_name($db, $pk) : '1') . ' AS id FROM ' . q_name($db, $table)
                                 . ' WHERE ' . q_name($db, $slugc) . ' = ? LIMIT 1', [$slug]);
                $ours = array_values(array_filter(blog_record(), fn($r) => ($r['slug'] ?? '') === $slug
                                                                          && empty($r['removed'])));
                reply(['ok' => true, 'table' => $table, 'slug' => $slug, 'found' => (bool)$hit,
                       'id' => $hit ? (string)$hit[0]['id'] : '', 'ours' => (bool)$ours]);
            }
            $order = $pk ? ' ORDER BY ' . q_name($db, $pk) . ' DESC' : '';
            $latest = $db->rows('SELECT * FROM ' . q_name($db, $table) . $order . ' LIMIT 1');
            $shown = [];
            foreach (($latest[0] ?? []) as $k => $v) {
                $shown[$k] = preg_match(SECRET_COLUMN, (string)$k) ? '(hidden)'
                           : (is_string($v) ? (function_exists('mb_substr') ? mb_substr($v, 0, 300)
                                                                          : substr($v, 0, 300)) : $v);
            }
            $n = $db->rows('SELECT COUNT(*) AS n FROM ' . q_name($db, $table));
            // WHERE EACH POST IS FILED. A new post copied the category of the
            // latest one, so a post on aluminium cladding would have gone under
            // "Wooden Work". The categories your blog has, and how recent posts
            // are filed, so the agency can pick the right one.
            $catc = '';
            foreach ($cols as $c) {
                if (preg_match('/^(cat|cat_id|category|category_id|categories|blog_category|category_name)$/i', $c['name'])) {
                    $catc = $c['name'];
                    break;
                }
            }
            $cats = [];
            if ($catc !== '') {
                foreach (all_tables($db) as $t) {
                    if ($t === $table || !preg_match('/categor/i', $t)) { continue; }
                    $tc = array_column(columns_of($db, $t), 'name');
                    $idc = in_array('id', $tc, true) ? 'id' : (pk_of(columns_of($db, $t)) ?: '');
                    $namec = '';
                    foreach (['name', 'title', 'category_name', 'category', 'cat_name'] as $w) {
                        if (in_array($w, $tc, true)) { $namec = $w; break; }
                    }
                    if ($idc && $namec) {
                        foreach ($db->rows('SELECT ' . q_name($db, $idc) . ' AS id, ' . q_name($db, $namec)
                                           . ' AS name FROM ' . q_name($db, $t) . ' LIMIT 100') as $r) {
                            $cats[] = ['id' => (string)$r['id'], 'name' => (string)$r['name']];
                        }
                        break;
                    }
                }
                if (!$cats) {
                    foreach ($db->rows('SELECT DISTINCT ' . q_name($db, $catc) . ' AS v FROM ' . q_name($db, $table)
                                       . ' LIMIT 100') as $r) {
                        if ((string)$r['v'] !== '') { $cats[] = ['id' => (string)$r['v'], 'name' => (string)$r['v']]; }
                    }
                }
            }
            $titlec = '';
            foreach ($cols as $c) {
                if (preg_match('/^(title|post_title|blog_title|heading|name)$/i', $c['name'])) { $titlec = $c['name']; break; }
            }
            $recent = [];
            if ($titlec !== '') {
                $pick = array_filter([$pk, $titlec, $slugc, $catc]);
                foreach ($db->rows('SELECT ' . implode(', ', array_map(fn($c) => q_name($db, $c), array_unique($pick)))
                                   . ' FROM ' . q_name($db, $table) . $order . ' LIMIT 30') as $r) {
                    $recent[] = array_map(fn($v) => is_string($v) ? substr($v, 0, 160) : $v, $r);
                }
            }
            // WHERE HIS POSTS' PICTURES ACTUALLY ARE. The latest post's picture
            // file is looked for in the usual places — one named file, checked
            // for, never a folder listed — so a new post's picture goes where
            // his blog looks for it.
            $imgc = '';
            foreach ($cols as $c) {
                if (preg_match('/image|img|photo|picture|thumb|banner|cover/i', $c['name'])
                    && !preg_match('/alt|caption|title|count/i', $c['name'])) { $imgc = $c['name']; break; }
            }
            $home = null;
            $v = $imgc !== '' ? (string)($latest[0][$imgc] ?? '') : '';
            if ($v !== '' && !preg_match('#^https?://#i', $v) && strpos($v, '..') === false) {
                $disp = blog_display();
                $cands = array_unique(array_filter([clean_rel((string)($disp['prefix'][$imgc] ?? ''))], 'strlen'));
                $cands = array_merge($cands, ['', 'uploads/', 'uploads/blog/', 'uploads/blogs/', 'uploads/images/',
                    'upload/', 'upload/blog/', 'images/', 'images/blog/', 'images/blogs/', 'img/', 'img/blog/',
                    'assets/uploads/', 'assets/images/', 'assets/images/blog/', 'assets/img/blog/',
                    'admin/uploads/', 'admin/uploads/blog/', 'admin/uploads/blogs/', 'admin/upload/',
                    'admin/images/', 'admin/assets/uploads/', 'admin/assets/images/']);
                foreach ($cands as $cand) {
                    $cand = $cand === '' ? '' : rtrim($cand, '/') . '/';
                    if (strpos($cand, '..') === false && is_file(SITE_ROOT . '/' . $cand . ltrim($v, '/'))) {
                        $home = ['column' => $imgc, 'value' => $v, 'prefix' => $cand,
                                 'folder' => rtrim(dirname($cand . ltrim($v, '/')), '/.')];
                        break;
                    }
                }
            }
            reply(['ok' => true, 'connector' => CONNECTOR, 'image_home' => $home,
                   'table' => $table, 'columns' => $cols, 'key' => $pk, 'slug_column' => $slugc,
                   'category_column' => $catc, 'categories' => $cats, 'title_column' => $titlec,
                   'recent' => $recent,
                   'latest' => $shown, 'count' => (int)($n[0]['n'] ?? 0), 'via' => $db->via,
                   'driver' => $db->driver(), 'display' => blog_display(),
                   'never_dirs' => IMAGE_NEVER_DIRS,
                   'ours' => array_values(array_filter(blog_record(), fn($r) => empty($r['removed'])))]);
        }

        if ($route === '/blog' && $method === 'POST') {
            $post = is_array($body['post'] ?? null) ? $body['post'] : [];
            if (!$post) { fail(400, 'no post to add'); }
            $row = [];
            foreach ($post as $k => $v) {
                if (!in_array($k, $names, true)) { fail(400, "your posts table has no column called $k"); }
                foreach ($cols as $c) {
                    if ($c['name'] === $k && $c['auto']) { fail(400, "$k is filled in by your database"); }
                }
                if (!is_scalar($v) && $v !== null) { fail(400, "$k must be plain text or a number"); }
                if (is_string($v) && strlen($v) > 1024 * 1024) { fail(400, "$k is too long"); }
                if ($v === '__NOW__') {
                    $type = '';
                    foreach ($cols as $c) { if ($c['name'] === $k) { $type = $c['type']; } }
                    $v = preg_match('/^(int|bigint)/', $type) ? (string)time()
                       : (preg_match('/^date$/', $type) ? date('Y-m-d') : date('Y-m-d H:i:s'));
                }
                $row[$k] = $v;
            }
            $slug = (string)($row[$slugc] ?? '');
            if ($slugc === '' || $slug === '') { fail(400, 'a post needs its slug'); }
            if ($db->rows('SELECT 1 AS x FROM ' . q_name($db, $table) . ' WHERE ' . q_name($db, $slugc)
                          . ' = ? LIMIT 1', [$slug])) {
                reply(['ok' => false, 'taken' => true, 'slug' => $slug,
                       'error' => "your blog already has a post at $slug"], 409);
            }
            $picture = '';
            $img = is_array($body['image'] ?? null) ? $body['image'] : null;
            $picture_error = '';
            if ($img && in_array((string)($img['column'] ?? ''), $names, true)) {
                [$picture, $picture_error] = save_picture($img);
            }
            if ($img && $picture === '' && in_array((string)($img['column'] ?? ''), $names, true)) {
                // No cover — the post still goes in, and says why.
                $row[(string)$img['column']] = $row[(string)$img['column']] ?? '';
            } elseif ($img && in_array((string)($img['column'] ?? ''), $names, true)) {
                $drop = clean_rel((string)($img['drop'] ?? ''));
                $value = ($drop !== '' && str_starts_with($picture, $drop)) ? substr($picture, strlen($drop)) : $picture;
                if (!empty($img['url_base'])) { $value = rtrim((string)$img['url_base'], '/') . '/' . $picture; }
                // Climbing out of the folder his page puts in front of the name,
                // so admin/uploads/ + ../../agency-pictures/x.jpg finds the file.
                $climb = (string)($img['climb'] ?? '');
                if ($climb !== '' && preg_match('#^(\.\./){1,6}$#', $climb)) { $value = $climb . $value; }
                $row[(string)$img['column']] = $value;
            }
            $keys = array_keys($row);
            try {
                $id = $db->run('INSERT INTO ' . q_name($db, $table) . ' ('
                               . implode(', ', array_map(fn($k) => q_name($db, $k), $keys)) . ') VALUES ('
                               . implode(', ', array_fill(0, count($keys), '?')) . ')', array_values($row));
            } catch (Throwable $e) {
                if ($picture !== '') { @unlink(SITE_ROOT . '/' . $picture); }
                fail(422, 'your database would not take the post: ' . db_problem($e));
            }
            if ($pk && ($id === '' || $id === '0')) {
                $hit = $db->rows('SELECT ' . q_name($db, $pk) . ' AS id FROM ' . q_name($db, $table)
                                 . ' WHERE ' . q_name($db, $slugc) . ' = ? LIMIT 1', [$slug]);
                $id = $hit ? (string)$hit[0]['id'] : '';
            }
            $rec = blog_record();
            $rec[] = ['id' => $id, 'key' => $pk, 'table' => $table, 'slug' => $slug,
                      'picture' => $picture, 'at' => date('c')];
            save_record($rec);
            reply(['ok' => true, 'table' => $table, 'id' => $id, 'slug' => $slug, 'picture' => $picture, 'picture_error' => $picture_error,
                   'display' => blog_display()]);
        }

        if ($route === '/blog-remove' && $method === 'POST') {
            $id = trim((string)($body['id'] ?? ''));
            $rec = blog_record();
            foreach ($rec as $i => $r) {
                if ((string)($r['id'] ?? '') !== $id || $id === '' || !empty($r['removed'])) { continue; }
                if (($r['table'] ?? '') !== $table || ($r['key'] ?? '') !== $pk || $pk === '') {
                    fail(409, 'your posts table has changed since that post was added, so it was left alone');
                }
                $db->run('DELETE FROM ' . q_name($db, $table) . ' WHERE ' . q_name($db, $pk) . ' = ?', [$id]);
                $pic = (string)($r['picture'] ?? '');
                if ($pic !== '' && strpos($pic, '..') === false && is_file(SITE_ROOT . '/' . $pic)) {
                    @unlink(SITE_ROOT . '/' . $pic);
                }
                $rec[$i]['removed'] = date('c');
                save_record($rec);
                reply(['ok' => true, 'removed' => $id, 'slug' => $r['slug'] ?? '']);
            }
            fail(403, 'only a post the agency added can be taken away, and that is not one of them');
        }
    } catch (Throwable $e) {
        fail(500, 'your database said: ' . db_problem($e));
    }
    fail(405, 'not a way this door opens');
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
