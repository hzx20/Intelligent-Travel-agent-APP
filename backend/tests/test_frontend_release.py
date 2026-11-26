import json
from pathlib import Path

import pytest

from tools import frontend_release as release


def build(root, version, *, main_name=None):
    (root / 'assets/nested').mkdir(parents=True)
    (root / '.vite').mkdir()
    main = main_name or f'assets/main-{version}.js'
    lazy = f'assets/nested/lazy-{version}.js'
    (root / main).write_text(f'main {version}', encoding='utf-8')
    (root / lazy).write_text(f'lazy {version}', encoding='utf-8')
    (root / 'index.html').write_text(f'<title>{version}</title><script type="module" src="/travel/{main}"></script>', encoding='utf-8')
    (root / '.vite/manifest.json').write_text(json.dumps({
        'index.html': {'file': main, 'isEntry': True, 'dynamicImports': ['lazy']},
        'lazy': {'file': lazy},
    }), encoding='utf-8')
    return root


def candidate(tmp_path, version, **kwargs):
    source = build(tmp_path / ('dist-' + version), version, **kwargs)
    target = tmp_path / ('candidate-' + version)
    return target, release.prepare(source, target)


def pair(target):
    return tuple(release.read_optional(target / name) for name in ('index.html', '.vite/manifest.json'))


def test_prepare_only_manifest_files_and_verify_rejects_tampering(tmp_path):
    source = build(tmp_path / 'dist', 'one')
    (source / '.env').write_text('DO-NOT-PACKAGE')
    (source / 'assets/unreferenced.js').write_text('old')
    output = tmp_path / 'candidate'
    metadata = release.prepare(source, output)
    assert release.verify(output) == metadata
    assert not (output / '.env').exists() and not (output / 'assets/unreferenced.js').exists()
    with pytest.raises(FileExistsError):
        release.prepare(source, output)
    (output / 'assets/main-one.js').write_text('tampered')
    with pytest.raises(ValueError, match='checksum'):
        release.verify(output)


def test_publish_switch_rollback_and_replay_keep_both_lazy_chunks(tmp_path):
    first, a = candidate(tmp_path, 'one')
    second, b = candidate(tmp_path, 'two')
    target = tmp_path / 'web/travel'
    target.mkdir(parents=True)
    (target / 'unrelated.txt').write_text('keep')
    release.publish(first, target)
    old = pair(target)
    release.publish(second, target)
    assert pair(target) == pair(second)
    assert release.publish(second, target) == b['id']
    assert release.status(target, b['id'])['matches_published']
    release.rollback(target, b['id'])
    assert pair(target) == old
    assert release.status(target, b['id'])['matches_previous']
    assert release.rollback(target, b['id']) == b['id']
    for version in ('one', 'two'):
        assert (target / f'assets/nested/lazy-{version}.js').read_text() == f'lazy {version}'
    assert (target / 'unrelated.txt').read_text() == 'keep'
    with pytest.raises(ValueError, match='reactivated'):
        release.publish(second, target)
    assert release.status(target, a['id'])['phase'] == 'committed'


def test_stale_rollback_and_publish_do_not_replace_later_release(tmp_path):
    first, _ = candidate(tmp_path, 'one')
    second, b = candidate(tmp_path, 'two')
    third, _ = candidate(tmp_path, 'three')
    target = tmp_path / 'live'
    release.publish(first, target)
    release.publish(second, target)
    release.publish(third, target)
    before = pair(target)
    with pytest.raises(ValueError, match='stale rollback'):
        release.rollback(target, b['id'])
    with pytest.raises(ValueError, match='stale publication'):
        release.publish(second, target)
    assert pair(target) == before


def test_immutable_collision_never_changes_current_entry(tmp_path):
    first, _ = candidate(tmp_path, 'one', main_name='assets/shared.js')
    second, _ = candidate(tmp_path, 'two', main_name='assets/shared.js')
    target = tmp_path / 'live'
    release.publish(first, target)
    before = pair(target)
    with pytest.raises(ValueError, match='immutable filename collision'):
        release.publish(second, target)
    assert pair(target) == before
    assert (target / 'assets/shared.js').read_text() == 'main one'


def test_entry_write_failure_restores_metadata_and_retry_uses_original_backup(tmp_path, monkeypatch):
    first, _ = candidate(tmp_path, 'one')
    second, b = candidate(tmp_path, 'two')
    target = tmp_path / 'live'
    release.publish(first, target)
    before = pair(target)
    original = release.atomic
    def fail_entry(path, data, **kwargs):
        if path == target / 'index.html':
            raise OSError('injected entry failure')
        return original(path, data, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(release, 'atomic', fail_entry)
        with pytest.raises(OSError, match='injected'):
            release.publish(second, target)
    assert pair(target) == before
    assert release.status(target, b['id'])['phase'] == 'prepared'
    release.publish(second, target)
    release.rollback(target, b['id'])
    assert pair(target) == before


def test_lost_commit_ack_retry_does_not_overwrite_previous_snapshot(tmp_path, monkeypatch):
    first, _ = candidate(tmp_path, 'one')
    second, b = candidate(tmp_path, 'two')
    target = tmp_path / 'live'
    release.publish(first, target)
    before = pair(target)
    original = release.save_state
    def fail_ack(path, state):
        if state['phase'] == 'committed':
            raise OSError('injected lost acknowledgement')
        return original(path, state)
    with monkeypatch.context() as patch:
        patch.setattr(release, 'save_state', fail_ack)
        with pytest.raises(OSError, match='injected'):
            release.publish(second, target)
    assert pair(target) == pair(second)
    assert release.status(target, b['id'])['phase'] == 'prepared'
    release.publish(second, target)
    release.rollback(target, b['id'])
    assert pair(target) == before


@pytest.mark.parametrize('fault', ['entry', 'ack'])
def test_interrupted_rollback_can_be_repeated(tmp_path, monkeypatch, fault):
    first, _ = candidate(tmp_path, 'one')
    second, b = candidate(tmp_path, 'two')
    target = tmp_path / 'live'
    release.publish(first, target)
    before = pair(target)
    release.publish(second, target)
    original_atomic, original_save = release.atomic, release.save_state
    def fail_entry(path, data, **kwargs):
        if path == target / 'index.html':
            raise OSError('injected rollback entry failure')
        return original_atomic(path, data, **kwargs)
    def fail_ack(path, state):
        if state['phase'] == 'rolled_back':
            raise OSError('injected rollback ack failure')
        return original_save(path, state)
    with monkeypatch.context() as patch:
        patch.setattr(release, 'atomic', fail_entry if fault == 'entry' else original_atomic)
        patch.setattr(release, 'save_state', fail_ack if fault == 'ack' else original_save)
        with pytest.raises(OSError, match='injected'):
            release.rollback(target, b['id'])
    release.rollback(target, b['id'])
    assert pair(target) == before


def test_first_deploy_rollback_and_replay_cannot_erase_newer_release(tmp_path):
    first, a = candidate(tmp_path, 'one')
    second, _ = candidate(tmp_path, 'two')
    target = tmp_path / 'live'
    release.publish(first, target)
    release.rollback(target, a['id'])
    assert pair(target) == (None, None)
    release.publish(second, target)
    before = pair(target)
    release.rollback(target, a['id'])
    assert pair(target) == before


def test_existing_lock_and_external_metadata_changes_fail_closed(tmp_path):
    first, _ = candidate(tmp_path, 'one')
    second, _ = candidate(tmp_path, 'two')
    target = tmp_path / 'live'
    release.publish(first, target)
    lock = target / release.CONTROL / 'publish.lock'
    lock.write_text('existing-owner')
    with pytest.raises(FileExistsError):
        release.publish(second, target)
    assert lock.read_text() == 'existing-owner'
    lock.unlink()
    # Seed a prepared attempt, then simulate another tool changing its baseline.
    (target / 'assets/main-two.js').write_text('collision')
    with pytest.raises(ValueError):
        release.publish(second, target)
    (target / '.vite/manifest.json').write_text('{}')
    with pytest.raises(ValueError, match='metadata changed'):
        release.publish(second, target)


@pytest.mark.parametrize('name', ['assets/../../escape.js', 'assets/..\\escape.js', '/absolute.js'])
def test_candidate_metadata_cannot_escape_directory(tmp_path, name):
    path, metadata = candidate(tmp_path, 'one')
    metadata['files'][name] = release.digest(b'escape')
    metadata['id'] = release.identifier(metadata['files'])
    (path / 'release.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError):
        release.verify(path)


def test_candidate_tamper_is_detected_before_target_is_created(tmp_path):
    path, _ = candidate(tmp_path, 'one')
    (path / 'unexpected.txt').write_text('extra')
    target = tmp_path / 'live'
    with pytest.raises(ValueError, match='Unexpected'):
        release.publish(path, target)
    assert not target.exists()


def test_nested_source_or_target_is_rejected(tmp_path):
    source = build(tmp_path / 'dist', 'one')
    with pytest.raises(ValueError, match='separate'):
        release.prepare(source, source / 'candidate')
    path, _ = candidate(tmp_path, 'two')
    with pytest.raises(ValueError, match='separate'):
        release.publish(path, path / 'live')
