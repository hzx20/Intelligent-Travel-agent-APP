"""Exercise the production adapter without a Docker daemon, ACME or real secrets."""
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import ssl
import subprocess
import sys

import pytest

from test_travel_tls_check import materials
from tools import travel_tls_edge as edge


def target(mode='normal'):
    return {
        'id': 'a' * 64,
        'image': 'sha256:' + 'b' * 64,
        'network': 'travel-test-net',
        'ip': '172.18.0.42',
        'config_sha256': next(digest for digest, name in edge.REVIEWED_CONFIG.items() if name == mode),
    }


def binding(source, destination):
    return {'Type': 'bind', 'Source': str(source), 'Destination': destination, 'RW': False}


def metadata(spec, *, published=True):
    return {
        'Id': spec['id'], 'Image': spec['image'], 'Name': edge.NAME,
        'Path': '/usr/sbin/nginx', 'Args': ['-c', edge.TARGET_CONF, '-g', 'daemon off;'],
        'Config': {'Labels': {'travel.edge.role': edge.ROLE}},
        'State': {'Running': True, 'Restarting': False},
        'HostConfig': {
            'ReadonlyRootfs': True, 'Privileged': False, 'NetworkMode': spec['network'],
            'PortBindings': {'443/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '443'}]} if published else None,
            'Tmpfs': {'/tmp': 'size=24m'},
        },
        'NetworkSettings': {'Networks': {spec['network']: {'IPAddress': spec['ip']}}},
        'Mounts': [
            binding(edge.CONF, edge.TARGET_CONF), binding(edge.STORE, edge.TARGET_STORE),
            binding(edge.STATIC, edge.TARGET_STATIC),
        ],
    }


@pytest.mark.parametrize('field,replacement', [
    ('id', 'continew-web'), ('image', 'nginx:latest'), ('network', 'host'),
    ('ip', '127.0.0.1'), ('config_sha256', '0' * 64),
])
def test_target_requires_reviewed_digest_and_exact_identifiers(field, replacement):
    spec = target()
    assert edge.parse_target(json.dumps(spec)) == spec
    with pytest.raises(ValueError):
        edge.parse_target(json.dumps({**spec, field: replacement}))


@pytest.mark.parametrize('drift', ('name', 'network', 'direct_key', 'volume', 'writable', 'privileged', 'missing_static'))
def test_distinct_or_overprivileged_containers_are_refused(drift):
    spec = target()
    info = metadata(spec)
    edge.verify_instance(info, spec)
    if drift == 'name':
        info['Name'] = '/continew-web'
    elif drift == 'network':
        info['NetworkSettings']['Networks']['default'] = {'IPAddress': '172.16.0.1'}
    elif drift == 'direct_key':
        info['Mounts'].append(binding('/tmp/key', f'{edge.TARGET_STORE}/current/privkey.pem'))
    elif drift == 'volume':
        info['Mounts'].append({'Type': 'volume', 'Destination': '/etc/ssl'})
    elif drift == 'writable':
        info['Mounts'][0]['RW'] = True
    elif drift == 'privileged':
        info['HostConfig']['Privileged'] = True
    else:
        info['Mounts'].pop()
    with pytest.raises(ValueError):
        edge.verify_instance(info, spec)


def test_maintenance_check_requires_unpublished_edge():
    spec = target('maintenance')
    info = metadata(spec, published=False)
    info['Mounts'].pop()
    edge.verify_instance(info, spec, unpublished=True)
    with pytest.raises(ValueError):
        edge.verify_instance(metadata(spec), spec, unpublished=True)
    with pytest.raises(ValueError):
        edge.verify_instance(info, spec)
    with pytest.raises(ValueError):
        edge.Edge(spec, unpublished=True).reload()


def test_docker_exec_rechecks_identity_and_both_digests(monkeypatch):
    spec = target()
    review = b'synthetic reviewed configuration'
    digest = hashlib.sha256(review).hexdigest()
    spec['config_sha256'] = digest
    monkeypatch.setitem(edge.REVIEWED_CONFIG, digest, 'normal')
    state = {'host': review, 'inside': digest, 'metadata': metadata(spec), 'status': b'200'}
    calls = []
    leaf = b'mock DER for command verification'
    pem = ssl.DER_cert_to_PEM_cert(leaf).encode()

    def docker(*args):
        calls.append(args)
        if args[0] == 'inspect':
            assert args == ('inspect', '--type', 'container', spec['id'])
            return json.dumps([state['metadata']]).encode()
        assert args[:2] == ('exec', spec['id'])
        if args[2] == '/usr/bin/sha256sum':
            return state['inside'].encode() + b'  config'
        if args[2] == '/usr/bin/curl':
            assert args[3] == '-q' and '--cacert' in args and '-k' not in args
            assert f'{edge.tls.TARGET}:443:127.0.0.1:443' in args
            return state['status'] + b'\n' + pem
        return b''

    monkeypatch.setattr(edge, 'docker', docker)
    monkeypatch.setattr(edge, 'read_root_file', lambda *args: state['host'])
    adapter = edge.Edge(spec)
    adapter.check()
    adapter.reload()
    assert adapter.peer() == hashlib.sha256(leaf).hexdigest()
    signals = [args for args in calls if 'reload' in args]
    for field, wrong in (('host', b'changed'), ('inside', '0'*64), ('metadata', {})):
        with monkeypatch.context() as patch:
            patch.setitem(state, field, wrong)
            with pytest.raises(ValueError):
                adapter.reload()
    assert [args for args in calls if 'reload' in args] == signals
    assert len(signals) == 1
    state['status'] = b'503'
    with pytest.raises(ValueError, match='reviewed mode'):
        adapter.peer()


def test_first_prepare_selects_once_and_first_verification_never_signals(tmp_path, monkeypatch):
    selected = []

    class Store:
        root = tmp_path
        locked = staticmethod(nullcontext)

        def stage(self, certificate, key, validate):
            assert (certificate, key) == (b'synthetic leaf', b'synthetic key')
            assert validate is edge.validate_pair
            return 'first'

        def point(self, name):
            selected.append(name)
            (self.root/'current').touch()

        def current(self):
            return selected[-1]

    staged = edge.bootstrap(Store(), pair_lookup=lambda: (b'synthetic leaf', b'synthetic key'))
    assert staged == {'state': 'first_pair_prepared', 'edge_started': False, 'public_verified': False}
    with pytest.raises(ValueError, match='already selected'):
        edge.bootstrap(Store(), pair_lookup=lambda: pytest.fail('should not read Certbot again'))
    assert selected == ['first']

    adapter = edge.Edge(target('maintenance'), unpublished=True)
    checks = []
    monkeypatch.setattr(adapter, 'check', lambda: checks.append('nginx -t'))
    monkeypatch.setattr(adapter, 'peer', lambda: checks.append('verified leaf') or 'first')
    monkeypatch.setattr(adapter, 'reload', lambda: pytest.fail('first verification must never signal nginx'))
    assert edge.verify_first(Store(), adapter) == {'state': 'first_maintenance_leaf_verified', 'public_verified': False}
    assert checks == ['nginx -t', 'verified leaf', 'verified leaf']


def test_mixed_certbot_generations_are_refused_before_read(monkeypatch):
    monkeypatch.setattr(edge.tls, 'local_renewal_policy', lambda: None)
    monkeypatch.setattr(edge.tls, 'checked_lineage_path',
                        lambda root, basename, **options: Path('privkey2.pem' if options else 'fullchain1.pem'))
    monkeypatch.setattr(edge, 'read_root_file', lambda *args: pytest.fail('unexpected read of mixed generations'))
    with pytest.raises(ValueError, match='Lineage changed'):
        edge.saved_pair()


def test_validate_pair_with_real_synthetic_certificates(materials, monkeypatch, tmp_path):
    openssl, ca, certificates = materials
    leaf, key = certificates[0]
    fullchain = tmp_path/'fullchain.pem'
    fullchain.write_bytes(leaf.read_bytes() + ca.read_bytes())

    def verify(*args):
        arguments = [str(ca) if arg == '/etc/ssl/certs/ca-certificates.crt' else arg for arg in args]
        return subprocess.run([openssl, *arguments], check=True, capture_output=True, timeout=8).stdout

    monkeypatch.setattr(edge.tls, 'run_openssl', verify)
    edge.validate_pair(fullchain, key)
    with pytest.raises(ValueError, match='do not match'):
        edge.validate_pair(fullchain, certificates[1][1])
    extra_san_leaf, extra_san_key = certificates[2]
    fullchain.write_bytes(extra_san_leaf.read_bytes() + ca.read_bytes())
    with pytest.raises(ValueError, match='SAN'):
        edge.validate_pair(fullchain, extra_san_key)


def test_isolated_cli_defaults_to_review_and_apply_is_required():
    for args, code in (([], 0), (['prepare-first'], 2), (['reconcile'], 2)):
        run = subprocess.run([sys.executable, '-I', '-B', edge.__file__, *args],
                             capture_output=True, timeout=8)
        assert run.returncode == code
        if code == 0:
            assert json.loads(run.stdout)['state'] == 'review_only'
