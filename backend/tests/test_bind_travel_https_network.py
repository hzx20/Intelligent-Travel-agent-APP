import json
from pathlib import Path

import pytest

from tools import bind_travel_https_network as binder, travel_tls_edge as edge
from tools.travel_https_host import publishes_443


RECEIPT = Path(__file__).resolve().parents[2] / 'docs/verification/budget-https-entry-host-readback-2026-11-19.json'

# 这两条要读 binder.REVIEW 里的私有复核包（.gitignore 排除）。CI 没有就跳过。
needs_review = pytest.mark.skipif(
    not binder.REVIEW.is_dir(), reason='Private review bundle required (local .release-candidates/)'
)


@needs_review
def test_live_readback_binds_edge_and_backend_to_exact_same_reviewed_addresses():
    files, report = binder.candidate_files(RECEIPT)
    assert report['files']['nginx-edge.conf'] == edge.DEDICATED_NORMAL
    assert files['nginx-edge.conf'].count('proxy_pass http://172.31.240.1:8000/') == 2
    assert 'proxy_pass' not in files['nginx-edge-maintenance.conf']
    assert '--host 172.31.240.1 --port 8000' in files['travel.service']
    assert '--forwarded-allow-ips 172.31.240.2' in files['travel.service']
    assert 'After=network.target docker.service' in files['travel.service']

    target = {'id': 'a'*64, 'image': 'sha256:'+'b'*64, 'network': 'travel-edge', 'ip': '172.31.240.2',
              'config_sha256': edge.DEDICATED_NORMAL}
    assert edge.parse_target(json.dumps(target)) == target
    with pytest.raises(ValueError, match='reviewed edge network'):
        edge.parse_target(json.dumps({**target, 'network': 'continew_default'}))


@needs_review
def test_binding_refuses_port_or_extra_upstream_and_network_conflict(tmp_path):
    normal, _ = binder.candidate_files(RECEIPT)
    with pytest.raises(ValueError, match='two exact travel upstreams'):
        binder.bind_nginx(normal['nginx-edge.conf'].replace(':8000/api/', ':8001/api/', 1), '172.31.240.1', '172.31.240.3')

    readback = json.loads(RECEIPT.read_bytes())
    readback['inventory']['network_candidate']['overlaps_observed_routes'] = True
    conflicted = tmp_path/'conflict.json'
    conflicted.write_text(json.dumps(readback))
    with pytest.raises(ValueError, match='No conflict-free network receipt'):
        binder.candidate_files(conflicted)


def test_readback_handles_docker_host_port_ranges():
    assert publishes_443(b'0.0.0.0:442-444->8000-8002/tcp')
    assert publishes_443(b'[::]:443->8443/tcp')
    assert not publishes_443(b'0.0.0.0:8443->443/tcp, 80/tcp')
