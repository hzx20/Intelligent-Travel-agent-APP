import sys

import pytest

from tools import guard_travel_write_opening as guard


APPLICATION = 'a' * 64
BACKUP = 'b' * 64


def test_opening_child_sees_the_marker_even_if_the_command_fails(tmp_path):
    marker = tmp_path/'opening.json'
    script = ('import json,sys; from pathlib import Path; '
              'record=json.loads(Path(sys.argv[1]).read_bytes()); '
              'assert record["state"]=="new_writes_may_have_been_accepted"; '
              'assert record["application_id"]==sys.argv[2]; sys.exit(23)')
    result = guard.run_opening(marker, APPLICATION, BACKUP, [sys.executable, '-c', script, str(marker), APPLICATION])
    assert result == 23
    assert guard.recovery_policy(marker) == {'state': 'new_writes_may_have_been_accepted',
                                             'preserve_current_database': True, 'old_snapshot_overwrite_allowed': False}


def test_existing_or_partial_record_blocks_repeating_the_opening(tmp_path, monkeypatch):
    marker = tmp_path/'opening.json'
    marker.write_bytes(b'partial record after interruption')

    def forbidden(*args, **kwargs):
        pytest.fail('Opening command must not run when a record already exists')

    monkeypatch.setattr(guard.subprocess, 'run', forbidden)
    with pytest.raises(FileExistsError):
        guard.run_opening(marker, APPLICATION, BACKUP, [sys.executable])
    assert marker.read_bytes() == b'partial record after interruption'
    assert guard.recovery_policy(marker) == {'state': 'opening_record_unconfirmed', 'preserve_current_database': True,
                                             'old_snapshot_overwrite_allowed': False}


def test_missing_record_is_not_proof_no_new_writes(tmp_path):
    report = guard.recovery_policy(tmp_path/'missing.json')
    assert report['need_independent_proof_no_new_writes']
    assert report['preserve_current_database'] and not report['old_snapshot_overwrite_allowed']


def test_failed_fsync_leaves_record_and_never_invokes_command(tmp_path, monkeypatch):
    marker = tmp_path/'opening.json'

    def interrupted(*_):
        raise OSError('fixture interruption before durable write')

    def forbidden(*args, **kwargs):
        pytest.fail('Opening command must wait for durable recording')

    monkeypatch.setattr(guard.os, 'fsync', interrupted)
    monkeypatch.setattr(guard.subprocess, 'run', forbidden)
    with pytest.raises(OSError):
        guard.run_opening(marker, APPLICATION, BACKUP, [sys.executable])
    assert marker.exists()
    assert guard.recovery_policy(marker)['preserve_current_database']
