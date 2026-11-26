"""Footprint snapshots survive itinerary deletion, without any network server."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi import Response
from app.db.database import Base
from app.db.models import User
from app.db.trip_models import Trip, TripDay, TripItem
from app.routers.trip_footprints import FootprintIn, add_footprint, trip_footprints, all_footprints
from app.routers.trips import Versioned, delete_item


def test_footprint_survives_deleted_item():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        user = User(username='footprint', password_hash='x', nickname='游客')
        db.add(user); db.flush()
        trip = Trip(user_id=user.id, title='旅行', days=[TripDay(day_number=1, items=[TripItem(name='西湖', sort_order=0)])])
        db.add(trip); db.commit()
        item_id = trip.days[0].items[0].id
        updated = add_footprint(trip.id, FootprintIn(version=1, item_id=item_id, city='杭州'), db, user)
        delete_item(trip.id, item_id, Versioned(version=updated['version']), db, user)
        records = trip_footprints(trip.id, Response(), db, user)['items']
        assert len(records) == 1 and records[0]['place_name'] == '西湖' and records[0]['item_id'] is None
        assert len(all_footprints(Response(), city='杭州', trip_id=trip.id, db=db, user=user)['items']) == 1
        assert all_footprints(Response(), city='北京', trip_id=trip.id, db=db, user=user)['items'] == []
        other = User(username='another', password_hash='x', nickname='另一个人')
        db.add(other); db.commit()
        assert all_footprints(Response(), city='', trip_id=None, db=db, user=other)['items'] == []
    engine.dispose()
