import pytest

from test_nginx_merge import SOURCE, TEMPLATE
from tools.nginx_merge import merge, parse, all_nodes
from tools.proxy_chain_fixture import render_fixture

MEMORY = '/dev/shm/travel-proxy-chain-' + 'a' * 32


def fixture(source=None, **overrides):
    merged = source or merge(SOURCE, TEMPLATE)[0]
    params = dict(memory_root=MEMORY, nginx_ip='172.18.0.5', nginx_port=42001, backend_ip='172.18.0.1', backend_port=42002)
    params.update(overrides)
    return render_fixture(merged, **params)


def test_isolates_listeners_process_files_and_only_travel_static_and_backend():
    result = fixture()
    assert 'listen 172.18.0.5:42001;' in result
    assert f'pid {MEMORY}/nginx.pid;' in result
    assert f'error_log {MEMORY}/error.log warn;' in result
    assert f'root {MEMORY}/html;' in result
    assert 'root /usr/share/nginx/html;' in result
    assert 'proxy_pass http://other:8080/api/;' in result
    assert 'http://172.18.0.1:8000/' not in result
    assert 'http://172.18.0.1:42002/api/' in result
    assert all(n.head[1] == 'off' for n in all_nodes(parse(result)) if n.head[0] == 'access_log')


@pytest.mark.parametrize('override', [
    {'memory_root': '/etc/nginx'}, {'nginx_ip': '0.0.0.0'}, {'nginx_ip': '134.175.137.18'},
    {'backend_ip': '*'}, {'nginx_port': 80}, {'backend_port': 8000}, {'backend_port': 70000},
])
def test_dangerous_paths_addresses_and_ports_are_rejected(override):
    with pytest.raises(ValueError):
        fixture(**override)


@pytest.mark.parametrize('change', ['ssl', 'cache', 'include', 'multiple_servers'])
def test_unreviewed_runtime_context_is_rejected(change):
    source = merge(SOURCE, TEMPLATE)[0]
    if change == 'ssl':
        source = source.replace('listen 80;', 'listen 80 ssl;')
    elif change == 'cache':
        source = source.replace('http {', 'http { proxy_cache_path /production/cache keys_zone=other:1m;', 1)
    elif change == 'include':
        source = source.replace('include /etc/nginx/mime.types;', 'include other.conf;')
    else:
        source = source.replace('http {', 'http { server { listen 81; }', 1)
    with pytest.raises(ValueError):
        fixture(source)
