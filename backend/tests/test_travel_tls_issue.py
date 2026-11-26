"""First-order orchestration checks, isolated from Certbot and network."""
from contextlib import contextmanager
import json
import subprocess
import sys

import pytest

from tools import travel_tls_issue as issue


@pytest.fixture
def order_fixture(monkeypatch, tmp_path):
    monkeypatch.setattr(issue, 'ROOT', tmp_path / 'etc')
    monkeypatch.setattr(issue, 'WORK_ROOT', tmp_path / 'var')
    monkeypatch.setattr(issue, 'LOG_ROOT', tmp_path / 'log')
    monkeypatch.setattr(issue.tls, 'ACME', issue.ROOT / 'acme')
    monkeypatch.setattr(issue, 'sync_dir', lambda path: None)
    monkeypatch.setattr(issue, 'private_directory', lambda path: None)
    monkeypatch.setattr(issue.os, 'umask', lambda mode: 0)
    events = []
    readiness = {'certbot_fails': False, 'host_invalid': False, 'lineage_status': 'ok', 'version': '5.10.0'}

    def inventory():
        events.append('webroot_check')
        if readiness['host_invalid']:
            raise ValueError('changed HTTP host')

    @contextmanager
    def webroot():
        events.append('challenge_enter')
        try:
            yield
        finally:
            events.append('challenge_cleanup')

    def command(*args, timeout):
        if args == (issue.CERTBOT, '--version'):
            return f"certbot {readiness['version']}\n".encode('ascii')
        assert args[:2] == (issue.CERTBOT, 'certonly')
        events.append('order')
        assert issue.tls.ACME.is_dir()
        assert (issue.ROOT / 'empty.ini').read_bytes() == b''
        log = (issue.ROOT / 'first-order.jsonl').read_text()
        assert 'invoking_production_certbot' in log
        if readiness['certbot_fails']:
            raise subprocess.TimeoutExpired(args, timeout)
        return b''

    def saved(*, paired_key):
        assert paired_key
        events.append('saved')
        return {'status': readiness['lineage_status'], 'key_match_checked': True,
                'fingerprint_sha256': 'a'*64, 'seconds_left': 130*3600}

    monkeypatch.setattr(issue, 'check_shared_webroot', inventory)
    monkeypatch.setattr(issue.acme, 'command', command)
    monkeypatch.setattr(issue.acme, 'prepared_webroot', webroot)
    monkeypatch.setattr(issue.tls, 'local_lineage', saved)
    monkeypatch.setattr(issue.tls, 'local_renewal_policy', lambda: events.append('renewal_policy'))
    return readiness, events


def test_pinned_command_has_production_ca_and_only_certificate_operation():
    args = issue.certbot_order('operator@example.invalid')
    assert args[:2] == (issue.CERTBOT, 'certonly')
    assert args[args.index('--server')+1] == issue.tls.PRODUCTION_CA
    assert args[args.index('--required-profile')+1] == 'shortlived'
    assert args[args.index('--webroot-path')+1] == issue.tls.WEBROOT
    assert args[args.index('--ip-address')+1] == '134.175.137.18'
    assert '--email=operator@example.invalid' in args
    assert '--no-directory-hooks' in args
    assert '--register-unsafely-without-email' in issue.certbot_order(None)


def test_success_rechecks_live_site_and_records_only_redacted_verification(order_fixture):
    _, events = order_fixture
    result = issue.issue('operator@example.invalid')
    assert result['state'] == 'first_certificate_verified'
    assert result['challenge_cleanup_verified'] is True and result['public_tls_verified'] is False
    assert events == ['webroot_check', 'challenge_enter', 'order', 'challenge_cleanup', 'webroot_check',
                      'renewal_policy', 'saved']
    receipt = (issue.ROOT / 'first-order.jsonl').read_text()
    assert 'example.invalid' not in receipt
    assert json.loads(receipt.splitlines()[-1]) == result
    with pytest.raises(ValueError, match='First-order state already exists'):
        issue.issue(None)
    assert events.count('order') == 1


@pytest.mark.parametrize('failure', ['certbot', 'lineage'])
def test_timeout_or_unverified_lineage_stops_without_automatic_retry(order_fixture, failure):
    readiness, events = order_fixture
    if failure == 'certbot':
        readiness['certbot_fails'] = True
    else:
        readiness['lineage_status'] = 'warning'
    result = issue.issue(None)
    assert result['state'] == 'first_order_unverified' and result['certbot_attempted'] is True
    assert result['public_tls_verified'] is False
    assert events.count('order') == 1 and 'challenge_cleanup' in events
    assert (issue.ROOT / 'first-order.jsonl').is_file()


def test_failed_preflight_does_not_create_certificate_state(order_fixture):
    readiness, events = order_fixture
    readiness['host_invalid'] = True
    with pytest.raises(ValueError, match='changed HTTP host'):
        issue.issue(None)
    assert events == ['webroot_check']
    assert not issue.ROOT.exists()


def test_older_certbot_is_refused_before_creating_state(order_fixture):
    readiness, events = order_fixture
    readiness['version'] = '5.9.0'
    with pytest.raises(ValueError, match='webroot fix'):
        issue.issue(None)
    assert events == ['webroot_check'] and not issue.ROOT.exists()


def test_default_cli_reviews_without_contact_or_order():
    program = (sys.executable, '-I', '-B', issue.__file__)
    preview = subprocess.run(program, capture_output=True, check=True, timeout=8)
    assert json.loads(preview.stdout)['state'] == 'review_only'
    refused = subprocess.run((*program, '--issue'), capture_output=True, timeout=8)
    assert refused.returncode == 2
