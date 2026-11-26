"""Real fresh imports prove release-directory changes preserve explicit secrets."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def release(root):
    core = root / 'backend/app/core'
    core.mkdir(parents=True)
    shutil.copyfile(BACKEND / 'app/core/config.py', core / 'config.py')
    shutil.copyfile(BACKEND / 'app/security.py', root / 'backend/app/security.py')
    return root


def invoke(root, *, selector=None, token=None, environment=None, set_selector=True):
    env = {key: value for key, value in os.environ.items() if key not in (
        'PYTHONPATH', 'TRAVEL_ENV_FILE', 'JWT_SECRET', 'AMAP_API_KEY', 'AMAP_JS_KEY', 'AMAP_JS_SECURITY_CODE', 'ZHIPU_API_KEY')}
    env.update({'PYTHONDONTWRITEBYTECODE': '1', 'TRAVEL_DATA_DIR': str(root.parent / 'external-data')})
    if set_selector and selector is not None:
        env['TRAVEL_ENV_FILE'] = str(selector)
    env.update(environment or {})
    code = '''import json,os,sys
from app.core.config import settings,ENV_FILE
from app.security import create_token,decode_token
print(json.dumps({'env_file':str(ENV_FILE),'jwt':settings.jwt_secret,'amap':settings.amap_api_key,
                  'data':os.environ.get('TRAVEL_DATA_DIR'),
                  'token':create_token(7) if len(sys.argv)==1 else None,
                  'decoded':decode_token(sys.argv[1]) if len(sys.argv)>1 else None}))
'''
    return subprocess.run([sys.executable, '-B', '-c', code, *([token] if token else [])], cwd=root / 'backend', env=env,
                          capture_output=True, text=True, encoding='utf-8', timeout=15)


def test_default_project_env_remains_supported(tmp_path):
    code = release(tmp_path / 'code')
    (code / '.env').write_text('JWT_SECRET=default-synthetic-secret\nAMAP_API_KEY=default-synthetic-key\n')
    result = invoke(code, set_selector=False)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed['jwt'] == 'default-synthetic-secret'
    assert observed['amap'] == 'default-synthetic-key'
    assert Path(observed['env_file']) == code / '.env'


def test_external_file_keeps_tokens_valid_across_two_release_directories(tmp_path):
    first, second = (release(tmp_path / name) for name in ('release-one', 'release-two'))
    (first / '.env').write_text('JWT_SECRET=wrong-release-one\n')
    (second / '.env').write_text('JWT_SECRET=wrong-release-two\n')
    secret = tmp_path / 'stable secrets.env'
    secret.write_text('JWT_SECRET=stable-synthetic-external-signing-secret\nAMAP_API_KEY=stable-map\nTRAVEL_DATA_DIR=/ignored-file-setting\n')
    one = invoke(first, selector=secret)
    assert one.returncode == 0, one.stderr
    token = json.loads(one.stdout)['token']
    two = invoke(second, selector=secret, token=token)
    assert two.returncode == 0, two.stderr
    observed = json.loads(two.stdout)
    assert observed['decoded'] == 7 and observed['jwt'] == 'stable-synthetic-external-signing-secret'
    assert observed['amap'] == 'stable-map'
    assert Path(observed['data']) == tmp_path / 'external-data'
    assert not (first / 'backend/data').exists() and not (second / 'backend/data').exists()
    assert not (tmp_path / 'external-data').exists()


def test_explicit_process_settings_keep_precedence(tmp_path):
    code = release(tmp_path / 'code')
    secret = tmp_path / 'secrets.env'
    secret.write_text('JWT_SECRET=file-synthetic-secret\nAMAP_API_KEY=file-map\n')
    result = invoke(code, selector=secret, environment={'JWT_SECRET': 'process-synthetic-secret', 'AMAP_API_KEY': 'process-map'})
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed['jwt'] == 'process-synthetic-secret' and observed['amap'] == 'process-map'


@pytest.mark.parametrize('selector', ['relative.env', '', 'missing', 'directory'])
def test_bad_explicit_file_fails_before_any_data_or_secret_creation(tmp_path, selector):
    code = release(tmp_path / 'code')
    (code / '.env').write_text('JWT_SECRET=must-not-fallback\n')
    value = tmp_path / selector if selector in ('missing', 'directory') else selector
    if selector == 'directory':
        value.mkdir()
    result = invoke(code, selector=value)
    assert result.returncode != 0 and 'TRAVEL_ENV_FILE' in result.stderr
    assert 'must-not-fallback' not in result.stdout
    assert not (code / 'backend/data').exists() and not (tmp_path / 'external-data').exists()
