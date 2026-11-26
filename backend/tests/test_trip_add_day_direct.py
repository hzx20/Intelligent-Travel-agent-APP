"""Appending a day respects authorization, limits and optimistic versions."""
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.database import Base
from app.db.models import User
from app.db.trip_models import Trip, TripDay, TripItem
from app.db.trip_expense_models import TripExpense
from app.db.footprint_models import TripFootprint
from app.routers.trips import Versioned, add_day, remove_day


def test_append_day_and_version_conflict():
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        owner = User(username='dayowner', password_hash='x', nickname='x')
        other = User(username='dayother', password_hash='x', nickname='y')
        db.add_all([owner, other]); db.flush()
        trip = Trip(user_id=owner.id, title='旅行', days=[TripDay(day_number=1)])
        db.add(trip); db.commit()
        with pytest.raises(HTTPException) as denied:
            add_day(trip.id, Versioned(version=1), db, other)
        assert denied.value.status_code == 404
        result = add_day(trip.id, Versioned(version=1), db, owner)
        assert [day['day_number'] for day in result['days']] == [1, 2]
        with pytest.raises(HTTPException) as stale:
            add_day(trip.id, Versioned(version=1), db, owner)
        assert stale.value.status_code == 409
    engine.dispose()


def test_only_empty_unlinked_last_day_can_be_removed():
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        user = User(username='removeday', password_hash='x', nickname='x')
        db.add(user); db.flush()
        trip = Trip(user_id=user.id, title='旅行', days=[TripDay(day_number=1), TripDay(day_number=2)])
        db.add(trip); db.commit()
        first, last = trip.days
        with pytest.raises(HTTPException) as invalid:
            remove_day(trip.id, first.id, Versioned(version=1), db, user)
        assert invalid.value.status_code == 422
        entry = TripExpense(trip_id=trip.id, day_id=last.id, category='其他', amount_minor=10, currency='CNY')
        db.add(entry); db.commit()
        with pytest.raises(HTTPException) as expense:
            remove_day(trip.id, last.id, Versioned(version=1), db, user)
        assert expense.value.status_code == 409
        db.delete(entry)
        item = TripItem(day_id=last.id, name='公园', sort_order=0)
        db.add(item); db.commit(); db.expire_all()
        with pytest.raises(HTTPException) as occupied:
            remove_day(trip.id, last.id, Versioned(version=1), db, user)
        assert occupied.value.status_code == 409
        db.delete(item); db.add(TripFootprint(user_id=user.id, trip_id=trip.id, day_number=2, place_name='旧足迹', item_id=None)); db.commit(); db.expire_all()
        with pytest.raises(HTTPException) as footprint:
            remove_day(trip.id, last.id, Versioned(version=1), db, user)
        assert footprint.value.status_code == 409
        db.query(TripFootprint).delete(); db.commit(); db.expire_all()
        result = remove_day(trip.id, last.id, Versioned(version=1), db, user)
        assert len(result['days']) == 1 and result['version'] == 2
    engine.dispose()
