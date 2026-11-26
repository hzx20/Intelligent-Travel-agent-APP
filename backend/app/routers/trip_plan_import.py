"""Explicit, recoverable conversion of one stable planning snapshot."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import AiPlan, User
from app.db.trip_models import PlanTripConversion, Trip
from app.routers.auth import get_current_user
from app.routers.trips import _serialize, _trip, _trip_from_plan

router = APIRouter(prefix='/api/trips', tags=['plan conversion'])


class ConversionIn(BaseModel):
    version: int = Field(ge=1)
    revision: UUID


def _receipt(db, uid, revision):
    return db.query(PlanTripConversion).filter_by(user_id=uid, plan_revision=str(revision)).first()


def _source(db, uid, pid, revision):
    plan = db.get(AiPlan, pid)
    if not plan or plan.user_id != uid or plan.status != 'completed':
        raise HTTPException(404, '规划记录不存在')
    if plan.revision != str(revision):
        raise HTTPException(409, '原方案标识已变化，请重新读取')
    return plan


def _result(db, user, pid, revision, receipt):
    if receipt.plan_id != pid:
        raise HTTPException(409, '原方案标识不匹配')
    trip = db.get(Trip, receipt.trip_id) if receipt.trip_id else None
    if not trip or trip.user_id != user.id:
        raise HTTPException(410, '转换后的旅行已不存在，本次不会重新创建')
    return {'owner_id': user.id, 'plan_id': pid, 'plan_revision': str(revision),
            'state': 'converted', 'trip': _serialize(_trip(db, user, trip.id))}


def _legacy_trip(db, user, plan):
    existing = db.query(Trip).filter_by(source_plan_id=plan.id).first()
    if existing:
        known = db.query(PlanTripConversion).filter_by(user_id=user.id, plan_id=plan.id, trip_id=existing.id).first()
        if existing.user_id != user.id or known and known.plan_revision != plan.revision:
            raise HTTPException(409, '该方案关联已变化，请重新核对')
    return existing


@router.get('/plan-import-capabilities')
def capabilities(response: Response, user: User = Depends(get_current_user)):
    response.headers['Cache-Control'] = 'no-store'
    return {'recoverable_conversion': True}


@router.get('/from-plan/{plan_id}/conversion')
def state(plan_id: int, revision: UUID, response: Response, db: Session = Depends(get_db),
          user: User = Depends(get_current_user)):
    response.headers['Cache-Control'] = 'no-store'
    receipt = _receipt(db, user.id, revision)
    if receipt:
        return _result(db, user, plan_id, revision, receipt)
    plan = _source(db, user.id, plan_id, revision)
    existing = _legacy_trip(db, user, plan)
    return {'owner_id': user.id, 'plan_id': plan_id, 'plan_revision': str(revision),
            'state': 'converted' if existing else 'not_created',
            'trip': _serialize(_trip(db, user, existing.id)) if existing else None}


@router.post('/from-plan/{plan_id}/convert', status_code=201)
def convert(plan_id: int, body: ConversionIn, response: Response, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    response.headers['Cache-Control'] = 'no-store'
    receipt = _receipt(db, user.id, body.revision)
    if receipt:
        response.status_code = 200
        return _result(db, user, plan_id, body.revision, receipt)
    plan = _source(db, user.id, plan_id, body.revision)
    # Lock the exact live source until copy and receipt commit; leave its version/content intact.
    locked = db.execute(update(AiPlan).where(AiPlan.id == plan_id, AiPlan.user_id == user.id,
        AiPlan.revision == str(body.revision), AiPlan.version == body.version, AiPlan.status == 'completed')
        .values(version=AiPlan.version).execution_options(synchronize_session=False))
    if locked.rowcount != 1:
        db.rollback()
        raise HTTPException(409, '原方案已更新，请先核对；本次未转换')
    # Another conversion may have committed while this request waited for the source lock.
    receipt = _receipt(db, user.id, body.revision)
    if receipt:
        response.status_code = 200
        return _result(db, user, plan_id, body.revision, receipt)
    existing = _legacy_trip(db, user, plan)
    trip = existing or _trip_from_plan(plan)
    try:
        if not existing:
            db.add(trip)
            db.flush()
        receipt = PlanTripConversion(user_id=user.id, plan_id=plan_id, plan_revision=str(body.revision),
                                     source_version=body.version, trip_id=trip.id)
        db.add(receipt)
        db.commit()
    except IntegrityError:
        db.rollback()
        receipt = _receipt(db, user.id, body.revision)
        if not receipt:
            raise HTTPException(409, '转换结果待核对，请继续原次确认')
        response.status_code = 200
    if existing:
        response.status_code = 200
    return _result(db, user, plan_id, body.revision, receipt)
