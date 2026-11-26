"""Verify the ingress evidence parser and closed-entry conditions without root."""
from contextlib import nullcontext
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from tools import verify_travel_cutover_ready as verify


def match(left, right):
    return {'match': {'op': '==', 'left': left, 'right': right}}


def payload(protocol, field):
    return {'payload': {'protocol': protocol, 'field': field}}


def kernel_snapshot():
    destination = match(payload('tcp', 'dport'), 8000)
    accept = [match({'meta': {'key': 'iifname'}}, 'br-travel-edge'), match(payload('ip', 'saddr'), verify.PROXY),
              match(payload('ip', 'daddr'), verify.GATEWAY), destination,
              {'counter': {'packets': 10, 'bytes': 660}}, {'accept': None}]
    drop = [destination, {'counter': {'packets': 3, 'bytes': 180}}, {'drop': None}]
    table = {'family': 'inet', 'table': 'travel_guard'}
    return [{'metainfo': {'json_schema_version': 1}}, {'table': {'family':'inet', 'name':'travel_guard', 'handle':1}},
            {'chain': {**table, 'name':'backend_input', 'handle':2, 'type':'filter', 'hook':'input',
                       'prio':-5, 'policy':'accept'}},
            {'rule': {**table, 'chain':'backend_input', 'handle':3, 'expr':accept}},
            {'rule': {**table, 'chain':'backend_input', 'handle':4, 'expr':drop}}]


def test_expected_rule_and_counters():
    snapshot = kernel_snapshot()
    assert verify.rule_counters(json.dumps({'nftables': snapshot})) == (10, 3)


@pytest.mark.parametrize('wrong', ('address', 'priority', 'order'))
def test_refuses_nft_drift(wrong):
    snapshot = kernel_snapshot()
    if wrong == 'address':
        snapshot[3]['rule']['expr'][1]['match']['right'] = '172.31.240.3'
    elif wrong == 'priority':
        snapshot[2]['chain']['prio'] = 0
    else:
        snapshot[3], snapshot[4] = snapshot[4], snapshot[3]
    with pytest.raises(ValueError, match='Unexpected'):
        verify.rule_counters(json.dumps({'nftables': snapshot}))


@pytest.mark.parametrize('scenario', ('valid', 'bypass', 'unchanged_drop', 'new_pid'))
def test_requires_pinned_inputs_allow_and_drop_with_same_process(monkeypatch, scenario):
    contents = b'synthetic reviewed bytes'
    backend = iter((65, 99 if scenario == 'new_pid' else 65))
    readings = iter(((1, 2), (3, 2 if scenario == 'unchanged_drop' else 4)))
    probes = []
    monkeypatch.setattr(verify, 'PINNED', {'/synthetic/app': sha256(contents).hexdigest()})
    monkeypatch.setattr(verify.edge, 'read_root_file', lambda *_, **__: contents)
    monkeypatch.setattr(verify, 'process_identity', lambda: next(backend))
    monkeypatch.setattr(verify, 'checked_edge', lambda: SimpleNamespace(peer=lambda: probes.append('maintenance')))
    monkeypatch.setattr(verify, 'live_counters', lambda: next(readings))

    def read(target, path):
        probes.append(path)
        return (200, {'status':'ok'}) if path == '/api/health' else (401, {})

    def connect(address, timeout):
        assert address == (verify.GATEWAY, 8000) and timeout == 1.5
        if scenario == 'bypass':
            return nullcontext()
        raise TimeoutError

    monkeypatch.setattr(verify, 'proxy_read', read)
    monkeypatch.setattr(verify.socket, 'create_connection', connect)
    if scenario == 'valid':
        assert verify.verify_while_closed()['state'] == 'maintenance_ingress_verified'
        assert probes == ['/api/health', '/api/trips', 'maintenance']
    else:
        with pytest.raises(ValueError):
            verify.verify_while_closed()
