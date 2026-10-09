<?php
/**
 * Glasswork Home — your program's own address on your website.
 *
 *   https://yoursite.com/glasswork-home/            a download page for new users
 *   ...?do=report     (anyone)  a person using the program sends you a problem
 *   ...?do=status     (anyone)  they see your answer, with the key they were given
 *   ...?do=latest     (anyone)  their program asks whether there is a new version
 *   ...?do=inbox / read / answer / remove   (you only)  the reports, and your replies
 *   ...?do=upload / release                 (you only)  putting a new version out
 *
 * WHAT TO DO WITH IT
 *
 *   Your program makes this folder for you (Settings → Publisher → Set up your
 *   Glasswork Home): unzip it and upload the glasswork-home folder into your
 *   website's main folder, next to your home page. That is all.
 *
 * WHAT IT KEEPS, AND WHERE
 *
 *   Reports are kept in private/ as files no browser can read: each one starts
 *   with a line of PHP that stops anyone who asks for it by address, and the
 *   folder refuses browsing as well. Updates are kept in updates/, which anyone
 *   may download — they are signed, so a changed file is refused by the program.
 *
 *   Your key (home-key.php) is what makes you "you" here. Nobody without it can
 *   read a report, answer one, or put out a version. It is never sent to the
 *   people using your program.
 *
 *   Nothing here touches the rest of your website.
 */

declare(strict_types=1);

@ini_set('display_errors', '0');
ob_start();

const HOME_VERSION = 1;
const REPORT_MAX = 400 * 1024;            // one report, with its support file
const CHUNK_MAX = 6 * 1024 * 1024;        // one piece of an upload
const PER_ADDRESS_HOUR = 8;               // reports from one address in an hour
const PER_DAY = 300;                      // reports from everyone in a day

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

function owner_key(): string {
    $k = is_file(__DIR__ . '/home-key.php') ? @include __DIR__ . '/home-key.php' : '';
    return is_string($k) ? trim($k) : '';
}

function is_owner(): bool {
    $want = owner_key();
    if (strlen($want) < 24) { return false; }
    $hdr = $_SERVER['HTTP_AUTHORIZATION'] ?? ($_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '');
    $got = preg_match('/Bearer\s+(\S+)/i', $hdr, $m) ? $m[1] : (string)($_SERVER['HTTP_X_HOME_KEY'] ?? '');
    return $got !== '' && hash_equals($want, $got);
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
    $body = "<?php exit; ?>\n" . json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE
                                                   | (defined('JSON_INVALID_UTF8_SUBSTITUTE') ? JSON_INVALID_UTF8_SUBSTITUTE : 0));
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

function report_path(string $id): string {
    $id = preg_replace('/[^a-z0-9-]/', '', strtolower($id));
    if ($id === '' || strlen($id) > 40) { refuse(400, 'which report?'); }
    return private_dir('reports') . '/' . $id . '.php';
}

function text(array $b, string $k, int $max): string {
    $v = trim((string)($b[$k] ?? ''));
    return function_exists('mb_substr') ? mb_substr($v, 0, $max) : substr($v, 0, $max);
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
       . "p.m{color:#5b6762;font-size:14px}</style><main><h1 style=\"margin:0 0 6px\">$name</h1>"
       . ($exe ? "<p>Version $ver for Windows. Download it, double-click it, and follow the steps — it installs everything it needs.</p>"
                 . "<a class=\"b\" href=\"$exe\">Download for Windows</a>"
                 . "<p class=\"m\">Windows may say it does not recognise the publisher the first time. Choose <b>More info</b> then <b>Run anyway</b>.</p>"
               : "<p>The first version is on its way. Please check back soon.</p>")
       . "</main>";
    exit;
}

if ($do === 'ping' && $method === 'GET') {
    $m = manifest();
    answer(['ok' => true, 'home' => HOME_VERSION, 'reports' => true, 'latest' => $m['version'] ?? '',
            'owner' => is_owner()]);
}

// --- is there a new version --------------------------------------------------
// The addresses of the files are made whole here, so the program can fetch
// them wherever this folder lives.
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

// --- a person sends a problem --------------------------------------------------
if ($do === 'report' && $method === 'POST') {
    if (strlen($raw) > REPORT_MAX) { refuse(413, 'that report is too large — leave out the support file and try again'); }
    $title = text($body, 'title', 200);
    $words = text($body, 'text', 20000);
    if ($title === '' && $words === '') { refuse(400, 'say what went wrong'); }
    // Not more than a few an hour from one address, and a ceiling for the day,
    // so a fault that repeats — or someone being a nuisance — cannot fill it.
    $rate = private_dir() . '/rate.php';
    $r = kept($rate) ?? [];
    $now = time();
    $who = hash('sha256', (string)($_SERVER['REMOTE_ADDR'] ?? '') . 'glasswork');
    $r = array_filter($r, fn($v) => is_array($v), ARRAY_FILTER_USE_BOTH);
    foreach ($r as $k => $times) { $r[$k] = array_values(array_filter($times, fn($t) => $t > $now - 86400)); }
    $mine = count(array_filter($r[$who] ?? [], fn($t) => $t > $now - 3600));
    $all = array_sum(array_map('count', $r));
    if ($mine >= PER_ADDRESS_HOUR || $all >= PER_DAY) {
        refuse(429, 'a lot of reports have come from here just now — please try again in an hour');
    }
    $r[$who][] = $now;
    keep($rate, $r);
    $id = date('ymd') . '-' . bin2hex(random_bytes(3));
    $key = bin2hex(random_bytes(12));
    $rep = ['id' => $id, 'key' => hash('sha256', $key), 'at' => $now, 'title' => $title ?: 'A problem', 'text' => $words,
            'contact' => text($body, 'contact', 200), 'product' => text($body, 'product', 60),
            'version' => text($body, 'version', 40), 'system' => text($body, 'system', 160),
            'support' => text($body, 'support', 300000), 'status' => 'new', 'reply' => '', 'fixed_in' => '',
            'updated' => $now];
    if (!keep(report_path($id), $rep)) { refuse(500, 'the report could not be saved on the website'); }
    $c = config();
    if (!empty($c['owner_email']) && filter_var($c['owner_email'], FILTER_VALIDATE_EMAIL)) {
        @mail((string)$c['owner_email'], '[' . ($c['product'] ?? 'Glasswork') . '] New report: ' . substr($rep['title'], 0, 80),
              "A new report came in ($id), from version {$rep['version']}.\n\n{$rep['title']}\n\n"
              . substr($words, 0, 1500) . "\n\nRead it and answer it in your program, under Reports.\n",
              'From: no-reply@' . preg_replace('/[^a-z0-9.-]/i', '', (string)($_SERVER['HTTP_HOST'] ?? 'localhost')));
    }
    answer(['ok' => true, 'id' => $id, 'key' => $key]);
}

// --- they see your answer ------------------------------------------------------
if ($do === 'status' && $method === 'POST') {
    $out = [];
    foreach (array_slice((array)($body['reports'] ?? []), 0, 50) as $q) {
        if (!is_array($q)) { continue; }
        $id = preg_replace('/[^a-z0-9-]/', '', strtolower((string)($q['id'] ?? '')));
        $rep = $id !== '' ? kept(private_dir('reports') . '/' . $id . '.php') : null;
        if (!$rep || !hash_equals((string)$rep['key'], hash('sha256', (string)($q['key'] ?? '')))) {
            $out[] = ['id' => $id, 'found' => false];
            continue;
        }
        $out[] = ['id' => $id, 'found' => true, 'status' => $rep['status'], 'reply' => $rep['reply'],
                  'fixed_in' => $rep['fixed_in'], 'updated' => $rep['updated']];
    }
    answer(['ok' => true, 'reports' => $out]);
}

// --- you: the reports ----------------------------------------------------------
if ($do === 'inbox' && $method === 'GET') {
    owner_only();
    $list = [];
    foreach ((array)glob(private_dir('reports') . '/*.php') as $f) {
        $rep = kept($f);
        if (!$rep) { continue; }
        $list[] = ['id' => $rep['id'], 'at' => $rep['at'], 'title' => $rep['title'],
                   'text' => function_exists('mb_substr') ? mb_substr((string)$rep['text'], 0, 240) : substr((string)$rep['text'], 0, 240),
                   'contact' => $rep['contact'], 'version' => $rep['version'], 'status' => $rep['status'],
                   'fixed_in' => $rep['fixed_in'], 'replied' => $rep['reply'] !== '', 'has_support' => $rep['support'] !== ''];
    }
    usort($list, fn($a, $b) => $b['at'] <=> $a['at']);
    answer(['ok' => true, 'reports' => array_slice($list, 0, 500), 'new' => count(array_filter($list, fn($x) => $x['status'] === 'new'))]);
}

if ($do === 'read' && $method === 'GET') {
    owner_only();
    $p = report_path((string)($_GET['id'] ?? ''));
    $rep = kept($p);
    if (!$rep) { refuse(404, 'no such report'); }
    if ($rep['status'] === 'new') { $rep['status'] = 'seen'; $rep['updated'] = time(); keep($p, $rep); }
    unset($rep['key']);
    answer(['ok' => true, 'report' => $rep]);
}

if ($do === 'answer' && $method === 'POST') {
    owner_only();
    $p = report_path((string)($body['id'] ?? ''));
    $rep = kept($p);
    if (!$rep) { refuse(404, 'no such report'); }
    $st = (string)($body['status'] ?? $rep['status']);
    if (!in_array($st, ['new', 'seen', 'working', 'fixed', 'wont-fix'], true)) { refuse(400, 'not a status'); }
    $rep['status'] = $st;
    if (array_key_exists('reply', $body)) { $rep['reply'] = text($body, 'reply', 5000); }
    if (array_key_exists('fixed_in', $body)) { $rep['fixed_in'] = text($body, 'fixed_in', 40); }
    $rep['updated'] = time();
    if (!keep($p, $rep)) { refuse(500, 'could not save it'); }
    answer(['ok' => true, 'id' => $rep['id'], 'status' => $rep['status']]);
}

if ($do === 'remove' && $method === 'POST') {
    owner_only();
    $p = report_path((string)($body['id'] ?? ''));
    if (is_file($p)) { @unlink($p); }
    answer(['ok' => true]);
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
