import json
from pathlib import Path

import pytest

from tools import acme_staging_remote as remote
from tools import run_acme_staging as staging


NONCE = 'ca' * 12


@pytest.mark.parametrize('report', [None, {'public_exact_body_match':False}, {'remote_cleanup_verified':False}, {'ssh_exit':255}])
def test_controller_never_opens_ssh_without_matching_public_and_cleanup_receipt(report, tmp_path, monkeypatch):
    monkeypatch.setattr(staging.probe,'REVIEW',tmp_path)

    def no_ssh(*args,**kwargs):
        pytest.fail('Do not connect before validating public probe and cleanup')

    monkeypatch.setattr(staging.subprocess,'run',no_ssh)
    if report is not None:
        first = {'state':'starting','payload_sha256':staging.PROBE_SHA,'probe_name':'travel-acme-probe-'+NONCE}
        end = {'state':'http01_token_verified','public_exact_body_match':True,'remote_cleanup_verified':True,'ssh_exit':0} | report
        (tmp_path/'public-token-attempt.jsonl').write_text(json.dumps(first)+'\n'+json.dumps(end)+'\n')
    with pytest.raises(ValueError,match='cleanup receipt'):
        staging.execute(b'local synthetic action only')
    assert not (tmp_path/'staging-cert-attempt.jsonl').exists()


def test_staging_command_has_no_path_to_default_ca_or_nginx_installer(tmp_path):
    cmd = remote.certbot_call(tmp_path)
    assert cmd[:2] == ('/snap/bin/certbot','certonly')
    assert cmd[cmd.index('--server')+1] == 'https://acme-staging-v02.api.letsencrypt.org/directory'
    assert cmd[cmd.index('--ip-address')+1] == '134.175.137.18'
    assert cmd[cmd.index('--preferred-profile')+1] == 'shortlived'
    assert '--webroot' in cmd and '--no-directory-hooks' in cmd and '--nginx' not in cmd and '--install' not in cmd
    assert all(Path(cmd[cmd.index(flag)+1]).parent==tmp_path for flag in ('--config-dir','--work-dir','--logs-dir','--config'))
    for old in (b'certbot 5.3.0', b'version unknown'):
        with pytest.raises(ValueError):
            remote.version(old)
    assert remote.version(b'certbot 5.4.0\n') == '5.4.0'


def test_independent_readback_only_resolves_the_exact_successful_public_read(tmp_path):
    first = {'state':'starting','payload_sha256':staging.PROBE_SHA,'probe_name':'travel-acme-probe-'+NONCE}
    end = {'state':'http01_not_verified','public_exact_body_match':True,'remote_cleanup_verified':False,'ssh_exit':1}
    receipt, readback = (tmp_path/n for n in ('public-token-attempt.jsonl','public-token-cleanup-readback.json'))
    receipt.write_text(json.dumps(first)+'\n'+json.dumps(end)+'\n')
    expected = {'probe_name':first['probe_name'],'probe_receipt_sha256':staging.probe.sha(receipt.read_bytes()),
                'file_absent':True,'only_probe_directories_absent':True,'original_config_unchanged':True,'read_only':True}
    assert not staging.probe_accepted(receipt)
    for alternate in ({'probe_name':'travel-acme-probe-'+'0'*24},{'file_absent':False},{'probe_receipt_sha256':'a'*64}):
        readback.write_text(json.dumps(expected | alternate))
        assert not staging.probe_accepted(receipt)
    readback.write_text(json.dumps(expected))
    assert staging.probe_accepted(receipt)
    receipt.write_text(json.dumps(first)+'\n'+json.dumps(end | {'public_exact_body_match':False})+'\n')
    assert not staging.probe_accepted(receipt)


@pytest.mark.parametrize('issuer,san,ok', [('(STAGING) CA','IP Address:134.175.137.18',True),
                                          ('(STAGING) CA','DNS:134.175.137.18',False),
                                          ('(STAGING) CA','IP Address:134.175.137.18, DNS:other.test',False),
                                          ('Public CA','IP Address:134.175.137.18',False)])
def test_staging_certificate_needs_exact_ip_san_and_staging_issuer(issuer,san,ok):
    assert remote.cert_is_exact(f'issuer={issuer}\nX509v3 Subject Alternative Name:\n   {san}\n'.encode()) is ok
    assert remote.cert_is_exact(f'issuer={issuer}\nX509v3 Subject Alternative Name: critical\n   {san}\n'.encode()) is ok


def test_terminal_success_requires_ordered_cleanup_and_unmodified_config_evidence():
    events = [
        {'event':'phase','name':'install_official_certbot'}, {'event':'certbot','version':'5.10.0'},
        {'event':'phase','name':'request_staging_only'}, {'event':'temporary_state','name':f'travel-acme-test-{NONCE}-12345abc'},
        {'event':'certbot_command_completed'}, {'event':'public_cleanup','verified':True},
        {'event':'verified','issuer_is_staging':True,'exact_ip_san':True,'temporary_state_removed':True,
         'shared_config_still_matches':True,'installed_certbot_retained':True,'public_tls_enabled':False,'elapsed_staging_seconds':24.3},
    ]

    def check(rows, code=0):
        return staging.classify('\n'.join(json.dumps(e) for e in rows).encode(),code,NONCE)

    assert check(events)['state']=='staging_verified'
    assert check(events[:-2]+events[-1:])['state']=='not_verified'
    assert check(events[:-1]+[events[-1] | {'shared_config_still_matches':False}])['state']=='not_verified'
    assert check(events,255)['state']=='not_verified'
    assert 'raw_private_log' not in str(check(events[:-1]+[events[-1] | {'raw_private_log':'secret'}]))


def test_install_continuation_requires_matching_done_change_and_no_prior_issuance(tmp_path):
    assert remote.installed_change_done('2', b'2   Done  today at 20:55 CST  today at 20:58 CST   Install "certbot" snap\n')
    assert not remote.installed_change_done('2', b'2   Doing  today at 20:55 CST  -   Install "certbot" snap\n')
    assert not remote.installed_change_done('2', b'3   Done  today at 20:55 CST  today at 20:58 CST   Install "certbot" snap\n')
    marker = tmp_path/'staging-cert-attempt.jsonl'
    first = {'state':'starting','probe_receipt_sha256':'a'*64,
             'action_sha256':'41ab3687db6a11fb1cfdf1693de464a0a8142e842ee0c203e5391d79f7349e21'}
    journal = [{'event':'phase','name':'install_official_certbot'},
               {'event':'failed','phase':'install_official_certbot','error_type':'TimeoutExpired'}]
    result = {'state':'not_verified','ssh_exit':1,'events':journal}
    marker.write_text(json.dumps(first)+'\n'+json.dumps(result)+'\n')
    assert staging.installation_only_timed_out(marker,'a'*64)
    assert not staging.installation_only_timed_out(marker,'b'*64)
    result['events'].insert(1,{'event':'phase','name':'request_staging_only'})
    marker.write_text(json.dumps(first)+'\n'+json.dumps(result)+'\n')
    assert not staging.installation_only_timed_out(marker,'a'*64)


def test_reverification_requires_clean_prior_attempt_and_never_overwrites_it(tmp_path, monkeypatch):
    marker = tmp_path/'public-token-attempt.jsonl'
    original = tmp_path/'staging-cert-attempt.jsonl'
    prior = tmp_path/'staging-after-install-attempt.jsonl'
    report = tmp_path/'report.json'
    probe_start = {'state':'starting','payload_sha256':staging.PROBE_SHA,'probe_name':'travel-acme-probe-'+NONCE}
    probe_end = {'state':'http01_token_verified','ssh_exit':0,'public_exact_body_match':True,'remote_cleanup_verified':True}
    marker.write_text(json.dumps(probe_start)+'\n'+json.dumps(probe_end)+'\n')
    install_start = {'state':'starting','probe_receipt_sha256':staging.probe.sha(marker.read_bytes()),
                     'action_sha256':'41ab3687db6a11fb1cfdf1693de464a0a8142e842ee0c203e5391d79f7349e21'}
    install_end = {'state':'not_verified','ssh_exit':1,'events':[{'event':'phase','name':'install_official_certbot'},
                    {'event':'failed','phase':'install_official_certbot','error_type':'TimeoutExpired'}]}
    original.write_text(json.dumps(install_start)+'\n'+json.dumps(install_end)+'\n')
    prior_start = {'reused_snap_change':'2'}
    prior_end = {'state':'not_verified','ssh_exit':1,'events':[{'event':'phase','name':'request_staging_only'},
                                                                   {'event':'public_cleanup','verified':True}]}
    prior.write_text(json.dumps(prior_start)+'\n'+json.dumps(prior_end)+'\n')
    archived = prior.read_bytes()
    prior_sha = staging.probe.sha(archived)
    monkeypatch.setattr(staging,'FIRST_STAGING_RECEIPT_SHA',prior_sha)
    monkeypatch.setattr(staging,'FIRST_STAGING_REPORT',report)
    monkeypatch.setattr(staging.probe,'REVIEW',tmp_path)
    monkeypatch.setattr(staging.probe,'REPO',tmp_path)
    calls = []

    def simulated_ssh(args,**kwargs):
        assert args[-1].endswith(' 2')  # Reuse the installed snap, never run initial installation mode.
        calls.append(args)
        return staging.subprocess.CompletedProcess(args,1,b'',b'ignored')

    monkeypatch.setattr(staging.subprocess,'run',simulated_ssh)
    without_cleanup = {'staging':{'receipt_sha256':prior_sha,'verification_passed':False,'events':prior_end['events']},
                       'read_only_postcheck':{'certbot_version':'certbot 5.10.0','temporary_state_absent':False,
                           'webroot_challenge_directory_absent':True,'shared_config_unchanged':True,'global_certificate_state_absent':True}}
    report.write_text(json.dumps(without_cleanup))
    with pytest.raises(ValueError,match='cleanup evidence'):
        staging.execute(b'example payload',reverify=True)
    assert calls == []
    without_cleanup['read_only_postcheck']['temporary_state_absent'] = True
    report.write_text(json.dumps(without_cleanup))
    assert staging.execute(b'example payload',reverify=True)==1
    assert prior.read_bytes()==archived
    new = tmp_path/'staging-reverification-attempt.jsonl'
    assert json.loads(new.read_text().splitlines()[0])['prior_order_receipt_sha256']==prior_sha
    with pytest.raises(FileExistsError):
        staging.execute(b'example payload',reverify=True)
    assert len(calls)==1
