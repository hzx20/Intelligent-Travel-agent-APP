import pytest

from test_nginx_merge import SOURCE, TEMPLATE
from tools.bind_switch_fixture import missing_entry_fixture, render_bind_fixture, required_hosts
from tools.nginx_merge import all_nodes, merge, parse
from tools.travel_maintenance import render as maintenance

MEMORY = '/dev/shm/travel-proxy-chain-' + 'c' * 32


@pytest.mark.parametrize('source', [merge(SOURCE, TEMPLATE)[0], maintenance(merge(SOURCE, TEMPLATE)[0])[0]])
def test_fixture_is_loopback_only_and_can_reload_without_production_paths(source):
    fixture = render_bind_fixture(source, MEMORY)
    nodes = parse(fixture)
    assert ('master_process', 'on') in [n.head for n in nodes]
    assert ('pid', MEMORY + '/nginx.pid') in [n.head for n in nodes]
    assert {n.head[1] for n in all_nodes(nodes) if n.head[0] == 'listen'} == {'127.0.0.1:28080', '127.0.0.1:28081'}
    assert 'root ' + MEMORY + '/html' not in fixture
    assert 'proxy_pass http://other:8080/api/;' in fixture
    assert 'SYNTHETIC-BACKEND' in fixture


@pytest.mark.parametrize('web,backend', [(28080, 28080), (80, 28081), (28080, 8000), (28080, 70000)])
def test_unsafe_or_colliding_ports_are_refused(web, backend):
    with pytest.raises(ValueError):
        render_bind_fixture(merge(SOURCE, TEMPLATE)[0], MEMORY, web, backend)


def test_only_literal_hostnames_are_mapped_and_internal_groups_are_not_dns():
    source = 'http { upstream app { server worker:8080; server 172.18.0.2:8081; } server { location / { proxy_pass http://app; } } }'
    assert required_hosts(source) == ['worker']
    assert required_hosts(SOURCE) == ['other']
    with pytest.raises(ValueError):
        required_hosts('http { server { location / { proxy_pass http://$upstream; } } }')


def test_negative_http_fixture_changes_only_one_entry_root():
    fixture = render_bind_fixture(merge(SOURCE, TEMPLATE)[0], MEMORY)
    broken = missing_entry_fixture(fixture)
    assert broken.replace('root /usr/share/nginx/html/__missing_for_runtime_gate;', 'root /usr/share/nginx/html;') == fixture
    assert broken.count('__missing_for_runtime_gate') == 1
    with pytest.raises(ValueError):
        missing_entry_fixture(render_bind_fixture(maintenance(merge(SOURCE, TEMPLATE)[0])[0], MEMORY))
