<?php
/**
 * Glasswork Home — a sealed postbox and a download page, on your website.
 *
 *   https://yoursite.com/glasswork-home/            a download page for new users
 *   ...?do=latest     (anyone)  their program asks whether there is a new version
 *   ...?do=drop       (anyone)  their program posts a SEALED envelope for you
 *   ...?do=mail       (anyone)  their program fetches sealed answers from its own mailbox
 *   ...?do=collect / ack        (you only)  your program takes the envelopes home
 *   ...?do=reply                (you only)  your program posts a sealed answer back
 *   ...?do=upload / release     (you only)  putting a new version out
 *
 * WHAT IT KEEPS — AND WHAT IT CANNOT READ
 *
 *   "i want the Users to be stored on my pc in program storage not on my
 *    website hosting"
 *
 *   Nothing about the people using your program is kept here. A problem report,
 *   a registration, or "this copy is in use" is sealed on THEIR computer with
 *   your public key before it leaves. This folder only holds the envelope until
 *   your program collects it, and then deletes it. It cannot open one: the only
 *   key that can is on your computer, never on this website. The same goes for
 *   your answers on their way back — sealed to the person's own copy, kept here
 *   only until it fetches them, and addressed by a random mailbox number that
 *   says nothing about who they are.
 *
 *   So someone who got into your hosting would find sealed envelopes and
 *   nothing else — no names, no emails, no reports, no list of users.
 *
 *   Envelopes are kept in private/ as files no browser can read (each starts
 *   with a line of PHP that stops anyone who asks for it by address, and the
 *   folder refuses browsing). Updates are kept in updates/, which anyone may
 *   download — they are signed, so a changed file is refused by the program.
 *
 *   home-key.php holds a fingerprint of your key, not the key itself. Nobody
 *   without the key collects, answers or puts out a version.
 *
 *   Nothing here touches the rest of your website.
 */

declare(strict_types=1);

@ini_set('display_errors', '0');
ob_start();

const HOME_VERSION = 3;
const ENVELOPE_MAX = 600 * 1024;          // one sealed envelope (a report with its support file)
const CHUNK_MAX = 6 * 1024 * 1024;        // one piece of an upload
const PER_ADDRESS_HOUR = 10;              // envelopes from one address in an hour
const PER_DAY = 1500;                     // envelopes from everyone in a day
const POSTBOX_FILES = 5000;               // waiting to be collected, at most
const POSTBOX_BYTES = 200 * 1024 * 1024;
const LETTERS_PER_BOX = 50;               // answers waiting in one mailbox
const LETTER_DAYS = 90;                   // an answer never fetched is thrown away after this

define('PRIVATE_DIR', __DIR__ . '/private');
define('UPDATES_DIR', __DIR__ . '/updates');

function answer(array $data, int $code = 200): void {
    while (ob_get_level() > 0) { ob_end_clean(); }
    http_response_code($code);
    header('Content-Type: application/json');
    header('Cache-Control: no-store');
    header('X-Robots-Tag: noindex, nofollow');
    $flags = JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE
           | (defined('JSON_INVALID_UTF8_SUBSTITUTE') ? JSON_INVALID_UTF8_SUBSTITUTE : 0);
    echo json_encode($data, $flags);
    exit;
}

function refuse(int $code, string $why): void {
    answer(['ok' => false, 'error' => $why], $code);
}

function config(): array {
    $c = is_file(__DIR__ . '/home-config.php') ? @include __DIR__ . '/home-config.php' : [];
    return is_array($c) ? $c : [];
}

/** The fingerprint of your key ("sha256:..."), or — from an older Home — the key itself. */
function owner_key(): string {
    $k = is_file(__DIR__ . '/home-key.php') ? @include __DIR__ . '/home-key.php' : '';
    return is_string($k) ? trim($k) : '';
}

function is_owner(): bool {
    $want = owner_key();
    if (strlen($want) < 24) { return false; }
    $hdr = $_SERVER['HTTP_AUTHORIZATION'] ?? ($_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '');
    $got = preg_match('/Bearer\s+(\S+)/i', $hdr, $m) ? $m[1] : (string)($_SERVER['HTTP_X_HOME_KEY'] ?? '');
    if ($got === '') { return false; }
    if (strncmp($want, 'sha256:', 7) === 0) {
        return hash_equals(substr($want, 7), hash('sha256', $got));
    }
    return hash_equals($want, $got);
}

function owner_only(): void {
    if (!is_owner()) { refuse(401, 'only the owner of this program can do that'); }
}

/** A folder nobody can browse, made once. */
function private_dir(string $sub = ''): string {
    $dir = PRIVATE_DIR . ($sub === '' ? '' : '/' . $sub);
    if (!is_dir($dir)) {
        @mkdir($dir, 0755, true);
    }
    foreach ([PRIVATE_DIR, $dir] as $d) {
        if (!is_file($d . '/.htaccess')) { @file_put_contents($d . '/.htaccess', "Require all denied\nDeny from all\n"); }
        if (!is_file($d . '/index.html')) { @file_put_contents($d . '/index.html', ''); }
    }
    return $dir;
}

/** Kept as a PHP file that stops at its first line, so it cannot be read by address. */
function keep(string $path, array $data): bool {
    $tmp = $path . '.' . bin2hex(random_bytes(3)) . '.tmp';
    $body = "<?php exit; ?>\n" . json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    if (@file_put_contents($tmp, $body, LOCK_EX) === false) { return false; }
    if (!@rename($tmp, $path)) { @unlink($tmp); return @file_put_contents($path, $body, LOCK_EX) !== false; }
    return true;
}

function kept(string $path): ?array {
    $raw = is_file($path) ? (string)@file_get_contents($path) : '';
    $nl = strpos($raw, "\n");
    if ($raw === '' || $nl === false) { return null; }
    $d = json_decode(substr($raw, $nl + 1), true);
    return is_array($d) ? $d : null;
}

/** A sealed envelope: "gw1." and base64url, nothing else, not too large. */
function envelope($v): string {
    $v = trim((string)$v);
    if (strlen($v) > ENVELOPE_MAX) { refuse(413, 'that is too large to send — leave out the support report and try again'); }
    if (!preg_match('/^gw1\.[A-Za-z0-9_-]{60,}$/', $v)) { refuse(400, 'only sealed envelopes are accepted here'); }
    return $v;
}

function box_id($v): string {
    $v = strtolower(trim((string)$v));
    return preg_match('/^[a-f0-9]{32}$/', $v) ? $v : '';
}

function item_id($v): string {
    $v = strtolower(trim((string)$v));
    return preg_match('/^[0-9]{10}-[a-f0-9]{12}$/', $v) ? $v : '';
}

function new_id(): string {
    return sprintf('%010d', time()) . '-' . bin2hex(random_bytes(6));
}

function here(): string {
    $https = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off')
          || (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https');
    $dir = rtrim(str_replace('\\', '/', dirname($_SERVER['SCRIPT_NAME'] ?? '/')), '/');
    return ($https ? 'https' : 'http') . '://' . ($_SERVER['HTTP_HOST'] ?? 'localhost') . $dir;
}

function manifest(): ?array {
    $m = is_file(UPDATES_DIR . '/manifest.json') ? json_decode((string)file_get_contents(UPDATES_DIR . '/manifest.json'), true) : null;
    return is_array($m) ? $m : null;
}

function waiting(): array {
    $files = (array)glob(private_dir('postbox') . '/*.php');
    sort($files);
    return $files;
}

/** Not more than a few an hour from one address, and a ceiling for the day. */
function rate_ok(): bool {
    $rate = private_dir() . '/rate.php';
    $r = kept($rate) ?? [];
    $now = time();
    // Kept only as a one-way fingerprint, and only for a day.
    $who = hash('sha256', (string)($_SERVER['REMOTE_ADDR'] ?? '') . owner_key());
    foreach ($r as $k => $times) {
        $r[$k] = array_values(array_filter(is_array($times) ? $times : [], fn($t) => is_int($t) && $t > $now - 86400));
        if (!$r[$k]) { unset($r[$k]); }
    }
    $mine = count(array_filter($r[$who] ?? [], fn($t) => $t > $now - 3600));
    $all = array_sum(array_map('count', $r));
    if ($mine >= PER_ADDRESS_HOUR || $all >= PER_DAY) { return false; }
    $r[$who][] = $now;
    keep($rate, $r);
    return true;
}

$method = strtoupper($_SERVER['REQUEST_METHOD'] ?? 'GET');
$do = preg_replace('/[^a-z-]/', '', strtolower((string)($_GET['do'] ?? '')));
$raw = (string)file_get_contents('php://input');
$body = $raw === '' ? [] : json_decode($raw, true);
if (!is_array($body)) { refuse(400, 'that arrived incomplete'); }

// --- the download page -------------------------------------------------------
if ($do === '' && $method === 'GET') {
    while (ob_get_level() > 0) { ob_end_clean(); }
    $c = config();
    $name = htmlspecialchars((string)($c['product'] ?? 'Glasswork'), ENT_QUOTES);
    $m = manifest();
    $ver = $m ? htmlspecialchars((string)($m['version'] ?? ''), ENT_QUOTES) : '';
    $exe = $m && !empty($m['installer']) ? htmlspecialchars(here() . '/updates/' . basename((string)$m['installer']), ENT_QUOTES) : '';
    header('Content-Type: text/html; charset=utf-8');
    echo "<!doctype html><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
       . "<title>$name — download</title><style>body{font:16px/1.5 system-ui,sans-serif;margin:0;background:#f4f6f5;color:#16201c}"
       . "main{max-width:560px;margin:10vh auto;padding:28px;background:#fff;border:1px solid #d9e0dc;border-radius:14px}"
       . "a.b{display:inline-block;margin-top:14px;padding:12px 18px;border-radius:10px;background:#1f5f4f;color:#fff;text-decoration:none;font-weight:600}"
       . "p.m,ol.m{color:#5b6762;font-size:14px}</style><main><h1 style=\"margin:0 0 6px\">$name</h1>"
       . ($exe ? "<p>Version $ver for Windows. Download it, double-click it, and follow the steps — it installs everything it needs.</p>"
                 . "<a class=\"b\" href=\"$exe\">Download for Windows</a>"
                 . "<ol class=\"m\"><li>Open the file you downloaded.</li><li>If a blue box says <b>Windows protected your PC</b>, "
               . "click <b>More info</b>, then <b>Run anyway</b>. It says that for any new program from a small maker.</li>"
               . "<li>Follow the steps: choose where to install it and where to keep your work. That is all.</li></ol>"
               . "<p class=\"m\">Your work stays on your own computer. Nothing about you is kept on this website.</p>"
               : "<p>The first version is on its way. Please check back soon.</p>")
       . "</main>";
    exit;
}

if ($do === 'ping' && $method === 'GET') {
    $m = manifest();
    $out = ['ok' => true, 'home' => HOME_VERSION, 'postbox' => true, 'latest' => $m['version'] ?? '', 'owner' => is_owner()];
    if ($out['owner']) {
        $out['waiting'] = count(waiting());
        // An older Home kept reports and people here, readable. Say so, so they can be taken home and removed.
        $out['old_records'] = count((array)glob(PRIVATE_DIR . '/reports/*.php')) + count((array)glob(PRIVATE_DIR . '/copies/*.php'));
    }
    answer($out);
}

// --- is there a new version --------------------------------------------------
// Nothing about the copy asking is recorded. The addresses of the files are
// made whole here, so the program can fetch them wherever this folder lives.
if ($do === 'latest' && $method === 'GET') {
    $m = manifest();
    if (!$m) { answer(['ok' => true, 'version' => '', 'notes' => 'nothing published yet']); }
    foreach (['zip', 'installer'] as $k) {
        if (!empty($m[$k]) && !preg_match('#^https://#i', (string)$m[$k])) {
            $m[$k] = here() . '/updates/' . basename((string)$m[$k]);
        }
    }
    answer($m + ['ok' => true]);
}

// --- a copy posts a sealed envelope ------------------------------------------
// The only thing readable is whether it is a problem report, so a new one can
// be emailed to you as "a report is waiting" — what it says stays sealed.
if ($do === 'drop' && $method === 'POST') {
    $sealed = envelope($body['sealed'] ?? '');
    $note = in_array($body['note'] ?? '', ['report', 'other'], true) ? (string)$body['note'] : 'other';
    $files = waiting();
    $bytes = 0;
    foreach ($files as $f) { $bytes += (int)@filesize($f); }
    if (count($files) >= POSTBOX_FILES || $bytes >= POSTBOX_BYTES) {
        refuse(507, 'the postbox is full until its owner collects — please try again tomorrow');
    }
    if (!rate_ok()) { refuse(429, 'a lot has come from here just now — please try again in an hour'); }
    $id = new_id();
    if (!keep(private_dir('postbox') . '/' . $id . '.php', ['id' => $id, 'at' => time(), 'note' => $note, 'sealed' => $sealed])) {
        refuse(500, 'it could not be saved on the website');
    }
    $c = config();
    if ($note === 'report' && !empty($c['owner_email']) && filter_var($c['owner_email'], FILTER_VALIDATE_EMAIL)) {
        @mail((string)$c['owner_email'], '[' . ($c['product'] ?? 'Glasswork') . '] A problem report is waiting',
              "Someone using your program sent a problem report.\n\nIt is sealed — only your program can open it. "
              . "Open your program and go to Your users to read it.\n",
              'From: no-reply@' . preg_replace('/[^a-z0-9.-]/i', '', (string)($_SERVER['HTTP_HOST'] ?? 'localhost')));
    }
    answer(['ok' => true, 'id' => $id]);
}

// --- a copy fetches the sealed answers in its own mailbox -----------------------
// The mailbox number is random, made by the copy, and known only to it and to
// you (inside its sealed report). Fetched answers are removed with mail-done.
if ($do === 'mail' && $method === 'POST') {
    $box = box_id($body['box'] ?? '');
    if ($box === '') { refuse(400, 'which mailbox?'); }
    $dir = PRIVATE_DIR . '/mail/' . $box;
    $out = [];
    foreach ((array)glob($dir . '/*.php') as $f) {
        $l = kept($f);
        if ($l && time() - (int)($l['at'] ?? 0) < LETTER_DAYS * 86400) { $out[] = ['id' => $l['id'], 'sealed' => $l['sealed']]; }
        elseif ($l) { @unlink($f); }
    }
    answer(['ok' => true, 'letters' => array_slice($out, 0, LETTERS_PER_BOX)]);
}

if ($do === 'mail-done' && $method === 'POST') {
    $box = box_id($body['box'] ?? '');
    if ($box === '') { refuse(400, 'which mailbox?'); }
    $gone = 0;
    foreach (array_slice((array)($body['ids'] ?? []), 0, LETTERS_PER_BOX) as $id) {
        $id = item_id($id);
        $f = PRIVATE_DIR . '/mail/' . $box . '/' . $id . '.php';
        if ($id !== '' && is_file($f) && @unlink($f)) { $gone++; }
    }
    $dir = PRIVATE_DIR . '/mail/' . $box;
    if (is_dir($dir) && !glob($dir . '/*.php')) { @unlink($dir . '/.htaccess'); @unlink($dir . '/index.html'); @rmdir($dir); }
    answer(['ok' => true, 'removed' => $gone]);
}

// --- you: take the envelopes home ------------------------------------------------
if ($do === 'collect' && $method === 'GET') {
    owner_only();
    $files = waiting();
    $out = [];
    foreach (array_slice($files, 0, 200) as $f) {
        $e = kept($f);
        if ($e) { $out[] = ['id' => $e['id'], 'at' => $e['at'], 'note' => $e['note'] ?? 'other', 'sealed' => $e['sealed']]; }
        else { @unlink($f); }
    }
    answer(['ok' => true, 'envelopes' => $out, 'waiting' => count($files), 'more' => count($files) > 200]);
}

if ($do === 'ack' && $method === 'POST') {
    owner_only();
    $gone = 0;
    foreach (array_slice((array)($body['ids'] ?? []), 0, 500) as $id) {
        $id = item_id($id);
        $f = PRIVATE_DIR . '/postbox/' . $id . '.php';
        if ($id !== '' && is_file($f) && @unlink($f)) { $gone++; }
    }
    answer(['ok' => true, 'removed' => $gone, 'waiting' => count(waiting())]);
}

// An older Home's readable records, handed over once so they can be removed.
if ($do === 'old-records' && $method === 'GET') {
    owner_only();
    $out = [];
    foreach (['reports', 'copies'] as $kind) {
        foreach ((array)glob(PRIVATE_DIR . '/' . $kind . '/*.php') as $f) {
            $d = kept($f);
            if ($d) { $out[] = ['kind' => $kind, 'name' => basename($f), 'record' => $d]; }
        }
    }
    answer(['ok' => true, 'records' => $out]);
}

if ($do === 'old-records-done' && $method === 'POST') {
    owner_only();
    $gone = 0;
    foreach (['reports', 'copies'] as $kind) {
        foreach ((array)glob(PRIVATE_DIR . '/' . $kind . '/*.php') as $f) { if (@unlink($f)) { $gone++; } }
        foreach (['.htaccess', 'index.html'] as $x) { @unlink(PRIVATE_DIR . '/' . $kind . '/' . $x); }
        @rmdir(PRIVATE_DIR . '/' . $kind);
    }
    answer(['ok' => true, 'removed' => $gone]);
}

// --- you: a sealed answer, back to one copy's mailbox -------------------------------
if ($do === 'reply' && $method === 'POST') {
    owner_only();
    $box = box_id($body['box'] ?? '');
    if ($box === '') { refuse(400, 'which mailbox?'); }
    $sealed = envelope($body['sealed'] ?? '');
    $dir = private_dir('mail/' . $box);
    $letters = (array)glob($dir . '/*.php');
    sort($letters);
    while (count($letters) >= LETTERS_PER_BOX) { @unlink((string)array_shift($letters)); }
    $id = new_id();
    if (!keep($dir . '/' . $id . '.php', ['id' => $id, 'at' => time(), 'sealed' => $sealed])) {
        refuse(500, 'the answer could not be saved on the website');
    }
    answer(['ok' => true, 'id' => $id]);
}

// --- you: putting a new version out ----------------------------------------------
// A big file goes up in pieces (a host takes only so much in one request); the
// last piece checks the whole file's fingerprint before it is put out.
if ($do === 'upload' && $method === 'POST') {
    owner_only();
    $name = (string)($body['name'] ?? '');
    if (!preg_match('/^[a-z0-9][a-z0-9._-]{0,90}\.(zip|exe|json)$/i', $name) || $name === 'manifest.json') {
        refuse(400, 'not a file name an update uses');
    }
    $part = (int)($body['part'] ?? 0);
    $parts = max(1, (int)($body['parts'] ?? 1));
    $data = base64_decode((string)($body['data'] ?? ''), true);
    if ($data === false || strlen($data) > CHUNK_MAX) { refuse(400, 'that piece arrived damaged'); }
    $tmp = private_dir('parts') . '/' . $name . '.part';
    if (@file_put_contents($tmp, $data, $part === 0 ? LOCK_EX : (FILE_APPEND | LOCK_EX)) === false) {
        refuse(500, 'could not save that piece on the website');
    }
    if ($part < $parts - 1) { answer(['ok' => true, 'part' => $part, 'of' => $parts]); }
    $want = strtolower((string)($body['sha256'] ?? ''));
    if ($want !== '' && hash_file('sha256', $tmp) !== $want) {
        @unlink($tmp);
        refuse(422, 'the file did not arrive whole — send it again');
    }
    if (!is_dir(UPDATES_DIR)) { @mkdir(UPDATES_DIR, 0755, true); }
    if (!@rename($tmp, UPDATES_DIR . '/' . $name)) { refuse(500, 'could not put the file in updates/'); }
    answer(['ok' => true, 'name' => $name, 'bytes' => filesize(UPDATES_DIR . '/' . $name)]);
}

if ($do === 'release' && $method === 'POST') {
    owner_only();
    $m = is_array($body['manifest'] ?? null) ? $body['manifest'] : null;
    if (!$m || empty($m['version']) || empty($m['signature'])) { refuse(400, 'that is not a signed release'); }
    foreach (['zip', 'installer'] as $k) {
        if (!empty($m[$k]) && !is_file(UPDATES_DIR . '/' . basename((string)$m[$k]))) {
            refuse(409, basename((string)$m[$k]) . ' has not been uploaded yet');
        }
    }
    if (!is_dir(UPDATES_DIR)) { @mkdir(UPDATES_DIR, 0755, true); }
    $tmp = UPDATES_DIR . '/manifest.json.' . bin2hex(random_bytes(3)) . '.tmp';
    file_put_contents($tmp, json_encode($m, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES), LOCK_EX);
    if (!@rename($tmp, UPDATES_DIR . '/manifest.json')) { @unlink($tmp); refuse(500, 'could not put the release out'); }
    answer(['ok' => true, 'version' => $m['version'], 'download_page' => here() . '/']);
}

refuse(404, 'not something this address does');
