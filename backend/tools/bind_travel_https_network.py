"""Prepare a local HTTPS edge and backend pair for the reviewed dedicated bridge."""
import argparse
import hashlib
import json
from pathlib import Path

if __package__:
    from . import nginx_merge, release_plan, travel_https_host as host
else:
    import nginx_merge
    import release_plan
    import travel_https_host as host


REPO = Path(__file__).resolve().parents[2]
REVIEW = REPO / '.release-candidates/budget-https-load-2026-11-19'
BASE_PLAN = '81ee1674adc6b9267e4b526ffdbf5313cae0b8094c5039ca220c528ef500f5b5'
APPLICATION = '553a85ca32f4a4d339baaa618b8c9fc97e756b96d713dbcbbcd45ffe974ae363'
EDGE_INPUTS = {
    'nginx-edge.conf': '222493b507c7737477061e944ec019d3b99f8b5d0c404d6e7d681790d50f5601',
    'nginx-edge-maintenance.conf': 'e2ba23f487a2ede868983373fc3af0d60652c7b6ab4b1da4bc2d945240288e1e',
}


def bind_nginx(source, old_gateway, new_gateway):
    old = f'http://{release_plan.private_ip(old_gateway)}:8000'
    new = f'http://{release_plan.private_ip(new_gateway)}:8000'
    nodes = [n for n in nginx_merge.all_nodes(nginx_merge.parse(source)) if n.head[0] == 'proxy_pass']
    expected = {('proxy_pass', old + '/api/plan/stream'), ('proxy_pass', old + '/api/')}
    if len(nodes) != 2 or {node.head for node in nodes} != expected:
        raise ValueError('Expected the two exact travel upstreams')
    return nginx_merge.replace(source, [
        (node.start, node.end, f'proxy_pass {new}{node.head[1][len(old):]};') for node in nodes
    ])


def candidate_files(receipt):
    evidence = json.loads(receipt.read_bytes())
    network = evidence['inventory']['network_candidate']
    gateway, proxy_ip = (str(host.EDGE_SUBNET[offset]) for offset in (1, 2))
    if (evidence.get('production_address_confirmed') is not True or evidence.get('host') != '134.175.137.18'
            or evidence.get('ssh_exit') != 0 or evidence['inventory'].get('shared_http_config_matches') is not True
            or network['subnet'] != str(host.EDGE_SUBNET) or (network['gateway'], network['proxy_ip']) != (gateway, proxy_ip)
            or network['docker_network'] != host.EDGE_NETWORK or network['host_interface'] != host.EDGE_INTERFACE
            or not all(network[flag] is False for flag in (
                'overlaps_observed_routes', 'overlaps_observed_docker_ipam', 'docker_name_in_use', 'interface_name_in_use'))):
        raise ValueError('No conflict-free network receipt for the confirmed public IP')

    plan = json.loads((REVIEW / 'private-plan/plan.json').read_bytes())
    plan_id = plan.pop('id')
    if (plan_id != BASE_PLAN or release_plan.identifier(plan) != plan_id
            or plan['application_id'] != APPLICATION or plan['backend_bind_ip'] != '172.18.0.1'):
        raise ValueError('Unexpected frozen release identity')
    template = (REVIEW / 'private-plan/ops/service-profile.conf.template').read_bytes()
    if hashlib.sha256(template).hexdigest() != plan['files']['ops/service-profile.conf.template']:
        raise ValueError('Frozen service template differs from the approved plan')

    files = {}
    for name, expected in EDGE_INPUTS.items():
        source = (REVIEW / 'https-ip-edge-rotation-review' / name).read_bytes().decode('utf-8')
        if nginx_merge.sha(source) != expected:
            raise ValueError('Frozen nginx configuration differs from the isolation rehearsal')
        files[name] = bind_nginx(source, plan['backend_bind_ip'], gateway) if name == 'nginx-edge.conf' else source
    service = release_plan.render_service(template.decode('utf-8'), APPLICATION, proxy_ip, gateway)
    files['travel.service'] = service.replace('After=network.target\n', 'Requires=docker.service\nAfter=network.target docker.service\n', 1)
    report = {
        'source_plan': BASE_PLAN, 'application_id': APPLICATION, 'public_ip': evidence['host'],
        'network': network, 'files': {name: nginx_merge.sha(contents) for name, contents in files.items()},
        'production_ready': False,
    }
    return files, report


def prepare(receipt, out):
    files, report = candidate_files(receipt)
    files['report.json'] = json.dumps(report, indent=2) + '\n'
    out.mkdir(mode=0o700)
    for name, contents in files.items():
        with (out / name).open('xb') as stream:
            stream.write(contents.encode('utf-8'))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt', type=Path)
    parser.add_argument('out', type=Path)
    args = parser.parse_args()
    try:
        report = prepare(args.receipt, args.out)
    except (OSError, KeyError, TypeError, ValueError) as error:
        parser.exit(1, f'HTTPS network candidate refused: {error}\n')
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
