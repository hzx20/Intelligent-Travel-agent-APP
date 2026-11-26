"""Reviewable text-to-places import for an existing trip."""
import base64
import binascii
import json
import math
import threading
import time
from datetime import date
from unicodedata import category
from cachetools import TTLCache
from pydantic import BaseModel, Field, field_validator, model_validator
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Guide, Spot, User
from app.db.trip_models import TripPlace
from app.routers.auth import get_current_user
from app.routers.trips import ItemData, _claim, _serialize, _trip
from app.services import trip_ai, trip_guide_import, guide_ocr, wikivoyage
from app.services.supplier_http import SupplierError

router = APIRouter(prefix="/api/trips", tags=["trip-guide-import"])


def _visible_site_guides(db, user):
    query = db.query(Guide)
    return query if user.is_admin else query.filter(or_(Guide.is_draft.is_(False), Guide.user_id == user.id))


@router.get('/guest-guide/site-guides')
def guest_site_guides(response: Response, keyword: str = Query('', max_length=100),
                      page: int = Query(1, ge=1), db: Session = Depends(get_db)):
    query = db.query(Guide).filter(Guide.is_draft.is_(False))
    if keyword.strip():
        query = query.filter(or_(Guide.title.contains(keyword.strip(), autoescape=True),
                                 Guide.city.contains(keyword.strip(), autoescape=True)))
    total = query.count()
    rows = query.order_by(Guide.created_at.desc(), Guide.id.desc()).offset((page - 1) * 10).limit(10).all()
    response.headers['Cache-Control'] = 'no-store'
    return {'items':[{'id':row.id, 'title':row.title, 'city':row.city, 'is_draft':False} for row in rows],
            'page':page, 'total':total}


@router.get('/guest-guide/site-guides/{guide_id}')
def guest_site_guide(guide_id: int, response: Response, db: Session = Depends(get_db)):
    guide = db.query(Guide).filter(Guide.id == guide_id, Guide.is_draft.is_(False)).first()
    if guide is None:
        raise HTTPException(404, '公开攻略不存在或已不可查看')
    response.headers['Cache-Control'] = 'no-store'
    return {'id':guide.id, 'title':guide.title, 'city':guide.city, 'text':guide.content, 'is_draft':False}


@router.get('/{trip_id}/guide-import/site-guides')
def site_guides(trip_id: int, response: Response, keyword: str = Query('', max_length=100),
                page: int = Query(1, ge=1), db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    _trip(db, user, trip_id)
    query = _visible_site_guides(db, user)
    if keyword.strip():
        query = query.filter(or_(Guide.title.contains(keyword.strip(), autoescape=True),
                                 Guide.city.contains(keyword.strip(), autoescape=True)))
    total = query.count()
    rows = query.order_by(Guide.created_at.desc(), Guide.id.desc()).offset((page - 1) * 10).limit(10).all()
    response.headers['Cache-Control'] = 'no-store'
    return {'items': [{'id': row.id, 'title': row.title, 'city': row.city, 'is_draft': row.is_draft}
                      for row in rows], 'page': page, 'total': total}


@router.get('/{trip_id}/guide-import/site-guides/{guide_id}')
def site_guide(trip_id: int, guide_id: int, response: Response, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    _trip(db, user, trip_id)
    guide = _visible_site_guides(db, user).filter(Guide.id == guide_id).first()
    if guide is None:
        raise HTTPException(404, '攻略不存在')
    response.headers['Cache-Control'] = 'no-store'
    return {'id': guide.id, 'title': guide.title, 'city': guide.city, 'text': guide.content,
            'is_draft': guide.is_draft}


async def _read_ocr_request(request: Request):
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > 5_600_000:
            raise HTTPException(413, '图片过大，请选择不超过 4 MB 的图片')
        chunks.append(chunk)
    try:
        payload = json.loads(b''.join(chunks))
        if not isinstance(payload, dict) or set(payload) != {'version', 'image'} or type(payload['version']) is not int or payload['version'] < 1 or not isinstance(payload['image'], str):
            raise ValueError()
        version = payload['version']
        image = base64.b64decode(payload['image'], validate=True)
    except (ValueError, TypeError, binascii.Error):
        raise HTTPException(422, '图片数据无效') from None
    if len(image) > 4 * 1024 * 1024:
        raise HTTPException(413, '图片过大，请选择不超过 4 MB 的图片')
    mime = 'image/png' if image.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg' if image.startswith(b'\xff\xd8\xff') else None
    if not mime:
        raise HTTPException(422, '请选择 PNG 或 JPEG 图片')
    try:
        guide_ocr.validate_image(image, mime)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return version, image, mime


_guest_ocr_peers = TTLCache(maxsize=2048, ttl=60)
_guest_ocr_lock = threading.Lock()
_guest_ocr_capacity = threading.BoundedSemaphore(1)


@router.post('/guest-guide-image-text')
async def guest_image_text(request: Request, response: Response):
    peer = request.client.host if request.client else 'unknown'
    with _guest_ocr_lock:
        if not _guest_ocr_capacity.acquire(blocking=False):
            raise HTTPException(429, '图片识别繁忙，请稍后重试')
    try:
        version, image, mime = await _read_ocr_request(request)
        now = time.monotonic()
        with _guest_ocr_lock:
            recent = [stamp for stamp in _guest_ocr_peers.get(peer, ()) if now - stamp < 60]
            if len(recent) >= 2:
                raise HTTPException(429, '图片识别请求较频繁，请稍后重试')
            recent.append(now); _guest_ocr_peers[peer] = recent
        try:
            text = await guide_ocr.recognize(image, mime)
        except guide_ocr.OcrUnavailable as exc:
            raise HTTPException(502, str(exc)) from None
        except Exception:
            raise HTTPException(502, '图片识别暂不可用，请重试或手动粘贴正文') from None
    finally:
        _guest_ocr_capacity.release()
    response.headers['Cache-Control'] = 'no-store'
    return {'version':version, 'text':text}


@router.post("/{trip_id}/guide-import/image-text")
async def image_text(trip_id: int, request: Request, response: Response, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    version, image, mime = await _read_ocr_request(request)
    if trip.version != version:
        raise HTTPException(409, '行程已更新，请刷新后重试')
    db.close()
    try:
        text = await guide_ocr.recognize(image, mime)
    except guide_ocr.OcrUnavailable as exc:
        raise HTTPException(502, str(exc)) from None
    except Exception:
        raise HTTPException(502, '图片识别暂不可用，请重试或手动粘贴正文') from None
    trip = _trip(db, user, trip_id)
    if trip.version != version:
        raise HTTPException(409, '识别期间行程已更新，请刷新后重试')
    response.headers['Cache-Control'] = 'no-store'
    return {'version': version, 'text': text}


class PreviewIn(BaseModel):
    version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=12000)

    @field_validator("text")
    @classmethod
    def nonblank_text(cls, value):
        if not value.strip():
            raise ValueError("请填写攻略正文")
        return value


class GuestPreviewIn(PreviewIn):
    destination: str = Field(default='', max_length=100)


class GuestAiItem(ItemData):
    id: int = Field(gt=0, le=9007199254740991)


class GuestAiDay(BaseModel):
    id: int = Field(gt=0, le=9007199254740991)
    day_number: int = Field(ge=1, le=30)
    theme: str = Field(default='', max_length=100)
    items: list[GuestAiItem] = Field(max_length=200)


class GuestAiTrip(BaseModel):
    version: int = Field(ge=1, le=9007199254740991)
    title: str = Field(min_length=1, max_length=100)
    destination: str = Field(default='', max_length=100)
    start_date: date | None = None
    days: list[GuestAiDay] = Field(min_length=1, max_length=30)

    @model_validator(mode='after')
    def valid_snapshot(self):
        if not self.title.strip() or [d.day_number for d in self.days] != list(range(1, len(self.days) + 1)):
            raise ValueError('旅行名称或日期顺序不正确')
        ids = [d.id for d in self.days] + [i.id for d in self.days for i in d.items]
        if len(ids) != len(set(ids)) or sum(len(d.items) for d in self.days) > 500:
            raise ValueError('行程项目重复或过多')
        return self


class GuestAiPreview(BaseModel):
    trip: GuestAiTrip
    request: str = Field(min_length=1, max_length=500)

    @field_validator('request')
    @classmethod
    def nonblank(cls, value):
        if not value.strip(): raise ValueError('请填写规划需求')
        return value.strip()


_guest_ai_peers = TTLCache(maxsize=2048, ttl=60)
_guest_ai_lock = threading.Lock()
_guest_ai_capacity = threading.BoundedSemaphore(2)


@router.post('/guest-ai-preview')
async def guest_ai_preview(body: GuestAiPreview, request: Request, response: Response):
    peer = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    with _guest_ai_lock:
        recent = [stamp for stamp in _guest_ai_peers.get(peer, ()) if now - stamp < 60]
        if len(recent) >= 2: raise HTTPException(429, 'AI 建议请求较频繁，请稍后重试')
        if not _guest_ai_capacity.acquire(blocking=False): raise HTTPException(429, 'AI 建议服务繁忙，请稍后重试')
        recent.append(now); _guest_ai_peers[peer] = recent
    try:
        try:
            operations, changes = await trip_ai.propose(body.trip.model_dump(mode='json'), body.request)
        except Exception:
            raise HTTPException(502, 'AI 建议生成失败，请稍后重试或缩小修改范围') from None
    finally:
        _guest_ai_capacity.release()
    response.headers['Cache-Control'] = 'no-store'
    return {'base_version':body.trip.version, 'operations':operations, 'changes':changes}


_guest_preview_peers = TTLCache(maxsize=2048, ttl=60)
_guest_preview_lock = threading.Lock()
_guest_preview_capacity = threading.BoundedSemaphore(2)


def _match_guest_places(db, candidates, destination):
    places = trip_guide_import.match_places(db, candidates, destination)
    ids = {choice['id'] for place in places for choice in place['matches']}
    spots = {s.id: s for s in db.query(Spot).filter(Spot.id.in_(ids)).all()} if ids else {}
    for place in places:
        for choice in place['matches']:
            spot = spots[choice['id']]
            valid = (spot.lng is not None and spot.lat is not None and math.isfinite(spot.lng)
                     and math.isfinite(spot.lat) and -180 <= spot.lng <= 180 and -90 <= spot.lat <= 90)
            choice.update(lng=spot.lng if valid else None, lat=spot.lat if valid else None, amap_id=spot.amap_id or '')
    return places


class BrowserGuideSnapshot(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1, lt=2**53, strict=True)
    names: list[str] = Field(max_length=50)

    @field_validator('names')
    @classmethod
    def clean_names(cls, names):
        cleaned = [name.strip() for name in names]
        if any(not 1 <= len(name) <= 160 or any(category(char).startswith('C') for char in name) for name in cleaned):
            raise ValueError('浏览器返回了无效的地点名称')
        return list(dict.fromkeys(cleaned))


class PublicGuideIn(BaseModel):
    url: str = Field(min_length=1, max_length=1024)
    version: int = Field(ge=1)
    browser_snapshot: BrowserGuideSnapshot | None = None


class GuestPublicGuideIn(PublicGuideIn):
    destination: str = Field(default='', max_length=160)


_public_guide_peers = TTLCache(maxsize=2048, ttl=60)
_public_guide_lock = threading.Lock()


def _public_guide(body, request):
    try:
        title = wikivoyage.title_from_url(body.url)
        peer = request.client.host if request.client else 'unknown'
        now = time.monotonic()
        with _public_guide_lock:
            recent = [stamp for stamp in _public_guide_peers.get(peer, ()) if now - stamp < 60]
            if len(recent) >= 4:
                raise HTTPException(429, '攻略链接查询较频繁，请稍后重试')
            _public_guide_peers[peer] = [*recent, now]
        snapshot = body.browser_snapshot
        if snapshot is None:
            guide = wikivoyage.fetch_guide(title)
        else:
            try:
                read_title = wikivoyage.article_title(snapshot.title)
                if read_title.casefold() != title.casefold():
                    raise ValueError
            except ValueError:
                raise HTTPException(422, '浏览器取得的攻略标题与链接不一致') from None
            guide = wikivoyage.PublicGuide(read_title, snapshot.revision, tuple(snapshot.names))
    except SupplierError as error:
        raise HTTPException(error.status_code, str(error)) from None
    return guide, [{'name': name, 'note': ''} for name in guide.names]


def _public_preview(guide, places, version, response):
    response.headers['Cache-Control'] = 'no-store'
    return {'version': version, 'places': places, 'guide': {
        'title': guide.title, 'revision': guide.revision, 'url': guide.source_url, 'license_url': wikivoyage.LICENSE_URL,
    }}


@router.post('/guest-guide/public-link-preview')
def guest_public_link_preview(body: GuestPublicGuideIn, request: Request, response: Response,
                              db: Session = Depends(get_db)):
    guide, candidates = _public_guide(body, request)
    places = _match_guest_places(db, candidates, body.destination)
    return _public_preview(guide, places, body.version, response)


@router.post('/{trip_id}/guide-import/public-link-preview')
def public_link_preview(trip_id: int, body: PublicGuideIn, request: Request, response: Response,
                        db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if _trip(db, user, trip_id).version != body.version:
        raise HTTPException(409, '行程已更新，请刷新后重试')
    db.close()
    guide, candidates = _public_guide(body, request)
    trip = _trip(db, user, trip_id)
    if trip.version != body.version:
        raise HTTPException(409, '预览期间行程已更新，请刷新后重试')
    places = trip_guide_import.match_places(db, candidates, trip.destination)
    return _public_preview(guide, places, trip.version, response)


@router.post('/guest-guide-preview')
async def guest_preview(body: GuestPreviewIn, request: Request, response: Response, db: Session = Depends(get_db)):
    peer = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    with _guest_preview_lock:
        recent = [stamp for stamp in _guest_preview_peers.get(peer, ()) if now - stamp < 60]
        if len(recent) >= 4:
            raise HTTPException(429, '攻略识别请求较频繁，请稍后重试')
        if not _guest_preview_capacity.acquire(blocking=False):
            raise HTTPException(429, '攻略识别繁忙，请稍后重试')
        recent.append(now); _guest_preview_peers[peer] = recent
    try:
        db.close()
        try:
            candidates = await trip_guide_import.extract_places(body.text)
        except Exception:
            raise HTTPException(502, '攻略解析失败，请稍后重试或缩短正文') from None
    finally:
        _guest_preview_capacity.release()
    places = _match_guest_places(db, candidates, body.destination)
    response.headers['Cache-Control'] = 'no-store'
    return {'version':body.version, 'places':places}


class SelectedPlace(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    note: str = Field(default="", max_length=2000)
    spot_id: int | None = Field(default=None, gt=0)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value):
        if not value.strip():
            raise ValueError("地点名称不能为空")
        return value.strip()


class ApplyIn(BaseModel):
    version: int = Field(ge=1)
    source: str = Field(default="用户粘贴攻略", max_length=150)
    places: list[SelectedPlace] = Field(min_length=1, max_length=50)


def _combine(old: str, addition: str, limit: int) -> str:
    old, addition = (old or "").strip(), (addition or "").strip()
    if not addition or addition == old or addition in old.split("\n\n"):
        return old
    combined = f"{old}\n\n{addition}" if old else addition
    if len(combined) > limit:
        raise HTTPException(422, "合并后的备注或来源过长，请缩短后再导入")
    return combined


@router.post("/{trip_id}/guide-import/preview")
async def preview(trip_id: int, body: PreviewIn, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    if trip.version != body.version:
        raise HTTPException(409, "行程已更新，请刷新后重试")
    db.close()  # Do not retain a DB connection while the model is running.
    try:
        candidates = await trip_guide_import.extract_places(body.text)
    except Exception:
        raise HTTPException(502, "攻略解析失败，请稍后重试或缩短正文") from None
    trip = _trip(db, user, trip_id)
    if trip.version != body.version:
        raise HTTPException(409, "解析期间行程已更新，请刷新后重试")
    return {"version": trip.version, "places": trip_guide_import.match_places(db, candidates, trip.destination)}


@router.post("/{trip_id}/guide-import/apply")
def apply(trip_id: int, body: ApplyIn, db: Session = Depends(get_db),
          user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    if trip.version != body.version:
        raise HTTPException(409, "行程已更新，请刷新后重新确认")
    requested_ids = {p.spot_id for p in body.places if p.spot_id is not None}
    spots = {spot.id: spot for spot in db.query(Spot).filter(Spot.id.in_(requested_ids)).all()} if requested_ids else {}
    if set(spots) != requested_ids:
        raise HTTPException(422, "选择的景点已不存在，请重新解析")
    source = body.source.strip() or "用户粘贴攻略"
    # Work out all merges before claiming the version; no partial writes on failure.
    by_poi = {place.amap_id: place for place in trip.places if place.amap_id}
    new_places = []
    modifications = {}
    added, merged, unchanged = 0, 0, 0
    for selected in body.places:
        spot = spots.get(selected.spot_id) if selected.spot_id else None
        if spot and spot.name != selected.name:
            raise HTTPException(422, "地点名称与所选景点不一致，请重新解析")
        poi_id = (spot.amap_id or "") if spot else ""
        existing = by_poi.get(poi_id) if poi_id else None
        if existing:
            previous = modifications.get(id(existing), (existing.note, existing.source))
            note = _combine(previous[0], selected.note, 4000)
            merged_source = _combine(previous[1], source, 200)
            if (note, merged_source) == previous:
                unchanged += 1
            else:
                modifications[id(existing)] = (note, merged_source)
                merged += 1
            continue
        valid_coords = bool(spot and spot.lng is not None and spot.lat is not None
                            and math.isfinite(spot.lng) and math.isfinite(spot.lat)
                            and -180 <= spot.lng <= 180 and -90 <= spot.lat <= 90)
        entry = TripPlace(trip_id=trip.id, name=spot.name if spot else selected.name,
                          address=spot.address if spot else "", lng=spot.lng if valid_coords else None,
                          lat=spot.lat if valid_coords else None, amap_id=poi_id, note=selected.note.strip(), source=source)
        new_places.append(entry)
        if poi_id:
            by_poi[poi_id] = entry
        added += 1
    if new_places or modifications:
        _claim(db, trip, body.version)
        for entry in [*trip.places, *new_places]:
            if id(entry) in modifications:
                entry.note, entry.source = modifications[id(entry)]
        db.add_all(new_places)
        db.commit()
        db.expire_all()
    return {"trip": _serialize(_trip(db, user, trip_id)), "added": added, "merged": merged, "unchanged": unchanged}
