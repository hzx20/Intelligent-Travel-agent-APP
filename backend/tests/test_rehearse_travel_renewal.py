import os
from pathlib import Path
import shlex

from tools import rehearse_travel_renewal_remote as stage


def test_staging_commands_use_only_private_paths_and_the_same_test_ca(tmp_path):
    issue, renew = stage.commands(tmp_path)
    for command in (issue, renew):
        assert command[command.index('--server')+1] == stage.STAGING
        assert command[command.index('--config-dir')+1] == str(tmp_path/'config')
        assert command[command.index('--cert-name')+1] == stage.NAME
        assert '--no-directory-hooks' in command
        assert '/etc/travel-tls' not in ' '.join(command)
    assert '--ip-address' in issue and '--ip-address' not in renew
    assert '--dry-run' in renew and '--no-random-sleep-on-renew' in renew


def test_dry_run_requires_the_expected_lineage_in_success_output():
    chain = Path('/fixture/live/travel-ip/fullchain.pem')
    assert stage.confirmed_renewal(b'Test\n  '+os.fsencode(chain)+b' (success)\n',chain)
    assert not stage.confirmed_renewal(b'No simulated renewals were attempted.',chain)
    assert not stage.confirmed_renewal(b'Test\n  /other/fullchain.pem (success)\n',chain)


def test_production_renewal_relies_on_systemd_jitter_without_forcing_issuance():
    folder = Path(__file__).resolve().parents[2]/'deploy/travel-tls'
    service = (folder/'travel-ip-renew.service').read_text(encoding='utf-8')
    command, = [line.split('=',1)[1] for line in service.splitlines() if line.startswith('ExecStart=')]
    args = shlex.split(command)
    timer = (folder/'travel-ip-renew.timer').read_text(encoding='utf-8')
    assert '--no-random-sleep-on-renew' in args and 'RandomizedDelaySec=10min' in timer
    assert '--force-renewal' not in args and '--dry-run' not in args
