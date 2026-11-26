import json
import subprocess

import pytest

from tools import run_protected_restore as restore


START = {'phase': 'started', 'private_ram_workdir': '/dev/shm/travel-budget-restore-44z_sibo'}


def production_evidence():
    observed = json.loads(restore.EVIDENCE.read_bytes())['enhanced_synthetic_receipt']
    return {k: v for k, v in observed.items() if k not in ('private_workdir', 'ssh_exit')} | {
        'scope': 'production', 'synthetic_negative_checks': None,
    }


def encode(*events):
    return b'\n'.join(json.dumps(e).encode() for e in events) + b'\n'


@pytest.mark.parametrize('drift', [
    {'candidate_id': '0' * 64}, {'scope': 'synthetic'}, {'ram_workdir_removed': False},
    {'process_dump_disabled': 1}, {'row': {'sensitive': 'never-copy-this'}},
    {'timings_seconds': {'snapshot': float('nan'), 'restore_migrate_verify': 2}},
])
def test_clean_ssh_exit_does_not_substitute_for_complete_sanitized_evidence(drift):
    good = production_evidence()
    assert restore.classify(encode(START, good), 0)['state'] == 'production_copy_verified'
    outcome = restore.classify(encode(START, good | drift), 0)
    assert outcome['state'] == 'attempt_unconfirmed' and not outcome['cleanup_verified']
    assert 'report' not in outcome and 'never-copy-this' not in str(outcome)


def test_refuses_duplicate_json_keys_and_accepts_only_sanitized_failure_details():
    valid = {'phase': 'snapshot', 'passed': False, 'error_type': 'OperationalError'}
    assert restore.classify(encode(START, valid), 1)['state'] == 'reported_failure'
    assert restore.classify(encode(START, valid | {'detail': 'possibly sensitive'}), 1)['state'] == 'attempt_unconfirmed'
    forged = encode(START) + b'{"phase":"snapshot","passed":true,"passed":false,"error_type":"OperationalError"}\n'
    assert restore.classify(forged, 1)['state'] == 'attempt_unconfirmed'


def test_interrupted_attempt_keeps_exact_handle_and_refuses_second_ssh(monkeypatch, tmp_path):
    archive = tmp_path / 'review'
    archive.mkdir()
    monkeypatch.setattr(restore, 'ARCHIVE', archive)
    monkeypatch.setattr(restore, 'REPO', tmp_path)
    calls = []
    receipt = archive / 'production-attempt.jsonl'

    def ssh_timeout(args, **kwargs):
        calls.append(args)
        initial = json.loads(receipt.read_text(encoding='utf-8'))
        assert initial['state'] == 'starting' and 'restore-' + initial['attempt'] in args[-1]
        assert kwargs['input'] == b'local test payload'
        raise subprocess.TimeoutExpired(args, 85, output=encode(START))

    monkeypatch.setattr(restore.subprocess, 'run', ssh_timeout)
    assert restore.run_once(b'local test payload') == 1
    with pytest.raises(FileExistsError):
        restore.run_once(b'local test payload')
    assert len(calls) == 1
    final = json.loads(receipt.read_text(encoding='utf-8').splitlines()[-1])
    assert final['state'] == 'attempt_unconfirmed' and final['reason'] == 'observation_timeout'
    assert final['private_ram_workdir'] == START['private_ram_workdir'] and not final['cleanup_verified']


def test_reviewed_package_is_loaded_without_ssh_and_altered_payload_is_refused(monkeypatch, tmp_path):
    approved = restore.package()
    bad = tmp_path / 'payload.py'
    bad.write_bytes(approved + b'\n# changed\n')
    monkeypatch.setattr(restore, 'SCRIPT', bad)
    with pytest.raises(ValueError):
        restore.package()
