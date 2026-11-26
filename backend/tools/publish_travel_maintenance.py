"""Publish the travel-only maintenance endpoint and retain it after external verification."""
import argparse
import hashlib
from http.client import HTTPConnection, HTTPSConnection, HTTPException
import json
from pathlib import Path
from queue import Empty, Queue
import re
import secrets
import shlex
import ssl
import subprocess
import threading

if __package__:
    from . import first_travel_https as first, run_acme_probe as probe
    from .publish_travel_maintenance_remote import MAINTENANCE, MODULES
else:
    import first_travel_https as first
    import run_acme_probe as probe
    from publish_travel_maintenance_remote import MAINTENANCE, MODULES


ATTEMPT = first.CANDIDATE / 'public-maintenance-current-certificate-attempt.jsonl'
SOURCE = Path(__file__).with_name('publish_travel_maintenance_remote.py')
LEAF_DIGEST = re.compile(r'[a-f0-9]{64}')


def inputs():
    logged = json.loads((first.DOCS/'budget-ip-production-issuance-2026-11-19.json').read_bytes())
    first_order = (probe.REPO/logged['execution_receipt']).read_bytes()
    if (logged['host'] != probe.TARGET or logged['unpublished_maintenance_leaf_and_503_verified'] is not True
            or logged['certificate_production_issued'] is not True
            or not LEAF_DIGEST.fullmatch(logged['certificate_fingerprint_sha256'])
            or probe.sha(first_order) != logged['execution_receipt_sha256']):
        raise ValueError('The successful formal issuance and private 503 check must match')
    issuance = json.loads(first_order.splitlines()[-1])
    if (issuance['verified'] is not True or issuance['ssh_exit'] != 0
            or issuance['report']['certificate']['fingerprint_sha256'] != logged['certificate_fingerprint_sha256']):
        raise ValueError('Historical first-issuance proof fails verification')
    _, approved = first.inputs()
    selected = {name: data for name, data in json.loads(approved).items() if name in MODULES | {'maintenance.conf'}}
    if set(selected) != MODULES | {'maintenance.conf'} or probe.sha(selected['maintenance.conf'].encode('utf-8')) != MAINTENANCE:
        raise ValueError('Maintenance bundle differs from private verification')
    remote = SOURCE.read_bytes()
    compile(remote, SOURCE.name, 'exec')
    return remote, json.dumps(selected, sort_keys=True).encode('ascii') + b'\n'


def http_state():
    statuses = []
    for path in ('/', '/travel/'):
        client = HTTPConnection(probe.TARGET, 80, timeout=4)
        try:
            client.request('GET', path)
            with client.getresponse() as response:
                statuses.append((response.status, response.getheader('Location')))
        finally:
            client.close()
    return statuses


def https_status(path, expected_leaf):
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    client = HTTPSConnection(probe.TARGET, 443, context=context, timeout=4)
    try:
        client.connect()
        identity = client.sock.getpeercert().get('subjectAltName')
        fingerprint = hashlib.sha256(client.sock.getpeercert(binary_form=True)).hexdigest()
        if identity != (('IP Address', probe.TARGET),) or fingerprint != expected_leaf:
            raise ValueError('Public certificate differs from expected IP leaf')
        client.request('GET', path)
        with client.getresponse() as response:
            return response.status, response.getheader('Retry-After'), response.getheader('Cache-Control')
    finally:
        client.close()


def public_checks(before, expected_leaf):
    for path in ('/travel/', '/travel/api/health'):
        if https_status(path, expected_leaf) != (503, '60', 'no-store'):
            raise ValueError('Expected HTTP 503 and maintenance headers')
    if https_status('/', expected_leaf)[0] != 404:
        raise ValueError('An unrelated path unexpectedly responded on 443')
    if http_state() != before:
        raise ValueError('Existing HTTP responses changed')
    return {'trusted_expected_ip_leaf': True, 'travel_and_api_503': True,
            'other_https_path_404': True, 'http_responses_unchanged': True}


def collect(pipe, stream):
    try:
        for _ in range(4):
            line = pipe.readline(2049)
            if not line:
                break
            stream.put(json.loads(line))
    except (OSError, ValueError):
        pass
    finally:
        stream.put(None)


def ready_leaf(event, tag):
    if not isinstance(event, dict) or set(event) != {'event', 'tag', 'fingerprint_sha256'}:
        return None
    leaf = event['fingerprint_sha256']
    if event['event'] == 'ready' and event['tag'] == tag and isinstance(leaf, str) and LEAF_DIGEST.fullmatch(leaf):
        return leaf
    return None


def execute(remote, payload, digest, *, attempt_path=ATTEMPT, read_http=http_state, verify=None):
    before = read_http()
    if before[1][0] != 200:
        raise ValueError('The existing HTTP travel entry is not healthy')
    tag = secrets.token_hex(8)
    command = ('/usr/bin/sudo -n /usr/bin/timeout --signal=TERM --kill-after=20s 106s /usr/bin/python3 -I -B -c '
               + shlex.quote(remote.decode('utf-8')) + ' ' + tag)
    with attempt_path.open('x', encoding='utf-8') as receipt:
        receipt.write(json.dumps({'state':'started','tag':tag,'bundle_sha256':digest})+'\n')
        receipt.flush()
        result = {'verified':False, 'commit_ack_sent':False}
        leaf = None
        try:
            with subprocess.Popen([*probe.SSH,command],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL) as ssh:
                stream = Queue()
                reader = threading.Thread(target=collect,args=(ssh.stdout,stream),daemon=True)
                reader.start()
                try:
                    ssh.stdin.write(payload)
                    ssh.stdin.flush()
                    event = stream.get(timeout=60)
                    result['readiness'] = event
                    leaf = ready_leaf(event, tag)
                    if leaf is not None:
                        result['external_checks'] = public_checks(before, leaf) if verify is None else verify(before, result)
                        ssh.stdin.write(('retain '+tag+'\n').encode('ascii'))
                        ssh.stdin.flush()
                        result['commit_ack_sent'] = True
                except (Empty, OSError, HTTPException, ValueError) as error:
                    result['probe_error'] = type(error).__name__
                finally:
                    ssh.stdin.close()
                result['ssh_exit'] = ssh.wait(timeout=80)
                reader.join(timeout=3)
                events = []
                while not stream.empty():
                    event = stream.get_nowait()
                    if event is not None:
                        events.append(event)
                result['events'] = events
                retained = next((event for event in events if isinstance(event, dict) and event.get('event') == 'retained'
                                 and event.get('tag') == tag and event.get('fingerprint_sha256') == leaf), None)
                result['verified'] = bool(result['commit_ack_sent'] and result['ssh_exit'] == 0 and retained)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            result['observation_error'] = type(error).__name__
        receipt.write(json.dumps(result)+'\n')
    print(json.dumps(result))
    return 0 if result['verified'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish',action='store_true')
    parser.add_argument('--expected-sha256')
    args = parser.parse_args()
    try:
        remote, payload = inputs()
        digest = probe.sha(remote+b'\0'+payload)
        if args.publish:
            if args.expected_sha256 != digest:
                parser.error('Exact reviewed bundle required before publication')
            return execute(remote, payload, digest)
    except (OSError, ValueError, HTTPException, KeyError) as error:
        parser.exit(1, f'Maintenance edge refused: {type(error).__name__}\n')
    print(json.dumps({'state':'review_only','bundle_sha256':digest,'bytes':len(remote)+len(payload),
                      'attempt_exists':ATTEMPT.exists()}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
