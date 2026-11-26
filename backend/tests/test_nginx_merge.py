import json
from pathlib import Path

import pytest

from tools.nginx_merge import merge, parse, prepare, signature

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / 'deploy/nginx-travel.conf').read_bytes().decode('utf-8')
SOURCE = r'''worker_processes auto;
events { worker_connections 1024; }
http {
    include /etc/nginx/mime.types;
    limit_req_zone $binary_remote_addr zone=travel_plan:10m rate=6r/m;
    map $http_upgrade $connection_upgrade { default upgrade; '' close; }
    server {
        listen 80;
        root /usr/share/nginx/html;
        # The unrelated application must stay byte-for-byte intact.
        location /api/ { proxy_pass http://other:8080/api/; proxy_set_header X-Private synthetic-private-value; }
        location ~* \.(js|css)$ { expires 1h; }
        location / { try_files $uri $uri/ /index.html; }
        location ^~ /travel/api/plan/stream { limit_req zone=travel_plan; proxy_pass http://172.18.0.1:8000/api/plan/stream; }
        location ^~ /travel/api/ { proxy_pass http://172.18.0.1:8000/api/; }
        location ^~ /travel/ { try_files $uri $uri/ /travel/index.html; }
        location = /travel { return 301 /travel/; }
    }
}
'''


def test_merge_preserves_other_application_and_replaces_only_travel():
    candidate, report = merge(SOURCE, TEMPLATE)
    assert report['unrelated_directives_unchanged']
    assert report['new_location_count'] == 8
    assert candidate.count('zone=travel_plan:10m') == 1
    assert 'location /api/ { proxy_pass http://other:8080/api/; proxy_set_header X-Private synthetic-private-value; }' in candidate
    assert r'location ~* \.(js|css)$ { expires 1h; }' in candidate
    assert 'synthetic-private-value' not in json.dumps(report)
    assert 'location ^~ /travel/.travel-deploy/' in candidate
    assert 'map_hash_bucket_size 128;' in candidate


def test_merge_is_byte_idempotent():
    first, _ = merge(SOURCE, TEMPLATE)
    second, _ = merge(first, TEMPLATE)
    assert second == first


def test_quotes_comments_escapes_and_variable_braces_are_not_blocks():
    source = r'''http { # location /travel/fake { return 200; }
      map ${request_method} $other { default "{"; }
      server { location / { return 200 "}"; }
        location /travel/ { return 200 ";"; }
        location /literal { return 200 'semi; # { } \'quote'; }
      }
    }'''
    candidate, report = merge(source, TEMPLATE)
    assert report['unrelated_directives_unchanged']
    assert 'map ${request_method} $other { default "{"; }' in candidate
    assert r"return 200 'semi; # { } \'quote';" in candidate
    assert signature(parse('return 200 ";";')) == [(('return', '200', ';'), None)]


@pytest.mark.parametrize('value,expected', [('64', 128), ('256', 256), ('1k', 1024)])
def test_existing_bucket_is_never_reduced(value, expected):
    source = SOURCE.replace('http {', 'http { map_hash_bucket_size ' + value + ';', 1)
    candidate, report = merge(source, TEMPLATE)
    assert report['map_bucket_after'] == expected
    assert candidate.count('map_hash_bucket_size ') == 1
    assert f'map_hash_bucket_size {expected};' in candidate


@pytest.mark.parametrize('extra', [
    'location /other { limit_req zone=travel_plan; }',
    'location /other { return 200 $travel_ai_limit_key; }',
    'location /other { return 200 ${travel_ai_limit_key}; }',
])
def test_shared_travel_names_used_by_unrelated_routes_are_rejected(extra):
    source = SOURCE.replace('listen 80;', 'listen 80; ' + extra)
    with pytest.raises(ValueError, match='outside travel'):
        merge(source, TEMPLATE)


@pytest.mark.parametrize('source', [
    'events {}',
    'http {} http {}',
    'http { server { location / { return 200; } } }',
    'http { server { location /travel/ {} } server { location /travel/ {} } }',
    'http { server { location ~ ^/travel/ {} } }',
    'http { server { location @travel {} } }',
])
def test_ambiguous_target_is_rejected(source):
    with pytest.raises(ValueError):
        merge(source, TEMPLATE)


@pytest.mark.parametrize('source', ['http {', 'http }', 'http { return "unfinished;', 'http { return 200 }'])
def test_incomplete_or_unbalanced_config_is_rejected(source):
    with pytest.raises(ValueError):
        parse(source)


def test_unrelated_directive_cannot_be_hidden_inside_managed_markers():
    candidate, _ = merge(SOURCE, TEMPLATE)
    changed = candidate.replace('# BEGIN TRAVEL HTTP', '# BEGIN TRAVEL HTTP\n client_max_body_size 9m;', 1)
    with pytest.raises(ValueError, match='Unrelated directive'):
        merge(changed, TEMPLATE)


@pytest.mark.parametrize('path', ['/etc/nginx/conf.d/*.conf', 'other-site.conf'])
def test_unknown_includes_are_not_claimed_as_preserved(path):
    with pytest.raises(ValueError, match='independent review'):
        merge(SOURCE.replace('http {', 'http { include ' + path + ';', 1), TEMPLATE)


@pytest.mark.parametrize('definition', ['limit_req_zone $binary_remote_addr zone=travel_plan:10m rate=6r/m;',
                                      'map $request_method $travel_ai_limit_key { default ""; } map $uri $travel_ai_limit_key { default ""; }'])
def test_duplicate_travel_definitions_need_review(definition):
    with pytest.raises(ValueError, match='Duplicate travel'):
        merge(SOURCE.replace('http {', 'http { ' + definition, 1), TEMPLATE)


def test_prepare_writes_private_diff_and_never_replaces_inputs_or_output(tmp_path):
    source = tmp_path / 'before.conf'
    source.write_bytes(SOURCE.replace('\n', '\r\n').encode())
    before = source.read_bytes()
    output = tmp_path / 'review'
    report = prepare(source, ROOT / 'deploy/nginx-travel.conf', output)
    assert source.read_bytes() == before
    assert report['idempotent'] and not report['production_deployed'] and not report['syntax_tested']
    assert (output / 'nginx.diff').read_text(encoding='utf-8').startswith('--- production-before.conf')
    assert b'        listen 80;\r\n' in (output / 'nginx-candidate.conf').read_bytes()
    with pytest.raises(FileExistsError):
        prepare(source, ROOT / 'deploy/nginx-travel.conf', output)
