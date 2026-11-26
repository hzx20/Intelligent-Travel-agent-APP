"""Owner-managed view links and invitation-based editor access.

Tokens use URL fragments in the UI and are supplied in POST bodies, not URLs,
so ordinary server access logs and Referer headers do not contain the secret.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal
import jwt

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db.database import get_db
from app.db.models import User
from app.db.sharing_models import TripMember, TripShareLink
from app.db.trip_models import Trip, TripDay
from app.routers.auth import get_current_user
from app.security import _jwt_secret

router = APIRouter(tags=["trip sharing"])


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat()


def _hash(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


class LinkIn(BaseModel):
    mode: Literal['view', 'edit']
    expires_days: int = Field(default=30, ge=1, le=30)


class TokenIn(BaseModel):
    token: str = Field(min_length=43, max_length=43, pattern=r'^[A-Za-z0-9_-]{43}$')


class ActivationIn(BaseModel):
    preparation: str = Field(min_length=1, max_length=2048)


class PreparedLink(TokenIn, LinkIn):
    uid: int = Field(gt=0)
    trip_id: int = Field(gt=0)
    exp: int


def _preparation_key():
    # Domain separation prevents these proofs from becoming login credentials.
    return hmac.new(_jwt_secret().encode('utf-8'), b'travel-share-preparation-v1', hashlib.sha256).digest()


def _prepared_link(value):
    try:
        payload = jwt.decode(value, _preparation_key(), algorithms=['HS256'], audience='trip-share-preparation-v1',
                             options={'verify_exp': False, 'require': ['exp', 'uid', 'trip_id', 'mode', 'expires_days', 'token']})
        return PreparedLink.model_validate(payload)
    except (jwt.PyJWTError, ValueError, TypeError):
        raise HTTPException(422, '本次分享准备信息无效，请先核对管理列表后重新生成。')


def _owner_trip(db, user, trip_id):
    trip = db.query(Trip).filter(Trip.id == trip_id, Trip.user_id == user.id).first()
    if not trip:
        raise HTTPException(404, '旅行不存在或无权管理分享')
    return trip


def _valid_link(db, token, mode):
    query = db.query(TripShareLink).filter(TripShareLink.token_hash == _hash(token),
                                           TripShareLink.mode == mode,
                                           TripShareLink.revoked_at.is_(None),
                                           TripShareLink.expires_at > _now())
    link = query.first()
    if not link:
        raise HTTPException(404, '链接无效、已撤销或已过期')
    return link


@router.get('/api/trips/{trip_id}/sharing')
def sharing_info(trip_id: int, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _owner_trip(db, user, trip_id)
    response.headers['Cache-Control'] = 'no-store'
    links = db.query(TripShareLink).filter_by(trip_id=trip_id).order_by(TripShareLink.id.desc()).all()
    members = (db.query(TripMember, User).join(User, User.id == TripMember.user_id)
               .filter(TripMember.trip_id == trip_id).order_by(TripMember.id).all())
    return {
        'links': [{'id': link.id, 'mode': link.mode, 'created_at': _iso(link.created_at),
                   'expires_at': _iso(link.expires_at),
                   'status': 'revoked' if link.revoked_at else 'expired' if link.expires_at <= _now() else 'active'}
                  for link in links],
        'members': [{'user_id': member.user_id, 'nickname': member_user.nickname,
                     'role': member.role, 'joined_at': _iso(member.joined_at)} for member, member_user in members],
    }


@router.post('/api/trips/{trip_id}/sharing/links', status_code=201)
def create_link(trip_id: int, body: LinkIn, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _owner_trip(db, user, trip_id)
    token = secrets.token_urlsafe(32)
    now = _now()
    link = TripShareLink(trip_id=trip_id, token_hash=_hash(token), mode=body.mode,
                         created_at=now, expires_at=now + timedelta(days=body.expires_days))
    db.add(link)
    db.commit()
    response.headers['Cache-Control'] = 'no-store'
    return {'id': link.id, 'token': token, 'mode': link.mode, 'expires_at': _iso(link.expires_at)}


@router.post('/api/trips/{trip_id}/sharing/links/prepare')
def prepare_link(trip_id: int, body: LinkIn, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _owner_trip(db, user, trip_id)
    deadline = _now() + timedelta(minutes=15)
    payload = {'aud': 'trip-share-preparation-v1', 'uid': user.id, 'trip_id': trip_id,
               'mode': body.mode, 'expires_days': body.expires_days, 'token': secrets.token_urlsafe(32),
               'exp': int(deadline.replace(tzinfo=timezone.utc).timestamp())}
    proof = jwt.encode(payload, _preparation_key(), algorithm='HS256')
    response.headers['Cache-Control'] = 'no-store'
    return {'preparation': proof, 'token': payload['token'], 'mode': body.mode, 'prepare_expires_at': _iso(deadline)}


@router.post('/api/trips/{trip_id}/sharing/links/activate', status_code=201)
def activate_link(trip_id: int, body: ActivationIn, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _owner_trip(db, user, trip_id)
    prepared = _prepared_link(body.preparation)
    if prepared.uid != user.id or prepared.trip_id != trip_id:
        raise HTTPException(404, '这次分享准备信息不属于当前账号或旅行。')
    digest = _hash(prepared.token)
    def previous():
        return db.query(TripShareLink).filter_by(token_hash=digest).first()
    def result(link, replay=False):
        if link.trip_id != trip_id or link.mode != prepared.mode or link.revoked_at or link.expires_at <= _now():
            raise HTTPException(409, '这条链接已撤销、过期或不匹配，不能通过重试重新启用。')
        if replay: response.status_code = 200
        response.headers['Cache-Control'] = 'no-store'
        return {'id': link.id, 'token': prepared.token, 'mode': link.mode, 'expires_at': _iso(link.expires_at)}
    saved = previous()
    if saved:
        return result(saved, replay=True)
    now = _now()
    if prepared.exp <= int(now.replace(tzinfo=timezone.utc).timestamp()):
        raise HTTPException(410, '本次准备信息已过期且未生成链接，请核对列表后重新准备。')
    link = TripShareLink(trip_id=trip_id, token_hash=digest, mode=prepared.mode,
                         created_at=now, expires_at=now + timedelta(days=prepared.expires_days))
    db.add(link)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        saved = previous()
        if not saved: raise
        return result(saved, replay=True)
    return result(link)


@router.delete('/api/trips/{trip_id}/sharing/links/{link_id}', status_code=204)
def revoke_link(trip_id: int, link_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _owner_trip(db, user, trip_id)
    link = db.query(TripShareLink).filter_by(id=link_id, trip_id=trip_id).with_for_update().first()
    if not link:
        raise HTTPException(404, '分享链接不存在')
    if not link.revoked_at:
        link.revoked_at = _now()
        db.commit()
    return Response(status_code=204, headers={'Cache-Control': 'no-store'})


@router.delete('/api/trips/{trip_id}/sharing/members/{member_id}', status_code=204)
def remove_member(trip_id: int, member_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _owner_trip(db, user, trip_id)
    member = db.query(TripMember).filter_by(trip_id=trip_id, user_id=member_id).first()
    if member is None:
        raise HTTPException(404, '成员不存在')
    # Serialize revocation with content mutations: any editor request based on
    # the previous version must fail its compare-and-swap after this commits.
    db.execute(update(Trip).where(Trip.id == trip_id).values(version=Trip.version + 1))
    db.delete(member)
    db.commit()
    return Response(status_code=204, headers={'Cache-Control': 'no-store'})


@router.post('/api/trip-sharing/view')
def view_shared(body: TokenIn, response: Response, db: Session = Depends(get_db)):
    link = _valid_link(db, body.token, 'view')
    trip = (db.query(Trip).options(selectinload(Trip.days).selectinload(TripDay.items))
            .filter(Trip.id == link.trip_id).first())
    if not trip:
        raise HTTPException(404, '链接无效、已撤销或已过期')
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return {'title': trip.title, 'destination': trip.destination, 'start_date': trip.start_date,
            'days': [{'day_number': day.day_number, 'theme': day.theme,
                      'items': [{name: getattr(item, name) for name in ('name', 'kind', 'start_time', 'duration_min', 'address', 'activity', 'note')}
                                for item in sorted(day.items, key=lambda item: (item.sort_order, item.id))]}
                     for day in trip.days]}


@router.post('/api/trip-sharing/accept')
def accept_invitation(body: TokenIn, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # SQLite ignores SELECT FOR UPDATE. The conditional UPDATE takes a write
    # lock and checks validity in the same statement, serializing acceptance
    # against link revocation even on SQLite. On other databases it locks the
    # matched link row until the membership commit.
    token_hash = _hash(body.token)
    claim = db.execute(update(TripShareLink).where(
        TripShareLink.token_hash == token_hash,
        TripShareLink.mode == 'edit',
        TripShareLink.revoked_at.is_(None),
        TripShareLink.expires_at > _now(),
    ).values(revoked_at=TripShareLink.revoked_at).execution_options(synchronize_session=False))
    if claim.rowcount != 1:
        raise HTTPException(404, '链接无效、已撤销或已过期')
    link = db.query(TripShareLink).filter_by(token_hash=token_hash).one()
    trip = db.get(Trip, link.trip_id)
    if trip is None:
        raise HTTPException(404, '链接无效、已撤销或已过期')
    if trip.user_id != user.id:
        member = db.query(TripMember).filter_by(trip_id=trip.id, user_id=user.id).first()
        if member is None:
            db.add(TripMember(trip_id=trip.id, user_id=user.id, role='editor', joined_at=_now()))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                # A concurrent acceptance may have inserted the same membership.
                if not db.query(TripMember).filter_by(trip_id=trip.id, user_id=user.id).first():
                    raise
    response.headers['Cache-Control'] = 'no-store'
    return {'trip_id': trip.id, 'role': 'owner' if trip.user_id == user.id else 'editor'}


@router.post('/api/trip-sharing/invitation-status')
def invitation_status(body: TokenIn, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Read current membership without accepting, renewing or granting access.

    Existing members can recover a lost acceptance receipt even after link
    revocation/expiry. Removed members get no trip identifier from invalid links.
    """
    private_headers = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}
    link = db.query(TripShareLink).filter_by(token_hash=_hash(body.token), mode='edit').first()
    trip = db.get(Trip, link.trip_id) if link else None
    if trip is None:
        raise HTTPException(404, '链接无效、已撤销或已过期', headers=private_headers)
    member = db.query(TripMember).filter_by(trip_id=trip.id, user_id=user.id).first()
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    if trip.user_id == user.id or member is not None:
        return {'state': 'joined', 'trip_id': trip.id,
                'role': 'owner' if trip.user_id == user.id else 'editor'}
    if link.revoked_at is not None or link.expires_at <= _now():
        raise HTTPException(404, '链接无效、已撤销或已过期', headers=private_headers)
    return {'state': 'not_joined'}
