"""Sharing tokens are capabilities; membership is explicit and owner-controlled."""
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db
from app.db.sharing_models import TripMember, TripShareLink
from app.routers.auth import router as auth_router
from app.routers.trips import router as trips_router
from app.routers.trip_sharing import router as sharing_router
from test_trips import account


@pytest.fixture()
def sharing_client():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(trips_router)
    app.include_router(sharing_router)

    def override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c, session_factory
    engine.dispose()


@pytest.fixture()
def file_sharing_client(tmp_path):
    # Separate connections against a file database are required to exercise SQLite locks.
    engine = create_engine(f"sqlite:///{tmp_path / 'sharing.db'}", connect_args={'check_same_thread': False, 'timeout': 5})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    app = FastAPI()
    for router in (auth_router, trips_router, sharing_router):
        app.include_router(router)
    def override_db():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c, sessions, app, engine
    engine.dispose()


def make_trip(c, owner):
    trip = c.post('/api/trips', headers=owner, json={'title': '杭州旅行', 'days': 1}).json()
    day = trip['days'][0]['id']
    return c.post(f"/api/trips/{trip['id']}/days/{day}/items", headers=owner,
                  json={'version': trip['version'], 'name': '西湖', 'note': '需要提前预约'}).json()


def test_view_link_does_not_grant_access_and_can_be_revoked(sharing_client):
    c, Session = sharing_client
    owner, stranger = account(c, 'share_owner'), account(c, 'share_stranger')
    trip = make_trip(c, owner)
    trip_id = trip['id']
    path = f'/api/trips/{trip_id}/sharing'
    assert c.post(f'{path}/links', headers=stranger, json={'mode': 'view'}).status_code == 404
    created = c.post(f'{path}/links', headers=owner, json={'mode': 'view'})
    assert created.status_code == 201 and created.headers['cache-control'] == 'no-store'
    link = created.json()
    token = link['token']
    assert len(token) == 43
    with Session() as db:
        stored = db.get(TripShareLink, link['id'])
        assert stored.token_hash != token and len(stored.token_hash) == 64
    assert token not in str(c.get(path, headers=owner).json())
    result = c.post('/api/trip-sharing/view', json={'token': token})
    assert result.status_code == 200 and result.headers['cache-control'] == 'no-store'
    data = result.json()
    assert data['days'][0]['items'][0]['note'] == '需要提前预约'
    assert 'user_id' not in data and 'id' not in data and 'places' not in data and 'source_plan_id' not in data
    assert c.post('/api/trip-sharing/accept', headers=stranger, json={'token': token}).status_code == 404
    assert c.get(f'/api/trips/{trip_id}', headers=stranger).status_code == 404
    assert c.delete(f"{path}/links/{link['id']}", headers=stranger).status_code == 404
    assert c.delete(f"{path}/links/{link['id']}", headers=owner).status_code == 204
    assert c.post('/api/trip-sharing/view', json={'token': token}).status_code == 404
    assert c.get(path, headers=owner).json()['links'][0]['status'] == 'revoked'


def test_edit_invitation_requires_explicit_login_and_can_be_removed(sharing_client):
    c, Session = sharing_client
    owner, editor = account(c, 'invite_owner'), account(c, 'invite_editor')
    trip = make_trip(c, owner)
    trip_id = trip['id']
    path = f'/api/trips/{trip_id}/sharing'
    link = c.post(f'{path}/links', headers=owner, json={'mode': 'edit'}).json()
    token = link['token']
    assert c.post('/api/trip-sharing/view', json={'token': token}).status_code == 404
    assert c.post('/api/trip-sharing/accept', json={'token': token}).status_code == 401
    with Session() as db:
        assert db.query(TripMember).count() == 0
    accept = c.post('/api/trip-sharing/accept', headers=editor, json={'token': token})
    assert accept.status_code == 200 and accept.json() == {'trip_id': trip_id, 'role': 'editor'}
    assert [t['id'] for t in c.get('/api/trips', headers=editor).json()['items']] == [trip_id]
    editable = c.get(f'/api/trips/{trip_id}', headers=editor).json()
    editable = c.patch(f'/api/trips/{trip_id}', headers=editor,
                       json={'version': editable['version'], 'title': '同行修改'}).json()
    assert c.get(f'/api/trips/{trip_id}', headers=owner).json()['title'] == '同行修改'
    assert c.post('/api/trip-sharing/accept', headers=editor, json={'token': token}).status_code == 200
    members = c.get(path, headers=owner).json()['members']
    assert len(members) == 1 and members[0]['role'] == 'editor'
    assert c.get(path, headers=editor).status_code == 404
    member_id = members[0]['user_id']
    assert c.delete(f'{path}/members/{member_id}', headers=editor).status_code == 404
    assert c.delete(f'{path}/members/{member_id}', headers=owner).status_code == 204
    assert c.get(path, headers=owner).json()['members'] == []
    assert c.get(f'/api/trips/{trip_id}', headers=owner).json()['version'] == editable['version'] + 1
    assert c.get(f'/api/trips/{trip_id}', headers=editor).status_code == 404
    assert c.patch(f'/api/trips/{trip_id}', headers=editor,
                   json={'version': editable['version'], 'title': '撤权后的旧请求'}).status_code == 404
    # A still-active reusable invitation permits rejoining, as the UI explains.
    assert c.post('/api/trip-sharing/accept', headers=editor, json={'token': token}).status_code == 200
    assert c.delete(f"{path}/links/{link['id']}", headers=owner).status_code == 204
    assert c.post('/api/trip-sharing/accept', headers=editor, json={'token': token}).status_code == 404
    assert len(c.get(path, headers=owner).json()['members']) == 1


def test_accepted_editor_can_edit_but_cannot_manage_sharing(sharing_client):
    c, _ = sharing_client
    owner, editor = account(c, 'editor_owner'), account(c, 'editor_member')
    trip = make_trip(c, owner)
    trip_id = trip['id']
    path = f'/api/trips/{trip_id}/sharing'
    invite = c.post(f'{path}/links', headers=owner, json={'mode': 'edit'}).json()
    assert c.post('/api/trip-sharing/accept', headers=editor, json={'token': invite['token']}).status_code == 200
    assert [item['id'] for item in c.get('/api/trips', headers=editor).json()['items']] == [trip_id]
    day_id = trip['days'][0]['id']
    edited = c.post(f'/api/trips/{trip_id}/days/{day_id}/items', headers=editor,
                    json={'version': trip['version'], 'name': '同行者添加的地点'})
    assert edited.status_code == 201
    assert [item['name'] for item in c.get(f'/api/trips/{trip_id}', headers=owner).json()['days'][0]['items']] == ['西湖', '同行者添加的地点']
    assert c.post(f'{path}/links', headers=editor, json={'mode': 'view'}).status_code == 404
    assert c.delete(f"{path}/links/{invite['id']}", headers=editor).status_code == 404


def test_expired_and_random_tokens_do_not_reveal_or_grant(sharing_client):
    c, Session = sharing_client
    owner, other = account(c, 'expiry_owner'), account(c, 'expiry_other')
    trip = make_trip(c, owner)
    path = f"/api/trips/{trip['id']}/sharing"
    link = c.post(f'{path}/links', headers=owner, json={'mode': 'edit', 'expires_days': 1}).json()
    with Session() as db:
        stored = db.get(TripShareLink, link['id'])
        stored.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
    assert c.post('/api/trip-sharing/accept', headers=other, json={'token': link['token']}).status_code == 404
    assert c.post('/api/trip-sharing/view', json={'token': 'A' * 43}).status_code == 404
    assert c.get(path, headers=owner).json()['links'][0]['status'] == 'expired'
    with Session() as db:
        assert db.query(TripMember).count() == 0


def test_accept_does_not_create_member_when_revocation_commits_first(file_sharing_client):
    c, Session, app, engine = file_sharing_client
    owner, editor = account(c, 'race_owner'), account(c, 'race_editor')
    trip = make_trip(c, owner)
    path = f"/api/trips/{trip['id']}/sharing"
    link = c.post(f'{path}/links', headers=owner, json={'mode': 'edit'}).json()
    before_claim, resume = Event(), Event()

    def pause_before_claim(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('UPDATE TRIP_SHARE_LINKS') and 'token_hash' in statement:
            before_claim.set()
            if not resume.wait(5):
                raise TimeoutError('test claim not released')

    event.listen(engine, 'before_cursor_execute', pause_before_claim)
    def accept():
        with TestClient(app) as client:
            return client.post('/api/trip-sharing/accept', headers=editor, json={'token': link['token']}).status_code
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(accept)
            assert before_claim.wait(5)
            assert c.delete(f"{path}/links/{link['id']}", headers=owner).status_code == 204
            resume.set()
            assert future.result(timeout=5) == 404
    finally:
        resume.set()
        event.remove(engine, 'before_cursor_execute', pause_before_claim)
    with Session() as db:
        assert db.query(TripMember).count() == 0


def test_accept_claim_serializes_before_concurrent_revocation(file_sharing_client):
    c, Session, app, engine = file_sharing_client
    owner, editor = account(c, 'claim_owner'), account(c, 'claim_editor')
    trip = make_trip(c, owner)
    path = f"/api/trips/{trip['id']}/sharing"
    link = c.post(f'{path}/links', headers=owner, json={'mode': 'edit'}).json()
    claimed, resume, revoke_attempt = Event(), Event(), Event()

    def pause_after_claim(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('UPDATE TRIP_SHARE_LINKS') and 'token_hash' in statement:
            claimed.set()
            if not resume.wait(5):
                raise TimeoutError('test claim not released')

    def detect_revocation_write(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('UPDATE TRIP_SHARE_LINKS') and 'token_hash' not in statement:
            revoke_attempt.set()

    event.listen(engine, 'after_cursor_execute', pause_after_claim)
    event.listen(engine, 'before_cursor_execute', detect_revocation_write)
    def accept():
        with TestClient(app) as client:
            return client.post('/api/trip-sharing/accept', headers=editor, json={'token': link['token']}).status_code
    def revoke():
        with TestClient(app) as client:
            return client.delete(f"{path}/links/{link['id']}", headers=owner).status_code
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            accepted = pool.submit(accept)
            assert claimed.wait(5)
            revoked = pool.submit(revoke)
            assert revoke_attempt.wait(5)
            # The revoke write has started but must not commit before the claim.
            assert not revoked.done()
            resume.set()
            assert accepted.result(timeout=5) == 200
            assert revoked.result(timeout=5) == 204
    finally:
        resume.set()
        event.remove(engine, 'after_cursor_execute', pause_after_claim)
        event.remove(engine, 'before_cursor_execute', detect_revocation_write)
    with Session() as db:
        assert db.query(TripMember).count() == 1
        assert db.get(TripShareLink, link['id']).revoked_at is not None


def test_editor_cannot_write_if_removed_before_version_claim(file_sharing_client):
    c, Session, app, engine = file_sharing_client
    owner, editor = account(c, 'edit_race_owner'), account(c, 'edit_race_editor')
    trip = make_trip(c, owner)
    path = f"/api/trips/{trip['id']}/sharing"
    link = c.post(f'{path}/links', headers=owner, json={'mode': 'edit'}).json()
    assert c.post('/api/trip-sharing/accept', headers=editor, json={'token': link['token']}).status_code == 200
    members = c.get(path, headers=owner).json()['members']
    assert c.get(f"/api/trips/{trip['id']}", headers=editor).status_code == 200
    pending_claim, resume = Event(), Event()

    def pause_before_claim(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('UPDATE TRIPS') and 'AND trips.version' in statement:
            pending_claim.set()
            if not resume.wait(5):
                raise TimeoutError('test claim not released')

    event.listen(engine, 'before_cursor_execute', pause_before_claim)
    def edit():
        with TestClient(app) as client:
            day_id = trip['days'][0]['id']
            return client.post(f"/api/trips/{trip['id']}/days/{day_id}/items", headers=editor,
                               json={'version': trip['version'], 'name': '越权项目'}).status_code
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(edit)
            assert pending_claim.wait(5)
            assert c.delete(f"{path}/members/{members[0]['user_id']}", headers=owner).status_code == 204
            resume.set()
            assert future.result(timeout=5) == 409
    finally:
        resume.set()
        event.remove(engine, 'before_cursor_execute', pause_before_claim)
    result = c.get(f"/api/trips/{trip['id']}", headers=owner).json()
    assert [item['name'] for item in result['days'][0]['items']] == ['西湖']
    assert c.get(f"/api/trips/{trip['id']}", headers=editor).status_code == 404
