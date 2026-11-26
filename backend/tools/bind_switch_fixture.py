"""Render a loopback-only nginx fixture for disposable-container reload tests."""
import ipaddress
import re
from urllib.parse import urlsplit

from tools.nginx_merge import all_nodes, one, parse, replace
from tools.proxy_chain_fixture import render_fixture


def render_bind_fixture(source, memory_root, web_port=28080, backend_port=28081):
    if web_port == backend_port:
        raise ValueError('Fixture listeners must differ')
    fixture = render_fixture(source, memory_root, '127.0.0.1', web_port, '127.0.0.1', backend_port)
    nodes = parse(fixture)
    masters = [n for n in nodes if n.head[0] == 'master_process']
    if len(masters) != 1:
        raise ValueError('Expected one private process-control directive')
    edits = [(masters[0].start, masters[0].end, 'master_process on;')]
    for node in all_nodes(nodes):
        if node.head == ('root', memory_root + '/html'):
            edits.append((node.start, node.end, 'root /usr/share/nginx/html;'))
    http = one(nodes, 'http')
    edits.append((http.body_start, http.body_start,
                  '\n server { listen 127.0.0.1:' + str(backend_port) + '; '
                  'location / { default_type text/plain; return 202 "SYNTHETIC-BACKEND"; } }\n'))
    return replace(fixture, edits)


def required_hosts(source):
    """Map only literal DNS upstreams to loopback in the network-none fixture."""
    nodes = parse(source)
    groups = {n.head[1] for n in all_nodes(nodes) if n.head[0] == 'upstream'}
    hosts = set()
    for node in all_nodes(nodes):
        value = None
        if node.head[0] == 'proxy_pass':
            value = node.head[1]
        elif node.head[0] == 'upstream':
            for child in node.children or ():
                if child.head[0] == 'server':
                    host = urlsplit('http://' + child.head[1]).hostname
                    if host:
                        hosts.add(host)
        if value:
            host = urlsplit(value).hostname
            if host and host not in groups:
                hosts.add(host)
    result = []
    for host in sorted(hosts):
        try:
            ipaddress.ip_address(host)
            continue
        except ValueError:
            pass
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', host):
            raise ValueError('Dynamic or unreviewed upstream hostname')
        result.append(host)
    return result


def missing_entry_fixture(source):
    """Create a deliberate travel-entry failure for the isolated HTTP gate."""
    entries = [n for n in all_nodes(parse(source)) if n.head == ('location', '=', '/travel/index.html')]
    if len(entries) != 1:
        raise ValueError('Expected one explicit travel entry')
    roots = [n for n in entries[0].children if n.head[0] == 'root']
    if len(roots) != 1 or roots[0].head[1] != '/usr/share/nginx/html':
        raise ValueError('Expected the disposable fixture static root')
    root = roots[0]
    return replace(source, [(root.start, root.end, 'root /usr/share/nginx/html/__missing_for_runtime_gate;')])
