"""Review or run the one-time isolated staging renewal check for travel."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

if __package__:
    from . import first_travel_https as first, run_acme_probe as probe
else:
    import first_travel_https as first
    import run_acme_probe as probe


SOURCE = Path(__file__).with_name('rehearse_travel_renewal_remote.py')
RECEIPT = first.CANDIDATE / 'staging-renewal-attempt.jsonl'


def inputs():
    _, already_reviewed = first.inputs()
    original = json.loads(already_reviewed)
    helpers = {key:original[key] for key in ('acme_probe_remote.py','acme_staging_remote.py')}
    source = SOURCE.read_bytes()
    compile(source,SOURCE.name,'exec')
    payload = json.dumps(helpers,sort_keys=True).encode('ascii')
    if len(payload)>=20*1024:
        raise ValueError('Unexpected size of staged helper sources')
    return source,payload,probe.sha(source+b'\0'+payload)


def execute(source,payload,digest):
    remote = ('/usr/bin/timeout --kill-after=8s 156s /usr/bin/python3 -I -B -c '
              + shlex.quote(source.decode('utf-8')))
    with RECEIPT.open('x',encoding='utf-8') as receipt:
        receipt.write(json.dumps({'state':'started','bundle_sha256':digest,'staging_only':True})+'\n')
        receipt.flush()
        try:
            process = subprocess.run([*probe.SSH,remote],input=payload,capture_output=True,timeout=177)
            events = [json.loads(line) for line in process.stdout.splitlines()]
            cleanups = [event for event in events if event.get('event')=='public_cleanup']
            valid = (process.returncode==0 and events and events[-1].get('event')=='verified'
                     and cleanups==[{'event':'public_cleanup','verified':True}]*2)
            report = {'ssh_exit':process.returncode,'verified':bool(valid),'events':events}
        except (OSError,ValueError,subprocess.SubprocessError) as error:
            report = {'verified':False,'state':'unconfirmed','error_type':type(error).__name__}
        receipt.write(json.dumps(report)+'\n')
    print(json.dumps(report))
    return 0 if report['verified'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--expected-sha256')
    args = parser.parse_args()
    try:
        source,payload,digest = inputs()
        if args.execute:
            if args.expected_sha256!=digest:
                parser.error('Exact reviewed staging bundle required')
            return execute(source,payload,digest)
    except (OSError,ValueError,KeyError) as error:
        parser.exit(1,f'Staging renewal review refused: {error}\n')
    print(json.dumps({'state':'review_only','bundle_sha256':digest,'attempt_exists':RECEIPT.exists()}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
