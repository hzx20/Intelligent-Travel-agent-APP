from types import SimpleNamespace

import pytest

from tools import qa_nginx_bind_switch as qa


def test_cleanup_uses_full_ids_and_releases_owned_container(monkeypatch, tmp_path):
    own = qa.Rehearsal(tmp_path, {'image_id': 'sha256:' + 'a' * 64, 'production_container_id': 'b' * 64})
    cid = 'c' * 64
    calls = []
    removed = False
    def command(args, **kwargs):
        nonlocal removed
        calls.append(args)
        if args[3] == 'ps':
            output = '' if removed else (cid if '--no-trunc' in args else cid[:12]) + '\n'
            return SimpleNamespace(returncode=0, stdout=output)
        if args[3] == 'rm':
            assert args[-1] == cid and '--volumes' in args
            removed = True
        return SimpleNamespace(returncode=0, stdout='')
    def inspect(cid_arg):
        assert cid_arg == cid
    monkeypatch.setattr(qa, 'command', command)
    monkeypatch.setattr(own, 'inspect_owned', inspect)
    assert own.cleanup()
    assert any(args[3] == 'stop' and args[-1] == cid for args in calls)


def test_cleanup_does_not_stop_resource_failing_ownership_check(monkeypatch, tmp_path):
    own = qa.Rehearsal(tmp_path, {'image_id': 'sha256:' + 'a' * 64, 'production_container_id': 'b' * 64})
    calls = []
    def command(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='c' * 64 + '\n')
    def inspect(cid):
        raise AssertionError('Ownership mismatch')
    monkeypatch.setattr(qa, 'command', command)
    monkeypatch.setattr(own, 'inspect_owned', inspect)
    with pytest.raises(AssertionError, match='Ownership mismatch'):
        own.cleanup()
    assert all(args[3] == 'ps' for args in calls)
