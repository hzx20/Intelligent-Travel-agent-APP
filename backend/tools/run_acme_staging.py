"""Stage a test-only IP certificate after a verified one-time public file probe."""
import argparse
import base64
import json
from pathlib import Path
import re
import secrets
import shlex
import subprocess

if __package__:
    from . import run_acme_probe as probe
else:
    import run_acme_probe as probe


STAGING_SCRIPT = Path(__file__).with_name('acme_staging_remote.py')
PROBE_SHA = 'fb7c503949f39f90d64d2b4faf2693c96b87f481ad09a7a678805b2d4335db02'
FIRST_STAGING_RECEIPT_SHA = 'da8ca9aa670abacc12af9843333170c9c4f14e949a4a87b5804adecda4433155'
FIRST_STAGING_REPORT = probe.REPO / 'docs/verification/budget-ip-acme-staging-2026-11-19.json'


def package():
    source, host_source = STAGING_SCRIPT.read_bytes(), probe.reviewed_source()
    if probe.sha(host_source) != PROBE_SHA:
        raise ValueError('Webroot probe payload differs from approved evidence')
    compile(source, STAGING_SCRIPT.name, 'exec')
    encoded_host, encoded_action = (base64.b64encode(p).decode('ascii') for p in (host_source, source))
    return ('import base64,sys,types\n'
            'host=types.ModuleType("acme_probe_remote")\n'
            'sys.modules[host.__name__]=host\n'
            f'exec(compile(base64.b64decode({encoded_host!r}),"host-check","exec"),host.__dict__)\n'
            f'exec(compile(base64.b64decode({encoded_action!r}),"staging-action","exec"),globals())\n').encode('ascii')


def probe_accepted(path):
    if not path.is_file() or path.is_symlink():
        return False
    try:
        raw = path.read_bytes()
        if len(raw) > 2048:
            return False
        first, end = [json.loads(line) for line in raw.splitlines()]
        if not (isinstance(first,dict) and first.keys() == {'state','probe_name','payload_sha256'} and
                first['state']=='starting' and first['payload_sha256']==PROBE_SHA and
                isinstance(first['probe_name'],str) and re.fullmatch('travel-acme-probe-[a-f0-9]{24}',first['probe_name']) and
                isinstance(end,dict) and end.get('public_exact_body_match') is True and type(end.get('ssh_exit')) is int):
            return False
        if end == {'state':'http01_token_verified','public_exact_body_match':True,'remote_cleanup_verified':True,'ssh_exit':0}:
            return end['remote_cleanup_verified'] is True
        # A successful exact public read can be paired with independent proof
        # that the same file and both originally absent directories are gone.
        if end != {'state':'http01_not_verified','public_exact_body_match':True,'remote_cleanup_verified':False,'ssh_exit':1}:
            return False
        readback = path.with_name('public-token-cleanup-readback.json')
        if readback.is_symlink() or not readback.is_file() or readback.stat().st_size > 2048:
            return False
        evidence = json.loads(readback.read_bytes())
        flags = ('file_absent','only_probe_directories_absent','original_config_unchanged','read_only')
        return (isinstance(evidence,dict) and evidence.keys() == {'probe_name','probe_receipt_sha256',*flags} and
                evidence['probe_name'] == first['probe_name'] and evidence['probe_receipt_sha256'] == probe.sha(raw) and
                all(evidence[k] is True for k in flags))
    except (OSError, ValueError, KeyError):
        return False


def classify(output, code, nonce, *, resume=False):
    events = []
    if len(output) > 3200:
        return {'state':'not_verified','sha_events':probe.sha(output),'ssh_exit':code}
    for row in output.splitlines():
        event = probe.parse_event(row)
        if event is None:
            break
        keys = event.keys()
        phase = event.get('event')
        if phase == 'phase' and keys == {'event','name'} and event['name'] in ('install_official_certbot','reuse_completed_install','request_staging_only'):
            events.append(event)
        elif phase == 'certbot' and keys == {'event','version'} and isinstance(event['version'],str) and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',event['version']):
            events.append(event)
        elif phase == 'temporary_state' and keys == {'event','name'} and isinstance(event['name'],str) and re.fullmatch(f'travel-acme-test-{nonce}-[a-z0-9_]+',event['name']):
            events.append(event)
        elif phase == 'public_cleanup' and keys == {'event','verified'} and type(event['verified']) is bool:
            events.append(event)
        elif event == {'event':'certbot_command_completed'}:
            events.append(event)
        elif phase == 'failed' and keys == {'event','phase','error_type'} and event['phase'] in ('preflight','install_official_certbot','reuse_completed_install','request_staging_only','inspect_staging_certificate','verify_staging_renewal_metadata','verify_unchanged_shared_configuration') and event['error_type'] in ('RuntimeError','ValueError','TimeoutError','OSError','CalledProcessError','TimeoutExpired','FileNotFoundError','PermissionError'):
            events.append(event)
        elif phase == 'verified':
            proof = {'event':'verified','issuer_is_staging':True,'exact_ip_san':True,'temporary_state_removed':True,
                     'shared_config_still_matches':True,'installed_certbot_retained':True,'public_tls_enabled':False}
            elapsed = event.get('elapsed_staging_seconds')
            if (type(elapsed) in (float,int) and 0 <= elapsed <= 115 and keys == proof.keys() | {'elapsed_staging_seconds'}
                    and all(event[k] is v for k,v in proof.items() if type(v) is bool)):
                events.append(event)
            else:
                break
        else:
            break
    passed = (code == 0 and len(events)==7 and len(output.splitlines())==len(events) and
              [e['event'] for e in events] == ['phase','certbot','phase','temporary_state','certbot_command_completed','public_cleanup','verified'] and
              events[0]['name']==('reuse_completed_install' if resume else 'install_official_certbot') and events[2]['name']=='request_staging_only' and
              tuple(map(int,events[1]['version'].split('.'))) >= (5,4,0) and events[-2]['verified'])
    return {'state':'staging_verified' if passed else 'not_verified','events':events,'ssh_exit':code}


def installation_only_timed_out(path, probe_hash):
    if not path.is_file() or path.is_symlink() or path.stat().st_size>4096:
        return False
    try:
        begin,end = [json.loads(x) for x in path.read_bytes().splitlines()]
        return (isinstance(begin,dict) and begin.get('state')=='starting' and begin.get('probe_receipt_sha256')==probe_hash
                and begin.get('action_sha256')=='41ab3687db6a11fb1cfdf1693de464a0a8142e842ee0c203e5391d79f7349e21'
                and end=={'state':'not_verified','ssh_exit':1,'events':[
                    {'event':'phase','name':'install_official_certbot'},
                    {'event':'failed','phase':'install_official_certbot','error_type':'TimeoutExpired'}]})
    except (OSError,ValueError):
        return False


def first_staging_cleanup_verified(receipt, report_path):
    try:
        if any(not p.is_file() or p.is_symlink() or p.stat().st_size > 12000 for p in (receipt,report_path)):
            return False
        raw = receipt.read_bytes()
        if probe.sha(raw) != FIRST_STAGING_RECEIPT_SHA:
            return False
        start, terminal = [json.loads(l) for l in raw.splitlines()]
        previous = json.loads(report_path.read_bytes())
        prior = previous['staging']
        readback = previous['read_only_postcheck']
        return (start['reused_snap_change']=='2' and terminal['state']=='not_verified' and terminal['ssh_exit']==1 and
                {'event':'phase','name':'request_staging_only'} in terminal['events'] and
                {'event':'public_cleanup','verified':True} in terminal['events'] and
                prior['receipt_sha256']==FIRST_STAGING_RECEIPT_SHA and prior['verification_passed'] is False and
                prior['events']==terminal['events'] and readback['certbot_version']=='certbot 5.10.0' and
                all(readback[k] is True for k in ('temporary_state_absent','webroot_challenge_directory_absent',
                                                   'shared_config_unchanged','global_certificate_state_absent')))
    except (KeyError, OSError, TypeError, ValueError):
        return False


def execute(payload, installed_change=None, *, reverify=False):
    evidence = probe.REVIEW/'public-token-attempt.jsonl'
    if not probe_accepted(evidence):
        raise ValueError('Successful public challenge and cleanup receipt required before contacting host')
    original = probe.REVIEW/'staging-cert-attempt.jsonl'
    prior_order = probe.REVIEW/'staging-after-install-attempt.jsonl'
    if reverify:
        if installed_change is not None or not first_staging_cleanup_verified(prior_order,FIRST_STAGING_REPORT):
            raise ValueError('Reverification requires the exact original order and independent cleanup evidence')
        installed_change = '2'
    if installed_change is not None and not (re.fullmatch('[0-9]{1,12}',installed_change) and
                                             installation_only_timed_out(original,probe.sha(evidence.read_bytes()))):
        raise ValueError('Original attempt must prove the timeout occurred before requesting a certificate')
    nonce = secrets.token_hex(12)
    remote = '/usr/bin/timeout --signal=TERM --kill-after=8s 245s /usr/bin/python3 -I -B -c ' + shlex.quote(payload.decode('ascii')) + ' ' + nonce
    if installed_change is not None:
        remote += ' ' + installed_change
    receipt = ((probe.REVIEW/'staging-reverification-attempt.jsonl') if reverify else
               prior_order if installed_change is not None else original)
    with receipt.open('x', encoding='utf-8') as out:
        start = {'state':'starting','attempt':nonce,'probe_receipt_sha256':probe.sha(evidence.read_bytes()),
                 'action_sha256':probe.sha(payload)}
        if json.loads(evidence.read_bytes().splitlines()[1])['state'] == 'http01_not_verified':
            start['probe_readback_sha256'] = probe.sha(evidence.with_name('public-token-cleanup-readback.json').read_bytes())
        if installed_change is not None:
            start.update(reused_snap_change=installed_change, original_install_receipt_sha256=probe.sha(original.read_bytes()))
        if reverify:
            start.update(prior_order_receipt_sha256=probe.sha(prior_order.read_bytes()),
                         prior_cleanup_report_sha256=probe.sha(FIRST_STAGING_REPORT.read_bytes()))
        out.write(json.dumps(start)+'\n')
        out.flush()
        try:
            result = subprocess.run([*probe.SSH, remote],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL,timeout=270)
        except subprocess.TimeoutExpired as error:
            summary = {**classify(error.stdout or b'',None,resume=installed_change is not None),'state':'unconfirmed','reason':'observation_timeout'}
        except OSError:
            summary = {'state':'unconfirmed','reason':'ssh_launch_failed'}
        else:
            summary = classify(result.stdout,result.returncode,nonce,resume=installed_change is not None)
        out.write(json.dumps(summary)+'\n')
    print(json.dumps({**summary,'receipt':receipt.relative_to(probe.REPO).as_posix(),'production_ready':False}))
    return 0 if summary['state']=='staging_verified' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true',help='Only with separate approval to install Certbot and request a staging certificate')
    parser.add_argument('--expected-sha256')
    routes = parser.add_mutually_exclusive_group()
    routes.add_argument('--continue-installed-change', metavar='SNAP_CHANGE')
    routes.add_argument('--reverify-staging', action='store_true', help='One new staging order; requires new approval')
    args = parser.parse_args()
    try:
        payload = package()
        if args.execute:
            if args.expected_sha256 != probe.sha(payload):
                parser.error('Reviewed action fingerprint required')
            return execute(payload,args.continue_installed_change,reverify=args.reverify_staging)
    except (ValueError,OSError) as error:
        parser.exit(1,f'Staging refused: {error}\n')
    print(json.dumps({'state':'local_staging_reviewed','remote_contacted':False,'action_sha256':probe.sha(payload),
                      'verified_probe_available':probe_accepted(probe.REVIEW/'public-token-attempt.jsonl'),
                      'prior_order_cleanup_verified':first_staging_cleanup_verified(probe.REVIEW/'staging-after-install-attempt.jsonl',FIRST_STAGING_REPORT),
                      'reverification_attempt_exists':(probe.REVIEW/'staging-reverification-attempt.jsonl').exists(),
                      'production_ready':False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
