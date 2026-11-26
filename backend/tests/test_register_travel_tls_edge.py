"""Protect the renewal target through a proxy rebuild and rollback."""
import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from tools import register_travel_tls_edge as register


def pinned(mode='maintenance'):
    return {'id': '1' * 64, 'image': 'sha256:' + '2' * 64, 'network': 'travel-edge', 'ip': '172.31.240.2',
            'config_sha256': register.EDGE_CONFIGS[mode]}


@pytest.mark.parametrize('old, target', [('maintenance', 'normal'), ('normal', 'maintenance')])
def test_replaces_only_id_and_configuration(old, target):
    previous = pinned(old)
    raw = json.dumps(previous).encode('ascii')
    digest = hashlib.sha256(raw).hexdigest()
    assert register.checked_target(raw, digest, target, '4' * 64) == {
        **previous, 'id': '4' * 64, 'config_sha256': register.EDGE_CONFIGS[target],
    }
    with pytest.raises(ValueError, match='since review'):
        register.checked_target(raw, '5' * 64, target, '4' * 64)


@pytest.mark.parametrize('timer_state, job', [('inactive', ''), ('active', ''), ('inactive', '19')])
def test_timer_and_service_must_be_off_without_jobs(monkeypatch, timer_state, job):
    result = SimpleNamespace(stdout=f'Id={register.TIMER}\nActiveState={timer_state}\nJob=\n\n'
                                   f'Id={register.RENEWAL}\nActiveState=inactive\nJob={job}\n')
    monkeypatch.setattr(subprocess, 'run', lambda *_, **__: result)
    if timer_state == 'inactive' and job == '':
        register.require_quiet_renewal()
    else:
        with pytest.raises(ValueError, match='renewal timer'):
            register.require_quiet_renewal()


def test_unmatched_leaf_rejected_before_write(monkeypatch, tmp_path):
    peer = SimpleNamespace(check=lambda: None, peer=lambda: '1' * 64)
    saved = tmp_path / 'cert.pem'
    saved.write_text('reviewed synthetic certificate', encoding='ascii')
    monkeypatch.setattr(register.edge.tls, 'checked_lineage_path', lambda *_: saved)
    monkeypatch.setattr(register.edge.rotate, 'fingerprint', lambda _: '1' * 64)
    monkeypatch.setattr(register.edge, 'Edge', lambda _: peer)
    monkeypatch.setattr(register.edge.tls, 'public_probe', lambda **_: {
        'matches_expected_leaf': False, 'fingerprint_sha256': '2' * 64, 'status': 'mismatch',
    })
    with pytest.raises(ValueError, match='Public HTTPS'):
        register.verify_certificate(SimpleNamespace(current=lambda: '1' * 64), pinned())


def test_replace_keeps_an_unexpected_concurrent_target(monkeypatch, tmp_path):
    path = tmp_path / 'edge-target.json'
    path.write_bytes(b'concurrent update')
    monkeypatch.setattr(register.edge.os, 'fchmod', lambda *_: None, raising=False)
    monkeypatch.setattr(register.edge, 'read_root_file', lambda file, _: file.read_bytes())
    with pytest.raises(ValueError, match='during verification'):
        register.replace_target(path, b'reviewed old target', b'reviewed new target')
    assert path.read_bytes() == b'concurrent update'
    assert list(tmp_path.iterdir()) == [path]


def test_replace_reads_back_reviewed_bytes_after_sync(monkeypatch, tmp_path):
    path = tmp_path / 'edge-target.json'
    path.write_bytes(b'reviewed old target')
    synced = []
    monkeypatch.setattr(register.edge.os, 'fchmod', lambda *_: None, raising=False)
    monkeypatch.setattr(register.edge, 'read_root_file', lambda file, _: file.read_bytes())
    monkeypatch.setattr(register.edge.rotate, 'sync_dir', synced.append)
    register.replace_target(path, b'reviewed old target', b'reviewed new target')
    assert path.read_bytes() == b'reviewed new target'
    assert synced == [tmp_path]
    assert list(tmp_path.iterdir()) == [path]
