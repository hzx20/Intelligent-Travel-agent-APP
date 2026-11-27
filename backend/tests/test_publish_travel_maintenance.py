from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import publish_travel_maintenance as publish, recheck_travel_maintenance as recheck
from tools import publish_travel_maintenance_remote as remote

# inputs() 读 .release-candidates/ 里的私有复核包，CI 拿不到就跳过。
REVIEW = Path(__file__).resolve().parents[2] / '.release-candidates/budget-https-load-2026-11-19'


NEW_LEAF = 'a' * 64
OLD_LEAF = 'b' * 64


@pytest.mark.parametrize('failure', [None, 'api', 'other_path', 'existing_http'])
def test_public_check_requires_maintenance_on_exact_paths_and_existing_http(monkeypatch, failure):
    before = [(200, None), (200, None)]
    responses = {'/travel/': (503, '60', 'no-store'),
                 '/travel/api/health': (503, '60', 'no-store'),
                 '/': (404, None, None)}
    if failure == 'api':
        responses['/travel/api/health'] = (200, None, None)
    elif failure == 'other_path':
        responses['/'] = (503, '60', 'no-store')
    def https_status(path, leaf):
        assert leaf == NEW_LEAF
        return responses[path]

    monkeypatch.setattr(publish, 'https_status', https_status)
    monkeypatch.setattr(publish, 'http_state', lambda: before if failure != 'existing_http' else [(404, None), (200, None)])
    if failure is None:
        assert all(publish.public_checks(before, NEW_LEAF).values())
    else:
        with pytest.raises(ValueError):
            publish.public_checks(before, NEW_LEAF)


@pytest.mark.skipif(not REVIEW.is_dir(), reason='Private review bundle required (local .release-candidates/)')
def test_source_and_input_match_the_verified_private_maintenance_bundle():
    source, payload = publish.inputs()
    assert source and payload.endswith(b'\n')
    assert b'proxy_pass' not in payload


@pytest.mark.parametrize('failed_stage', [None, 'tcp', 'tls', 'http'])
def test_recheck_retains_only_after_verifying_host_port_and_selected_route(monkeypatch, failed_stage):
    response = {'stage': 'complete', 'status': 503, 'retry_after': '60', 'no_store': True}
    before = [(200, 'same'), (200, 'same')]
    result = {'readiness': {'fingerprint_sha256': NEW_LEAF}}
    interface = 19

    def observed(path, port, index=None, *, expected_leaf=None):
        assert port == 443 and expected_leaf == NEW_LEAF
        if index is None:
            return {'stage': 'tls', 'error_type': 'TimeoutError'}
        assert index == interface
        if failed_stage and path == '/travel/':
            return {'stage': failed_stage, 'error_type': 'TimeoutError'}
        return response if path != '/' else {'stage': 'complete', 'status': 404}

    monkeypatch.setattr(recheck, 'loopback',
                        lambda: {'stage': 'complete', 'fingerprint_sha256': NEW_LEAF, 'status': 503})
    monkeypatch.setattr(recheck, 'observed', observed)
    monkeypatch.setattr(recheck, 'http_readback', lambda index: before)
    if failed_stage:
        with pytest.raises(ValueError):
            recheck.verify(interface, before, result)
        assert result['route_trace']['selected_interface_travel']['stage'] == failed_stage
    else:
        assert all(recheck.verify(interface, before, result).values())
        assert result['route_trace']['default_route_travel']['stage'] == 'tls'


@pytest.mark.parametrize('reported', [NEW_LEAF, OLD_LEAF])
def test_saved_certificate_selection_accepts_a_renewal_and_checks_snapshot_drift(reported):
    def validate(*_):
        pass

    def stage(chain, key, validator):
        assert (chain, key, validator) == (b'chain', b'key', validate)
        return NEW_LEAF

    store = SimpleNamespace(current=lambda: OLD_LEAF, stage=stage)
    tls = SimpleNamespace(local_renewal_policy=lambda: {'status': 'ok'},
                          local_lineage=lambda paired_key: {'status': 'ok', 'fingerprint_sha256': reported})
    edge = SimpleNamespace(tls=tls, saved_pair=lambda: (b'chain', b'key'), validate_pair=validate)
    if reported != NEW_LEAF:
        with pytest.raises(ValueError):
            remote.select_saved_certificate(store, edge)
    else:
        assert remote.select_saved_certificate(store, edge) == (OLD_LEAF, NEW_LEAF)


def test_failing_to_publish_restores_the_previous_certificate_pointer(monkeypatch):
    plan = {'host_interface': 'br-travel-edge', 'subnet': '172.31.240.0/28', 'gateway': '172.31.240.1',
            'dynamic_range': '172.31.240.8/29', 'docker_network': 'travel-edge'}
    pointers = []

    def no_network(*_):
        raise OSError('isolated fixture refusal')

    monkeypatch.setattr(remote, 'call', no_network)
    monkeypatch.setattr(remote, 'owned', lambda *_, **__: [])
    with pytest.raises(OSError):
        remote.publish_edge('sample', b'', plan, 'image', None, None,
                            SimpleNamespace(point=pointers.append), OLD_LEAF, NEW_LEAF)
    assert pointers == [NEW_LEAF, OLD_LEAF]


@pytest.mark.parametrize('lineage', [NEW_LEAF, OLD_LEAF])
def test_publication_rechecks_the_saved_certificate_before_retaining(tmp_path, monkeypatch, lineage):
    plan = {'host_interface': 'br-travel-edge', 'subnet': '172.31.240.0/28', 'gateway': '172.31.240.1',
            'dynamic_range': '172.31.240.8/29', 'docker_network': 'travel-edge', 'proxy_ip': '172.31.240.2'}
    container, network = [], []
    current = [OLD_LEAF]
    actions = []

    def point(fingerprint):
        current[0] = fingerprint

    def docker(_, command, *arguments):
        actions.append((command, arguments))
        if command == 'network' and arguments[0] == 'create':
            network.append('network')
            return b'network'
        if command == 'run':
            assert current == [NEW_LEAF]
            container.append('container')
            return b'container'
        if command == 'inspect':
            return b'unless-stopped'
        if command == 'rm':
            container.clear()
        if command == 'network' and arguments[0] == 'rm':
            network.clear()
        return b''

    monkeypatch.setattr(remote, 'call', docker)
    monkeypatch.setattr(remote, 'owned', lambda _, network=False: list(resources[network]))
    monkeypatch.setattr(remote.select, 'select', lambda *args: ([True], [], []))
    monkeypatch.setattr(remote.sys, 'stdin', StringIO('retain sample\n'))
    resources = {True: network, False: container}
    target = SimpleNamespace(check=lambda: None, peer=lambda: NEW_LEAF, guarded=lambda: 'container')
    edge = SimpleNamespace(CONF=tmp_path/'edge.conf', PIN=tmp_path/'pin.json', STORE=tmp_path, TARGET_CONF='/edge.conf',
                           TARGET_STORE='/certs', NAME='/travel-https-edge', ROLE='fixture',
                           read_root_file=lambda *args: b'fixture', parse_target=lambda raw: raw,
                           Edge=lambda _: target, rotate=SimpleNamespace(wait_for_leaf=lambda peer, expected: peer() == expected),
                           tls=SimpleNamespace(local_lineage=lambda paired_key: {'status': 'ok', 'fingerprint_sha256': lineage}))
    store = SimpleNamespace(current=lambda: current[0], point=point)
    args = ('sample', b'fixture', plan, 'image', lambda: None, edge, store, OLD_LEAF, NEW_LEAF)
    if lineage == NEW_LEAF:
        result = remote.publish_edge(*args)
        assert result['fingerprint_sha256'] == NEW_LEAF and current == [NEW_LEAF]
        assert container and network and any(command == 'update' for command, _ in actions)
    else:
        with pytest.raises(ValueError):
            remote.publish_edge(*args)
        assert not container and not network and current == [OLD_LEAF]
        assert not edge.CONF.exists() and not edge.PIN.exists()
        assert all(command != 'update' for command, _ in actions)


@pytest.mark.parametrize(('tag', 'leaf', 'expected'), [('matching', NEW_LEAF, NEW_LEAF),
                                                       ('wrong', NEW_LEAF, None),
                                                       ('matching', 'malformed', None)])
def test_ready_fingerprint_is_pinned_for_the_expected_origin_attempt(tag, leaf, expected):
    assert publish.ready_leaf({'event': 'ready', 'tag': tag, 'fingerprint_sha256': leaf}, 'matching') == expected
