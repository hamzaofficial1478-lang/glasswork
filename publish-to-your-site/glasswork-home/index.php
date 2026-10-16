<?php
/**
 * Botix Home — a sealed postbox, the program's updates, and a download page.
 *
 *   https://yourprogram.com/glasswork-home/        the download page for new people
 *   ...?do=latest      (anyone)  is there a new version (nothing about the asker is recorded)
 *   ...?do=drop        (anyone)  a copy posts a SEALED problem report
 *   ...?do=mail        (anyone)  a copy fetches the sealed answers to its own reports
 *   ...?do=mail-done   (anyone)  ...and has them removed once read
 *   ...?do=collect / ack         (owner)  the owner's program takes the reports home
 *   ...?do=reply                 (owner)  the owner's program posts a sealed answer
 *   ...?do=upload / release      (owner)  putting a new version out
 *   ...?do=licence-* / stripe / welcome      plans, keys and payments (licences.php)
 *
 * WHAT IT KEEPS — AND WHAT IT CANNOT READ
 *
 *   A report is sealed on the person's computer with the owner's public key
 *   before it leaves. This folder holds the envelope only until the owner's
 *   program collects it, and then deletes it; it cannot open it. Answers go
 *   back sealed to a mailbox made for that one report — a random number that
 *   says nothing about who sent it — and are deleted once read. Nothing here
 *   counts copies or records who asks for updates. Someone who got into the
 *   hosting would find sealed envelopes and nothing else.
 *
 *   Envelopes live in private/ as files that stop at their first line (and the
 *   folder refuses browsing). Updates live in updates/, which anyone may
 *   download — they are signed, so a changed file is refused by the program.
 *   home-key.php holds a fingerprint of the owner's key, never the key.
 */

declare(strict_types=1);
@ini_set('display_errors', '0');
ob_start();

const HOME_VERSION = 4;               // 4: plans, keys and payments (licences.php)
const ENVELOPE_MAX = 600 * 1024;          // one sealed report, with its support file
const CHUNK_MAX = 6 * 1024 * 1024;        // one piece of an upload
const PER_ADDRESS_HOUR = 10;              // reports from one address in an hour
const PER_DAY = 1500;                     // reports from everyone in a day
const POSTBOX_FILES = 5000;               // waiting to be collected, at most
const POSTBOX_BYTES = 200 * 1024 * 1024;
const LETTERS_PER_BOX = 50;
const LETTER_DAYS = 90;                   // an answer never fetched is thrown away after this
const BOXES_PER_ASK = 50;

define('PRIVATE_DIR', __DIR__ . '/private');
define('UPDATES_DIR', __DIR__ . '/updates');

function answer(array $data, int $code = 200): void {
    while (ob_get_level() > 0) { ob_end_clean(); }
    http_response_code($code);
    header('Content-Type: application/json');
    header('Cache-Control: no-store');
    header('X-Robots-Tag: noindex, nofollow');
    echo json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    exit;
}

function refuse(int $code, string $why): void { answer(['ok' => false, 'error' => $why], $code); }

function config(): array {
    $c = is_file(__DIR__ . '/home-config.php') ? @include __DIR__ . '/home-config.php' : [];
    return is_array($c) ? $c : [];
}

function is_owner(): bool {
    $want = is_file(__DIR__ . '/home-key.php') ? @include __DIR__ . '/home-key.php' : '';
    $want = is_string($want) ? trim($want) : '';
    if (strncmp($want, 'sha256:', 7) !== 0 || strlen($want) < 30) { return false; }
    $hdr = $_SERVER['HTTP_AUTHORIZATION'] ?? ($_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '');
    $got = preg_match('/Bearer\s+(\S+)/i', $hdr, $m) ? $m[1] : (string)($_SERVER['HTTP_X_HOME_KEY'] ?? '');
    return $got !== '' && hash_equals(substr($want, 7), hash('sha256', $got));
}

function owner_only(): void { if (!is_owner()) { refuse(401, 'only the owner of this program can do that'); } }

/** A folder nobody can browse, made once. */
function private_dir(string $sub = ''): string {
    $dir = PRIVATE_DIR . ($sub === '' ? '' : '/' . $sub);
    if (!is_dir($dir)) { @mkdir($dir, 0755, true); }
    foreach ([PRIVATE_DIR, $dir] as $d) {
        if (!is_file($d . '/.htaccess')) { @file_put_contents($d . '/.htaccess', "Require all denied\nDeny from all\n"); }
        if (!is_file($d . '/index.html')) { @file_put_contents($d . '/index.html', ''); }
    }
    return $dir;
}

/** Kept as a PHP file that stops at its first line, so it cannot be read by address. */
function keep(string $path, array $data): bool {
    $tmp = $path . '.' . bin2hex(random_bytes(3)) . '.tmp';
    if (@file_put_contents($tmp, "<?php exit; ?>\n" . json_encode($data, JSON_UNESCAPED_SLASHES), LOCK_EX) === false) { return false; }
    if (@rename($tmp, $path)) { return true; }
    @unlink($tmp);
    return false;
}

function kept(string $path): ?array {
    $raw = is_file($path) ? (string)@file_get_contents($path) : '';
    $nl = strpos($raw, "\n");
    $d = $nl === false ? null : json_decode(substr($raw, $nl + 1), true);
    return is_array($d) ? $d : null;
}

function envelope($v): string {
    $v = trim((string)$v);
    if (strlen($v) > ENVELOPE_MAX) { refuse(413, 'that is too large to send — leave out the support report and try again'); }
    if (!preg_match('/^gw1\.[A-Za-z0-9_-]{60,}$/', $v)) { refuse(400, 'only sealed envelopes are accepted here'); }
    return $v;
}

function box_id($v): string { $v = strtolower(trim((string)$v)); return preg_match('/^[a-f0-9]{32}$/', $v) ? $v : ''; }
function item_id($v): string { $v = strtolower(trim((string)$v)); return preg_match('/^[0-9]{10}-[a-f0-9]{12}$/', $v) ? $v : ''; }
function new_id(): string { return sprintf('%010d', time()) . '-' . bin2hex(random_bytes(6)); }

function here(): string {
    $https = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') || (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https');
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

/** Not more than a few an hour from one address, and a ceiling for the day. Kept as a one-way fingerprint for a day. */
function rate_ok(): bool {
    $path = private_dir() . '/rate.php';
    $r = kept($path) ?? [];
    $now = time();
    $who = hash('sha256', (string)($_SERVER['REMOTE_ADDR'] ?? '') . __DIR__);
    foreach ($r as $k => $times) {
        $r[$k] = array_values(array_filter(is_array($times) ? $times : [], fn($t) => is_int($t) && $t > $now - 86400));
        if (!$r[$k]) { unset($r[$k]); }
    }
    if (count(array_filter($r[$who] ?? [], fn($t) => $t > $now - 3600)) >= PER_ADDRESS_HOUR
        || array_sum(array_map('count', $r)) >= PER_DAY) { return false; }
    $r[$who][] = $now;
    keep($path, $r);
    return true;
}

function box_dir(string $box): string { return PRIVATE_DIR . '/mail/' . $box; }

$method = strtoupper($_SERVER['REQUEST_METHOD'] ?? 'GET');
$do = preg_replace('/[^a-z-]/', '', strtolower((string)($_GET['do'] ?? '')));
$raw = (string)file_get_contents('php://input');
$body = $raw === '' ? [] : json_decode($raw, true);
if (!is_array($body)) { refuse(400, 'that arrived incomplete'); }

// --- plans, keys and payments: their own file ------------------------------------
if ($do === 'stripe' || $do === 'welcome' || strncmp($do, 'licence-', 8) === 0) {
    require __DIR__ . '/licences.php';
}

// --- the download page -------------------------------------------------------
if ($do === '' && $method === 'GET') {
    while (ob_get_level() > 0) { ob_end_clean(); }
    $name = htmlspecialchars((string)(config()['product'] ?? 'Botix'), ENT_QUOTES);
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
                 . "<p class=\"m\">Your work stays on your own computer, encrypted with your password.</p>"
               : "<p>The first version is on its way. Please check back soon.</p>")
       . "</main>";
    exit;
}

if ($do === 'ping' && $method === 'GET') {
    $out = ['ok' => true, 'home' => HOME_VERSION, 'latest' => manifest()['version'] ?? '', 'owner' => is_owner()];
    if ($out['owner']) { $out['waiting'] = count(waiting()); }
    answer($out);
}

// --- is there a new version (the file addresses are made whole here) -------------
if ($do === 'latest' && $method === 'GET') {
    $m = manifest();
    if (!$m) { answer(['ok' => true, 'version' => '', 'notes' => 'nothing published yet']); }
    foreach (['zip', 'installer'] as $k) {
        if (!empty($m[$k]) && !preg_match('#^https://#i', (string)$m[$k])) { $m[$k] = here() . '/updates/' . basename((string)$m[$k]); }
    }
    answer($m + ['ok' => true]);
}

// --- a copy posts a sealed report --------------------------------------------------
if ($do === 'drop' && $method === 'POST') {
    $sealed = envelope($body['sealed'] ?? '');
    $files = waiting();
    $bytes = array_sum(array_map(fn($f) => (int)@filesize($f), $files));
    if (count($files) >= POSTBOX_FILES || $bytes >= POSTBOX_BYTES) { refuse(507, 'the postbox is full until its owner collects — please try again tomorrow'); }
    if (!rate_ok()) { refuse(429, 'a lot has come from here just now — please try again in an hour'); }
    $id = new_id();
    if (!keep(private_dir('postbox') . '/' . $id . '.php', ['id' => $id, 'at' => time(), 'sealed' => $sealed])) {
        refuse(500, 'it could not be saved on the website');
    }
    $c = config();
    // One email when the postbox stops being empty, not one per report: a burst cannot flood his inbox.
    if (!$files && !empty($c['owner_email']) && filter_var($c['owner_email'], FILTER_VALIDATE_EMAIL)) {
        @mail((string)$c['owner_email'], '[' . ($c['product'] ?? 'Botix') . '] A problem report is waiting',
              "Someone using your program sent a problem report.\n\nIt is sealed — only your program can open it. Open your program and go to Reports.\n",
              'From: no-reply@' . preg_replace('/[^a-z0-9.-]/i', '', (string)($_SERVER['HTTP_HOST'] ?? 'localhost')));
    }
    answer(['ok' => true, 'id' => $id]);
}

// --- a copy fetches the sealed answers to its reports, then has them removed ----------
if ($do === 'mail' && $method === 'POST') {
    $out = [];
    foreach (array_slice((array)($body['boxes'] ?? []), 0, BOXES_PER_ASK) as $box) {
        if (($box = box_id($box)) === '') { continue; }
        foreach ((array)glob(box_dir($box) . '/*.php') as $f) {
            $l = kept($f);
            if ($l && time() - (int)($l['at'] ?? 0) < LETTER_DAYS * 86400) { $out[$box][] = ['id' => $l['id'], 'sealed' => $l['sealed']]; }
            else { @unlink($f); }
        }
    }
    answer(['ok' => true, 'letters' => (object)$out]);
}

if ($do === 'mail-done' && $method === 'POST') {
    $gone = 0;
    foreach (array_slice((array)($body['done'] ?? []), 0, BOXES_PER_ASK, true) as $box => $ids) {
        if (($box = box_id($box)) === '') { continue; }
        foreach (array_slice((array)$ids, 0, LETTERS_PER_BOX) as $id) {
            $f = box_dir($box) . '/' . item_id($id) . '.php';
            if (item_id($id) !== '' && is_file($f) && @unlink($f)) { $gone++; }
        }
        if (is_dir(box_dir($box)) && !glob(box_dir($box) . '/*.php')) {
            @unlink(box_dir($box) . '/.htaccess'); @unlink(box_dir($box) . '/index.html'); @rmdir(box_dir($box));
        }
    }
    answer(['ok' => true, 'removed' => $gone]);
}

// --- the owner: take the reports home, then let them go -----------------------------
if ($do === 'collect' && $method === 'GET') {
    owner_only();
    $files = waiting();
    $out = [];
    foreach (array_slice($files, 0, 200) as $f) {
        $e = kept($f);
        if ($e) { $out[] = ['id' => $e['id'], 'at' => $e['at'], 'sealed' => $e['sealed']]; } else { @unlink($f); }
    }
    answer(['ok' => true, 'envelopes' => $out, 'waiting' => count($files), 'more' => count($files) > 200]);
}

if ($do === 'ack' && $method === 'POST') {
    owner_only();
    $gone = 0;
    foreach (array_slice((array)($body['ids'] ?? []), 0, 500) as $id) {
        $f = PRIVATE_DIR . '/postbox/' . item_id($id) . '.php';
        if (item_id($id) !== '' && is_file($f) && @unlink($f)) { $gone++; }
    }
    answer(['ok' => true, 'removed' => $gone, 'waiting' => count(waiting())]);
}

// --- the owner: a sealed answer, to one report's mailbox -------------------------------
if ($do === 'reply' && $method === 'POST') {
    owner_only();
    if (($box = box_id($body['box'] ?? '')) === '') { refuse(400, 'which mailbox?'); }
    $sealed = envelope($body['sealed'] ?? '');
    $dir = private_dir('mail/' . $box);
    $letters = (array)glob($dir . '/*.php');
    sort($letters);
    while (count($letters) >= LETTERS_PER_BOX) { @unlink((string)array_shift($letters)); }
    $id = new_id();
    if (!keep($dir . '/' . $id . '.php', ['id' => $id, 'at' => time(), 'sealed' => $sealed])) { refuse(500, 'the answer could not be saved on the website'); }
    answer(['ok' => true, 'id' => $id]);
}

// --- the owner: a new version, in pieces, checked whole at the end ---------------------
if ($do === 'upload' && $method === 'POST') {
    owner_only();
    $name = (string)($body['name'] ?? '');
    if (!preg_match('/^[a-z0-9][a-z0-9._-]{0,90}\.(zip|exe)$/i', $name)) { refuse(400, 'not a file name an update uses'); }
    $part = (int)($body['part'] ?? 0);
    $parts = max(1, (int)($body['parts'] ?? 1));
    $data = base64_decode((string)($body['data'] ?? ''), true);
    if ($data === false || strlen($data) > CHUNK_MAX) { refuse(400, 'that piece arrived damaged'); }
    $tmp = private_dir('parts') . '/' . $name . '.part';
    if (@file_put_contents($tmp, $data, $part === 0 ? LOCK_EX : (FILE_APPEND | LOCK_EX)) === false) { refuse(500, 'could not save that piece on the website'); }
    if ($part < $parts - 1) { answer(['ok' => true, 'part' => $part, 'of' => $parts]); }
    $want = strtolower((string)($body['sha256'] ?? ''));
    if ($want !== '' && hash_file('sha256', $tmp) !== $want) { @unlink($tmp); refuse(422, 'the file did not arrive whole — send it again'); }
    if (!is_dir(UPDATES_DIR)) { @mkdir(UPDATES_DIR, 0755, true); }
    if (!@rename($tmp, UPDATES_DIR . '/' . $name)) { refuse(500, 'could not put the file in updates/'); }
    answer(['ok' => true, 'name' => $name, 'bytes' => filesize(UPDATES_DIR . '/' . $name)]);
}

if ($do === 'release' && $method === 'POST') {
    owner_only();
    $m = is_array($body['manifest'] ?? null) ? $body['manifest'] : null;
    if (!$m || empty($m['version']) || empty($m['signature'])) { refuse(400, 'that is not a signed release'); }
    foreach (['zip', 'installer'] as $k) {
        if (!empty($m[$k]) && !is_file(UPDATES_DIR . '/' . basename((string)$m[$k]))) { refuse(409, basename((string)$m[$k]) . ' has not been uploaded yet'); }
    }
    if (!is_dir(UPDATES_DIR)) { @mkdir(UPDATES_DIR, 0755, true); }
    $tmp = UPDATES_DIR . '/manifest.json.' . bin2hex(random_bytes(3)) . '.tmp';
    file_put_contents($tmp, json_encode($m, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES), LOCK_EX);
    if (!@rename($tmp, UPDATES_DIR . '/manifest.json')) { @unlink($tmp); refuse(500, 'could not put the release out'); }
    answer(['ok' => true, 'version' => $m['version'], 'download_page' => here() . '/']);
}

refuse(404, 'not something this address does');
