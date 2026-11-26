"""Prepare a travel-only maintenance candidate; never install or reload it."""
import argparse
import difflib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.nginx_merge import all_nodes, location, managed, one, parse, replace, section, sha, signature

MESSAGE = '旅行服务正在维护，请稍后重试。'
BODY = '''location = /travel {
    recursive_error_pages off;
    error_page 503 =503 /travel/__maintenance_response;
    return 503;
}
location ^~ /travel/ {
    recursive_error_pages off;
    error_page 503 =503 /travel/__maintenance_response;
    return 503;
}
location = /travel/__maintenance_response {
    internal;
    recursive_error_pages off;
    default_type text/plain;
    charset utf-8;
    add_header Cache-Control "no-store" always;
    add_header Retry-After "60" always;
    return 503 "''' + MESSAGE + '''";
}'''


def render(source):
    nodes = parse(source)
    if any(n.head[0] == 'include' and n.head not in (('include', 'mime.types'), ('include', '/etc/nginx/mime.types')) for n in all_nodes(nodes)):
        raise ValueError('Unreviewed include in shared configuration')
    http = one(nodes, 'http')
    servers = [n for n in http.children if n.head == ('server',) and any(location(c) for c in n.children or ())]
    if len(servers) != 1:
        raise ValueError('Expected one unambiguous travel server')
    server = servers[0]
    selected = [n for n in server.children if location(n)]
    remaining = signature(nodes, {n.start for n in selected})
    edits = section(source, 'SERVER', server, selected)
    edits.append((server.body_start, server.body_start, managed(BODY, 'SERVER')))
    candidate = replace(source, edits)
    changed = parse(candidate)
    target = one(changed, 'http')
    new_servers = [n for n in target.children if n.head == ('server',) and any(location(c) for c in n.children or ())]
    if len(new_servers) != 1:
        raise ValueError('Maintenance server identity changed')
    new_locations = [n for n in new_servers[0].children if location(n)]
    after = signature(changed, {n.start for n in new_locations})
    if remaining != after or signature(new_locations) != signature(parse(BODY)):
        raise ValueError('Unexpected change outside travel maintenance locations')
    return candidate, {'unrelated_directives_unchanged': True,
                       'unrelated_directives_sha256': sha(json.dumps(remaining, ensure_ascii=False)),
                       'maintenance_status': 503, 'retry_after_seconds': 60,
                       'production_deployed': False, 'syntax_tested': False}


def prepare(source_file, output):
    source_file, output = (Path(p).absolute() for p in (source_file, output))
    if any(p != p.resolve() for p in (source_file, output)) or source_file.is_relative_to(output):
        raise ValueError('Use canonical, separate source and output paths')
    source = source_file.read_bytes().decode('utf-8')
    candidate, report = render(source)
    if render(candidate)[0] != candidate:
        raise ValueError('Maintenance transformation is not idempotent')
    report.update({'source_sha256': sha(source), 'candidate_sha256': sha(candidate), 'idempotent': True})
    output.mkdir(mode=0o700)
    diff = ''.join(difflib.unified_diff(source.splitlines(keepends=True), candidate.splitlines(keepends=True),
                                        fromfile='before.conf', tofile='maintenance.conf'))
    for name, content in (('nginx-maintenance.conf', candidate), ('nginx.diff', diff),
                          ('report.json', json.dumps(report, ensure_ascii=False, indent=2))):
        fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            handle.write(content)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-file', required=True)
    parser.add_argument('--output-dir', required=True)
    args = parser.parse_args()
    try:
        report = prepare(args.source_file, args.output_dir)
    except (OSError, ValueError, IndexError) as error:
        parser.exit(1, 'Maintenance candidate refused: ' + str(error) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
