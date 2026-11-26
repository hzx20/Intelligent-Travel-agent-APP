import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from test_frontend_release import build
from tools import release_candidate as application
from tools import release_plan as plan
from tools.frontend_release import prepare as prepare_frontend

ROOT = Path(__file__).resolve().parents[2]
SOURCE = '''events { worker_connections 64; }
http { include mime.types; server { listen 80; root /usr/share/nginx/html;
location / { try_files $uri $uri/ /index.html; }
location ^~ /travel/ { try_files $uri $uri/ /travel/index.html; }
} }
'''


def wheel_bytes(*, tag='py3-none-any', version='0.28.1', metadata_name='httpx'):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('httpx-0.28.1.dist-info/METADATA', 'Metadata-Version: 2.1\nName: ' + metadata_name + '\nVersion: ' + version + '\n')
        archive.writestr('httpx-0.28.1.dist-info/WHEEL', 'Wheel-Version: 1.0\nTag: ' + tag + '\n')
    return stream.getvalue()


@pytest.fixture()
def inputs(tmp_path):
    source = tmp_path / 'source'
    for name in application.REQUIRED:
        if name.startswith('frontend/'):
            continue
        p = source / name; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("raise RuntimeError('APPLICATION MUST NOT BE IMPORTED')\n" if name.endswith('.py') else 'example\n', encoding='utf-8')
    (source / 'backend/requirements.txt').write_text('httpx==0.28.1\n')
    (source / 'deploy/nginx-travel.conf').write_bytes((ROOT / 'deploy/nginx-travel.conf').read_bytes())
    for name in application.TOOLS:
        (source / 'backend/tools' / name).write_bytes((ROOT / 'backend/tools' / name).read_bytes())
    frontend = tmp_path / 'front'
    prepare_frontend(build(tmp_path / 'dist', 'plan'), frontend)
    candidate = tmp_path / 'application'
    app = application.prepare(source, frontend, candidate)
    wheelhouse = tmp_path / 'wheelhouse'; wheelhouse.mkdir()
    filename = 'httpx-0.28.1-py3-none-any.whl'
    data = wheel_bytes(); (wheelhouse / filename).write_bytes(data)
    checksum = hashlib.sha256(data).hexdigest()
    ops = tmp_path / 'ops'; ops.mkdir()
    index = ops / 'wheels.json'
    index.write_text(json.dumps({'complete': True, 'errors': {}, 'packages': {'httpx': {
        'name': 'httpx', 'version': '0.28.1', 'extras': [], 'filename': filename, 'bytes': len(data), 'sha256': checksum,
        'source_url': 'https://files.pythonhosted.org/packages/synthetic/' + filename,
        'metadata_url': 'https://pypi.org/pypi/httpx/0.28.1/json', 'metadata_sha256': 'a' * 64}}}))
    lock = ops / 'linux.lock'; lock.write_text('httpx==0.28.1 --hash=sha256:' + checksum + '\n')
    tags = ops / 'tags.json'; tags.write_text(json.dumps(['cp312-cp312-manylinux_2_39_x86_64', 'py3-none-any']))
    nginx = ops / 'nginx.conf'; nginx.write_text(SOURCE)
    profile = ops / 'service.conf'; profile.write_bytes((ROOT / 'deploy/travel-release.conf.template').read_bytes())
    runbook = ops / 'runbook.md'; runbook.write_text('Review only. No production deployment.\n')
    return {'application': candidate, 'wheelhouse': wheelhouse, 'wheel_index': index, 'lock_file': lock,
            'target_tags': tags, 'nginx_source': nginx, 'service_profile': profile, 'runbook': runbook,
            'output': tmp_path / 'plan', 'proxy_ip': '172.18.0.5', 'backend_ip': '172.18.0.1'}


def rehash(root):
    p = root / 'plan.json'; meta = json.loads(p.read_text(encoding='utf-8'))
    meta['files'] = {n: plan.digest((root / n).read_bytes()) for n in meta['files']}
    meta['id'] = plan.identifier({k: v for k, v in meta.items() if k != 'id'})
    p.write_text(json.dumps(meta), encoding='utf-8')
    return meta


def test_build_binds_application_wheels_service_and_nginx_without_mutating_inputs(inputs):
    before = application.verify(inputs['application'])
    meta = plan.prepare(**inputs)
    assert plan.verify(inputs['output'], expected_id=meta['id']) == meta
    assert application.verify(inputs['application']) == before
    assert meta['application_id'] == before['id'] and meta['frontend_id'] == before['frontend_id']
    unit = (inputs['output'] / 'ops/travel.service').read_text(encoding='utf-8')
    assert '[Unit]' in unit and '[Service]' in unit and '[Install]' in unit
    assert '/opt/travel/releases/' + before['id'] + '/backend' in unit
    assert '--host 172.18.0.1' in unit and '--forwarded-allow-ips 172.18.0.5' in unit
    assert not (inputs['output'] / 'application/backend/data').exists()
    with pytest.raises(FileExistsError):
        plan.prepare(**inputs)


def test_copied_plan_verifier_is_self_contained_and_does_not_import_app(inputs):
    meta = plan.prepare(**inputs)
    root = inputs['output']
    result = subprocess.run([sys.executable, '-B', str(root / 'tools/release_plan.py'), 'verify',
                             '--plan', str(root), '--expected-id', meta['id']], cwd=root,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['plan_verified'] and not json.loads(result.stdout)['production_ready']
    assert not (root / 'application/backend/data').exists()


@pytest.mark.parametrize('change', ['bytes', 'extra-secret', 'empty-data-directory', 'missing-wheel'])
def test_verifier_rejects_changed_or_unselected_files_and_directories(inputs, change):
    plan.prepare(**inputs); root = inputs['output']
    if change == 'bytes':
        (root / 'ops/travel.service').write_text('changed')
    elif change == 'extra-secret':
        (root / '.env').write_text('NOT-FOR-PACKAGING')
    elif change == 'empty-data-directory':
        (root / 'application/backend/data').mkdir()
    else:
        next((root / 'wheelhouse').iterdir()).unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        plan.verify(root)


@pytest.mark.parametrize('change', ['normal', 'maintenance', 'unit', 'source-origin', 'profile', 'tags', 'helper'])
def test_relations_are_checked_even_if_manifest_is_rehashed(inputs, change):
    plan.prepare(**inputs); root = inputs['output']
    if change in ('normal', 'maintenance'):
        p = root / ('ops/nginx-' + change + '.conf'); p.write_bytes(p.read_bytes() + b'\n# changed\n')
    elif change == 'unit':
        p = root / 'ops/travel.service'; p.write_text(p.read_text().replace('--host 172.18.0.1', '--host 0.0.0.0'))
    elif change == 'source-origin':
        p = root / 'ops/wheels.json'; value = json.loads(p.read_text()); value['packages']['httpx']['source_url'] = 'https://example.test/not-official.whl'; p.write_text(json.dumps(value))
    elif change == 'profile':
        p = root / 'ops/service-profile.conf.template'; p.write_text(p.read_text().replace('TRAVEL_SHARED_CACHE=0', 'TRAVEL_SHARED_CACHE=1'))
    elif change == 'tags':
        (root / 'ops/target-tags.json').write_text(json.dumps(['cp312-cp312-win_amd64']))
    else:
        p = root / 'tools/release_candidate.py'; p.write_bytes(p.read_bytes() + b'\n# different helper\n')
    rehash(root)
    with pytest.raises(ValueError):
        plan.verify(root)


def test_separately_reviewed_identity_rejects_resigned_changes(inputs):
    meta = plan.prepare(**inputs); root = inputs['output']
    p = root / 'ops/RELEASE_RUNBOOK.md'; p.write_bytes(p.read_bytes() + b'New review required.\n')
    changed = rehash(root)
    assert plan.verify(root)['id'] == changed['id']
    with pytest.raises(ValueError, match='separately reviewed identity'):
        plan.verify(root, expected_id=meta['id'])


@pytest.mark.parametrize('value', ['httpx>=0.28.1', 'httpx==0.28.1', '-r other.lock', 'httpx==0.28.1 --hash=sha256:' + 'x' * 64])
def test_bad_lock_is_refused_before_output_is_created(inputs, value):
    inputs['lock_file'].write_text(value)
    with pytest.raises(ValueError):
        plan.prepare(**inputs)
    assert not inputs['output'].exists()


@pytest.mark.parametrize('field,value', [('proxy_ip', '0.0.0.0'), ('backend_ip', '134.175.137.18'),
                                        ('backend_ip', '127.0.0.1'), ('backend_ip', '172.18.0.5')])
def test_unreviewed_network_targets_are_refused(inputs, field, value):
    inputs[field] = value
    with pytest.raises(ValueError):
        plan.prepare(**inputs)
    assert not inputs['output'].exists()


@pytest.mark.parametrize('change', ['version', 'name', 'platform', 'checksum'])
def test_wheel_embedded_identity_and_platform_must_match_lock(inputs, change):
    p = next(inputs['wheelhouse'].iterdir())
    if change == 'version':
        data = wheel_bytes(version='99.0')
    elif change == 'name':
        data = wheel_bytes(metadata_name='other-package')
    elif change == 'platform':
        data = wheel_bytes(tag='cp312-cp312-win_amd64')
    else:
        data = p.read_bytes() + b'changed'
    p.write_bytes(data)
    if change != 'checksum':
        value = json.loads(inputs['wheel_index'].read_text()); entry = value['packages']['httpx']
        entry['sha256'] = plan.digest(data); entry['bytes'] = len(data)
        inputs['wheel_index'].write_text(json.dumps(value))
        inputs['lock_file'].write_text('httpx==0.28.1 --hash=sha256:' + entry['sha256'] + '\n')
    with pytest.raises(ValueError):
        plan.prepare(**inputs)
    assert not inputs['output'].exists()


def test_direct_extras_and_version_cannot_be_lost_in_lock(inputs):
    root = inputs['application']
    (root / 'backend/requirements.txt').write_text('httpx[socks]==0.28.1\n')
    metadata = json.loads((root / 'release.json').read_text(encoding='utf-8'))
    metadata['files']['backend/requirements.txt'] = plan.digest((root / 'backend/requirements.txt').read_bytes())
    metadata['id'] = plan.identifier({k: v for k, v in metadata.items() if k != 'id'})
    (root / 'release.json').write_text(json.dumps(metadata), encoding='utf-8')
    with pytest.raises(ValueError, match='direct requirements/extras'):
        plan.prepare(**inputs)


def test_output_must_be_separate_from_application_and_wheels(inputs):
    for key in ('application', 'wheelhouse'):
        with pytest.raises(ValueError, match='overlaps'):
            plan.prepare(**{**inputs, 'output': inputs[key] / 'plan'})
