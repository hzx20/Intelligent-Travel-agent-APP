"""Membership reconciliation must never grant or restore editor access."""
from datetime import timedelta

from app.db.sharing_models import TripMember, TripShareLink
from app.db.trip_models import Trip
from app.routers.trip_sharing import _now
from test_trip_sharing import sharing_client, make_trip
from test_trips import account


def setup_invitation(c):
    owner, editor, stranger = (account(c, name) for name in ('status_owner', 'status_editor', 'status_stranger'))
    trip = make_trip(c, owner)
    link = c.post(f"/api/trips/{trip['id']}/sharing/links", headers=owner, json={'mode': 'edit'}).json()
    return owner, editor, stranger, trip, link


def status(c, token, user=None):
    return c.post('/api/trip-sharing/invitation-status', headers=user or {}, json={'token': token})


def test_status_without_membership_never_joins_or_increments_version(sharing_client):
    c, sessions = sharing_client
    owner, editor, stranger, trip, link = setup_invitation(c)
    assert status(c, link['token']).status_code == 401
    for user in (editor, stranger):
        for _ in range(2):
            result = status(c, link['token'], user)
            assert result.json() == {'state': 'not_joined'}
            assert result.headers['cache-control'] == 'no-store'
            assert result.headers['referrer-policy'] == 'no-referrer'
        assert c.get(f"/api/trips/{trip['id']}", headers=user).status_code == 404
    with sessions() as db:
        assert db.query(TripMember).count() == 0
    assert c.get(f"/api/trips/{trip['id']}", headers=owner).json()['version'] == trip['version']


def test_members_can_recover_after_revocation_but_removed_users_cannot(sharing_client):
    c, sessions = sharing_client
    owner, editor, stranger, trip, link = setup_invitation(c)
    path = f"/api/trips/{trip['id']}/sharing"
    expected = c.post('/api/trip-sharing/accept', headers=editor, json={'token': link['token']}).json()
    assert c.delete(f"{path}/links/{link['id']}", headers=owner).status_code == 204
    assert status(c, link['token'], editor).json() == {'state': 'joined', **expected}
    assert status(c, link['token'], owner).json() == {'state': 'joined', 'trip_id': trip['id'], 'role': 'owner'}
    assert status(c, link['token'], stranger).status_code == 404
    member = c.get(path, headers=owner).json()['members'][0]
    assert c.delete(f"{path}/members/{member['user_id']}", headers=owner).status_code == 204
    assert status(c, link['token'], editor).status_code == 404
    with sessions() as db:
        assert db.query(TripMember).count() == 0


def test_expired_invitation_recovers_only_existing_members(sharing_client):
    c, sessions = sharing_client
    _, editor, stranger, trip, link = setup_invitation(c)
    assert c.post('/api/trip-sharing/accept', headers=editor, json={'token': link['token']}).status_code == 200
    with sessions() as db:
        db.get(TripShareLink, link['id']).expires_at = _now() - timedelta(seconds=1)
        db.commit()
    assert status(c, link['token'], editor).json() == {'state': 'joined', 'trip_id': trip['id'], 'role': 'editor'}
    assert status(c, link['token'], stranger).status_code == 404


def test_valid_link_status_after_removal_is_read_only(sharing_client):
    c, sessions = sharing_client
    owner, editor, _, trip, link = setup_invitation(c)
    assert c.post('/api/trip-sharing/accept', headers=editor, json={'token': link['token']}).status_code == 200
    path = f"/api/trips/{trip['id']}/sharing"
    member = c.get(path, headers=owner).json()['members'][0]
    assert c.delete(f"{path}/members/{member['user_id']}", headers=owner).status_code == 204
    assert status(c, link['token'], editor).json() == {'state': 'not_joined'}
    assert c.get(f"/api/trips/{trip['id']}", headers=editor).status_code == 404
    with sessions() as db:
        assert db.query(TripMember).count() == 0


def test_wrong_mode_unknown_token_and_deleted_trip_reveal_no_trip(sharing_client):
    c, sessions = sharing_client
    owner, editor, _, trip, link = setup_invitation(c)
    view = c.post(f"/api/trips/{trip['id']}/sharing/links", headers=owner, json={'mode': 'view'}).json()
    for token in (view['token'], 'x' * 43):
        result = status(c, token, editor)
        assert result.status_code == 404 and 'trip_id' not in result.json()
        assert result.headers['cache-control'] == 'no-store'
    assert status(c, 'broken', editor).status_code == 422
    with sessions() as db:
        db.delete(db.get(Trip, trip['id']))
        db.commit()
    assert status(c, link['token'], owner).status_code == 404
