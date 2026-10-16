<?php
/**
 * licences.php — who has bought which plan, until when, on which computer, and
 * how much of it they have used. Part of Botix Home; index.php hands these
 * requests here.
 *
 *   ?do=licence-activate  (a copy)  a key entered on a computer: bind it, or say it is in use
 *   ?do=licence-check     (a copy)  the plan as it stands now, signed (every few hours)
 *   ?do=licence-use       (a copy)  may this job run? counted here, so a plan is a plan
 *   ?do=licence-release   (a copy)  this computer gives its place back
 *   ?do=stripe            (Stripe)  a payment, an upgrade, a cancellation — signed by Stripe
 *   ?do=welcome           (a buyer) after paying: the key, shown on the thank-you page
 *   ?do=licence-list / -new / -change / -key / -unbind / -settings / -setup   (the owner)
 *
 * WHAT A PLAN IS
 *
 *   Limits on the work, never on the AI: leads found, emails sent and posts
 *   written each day for each website, the words in a post, the websites on the
 *   account, Website Check reports each period. The same whether the person uses
 *   their own AI key or Botix AI — the plan is what they bought.
 *
 * ONE PERSON, ONE KEY
 *
 *   A key works on as many computers as its plan allows (one, unless the owner
 *   says otherwise). Entering it on another computer asks to move it there; the
 *   first computer stops at its next check. A key can move a few times a month
 *   (a new laptop, a reinstall) and no more, so passing it round is useless.
 *   And one computer at a time: two copies running with the same key at once
 *   are told apart and the second is refused.
 *
 *   Not an IP address: most connections change address every day, and two
 *   people in one office share one. A computer is a random number made when
 *   the program is installed — nothing about the person or the machine.
 *
 * WHAT IS KEPT HERE
 *
 *   For each licence: the email it was bought with, the plan, the dates
 *   (bought, plan changed, paid until, cancelled), the computers it is on (their
 *   random numbers and the names the person gave them), and counts of work done
 *   this day and this period. Never what the work was: no lead, post or mail
 *   reaches this server. Keys themselves are kept only as fingerprints.
 */

declare(strict_types=1);

const LIC_ACTIVE = ['trial', 'active', 'past_due'];
const LIC_RUN_FRESH = 600;              // a copy that checked in within this long is "running"
const LIC_KEEP_DAYS = 40;               // daily counts kept this long
const LIC_SITE_DAYS = 30;               // a website not worked on for this long gives its place back
const LIC_TRIES_HOUR = 30;              // keys entered from one address in an hour

function lic_dir(): string { return private_dir('licences'); }

function lic_settings(): array {
    $s = kept(private_dir() . '/licence-settings.php') ?? [];
    return $s + ['plans' => [], 'cancel_effect' => 'now', 'grace_days' => 3, 'moves_per_month' => 3,
                 'offline_days' => 3, 'trial_days' => 14, 'prices' => [], 'pricing_url' => '', 'portal_url' => '',
                 'mail_from' => '', 'stripe_secret' => ''];
}

/** Keys entered from one address: a few dozen an hour, kept as a one-way fingerprint for an hour. */
function lic_rate_ok(): bool {
    $path = lic_dir() . '/tries.php';
    $r = kept($path) ?? [];
    $now = time();
    $who = hash('sha256', (string)($_SERVER['REMOTE_ADDR'] ?? '') . __DIR__);
    foreach ($r as $k => $times) {
        $r[$k] = array_values(array_filter(is_array($times) ? $times : [], fn($t) => is_int($t) && $t > $now - 3600));
        if (!$r[$k]) { unset($r[$k]); }
    }
    if (count($r[$who] ?? []) >= LIC_TRIES_HOUR) { return false; }
    $r[$who][] = $now;
    keep($path, $r);
    return true;
}

function lic_norm_key(string $key): string {
    $k = strtoupper(preg_replace('/[^A-Za-z0-9]/', '', $key));
    return strpos($k, 'BTX') === 0 ? substr($k, 3) : $k;
}

function lic_hash(string $key): string { return hash('sha256', 'botix-licence:' . lic_norm_key($key)); }

function lic_new_key(): string {
    $abc = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
    $out = [];
    for ($g = 0; $g < 4; $g++) {
        $part = '';
        for ($i = 0; $i < 5; $i++) { $part .= $abc[random_int(0, 31)]; }
        $out[] = $part;
    }
    return 'BTX-' . implode('-', $out);
}

/** Every change to licences holds this lock: two requests never write over each other. */
function lic_locked(callable $fn) {
    $fh = fopen(lic_dir() . '/.lock', 'c');
    flock($fh, LOCK_EX);
    try { return $fn(); } finally { flock($fh, LOCK_UN); fclose($fh); }
}

function lic_index(): array {
    return (kept(lic_dir() . '/index.php') ?? []) + ['by_id' => [], 'by_sub' => [], 'by_customer' => [],
                                                     'by_session' => [], 'trials' => []];
}
function lic_save_index(array $ix): void { keep(lic_dir() . '/index.php', $ix); }
function lic_load(string $hash): ?array { return preg_match('/^[a-f0-9]{64}$/', $hash) ? kept(lic_dir() . "/$hash.php") : null; }
function lic_save(string $hash, array $lic): void { keep(lic_dir() . "/$hash.php", $lic); }

function lic_find_key(string $key): array {
    $h = lic_hash($key);
    return [$h, lic_load($h)];
}

function lic_unknown(): void {
    answer(['ok' => false, 'unknown_key' => true, 'error' => 'That key is not known. Check it, or ask for it again.'], 404);
}

/** The day a licence's counts are under now: the copy's own day, last it said. */
function lic_today(array $lic): string {
    $day = (string)($lic['last_day'] ?? '');
    return $day >= gmdate('Y-m-d', time() - 12 * 3600) ? $day : gmdate('Y-m-d');
}

function lic_plan(array $lic): array {
    $plans = lic_settings()['plans'];
    return is_array($plans[$lic['plan'] ?? ''] ?? null) ? $plans[$lic['plan']] : ['name' => (string)($lic['plan'] ?? '')];
}

/** The status that counts now: a plan past its paid date (and the grace days) has ended. */
function lic_status(array $lic): string {
    $s = (string)($lic['status'] ?? 'pending');
    $now = time();
    if (in_array($s, ['cancelled', 'trial_used', 'pending'], true)) { return $s; }
    if (!empty($lic['cancel_at']) && $now >= (int)$lic['cancel_at']) { return 'cancelled'; }
    $grace = (int)lic_settings()['grace_days'] * 86400;
    if (!empty($lic['period_end']) && $now > (int)$lic['period_end'] + ($s === 'trial' ? 0 : $grace)) { return 'expired'; }
    return $s;
}

function lic_why(string $status, array $lic): string {
    $until = !empty($lic['period_end']) ? date('j M Y', (int)$lic['period_end']) : '';
    switch ($status) {
        case 'cancelled': return 'Your Botix plan was cancelled' . (!empty($lic['cancelled_at']) ? ' on ' . date('j M Y', (int)$lic['cancelled_at']) : '')
                                 . '. Your work is safe — renew your plan to carry on.';
        case 'expired': return 'Your Botix plan ended' . ($until ? " on $until" : '') . '. Your work is safe — renew your plan to carry on.';
        case 'trial_used': return 'This address has already had a trial. Choose a plan to carry on.';
        case 'pending': return 'Your payment is still being confirmed. Try again in a minute.';
    }
    return '';
}

function lic_email_mask(string $e): string {
    if (!preg_match('/^(.)(.*)(@.*)$/', $e, $m)) { return ''; }
    return $m[1] . str_repeat('•', min(6, strlen($m[2]))) . $m[3];
}

function b64u(string $s): string { return rtrim(strtr(base64_encode($s), '+/', '-_'), '='); }

/** The plan, signed with the Home's licence key, so a copy can trust it offline for a few days. */
function lic_token(array $lic, string $device): string {
    $key = kept(private_dir() . '/licence-key.php') ?? [];
    $seed = base64_decode((string)($key['seed'] ?? ''), true);
    if (!is_string($seed) || strlen($seed) !== 32 || !function_exists('sodium_crypto_sign_seed_keypair')) {
        refuse(503, 'the licence server is not set up yet — the owner sets it up from his program');
    }
    $st = lic_settings();
    $plan = lic_plan($lic);
    $status = lic_status($lic);
    $day = lic_today($lic);
    $period = 'm:' . (int)($lic['period_start'] ?? 0);
    // When it stops by itself, so a copy that cannot reach here still stops on time.
    $ends = empty($lic['period_end']) ? 0
          : (int)$lic['period_end'] + (($lic['status'] ?? '') === 'trial' ? 0 : (int)$st['grace_days'] * 86400);
    if (!empty($lic['cancel_at'])) { $ends = $ends ? min($ends, (int)$lic['cancel_at']) : (int)$lic['cancel_at']; }
    $payload = json_encode([
        'v' => 1, 'lic' => $lic['id'], 'plan' => $lic['plan'] ?? '', 'plan_name' => (string)($plan['name'] ?? ($lic['plan'] ?? '')),
        'variant' => $lic['variant'] ?? 'own', 'status' => $status, 'why' => lic_why($status, $lic),
        'bought_at' => (int)($lic['bought_at'] ?? 0), 'plan_since' => (int)($lic['plan_since'] ?? 0),
        'period_start' => (int)($lic['period_start'] ?? 0), 'period_end' => (int)($lic['period_end'] ?? 0),
        'cancel_at' => (int)($lic['cancel_at'] ?? 0), 'ends_at' => $ends,
        'limits' => (array)($plan['limits'] ?? []), 'websites' => (int)($plan['websites'] ?? 1),
        'post_words' => (int)($plan['post_words'] ?? 0), 'features' => array_values((array)($plan['features'] ?? [])),
        'devices' => (int)($plan['devices'] ?? 1), 'device' => $device,
        'usage' => ['day' => $day, 'today' => $lic['usage']["d:$day"] ?? new stdClass(),
                    'period' => $lic['usage'][$period] ?? new stdClass()],
        'issued' => time(), 'valid_until' => time() + (int)$st['offline_days'] * 86400,
        'pricing_url' => (string)$st['pricing_url'], 'portal_url' => (string)$st['portal_url'],
        'email' => lic_email_mask((string)($lic['email'] ?? '')),
    ], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    $kp = sodium_crypto_sign_seed_keypair($seed);
    return b64u($payload) . '.' . b64u(sodium_crypto_sign_detached($payload, sodium_crypto_sign_secretkey($kp)));
}

function lic_history(array &$lic, string $what, string $note = ''): void {
    $lic['history'][] = ['at' => time(), 'what' => $what, 'plan' => $lic['plan'] ?? '', 'note' => substr($note, 0, 200)];
    $lic['history'] = array_slice($lic['history'], -100);
}

function lic_device_id($v): string { $v = strtolower(trim((string)$v)); return preg_match('/^[a-f0-9]{16,64}$/', $v) ? $v : ''; }
function lic_run_id($v): string { $v = strtolower(trim((string)$v)); return preg_match('/^[a-f0-9]{8,64}$/', $v) ? $v : ''; }

/**
 * One computer at a time with one key: a copy that checked in within the last
 * few minutes under another run is running, and a second is refused — unless
 * it is the same copy carrying on (it names the run it had before).
 */
function lic_run_ok(array &$dev, array $b): bool {
    $run = lic_run_id($b['run'] ?? '');
    if ($run === '') { return true; }
    $mine = (string)($dev['run'] ?? '');
    if ($mine !== '' && $mine !== $run && (int)($dev['run_at'] ?? 0) > time() - LIC_RUN_FRESH
            && lic_run_id($b['prev_run'] ?? '') !== $mine) {
        return false;
    }
    $dev['run'] = $run;
    $dev['run_at'] = time();
    return true;
}

function lic_prune(array &$lic): void {
    $keep = 'm:' . (int)($lic['period_start'] ?? 0);
    $cut = gmdate('Y-m-d', time() - LIC_KEEP_DAYS * 86400);
    $periods = array_values(array_filter(array_keys((array)($lic['usage'] ?? [])), fn($x) => strncmp((string)$x, 'm:', 2) === 0));
    rsort($periods);
    foreach (array_keys((array)($lic['usage'] ?? [])) as $k) {
        $k = (string)$k;
        if ($k === $keep) { continue; }
        if (strncmp($k, 'd:', 2) === 0 && substr($k, 2) >= $cut) { continue; }
        if (strncmp($k, 'm:', 2) === 0 && in_array($k, array_slice($periods, 0, 3), true)) { continue; }
        unset($lic['usage'][$k]);
    }
}

/**
 * The day the copy says it is — its own calendar, so a day's limit starts at
 * its midnight — if that day is today somewhere on Earth; this server's day
 * otherwise. Never a day before one it has already used: moving the clock
 * back does not give a day's work twice.
 */
function lic_day($v, array &$lic): string {
    $v = (string)$v;
    $now = time();
    $day = ($v >= gmdate('Y-m-d', $now - 12 * 3600) && $v <= gmdate('Y-m-d', $now + 14 * 3600)
            && preg_match('/^\d{4}-\d{2}-\d{2}$/', $v)) ? $v : gmdate('Y-m-d', $now);
    if ($day < (string)($lic['last_day'] ?? '')) { $day = (string)$lic['last_day']; }
    $lic['last_day'] = $day;
    return $day;
}

/** A day work was done while the server could not be reached: within the days a copy may work alone. */
function lic_past_day($v): string {
    $v = (string)$v;
    $now = time();
    $from = gmdate('Y-m-d', $now - ((int)lic_settings()['offline_days'] + 1) * 86400);
    return (preg_match('/^\d{4}-\d{2}-\d{2}$/', $v) && $v >= $from && $v <= gmdate('Y-m-d', $now + 14 * 3600))
         ? $v : gmdate('Y-m-d', $now);
}

/** A website, as the copy names it: a fingerprint of its address (never the address). */
function lic_site($v): string {
    $v = strtolower(trim((string)$v));
    if (!preg_match('/^[a-z0-9_-]{1,64}$/', $v)) { return 'main'; }
    return ctype_digit($v) ? "s$v" : $v;
}

/** The websites worked on lately, oldest first; one left alone for a month gives its place back. */
function lic_sites(array &$lic): array {
    $sites = [];
    foreach ((array)($lic['sites'] ?? []) as $k => $t) {
        if (is_int($k)) { $sites[(string)$t] = time(); continue; }        // an older list of names
        if ((int)$t > time() - LIC_SITE_DAYS * 86400) { $sites[(string)$k] = (int)$t; }
    }
    asort($sites);
    $lic['sites'] = $sites;
    return $sites;
}

function lic_count(array &$lic, string $kind, int $n, string $site, string $day): array {
    $plan = lic_plan($lic);
    $lim = (array)(($plan['limits'] ?? [])[$kind] ?? []);
    if (!$lim) { return ['allowed' => true, 'limit' => null]; }
    $cap = (int)($lim['n'] ?? 0);
    $per = in_array($lim['per'] ?? 'day', ['day', 'period', 'month'], true) ? $lim['per'] : 'day';
    $scope = ($lim['scope'] ?? 'site') === 'account' ? '*' : $site;
    if ($cap <= 0) {
        return ['allowed' => false, 'limit' => 0, 'per' => $per, 'why' => 'not_in_plan'];
    }
    if ($scope !== '*') {
        $sites = lic_sites($lic);
        if (!isset($sites[$scope]) && count($sites) >= max(1, (int)($plan['websites'] ?? 1))) {
            return ['allowed' => false, 'limit' => $cap, 'per' => $per, 'why' => 'websites',
                    'resets_at' => (int)reset($sites) + LIC_SITE_DAYS * 86400];
        }
        $lic['sites'][$scope] = time();
    }
    $slot = $per === 'day' ? "d:$day" : 'm:' . (int)($lic['period_start'] ?? 0);
    $used = (int)($lic['usage'][$slot][$kind][$scope] ?? 0);
    $resets = $per === 'day' ? (int)strtotime($day . ' 00:00:00 UTC +1 day') : (int)($lic['period_end'] ?? 0);
    if ($used + $n > $cap) {
        return ['allowed' => false, 'used' => $used, 'limit' => $cap, 'per' => $per, 'resets_at' => $resets, 'why' => 'used_up'];
    }
    $lic['usage'][$slot][$kind][$scope] = $used + $n;
    return ['allowed' => true, 'used' => $used + $n, 'limit' => $cap, 'per' => $per, 'resets_at' => $resets];
}

/** A copy's request: the licence for its key, bound to its computer, still running there alone. */
function lic_for_copy(array $b, bool $need_active = true): array {
    [$h, $lic] = lic_find_key((string)($b['key'] ?? ''));
    if (!$lic) { lic_unknown(); }
    $dev = lic_device_id($b['device'] ?? '');
    if ($dev === '') { refuse(400, 'this computer did not say who it is'); }
    lic_day($b['day'] ?? '', $lic);
    $i = null;
    foreach ((array)($lic['devices'] ?? []) as $n => $d) { if (($d['id'] ?? '') === $dev) { $i = $n; } }
    if ($i === null) {
        answer(['ok' => false, 'moved' => true, 'error' => 'This key is now in use on another computer. Enter it again here to move it back.'], 409);
    }
    if (!lic_run_ok($lic['devices'][$i], $b)) {
        answer(['ok' => false, 'busy' => true, 'error' => 'This key is in use on another computer right now. Close Botix there, or wait a few minutes, then try again.'], 409);
    }
    $lic['devices'][$i]['seen'] = time();
    $st = lic_status($lic);
    if ($need_active && !in_array($st, LIC_ACTIVE, true)) {
        lic_save($h, $lic);
        answer(['ok' => false, 'status' => $st, 'error' => lic_why($st, $lic), 'token' => lic_token($lic, $dev)], 402);
    }
    return [$h, $lic, $dev];
}

// ---------------------------------------------------------------------------
// what a copy asks
// ---------------------------------------------------------------------------

if ($do === 'licence-activate' && $method === 'POST') {
    if (!lic_rate_ok()) { refuse(429, 'too many tries from here — wait an hour'); }
    lic_locked(function () use ($body) {
        [$h, $lic] = lic_find_key((string)($body['key'] ?? ''));
        if (!$lic) { lic_unknown(); }
        $dev = lic_device_id($body['device'] ?? '');
        if ($dev === '') { refuse(400, 'this computer did not say who it is'); }
        lic_day($body['day'] ?? '', $lic);
        $name = substr(trim(strip_tags((string)($body['name'] ?? 'A computer'))), 0, 60) ?: 'A computer';
        $st = lic_settings();
        $seats = max(1, (int)(lic_plan($lic)['devices'] ?? 1));
        $lic['devices'] = array_values((array)($lic['devices'] ?? []));
        $lic['moves'] = array_values(array_filter((array)($lic['moves'] ?? []), fn($t) => (int)$t > time() - 30 * 86400));
        $mine = null;
        foreach ($lic['devices'] as $n => $d) { if (($d['id'] ?? '') === $dev) { $mine = $n; } }
        if ($mine === null) {
            if (count($lic['devices']) >= $seats) {
                $left = max(0, (int)$st['moves_per_month'] - count($lic['moves']));
                if (empty($body['move'])) {
                    $others = array_map(fn($d) => ['name' => $d['name'] ?? 'A computer', 'since' => (int)($d['first'] ?? 0),
                                                   'seen' => (int)($d['seen'] ?? 0)], $lic['devices']);
                    answer(['ok' => false, 'in_use' => true, 'computers' => $others, 'moves_left' => $left,
                            'error' => 'This key is in use on another computer. Move it here? That computer will stop using it.'], 409);
                }
                if ($left <= 0) {
                    answer(['ok' => false, 'moves_left' => 0,
                            'error' => 'This key has moved computers ' . (int)$st['moves_per_month'] . ' times in the last month, '
                                     . 'which is as often as a plan allows. Ask for help if you have a new computer.'], 409);
                }
                usort($lic['devices'], fn($a, $b2) => (int)($a['seen'] ?? 0) <=> (int)($b2['seen'] ?? 0));
                $gone = array_shift($lic['devices']);
                $lic['moves'][] = time();
                lic_history($lic, 'computer_moved', 'from ' . ($gone['name'] ?? 'a computer') . ' to ' . $name);
            } else {
                lic_history($lic, 'computer_added', $name);
            }
            $lic['devices'][] = ['id' => $dev, 'name' => $name, 'first' => time(), 'seen' => time(), 'run' => '', 'run_at' => 0];
            $mine = count($lic['devices']) - 1;
        }
        $lic['devices'][$mine]['name'] = $name;
        if (!lic_run_ok($lic['devices'][$mine], $body)) {
            answer(['ok' => false, 'busy' => true, 'error' => 'This key is in use on another computer right now.'], 409);
        }
        $lic['devices'][$mine]['seen'] = time();
        lic_save($h, $lic);
        $status = lic_status($lic);
        answer(['ok' => in_array($status, LIC_ACTIVE, true), 'status' => $status, 'error' => lic_why($status, $lic),
                'token' => lic_token($lic, $dev), 'moves_left' => max(0, (int)$st['moves_per_month'] - count($lic['moves']))]);
    });
}

if ($do === 'licence-check' && $method === 'POST') {
    lic_locked(function () use ($body) {
        [$h, $lic, $dev] = lic_for_copy($body, false);
        // Work done while this server could not be reached, counted now (never refused afterwards).
        foreach (array_slice((array)($body['pending'] ?? []), 0, 500) as $p) {
            $kind = preg_replace('/[^a-z_]/', '', (string)($p['kind'] ?? ''));
            if ($kind === '') { continue; }
            $lim = (array)((lic_plan($lic)['limits'] ?? [])[$kind] ?? []);
            if (!$lim) { continue; }
            $day = lic_past_day($p['day'] ?? '');
            $scope = ($lim['scope'] ?? 'site') === 'account' ? '*' : lic_site($p['site'] ?? '');
            $slot = ($lim['per'] ?? 'day') === 'day' ? "d:$day" : 'm:' . (int)($lic['period_start'] ?? 0);
            $lic['usage'][$slot][$kind][$scope] = (int)($lic['usage'][$slot][$kind][$scope] ?? 0) + max(0, min(1000, (int)($p['n'] ?? 1)));
        }
        lic_prune($lic);
        lic_save($h, $lic);
        $status = lic_status($lic);
        answer(['ok' => in_array($status, LIC_ACTIVE, true), 'status' => $status, 'error' => lic_why($status, $lic),
                'token' => lic_token($lic, $dev)]);
    });
}

if ($do === 'licence-use' && $method === 'POST') {
    lic_locked(function () use ($body) {
        [$h, $lic, $dev] = lic_for_copy($body);
        $kind = preg_replace('/[^a-z_]/', '', (string)($body['kind'] ?? ''));
        $n = max(1, min(1000, (int)($body['n'] ?? 1)));
        $before = ['usage' => $lic['usage'] ?? [], 'sites' => $lic['sites'] ?? [], 'last_day' => $lic['last_day'] ?? ''];
        $got = lic_count($lic, $kind, $n, lic_site($body['site'] ?? ''), lic_day($body['day'] ?? '', $lic));
        if (!empty($body['dry'])) {           // a look, not a use: nothing is counted
            $lic['usage'] = $before['usage'];
            $lic['sites'] = $before['sites'];
            $lic['last_day'] = $before['last_day'];
        }
        lic_prune($lic);
        lic_save($h, $lic);
        answer(['ok' => true] + $got + ['token' => lic_token($lic, $dev)]);
    });
}

if ($do === 'licence-release' && $method === 'POST') {
    lic_locked(function () use ($body) {
        [$h, $lic] = lic_find_key((string)($body['key'] ?? ''));
        if (!$lic) { lic_unknown(); }
        $dev = lic_device_id($body['device'] ?? '');
        $before = count((array)($lic['devices'] ?? []));
        $lic['devices'] = array_values(array_filter((array)($lic['devices'] ?? []), fn($d) => ($d['id'] ?? '') !== $dev));
        if (count($lic['devices']) !== $before) { lic_history($lic, 'computer_released'); }
        lic_save($h, $lic);
        answer(['ok' => true]);
    });
}

// ---------------------------------------------------------------------------
// Stripe: a payment, an upgrade, a cancellation
// ---------------------------------------------------------------------------

function lic_rank(string $plan): int {
    $i = array_search($plan, array_keys((array)lic_settings()['plans']), true);
    return $i === false ? -1 : (int)$i;
}

function lic_create(array &$ix, array $fields): array {
    $key = lic_new_key();
    $h = lic_hash($key);
    $lic = $fields + ['id' => 'lic_' . bin2hex(random_bytes(6)), 'email' => '', 'name' => '',
                      'plan' => '', 'variant' => 'own', 'status' => 'pending', 'bought_at' => time(), 'plan_since' => time(),
                      'period_start' => time(), 'period_end' => 0, 'cancel_at' => 0, 'cancelled_at' => 0, 'source' => 'manual',
                      'history' => [], 'devices' => [], 'moves' => [], 'sites' => [], 'usage' => []];
    $lic['key_hint'] = substr($key, -5);
    lic_history($lic, 'bought', (string)($fields['note'] ?? ''));
    unset($lic['note']);
    $ix['by_id'][$lic['id']] = $h;
    // The key itself, for the thank-you page and the email, for two days at most.
    keep(lic_dir() . '/welcome-' . $lic['id'] . '.php', ['key' => $key, 'until' => time() + 2 * 86400]);
    return [$h, $lic, $key];
}

function lic_welcome_key(string $id): string {
    if (!preg_match('/^lic_[a-f0-9]{12}$/', $id)) { return ''; }
    $w = kept(lic_dir() . "/welcome-$id.php");
    if (!$w) { return ''; }
    if ((int)($w['until'] ?? 0) < time()) { @unlink(lic_dir() . "/welcome-$id.php"); return ''; }
    return (string)($w['key'] ?? '');
}

function lic_mail_key(array $lic, string $key): void {
    $from = (string)lic_settings()['mail_from'];
    $to = (string)($lic['email'] ?? '');
    if ($from === '' || $key === '' || !filter_var($to, FILTER_VALIDATE_EMAIL)) { return; }
    $name = (string)(config()['product'] ?? 'Botix');
    $plan = (string)(lic_plan($lic)['name'] ?? $lic['plan']);
    $text = "Thank you for choosing $name ($plan).\n\nYour key:\n\n    $key\n\nOpen $name, sign in, and enter this key when it asks. "
          . "It works on one computer at a time; entering it on another computer moves it there.\n\nKeep it safe — it is how $name knows your plan.\n";
    @mail($to, "Your $name key", $text, "From: $from\r\nContent-Type: text/plain; charset=utf-8");
}

/** The period a Stripe subscription is paid for (its items carry it in newer API versions). */
function lic_sub_period(array $sub): array {
    $item = (array)(($sub['items']['data'] ?? [])[0] ?? []);
    $start = (int)($sub['current_period_start'] ?? ($item['current_period_start'] ?? 0));
    $end = (int)($sub['current_period_end'] ?? ($item['current_period_end'] ?? 0));
    if (($sub['status'] ?? '') === 'trialing' && !empty($sub['trial_end'])) { $end = (int)$sub['trial_end']; }
    return [$start, $end];
}

/** One trial per customer: an address that has had one before gets none, whichever Stripe message arrives first. */
function lic_trial_once(array &$lic, array &$ix): void {
    $email = (string)($lic['email'] ?? '');
    if (($lic['status'] ?? '') !== 'trial' || $email === '' || !empty($lic['trial_ok'])) { return; }
    $eh = hash('sha256', 'trial:' . $email);
    if (in_array($eh, $ix['trials'], true)) {
        $lic['status'] = 'trial_used';
        lic_history($lic, 'trial_refused', 'this address had a trial before');
    } else {
        $ix['trials'][] = $eh;
        $lic['trial_ok'] = true;
    }
}

function lic_stripe_ok(string $raw, string $secret): bool {
    $sig = (string)($_SERVER['HTTP_STRIPE_SIGNATURE'] ?? '');
    if ($secret === '' || $sig === '') { return false; }
    $t = 0;
    $v1 = [];
    foreach (explode(',', $sig) as $part) {
        [$k, $v] = array_pad(explode('=', trim($part), 2), 2, '');
        if ($k === 't') { $t = (int)$v; }
        if ($k === 'v1') { $v1[] = $v; }
    }
    if ($t === 0 || abs(time() - $t) > 300) { return false; }
    $want = hash_hmac('sha256', $t . '.' . $raw, $secret);
    foreach ($v1 as $v) { if (hash_equals($want, $v)) { return true; } }
    return false;
}

if ($do === 'stripe' && $method === 'POST') {
    $st = lic_settings();
    if (!lic_stripe_ok($raw, (string)$st['stripe_secret'])) { refuse(400, 'not signed by Stripe'); }
    $type = (string)($body['type'] ?? '');
    $obj = (array)($body['data']['object'] ?? []);
    lic_locked(function () use ($type, $obj, $st) {
        $ix = lic_index();
        $map = (array)$st['prices'];
        if (in_array($type, ['customer.subscription.created', 'customer.subscription.updated', 'customer.subscription.deleted'], true)) {
            $sub = (string)($obj['id'] ?? '');
            $cus = (string)($obj['customer'] ?? '');
            $h = $ix['by_sub'][$sub] ?? ($ix['by_customer'][$cus] ?? '');
            $lic = $h ? lic_load($h) : null;
            $price = (string)((($obj['items']['data'] ?? [])[0]['price']['id'] ?? ''));
            $want = (array)($map[$price] ?? []);
            $new = !$lic;
            if ($new) {
                [$h, $lic] = lic_create($ix, ['source' => 'stripe', 'plan' => (string)($want['plan'] ?? ''),
                                               'variant' => (string)($want['variant'] ?? 'own')]);
            }
            $lic['stripe'] = ['customer' => $cus, 'subscription' => $sub];
            $ix['by_sub'][$sub] = $h;
            $ix['by_customer'][$cus] = $h;
            [$start, $end] = lic_sub_period($obj);
            if (!empty($want['plan']) && ($want['plan'] ?? '') !== ($lic['plan'] ?? '')) {
                $up = lic_rank((string)$want['plan']) > lic_rank((string)($lic['plan'] ?? ''));
                $lic['plan'] = (string)$want['plan'];
                $lic['variant'] = (string)($want['variant'] ?? 'own');
                $lic['plan_since'] = time();
                if (!$new) { lic_history($lic, $up ? 'upgraded' : 'downgraded', $price); }
            }
            if ($end && !$new && (int)($lic['period_end'] ?? 0) > 0 && $start >= (int)$lic['period_end'] - 86400) {
                lic_history($lic, 'renewed');
            }
            if ($start) { $lic['period_start'] = $start; }
            if ($end) { $lic['period_end'] = $end; }
            $status = ['trialing' => 'trial', 'active' => 'active', 'past_due' => 'past_due', 'unpaid' => 'past_due',
                       'canceled' => 'cancelled', 'incomplete' => 'pending', 'incomplete_expired' => 'cancelled',
                       'paused' => 'cancelled'][(string)($obj['status'] ?? '')] ?? 'pending';
            if ($type === 'customer.subscription.deleted') { $status = 'cancelled'; }
            if (!empty($obj['cancel_at_period_end']) && $status !== 'cancelled') {
                if ($st['cancel_effect'] === 'now') {
                    $status = 'cancelled';
                } else {
                    $lic['cancel_at'] = $end;
                }
            } elseif (empty($obj['cancel_at_period_end'])) {
                $lic['cancel_at'] = 0;
            }
            if ($status === 'cancelled' && ($lic['status'] ?? '') !== 'cancelled') {
                $lic['cancelled_at'] = time();
                lic_history($lic, 'cancelled');
            } elseif ($status !== 'cancelled' && ($lic['status'] ?? '') === 'cancelled') {
                $lic['cancelled_at'] = 0;
                lic_history($lic, 'reactivated');
            }
            if ($status === 'past_due' && ($lic['status'] ?? '') !== 'past_due') { lic_history($lic, 'payment_failed'); }
            if (($lic['status'] ?? '') !== 'trial_used' || $status !== 'trial') { $lic['status'] = $status; }
            lic_trial_once($lic, $ix);
            lic_save($h, $lic);
        } elseif ($type === 'checkout.session.completed') {
            $cus = (string)($obj['customer'] ?? '');
            $sub = (string)($obj['subscription'] ?? '');
            $email = strtolower(trim((string)($obj['customer_details']['email'] ?? ($obj['customer_email'] ?? ''))));
            $h = $ix['by_sub'][$sub] ?? ($ix['by_customer'][$cus] ?? '');
            $lic = $h ? lic_load($h) : null;
            if (!$lic) {
                [$h, $lic] = lic_create($ix, ['source' => 'stripe']);
                $ix['by_sub'][$sub] = $h;
                $ix['by_customer'][$cus] = $h;
                $lic['stripe'] = ['customer' => $cus, 'subscription' => $sub];
            }
            $lic['email'] = $email;
            lic_trial_once($lic, $ix);
            $ix['by_session'][(string)($obj['id'] ?? '')] = $lic['id'];
            $ix['by_session'] = array_slice($ix['by_session'], -2000, null, true);
            lic_save($h, $lic);
            lic_mail_key($lic, lic_welcome_key($lic['id']));
        } elseif ($type === 'invoice.payment_failed') {
            $h = $ix['by_sub'][(string)($obj['subscription'] ?? '')] ?? ($ix['by_customer'][(string)($obj['customer'] ?? '')] ?? '');
            $lic = $h ? lic_load($h) : null;
            if ($lic && ($lic['status'] ?? '') === 'active') {
                $lic['status'] = 'past_due';
                lic_history($lic, 'payment_failed');
                lic_save($h, $lic);
            }
        }
        lic_save_index($ix);
        answer(['ok' => true]);
    });
}

if ($do === 'welcome' && $method === 'GET') {
    $ix = lic_index();
    $id = (string)($ix['by_session'][(string)($_GET['session'] ?? '')] ?? '');
    $key = $id ? lic_welcome_key($id) : '';
    $name = htmlspecialchars((string)(config()['product'] ?? 'Botix'), ENT_QUOTES);
    while (ob_get_level() > 0) { ob_end_clean(); }
    header('Content-Type: text/html; charset=utf-8');
    header('Cache-Control: no-store');
    header('X-Robots-Tag: noindex, nofollow');
    echo "<!doctype html><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
       . ($id ? '' : '<meta http-equiv="refresh" content="5">')
       . "<title>$name — your key</title><style>body{font:16px/1.55 system-ui,sans-serif;margin:0;background:#f5f6f8;color:#111318}"
       . "main{max-width:560px;margin:10vh auto;padding:28px;background:#fff;border:1px solid #e3e5ea;border-radius:14px}"
       . "code{display:block;font:600 22px/1.4 ui-monospace,monospace;letter-spacing:1px;padding:14px;background:#f5f6f8;border-radius:10px;margin:12px 0;user-select:all}"
       . "p.m{color:#5e6573;font-size:14px}</style><main><h1 style=\"margin:0 0 6px\">Thank you</h1>"
       . ($key ? "<p>Your $name key:</p><code>" . htmlspecialchars($key, ENT_QUOTES) . "</code>"
                 . "<p>Open $name, sign in, and enter this key when it asks. Copy it somewhere safe now.</p>"
                 . "<p class=\"m\">It works on one computer at a time. Entering it on another computer moves it there.</p>"
               : ($id ? "<p>This page has expired. If you did not keep your key, write to us and we will make you a new one.</p>"
                      : "<p>Your payment went through. Your key is being prepared — this page refreshes by itself in a few seconds.</p>"))
       . "</main>";
    exit;
}

// ---------------------------------------------------------------------------
// the owner
// ---------------------------------------------------------------------------

function lic_public(array $lic): array {
    $st = lic_status($lic);
    $day = lic_today($lic);
    return ['id' => $lic['id'], 'email' => $lic['email'] ?? '', 'name' => $lic['name'] ?? '', 'plan' => $lic['plan'] ?? '',
            'plan_name' => (string)(lic_plan($lic)['name'] ?? ($lic['plan'] ?? '')), 'variant' => $lic['variant'] ?? 'own',
            'status' => $st, 'stored_status' => $lic['status'] ?? '', 'source' => $lic['source'] ?? '',
            'key_hint' => $lic['key_hint'] ?? '', 'bought_at' => (int)($lic['bought_at'] ?? 0),
            'plan_since' => (int)($lic['plan_since'] ?? 0), 'period_start' => (int)($lic['period_start'] ?? 0),
            'period_end' => (int)($lic['period_end'] ?? 0), 'cancel_at' => (int)($lic['cancel_at'] ?? 0),
            'cancelled_at' => (int)($lic['cancelled_at'] ?? 0),
            'computers' => array_map(fn($d) => ['name' => $d['name'] ?? '', 'first' => (int)($d['first'] ?? 0),
                                                'seen' => (int)($d['seen'] ?? 0)], (array)($lic['devices'] ?? [])),
            'moves' => count(array_filter((array)($lic['moves'] ?? []), fn($t) => (int)$t > time() - 30 * 86400)),
            'websites' => count(lic_sites($lic)),
            'today' => $lic['usage']["d:$day"] ?? new stdClass(),
            'period' => $lic['usage']['m:' . (int)($lic['period_start'] ?? 0)] ?? new stdClass(),
            'history' => array_slice((array)($lic['history'] ?? []), -30)];
}

function lic_by_id(string $id): array {
    $ix = lic_index();
    $h = (string)($ix['by_id'][$id] ?? '');
    $lic = $h ? lic_load($h) : null;
    if (!$lic) { refuse(404, 'no such licence'); }
    return [$h, $lic, $ix];
}

if (in_array($do, ['licence-list', 'licence-new', 'licence-change', 'licence-key', 'licence-unbind',
                   'licence-settings', 'licence-setup'], true)) {
    owner_only();
}

if ($do === 'licence-setup') {
    $path = private_dir() . '/licence-key.php';
    if ($method === 'POST') {
        $seed = base64_decode((string)($body['seed'] ?? ''), true);
        if (!is_string($seed) || strlen($seed) !== 32) { refuse(400, 'that is not a licence key'); }
        if (is_file($path) && empty($body['rotate'])) { refuse(409, 'the licence key is already set; send rotate to replace it'); }
        keep($path, ['seed' => base64_encode($seed), 'at' => time()]);
    }
    $k = kept($path) ?? [];
    $seed = base64_decode((string)($k['seed'] ?? ''), true);
    $pub = (is_string($seed) && strlen($seed) === 32 && function_exists('sodium_crypto_sign_seed_keypair'))
         ? base64_encode(sodium_crypto_sign_publickey(sodium_crypto_sign_seed_keypair($seed))) : '';
    answer(['ok' => true, 'ready' => $pub !== '', 'public_key' => $pub, 'sodium' => function_exists('sodium_crypto_sign_seed_keypair')]);
}

if ($do === 'licence-settings') {
    $path = private_dir() . '/licence-settings.php';
    if ($method === 'POST') {
        $cur = kept($path) ?? [];
        foreach (['plans', 'prices'] as $k) { if (isset($body[$k]) && is_array($body[$k])) { $cur[$k] = $body[$k]; } }
        foreach (['grace_days', 'moves_per_month', 'offline_days', 'trial_days'] as $k) {
            if (isset($body[$k])) { $cur[$k] = max(0, min(60, (int)$body[$k])); }
        }
        if (isset($body['cancel_effect'])) { $cur['cancel_effect'] = $body['cancel_effect'] === 'period_end' ? 'period_end' : 'now'; }
        foreach (['pricing_url', 'portal_url', 'mail_from'] as $k) {
            if (isset($body[$k])) { $cur[$k] = substr(trim((string)$body[$k]), 0, 300); }
        }
        if (!empty($body['stripe_secret'])) { $cur['stripe_secret'] = substr(trim((string)$body['stripe_secret']), 0, 200); }
        keep($path, $cur);
    }
    $s = lic_settings();
    $s['has_stripe_secret'] = $s['stripe_secret'] !== '';
    unset($s['stripe_secret']);
    $s['webhook_url'] = here() . '/index.php?do=stripe';
    $s['welcome_url'] = here() . '/index.php?do=welcome&session={CHECKOUT_SESSION_ID}';
    answer(['ok' => true] + $s);
}

if ($do === 'licence-list' && $method === 'GET') {
    $out = [];
    foreach ((array)glob(lic_dir() . '/*.php') as $f) {
        if (!preg_match('/\/([a-f0-9]{64})\.php$/', (string)$f)) { continue; }
        $lic = kept((string)$f);
        if ($lic) { $out[] = lic_public($lic); }
    }
    usort($out, fn($a, $b) => $b['bought_at'] <=> $a['bought_at']);
    answer(['ok' => true, 'licences' => $out, 'plans' => lic_settings()['plans']]);
}

if ($do === 'licence-new' && $method === 'POST') {
    lic_locked(function () use ($body) {
        $plans = lic_settings()['plans'];
        $plan = (string)($body['plan'] ?? '');
        if (!isset($plans[$plan])) { refuse(400, 'choose one of your plans'); }
        $email = strtolower(trim((string)($body['email'] ?? '')));
        if ($email !== '' && !filter_var($email, FILTER_VALIDATE_EMAIL)) { refuse(400, 'that address does not look right'); }
        $days = max(1, min(3660, (int)($body['days'] ?? 30)));
        $ix = lic_index();
        [$h, $lic, $key] = lic_create($ix, ['email' => $email, 'name' => substr(trim((string)($body['name'] ?? '')), 0, 80),
                                             'plan' => $plan, 'variant' => ($body['variant'] ?? '') === 'ai' ? 'ai' : 'own',
                                             'status' => ($body['status'] ?? '') === 'trial' ? 'trial' : 'active',
                                             'period_end' => time() + $days * 86400, 'note' => (string)($body['note'] ?? '')]);
        lic_save($h, $lic);
        lic_save_index($ix);
        @unlink(lic_dir() . '/welcome-' . $lic['id'] . '.php');
        answer(['ok' => true, 'key' => $key, 'licence' => lic_public($lic)]);
    });
}

if ($do === 'licence-change' && $method === 'POST') {
    lic_locked(function () use ($body) {
        [$h, $lic] = lic_by_id((string)($body['id'] ?? ''));
        $plans = lic_settings()['plans'];
        $note = (string)($body['note'] ?? '');
        if (!empty($body['plan']) && $body['plan'] !== $lic['plan']) {
            if (!isset($plans[$body['plan']])) { refuse(400, 'choose one of your plans'); }
            $up = lic_rank((string)$body['plan']) > lic_rank((string)$lic['plan']);
            $lic['plan'] = (string)$body['plan'];
            $lic['plan_since'] = time();
            lic_history($lic, $up ? 'upgraded' : 'downgraded', $note);
        }
        if (isset($body['variant'])) { $lic['variant'] = $body['variant'] === 'ai' ? 'ai' : 'own'; }
        if (!empty($body['restart'])) {
            $len = (int)($lic['period_end'] ?? 0) - (int)($lic['period_start'] ?? 0);
            $lic['period_start'] = max(time(), (int)($lic['period_start'] ?? 0) + 1);   // a new month's own count
            $lic['period_end'] = $lic['period_start'] + ($len >= 86400 ? $len : 30 * 86400);
            lic_history($lic, 'new_period', $note);
        }
        if (!empty($body['extend_days'])) {
            $from = max(time(), (int)($lic['period_end'] ?? 0));
            $lic['period_end'] = $from + max(1, min(3660, (int)$body['extend_days'])) * 86400;
            if (in_array($lic['status'], ['cancelled', 'expired'], true)) { $lic['status'] = 'active'; $lic['cancelled_at'] = 0; }
            lic_history($lic, 'renewed', $note);
        }
        if (!empty($body['until'])) {
            $lic['period_end'] = max(time(), (int)$body['until']);
            lic_history($lic, 'renewed', $note);
        }
        if (($body['status'] ?? '') === 'cancelled' && $lic['status'] !== 'cancelled') {
            $lic['status'] = 'cancelled';
            $lic['cancelled_at'] = time();
            lic_history($lic, 'cancelled', $note);
        } elseif (($body['status'] ?? '') === 'active' && $lic['status'] !== 'active') {
            $lic['status'] = 'active';
            $lic['cancelled_at'] = 0;
            $lic['cancel_at'] = 0;
            lic_history($lic, 'reactivated', $note);
        }
        if (isset($body['email'])) { $lic['email'] = strtolower(trim((string)$body['email'])); }
        lic_save($h, $lic);
        answer(['ok' => true, 'licence' => lic_public($lic)]);
    });
}

if ($do === 'licence-key' && $method === 'POST') {
    lic_locked(function () use ($body) {
        [$h, $lic, $ix] = lic_by_id((string)($body['id'] ?? ''));
        $key = lic_new_key();
        $nh = lic_hash($key);
        $lic['key_hint'] = substr($key, -5);
        $lic['devices'] = [];
        lic_history($lic, 'key_reissued');
        lic_save($nh, $lic);
        @unlink(lic_dir() . "/$h.php");
        foreach (['by_id', 'by_sub', 'by_customer'] as $k) {
            foreach ($ix[$k] as $kk => $vv) { if ($vv === $h) { $ix[$k][$kk] = $nh; } }
        }
        lic_save_index($ix);
        answer(['ok' => true, 'key' => $key, 'licence' => lic_public($lic)]);
    });
}

if ($do === 'licence-unbind' && $method === 'POST') {
    lic_locked(function () use ($body) {
        [$h, $lic] = lic_by_id((string)($body['id'] ?? ''));
        $lic['devices'] = [];
        $lic['moves'] = [];
        if (!empty($body['websites'])) { $lic['sites'] = []; }
        lic_history($lic, 'computers_cleared', !empty($body['websites']) ? 'and websites' : '');
        lic_save($h, $lic);
        answer(['ok' => true, 'licence' => lic_public($lic)]);
    });
}

refuse(404, 'no such licence request');
