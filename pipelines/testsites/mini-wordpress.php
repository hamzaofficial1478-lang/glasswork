<?php
/*
 * mini-wordpress.php — a stand-in WordPress for the tests (pipelines/wp_fix_tests.py).
 *
 * Not WordPress: just enough of it, served by PHP's own web server, for the
 * real Botix helper plugin to run as it would on a real site — the plugin API
 * (actions, filters, options), the REST API (the plugin's routes and the core
 * pages/posts/settings/users routes the agency uses, with Basic auth by
 * Application Password), and a theme that prints <head> through wp_head().
 * State lives in data.json beside it. Never used outside the tests.
 */
define('ABSPATH', __DIR__ . '/');
$GLOBALS['wp_file'] = __DIR__ . '/data.json';

function wp_load() {
    $fh = fopen($GLOBALS['wp_file'], 'r');
    flock($fh, LOCK_SH);
    $GLOBALS['wp_data'] = json_decode(stream_get_contents($fh), true);
    flock($fh, LOCK_UN);
    fclose($fh);
}
function wp_save() {
    file_put_contents($GLOBALS['wp_file'], json_encode($GLOBALS['wp_data'], JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES), LOCK_EX);
}
wp_load();

// ---- the plugin API ---------------------------------------------------------
$GLOBALS['wp_hooks'] = array();
function add_filter($tag, $fn, $prio = 10, $args = 1) { $GLOBALS['wp_hooks'][$tag][$prio][] = array($fn, $args); return true; }
function add_action($tag, $fn, $prio = 10, $args = 1) { return add_filter($tag, $fn, $prio, $args); }
function apply_filters($tag, $value) {
    $args = func_get_args(); array_shift($args);
    if (empty($GLOBALS['wp_hooks'][$tag])) { return $value; }
    ksort($GLOBALS['wp_hooks'][$tag]);
    foreach ($GLOBALS['wp_hooks'][$tag] as $fns) {
        foreach ($fns as $f) { $args[0] = $value; $value = call_user_func_array($f[0], array_slice($args, 0, max(1, $f[1]))); }
    }
    return $value;
}
function do_action($tag) {
    $args = func_get_args(); array_shift($args);
    if (empty($GLOBALS['wp_hooks'][$tag])) { return; }
    ksort($GLOBALS['wp_hooks'][$tag]);
    foreach ($GLOBALS['wp_hooks'][$tag] as $fns) {
        foreach ($fns as $f) { call_user_func_array($f[0], array_slice($args, 0, $f[1])); }
    }
}
function __return_true() { return true; }

// ---- options -----------------------------------------------------------------
function get_option($k, $d = false) { return array_key_exists($k, $GLOBALS['wp_data']['options']) ? $GLOBALS['wp_data']['options'][$k] : $d; }
function update_option($k, $v, $autoload = null) { wp_load(); $GLOBALS['wp_data']['options'][$k] = $v; wp_save(); return true; }
function delete_option($k) { wp_load(); unset($GLOBALS['wp_data']['options'][$k]); wp_save(); return true; }

// ---- small helpers WordPress has -------------------------------------------------
function esc_html($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function esc_attr($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function esc_url($s) { return preg_match('#^https?://#i', (string)$s) ? htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8') : ''; }
function esc_url_raw($s) { return preg_match('#^https?://#i', (string)$s) ? trim((string)$s) : ''; }
function wp_strip_all_tags($s) { return trim(strip_tags(preg_replace('@<(script|style)[^>]*?>.*?</\\1>@si', '', (string)$s))); }
function get_bloginfo($k) { return $k === 'version' ? '6.6' : 'Glass Co'; }
function get_locale() { return 'en_GB'; }
function is_admin() { return false; }
function is_singular() { return !empty($GLOBALS['wp_singular']); }
function home_url() { return 'http://' . $_SERVER['HTTP_HOST']; }
function rest_url($p = '') { return home_url() . '/wp-json/' . ltrim($p, '/'); }
function current_user_can($cap) { return !empty($GLOBALS['wp_admin']); }
class WP_Error {
    public $code; public $message; public $data;
    function __construct($c, $m, $d = array()) { $this->code = $c; $this->message = $m; $this->data = $d; }
}
function is_wp_error($x) { return $x instanceof WP_Error; }
class WP_REST_Request {
    public $json;
    function __construct($json) { $this->json = $json; }
    function get_json_params() { return $this->json; }
}
function wp_remote_get($url, $args = array()) {
    $ctx = stream_context_create(array('http' => array('timeout' => 10, 'ignore_errors' => true)));
    $body = @file_get_contents($url, false, $ctx);
    if ($body === false) { return new WP_Error('http', 'could not connect'); }
    $code = 0;
    foreach ((array)$http_response_header as $h) { if (preg_match('#^HTTP/\S+\s+(\d{3})#', $h, $m)) { $code = (int)$m[1]; } }
    return array('body' => $body, 'code' => $code);
}
function wp_remote_retrieve_response_code($r) { return $r['code']; }
function wp_remote_retrieve_body($r) { return $r['body']; }
$GLOBALS['wp_routes'] = array();
function register_rest_route($ns, $route, $args) {
    $list = isset($args['methods']) ? array($args) : $args;
    foreach ($list as $a) { $GLOBALS['wp_routes']["/$ns$route"][strtoupper($a['methods'])] = $a; }
}

// ---- who is asking (Basic auth with an Application Password) -------------------
$auth = isset($_SERVER['HTTP_AUTHORIZATION']) ? $_SERVER['HTTP_AUTHORIZATION'] : '';
if ($auth === '' && function_exists('getallheaders')) {
    foreach (getallheaders() as $k => $v) { if (strtolower($k) === 'authorization') { $auth = $v; } }
}
$want = 'Basic ' . base64_encode($GLOBALS['wp_data']['user'] . ':' . $GLOBALS['wp_data']['password']);
$GLOBALS['wp_admin'] = hash_equals($want, $auth);

// ---- the plugins ---------------------------------------------------------------
$plugin = __DIR__ . '/wp-content/plugins/botix-helper/botix-helper.php';
if (is_file($plugin)) { include $plugin; }

// ---- core's own head -------------------------------------------------------------
function wp_get_document_title() {
    $t = apply_filters('pre_get_document_title', '');
    return $t !== '' ? $t : esc_html($GLOBALS['wp_doc_title']);
}
function _wp_render_title_tag() { echo '<title>' . wp_get_document_title() . "</title>\n"; }
function wp_robots() {
    $r = array();
    if (!get_option('blog_public', 1)) { $r['noindex'] = true; $r['nofollow'] = true; }
    if (!empty($GLOBALS['wp_page_noindex'])) { $r['noindex'] = true; }
    $r = apply_filters('wp_robots', $r);
    if ($r) {
        $parts = array();
        foreach ($r as $k => $v) { $parts[] = $v === true ? $k : "$k:$v"; }
        echo '<meta name="robots" content="' . esc_attr(implode(', ', $parts)) . "\">\n";
    }
}
function rel_canonical() {
    if (!is_singular()) { return; }
    echo '<link rel="canonical" href="' . esc_url(apply_filters('get_canonical_url', $GLOBALS['wp_permalink'], null)) . "\">\n";
}
add_action('wp_head', '_wp_render_title_tag', 1);
add_action('wp_head', 'wp_robots', 1);
add_action('wp_head', 'rel_canonical', 10);
function wp_head() { do_action('wp_head'); }
function language_attributes() { echo apply_filters('language_attributes', 'lang="en-GB"'); }

function wp_json($code, $obj) {
    http_response_code($code);
    header('Content-Type: application/json; charset=UTF-8');
    echo json_encode($obj, JSON_UNESCAPED_SLASHES);
    exit;
}

// ---- the request -------------------------------------------------------------------
$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
parse_str((string)parse_url($_SERVER['REQUEST_URI'], PHP_URL_QUERY), $query);
$method = $_SERVER['REQUEST_METHOD'];
$body = json_decode((string)file_get_contents('php://input'), true);
if (!is_array($body)) { $body = array(); }
do_action('init');

if (strpos($path, '/wp-json/') === 0) {
    $route = substr($path, strlen('/wp-json'));
    do_action('rest_api_init');
    if (isset($GLOBALS['wp_routes'][$route][$method])) {
        $r = $GLOBALS['wp_routes'][$route][$method];
        if (!call_user_func($r['permission_callback'])) {
            wp_json(401, array('code' => 'rest_forbidden', 'message' => 'Sorry, you are not allowed to do that.'));
        }
        $out = call_user_func($r['callback'], new WP_REST_Request($body));
        if (is_wp_error($out)) {
            wp_json(isset($out->data['status']) ? $out->data['status'] : 500,
                    array('code' => $out->code, 'message' => $out->message, 'data' => $out->data));
        }
        wp_json(200, $out);
    }
    if (strpos($route, '/wp/v2/') === 0) {
        if (!$GLOBALS['wp_admin']) {
            wp_json(401, array('code' => 'incorrect_password', 'message' => 'The provided password is an invalid application password.'));
        }
        $r = substr($route, strlen('/wp/v2'));
        if ($r === '/users/me') {
            wp_json(200, array('id' => 1, 'name' => 'Owner', 'slug' => 'owner', 'capabilities' => array(
                'edit_pages' => true, 'publish_posts' => true, 'upload_files' => true, 'unfiltered_html' => true, 'manage_options' => true)));
        }
        if ($r === '/settings') {
            wp_json(200, array('page_on_front' => $GLOBALS['wp_data']['front'], 'show_on_front' => 'page'));
        }
        if (preg_match('#^/(pages|posts)(?:/(\d+))?(/revisions)?$#', $r, $m)) {
            $kind = $m[1];
            $view = function ($id, $p) use ($kind) {
                return array('id' => (int)$id, 'slug' => $p['slug'], 'status' => 'publish', 'modified' => '2026-10-16T10:00:00',
                             'link' => home_url() . '/' . $p['slug'] . '/', 'content' => array('raw' => $p['content'], 'rendered' => $p['content']));
            };
            $store = $GLOBALS['wp_data'][$kind];
            if (empty($m[2])) {
                $rows = array();
                foreach ($store as $id => $p) { if (empty($query['slug']) || $query['slug'] === $p['slug']) { $rows[] = $view($id, $p); } }
                wp_json(200, $rows);
            }
            $id = $m[2];
            if (!isset($store[$id])) { wp_json(404, array('code' => 'rest_post_invalid_id', 'message' => 'Invalid post ID.')); }
            if (!empty($m[3])) {
                $revs = array_merge(array($store[$id]['content']), array_reverse(isset($store[$id]['revs']) ? $store[$id]['revs'] : array()));
                $rows = array();
                foreach ($revs as $i => $c) { $rows[] = array('id' => 900 + $i, 'modified' => '2026-10-16T09:00:00', 'content' => array('raw' => $c)); }
                wp_json(200, $rows);
            }
            if ($method === 'POST') {
                wp_load();
                $GLOBALS['wp_data'][$kind][$id]['revs'][] = $GLOBALS['wp_data'][$kind][$id]['content'];
                $GLOBALS['wp_data'][$kind][$id]['content'] = isset($body['content']) ? $body['content'] : $GLOBALS['wp_data'][$kind][$id]['content'];
                wp_save();
                wp_json(200, $view($id, $GLOBALS['wp_data'][$kind][$id]));
            }
            wp_json(200, $view($id, $store[$id]));
        }
    }
    wp_json(404, array('code' => 'rest_no_route', 'message' => 'No route was found matching the URL and request method.'));
}

if ($path === '/robots.txt') {
    header('Content-Type: text/plain; charset=utf-8');
    echo apply_filters('robots_txt', "User-agent: *\nDisallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php\n", get_option('blog_public', 1));
    exit;
}
if ($path === '/wp-sitemap.xml') {
    header('Content-Type: application/xml; charset=utf-8');
    echo '<?xml version="1.0" encoding="UTF-8"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
       . '<sitemap><loc>' . home_url() . '/wp-sitemap-posts-page-1.xml</loc></sitemap></sitemapindex>';
    exit;
}

// ---- a page or post ----------------------------------------------------------------
$slug = trim($path, '/');
$found = null;
if ($slug === '') {
    $found = $GLOBALS['wp_data']['pages'][$GLOBALS['wp_data']['front']];
} else {
    foreach (array('pages', 'posts') as $kind) {
        foreach ($GLOBALS['wp_data'][$kind] as $p) { if ($p['slug'] === $slug) { $found = $p; } }
    }
}
if ($found === null) {
    http_response_code(404);
    echo '<!doctype html><html><head><title>Not found</title></head><body><p>Nothing here.</p></body></html>';
    exit;
}
$GLOBALS['wp_singular'] = true;
$GLOBALS['wp_permalink'] = home_url() . ($slug === '' ? '/' : '/' . $slug . '/');
$GLOBALS['wp_doc_title'] = $found['title'] . ' – Glass Co';
$GLOBALS['wp_page_noindex'] = !empty($found['noindex']);
header('Content-Type: text/html; charset=UTF-8');
?><!doctype html>
<html <?php language_attributes(); ?>>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<?php wp_head(); ?>
</head>
<body>
<header class="site-header"><p class="site-title"><a href="/">Glass Co</a></p>
<nav><a href="/">Home</a> <a href="/about/">About us</a> <a href="/services/">What we do</a> <a href="/doors/">Aluminium doors</a></nav></header>
<main><article><?php echo $found['content']; ?></article></main>
<footer><p>&copy; 2019 Glass Co. Dubai.</p></footer>
</body>
</html>
