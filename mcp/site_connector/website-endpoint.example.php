<?php
/**
 * SUPERSEDED — DO NOT DEPLOY THIS FILE.
 *
 * The connector that is actually maintained, tested, and running on the
 * owner's site is:
 *
 *     paste-into-your-site/agency-api.php
 *
 * Use that one. Copy it to <your website folder>/agency-api/index.php and put
 * your token in agency-api/token.php — the instructions are in its header.
 *
 * WHY THIS STUB EXISTS RATHER THAN THE OLD FILE
 *
 * Two connector files sat in this repo, and the other one is four months of
 * fixes ahead: ?do= routing instead of a URL path Apache 404s on, the
 * X-Agency-Token fallback for hosts that strip Authorization, token.php for
 * shared hosting with no environment variables, a real walk of the site
 * folder, and the /head, /backups, /rollback and /deploy endpoints
 * pipelines/site_api.py actually calls.
 *
 * This copy had none of that, and it cost real time: asked why nothing the
 * agency did reached the website, I read THIS file, reproduced its failures
 * against a live PHP server, and reported the connector as broken — while the
 * owner was running the good one and had a passing /health check in front of
 * him proving it. The two files were the whole error. A stale duplicate of a
 * setup file does not sit quietly; it gets found and believed.
 *
 * So it is deliberately not a working endpoint any more. If you deploy it, it
 * refuses and tells you where the real one is, which is the one job left worth
 * doing here.
 */

header('Content-Type: application/json; charset=utf-8');
http_response_code(410);
echo json_encode([
    'ok'    => false,
    'error' => 'This is not the agency connector. Deploy paste-into-your-site/'
             . 'agency-api.php instead — copy it to agency-api/index.php on '
             . 'your site and put your token in agency-api/token.php.',
], JSON_UNESCAPED_SLASHES);
