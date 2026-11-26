"""Review or run one authorized ACME HTTP-01 webroot probe, without installing Certbot."""
import argparse
import hashlib
from http.client import HTTPConnection, HTTPException
import json
from pathlib import Path
from queue import Empty, Queue
import secrets
import shlex
import subprocess
import threading


REPO = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).with_name('acme_probe_remote.py')
REVIEW = REPO / '.release-candidates/budget-https-load-2026-11-19/https-ip-acme-existing-webroot'
TARGET = '134.175.137.18'
REFERENCE = '300ba0f22714e4942ac5e7ddeb6d51269f0f6b4557e78c3364e04ff50e589f73'
SSH = ('ssh', '-T', '-oBatchMode=yes', '-oStrictHostKeyChecking=yes', '-oConnectTimeout=8',
       '-oClearAllForwardings=yes', '-oForwardAgent=no', 'ubuntu@'+TARGET)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def reviewed_source():
    payload = SOURCE.read_bytes()
    # Windows source bytes may have CRLF. Validate and send the same UTF-8 text.
    compile(payload, SOURCE.name, 'exec')
    proof = json.loads((REVIEW/'report.json').read_bytes())
    if proof['host'] != TARGET or proof['challenge_source_sha256'] != REFERENCE or proof['acme_container_root'] != '/usr/share/nginx/html':
        raise ValueError('ACME review differs from the inspected webroot')
    return payload


def public_body_matches(name, expected, *, connect=HTTPConnection):
    connection = connect(TARGET, 80, timeout=5)
    try:
        connection.request('GET', '/.well-known/acme-challenge/'+name, headers={'Cache-Control':'no-cache', 'Accept-Encoding':'identity'})
        with connection.getresponse() as response:
            return response.status == 200 and response.read(len(expected)+1) == expected
    except (OSError, HTTPException):
        return False
    finally:
        connection.close()


def parse_event(line):
    if not line or len(line) > 512:
        return None
    try:
        event = json.loads(line)
        return event if isinstance(event, dict) else None
    except (ValueError, UnicodeError):
        return None


def reader(pipe, queue):
    try:
        for _ in range(4):
            line = pipe.readline(513)
            if not line:
                break
            queue.put(parse_event(line))
    finally:
        queue.put(None)


def evaluate(process, nonce, *, fetch=public_body_matches):
    name = 'travel-acme-probe-' + nonce
    expected = f'travel-acme-probe:{nonce}\n'.encode('ascii')
    queue = Queue(maxsize=5)
    watcher = threading.Thread(target=reader, args=(process.stdout,queue), daemon=True)
    watcher.start()
    wanted = {'event':'ready','name':name,'body_sha256':sha(expected)}
    exact = False
    observed = None
    confirmed = None
    try:
        observed = queue.get(timeout=24)
        if observed == wanted:
            exact = fetch(name, expected)
            if exact:
                process.stdin.write('exact-match\n')
                process.stdin.flush()
    except (Empty, OSError):
        pass
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
    try:
        confirmed = queue.get(timeout=18) if observed == wanted else None
    except Empty:
        pass
    try:
        code = process.wait(timeout=9)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=4)
        code = None
    watcher.join(timeout=2)
    good_cleanup = confirmed == {'event':'cleanup','verified': True,'caller_confirmed_exact_body':exact}
    return {'state':'http01_token_verified' if exact and good_cleanup and code == 0 else 'http01_not_verified',
            'public_exact_body_match': exact, 'remote_cleanup_verified': good_cleanup, 'ssh_exit': code}


def run_once(payload):
    nonce = secrets.token_hex(12)
    remote = '/usr/bin/timeout --signal=TERM --kill-after=5s 35s /usr/bin/python3 -I -B -c ' + shlex.quote(payload.decode('utf-8')) + ' ' + nonce
    receipt = REVIEW/'public-token-attempt.jsonl'
    with receipt.open('x', encoding='utf-8') as handle:
        initial = {'state':'starting', 'probe_name':'travel-acme-probe-'+nonce, 'payload_sha256':sha(payload)}
        handle.write(json.dumps(initial)+'\n')
        handle.flush()
        try:
            with subprocess.Popen([*SSH, remote], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, encoding='utf-8') as p:
                p.stdin.reconfigure(newline='\n')  # Keep the acknowledgment identical on Windows and Linux.
                result = evaluate(p, nonce)
        except OSError:
            result = {'state':'http01_not_verified','ssh_exit':None,'remote_cleanup_verified':False}
        handle.write(json.dumps(result)+'\n')
    print(json.dumps({**result,'receipt':receipt.relative_to(REPO).as_posix(), 'certbot_installed':False,'certificate_issued':False}))
    return 0 if result['state'] == 'http01_token_verified' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Execute only within an approved one-time public file test')
    parser.add_argument('--expected-sha256')
    args = parser.parse_args()
    payload = reviewed_source()
    if args.execute:
        if args.expected_sha256 != sha(payload):
            parser.error('Approved probe source checksum required')
        return run_once(payload)
    print(json.dumps({'state':'local_probe_reviewed','probe_bytes':len(payload),'probe_sha256':sha(payload),
                      'production_changed':False,'remote_contacted':False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
