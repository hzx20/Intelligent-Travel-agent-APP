"""Check the travel routes and public certificate from outside the origin."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import socket
import ssl
import struct
import subprocess


ORIGIN = '134.175.137.18'
ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / '.release-candidates/supplier-city-2026-11-20/private-plan/application/release.json'


def fetch(scheme, path, method='GET'):
    url = f'{scheme}://{ORIGIN}{path}'
    result = subprocess.run(['curl.exe', '-q', '--noproxy', '*', '-sS', '--max-time', '7',
                             '-X', method, '-w', '\n%{http_code}|%{redirect_url}', url],
                            capture_output=True, check=True, timeout=9)
    body, meta = result.stdout.rsplit(b'\n', 1)
    code, redirect = meta.decode('ascii').split('|', 1)
    return int(code), redirect, body


def public_certificate():
    context = ssl.create_default_context()
    with socket.create_connection((ORIGIN, 443), timeout=5) as connection:
        with context.wrap_socket(connection, server_hostname=ORIGIN) as peer:
            return sha256(peer.getpeercert(binary_form=True)).hexdigest()


def verifies_direct_8000_blocked(interface):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        if interface is not None:
            connection.setsockopt(socket.IPPROTO_IP, getattr(socket, 'IP_UNICAST_IF', 31), struct.pack('!I', interface))
        connection.settimeout(2)
        try:
            connection.connect((ORIGIN, 8000))
        except (TimeoutError, ConnectionRefusedError):
            return True
    return False


def verify_routes(mode, expected_leaf, interface, frontend_manifest):
    leaf = public_certificate()
    if expected_leaf and leaf != expected_leaf:
        raise ValueError('Public TLS leaf differs from the registered proxy')
    statuses = {}
    status, _, _ = fetch('http', '/')
    statuses['shared_http_root'] = status
    if status != 200:
        raise ValueError('The shared HTTP site is unavailable')
    for name, scheme, path, method, wanted in (
        ('travel_http_page', 'http', '/travel/', 'GET', 200 if mode == 'baseline' else 308),
        ('travel_http_api', 'http', '/travel/api/health', 'GET', 200 if mode == 'baseline' else 403),
        ('travel_https_page', 'https', '/travel/', 'GET', 200 if mode == 'open' else 503),
        ('travel_https_api', 'https', '/travel/api/health', 'GET', 200 if mode == 'open' else 503),
    ):
        status, redirect, body = fetch(scheme, path, method)
        statuses[name] = status
        if status != wanted or (wanted == 308 and redirect != f'https://{ORIGIN}/travel/'):
            raise ValueError(f'Unexpected {name} response: {status}')
        if name == 'travel_https_page' and mode == 'open':
            manifest = json.loads((frontend_manifest or RELEASE).read_bytes())
            expected = manifest['files']['index.html' if frontend_manifest else 'frontend/index.html']
            if sha256(body).hexdigest() != expected:
                raise ValueError('Public HTML differs from the frozen release')
        if name == 'travel_https_api' and mode == 'open' and json.loads(body) != {'status': 'ok'}:
            raise ValueError('Unexpected public backend health result')
    if mode != 'baseline' and fetch('http', '/travel/api/auth/login', 'POST')[0] != 403:
        raise ValueError('The cleartext write route is still enabled')
    if mode == 'open' and fetch('https', '/travel/api/trips')[0] != 401:
        raise ValueError('Anonymous access was not denied for private trips')
    blocked = verifies_direct_8000_blocked(interface)
    if mode != 'baseline' and not blocked:
        raise ValueError('Public TCP 8000 accepted a connection on the selected interface')
    return {'mode': mode, 'certificate_trusted_and_ip_verified': True, 'leaf_sha256': leaf,
            'public_8000_blocked': blocked, 'direct_probe_interface': interface, 'statuses': statuses}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('baseline', 'closed', 'open'), required=True)
    parser.add_argument('--expected-leaf')
    parser.add_argument('--interface-index', type=int)
    parser.add_argument('--frontend-manifest', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_routes(args.mode, args.expected_leaf, args.interface_index, args.frontend_manifest)))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Public travel check failed. {error}\n')


if __name__ == '__main__':
    main()
