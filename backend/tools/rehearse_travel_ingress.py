"""Review or run the approved isolated packet test for the travel backend policy."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

if __package__:
    from . import run_acme_probe as probe
    from .rehearse_travel_ingress_remote import PROBE, REVIEWED
else:
    import run_acme_probe as probe
    from rehearse_travel_ingress_remote import PROBE, REVIEWED


SOURCE = Path(__file__).with_name('rehearse_travel_ingress_remote.py')
RULES = probe.REPO / 'deploy/travel-tls/backend-ingress.nft.example'
RECEIPT = probe.REPO / '.release-candidates/budget-https-load-2026-11-19/https-ip-entry-candidate/ingress-packet-attempt.jsonl'
WRAPPER = '''
import json,subprocess,sys

def snapshot():
    result=subprocess.run(('sudo','-n','/usr/sbin/nft','--stateless','--json','list','ruleset'),
                          stdin=subprocess.DEVNULL,capture_output=True,timeout=8,check=True)
    return result.stdout

before=snapshot()
rules=sys.stdin.buffer.read(16384)
try:
    result=subprocess.run(('sudo','-n','/usr/bin/timeout','--kill-after=6s','46s','/usr/bin/unshare','--net','--',
                           '/usr/bin/python3','-I','-B','-c',sys.argv[1]),input=rules,capture_output=True,timeout=55)
    report=json.loads(result.stdout)
    status=result.returncode
except (OSError,ValueError,subprocess.SubprocessError) as error:
    report={'state':'not_verified','error_type':type(error).__name__}
    status=1
unchanged=before==snapshot()
print(json.dumps({'isolated_result':report,'isolated_exit':status,'host_ruleset_unchanged':unchanged}))
raise SystemExit(status or int(not unchanged))
'''


def inputs():
    rules = RULES.read_bytes()
    source = SOURCE.read_bytes()
    if probe.sha(rules) != REVIEWED:
        raise ValueError('The exact reviewed nftables rules are required')
    compile(source, SOURCE.name, 'exec')
    compile(WRAPPER, '<host-readback>', 'exec')
    compile(PROBE, '<packet-probe>', 'exec')
    script = '/usr/bin/timeout 72s /usr/bin/python3 -I -B -c ' + shlex.quote(WRAPPER) + ' ' + shlex.quote(source.decode('utf-8'))
    return script, rules, probe.sha(script.encode('utf-8')+b'\0'+rules)


def execute(script, rules, digest):
    with RECEIPT.open('x', encoding='utf-8') as transcript:
        transcript.write(json.dumps({'state':'started','bundle_sha256':digest})+'\n')
        transcript.flush()
        try:
            completed = subprocess.run([*probe.SSH,script],input=rules,capture_output=True,timeout=82)
            report = json.loads(completed.stdout)
            report['ssh_exit'] = completed.returncode
            report['verified'] = (completed.returncode == 0 and report['host_ruleset_unchanged'] is True
                                  and report['isolated_result']['state'] == 'isolated_packets_verified')
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            report = {'state':'unconfirmed','error_type':type(error).__name__,'verified':False}
        transcript.write(json.dumps(report)+'\n')
    print(json.dumps(report))
    return 0 if report['verified'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--expected-sha256')
    args = parser.parse_args()
    try:
        script, rules, digest = inputs()
        if args.execute:
            if args.expected_sha256 != digest:
                parser.error('Exact reviewed packet rehearsal bundle required')
            return execute(script,rules,digest)
    except (OSError, ValueError) as error:
        parser.exit(1,f'Ingress rehearsal refused: {error}\n')
    print(json.dumps({'state':'review_only','bundle_sha256':digest,'attempt_exists':RECEIPT.exists()}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
