"""Run the packaged app, not the workspace, against a restored legacy fixture."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import User, Guide
from app.db.trip_models import Trip, TripDay, TripItem
from app.security import hash_password
from tools.backup_db import backup_database
from tools.frontend_release import digest, prepare as prepare_frontend
from tools.release_candidate import prepare, verify

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / 'frontend/dist'
pytestmark = pytest.mark.skipif(not (DIST / '.vite/manifest.json').is_file(), reason='Actual production frontend build required')


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@contextmanager
def packaged_backend(candidate, data, proof, *, shared=True, proxy_headers=False, forward_probe=False):
    listen = port()
    env = {**os.environ, 'PYTHONPATH': '', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
           'TRAVEL_DATA_DIR': str(data), 'JWT_SECRET': 'candidate-runtime-synthetic-signing-secret',
           'ZHIPU_API_KEY': '', 'AMAP_API_KEY': '', 'AMAP_JS_KEY': '', 'AMAP_JS_SECURITY_CODE': '',
           'TRAVEL_SHARED_LIMITS': str(int(shared)), 'TRAVEL_SHARED_CACHE': str(int(shared)),
           'TRAVEL_PROVIDER_BUDGET': str(int(shared))}
    bootstrap = r'''import json,os,pathlib,socket,sys,threading
root=pathlib.Path(sys.argv[1]).resolve()
original=socket.socket.connect
def local_only(sock,address):
    if sock.family in (socket.AF_INET,socket.AF_INET6) and address[0] not in ('127.0.0.1','::1'):
        raise RuntimeError('External connection forbidden during candidate rehearsal')
    return original(sock,address)
socket.socket.connect=local_only
import app.main as entry
from app.db.database import DATA_DIR
assert pathlib.Path(entry.__file__).resolve()==root/'backend/app/main.py'
assert DATA_DIR==pathlib.Path(sys.argv[2]).resolve()
if sys.argv[6]=='1':
 from fastapi import Request
 @entry.app.get('/api/__isolated_forward_probe')
 def observed(request: Request):
  return {'address':request.client.host,'scheme':request.url.scheme,'host':request.url.hostname}
pathlib.Path(sys.argv[3]).write_text(json.dumps({'app_file':entry.__file__,'data_dir':str(DATA_DIR)}),encoding='utf-8')
import uvicorn
server = uvicorn.Server(uvicorn.Config(
    entry.app, host='127.0.0.1', port=int(sys.argv[4]), log_level='warning',
    proxy_headers=sys.argv[5]=='1', forwarded_allow_ips='127.0.0.1',
))
def stop_on_parent_close():
    try:
        os.read(0, 1)
    finally:
        server.should_exit = True
threading.Thread(target=stop_on_parent_close, daemon=True).start()
server.run()
'''
    with (data.parent / 'candidate-server.log').open('a', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, '-B', '-c', bootstrap, str(candidate), str(data), str(proof), str(listen),
                                    str(int(proxy_headers)), str(int(forward_probe))],
                                   cwd=candidate / 'backend', env=env, stdin=subprocess.PIPE, stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        client = httpx.Client(base_url=f'http://127.0.0.1:{listen}', trust_env=False, timeout=5)
        try:
            deadline = time.monotonic() + 30
            while True:
                assert process.poll() is None, 'Packaged backend exited before startup; inspect isolated candidate-server.log'
                try:
                    if client.get('/api/health').status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, 'Packaged backend startup timed out'
                time.sleep(.1)
            yield client, env
        finally:
            client.close()
            # Let Uvicorn release SQLite handles before the temporary directory is removed.
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def test_packaged_backend_migrates_restored_legacy_copy_and_restarts(tmp_path):
    configured = os.environ.get('TRAVEL_TEST_RELEASE_CANDIDATE')
    if configured:
        candidate = Path(configured).resolve()
        metadata = verify(candidate)
    else:
        frontend = tmp_path / 'frontend'
        prepare_frontend(DIST, frontend)
        candidate = tmp_path / 'candidate'
        metadata = prepare(ROOT, frontend, candidate)
    legacy = tmp_path / 'legacy.db'
    engine = create_engine(f'sqlite:///{legacy}')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(username='candidate_legacy', password_hash=hash_password('synthetic-candidate-password'))
        db.add(user); db.flush()
        db.add(Guide(user_id=user.id, title='旧攻略', content='原始正文', city='杭州'))
        trip = Trip(user_id=user.id, title='保留旧旅行', destination='杭州')
        db.add(trip); db.flush()
        day = TripDay(trip_id=trip.id, day_number=1)
        db.add(day); db.flush()
        db.add(TripItem(day_id=day.id, sort_order=0, name='原安排', note='原备注'))
        db.commit()
    with engine.begin() as conn:
        missing_columns = {
            'users': ['profile_version'],
            'guides': ['version', 'revision'],
            'trips': ['walking_preferences', 'budget_review'],
            'trip_days': ['route_preferences'],
            'trip_items': ['source', 'location_candidates'],
        }
        for table, columns in missing_columns.items():
            for column in columns:
                conn.execute(text(f'ALTER TABLE {table} DROP COLUMN {column}'))
        for table in ('trip_creation_requests', 'guide_creation_requests', 'trip_ai_jobs', 'plan_generation_jobs'):
            conn.execute(text(f'DROP TABLE {table}'))
    engine.dispose()
    snapshot = tmp_path / 'snapshot.db'
    data = tmp_path / 'restored-data'
    data.mkdir()
    backup_database(legacy, snapshot)
    backup_database(snapshot, data / 'app.db')
    before = {path: digest(path.read_bytes()) for path in (legacy, snapshot)}
    proof = tmp_path / 'candidate-proof.json'
    with packaged_backend(candidate, data, proof) as (client, env):
        login = client.post('/api/auth/login', json={'username': 'candidate_legacy', 'password': 'synthetic-candidate-password'})
        assert login.status_code == 200
        auth = {'Authorization': 'Bearer ' + login.json()['token']}
        loaded = client.get('/api/trips/1', headers=auth).json()
        assert loaded['title'] == '保留旧旅行' and loaded['days'][0]['items'][0]['note'] == '原备注'
        assert loaded['days'][0]['items'][0]['source'] == ''
        assert loaded['budget_review'] == loaded['walking_preferences'] == loaded['days'][0]['route_preferences'] == {}
        quote = {'status':'quoted', 'amount_minor':98000, 'source':'仅隔离演练：两晚含税', 'checked_on':'2026-11-18'}
        saved_budget = client.patch('/api/trips/1/budget-review', headers=auth, json={
            'version':loaded['version'], 'checklist':{'limit_minor':300000, 'entries':{'lodging':quote}}})
        assert saved_budget.status_code == 200, saved_budget.text
        saved_review = saved_budget.json()['budget_review']
        assert saved_review['checklist']['entries'] == {'lodging':quote}
        assert client.get('/api/trips/1/expenses', headers=auth).json() == {'items':[], 'totals':{}}
        guide = client.get('/api/guides/1', headers=auth).json()
        assert guide['content'] == '原始正文' and guide['version'] == 1 and len(guide['revision']) == 36
        changed = client.patch('/api/me/profile/edit', headers=auth, json={'nickname': '候选升级后', 'version': 1})
        assert changed.status_code == 200 and changed.json()['version'] == 2
        creation = {'title': '候选可靠创建', 'days': 1, 'request_id': '11111111-1111-4111-8111-111111111111'}
        created = client.post('/api/trips/create', headers=auth, json=creation)
        assert created.status_code == 201
        created_id = created.json()['id']
        # Run the packaged preflight, which must import packaged schema metadata.
        checked = subprocess.run([sys.executable, '-B', str(candidate / 'backend/tools/deployment_check.py'),
                                  '--database', str(data / 'app.db'), '--frontend', str(candidate / 'frontend'), '--workers', '1'],
                                 cwd=candidate / 'backend', env=env, capture_output=True, text=True, timeout=20)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        assert json.loads(checked.stdout)['local_checks_passed']
    assert Path(json.loads(proof.read_text(encoding='utf-8'))['app_file']).resolve() == candidate / 'backend/app/main.py'
    with packaged_backend(candidate, data, proof) as (restarted, _):
        assert restarted.get('/api/auth/me', headers=auth).status_code == 200
        replay = restarted.post('/api/trips/create', headers=auth, json=creation)
        assert replay.status_code == 200 and replay.json()['id'] == created_id
        assert restarted.get('/api/guides/1', headers=auth).json()['revision'] == guide['revision']
        assert restarted.get('/api/trips/1', headers=auth).json()['budget_review'] == saved_review
        profile = restarted.get('/api/me/profile', headers=auth).json()
        assert profile['nickname'] == '候选升级后' and profile['version'] == 2
    assert verify(candidate) == metadata  # No pyc, .env, data or JWT written into artifact.
    assert not (candidate / 'backend/data').exists()
    assert {path: digest(path.read_bytes()) for path in before} == before
