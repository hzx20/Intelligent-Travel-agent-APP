from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from app.db.trip_models import Trip, TripDay, TripCreationRequest
from test_trips import account


def test_retry_returns_current_trip_without_overwriting_changes_and_rejects_changed_payload(client):
    c, sessions = client
    auth = account(c, 'creation_owner')
    body = {'request_id': str(uuid4()), 'title': '杭州', 'destination': ' 杭州 ', 'days': 2}
    created = c.post('/api/trips', headers=auth, json=body)
    assert created.status_code == 201
    trip = created.json()
    edited = c.patch(f"/api/trips/{trip['id']}", headers=auth,
                     json={'version': trip['version'], 'title': '创建后已编辑'})
    assert edited.status_code == 200
    retry = c.post('/api/trips', headers=auth, json={**body, 'title': ' 杭州 ', 'destination': '杭州'})
    assert retry.status_code == 200 and retry.json() == edited.json()
    assert retry.headers['cache-control'] == 'no-store'
    assert c.post('/api/trips', headers=auth, json={**body, 'days': 3}).status_code == 409
    with sessions() as db:
        assert db.query(Trip).count() == 1
        assert db.query(TripDay).count() == 2
        assert db.query(TripCreationRequest).count() == 1


def test_concurrent_identical_attempt_creates_one_trip_and_no_orphan_days(client):
    c, sessions = client
    auth = account(c, 'creation_race')
    body = {'request_id': str(uuid4()), 'title': '并发创建', 'days': 3}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: c.post('/api/trips', headers=auth, json=body), range(2)))
    assert sorted(result.status_code for result in results) == [200, 201]
    assert results[0].json()['id'] == results[1].json()['id']
    with sessions() as db:
        assert db.query(Trip).count() == 1
        assert db.query(TripDay).count() == 3
        assert db.query(TripCreationRequest).count() == 1


def test_request_ids_are_account_scoped_validated_and_optional_for_legacy_clients(client):
    c, sessions = client
    first, second = account(c, 'creation_first'), account(c, 'creation_second')
    body = {'request_id': str(uuid4()), 'title': '独立账号'}
    assert c.get('/api/trips/create-capabilities').status_code == 401
    caps = c.get('/api/trips/create-capabilities', headers=first)
    assert caps.status_code == 200 and caps.json()['idempotent_creation']
    assert c.post('/api/trips', json=body).status_code == 401
    assert c.post('/api/trips', headers=first, json={**body, 'request_id': 'not-a-uuid'}).status_code == 422
    a = c.post('/api/trips', headers=first, json=body)
    b = c.post('/api/trips', headers=second, json={**body, 'title': '另一个账号'})
    assert a.status_code == b.status_code == 201 and a.json()['id'] != b.json()['id']
    legacy = [c.post('/api/trips', headers=first, json={'title': '旧客户端'}) for _ in range(2)]
    assert all(result.status_code == 201 for result in legacy)
    assert legacy[0].json()['id'] != legacy[1].json()['id']
    with sessions() as db:
        assert db.query(TripCreationRequest).count() == 2


def test_invalid_attempt_does_not_reserve_key_and_deleted_trip_is_not_recreated(client):
    c, sessions = client
    auth = account(c, 'creation_deleted')
    body = {'request_id': str(uuid4()), 'title': '   '}
    assert c.post('/api/trips', headers=auth, json=body).status_code == 422
    with sessions() as db:
        assert db.query(TripCreationRequest).count() == 0
    body['title'] = '有效旅行'
    result = c.post('/api/trips', headers=auth, json=body)
    assert result.status_code == 201
    with sessions() as db:
        db.delete(db.get(Trip, result.json()['id'])); db.commit()
        assert db.query(TripCreationRequest).one().trip_id is None
    replacement = c.post('/api/trips/create', headers=auth,
                         json={'request_id': str(uuid4()), 'title': '后来新建的另一份'})
    assert replacement.status_code == 201
    assert c.post('/api/trips', headers=auth, json=body).status_code == 410
    with sessions() as db:
        assert db.query(Trip).count() == 1
        assert db.query(Trip).one().title == '后来新建的另一份'
        assert db.query(TripCreationRequest).count() == 2


def test_dedicated_creation_route_requires_key_and_replays(client):
    c, _ = client
    auth = account(c, 'creation_dedicated')
    assert c.post('/api/trips/create', headers=auth, json={'title': '必须保护'}).status_code == 422
    body = {'title': '可靠创建', 'request_id': str(uuid4())}
    first = c.post('/api/trips/create', headers=auth, json=body)
    second = c.post('/api/trips/create', headers=auth, json=body)
    assert first.status_code == 201 and second.status_code == 200
    assert first.json() == second.json()
