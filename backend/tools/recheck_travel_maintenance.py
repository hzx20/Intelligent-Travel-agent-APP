"""Recheck the authorized public maintenance edge from a selected Windows interface."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import functools
import hashlib
from http.client import HTTPException, HTTPResponse
import json
import socket
import ssl
import struct
import subprocess
import sys

if __package__:
    from . import publish_travel_maintenance as publication
else:
    import publish_travel_maintenance as publication


TARGET = publication.probe.TARGET
ATTEMPT = publication.first.CANDIDATE / 'public-maintenance-current-certificate-route-recheck.jsonl'
IP_UNICAST_IF = 31
HOST_PROBE = '''
import hashlib,json,socket,ssl
from http.client import HTTPResponse

target = '134.175.137.18'
stage = 'tcp'
try:
    with socket.create_connection(('127.0.0.1',443),timeout=4) as tcp:
        stage = 'tls'
        with ssl.create_default_context().wrap_socket(tcp,server_hostname=target) as secure:
            leaf = hashlib.sha256(secure.getpeercert(binary_form=True)).hexdigest()
            stage = 'http'
            secure.sendall(b'GET /travel/ HTTP/1.1\\r\\nHost: 134.175.137.18\\r\\nConnection: close\\r\\n\\r\\n')
            with HTTPResponse(secure) as response:
                response.begin()
                report = {'stage':'complete','status':response.status,'fingerprint_sha256':leaf}
except (OSError,ValueError) as error:
    report = {'stage':stage,'error_type':type(error).__name__}
print(json.dumps(report))
'''


def observed(path, port, interface=None, *, expected_leaf=None):
    stage = 'tcp'
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
            tcp.settimeout(4)
            if interface is not None:
                tcp.setsockopt(socket.IPPROTO_IP, IP_UNICAST_IF, struct.pack('!I', interface))
            tcp.connect((TARGET, port))
            if port == 443:
                stage = 'tls'
                context = ssl.create_default_context()
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                with context.wrap_socket(tcp, server_hostname=TARGET) as secure:
                    identities = secure.getpeercert().get('subjectAltName')
                    digest = hashlib.sha256(secure.getpeercert(binary_form=True)).hexdigest()
                    if identities != (('IP Address', TARGET),) or digest != expected_leaf:
                        return {'stage': 'tls', 'error_type': 'UnexpectedLeaf'}
                    return read_response(secure, path)
            return read_response(tcp, path)
    except (OSError, ValueError) as error:
        return {'stage': stage, 'error_type': type(error).__name__}


def read_response(sock, path):
    try:
        sock.sendall(f'GET {path} HTTP/1.1\r\nHost: {TARGET}\r\nConnection: close\r\n\r\n'.encode('ascii'))
        with HTTPResponse(sock) as response:
            response.begin()
            return {'stage': 'complete', 'status': response.status, 'retry_after': response.getheader('Retry-After'),
                    'no_store': response.getheader('Cache-Control') == 'no-store',
                    'location_sha256': hashlib.sha256((response.getheader('Location') or '').encode()).hexdigest()}
    except (OSError, HTTPException, ValueError) as error:
        return {'stage': 'http', 'error_type': type(error).__name__}


def loopback():
    destination = '/usr/bin/timeout 9s /usr/bin/python3 -I -B -'
    try:
        result = subprocess.run([*publication.probe.SSH, destination], input=HOST_PROBE.encode('ascii'),
                                capture_output=True, check=True, timeout=14)
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return {'stage': 'host_observation', 'error_type': type(error).__name__}


def http_readback(interface):
    statuses = []
    for path in ('/', '/travel/'):
        trace = observed(path, 80, interface)
        if trace['stage'] != 'complete':
            raise ValueError('Existing HTTP readback failed')
        statuses.append((trace['status'], trace['location_sha256']))
    return statuses


def verify(interface, before, result):
    leaf = result['readiness']['fingerprint_sha256']
    trace = {}
    result['route_trace'] = trace
    with ThreadPoolExecutor(max_workers=3) as workers:
        host = workers.submit(loopback)
        default = workers.submit(observed, '/travel/', 443, expected_leaf=leaf)
        direct = workers.submit(observed, '/travel/', 443, interface, expected_leaf=leaf)
        trace['host_published_loopback'] = host.result()
        trace['default_route_travel'] = default.result()
        trace['selected_interface_travel'] = direct.result()
    probe = trace['host_published_loopback']
    if probe != {'stage':'complete','status':503,'fingerprint_sha256':leaf}:
        raise ValueError('Host published-port check failed')
    travel = trace['selected_interface_travel']
    if (travel.get('status'),travel.get('retry_after'),travel.get('no_store')) != (503,'60',True):
        raise ValueError('Interface-scoped travel maintenance check failed')
    api = trace['selected_interface_api'] = observed('/travel/api/health', 443, interface, expected_leaf=leaf)
    root = trace['selected_interface_root'] = observed('/', 443, interface, expected_leaf=leaf)
    if (api.get('status'),api.get('retry_after'),api.get('no_store')) != (503,'60',True) or root.get('status') != 404:
        raise ValueError('Interface-scoped API or unrelated path check failed')
    trace['http_after'] = http_readback(interface)
    if trace['http_after'] != before:
        raise ValueError('Existing HTTP readback changed')
    return {'trusted_expected_ip_leaf':True,'travel_and_api_503':True,
            'other_https_path_404':True,'http_responses_unchanged':True,'host_public_port_verified':True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interface-index', type=int)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--expected-sha256')
    options = parser.parse_args()
    source, payload = publication.inputs()
    digest = publication.probe.sha(source+b'\0'+payload)
    if not options.publish:
        print(json.dumps({'state':'review_only','bundle_sha256':digest,'attempt_exists':ATTEMPT.exists()}))
        return 0
    if (digest != options.expected_sha256 or sys.platform != 'win32' or options.interface_index is None
            or not 0 < options.interface_index < (1 << 24)):
        parser.error('Exact reviewed bundle and the observed Windows interface index are required')
    return publication.execute(source,payload,digest,attempt_path=ATTEMPT,
                               read_http=functools.partial(http_readback,options.interface_index),
                               verify=functools.partial(verify,options.interface_index))


if __name__ == '__main__':
    raise SystemExit(main())
