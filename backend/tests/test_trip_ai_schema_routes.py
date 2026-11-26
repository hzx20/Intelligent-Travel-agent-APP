"""Direct route/transaction checks with in-memory SQLite and immediate model stubs."""
import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import User
from app.db.trip_models import Trip, TripDay, TripItem, TripProposal
from app.routers import trips
from app.services import trip_ai
from test_trip_ai_schema import run_immediate


@pytest.fixture()
def owned_trip():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(username='schema_route_owner', password_hash='synthetic-no-login')
        db.add(user)
        db.flush()
        trip = Trip(user_id=user.id, title='整数校验合成旅行', days=[TripDay(day_number=1, items=[
            TripItem(sort_order=0, name='保留安排', note='原备注', lng=120.14, lat=30.25),
        ])])
        db.add(trip)
        db.commit()
        yield db, user, trip.id, trip.days[0].items[0].id
    engine.dispose()


def test_invalid_boolean_identifier_never_persists_a_proposal_or_deletes_item(owned_trip, monkeypatch):
    db, user, trip_id, item_id = owned_trip
    calls = []
    async def reply(*args, **kwargs):
        calls.append(1)
        return '{"operations":[{"action":"remove","item_id":true}]}'
    monkeypatch.setattr(trip_ai, 'chat', reply)
    with pytest.raises(HTTPException) as error:
        run_immediate(trips.create_proposal(trip_id, trips.ProposalIn(version=1, request='调整行程'), db, user))
    assert error.value.status_code == 502 and len(calls) == 2
    assert db.get(TripItem, item_id).note == '原备注'
    assert db.get(Trip, trip_id).version == 1
    assert db.query(TripProposal).count() == 0


def test_valid_preview_stays_read_only_until_explicit_apply(owned_trip, monkeypatch):
    db, user, trip_id, item_id = owned_trip
    async def reply(*args, **kwargs):
        return json.dumps({'operations': [{'action': 'edit', 'item_id': item_id,
                                         'changes': {'note': '确认后修改', 'duration_min': 0}}]})
    monkeypatch.setattr(trip_ai, 'chat', reply)
    preview = run_immediate(trips.create_proposal(trip_id, trips.ProposalIn(version=1, request='调整行程'), db, user))
    assert db.get(TripItem, item_id).note == '原备注'
    assert db.get(Trip, trip_id).version == 1
    changed = trips.apply_proposal(trip_id, preview['id'], trips.Versioned(version=1), db, user)
    assert changed['version'] == 2 and changed['days'][0]['items'][0]['note'] == '确认后修改'
    assert changed['days'][0]['items'][0]['duration_min'] == 0
    assert changed['days'][0]['items'][0]['lng'] == 120.14


def test_ai_insertion_retires_a_previously_chosen_leg(owned_trip):
    db, user, trip_id, item_id = owned_trip
    day = db.get(Trip, trip_id).days[0]
    end = TripItem(sort_order=1, name='已确认地点', lng=120.16, lat=30.26)
    day.items.append(end)
    db.commit()
    saved = trips.set_route_preference(trip_id, day.id, trips.RoutePreferenceWrite(
        version=1, origin_id=item_id, destination_id=end.id, mode='walking'), db, user)
    assert saved['days'][0]['route_preferences'] == {f'{item_id}:{end.id}': 'walking'}
    proposal = TripProposal(trip_id=trip_id, base_version=saved['version'], request='插入一站', operations_json=json.dumps([
        {'action': 'add', 'day_id': day.id, 'index': 1, 'item': {'name': '未确认餐厅', 'kind': '餐饮'}},
    ]))
    db.add(proposal)
    db.commit()
    changed = trips.apply_proposal(trip_id, proposal.id, trips.Versioned(version=2), db, user)
    assert [item['name'] for item in changed['days'][0]['items']] == ['保留安排', '未确认餐厅', '已确认地点']
    assert changed['days'][0]['route_preferences'] == {}
    assert db.get(TripDay, day.id).route_preferences == {}
