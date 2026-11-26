"""Exercise dedicated proxy promotion and compensation without touching Docker."""
from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import switch_travel_https_mode as switch


def initial_spec(config_hash):
    return {'id': 'a' * 64, 'image': 'sha256:' + 'b' * 64, 'network': 'travel-edge', 'ip': '172.31.240.2',
            'config_sha256': config_hash}


def test_docker_arguments_pin_mounts_network_and_privileges():
    spec = initial_spec(switch.MODES['maintenance'])
    normal = switch.docker_arguments(spec, 'normal', 'tag')
    assert normal[normal.index('--pull') + 1] == 'never'
    assert normal[normal.index('--network') + 1] == 'travel-edge'
    assert normal[normal.index('--publish') + 1] == '0.0.0.0:443:443/tcp'
    assert normal[normal.index('--restart') + 1] == 'unless-stopped'
    assert normal[normal.index('--cap-drop') + 1] == 'ALL' and '--privileged' not in normal
    static = f'type=bind,source={switch.edge.STATIC},target={switch.edge.TARGET_STATIC},readonly'
    assert static in normal and static not in switch.docker_arguments(spec, 'maintenance', 'tag')
    preview = switch.docker_arguments(spec, 'normal', 'preview', syntax_config=Path('/private/normal.conf'))
    assert preview[preview.index('--network') + 1] == 'none' and '--rm' in preview
    assert '--publish' not in preview and '--detach' not in preview and '-t' in preview


@pytest.mark.parametrize('scenario', ('success', 'normal_health_failure', 'marker_sync_failure'))
def test_only_marked_normal_mode_is_started_and_failure_recovers_maintenance(monkeypatch, tmp_path, scenario):
    old = b'synthetic maintenance directives'
    updated = b'synthetic normal directives'
    mode_hashes = {'maintenance': switch.sha(old), 'normal': switch.sha(updated)}
    spec = initial_spec(mode_hashes['maintenance'])
    pin = tmp_path / 'pin.json'
    conf = tmp_path / 'live.conf'
    candidate = tmp_path / 'normal.conf'
    previous = tmp_path / 'maintenance.conf'
    record = tmp_path / 'opening.json'
    data = json.dumps(spec).encode()
    pin.write_bytes(data)
    for path, content in ((conf, old), (candidate, updated), (previous, old)):
        path.write_bytes(content)
    labels = {}
    running = {spec['id']}
    messages = []
    monkeypatch.setattr(switch, 'MODES', mode_hashes)
    monkeypatch.setattr(switch.edge, 'PIN', pin)
    monkeypatch.setattr(switch.edge, 'CONF', conf)
    monkeypatch.setattr(switch.edge, 'read_root_file', lambda p, _: p.read_bytes())
    monkeypatch.setattr(switch.edge, 'parse_target', json.loads)
    monkeypatch.setattr(switch.edge, 'Edge', lambda selected: SimpleNamespace(guarded=lambda: selected['id']))
    monkeypatch.setattr(switch.edge.rotate, 'private_directory', lambda _: None)
    monkeypatch.setattr(switch.edge.rotate, 'Store', lambda _: SimpleNamespace(locked=nullcontext))
    monkeypatch.setattr(switch.readiness, 'verify_while_closed', lambda: messages.append('gate_check'))
    monkeypatch.setattr(switch, 'verify_static', lambda: messages.append('static_check'))
    monkeypatch.setattr(switch.registration, 'require_quiet_renewal', lambda: None)

    def replace(path, before, after):
        assert path.read_bytes() == before
        path.write_bytes(after)

    def marker(path, application, snapshot):
        assert application == switch.readiness.APP and snapshot == 'c' * 64
        with path.open('xb') as stream:
            stream.write(b'possible write marker')
        if scenario == 'marker_sync_failure':
            raise OSError('marker could not be synced')

    def docker(*args):
        if args[:2] == ('rm', '-f'):
            running.remove(args[2])
            return b''
        assert args[0] == 'run' and '--network' in args and args[args.index('--network')+1] == 'none'
        assert conf.read_bytes() == old and not record.exists()
        messages.append('syntax_check')
        return b''

    def inventory(rule):
        if rule.startswith('name='):
            return list(running)
        return [cid for cid in running if labels.get(cid) == rule.removeprefix(f'label={switch.LABEL}=')]

    def published(old_spec, mode, tag):
        assert record.exists() and not running
        cid = ('d' if mode == 'normal' else 'e') * 64
        labels[cid] = tag
        running.add(cid)
        messages.append(mode)
        assert switch.sha(conf.read_bytes()) == mode_hashes[mode]
        return {**old_spec, 'id': cid, 'config_sha256': mode_hashes[mode]}

    def confirm(target, store, old_pin, pins):
        if target['id'] == 'd' * 64 and scenario == 'normal_health_failure':
            raise ValueError('normal HTTPS probe failure')
        pin.write_text(json.dumps(target), encoding='ascii')
        return {'container_id': target['id']}

    monkeypatch.setattr(switch.registration, 'replace_target', replace)
    monkeypatch.setattr(switch.opening, 'mark_opening', marker)
    monkeypatch.setattr(switch.edge, 'docker', docker)
    monkeypatch.setattr(switch, 'containers', inventory)
    monkeypatch.setattr(switch, 'published', published)
    monkeypatch.setattr(switch, 'confirm_and_store', confirm)
    report = switch.replace_running('normal', candidate, previous, record, 'c' * 64, switch.sha(data))
    assert messages[:3] == ['gate_check', 'static_check', 'syntax_check'] and record.exists()
    if scenario == 'success':
        assert report['state'] == 'normal_edge_verified' and running == {'d' * 64}
    else:
        assert report['state'] == 'maintenance_restored' and running == {'e' * 64} and conf.read_bytes() == old
        if scenario == 'marker_sync_failure':
            assert 'normal' not in messages
