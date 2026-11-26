"""Prepare travel-only HTTPS review files from the exact approved HTTP candidate."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.nginx_merge import all_nodes, location, managed, one, parse, replace, section, sha, signature, zone
from tools.travel_maintenance import BODY


ROUTES = {('^~', '/travel/api/plan/stream'), ('^~', '/travel/api/'), ('^~', '/travel/assets/'),
          ('=', '/travel/index.html'), ('^~', '/travel/.vite/'), ('^~', '/travel/.travel-deploy/'),
          ('^~', '/travel/'), ('=', '/travel')}
CERT = '/etc/nginx/travel-tls/fullchain.pem'
KEY = '/etc/nginx/travel-tls/privkey.pem'
ACME = '''location ^~ /.well-known/acme-challenge/ {
    if ($request_method !~ ^(GET|HEAD)$) { return 405; }
    if ($uri !~ ^/\\.well-known/acme-challenge/[A-Za-z0-9_-]+$) { return 404; }
    root /var/lib/travel-acme;
    default_type text/plain;
    add_header Cache-Control "no-store" always;
    try_files $uri =404;
}'''


def identity(host, mode):
    if mode == 'ip':
        address = ipaddress.IPv4Address(host)
        if not address.is_global or str(address) != host:
            raise ValueError('Use a canonical public IPv4 for the IP certificate profile')
    elif mode == 'dns':
        labels = host.split('.')
        if (len(host) > 253 or len(labels) < 2 or not any(c.isalpha() for c in labels[-1]) or
            any(not re.fullmatch('[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)):
            raise ValueError('Use a lowercase, exact DNS name, without a scheme, port or wildcard')
    else:
        raise ValueError('Unknown endpoint profile')
    return host


def layout(source, *, expect_normal=True):
    nodes = parse(source)
    http = one(nodes, 'http')
    servers = [n for n in http.children if n.head == ('server',)]
    listening = [n.head for n in all_nodes(nodes) if n.head[0] == 'listen']
    if (len(servers) != 1 or listening != [('listen', '80')] or
        any(n.head[0].startswith('ssl_') or (n.head[0] == 'include' and n.head not in
            (('include', 'mime.types'), ('include', '/etc/nginx/mime.types'))) for n in all_nodes(nodes))):
        raise ValueError('Review the existing listeners, TLS or included configuration before rendering')
    server = servers[0]
    routes = [n for n in server.children if location(n)]
    if expect_normal and (len(routes) != len(ROUTES) or {n.head[1:] for n in routes} != ROUTES):
        raise ValueError('Expected the reviewed travel routes')
    return nodes, http, server, routes


def public_challenge(existing_mount):
    # The shared public tree may carry ACME response tokens only. The ACME
    # client state, certificate and key are never placed under this root.
    return ACME.replace('root /var/lib/travel-acme;', 'root /usr/share/nginx/html;') if existing_mount else ACME


def plain_locations(host, server, acme_webroot, *, existing_mount=False):
    if acme_webroot and any(n.head[0] == 'location' and any('.well-known' in arg or 'acme-challenge' in arg for arg in n.head[1:])
                           for n in server.children):
        raise ValueError('Review the existing shared challenge location first')
    plain = '\n'.join(f'''location {match} {{
    add_header Cache-Control "no-store" always;
    return 403;
}}''' for match in ('= /travel/api', '^~ /travel/api/'))
    plain += '\n' + '\n'.join(f'''location {match} {{
    add_header Cache-Control "no-store" always;
    if ($request_method !~ ^(GET|HEAD)$) {{ return 403; }}
    return 308 https://{host}$request_uri;
}}''' for match in ('= /travel', '^~ /travel/'))
    return plain + ('\n' + public_challenge(existing_mount) if acme_webroot else '')


def ssl_servers(source, host, mode, routes, maintenance):
    public_routes = '\n'.join(source[n.start:n.end] if n.head != ('location', '=', '/travel') else
                              'location = /travel { return 308 /travel/$is_args$args; }' for n in routes)
    tls = f'''server {{
    listen 443 ssl{' default_server' if mode == 'ip' else ''};
    server_name {host};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_certificate {CERT};
    ssl_certificate_key {KEY};
    if ($http_host = "") {{ return 421; }}
    if ($host != {host}) {{ return 421; }}
    absolute_redirect off;
    root /usr/share/nginx/html;
    index index.html;
    location / {{ return 404; }}
    {BODY if maintenance else public_routes}
}}'''
    if mode == 'dns':
        tls = '''server {
    listen 443 ssl default_server;
    server_name _;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_reject_handshake on;
    return 421;
}
''' + tls
    return tls


def render(source, host, mode, *, maintenance=False, acme_webroot=False, existing_mount=False):
    host = identity(host, mode)
    nodes, http, server, routes = layout(source)
    plain = plain_locations(host, server, acme_webroot, existing_mount=existing_mount)
    tls = ssl_servers(source, host, mode, routes, maintenance)
    edits = section(source, 'SERVER', server, routes)
    edits.extend([(server.body_start, server.body_start, managed(plain, 'SERVER')),
                  (http.end-1, http.end-1, '\n' + tls + '\n')])
    final = replace(source, edits)
    after = parse(final)
    new_http = one(after, 'http')
    new_servers = [n for n in new_http.children if n.head == ('server',)]
    additions = [n for n in new_servers[0].children if location(n) or (acme_webroot and n.head == ('location', '^~', '/.well-known/acme-challenge/'))]
    exclude = {n.start for n in [*new_servers[1:], *additions]}
    if signature(nodes, {n.start for n in routes}) != signature(after, exclude):
        raise ValueError('Unrelated HTTP directives changed')
    return final


def standalone_edge(source, host, mode, *, maintenance=False):
    """An independent 443-only nginx; no site other than travel is copied."""
    host = identity(host, mode)
    _, http, _, routes = layout(source)
    controls = [n for n in http.children if n.head[0] == 'map_hash_bucket_size'
                or n.head[0] == 'map' and n.head[-1].startswith('$travel_')
                or n.head[0] == 'limit_req_zone' and zone(n).startswith('travel_')]
    if not controls or not any(n.head[0] == 'limit_req_zone' for n in controls):
        raise ValueError('Missing reviewed travel rate controls')
    control_text = '\n'.join(source[n.start:n.end] for n in controls)
    return ('worker_processes 1;\npid /tmp/travel-nginx.pid;\nerror_log stderr warn;\nevents { worker_connections 256; }\nhttp {\n'
            ' include /etc/nginx/mime.types;\n default_type application/octet-stream;\n'
            ' access_log off;\n client_body_temp_path /tmp/travel-body;\n proxy_temp_path /tmp/travel-proxy;\n'
            ' fastcgi_temp_path /tmp/travel-fastcgi;\n uwsgi_temp_path /tmp/travel-uwsgi;\n scgi_temp_path /tmp/travel-scgi;\n'
            + control_text + '\n' + ssl_servers(source, host, mode, routes, maintenance) + '\n}\n')


def cleartext_companion(original, host, mode, *, acme_webroot=False, existing_mount=False):
    """Change only the pre-existing shared HTTP travel locations after TLS is ready."""
    host = identity(host, mode)
    nodes, _, server, routes = layout(original, expect_normal=False)
    replacements = plain_locations(host, server, acme_webroot, existing_mount=existing_mount)
    edits = section(original, 'SERVER', server, routes)
    edits.append((server.body_start, server.body_start, managed(replacements, 'SERVER')))
    result = replace(original, edits)
    after, _, _, selected = layout(result, expect_normal=False)
    challenge = {n.start for n in all_nodes(after) if n.head == ('location', '^~', '/.well-known/acme-challenge/')}
    if signature(nodes, {n.start for n in routes}) != signature(after, {n.start for n in selected} | (challenge if acme_webroot else set())):
        raise ValueError('Unrelated shared HTTP directives changed')
    return result


def challenge_only(source, *, existing_mount=False):
    _, _, server, _ = layout(source, expect_normal=False)
    if any(n.head[0] == 'location' and any('.well-known' in arg or 'acme-challenge' in arg for arg in n.head[1:])
           for n in server.children):
        raise ValueError('Review the existing shared challenge location first')
    return replace(source, [(server.end-1, server.end-1, '\n' + public_challenge(existing_mount) + '\n')])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--mode', choices=('dns', 'ip'), required=True)
    parser.add_argument('--acme-webroot', action='store_true')
    parser.add_argument('--acme-existing-webroot', action='store_true', help='Use the existing mounted public root for ACME tokens only')
    parser.add_argument('--standalone-edge', action='store_true', help='Generate a 443-only proxy and separate shared-HTTP companion')
    parser.add_argument('--challenge-source', type=Path, help='Original live HTTP bytes, not the release HTTP candidate')
    parser.add_argument('--challenge-sha256')
    args = parser.parse_args()
    if args.acme_existing_webroot and not args.acme_webroot:
        parser.error('--acme-existing-webroot requires --acme-webroot')
    try:
        source = args.source.read_bytes().decode('utf-8')
        if sha(source) != args.expected_sha256:
            raise ValueError('Shared source hash changed')
        if args.standalone_edge:
            paths = {'nginx-edge.conf': standalone_edge(source, args.host, args.mode),
                     'nginx-edge-maintenance.conf': standalone_edge(source, args.host, args.mode, maintenance=True)}
        else:
            paths = {'nginx-https.conf': render(source, args.host, args.mode, acme_webroot=args.acme_webroot, existing_mount=args.acme_existing_webroot),
                     'nginx-https-maintenance.conf': render(source, args.host, args.mode, maintenance=True, acme_webroot=args.acme_webroot,
                                                           existing_mount=args.acme_existing_webroot)}
        if args.acme_webroot or args.standalone_edge:
            if not args.challenge_source or not args.challenge_sha256 or args.challenge_sha256 == args.expected_sha256:
                raise ValueError('Use independently reviewed original HTTP bytes for shared port-80 changes')
            bootstrap = args.challenge_source.read_bytes().decode('utf-8')
            if sha(bootstrap) != args.challenge_sha256:
                raise ValueError('Original HTTP source hash changed')
            if args.acme_webroot:
                paths['nginx-challenge-only.conf'] = challenge_only(bootstrap, existing_mount=args.acme_existing_webroot)
            if args.standalone_edge:
                paths['nginx-http-after-tls.conf'] = cleartext_companion(bootstrap, args.host, args.mode, acme_webroot=args.acme_webroot,
                                                                       existing_mount=args.acme_existing_webroot)
        elif args.challenge_source or args.challenge_sha256:
            raise ValueError('Challenge bootstrap requires --acme-webroot')
        proof = {'source_sha256': sha(source), 'host': args.host, 'mode': args.mode,
                 'challenge_source_sha256': args.challenge_sha256,
                 'files': {name: sha(content) for name, content in paths.items()}, 'production_ready': False}
        if args.acme_existing_webroot:
            proof['acme_container_root'] = '/usr/share/nginx/html'
        paths['report.json'] = json.dumps(proof, indent=2) + '\n'
        args.output.mkdir(mode=0o700)
        for name, content in paths.items():
            fd = os.open(args.output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as file:
                file.write(content.encode('utf-8'))
    except (ValueError, OSError) as error:
        parser.exit(1, f'HTTPS candidate refused: {error}\n')
    print(json.dumps(proof))


if __name__ == '__main__':
    main()
