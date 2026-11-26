import json

import pytest

from test_nginx_merge import SOURCE, TEMPLATE
from tools.nginx_merge import merge
from tools.travel_maintenance import prepare, render


@pytest.mark.parametrize('source', [SOURCE, merge(SOURCE, TEMPLATE)[0]])
def test_maintenance_is_idempotent_and_can_return_to_normal_rules(source):
    maintenance, report = render(source)
    assert report['unrelated_directives_unchanged']
    assert 'proxy_pass http://172.18.0.1:8000/' not in maintenance
    assert 'proxy_pass http://other:8080/api/;' in maintenance
    assert render(maintenance)[0] == maintenance
    restored, _ = merge(maintenance, TEMPLATE)
    assert restored == merge(source, TEMPLATE)[0]


@pytest.mark.parametrize('source', [SOURCE.replace('http {', 'http { include other.conf;'),
                                  SOURCE.replace('http {', 'http { server { location /travel/ { return 200; } }'),
                                  SOURCE.replace('location = /travel', 'location ~ /travel')])
def test_unseen_or_ambiguous_shared_context_is_refused(source):
    with pytest.raises(ValueError):
        render(source)


def test_prepare_is_private_review_only_and_refuses_existing_output(tmp_path):
    source = tmp_path / 'source.conf'
    source.write_text(SOURCE, encoding='utf-8')
    output = tmp_path / 'review'
    report = prepare(source, output)
    assert report['production_deployed'] is False and report['syntax_tested'] is False
    assert json.loads((output / 'report.json').read_text()) == report
    assert source.read_text(encoding='utf-8') == SOURCE
    with pytest.raises(FileExistsError):
        prepare(source, output)
