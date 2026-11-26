"""Copy a browser-local trip into an account atomically and idempotently."""
from datetime import date
import hashlib
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.guest_import_models import GuestTripImport
from app.db.models import User
from app.db.trip_models import Trip, TripDay, TripItem, TripPlace
from app.db.reservation_models import TripReservation
from app.routers.auth import get_current_user
from app.routers.trips import ItemData, PlaceWrite, _serialize, _trip
from app.services import route_preferences as route_choices
from app.services.walking_preferences import WalkingPreferences

router = APIRouter(prefix="/api/trips", tags=["guest trip import"])


class GuestPlace(PlaceWrite):
    version: int = 1


class GuestDay(BaseModel):
    day_number: int = Field(ge=1, le=30)
    theme: str = Field(default="", max_length=100)
    items: list[ItemData] = Field(max_length=200)
    route_preferences: dict[str, route_choices.RouteMode] = Field(default_factory=dict, max_length=199)

    @model_validator(mode='after')
    def current_pairs(self):
        stops = [(index, item) for index, item in enumerate(self.items) if route_choices.is_stop(item)]
        allowed = {f'{start}:{end}' for (start, origin), (end, destination) in zip(stops, stops[1:])
                   if route_choices.located(origin) and route_choices.located(destination)}
        if self.route_preferences.keys() - allowed:
            raise ValueError('交通方式关联的相邻地点不存在')
        return self


class ReservationLocation(BaseModel):
    day_number: int = Field(ge=1, le=30)
    item_index: int = Field(ge=0, le=199)


class GuestReservation(BaseModel):
    kind: Literal['住宿', '交通', '门票', '其他']
    title: str = Field(min_length=1, max_length=160)
    status: Literal['待预订', '已预订', '已取消']
    start_date: date | None = None
    end_date: date | None = None
    item_name: str = Field(default='', max_length=160)
    location: ReservationLocation | None = None
    reference: str = Field(default='', max_length=150)
    note: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def valid_record(self):
        if not self.title.strip() or (self.start_date and self.end_date and self.end_date < self.start_date):
            raise ValueError('预订名称或日期不正确')
        return self


class GuestSnapshot(BaseModel):
    id: str = Field(pattern=r"^guest-[A-Za-z0-9-]{1,74}$", max_length=80)
    version: int = Field(ge=1, le=2147483647)
    title: str = Field(min_length=1, max_length=100)
    destination: str = Field(default="", max_length=100)
    start_date: date | None = None
    status: Literal['planning', 'upcoming', 'completed'] = 'planning'
    days: list[GuestDay] = Field(min_length=1, max_length=30)
    places: list[GuestPlace] = Field(max_length=500)
    reservations: list[GuestReservation] = Field(default_factory=list, max_length=500)
    walking_preferences: WalkingPreferences | None = None

    @model_validator(mode='after')
    def valid_snapshot(self):
        if not self.title.strip() or [d.day_number for d in self.days] != list(range(1, len(self.days) + 1)):
            raise ValueError('旅行名称或日期顺序不正确')
        if sum(len(d.items) for d in self.days) > 500:
            raise ValueError('行程项目过多')
        ids = [p.amap_id for p in self.places if p.amap_id]
        if len(ids) != len(set(ids)):
            raise ValueError('待安排地点包含重复 POI')
        for reservation in self.reservations:
            loc = reservation.location
            if loc and (loc.day_number > len(self.days) or loc.item_index >= len(self.days[loc.day_number - 1].items)):
                raise ValueError('预订记录关联的行程项目不存在')
        return self


class GuestImportIn(BaseModel):
    guest_id: str = Field(pattern=r"^guest-[A-Za-z0-9-]{1,74}$", max_length=80)
    guest_version: int = Field(ge=1, le=2147483647)
    trip: GuestSnapshot

    @model_validator(mode='after')
    def same_identity(self):
        if self.trip.id != self.guest_id or self.trip.version != self.guest_version:
            raise ValueError('本地旅行标识或版本不一致')
        return self


@router.get('/guest-import-capabilities')
def guest_import_capabilities(response: Response, user: User = Depends(get_current_user)):
    response.headers['Cache-Control'] = 'no-store'
    return {'reservation_records': True, 'idempotent_import': True, 'status_lookup': True,
            'walking_preferences': True, 'route_preferences': True}


def _digest(body):
    snapshot = body.trip.model_dump(mode='json')
    # Earlier imports hashed snapshots without this field; preserve idempotency for old trips.
    if not snapshot['reservations']:
        snapshot.pop('reservations')
    prefs = body.trip.walking_preferences
    active_prefs = prefs.model_dump(exclude_defaults=True) if prefs else {}
    if active_prefs:
        snapshot['walking_preferences'] = active_prefs
    else:
        snapshot.pop('walking_preferences', None)
    for day in snapshot['days']:
        if not day['route_preferences']:
            day.pop('route_preferences')
    return hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                       separators=(',', ':')).encode('utf-8')).hexdigest()


def _guest_result(body, user, trip, already_imported=True):
    return {'owner_id': user.id, 'guest_id': body.guest_id, 'guest_version': body.guest_version,
            'state': 'copied' if trip else 'not_created', 'trip': trip, 'already_imported': bool(trip) and already_imported}


@router.post('/import-guest/check')
def check_import(body: GuestImportIn, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Read-only lookup using exactly the originally submitted snapshot, without creating data."""
    response.headers['Cache-Control'] = 'no-store'
    old = db.query(GuestTripImport).filter_by(user_id=user.id, guest_id=body.guest_id, guest_version=body.guest_version).first()
    if old and old.payload_hash != _digest(body):
        raise HTTPException(409, '该本地版本的内容与之前导入的不一致')
    return _guest_result(body, user, _serialize(_trip(db, user, old.trip_id)) if old else None)


@router.post('/import-guest')
def import_guest(body: GuestImportIn, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    response.headers['Cache-Control'] = 'no-store'
    digest = _digest(body)
    def previous():
        return db.query(GuestTripImport).filter_by(user_id=user.id, guest_id=body.guest_id,
                                                  guest_version=body.guest_version).first()
    already = previous()
    if already:
        if already.payload_hash != digest:
            raise HTTPException(409, '该本地版本的内容与之前导入的不一致')
        return _guest_result(body, user, _serialize(_trip(db, user, already.trip_id)))
    data = body.trip
    trip = Trip(user_id=user.id, title=data.title.strip(), destination=data.destination.strip(),
                start_date=data.start_date.isoformat() if data.start_date else None, status=data.status,
                walking_preferences=data.walking_preferences.model_dump(exclude_defaults=True) if data.walking_preferences else {})
    for old_day in data.days:
        day = TripDay(day_number=old_day.day_number, theme=old_day.theme)
        for position, item in enumerate(old_day.items):
            day.items.append(TripItem(sort_order=position, **item.model_dump()))
        trip.days.append(day)
    trip.places = [TripPlace(**place.model_dump(exclude={'version'})) for place in data.places]
    db.add(trip)
    try:
        db.flush()
        for old_day, day in zip(data.days, trip.days):
            choices = {}
            for positions, mode in old_day.route_preferences.items():
                start, end = map(int, positions.split(':'))
                origin, destination = day.items[start], day.items[end]
                choices[route_choices.leg_key(origin, destination)] = route_choices.selection(origin, destination, mode)
            if choices:
                day.route_preferences = choices
        for reservation in data.reservations:
            loc = reservation.location
            linked = trip.days[loc.day_number - 1].items[loc.item_index] if loc else None
            db.add(TripReservation(trip_id=trip.id, kind=reservation.kind, title=reservation.title.strip(),
                    status=reservation.status, start_date=reservation.start_date.isoformat() if reservation.start_date else None,
                    end_date=reservation.end_date.isoformat() if reservation.end_date else None,
                    item_id=linked.id if linked else None, item_name=linked.name if linked else reservation.item_name,
                    reference=reservation.reference.strip(), note=reservation.note.strip()))
        db.add(GuestTripImport(user_id=user.id, guest_id=body.guest_id, guest_version=body.guest_version, trip_id=trip.id, payload_hash=digest))
        db.commit()
    except IntegrityError:
        db.rollback()
        already = previous()
        if not already:
            raise
        if already.payload_hash != digest:
            raise HTTPException(409, '该本地版本的内容与之前导入的不一致')
        return _guest_result(body, user, _serialize(_trip(db, user, already.trip_id)))
    return _guest_result(body, user, _serialize(_trip(db, user, trip.id)), False)
