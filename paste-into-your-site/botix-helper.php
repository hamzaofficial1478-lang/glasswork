<?php
/**
 * Plugin Name: Botix helper
 * Plugin URI: https://techduo.ae/botix
 * Description: Lets Botix put each page's own title, description, address and preview card right, add your sitemap to robots.txt, publish llms.txt — and keeps itself up to date. It changes nothing on its own: only Botix, signed in with your Application Password, can ask it to.
 * Version: 1
 * Requires at least: 5.7
 * Requires PHP: 7.0
 * Author: TECH DUO
 * License: Proprietary
 *
 * WHY THIS EXISTS
 *
 * A WordPress site's page content can be changed through WordPress's own
 * REST API, which is how Botix edits pages and posts. The <head> of a page —
 * its title, the description Google shows, the canonical address, the preview
 * card, the "do not list me" line — is written by the theme and the SEO
 * plugin, and WordPress gives no way into it from outside. This plugin is
 * that way in, and nothing more:
 *
 *   botix/v1/ping          public: is the helper here, and which version
 *   botix/v1/health        what it can do here (admin only)
 *   botix/v1/meta          each page's own values, by address; set them (admin only)
 *   botix/v1/meta-undo     put the values back as they were before the last change
 *   botix/v1/settings      WordPress's "discourage search engines" box (admin only)
 *   botix/v1/self-update   take a newer copy of this same plugin (admin only)
 *
 * The values are shown through the SEO plugin you already use (Yoast, Rank
 * Math, All in One SEO, SEOPress) when there is one, so nothing is printed
 * twice; with none, the helper prints the tags itself.
 *
 * Remove it at any time from Plugins: the values it kept stay in one option
 * (botix_meta) and simply stop being used.
 */

if (!defined('ABSPATH')) {
    exit;
}

const BOTIX_HELPER = 1;
const BOTIX_NEEDS_PHP = '7.0';
const BOTIX_PAGE_KEYS = array('title', 'description', 'canonical', 'robots', 'og:title', 'og:description',
                              'og:image', 'og:url', 'twitter:card');
const BOTIX_SITE_KEYS = array('viewport', 'lang', 'robots_extra', 'llms');
const BOTIX_UPDATE_MAX = 524288;

// ---------------------------------------------------------------------------
// the page being shown, and its values
// ---------------------------------------------------------------------------

function botix_clean_path($path) {
    $p = parse_url((string)$path, PHP_URL_PATH);
    $p = rawurldecode($p ? $p : '/');
    $p = preg_replace('#/index\.php$#i', '/', $p);
    return ($p === '/' || $p === '') ? '/' : rtrim($p, '/');
}

function botix_path() {
    return botix_clean_path(isset($_SERVER['REQUEST_URI']) ? $_SERVER['REQUEST_URI'] : '/');
}

function botix_mine($key) {
    static $mine = null;
    if ($mine === null) {
        $all = get_option('botix_meta', array());
        $p = botix_path();
        $mine = (is_array($all) && isset($all[$p]) && is_array($all[$p])) ? $all[$p] : array();
    }
    return isset($mine[$key]) ? (string)$mine[$key] : '';
}

function botix_site($key) {
    $site = get_option('botix_site', array());
    return (is_array($site) && isset($site[$key])) ? (string)$site[$key] : '';
}

function botix_seo_plugin() {
    if (defined('WPSEO_VERSION')) { return 'yoast'; }
    if (defined('RANK_MATH_VERSION') || class_exists('RankMath')) { return 'rankmath'; }
    if (defined('AIOSEO_VERSION') || function_exists('aioseo')) { return 'aioseo'; }
    if (defined('SEOPRESS_VERSION')) { return 'seopress'; }
    return '';
}

/** A value as it may be kept: plain text, an address, or one of a few words. */
function botix_clean($key, $value) {
    $v = trim(wp_strip_all_tags((string)$value));
    if ($v === '') { return ''; }
    switch ($key) {
        case 'canonical': case 'og:url': case 'og:image':
            return preg_match('#^https?://#i', $v) ? esc_url_raw($v) : '';
        case 'robots':
            return $v === 'index' ? 'index' : '';
        case 'twitter:card':
            return in_array($v, array('summary', 'summary_large_image'), true) ? $v : '';
        case 'viewport':
            return $v === '1' ? '1' : '';
        case 'lang':
            return preg_match('/^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$/', $v) ? $v : '';
        case 'robots_extra':
            // Only Sitemap lines: nothing this helper adds can shut anyone out.
            $keep = array();
            foreach (preg_split('/\r?\n/', (string)$value) as $line) {
                $line = trim($line);
                if (preg_match('#^Sitemap:\s*https?://\S+$#i', $line)) { $keep[] = $line; }
            }
            return implode("\n", $keep);
        case 'llms':
            return substr(str_replace("\r", '', wp_strip_all_tags((string)$value)), 0, 20000);
    }
    return function_exists('mb_substr') ? mb_substr($v, 0, 320) : substr($v, 0, 320);
}

// ---------------------------------------------------------------------------
// what a visitor's browser receives
// ---------------------------------------------------------------------------

// The title. Core themes (title-tag support), older themes (wp_title) and the
// SEO plugins each have their own way in; plugins escape what they are given,
// core does not, so only the core filters escape here.
add_filter('pre_get_document_title', function ($t) {
    return botix_mine('title') !== '' ? esc_html(botix_mine('title')) : $t;
}, 999);
add_filter('wp_title', function ($t) {
    return botix_mine('title') !== '' ? esc_html(botix_mine('title')) : $t;
}, 999);
foreach (array('wpseo_title', 'rank_math/frontend/title', 'aioseo_title', 'seopress_titles_title') as $hook) {
    add_filter($hook, function ($t) { return botix_mine('title') !== '' ? botix_mine('title') : $t; }, 999);
}
foreach (array('wpseo_metadesc', 'rank_math/frontend/description', 'aioseo_description', 'seopress_titles_desc') as $hook) {
    add_filter($hook, function ($d) { return botix_mine('description') !== '' ? botix_mine('description') : $d; }, 999);
}
foreach (array('wpseo_canonical', 'rank_math/frontend/canonical', 'aioseo_canonical_url', 'seopress_titles_canonical') as $hook) {
    add_filter($hook, function ($c) { return botix_mine('canonical') !== '' ? botix_mine('canonical') : $c; }, 999);
}
// Core prints the canonical on single pages and posts; it is given ours.
add_filter('get_canonical_url', function ($c) {
    return botix_mine('canonical') !== '' ? botix_mine('canonical') : $c;
}, 999);

// "Do not list me": taken off a page only when Botix was asked to (robots = index).
add_filter('wp_robots', function ($robots) {
    if (botix_mine('robots') === 'index' && is_array($robots)) {
        unset($robots['noindex'], $robots['none']);
    }
    return $robots;
}, 999);
add_filter('wpseo_robots', function ($r) {
    return botix_mine('robots') === 'index' ? preg_replace('/\bnoindex\b/i', 'index', (string)$r) : $r;
}, 999);
foreach (array('rank_math/frontend/robots', 'aioseo_robots_meta') as $hook) {
    add_filter($hook, function ($r) {
        if (botix_mine('robots') === 'index' && is_array($r)) {
            unset($r['noindex']);
            $r['index'] = 'index';
        }
        return $r;
    }, 999);
}

// The page's language, when the theme prints <html> through language_attributes().
add_filter('language_attributes', function ($out) {
    $lang = botix_site('lang');
    if ($lang !== '' && stripos((string)$out, 'lang=') === false) {
        $out = trim($out . ' lang="' . esc_attr($lang) . '"');
    }
    return $out;
}, 999);

add_action('wp_head', function () {
    if (botix_site('viewport') === '1') {
        echo '<meta name="viewport" content="width=device-width, initial-scale=1">' . "\n";
    }
}, 0);

add_action('wp_head', function () {
    $seo = botix_seo_plugin();
    $out = array();
    if ($seo === '' && botix_mine('description') !== '') {
        $out[] = '<meta name="description" content="' . esc_attr(botix_mine('description')) . '">';
    }
    // Core prints a canonical on single pages and posts (given ours above);
    // anywhere else, with no SEO plugin, the helper prints it.
    if ($seo === '' && botix_mine('canonical') !== '' && !(function_exists('is_singular') && is_singular())) {
        $out[] = '<link rel="canonical" href="' . esc_url(botix_mine('canonical')) . '">';
    }
    foreach (array('og:title', 'og:description', 'og:image', 'og:url') as $k) {
        if (botix_mine($k) !== '') {
            $v = ($k === 'og:image' || $k === 'og:url') ? esc_url(botix_mine($k)) : esc_attr(botix_mine($k));
            $out[] = '<meta property="' . $k . '" content="' . $v . '">';
        }
    }
    if (botix_mine('twitter:card') !== '') {
        $out[] = '<meta name="twitter:card" content="' . esc_attr(botix_mine('twitter:card')) . '">';
    }
    if ($out) {
        echo "<!-- Botix helper -->\n" . implode("\n", $out) . "\n";
    }
}, 1);

// robots.txt: WordPress makes it up when there is no file; Sitemap lines are added.
add_filter('robots_txt', function ($output, $public) {
    $extra = botix_site('robots_extra');
    if ($extra === '') { return $output; }
    foreach (preg_split('/\n/', $extra) as $line) {
        if ($line !== '' && stripos((string)$output, $line) === false) {
            $output = rtrim((string)$output) . "\n" . $line . "\n";
        }
    }
    return $output;
}, 999, 2);

// llms.txt: a short plain summary for AI assistants, at the top of the site.
add_action('init', function () {
    if (botix_path() !== '/llms.txt') { return; }
    $text = botix_site('llms');
    if ($text === '') { return; }
    if (!headers_sent()) {
        header('Content-Type: text/plain; charset=utf-8');
        header('X-Robots-Tag: noindex');
    }
    echo $text;
    exit;
}, 0);

// ---------------------------------------------------------------------------
// what Botix may ask, signed in as an administrator
// ---------------------------------------------------------------------------

function botix_admin() {
    return current_user_can('manage_options');
}

function botix_update_problem() {
    if (version_compare(PHP_VERSION, '7.0', '<') || !function_exists('token_get_all')) {
        return 'your host has PHP\'s tokenizer switched off, so a new copy cannot be checked before it goes in';
    }
    if (!is_writable(__FILE__) || !is_writable(dirname(__FILE__))) {
        return 'the plugin folder is not writable, so the helper cannot replace itself';
    }
    return '';
}

function botix_health() {
    return array('ok' => true, 'helper' => BOTIX_HELPER, 'php' => PHP_VERSION,
                 'wp' => get_bloginfo('version'), 'seo_plugin' => botix_seo_plugin(),
                 'blog_public' => (int)get_option('blog_public', 1), 'locale' => get_locale(),
                 'pages' => count((array)get_option('botix_meta', array())),
                 'can' => array('meta', 'settings', 'robots', 'llms', 'self-update'),
                 'self_update' => botix_update_problem());
}

function botix_meta_get() {
    return array('ok' => true, 'helper' => BOTIX_HELPER, 'meta' => (array)get_option('botix_meta', array()),
                 'site' => (array)get_option('botix_site', array()));
}

function botix_meta_set($request) {
    $body = (array)$request->get_json_params();
    $all = (array)get_option('botix_meta', array());
    $site = (array)get_option('botix_site', array());
    update_option('botix_meta_prev', array('meta' => $all, 'site' => $site, 'at' => time()), false);
    $changed = 0;
    foreach ((array)(isset($body['pages']) ? $body['pages'] : array()) as $path => $values) {
        $path = botix_clean_path($path);
        foreach ((array)$values as $key => $value) {
            if (!in_array($key, BOTIX_PAGE_KEYS, true)) { continue; }
            $v = botix_clean($key, $value);
            if ($v === '') {
                unset($all[$path][$key]);
            } else {
                $all[$path][$key] = $v;
            }
            $changed++;
        }
        if (empty($all[$path])) { unset($all[$path]); }
    }
    foreach ((array)(isset($body['site']) ? $body['site'] : array()) as $key => $value) {
        if (!in_array($key, BOTIX_SITE_KEYS, true)) { continue; }
        $v = botix_clean($key, $value);
        if ($v === '') { unset($site[$key]); } else { $site[$key] = $v; }
        $changed++;
    }
    update_option('botix_meta', $all, true);
    update_option('botix_site', $site, true);
    return array('ok' => true, 'changed' => $changed, 'meta' => $all, 'site' => $site);
}

function botix_meta_undo() {
    $prev = get_option('botix_meta_prev', null);
    if (!is_array($prev) || !isset($prev['meta'])) {
        return new WP_Error('botix_nothing', 'there is no earlier set of values to put back', array('status' => 404));
    }
    update_option('botix_meta', (array)$prev['meta'], true);
    update_option('botix_site', (array)$prev['site'], true);
    delete_option('botix_meta_prev');
    return array('ok' => true, 'restored' => (int)$prev['at']);
}

function botix_settings($request) {
    $body = (array)$request->get_json_params();
    $was = array('blog_public' => (int)get_option('blog_public', 1));
    if (array_key_exists('blog_public', $body)) {
        update_option('blog_public', ((int)$body['blog_public']) ? 1 : 0);
    }
    return array('ok' => true, 'was' => $was, 'blog_public' => (int)get_option('blog_public', 1));
}

/** Ask this website for ping, as a visitor would: 'new', 'old', 'broken' or 'unknown'. */
function botix_ask_self($want) {
    if (!function_exists('wp_remote_get')) { return 'unknown'; }
    $r = wp_remote_get(rest_url('botix/v1/ping') . '?n=' . mt_rand(), array('timeout' => 10));
    if (is_wp_error($r)) { return 'unknown'; }
    $code = (int)wp_remote_retrieve_response_code($r);
    $j = json_decode((string)wp_remote_retrieve_body($r), true);
    if (!is_array($j)) { return $code >= 500 ? 'broken' : 'unknown'; }
    if (empty($j['ok'])) { return 'broken'; }
    $v = (int)(isset($j['helper']) ? $j['helper'] : 0);
    return $v === $want ? 'new' : ($v === BOTIX_HELPER ? 'old' : 'broken');
}

function botix_put_in_place($code) {
    $tmp = dirname(__FILE__) . '/.botix-' . bin2hex(random_bytes(4)) . '.tmp';
    if (@file_put_contents($tmp, $code, LOCK_EX) !== strlen($code)) { @unlink($tmp); return false; }
    if (!@rename($tmp, __FILE__)) { @unlink($tmp); return false; }
    if (function_exists('opcache_invalidate')) { @opcache_invalidate(__FILE__, true); }
    clearstatcache(true, __FILE__);
    return true;
}

function botix_self_update($request) {
    $body = (array)$request->get_json_params();
    $why = botix_update_problem();
    if ($why !== '') {
        return new WP_Error('botix_cannot', $why . ' — upload the new helper by hand this once', array('status' => 409));
    }
    $ver = (int)(isset($body['version']) ? $body['version'] : 0);
    $code = base64_decode((string)(isset($body['code']) ? $body['code'] : ''), true);
    if (!is_string($code) || $code === '' || strlen($code) > BOTIX_UPDATE_MAX) {
        return new WP_Error('botix_bad', 'the new copy did not arrive whole — nothing was changed', array('status' => 400));
    }
    $sha = strtolower((string)(isset($body['sha256']) ? $body['sha256'] : ''));
    if (!hash_equals(hash('sha256', $code), $sha)) {
        return new WP_Error('botix_bad', 'the new copy was damaged on the way (its fingerprint does not match) — nothing was changed',
                            array('status' => 400));
    }
    if ($ver <= BOTIX_HELPER) {
        return array('ok' => true, 'unchanged' => true, 'helper' => BOTIX_HELPER,
                     'head' => 'the helper is already version ' . BOTIX_HELPER);
    }
    if (strncmp($code, '<?php', 5) !== 0 || strpos(substr($code, 0, 300), 'Plugin Name: Botix helper') === false
            || !preg_match('/^const BOTIX_HELPER = (\d+);/m', $code, $m) || (int)$m[1] !== $ver) {
        return new WP_Error('botix_bad', 'that is not a newer copy of this helper — nothing was changed', array('status' => 400));
    }
    if (preg_match("/^const BOTIX_NEEDS_PHP = '([0-9.]+)';/m", $code, $need) && version_compare(PHP_VERSION, $need[1], '<')) {
        return new WP_Error('botix_php', 'the new helper needs PHP ' . $need[1] . ' and your website runs PHP ' . PHP_VERSION
                            . '. Ask your host to raise the PHP version — nothing was changed', array('status' => 409));
    }
    try {
        token_get_all($code, TOKEN_PARSE);
    } catch (\Throwable $e) {
        return new WP_Error('botix_bad', 'the new copy would not run on your website (' . substr($e->getMessage(), 0, 160)
                            . ') — nothing was changed', array('status' => 400));
    }
    $mine = (string)file_get_contents(__FILE__);
    $kept = dirname(__FILE__) . '/botix-helper-v' . BOTIX_HELPER . '.php.bak';
    if (@file_put_contents($kept, $mine, LOCK_EX) !== strlen($mine)) {
        return new WP_Error('botix_keep', 'the old copy could not be kept, so nothing was changed', array('status' => 500));
    }
    if (!botix_put_in_place($code)) {
        return new WP_Error('botix_write', 'the new copy could not be written — the old one is still in place', array('status' => 500));
    }
    $seen = botix_ask_self($ver);
    if ($seen === 'broken') {
        $back = botix_put_in_place($mine);
        return new WP_Error('botix_broken', 'the new helper answered with an error, so the old one was '
                            . ($back ? 'put straight back — nothing else changed' : 'meant to be put back but could not be'),
                            array('status' => 502));
    }
    return array('ok' => true, 'from' => BOTIX_HELPER, 'to' => $ver, 'checked' => $seen, 'kept' => basename($kept),
                 'head' => 'the helper updated itself from version ' . BOTIX_HELPER . ' to ' . $ver
                         . ($seen === 'new' ? '' : ' (your website could not call itself to check, so Botix checks next)'));
}

add_action('rest_api_init', function () {
    register_rest_route('botix/v1', '/ping', array(
        'methods' => 'GET', 'permission_callback' => '__return_true',
        'callback' => function () { return array('ok' => true, 'helper' => BOTIX_HELPER); }));
    register_rest_route('botix/v1', '/health', array(
        'methods' => 'GET', 'permission_callback' => 'botix_admin', 'callback' => 'botix_health'));
    register_rest_route('botix/v1', '/meta', array(
        array('methods' => 'GET', 'permission_callback' => 'botix_admin', 'callback' => 'botix_meta_get'),
        array('methods' => 'POST', 'permission_callback' => 'botix_admin', 'callback' => 'botix_meta_set')));
    register_rest_route('botix/v1', '/meta-undo', array(
        'methods' => 'POST', 'permission_callback' => 'botix_admin', 'callback' => 'botix_meta_undo'));
    register_rest_route('botix/v1', '/settings', array(
        'methods' => 'POST', 'permission_callback' => 'botix_admin', 'callback' => 'botix_settings'));
    register_rest_route('botix/v1', '/self-update', array(
        'methods' => 'POST', 'permission_callback' => 'botix_admin', 'callback' => 'botix_self_update'));
});
