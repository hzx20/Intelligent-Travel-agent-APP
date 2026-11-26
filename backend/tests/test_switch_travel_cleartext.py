"""Test the shared nginx handoff on a temporary real file and fake nginx."""
import os
import subprocess

import pytest

from tools import switch_travel_cleartext as switch


@pytest.mark.parametrize('restore', [False, True])
@pytest.mark.parametrize(('failure', 'expected', 'reloads'), [
    ('none', 'redirect_active_and_verified', 1),
    ('syntax', 'original_restored_and_verified', 0),
    ('route', 'original_restored_and_verified', 2),
    ('short_write', 'original_restored_and_verified', 0),
    ('concurrent_write', 'restoration_unconfirmed', 1),
])
def test_guard_preserves_inode_backup_and_refuses_to_overwrite_drift(monkeypatch, tmp_path, failure, expected, reloads,
                                                                  restore):
    original, replacement = b'original synthetic shared config', b'travel redirect'
    monkeypatch.setattr(switch, 'LEGACY_SHA', switch.sha(original))
    monkeypatch.setattr(switch, 'CANDIDATE_SHA', switch.sha(replacement))
    original, replacement = (replacement, original) if restore else (original, replacement)
    before_routes = switch.redirect_routes if restore else switch.original_routes
    after_routes = switch.original_routes if restore else switch.redirect_routes
    path = tmp_path / 'nginx.conf'
    backup = tmp_path / 'nginx.before.conf'
    path.write_bytes(original)
    inode = path.stat().st_ino
    original_root = (200, None, 'synthetic root digest')
    calls = []
    monkeypatch.setattr(switch, 'CONFIG', path)
    monkeypatch.setattr(switch, 'visible_digest', lambda _: switch.sha(path.read_bytes()))
    monkeypatch.setattr(switch, 'shared_master', lambda *_: b'1')
    monkeypatch.setattr(switch, 'response', lambda *_, **__: original_root)
    monkeypatch.setattr(switch, 'maintenance_edges', lambda: None)
    monkeypatch.setattr(switch, 'private_backup', lambda target, raw: target.write_bytes(raw))
    monkeypatch.setattr(switch, before_routes.__name__, lambda _: True)
    before_routes = getattr(switch, before_routes.__name__)

    def command(*args):
        calls.append(args)
        if failure == 'syntax' and '-t' in args and path.read_bytes() == replacement:
            raise subprocess.CalledProcessError(1, args)

    def verify(check, root):
        assert root == original_root
        if check is before_routes:
            assert path.read_bytes() == original
            return
        assert check is after_routes
        if failure == 'concurrent_write':
            path.write_bytes(b'concurrent edit')
            raise ValueError('unexpected other edit')
        elif failure == 'route':
            raise ValueError('bad route')
        else:
            assert path.read_bytes() == replacement

    monkeypatch.setattr(switch, 'command', command)
    monkeypatch.setattr(switch, 'confirm_routes', verify)
    with path.open('r+b') as opened:
        fd = opened.fileno()
        if failure == 'short_write':
            os_write = os.write
            count = 0

            def partial(descriptor, raw):
                nonlocal count
                if descriptor == fd:
                    count += 1
                    if count == 1:
                        return os_write(descriptor, raw[:4])
                    if count == 2:
                        raise OSError('synthetic write failure')
                return os_write(descriptor, raw)

            monkeypatch.setattr(os, 'write', partial)
        report = switch.switch_locked(fd, replacement, 'id', 'image', backup, restore=restore)
    if restore:
        expected = {'redirect_active_and_verified': 'legacy_service_and_http_restored',
                    'original_restored_and_verified': 'redirect_retained'}.get(expected, expected)
    assert report['state'] == expected
    assert backup.read_bytes() == original and path.stat().st_ino == inode
    resulting = {'none': replacement, 'concurrent_write': b'concurrent edit'}.get(failure, original)
    assert path.read_bytes() == resulting
    assert sum('reload' in step for step in calls) == reloads


def test_write_gate_requires_redirect_and_denial_at_the_expected_paths(monkeypatch):
    root = (200, None, 'original site')
    deep = '/travel/trips/1?panel=budget'
    target = f'https://{switch.PUBLIC}{deep}'

    def response(path, method='GET', *, body_hash=False):
        if path == '/':
            assert body_hash
            return root
        if path == deep and method in ('GET', 'HEAD'):
            return (308, target, None)
        if path.startswith('/.well-known/'):
            return (404, None, None)
        return (403, None, None)

    monkeypatch.setattr(switch, 'response', response)
    assert switch.redirect_routes(root)
    monkeypatch.setattr(switch, 'response', lambda path, *a, **kw:
                        (200, None, None) if path == '/travel/api/health' else response(path, *a, **kw))
    assert not switch.redirect_routes(root)


def test_legacy_readiness_retries_connection_refusal_and_bad_body(monkeypatch, tmp_path):
    attempts = iter([None, b'<html>loading</html>', b'{"status":"ok"}'])
    sleeps = []

    def probe(*args):
        assert args[-1] == 'http://127.0.0.1:8000/api/health'
        result = next(attempts)
        if result is None:
            raise subprocess.CalledProcessError(7, args)
        return result

    monkeypatch.setattr(switch, 'command', probe)
    monkeypatch.setattr(switch, 'legacy_service_state', lambda _: {'ActiveState': 'active', 'MainPID': '1'})
    monkeypatch.setattr(switch.time, 'monotonic', lambda: sum(sleeps))
    monkeypatch.setattr(switch.time, 'sleep', sleeps.append)
    switch.wait_for_legacy_readiness(tmp_path, timeout=1)
    assert sleeps == [.25, .25]


@pytest.mark.parametrize('pending_upgrade', [True, False])
def test_premigration_restore_preserves_redirect_without_verified_readiness(monkeypatch, tmp_path, pending_upgrade):
    config = tmp_path / 'nginx.conf'
    config.write_bytes(b'redirect')
    monkeypatch.setattr(switch, 'CONFIG', config)
    monkeypatch.setattr(switch, 'CANDIDATE_SHA', switch.sha(b'redirect'))
    monkeypatch.setattr(switch, 'visible_digest', lambda _: switch.CANDIDATE_SHA)
    monkeypatch.setattr(switch, 'shared_master', lambda *_: b'1')
    monkeypatch.setattr(switch, 'maintenance_edges', lambda: None)
    failures = []

    def command(*args):
        if args[0] == switch.SYSTEMCTL:
            failures.append('start')
            return b''
        raise subprocess.CalledProcessError(7, args)

    unit = tmp_path / 'travel.service'
    unit.write_bytes(b'legacy')
    monkeypatch.setattr(switch, 'LEGACY_UNIT', unit)
    monkeypatch.setattr(switch, 'LEGACY_UNIT_SHA', switch.sha(b'legacy'))
    if pending_upgrade:
        (tmp_path / 'write-opening.json').touch()
    else:
        monkeypatch.setattr(switch, 'legacy_service_state', lambda _: None)
    sleeps = []
    monkeypatch.setattr(switch, 'command', command)
    monkeypatch.setattr(switch.time, 'monotonic', lambda: sum(sleeps))
    monkeypatch.setattr(switch.time, 'sleep', sleeps.append)
    with config.open('r+b') as opened:
        expected = 'only valid before upgrade' if pending_upgrade else 'did not become ready'
        with pytest.raises(ValueError, match=expected):
            switch.restore_before_upgrade(opened.fileno(), b'legacy', 'id', 'image', tmp_path / 'redirect-backup')
    assert failures == ([] if pending_upgrade else ['start'])
    assert config.read_bytes() == b'redirect' and not (tmp_path / 'redirect-backup').exists()
