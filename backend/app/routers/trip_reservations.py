"""Manually recorded reservations shared with trip editors, without supplier actions."""
from datetime import date
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import Field, field_validator, model_validator
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import User
from app.db.reservation_models import TripReservation
from app.routers.auth import get_current_user
from app.routers.trips import Versioned, _claim, _item, _serialize, _trip

router = APIRouter(prefix='/api/trips', tags=['trip reservations'])


class ReservationIn(Versioned):
    kind: Literal['住宿', '交通', '门票', '其他'] = '住宿'
    title: str = Field(min_length=1, max_length=160)
    status: Literal['待预订', '已预订', '已取消'] = '待预订'
    start_date: date | None = None
    end_date: date | None = None
    item_id: int | None = Field(default=None, gt=0)
    reference: str = Field(default='', max_length=150)
    note: str = Field(default='', max_length=2000)

    @field_validator('title')
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError('名称不能为空')
        return value.strip()

    @model_validator(mode='after')
    def dates_in_order(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError('结束日期不能早于开始日期')
        return self


def _data(trip, body):
    item_name = _item(trip, body.item_id)[1].name if body.item_id is not None else ''
    return {'kind': body.kind, 'title': body.title, 'status': body.status,
            'start_date': body.start_date.isoformat() if body.start_date else None,
            'end_date': body.end_date.isoformat() if body.end_date else None,
            'item_id': body.item_id, 'item_name': item_name,
            'reference': body.reference.strip(), 'note': body.note.strip()}


def _find(db, trip_id, reservation_id):
    row = db.query(TripReservation).filter_by(id=reservation_id, trip_id=trip_id).first()
    if not row: raise HTTPException(404, '预订记录不存在')
    return row


def _payload(row):
    return {key: getattr(row, key) for key in ('id', 'kind', 'title', 'status', 'start_date', 'end_date',
            'item_id', 'item_name', 'reference', 'note')}


@router.get('/{trip_id}/reservations')
def list_reservations(trip_id: int, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _trip(db, user, trip_id)
    rows = db.query(TripReservation).filter_by(trip_id=trip_id).order_by(TripReservation.id.desc()).all()
    response.headers['Cache-Control'] = 'no-store'
    return {'items': [_payload(row) for row in rows]}


@router.post('/{trip_id}/reservations', status_code=201)
def create_reservation(trip_id: int, body: ReservationIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    values = _data(trip, body)
    _claim(db, trip, body.version)
    db.add(TripReservation(trip_id=trip_id, **values)); db.commit(); db.expire_all()
    return _serialize(_trip(db, user, trip_id))


@router.patch('/{trip_id}/reservations/{reservation_id}')
def edit_reservation(trip_id: int, reservation_id: int, body: ReservationIn,
                     db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    row = _find(db, trip_id, reservation_id)
    values = _data(trip, body)
    if row.item_id is None and body.item_id is None:
        values['item_name'] = row.item_name
    _claim(db, trip, body.version)
    for key, value in values.items(): setattr(row, key, value)
    db.commit(); db.expire_all()
    return _serialize(_trip(db, user, trip_id))


@router.post('/{trip_id}/reservations/{reservation_id}/delete')
def delete_reservation(trip_id: int, reservation_id: int, body: Versioned,
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    row = _find(db, trip_id, reservation_id)
    _claim(db, trip, body.version)
    db.delete(row); db.commit(); db.expire_all()
    return _serialize(_trip(db, user, trip_id))
