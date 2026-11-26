import sqlite3
import json
import sys

import pytest

from tools.rehearse_release import fingerprints, require_empty_jobs, validate_paths
from tools import rehearse_release
from test_release_candidate import inputs
from tools.release_candidate import prepare


def test_original_column_fingerprint_ignores_new_columns_but_detects_changes(tmp_path):
    path = tmp_path / 'source.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE records(id INTEGER PRIMARY KEY, value BLOB)')
        db.execute('INSERT INTO records VALUES(1, ?)', (b'private-value',))
    before = fingerprints(path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE records ADD COLUMN revision TEXT DEFAULT 'new'")
    assert fingerprints(path, before) == before
    with sqlite3.connect(path) as db:
        db.execute("UPDATE records SET value = 'changed'")
    assert fingerprints(path, before) != before


@pytest.mark.parametrize('table', ['trip_ai_jobs', 'plan_generation_jobs'])
def test_persistent_jobs_are_rejected_before_any_candidate_start(tmp_path, table):
    path = tmp_path / 'copy.db'
    with sqlite3.connect(path) as db:
        db.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY)')
    require_empty_jobs(path)
    with sqlite3.connect(path) as db:
        db.execute(f'INSERT INTO {table} VALUES(1)')
    with pytest.raises(ValueError, match='persistent job'):
        require_empty_jobs(path)


def test_work_directory_must_be_new_and_separate_from_source_and_candidate(tmp_path):
    candidate = tmp_path / 'candidate'
    candidate.mkdir()
    source = tmp_path / 'source.db'
    source.write_bytes(b'not-opened')
    with pytest.raises(ValueError, match='overlaps'):
        validate_paths(candidate, source, candidate / 'work')
    with pytest.raises(ValueError, match='overlaps'):
        validate_paths(candidate, source, tmp_path)
    existing = tmp_path / 'existing'
    existing.mkdir()
    with pytest.raises(FileExistsError):
        validate_paths(candidate, source, existing)
    assert validate_paths(candidate, source, tmp_path / 'new')[2] == tmp_path / 'new'
    assert source.read_bytes() == b'not-opened'


@pytest.mark.parametrize('failure', ['startup', 'job_guard'])
def test_failure_cleans_only_private_copies_and_records_no_business_rows(inputs, tmp_path, monkeypatch, failure):
    source_tree, frontend, candidate = inputs
    prepare(source_tree, frontend, candidate)
    source = tmp_path / 'business.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE records(id INTEGER PRIMARY KEY, content TEXT)')
        db.execute("INSERT INTO records VALUES(1, 'PRIVATE-ROW-MUST-NOT-BE-REPORTED')")
        if failure == 'job_guard':
            db.execute('CREATE TABLE trip_ai_jobs(id INTEGER PRIMARY KEY)')
            db.execute('INSERT INTO trip_ai_jobs VALUES(1)')
    before = source.read_bytes()
    calls = []
    def fail_start(*args):
        calls.append(True)
        raise RuntimeError('injected startup failure')
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(rehearse_release, 'serve_once', fail_start)
    work = tmp_path / 'private-rehearsal'
    report = rehearse_release.rehearse(candidate, source, work)
    assert not report['success'] and report['business_copies_removed']
    assert source.read_bytes() == before
    assert not (work / 'snapshot.db').exists() and not (work / 'data').exists()
    assert (work / 'report.json').is_file()
    assert 'PRIVATE-ROW-MUST-NOT-BE-REPORTED' not in json.dumps(report)
    assert len(calls) == (1 if failure == 'startup' else 0)
