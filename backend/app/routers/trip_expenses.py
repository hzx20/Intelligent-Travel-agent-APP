"""Trip expense ledger; all writes share the trip's optimistic version."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User
from app.db.trip_expense_models import TripExpense
from app.routers.auth import get_current_user
from app.routers.trips import Versioned, _claim, _day, _serialize, _trip

router = APIRouter(prefix="/api/trips", tags=["trip expenses"])


class ExpenseIn(Versioned):
    amount_minor: int = Field(gt=0, le=100000000000)
    currency: Literal["CNY", "USD", "EUR", "JPY"] = "CNY"
    category: Literal["餐饮", "交通", "住宿", "门票", "购物", "其他"] = "其他"
    day_id: int | None = None
    item_id: int | None = None
    spent_on: date | None = None
    note: str = Field(default="", max_length=500)


def _payload(expense):
    return {"id": expense.id, "day_id": expense.day_id, "item_id": expense.item_id,
            "item_name": expense.item_name, "category": expense.category, "amount_minor": expense.amount_minor,
            "currency": expense.currency, "spent_on": expense.spent_on, "note": expense.note,
            "created_at": expense.created_at.isoformat()}


def _expense(db, trip_id, expense_id):
    item = db.query(TripExpense).filter(TripExpense.trip_id == trip_id, TripExpense.id == expense_id).first()
    if not item:
        raise HTTPException(404, "支出记录不存在")
    return item


def _validated_data(trip, body):
    day = _day(trip, body.day_id) if body.day_id is not None else None
    name = ""
    if body.item_id is not None:
        if day is None:
            raise HTTPException(422, "关联安排时需要选择日期")
        item = next((i for i in day.items if i.id == body.item_id), None)
        if item is None:
            raise HTTPException(404, "所选行程安排不存在")
        name = item.name
    return {"amount_minor": body.amount_minor, "currency": body.currency, "category": body.category,
            "day_id": body.day_id, "item_id": body.item_id, "item_name": name,
            "spent_on": body.spent_on.isoformat() if body.spent_on else None, "note": body.note.strip()}


@router.get("/{trip_id}/expenses")
def list_expenses(trip_id: int, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    items = db.query(TripExpense).filter(TripExpense.trip_id == trip_id).order_by(TripExpense.id.desc()).all()
    item_days = {item.id: day.id for day in trip.days for item in day.items}
    totals = {}
    payloads = []
    for item in items:
        totals[item.currency] = totals.get(item.currency, 0) + item.amount_minor
        payload = _payload(item)
        # Correct older records whose linked item was moved before synchronization existed.
        if item.item_id in item_days:
            payload['day_id'] = item_days[item.item_id]
        payloads.append(payload)
    response.headers['Cache-Control'] = 'private, no-store'
    return {"items": payloads, "totals": totals}


@router.post("/{trip_id}/expenses", status_code=201)
def add_expense(trip_id: int, body: ExpenseIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    data = _validated_data(trip, body)
    _claim(db, trip, body.version)
    db.add(TripExpense(trip_id=trip_id, **data))
    db.commit()
    db.expire_all()
    return _serialize(_trip(db, user, trip_id))


@router.patch("/{trip_id}/expenses/{expense_id}")
def edit_expense(trip_id: int, expense_id: int, body: ExpenseIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    expense = _expense(db, trip_id, expense_id)
    data = _validated_data(trip, body)
    if expense.item_id is None and body.item_id is None and expense.day_id == body.day_id:
        data['item_name'] = expense.item_name
    _claim(db, trip, body.version)
    for key, value in data.items():
        setattr(expense, key, value)
    db.commit()
    db.expire_all()
    return _serialize(_trip(db, user, trip_id))


@router.post("/{trip_id}/expenses/{expense_id}/delete")
def delete_expense(trip_id: int, expense_id: int, body: Versioned, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    expense = _expense(db, trip_id, expense_id)
    _claim(db, trip, body.version)
    db.delete(expense)
    db.commit()
    db.expire_all()
    return _serialize(_trip(db, user, trip_id))
