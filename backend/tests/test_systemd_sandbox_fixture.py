import copy
import ast
from pathlib import Path

import pytest

from tools.release_plan import render_service
from tools.systemd_sandbox_fixture import crash_arguments, run_arguments, specification

ROOT = Path(__file__).resolve().parents[2]
BASE = '/tmp/travel-release-rehearsal-20261116-3d8b3a0a'
META = {'id': 'a' * 64, 'application_id': 'b' * 64, 'backend_bind_ip': '172.18.0.1', 'proxy_ip': '172.18.0.5'}
UNIT = render_service((ROOT / 'deploy/travel-release.conf.template').read_text(encoding='utf-8'),
                      META['application_id'], META['proxy_ip'], META['backend_bind_ip'])


def fixture(**kwargs):
    values = {'meta': META, 'unit_text': UNIT, 'base': BASE, 'stage': BASE + '/systemd-qa',
              'application_source': BASE + '/plan/application', 'venv_source': BASE + '/runtime/venv',
              'nonce': 'c' * 32, 'port': 28080, 'sink_port': 28081}
    values.update(kwargs)
    return specification(**values)


def test_preserves_formal_paths_and_hardening_but_maps_only_synthetic_sources():
    spec = fixture()
    assert spec['properties']['PrivateTmp'] == spec['properties']['NoNewPrivileges'] == 'true'
    assert spec['properties']['TemporaryFileSystem'] == '/opt/travel:rw,size=16m'
    assert spec['command'][:5] == [spec['formal_runtime'] + '/bin/python', '-B', '-m', 'uvicorn', 'app.main:app']
    assert spec['command'][6:9] == ['127.0.0.1', '--port', '28080']
    assert all(source.startswith(BASE + '/') for source, _ in spec['readonly_maps'] + spec['writable_maps'])
    assert dict(spec['writable_maps']) == {BASE + '/systemd-qa/work/data': '/opt/travel/app/backend/data'}
    assert spec['properties']['IPAddressDeny'] == 'any' and spec['properties']['IPAddressAllow'] == 'localhost'
    assert '--collect' in run_arguments(spec) and spec['unit_name'].startswith('travel-qa-')


@pytest.mark.parametrize('kwargs', [
    {'application_source': '/opt/travel/app'}, {'venv_source': '/opt/travel/venv'},
    {'stage': BASE + '/../escape'}, {'stage': BASE + '/space path'}, {'stage': BASE + '/x:y'},
    {'stage': BASE + '/nested/stage'}, {'base': '/tmp/not-authorized'},
    {'application_source': BASE}, {'venv_source': BASE + '/systemd-qa/nested-venv'},
    {'port': 8000}, {'sink_port': 80}, {'port': 28081}, {'nonce': 'travel'},
])
def test_unsafe_sources_names_and_production_ports_are_rejected(kwargs):
    with pytest.raises(ValueError):
        fixture(**kwargs)


@pytest.mark.parametrize('before,after', [('PrivateTmp=true', 'PrivateTmp=false'),
                                        ('NoNewPrivileges=true', 'NoNewPrivileges=false'),
                                        ('User=ubuntu', 'User=root'), ('--workers 1', '--workers 2'),
                                        ('TRAVEL_DATA_DIR=/opt/travel/app/backend/data', 'TRAVEL_DATA_DIR=/elsewhere'),
                                        ('MemoryMax=1200M', 'MemoryMax=infinity')])
def test_do_not_disable_or_silently_change_reviewed_profile(before, after):
    with pytest.raises(ValueError):
        fixture(unit_text=UNIT.replace(before, after))


def test_command_cannot_target_existing_service_name():
    spec = copy.deepcopy(fixture()); spec['unit_name'] = 'travel.service'
    with pytest.raises(ValueError):
        run_arguments(spec)


def test_namespace_executable_resolution_keeps_absolute_python_and_all_controls():
    spec = fixture()
    args = run_arguments(spec)
    assert '--property=ExecSearchPath=' + spec['formal_runtime'] + '/bin' in args
    assert args[args.index('--') + 1:] == spec['command']
    assert spec['command'][0] == spec['formal_runtime'] + '/bin/python'
    assert spec['properties']['PrivateTmp'] == spec['properties']['NoNewPrivileges'] == 'true'
    assert spec['properties']['IPAddressDeny'] == 'any'


def test_actual_systemctl_empty_array_output_does_not_break_cleanup_guard():
    tree = ast.parse((ROOT / 'backend/tools/qa_systemd_inspect.py').read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'parse_properties')
    scope = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<parse_properties>', 'exec'), scope)
    parse = scope['parse_properties']
    assert parse('Id=travel-qa-test.service\nLoadState=loaded\n')['EnvironmentFiles'] == ''
    assert parse('EnvironmentFiles=\n')['EnvironmentFiles'] == ''
    assert parse('EnvironmentFiles=/unexpected.env (ignore_errors=no)\n')['EnvironmentFiles'] != ''


def test_restart_gap_without_cgroup_is_allowed_only_with_no_pid_and_exact_state():
    tree = ast.parse((ROOT / 'backend/tools/qa_systemd_inspect.py').read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'cgroup_matches')
    scope = {}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<cgroup_matches>', 'exec'), scope)
    check=scope['cgroup_matches'];unit='travel-qa-test.service'
    props={'ControlGroup':'','ActiveState':'activating','SubState':'auto-restart'}
    assert check(props,unit,0)
    assert not check(props,unit,2345)
    assert not check({**props,'ControlGroup':'/system.slice/travel.service'},unit,0)
    assert not check({**props,'ActiveState':'active','SubState':'running'},unit,0)
    assert not check({**props,'SubState':'start-pre'},unit,0)


def test_crash_targets_only_owned_unit_main_process():
    spec=fixture()
    assert crash_arguments(spec)==['sudo','-n','systemctl','kill','--kill-whom=main','--signal=SIGKILL',spec['unit_name']]
    with pytest.raises(ValueError):
        crash_arguments({**spec,'unit_name':'travel.service'})
