"""An AI proposal is reviewed, authorized, versioned, and applied atomically."""
import json
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.db.database import Base, get_db
from app.db.models import User
from app.main import app
from app.routers.auth import get_current_user
from app.services import trip_ai
from app.routers import trips
from app.db.trip_models import Trip, TripAiJob, TripDay, TripProposal
from test_trips import account


def setup_trip(c, headers):
    trip = c.post('/api/trips', headers=headers, json={'title': '杭州', 'destination': '杭州', 'days': 2}).json()
    day = trip['days'][0]['id']
    for name in ('西湖', '公园'):
        trip = c.post(f"/api/trips/{trip['id']}/days/{day}/items", headers=headers,
                      json={'name': name, 'version': trip['version'], 'lng': 120.14, 'lat': 30.25,
                            'address': '原地址', 'note': '保留的备注'}).json()
    return trip


def test_proposal_preview_and_explicit_atomic_apply(client, monkeypatch):
    c, _ = client
    owner, other = account(c, 'ai_owner'), account(c, 'ai_other')
    trip = setup_trip(c, owner)
    first, second = trip['days'][0]['items']
    second_day = trip['days'][1]['id']
    ops = [{'action': 'edit', 'item_id': first['id'], 'changes': {'start_time': '10:00'}},
           {'action': 'move', 'item_id': second['id'], 'day_id': second_day, 'index': 0},
           {'action': 'add', 'day_id': second_day, 'index': 1,
            'item': {'name': '新建议地点', 'note': '用户需核实'}}]

    async def fake_chat(messages, **kwargs):
        assert json.loads(messages[1]['content'])['request'] == '轻松一些'
        return json.dumps({'operations': ops}, ensure_ascii=False)

    monkeypatch.setattr(trip_ai, 'chat', fake_chat)
    path = f"/api/trips/{trip['id']}/ai-proposals"
    assert c.post(path, headers=other, json={'version': trip['version'], 'request': '轻松一些'}).status_code == 404
    response = c.post(path, headers=owner, json={'version': trip['version'], 'request': '轻松一些'})
    assert response.status_code == 201, response.text
    proposal = response.json()
    assert [change['action'] for change in proposal['changes']] == ['edit', 'move', 'add']
    assert c.get(f"/api/trips/{trip['id']}", headers=owner).json() == trip
    apply = f"{path}/{proposal['id']}/apply"
    assert c.post(apply, headers=other, json={'version': trip['version']}).status_code == 404
    applied = c.post(apply, headers=owner, json={'version': trip['version']})
    assert applied.status_code == 200, applied.text
    result = applied.json()
    changed = result['days'][0]['items'][0]
    assert changed['start_time'] == '10:00' and changed['note'] == '保留的备注'
    assert changed['address'] == '原地址' and changed['lng'] == 120.14
    assert [i['name'] for i in result['days'][1]['items']] == ['公园', '新建议地点']
    assert result['days'][1]['items'][1]['lng'] is None
    assert '待核实' in result['days'][1]['items'][1]['source']
    assert result['version'] == trip['version'] + 1
    assert c.post(apply, headers=owner, json={'version': trip['version']}).status_code == 409


def test_stale_proposal_and_invalid_model_output_never_change_trip(client, monkeypatch):
    c, _ = client
    owner = account(c, 'ai_conflict')
    trip = setup_trip(c, owner)
    item_id = trip['days'][0]['items'][0]['id']
    path = f"/api/trips/{trip['id']}/ai-proposals"

    async def fake_chat(messages, **kwargs):
        return json.dumps({'operations': [{'action': 'remove', 'item_id': item_id}]})

    monkeypatch.setattr(trip_ai, 'chat', fake_chat)
    proposal = c.post(path, headers=owner, json={'version': trip['version'], 'request': '删除第一项'}).json()
    assert proposal['changes'][0]['item']['address'] == '原地址'
    current = c.patch(f"/api/trips/{trip['id']}", headers=owner,
                      json={'version': trip['version'], 'title': '手动修改过'}).json()
    assert c.get(f'{path}/latest', headers=owner).json() == {'proposal': None}
    assert c.post(f"{path}/{proposal['id']}/apply", headers=owner,
                  json={'version': proposal['base_version']}).status_code == 409
    assert c.get(f"/api/trips/{trip['id']}", headers=owner).json() == current

    async def bad_chat(messages, **kwargs):
        return json.dumps({'operations': [{'action': 'remove', 'item_id': 99999}]})
    monkeypatch.setattr(trip_ai, 'chat', bad_chat)
    assert c.post(path, headers=owner, json={'version': current['version'], 'request': '调整'}).status_code == 502
    assert c.get(f"/api/trips/{trip['id']}", headers=owner).json() == current


def test_empty_proposal_cannot_be_applied(client, monkeypatch):
    c, _ = client
    owner = account(c, 'ai_empty')
    trip = setup_trip(c, owner)
    async def fake_chat(messages, **kwargs):
        return '{"operations":[]}'
    monkeypatch.setattr(trip_ai, 'chat', fake_chat)
    path = f"/api/trips/{trip['id']}/ai-proposals"
    proposal = c.post(path, headers=owner, json={'version': trip['version'], 'request': '看看'}).json()
    assert proposal['changes'] == []
    assert c.post(f"{path}/{proposal['id']}/apply", headers=owner, json={'version': trip['version']}).status_code == 422


def test_latest_proposal_recovers_only_authorized_current_unapplied_preview(client, monkeypatch):
    c, _ = client
    owner, stranger = account(c, 'ai_recover_owner'), account(c, 'ai_recover_other')
    trip = setup_trip(c, owner)
    path = f"/api/trips/{trip['id']}/ai-proposals"
    latest = f'{path}/latest'
    assert c.get(latest, headers=stranger).status_code == 404
    assert c.get(latest, headers=owner).json() == {'proposal': None}

    async def fake_chat(messages, **kwargs):
        return json.dumps({'operations': [{'action': 'edit', 'item_id': trip['days'][0]['items'][0]['id'],
                                           'changes': {'start_time': '10:00'}}]})

    monkeypatch.setattr(trip_ai, 'chat', fake_chat)
    first = c.post(path, headers=owner, json={'version': trip['version'], 'request': '调整'}).json()
    second = c.post(path, headers=owner, json={'version': trip['version'], 'request': '再调整'}).json()
    recovered = c.get(latest, headers=owner)
    assert recovered.headers['cache-control'] == 'no-store'
    assert recovered.json() == {'proposal': second}
    assert first['id'] != second['id']
    applied = c.post(f"{path}/{second['id']}/apply", headers=owner, json={'version': trip['version']})
    assert applied.status_code == 200
    assert c.get(latest, headers=owner).json() == {'proposal': None}


def test_background_job_survives_new_requests_and_checks_version(client, monkeypatch):
    c, _ = client
    owner, stranger = account(c, 'job_owner'), account(c, 'job_stranger')
    trip = setup_trip(c, owner)
    path = f"/api/trips/{trip['id']}/ai-jobs"
    gate = threading.Event()
    calls = []

    async def slow_chat(messages, **kwargs):
        calls.append(1)
        while not gate.is_set():
            await asyncio.sleep(0.01)
        return json.dumps({'operations': [{'action': 'remove', 'item_id': trip['days'][0]['items'][0]['id']}]})

    monkeypatch.setattr(trip_ai, 'chat', slow_chat)
    try:
        payload = {'version': trip['version'], 'request': '删除第一项'}
        assert c.post(path, headers=stranger, json=payload).status_code == 404
        assert c.post(path, headers=owner, json=payload).status_code == 202
        assert c.post(path, headers=owner, json=payload).status_code == 202
        assert c.post(path, headers=owner, json={**payload, 'request': '其他要求'}).status_code == 409
        assert c.get(f'{path}/latest', headers=stranger).status_code == 404
        running = c.get(f'{path}/latest', headers=owner)
        assert running.headers['cache-control'] == 'no-store'
        assert running.json()['job']['status'] == 'running'
        gate.set()
        for _ in range(400):
            job = c.get(f'{path}/latest', headers=owner).json()['job']
            if job['status'] != 'running':
                break
            time.sleep(0.01)
        assert job['status'] == 'done' and len(calls) == 1
        assert job['proposal']['changes'][0]['action'] == 'remove'
        assert c.get(f"/api/trips/{trip['id']}", headers=owner).json() == trip
        updated = c.patch(f"/api/trips/{trip['id']}", headers=owner,
                          json={'version': trip['version'], 'title': '手动更新'}).json()
        assert c.get(f'{path}/latest', headers=owner).json() == {'job': None}
        assert c.post(f"/api/trips/{trip['id']}/ai-proposals/{job['proposal']['id']}/apply",
                      headers=owner, json={'version': trip['version']}).status_code == 409
        assert updated['version'] == trip['version'] + 1
    finally:
        gate.set()



def test_background_job_rejects_changes_made_while_generating(client, monkeypatch):
    c, Session = client
    owner = account(c, 'job_conflict')
    trip = setup_trip(c, owner)
    gate = threading.Event()
    started = threading.Event()

    async def slow_chat(messages, **kwargs):
        started.set()
        while not gate.is_set():
            await asyncio.sleep(0.01)
        return '{"operations":[]}'

    monkeypatch.setattr(trip_ai, 'chat', slow_chat)
    try:
        path = f"/api/trips/{trip['id']}/ai-jobs"
        assert c.post(path, headers=owner, json={'version': trip['version'], 'request': '调整'}).status_code == 202
        assert started.wait(3)
        updated = c.patch(f"/api/trips/{trip['id']}", headers=owner,
                          json={'version': trip['version'], 'title': '新标题'})
        assert updated.status_code == 200
        stale = c.get(f'{path}/latest', headers=owner).json()['job']
        assert stale == {'status': 'stale_running', 'version': trip['version']}
        assert c.post(path, headers=owner, json={'version': updated.json()['version'], 'request': '重新调整'}).status_code == 409
        gate.set()
        for _ in range(400):
            with Session() as db:
                job = db.query(TripAiJob).one()
                status, error = job.status, job.error
            if status == 'error':
                break
            time.sleep(0.01)
        assert status == 'error' and '已更新' in error
        with Session() as db:
            assert db.query(TripProposal).count() == 0
        assert c.get(f'{path}/latest', headers=owner).json() == {'job': None}
    finally:
        gate.set()


def test_background_job_checks_revoked_collaborator_access(client, monkeypatch):
    c, Session = client
    owner, editor = account(c, 'job_share_owner'), account(c, 'job_share_editor')
    trip = setup_trip(c, owner)
    invite = c.post(f"/api/trips/{trip['id']}/sharing/links", headers=owner, json={'mode': 'edit'}).json()
    assert c.post('/api/trip-sharing/accept', headers=editor, json={'token': invite['token']}).status_code == 200
    trip = c.get(f"/api/trips/{trip['id']}", headers=editor).json()
    started, gate = threading.Event(), threading.Event()

    async def slow_chat(messages, **kwargs):
        started.set()
        while not gate.is_set():
            await asyncio.sleep(0.01)
        return '{"operations":[]}'

    monkeypatch.setattr(trip_ai, 'chat', slow_chat)
    try:
        path = f"/api/trips/{trip['id']}/ai-jobs"
        assert c.post(path, headers=editor, json={'version': trip['version'], 'request': '调整'}).status_code == 202
        assert started.wait(3)
        member = c.get(f"/api/trips/{trip['id']}/sharing", headers=owner).json()['members'][0]
        assert c.delete(f"/api/trips/{trip['id']}/sharing/members/{member['user_id']}", headers=owner).status_code == 204
        gate.set()
        for _ in range(300):
            with Session() as db:
                status = db.query(TripAiJob).one().status
            if status == 'error':
                break
            time.sleep(0.01)
        assert status == 'error'
        assert c.get(f'{path}/latest', headers=editor).status_code == 404
        with Session() as db:
            assert db.query(TripProposal).count() == 0
    finally:
        gate.set()


def test_expired_lease_recovers_once_and_fences_old_worker(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}", connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        user = User(username='lease_owner', password_hash='unused', nickname='测试')
        db.add(user); db.flush()
        trip = Trip(user_id=user.id, title='测试', days=[TripDay(day_number=1)])
        db.add(trip); db.flush()
        snapshot = trips._serialize(trip)
        job = TripAiJob(user_id=user.id, trip_id=trip.id, version=trip.version, request='安排',
                        snapshot_json=json.dumps(snapshot, ensure_ascii=False), status='queued')
        db.add(job); db.commit(); job_id = job.id

    async def fake_propose(snapshot, request):
        return [], []

    monkeypatch.setattr(trip_ai, 'propose', fake_propose)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: trips._claim_ai_jobs(sessions), range(2)))
    first = [claim for result in claims for claim in result]
    assert len(first) == 1
    old_token = first[0][1]
    with sessions() as db:
        job = db.get(TripAiJob, job_id)
        job.lease_until = trips._utcnow() - timedelta(seconds=1)
        db.commit()
    second = trips._claim_ai_jobs(sessions)
    assert len(second) == 1 and second[0][1] != old_token
    asyncio.run(trips._run_ai_job(job_id, old_token, sessions))
    asyncio.run(trips._run_ai_job(job_id, second[0][1], sessions))
    with sessions() as db:
        job = db.get(TripAiJob, job_id)
        assert job.status == 'done' and job.attempts == 2 and job.snapshot_json == ''
        assert db.query(TripProposal).count() == 1
    engine.dispose()


def test_pending_job_resumes_after_application_restart(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'restart.db'}", connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as db:
        user = User(username='restart_owner', password_hash='unused', nickname='测试')
        db.add(user); db.flush()
        trip = Trip(user_id=user.id, title='测试', days=[TripDay(day_number=1)])
        db.add(trip); db.commit(); trip_id, user_id = trip.id, user.id

    def override_db():
        with sessions() as db:
            yield db

    def override_user():
        with sessions() as db:
            return db.get(User, user_id)

    started = threading.Event()
    calls = []

    async def interrupted_then_success(snapshot, request):
        calls.append(1)
        if len(calls) == 1:
            started.set()
            await asyncio.Event().wait()
        return [], []

    monkeypatch.setattr(trip_ai, 'propose', interrupted_then_success)
    monkeypatch.setattr(trips, 'AI_SESSION_FACTORY', sessions)
    monkeypatch.setattr(trips, 'AI_LEASE_SECONDS', 1)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        with TestClient(app) as c:
            path = f'/api/trips/{trip_id}/ai-jobs'
            assert c.post(path, json={'version': 1, 'request': '安排'}).status_code == 202
            assert started.wait(3)
        with TestClient(app) as c:
            for _ in range(500):
                job = c.get(f'{path}/latest').json()['job']
                if job['status'] == 'done':
                    break
                time.sleep(0.01)
            assert job['status'] == 'done' and len(calls) == 2
        with sessions() as db:
            assert db.query(TripProposal).count() == 1
            assert db.query(TripAiJob).one().attempts == 2
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_invalid_json_is_retried_before_returning_a_preview(client, monkeypatch):
    c, _ = client
    owner = account(c, 'ai_retry')
    trip = setup_trip(c, owner)
    attempts = []

    async def fake_chat(messages, **kwargs):
        attempts.append(messages)
        if len(attempts) == 1:
            return '{"operations":[{"action":"remove"},"remove":{"item_id":1}]}'
        return json.dumps({'operations': [{'action': 'remove', 'item_id': trip['days'][0]['items'][0]['id']}]})

    monkeypatch.setattr(trip_ai, 'chat', fake_chat)
    response = c.post(f"/api/trips/{trip['id']}/ai-proposals", headers=owner,
                      json={'version': trip['version'], 'request': '删除第一项'})
    assert response.status_code == 201 and len(attempts) == 2
    assert response.json()['changes'][0]['action'] == 'remove'


def test_common_model_json_errors_are_repaired_then_validated():
    text = '{"operations":[{"action":"add","day_id":1,"index":0,"item":{"name":"湖"}},"add":{"action":"add","day_id":1,"index":1,"item":{"name":"山"}}}'
    parsed = trip_ai.parse_model_json(text)
    final, changes = trip_ai.simulate({'days': [{'id': 1, 'day_number': 1, 'items': []}]}, parsed['operations'])
    assert [item['name'] for item in final['days'][0]['items']] == ['湖', '山']
    assert len(changes) == 2


def test_guest_ai_preview_validates_snapshot_and_does_not_persist(client, monkeypatch):
    from app.routers import trip_imports
    from app.db.trip_models import Trip
    c, sessions = client
    trip_imports._guest_ai_peers.clear()
    seen = []
    async def fake_chat(messages, **kwargs):
        payload = json.loads(messages[1]['content']); seen.append(payload)
        return json.dumps({'operations':[{'action':'move','item_id':3,'day_id':2,'index':0},
                                          {'action':'add','day_id':1,'index':0,'item':{'name':'建议地点'}}]}, ensure_ascii=False)
    monkeypatch.setattr(trip_ai, 'chat', fake_chat)
    trip = {'version':4,'title':'杭州','destination':'杭州','start_date':'2026-11-20',
            'days':[{'id':1,'day_number':1,'theme':'','items':[{'id':3,'name':'西湖'}]},
                    {'id':2,'day_number':2,'theme':'','items':[]}],
            'reservations':[{'reference':'never-send'}]}
    body = {'trip':trip,'request':'调整安排'}
    response = c.post('/api/trips/guest-ai-preview', json=body)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['base_version'] == 4 and [x['action'] for x in response.json()['changes']] == ['move','add']
    assert 'reservations' not in seen[0]['trip'] and 'never-send' not in str(seen)
    with sessions() as db: assert db.query(Trip).count() == 0
    bad = {'trip':{**trip, 'days':[trip['days'][0],{**trip['days'][1],'id':3}]}, 'request':'调整'}
    assert c.post('/api/trips/guest-ai-preview', json=bad).status_code == 422
    assert c.post('/api/trips/guest-ai-preview', json=body).status_code == 200
    assert c.post('/api/trips/guest-ai-preview', json=body).status_code == 429
    trip_imports._guest_ai_peers.clear()
