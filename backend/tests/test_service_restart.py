"""Actual Uvicorn processes and HTTP against an isolated disk database."""
from contextlib import contextmanager
from io import BytesIO
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time

import httpx
from PIL import Image
from test_footprint_photos import photo_bytes

BACKEND = Path(__file__).resolve().parents[1]


@contextmanager
def running_backend(data_dir, port, *, bootstrap=None, bootstrap_args=()):
    env = {**os.environ, 'TRAVEL_DATA_DIR':str(data_dir),
           'JWT_SECRET':'isolated-restart-test-signing-key-not-production',
           'ZHIPU_API_KEY':'', 'AMAP_API_KEY':'', 'AMAP_JS_KEY':'', 'AMAP_JS_SECURITY_CODE':''}
    command = ([sys.executable, '-c', bootstrap, str(port), *bootstrap_args] if bootstrap else
               [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1',
                '--port', str(port), '--log-level', 'warning'])
    with (data_dir.parent/'restart-server.log').open('a',encoding='utf-8') as log:
        process = subprocess.Popen(command,cwd=BACKEND,env=env,
                                   stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        client = httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=3,trust_env=False)
        try:
            deadline = time.monotonic()+30
            while True:
                assert process.poll() is None, 'Isolated backend exited before startup'
                try:
                    if client.get('/api/health').status_code == 200: break
                except httpx.TransportError:
                    pass
                assert time.monotonic()<deadline, 'Isolated backend startup timed out'
                time.sleep(0.1)
            yield client
        finally:
            client.close()
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=10)


def test_trip_reservation_and_private_photo_survive_service_restart(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
    data_dir = tmp_path/'isolated-data'
    with running_backend(data_dir,port) as c:
        assert c.post('/api/auth/register',json={'username':'restart_photo','password':'test-only-password'}).status_code == 201
        login = c.post('/api/auth/login',json={'username':'restart_photo','password':'test-only-password'})
        assert login.status_code == 200
        auth = {'Authorization':'Bearer '+login.json()['token']}
        creation = {'title':'重启验收','days':1,'request_id':'11111111-1111-4111-8111-111111111111'}
        trip = c.post('/api/trips/create',headers=auth,json=creation).json()
        trip = c.post(f"/api/trips/{trip['id']}/days/{trip['days'][0]['id']}/items",headers=auth,
                      json={'version':trip['version'],'name':'测试地点','note':'重启后保留'}).json()
        item_id = trip['days'][0]['items'][0]['id']
        trip = c.post(f"/api/trips/{trip['id']}/reservations",headers=auth,
                      json={'version':trip['version'],'kind':'住宿','title':'测试酒店','status':'已预订','reference':'TEST-RESTART','item_id':item_id}).json()
        trip = c.post(f"/api/trips/{trip['id']}/footprints",headers=auth,
                      json={'version':trip['version'],'item_id':item_id}).json()
        footprint_path = f"/api/trips/{trip['id']}/footprints"
        record = c.get(footprint_path,headers=auth).json()['items'][0]
        photo_path = f"{footprint_path}/{record['id']}/photos"
        response = c.post(photo_path,headers={**auth,'Content-Type':'image/jpeg','X-Trip-Version':str(trip['version'])},content=photo_bytes())
        assert response.status_code == 201
        saved_trip = response.json()
        photo_id = c.get(footprint_path,headers=auth).json()['items'][0]['photo_ids'][0]
        before = c.get(f'{photo_path}/{photo_id}',headers=auth)
        assert before.status_code == 200
        before_bytes = before.content
        sharing_path = f"/api/trips/{trip['id']}/sharing"
        prepared = c.post(sharing_path+'/links/prepare', headers=auth, json={'mode':'view'})
        assert prepared.status_code == 200
        proof = prepared.json()['preparation']
        activated = c.post(sharing_path+'/links/activate', headers=auth, json={'preparation':proof})
        assert activated.status_code == 201
        shared_link = activated.json()
        guide_creation = {'request_id':'33333333-3333-4333-8333-333333333333','title':'重启攻略','content':'创建正文','is_draft':True}
        guide_created = c.post('/api/guides/create',headers=auth,json=guide_creation)
        assert guide_created.status_code == 201
        guide_id = guide_created.json()['id']
        guide_updated = c.patch(f'/api/guides/{guide_id}/content',headers=auth,
                                json={'title':'后续修改','content':'修改正文','version':1,'revision':guide_created.json()['revision']}).json()
        assert guide_updated['version'] == 2
        changed_profile = c.patch('/api/me/profile/edit',headers=auth,json={'nickname':'重启后保留昵称','version':1})
        assert changed_profile.status_code == 200 and changed_profile.json()['version'] == 2
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from app.db.models import Spot
        seed_engine = create_engine(f"sqlite:///{data_dir/'app.db'}")
        try:
            with Session(seed_engine) as db:
                spot = Spot(name='重启收藏景点',city='杭州',amap_id='restart-spot')
                db.add(spot); db.commit(); spot_id = spot.id
        finally: seed_engine.dispose()
        favorite_body = {'favorited':True,'version':0}
        favorite_saved = c.put(f'/api/spots/{spot_id}/favorite-state',headers=auth,json=favorite_body)
        assert favorite_saved.status_code == 200
        comment_body = {'content':'重启后同一评论','request_id':'55555555-5555-4555-8555-555555555555','rating':3}
        comment_saved = c.post(f'/api/spots/{spot_id}/comments/create',headers=auth,json=comment_body)
        assert comment_saved.status_code == 201
    assert (data_dir/'app.db').is_file()
    with running_backend(data_dir,port) as c:
        loaded = c.get(f"/api/trips/{trip['id']}",headers=auth)
        assert loaded.status_code == 200 and loaded.json() == saved_trip
        replayed = c.post('/api/trips/create', headers=auth, json=creation)
        assert replayed.status_code == 200 and replayed.json() == saved_trip
        assert len(c.get('/api/trips', headers=auth).json()['items']) == 1
        recovered = c.post(sharing_path+'/links/activate', headers=auth, json={'preparation':proof})
        assert recovered.status_code == 200 and recovered.json() == shared_link
        assert len(c.get(sharing_path, headers=auth).json()['links']) == 1
        guide_replayed = c.post('/api/guides/create',headers=auth,json=guide_creation)
        assert guide_replayed.status_code == 200 and guide_replayed.json() == guide_updated
        loaded_guide = c.get(f'/api/guides/{guide_id}',headers=auth).json()
        assert loaded_guide['content'] == '修改正文' and loaded_guide['version'] == 2
        assert len(c.get('/api/me/guides',headers=auth).json()) == 1
        restored_profile = c.get('/api/me/profile',headers=auth)
        assert restored_profile.status_code == 200 and restored_profile.json()['nickname'] == '重启后保留昵称'
        assert restored_profile.json()['version'] == 2
        assert c.get('/api/auth/me',headers=auth).json()['profile_version'] == 2
        favorite_replay = c.put(f'/api/spots/{spot_id}/favorite-state',headers=auth,json=favorite_body)
        assert favorite_replay.status_code == 200 and favorite_replay.json() == favorite_saved.json()
        comment_replay = c.post(f'/api/spots/{spot_id}/comments/create',headers=auth,json=comment_body)
        assert comment_replay.status_code == 200 and comment_replay.json() == comment_saved.json()
        reservations = c.get(f"/api/trips/{trip['id']}/reservations",headers=auth).json()['items']
        assert reservations[0]['reference'] == 'TEST-RESTART'
        assert reservations[0]['item_id'] == item_id
        after = c.get(f'{photo_path}/{photo_id}',headers=auth)
        assert after.status_code == 200 and after.content == before_bytes
        assert after.headers['cache-control'] == 'private, no-store'
        assert c.get(f'{photo_path}/{photo_id}').status_code == 401
        with Image.open(BytesIO(after.content)) as image:
            assert image.format == 'JPEG' and len(image.getexif()) == 0


# Only the external model transport is replaced. The real application lifespan,
# scheduler, leases, JSON validation, API permissions and SQLite remain active.
AI_RESTART_BOOTSTRAP = """
import asyncio
import json
import os
from pathlib import Path
import sys
import uvicorn
from app.main import app
from app.services import trip_ai

async def fake_chat(messages, **kwargs):
    payload = json.loads(messages[1]['content'])
    marker = Path(os.environ['TRAVEL_DATA_DIR']).parent / ('model-' + sys.argv[2])
    marker.write_text('started', encoding='utf-8')
    if sys.argv[2] == 'interrupted':
        await asyncio.Event().wait()
    return json.dumps({'operations': [{'action': 'add',
        'day_id': payload['trip']['days'][0]['id'], 'index': 0,
        'item': {'name': 'Restart test activity', 'note': 'Synthetic model response'}}]})

trip_ai.chat = fake_chat
uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')
"""


def test_ai_job_recovers_after_process_restart_and_requires_explicit_apply(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    data_dir = tmp_path/'isolated-ai-data'
    database = data_dir/'app.db'
    with running_backend(data_dir, port, bootstrap=AI_RESTART_BOOTSTRAP,
                         bootstrap_args=('interrupted',)) as c:
        assert c.post('/api/auth/register', json={
            'username': 'restart_ai', 'password': 'test-only-password'}).status_code == 201
        login = c.post('/api/auth/login', json={
            'username': 'restart_ai', 'password': 'test-only-password'})
        assert login.status_code == 200
        auth = {'Authorization': 'Bearer '+login.json()['token']}
        created = c.post('/api/trips', headers=auth, json={'title': 'AI restart test', 'days': 1})
        assert created.status_code == 201
        trip = created.json()
        trip_path = f"/api/trips/{trip['id']}"
        job_path = f'{trip_path}/ai-jobs'
        assert c.post(job_path, headers=auth, json={
            'version': trip['version'], 'request': 'Add one test activity'}).status_code == 202
        deadline = time.monotonic()+10
        while not (tmp_path/'model-interrupted').exists():
            assert time.monotonic() < deadline, 'AI worker did not start'
            time.sleep(0.05)
        with sqlite3.connect(database) as db:
            status, attempts, old_token = db.execute(
                'SELECT status, attempts, lease_token FROM trip_ai_jobs').fetchone()
            assert status == 'running' and attempts == 1 and old_token
            assert db.execute('SELECT count(*) FROM trip_proposals').fetchone()[0] == 0

    # Advance only this stopped test database's lease instead of waiting the
    # production 180 seconds or changing production scheduler settings.
    with sqlite3.connect(database) as db:
        db.execute("UPDATE trip_ai_jobs SET lease_until = '2000-01-01 00:00:00'")

    with running_backend(data_dir, port, bootstrap=AI_RESTART_BOOTSTRAP,
                         bootstrap_args=('recovered',)) as c:
        assert c.get(f'{job_path}/latest').status_code == 401
        deadline = time.monotonic()+10
        while True:
            response = c.get(f'{job_path}/latest', headers=auth)
            assert response.status_code == 200
            job = response.json()['job']
            if job['status'] == 'done':
                break
            assert job['status'] == 'running', job
            assert time.monotonic() < deadline, 'Interrupted AI job did not recover'
            time.sleep(0.05)
        proposal = job['proposal']
        assert proposal['base_version'] == trip['version']
        assert [change['action'] for change in proposal['changes']] == ['add']
        assert (tmp_path/'model-recovered').exists()
        assert c.get(trip_path, headers=auth).json() == trip
        with sqlite3.connect(database) as db:
            assert db.execute('SELECT count(*) FROM trip_proposals').fetchone()[0] == 1
            assert db.execute('SELECT status, attempts, snapshot_json, lease_token FROM trip_ai_jobs').fetchone() == (
                'done', 2, '', '')

    # A completed preview also survives a further process restart, without
    # rerunning the model or changing the trip until the user confirms it.
    with running_backend(data_dir, port, bootstrap=AI_RESTART_BOOTSTRAP,
                         bootstrap_args=('unexpected',)) as c:
        assert c.get(f'{job_path}/latest', headers=auth).json()['job']['proposal'] == proposal
        assert c.get(f'{trip_path}/ai-proposals/latest', headers=auth).json()['proposal'] == proposal
        assert c.get(trip_path, headers=auth).json() == trip
        apply_path = f"{trip_path}/ai-proposals/{proposal['id']}/apply"
        applied = c.post(apply_path, headers=auth, json={'version': trip['version']})
        assert applied.status_code == 200, applied.text
        result = applied.json()
        assert result['version'] == trip['version']+1
        assert [item['name'] for item in result['days'][0]['items']] == ['Restart test activity']
        assert c.post(apply_path, headers=auth, json={'version': trip['version']}).status_code == 409
        assert c.get(trip_path, headers=auth).json() == result
        assert not (tmp_path/'model-unexpected').exists()
        with sqlite3.connect(database) as db:
            assert db.execute('SELECT status, attempts FROM trip_ai_jobs').fetchone() == ('done', 2)
            assert db.execute('SELECT status FROM trip_proposals').fetchall() == [('applied',)]
