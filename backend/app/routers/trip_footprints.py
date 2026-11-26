"""Personal, explicitly confirmed footprints for accessible trips."""
from datetime import date
from io import BytesIO
import warnings

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.db.database import get_db
from app.db.footprint_models import FootprintPhoto, TripFootprint
from app.db.models import User
from app.db.trip_models import Trip
from app.routers.auth import get_current_user
from app.routers.trips import Versioned, _accessible, _claim, _item, _serialize, _trip

router = APIRouter(tags=['trip footprints'])


class FootprintIn(Versioned):
    item_id: int = Field(gt=0)
    city: str = Field(default='', max_length=100)
    visited_on: date | None = None
    note: str = Field(default='', max_length=2000)


class FootprintEdit(Versioned):
    city: str = Field(default='', max_length=100)
    visited_on: date | None = None
    note: str = Field(default='', max_length=2000)


def _payload(record, trip_title=None):
    result = {key: getattr(record, key) for key in ('id', 'trip_id', 'item_id', 'day_number', 'place_name',
              'address', 'lng', 'lat', 'amap_id', 'city', 'visited_on', 'note')}
    result['created_at'] = record.created_at.isoformat()
    result['location_status'] = 'associated' if record.amap_id and record.lng is not None and record.lat is not None else 'unconfirmed'
    result['photo_ids'] = [photo.id for photo in record.photos]
    if trip_title is not None:
        result['trip_title'] = trip_title
    return result


def _record(db, user_id, trip_id, footprint_id):
    result = db.query(TripFootprint).filter_by(id=footprint_id, trip_id=trip_id, user_id=user_id).first()
    if result is None:
        raise HTTPException(404, '足迹不存在')
    return result


@router.get('/api/footprints')
def all_footprints(response: Response, city: str = '', trip_id: int | None = None, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    if len(city) > 100:
        raise HTTPException(422, '城市名称过长')
    # Membership revocation immediately hides even previously created snapshots.
    query = (db.query(TripFootprint, Trip.title).join(Trip, Trip.id == TripFootprint.trip_id)
             .filter(TripFootprint.user_id == user.id, _accessible(user.id)))
    if city:
        query = query.filter(TripFootprint.city == city)
    if trip_id is not None:
        query = query.filter(TripFootprint.trip_id == trip_id)
    rows = query.order_by(TripFootprint.visited_on.desc(), TripFootprint.id.desc()).all()
    response.headers['Cache-Control'] = 'private, no-store'
    return {'items': [_payload(record, title) for record, title in rows]}


@router.get('/api/trips/{trip_id}/footprints')
def trip_footprints(trip_id: int, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _trip(db, user, trip_id)
    rows = (db.query(TripFootprint).filter_by(trip_id=trip_id, user_id=user.id)
            .order_by(TripFootprint.id.desc()).all())
    response.headers['Cache-Control'] = 'private, no-store'
    return {'items': [_payload(record) for record in rows]}


@router.post('/api/trips/{trip_id}/footprints', status_code=201)
def add_footprint(trip_id: int, body: FootprintIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    day, item = _item(trip, body.item_id)
    if db.query(TripFootprint).filter_by(user_id=user.id, trip_id=trip_id, item_id=item.id).first():
        raise HTTPException(409, '这个安排已记录过足迹，可编辑已有记录')
    _claim(db, trip, body.version)
    record = TripFootprint(user_id=user.id, trip_id=trip.id, item_id=item.id, day_number=day.day_number,
                           place_name=item.name, address=item.address, lng=item.lng, lat=item.lat,
                           amap_id=item.amap_id, city=body.city.strip(),
                           visited_on=body.visited_on.isoformat() if body.visited_on else None, note=body.note.strip())
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '这个安排已记录过足迹，请刷新后重试') from None
    db.expire_all()
    return _serialize(_trip(db, user, trip_id))


@router.patch('/api/trips/{trip_id}/footprints/{footprint_id}')
def edit_footprint(trip_id: int, footprint_id: int, body: FootprintEdit,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    record = _record(db, user.id, trip_id, footprint_id)
    _claim(db, trip, body.version)
    record.city = body.city.strip()
    record.visited_on = body.visited_on.isoformat() if body.visited_on else None
    record.note = body.note.strip()
    db.commit()
    db.expire_all()
    return _serialize(_trip(db, user, trip_id))


@router.post('/api/trips/{trip_id}/footprints/{footprint_id}/delete')
def delete_footprint(trip_id: int, footprint_id: int, body: Versioned,
                     db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    record = _record(db, user.id, trip_id, footprint_id)
    _claim(db, trip, body.version)
    db.delete(record)
    db.commit()
    db.expire_all()
    return _serialize(_trip(db, user, trip_id))


def _normalize_photo(data):
    """Decode a bounded JPEG/PNG and re-encode without EXIF or other metadata."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in ('JPEG', 'PNG') or image.width * image.height > 24_000_000:
                    raise ValueError()
                image.load()
                image = ImageOps.exif_transpose(image)
                image.thumbnail((1920, 1920))
                if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
                    rgba = image.convert('RGBA')
                    background = Image.new('RGB', rgba.size, 'white')
                    background.paste(rgba, mask=rgba.getchannel('A'))
                    image = background
                else:
                    image = image.convert('RGB')
                output = BytesIO()
                image.save(output, format='JPEG', quality=85, optimize=True)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise HTTPException(422, '照片无法识别或尺寸过大，请选择 JPEG 或 PNG 图片') from None


@router.post('/api/trips/{trip_id}/footprints/{footprint_id}/photos', status_code=201)
async def add_photo(trip_id: int, footprint_id: int, request: Request,
                    x_trip_version: int = Header(ge=1), db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    _record(db, user.id, trip_id, footprint_id)
    if trip.version != x_trip_version:
        raise HTTPException(409, '行程已在其他页面更新，请刷新后重试')
    if request.headers.get('content-type', '').split(';')[0].strip().lower() not in ('image/jpeg', 'image/png'):
        raise HTTPException(415, '请选择 JPEG 或 PNG 图片')
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 4 * 1024 * 1024:
            raise HTTPException(413, '照片不能超过 4 MB')
        data.extend(chunk)
    db.close()
    encoded = await run_in_threadpool(_normalize_photo, bytes(data))
    # Recheck permissions/version after decoding and claim before checking the photo count.
    trip = _trip(db, user, trip_id)
    record = _record(db, user.id, trip_id, footprint_id)
    _claim(db, trip, x_trip_version)
    if db.query(FootprintPhoto).filter_by(footprint_id=record.id).count() >= 3:
        raise HTTPException(409, '每条足迹最多保存 3 张照片')
    db.add(FootprintPhoto(footprint_id=record.id, data=encoded))
    db.commit(); db.expire_all()
    return _serialize(_trip(db, user, trip_id))


@router.get('/api/trips/{trip_id}/footprints/{footprint_id}/photos/{photo_id}')
def get_photo(trip_id: int, footprint_id: int, photo_id: int, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    _trip(db, user, trip_id)
    _record(db, user.id, trip_id, footprint_id)
    photo = db.query(FootprintPhoto).filter_by(id=photo_id, footprint_id=footprint_id).first()
    if photo is None:
        raise HTTPException(404, '照片不存在')
    return Response(content=photo.data, media_type='image/jpeg', headers={
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'"})


@router.post('/api/trips/{trip_id}/footprints/{footprint_id}/photos/{photo_id}/delete')
def delete_photo(trip_id: int, footprint_id: int, photo_id: int, body: Versioned,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    _record(db, user.id, trip_id, footprint_id)
    photo = db.query(FootprintPhoto).filter_by(id=photo_id, footprint_id=footprint_id).first()
    if photo is None:
        raise HTTPException(404, '照片不存在')
    _claim(db, trip, body.version)
    db.delete(photo); db.commit(); db.expire_all()
    return _serialize(_trip(db, user, trip_id))
