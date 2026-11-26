"""Render an isolated HTTP-only fixture from a reviewed shared nginx candidate."""
import ipaddress
import re

from .nginx_merge import all_nodes, location, one, parse, replace


def render_fixture(source, memory_root, nginx_ip, nginx_port, backend_ip, backend_port):
    if not re.fullmatch(r'/dev/shm/travel-proxy-chain-[0-9a-f]{32}', memory_root):
        raise ValueError('Fixture must use its own memory-directory namespace')
    for value in (nginx_ip, backend_ip):
        address = ipaddress.ip_address(value)
        if address.version != 4 or not address.is_private or address.is_unspecified:
            raise ValueError('Only explicit private IPv4 addresses are supported')
    for port in (nginx_port, backend_port):
        if not isinstance(port, int) or not 20000 <= port <= 65535:
            raise ValueError('Fixture ports must be high and outside production listeners')
    nodes = parse(source)
    http = one(nodes, 'http')
    servers = [n for n in http.children if n.head == ('server',)]
    if len(servers) != 1 or not any(location(n) for n in servers[0].children):
        raise ValueError('Fixture supports one shared travel server')
    server = servers[0]
    if any(n.head[0] in ('proxy_cache_path', 'fastcgi_cache_path', 'uwsgi_cache_path', 'scgi_cache_path') for n in all_nodes(nodes)):
        raise ValueError('Shared disk caches require a separate fixture')
    includes = [n.head for n in all_nodes(nodes) if n.head[0] == 'include']
    if any(head not in (('include', 'mime.types'), ('include', '/etc/nginx/mime.types')) for head in includes):
        raise ValueError('Unreviewed include in fixture source')
    replacements = {'worker_processes': 'worker_processes 1;', 'daemon': 'daemon off;',
                    'master_process': 'master_process off;', 'pid': f'pid {memory_root}/nginx.pid;'}
    edits, prefix = [], []
    for key, value in replacements.items():
        existing = [n for n in nodes if n.head[0] == key]
        if len(existing) > 1:
            raise ValueError('Duplicate global control directive')
        if existing:
            edits.append((existing[0].start, existing[0].end, value))
        else:
            prefix.append(value)
    for node in all_nodes(nodes):
        if node.head[0] == 'error_log':
            edits.append((node.start, node.end, f'error_log {memory_root}/error.log warn;'))
        elif node.head[0] == 'access_log':
            edits.append((node.start, node.end, 'access_log off;'))
    if not any(n.head[0] == 'error_log' for n in nodes):
        prefix.append(f'error_log {memory_root}/error.log warn;')
    for name, folder in (('client_body_temp_path', 'body'), ('proxy_temp_path', 'proxy'),
                         ('fastcgi_temp_path', 'fastcgi'), ('uwsgi_temp_path', 'uwsgi'), ('scgi_temp_path', 'scgi')):
        selected = [n for n in all_nodes(http.children) if n.head[0] == name]
        for node in selected:
            edits.append((node.start, node.end, f'{name} {memory_root}/temp/{folder};'))
        if not any(n in http.children for n in selected):
            edits.append((http.body_start, http.body_start, f'\n {name} {memory_root}/temp/{folder};\n'))
    if not any(n.head[0] == 'access_log' for n in http.children):
        edits.append((http.body_start, http.body_start, '\n access_log off;\n'))
    listens = [n for n in server.children if n.head[0] == 'listen']
    if not listens or any('ssl' in n.head or 'quic' in n.head or not (n.head[1] == '80' or n.head[1].endswith(':80')) for n in listens):
        raise ValueError('Only the reviewed plaintext port-80 server is supported')
    for index, node in enumerate(listens):
        edits.append((node.start, node.end, f'listen {nginx_ip}:{nginx_port};' if index == 0 else ''))
    static = {'/travel/', '/travel/assets/', '/travel/index.html'}
    for node in server.children:
        if not location(node):
            continue
        path = node.head[-1]
        if path in static:
            if any(n.head[0] in ('root', 'alias') for n in node.children):
                raise ValueError('Travel static root already overridden')
            edits.append((node.body_start, node.body_start, f'\n root {memory_root}/html;\n'))
        if path.startswith('/travel/api/'):
            proxies = [n for n in node.children if n.head[0] == 'proxy_pass']
            if len(proxies) != 1 or not proxies[0].head[1].startswith('http://172.18.0.1:8000/api/'):
                raise ValueError('Unexpected travel backend definition')
            upstream = proxies[0]
            value = upstream.head[1].replace('http://172.18.0.1:8000/', f'http://{backend_ip}:{backend_port}/', 1)
            edits.append((upstream.start, upstream.end, f'proxy_pass {value};'))
    fixture = '\n'.join(prefix) + '\n' + replace(source, edits)
    # No production listener or process-control path survives the transformation.
    result = parse(fixture)
    assert all(n.head[1] == f'{nginx_ip}:{nginx_port}' for n in all_nodes(result) if n.head[0] == 'listen')
    assert [n.head for n in result if n.head[0] == 'pid'] == [('pid', memory_root + '/nginx.pid')]
    return fixture
