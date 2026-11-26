"""Keep a successful pre-install renewal from counting as hook execution."""
from hashlib import sha256
import json

import pytest

from tools import observe_travel_edge_load as observer


def post(argv, start=0, stop=0, pid=0, code=0):
    return [argv[0], argv, False, start, 0, stop, 0, pid, code, 0]


STATUS = {'DropInPaths': observer.HOOK_PATH, 'NeedDaemonReload': 'no', 'ActiveState': 'inactive', 'Result': 'success', 'OnFailure': ''}


@pytest.mark.parametrize(('trigger', 'load', 'expected'), [
    (100, post(observer.POST_LOAD), 'waiting_for_first_hook_execution'),
    (100, post(observer.POST_LOAD, 130, 140, 725, 1), 'hook_completed'),
    (145, post(observer.POST_LOAD, 130, 140, 725, 1), 'last_hook_could_not_be_tied_to_timer'),
    (100, post(observer.POST_LOAD, 130, 140, 725, 2), 'scheduled_check_failed'),
])
def test_readback_checks_execution_time_and_status(trigger, load, expected):
    check = post(observer.POST_CHECK, 110, 120, 724, 1)
    assert observer.classify_run(trigger, STATUS, [check, load]) == expected
    with pytest.raises(ValueError, match='post-command order'):
        observer.classify_run(trigger, STATUS, [load, check])


def test_readback_accepts_only_the_matching_mail_handler():
    status = {**STATUS, 'DropInPaths': f'{observer.HOOK_PATH} {observer.ALERT_PATH}',
              'OnFailure': 'travel-ip-alert@renewal.service'}
    commands = [post(observer.POST_CHECK), post(observer.POST_LOAD)]
    assert observer.classify_run(100, status, commands) == 'waiting_for_first_hook_execution'
    with pytest.raises(ValueError, match='configuration is not active'):
        observer.classify_run(100, {**status, 'OnFailure': ''}, commands)


@pytest.mark.parametrize(('outcome', 'reload_sent'), [('unchanged', False), ('switched', True)])
def test_journal_result_is_scoped_to_the_hook_process(monkeypatch, outcome, reload_sent):
    load = post(observer.POST_LOAD, 135_000_000, 140_000_001, 725, 1)
    entry = json.dumps({'MESSAGE': json.dumps({'state': outcome, 'reload_sent': reload_sent, 'rollback_verified': False})})

    def read(command):
        assert '--since=@135 --until=@141' in command
        assert '_SYSTEMD_UNIT=travel-ip-renew.service _PID=725' in command
        return entry + '\n'

    monkeypatch.setattr(observer, 'read_origin', read)
    assert observer.hook_outcome(load) == outcome
    monkeypatch.setattr(observer, 'read_origin', lambda _: entry + '\n' + entry)
    with pytest.raises(ValueError, match='unambiguous success'):
        observer.hook_outcome(load)


@pytest.mark.parametrize(('mode', 'http_mode'), [
    ('normal', 'https_redirect'),
    ('maintenance', 'https_redirect'),
    ('maintenance', 'legacy'),
])
def test_public_routes_match_reviewed_active_proxy(monkeypatch, mode, http_mode):
    config = next(checksum for checksum, known_mode in observer.REVIEWED_CONFIG.items() if known_mode == mode)
    leaf = 'a' * 64
    identity = ('b' * 64, config, leaf)
    monkeypatch.setattr(observer, 'edge_identity', lambda: identity)
    redirect = sha256(f'https://{observer.TARGET}/travel/'.encode()).hexdigest()
    monkeypatch.setattr(observer, 'http_readback', lambda _: [(200, ''), (308, redirect)]
                        if http_mode == 'https_redirect' else [(200, ''), (200, '')])

    def observed(path, port, interface, *, expected_leaf=None):
        assert interface == 18
        if port == 80:
            assert path == '/travel/api/health' and expected_leaf is None
            return {'status': 403}
        assert expected_leaf == leaf
        if path == '/':
            return {'status': 404}
        if mode == 'normal':
            return {'status': 200}
        return {'status': 503, 'retry_after': '60', 'no_store': True}

    monkeypatch.setattr(observer, 'observed', observed)
    report = observer.verify_public(18)
    assert report['public_edge_mode'] == mode and report['public_http_mode'] == http_mode
    assert report['saved_selected_and_public_leaf_sha256'] == leaf and report['public_routes_verified']
    assert report.get('public_maintenance_and_legacy_http_verified', False) == (http_mode == 'legacy')

    identities = iter((identity, (*identity[:2], 'c' * 64)))
    monkeypatch.setattr(observer, 'edge_identity', lambda: next(identities))
    with pytest.raises(ValueError, match='changed during public verification'):
        observer.verify_public(18)

    monkeypatch.setattr(observer, 'edge_identity', lambda: identity)
    monkeypatch.setattr(observer, 'http_readback', lambda _: [(200, ''), (308, 'wrong-location')])
    with pytest.raises(ValueError, match='HTTP routes differ'):
        observer.verify_public(18)
