import json
from pathlib import Path
import platform
import subprocess
import sys

import pytest

from test_frontend_release import build
from tools import release_candidate as release
from tools.frontend_release import prepare as prepare_frontend


@pytest.fixture()
def inputs(tmp_path):
    source = tmp_path / 'repo'
    for name in release.REQUIRED:
        if name.startswith('frontend/'):
            continue
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('VALUE = 1\n' if name.endswith('.py') else 'example\n')
    (source / 'backend/requirements.txt').write_text('httpx==0.28.1\n')
    frontend = tmp_path / 'frontend-candidate'
    prepare_frontend(build(tmp_path / 'dist', 'one'), frontend)
    return source, frontend, tmp_path / 'candidate'


def test_bundle_excludes_secrets_data_tests_and_unselected_tools(inputs):
    source, frontend, output = inputs
    excluded = ('.env', 'backend/data/app.db', 'backend/data/.jwt_secret',
                'backend/tests/test_secret.py', 'backend/tools/make_admin.py',
                'backend/app/__pycache__/cached.py', 'backend/app/.local.py', 'backend/app/private.env')
    for name in excluded:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('DO-NOT-PACKAGE')
    metadata = release.prepare(source, frontend, output)
    assert release.verify(output) == metadata
    assert not set(excluded) & metadata['files'].keys()
    assert all(not (output / name).exists() for name in excluded)
    assert metadata['frontend_id'] == json.loads((frontend / 'release.json').read_text())['id']
    with pytest.raises(FileExistsError):
        release.prepare(source, frontend, output)


@pytest.mark.parametrize('change', ['code', 'extra', 'missing', 'frontend_identity'])
def test_bundle_verifier_rejects_corruption_and_mismatched_frontend(inputs, change):
    source, frontend, output = inputs
    metadata = release.prepare(source, frontend, output)
    if change == 'code':
        (output / 'backend/app/main.py').write_text('CHANGED = 1')
    elif change == 'extra':
        (output / '.env').write_text('not-allowed')
    elif change == 'missing':
        (output / 'backend/app/security.py').unlink()
    else:
        metadata['frontend_id'] = '0' * 64
        metadata['id'] = release.identifier({k: v for k, v in metadata.items() if k != 'id'})
        (output / 'release.json').write_text(json.dumps(metadata))
    with pytest.raises((ValueError, FileNotFoundError)):
        release.verify(output)


def test_prepare_refuses_invalid_python_and_overlapping_output(inputs):
    source, frontend, output = inputs
    with pytest.raises(ValueError, match='overlaps'):
        release.prepare(source, frontend, source / 'backend/candidate')
    (source / 'backend/app/main.py').write_text('def invalid(')
    with pytest.raises(SyntaxError):
        release.prepare(source, frontend, output)
    assert not output.exists()


@pytest.mark.parametrize('asset', ['.env', 'cache.db', 'private.pem'])
def test_private_looking_assets_are_rejected_even_when_referenced(inputs, tmp_path, asset):
    source, _, output = inputs
    dist = build(tmp_path / 'unsafe-dist', 'unsafe')
    (dist / 'assets' / asset).write_text('not-for-release')
    manifest_path = dist / '.vite/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['private'] = {'file': 'assets/' + asset}
    manifest_path.write_text(json.dumps(manifest))
    frontend = tmp_path / 'unsafe-frontend'
    prepare_frontend(dist, frontend)
    with pytest.raises(ValueError, match='unsupported candidate files'):
        release.prepare(source, frontend, output)
    assert not output.exists()


@pytest.mark.parametrize('requirements', ['httpx>=0.28', '-r other.txt', 'httpx==0.28.1\nHTTPX==0.28.1', ''])
def test_requirements_are_exact_pins_without_hidden_includes(inputs, requirements):
    source, frontend, output = inputs
    (source / 'backend/requirements.txt').write_text(requirements)
    with pytest.raises(ValueError):
        release.prepare(source, frontend, output)
    assert not output.exists()


def test_runtime_inspection_is_read_only_and_reports_missing_dependency(inputs, monkeypatch):
    source, frontend, output = inputs
    release.prepare(source, frontend, output)
    assert release.runtime_report(output)['pinned_runtime_matches']
    original = release.importlib.metadata.version
    def missing(name):
        if name == 'httpx':
            raise release.importlib.metadata.PackageNotFoundError(name)
        return original(name)
    monkeypatch.setattr(release.importlib.metadata, 'version', missing)
    report = release.runtime_report(output)
    assert report['packages']['httpx'] is None and not report['pinned_runtime_matches']
    assert release.verify(output)
    assert not (output / 'backend/data').exists()


def test_candidate_verifier_runs_without_importing_application_or_creating_data(inputs):
    source, frontend, output = inputs
    # Copy the actual verification tools into this small fixture, not its app.
    backend = Path(__file__).resolve().parents[1]
    for name in release.TOOLS:
        (source / 'backend/tools' / name).write_bytes((backend / 'tools' / name).read_bytes())
    (source / 'backend/app/main.py').write_text("raise RuntimeError('APPLICATION MUST NOT BE IMPORTED')")
    release.prepare(source, frontend, output)
    result = subprocess.run([sys.executable, '-B', str(output / 'backend/tools/release_candidate.py'),
                             'verify', '--candidate', str(output)], cwd=output, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['candidate_verified']
    assert not (output / 'backend/data').exists()


def test_python_environment_difference_is_explicit(inputs, monkeypatch):
    source, frontend, output = inputs
    release.prepare(source, frontend, output)
    monkeypatch.setattr(platform, 'python_version_tuple', lambda: ('0', '0', '0'))
    report = release.runtime_report(output)
    assert not report['pinned_runtime_matches']
    assert any('Python major/minor' in error for error in report['errors'])
