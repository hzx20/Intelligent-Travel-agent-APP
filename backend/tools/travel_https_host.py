"""Read-only inventory of the existing host before configuring the travel HTTPS edge."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection, HTTPException
import ipaddress
import json
from pathlib import Path
import re
import shlex
import socket
import ssl
import subprocess
import sys


EDGE_SUBNET = ipaddress.IPv4Network('172.31.240.0/28')
EDGE_NETWORK = 'travel-edge'
EDGE_INTERFACE = 'br-travel-edge'


def listeners(host, port):
    rows = host['show']('/usr/bin/ss', '-H', '-ltn', 'sport', '=', f':{port}').decode('ascii')
    return sorted({row.split()[3] for row in rows.splitlines() if row.strip()})


def publishes_443(mappings):
    for first, last in re.findall(rb':([0-9]+)(?:-([0-9]+))?->', mappings):
        if int(first) <= 443 <= int(last or first):
            return True
    return False


def network_candidate(show, docker):
    routes = json.loads(show('/usr/sbin/ip', '-j', '-4', 'route', 'show', 'table', 'all'))
    links = json.loads(show('/usr/sbin/ip', '-j', 'link', 'show'))
    networks = show(*docker, 'network', 'ls', '-q').decode('ascii').split()
    allocations = json.loads(show(*docker, 'network', 'inspect', *networks))
    routed = [
        ipaddress.IPv4Network(route['dst'], strict=False)
        for route in routes if route.get('dst') not in (None, 'default')
    ]
    assigned = [
        ipaddress.ip_network(config['Subnet']) for network in allocations
        for config in (network['IPAM']['Config'] or []) if 'Subnet' in config
    ]
    return {
        'subnet': str(EDGE_SUBNET),
        'gateway': str(EDGE_SUBNET[1]),
        'proxy_ip': str(EDGE_SUBNET[2]),
        'dynamic_range': str(list(EDGE_SUBNET.subnets(new_prefix=29))[1]),
        'docker_network': EDGE_NETWORK,
        'host_interface': EDGE_INTERFACE,
        'overlaps_observed_routes': any(EDGE_SUBNET.overlaps(route) for route in routed),
        'overlaps_observed_docker_ipam': any(EDGE_SUBNET.overlaps(net) for net in assigned if net.version == 4),
        'docker_name_in_use': any(net['Name'] == EDGE_NETWORK for net in allocations),
        'interface_name_in_use': any(link['ifname'] == EDGE_INTERFACE for link in links),
    }


def inventory(host):
    host['preflight']()
    show, docker = host['show'], host['DOCKER']
    bridge = json.loads(show(*docker, 'network', 'inspect', 'continew_default'))[0]
    host['require'](bridge['Name'] == 'continew_default' and bridge['Driver'] == 'bridge')
    endpoints = bridge['Containers'] or {}
    shared_addresses = [c['IPv4Address'] for c in endpoints.values() if c['Name'] == 'continew-web']
    host['require'](len(shared_addresses) == 1)
    ipv4 = []
    for config in bridge['IPAM']['Config']:
        subnet = ipaddress.ip_network(config['Subnet'])
        if subnet.version == 4:
            ipv4.append({'subnet': str(subnet), 'gateway': str(ipaddress.IPv4Address(config['Gateway'])),
                         'dynamic_range': str(ipaddress.IPv4Network(config['IPRange'])) if config.get('IPRange') else None})
    names = show(*docker, 'ps', '-a', '--format', '{{.Names}}').decode('ascii').splitlines()
    mappings = show(*docker, 'ps', '--format', '{{.Ports}}')
    certbot = show('/usr/bin/snap', 'list', 'certbot').decode('ascii').splitlines()
    versions = [line.split()[1] for line in certbot if line.split() and line.split()[0] == 'certbot']
    host['require'](len(versions) == 1 and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', versions[0]))
    roots = ('/etc/travel-tls', '/var/lib/travel-tls', '/var/log/travel-tls')
    return {
        'state': 'host_inventory_read',
        'shared_http_config_matches': True,
        'certbot_version': versions[0],
        'listeners_443': listeners(host, 443),
        'listeners_8000': listeners(host, 8000),
        'docker_publishes_443': publishes_443(mappings),
        'dedicated_edge_exists': 'travel-https-edge' in names,
        'dedicated_tls_roots_exist': {path: Path(path).exists() or Path(path).is_symlink() for path in roots},
        'current_bridge': {'ipv4': ipv4, 'shared_proxy': str(ipaddress.IPv4Interface(shared_addresses[0]).ip),
                           'endpoint_count': len(endpoints), 'internal': bridge['Internal']},
        'network_candidate': network_candidate(show, docker),
    }


def remote_inventory():
    # Reuse the reviewed check of HTTP port 80, the public webroot and both config hashes.
    helper = {'__name__': 'acme_host_checks'}
    try:
        exec(compile(sys.stdin.buffer.read(16 * 1024), '<shared-http-preflight>', 'exec'), helper)
        print(json.dumps(inventory(helper), sort_keys=True))
    except (OSError, ValueError, RuntimeError, KeyError, IndexError, subprocess.SubprocessError) as error:
        print(json.dumps({'state': 'host_inventory_unverified', 'error_type': type(error).__name__}))
        return 1
    return 0


def http_status(target, port, path):
    connection = HTTPConnection(target, port, timeout=5)
    try:
        connection.request('GET', path)
        with connection.getresponse() as response:
            result = {'status': response.status}
            if port == 8000:
                try:
                    body = json.loads(response.read(256))
                except ValueError:
                    body = None
                result['json_health_shape'] = isinstance(body, dict) and ('status' in body or 'ok' in body)
            return result
    except (OSError, HTTPException) as error:
        return {'error': type(error).__name__}
    finally:
        connection.close()


def trusted_tls(target):
    try:
        with socket.create_connection((target, 443), timeout=5) as tcp:
            with ssl.create_default_context().wrap_socket(tcp, server_hostname=target) as secure:
                return {'trusted_tls': True, 'version': secure.version()}
    except OSError as error:
        return {'trusted_tls': False, 'error': type(error).__name__}


def public_readback(target):
    with ThreadPoolExecutor(max_workers=3) as pool:
        http = pool.submit(http_status, target, 80, '/travel/')
        tls = pool.submit(trusted_tls, target)
        backend = pool.submit(http_status, target, 8000, '/api/health')
        return {'80_http': http.result(), '443_tls': tls.result(), '8000_http': backend.result()}


def main():
    if __package__:
        from . import run_acme_probe as probe
    else:
        import run_acme_probe as probe
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--inspect', action='store_true', help='Inspect the pinned server over SSH')
    mode.add_argument('--public-readback', action='store_true', help='Read the pinned site from the current machine')
    args = parser.parse_args()
    if args.public_readback:
        print(json.dumps(public_readback(probe.TARGET), sort_keys=True))
        return 0
    if not args.inspect:
        print(json.dumps({'state': 'review_only', 'host': probe.TARGET}))
        return 0
    # Pass source over one authenticated session, with no remote write or forwarding.
    payload = probe.reviewed_source()
    source = Path(__file__).read_text(encoding='utf-8')
    remote = '/usr/bin/timeout 28s /usr/bin/python3 -I -B -c ' + shlex.quote(source) + ' --remote-inventory'
    try:
        result = subprocess.run([*probe.SSH, remote], input=payload, capture_output=True, timeout=38)
        report = json.loads(result.stdout)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        report = {'state': 'host_inventory_unverified', 'error_type': type(error).__name__}
        print(json.dumps(report))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0 if result.returncode == 0 and report.get('state') == 'host_inventory_read' else 1


if __name__ == '__main__':
    raise SystemExit(remote_inventory() if sys.argv[1:] == ['--remote-inventory'] else main())
