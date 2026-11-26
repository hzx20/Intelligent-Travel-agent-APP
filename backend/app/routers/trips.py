"""Editable trip API. Every mutation uses an atomic trip version check."""
import asyncio
import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import exists, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db.database import get_db, SessionLocal
from app.db.models import AiPlan, User, now_local
from app.db.trip_models import Trip, TripDay, TripItem, TripPlace, TripProposal, TripAiJob, TripCreationRequest
from app.db.sharing_models import TripMember
from app.routers.auth import get_current_user
from app.services import route_preferences, trip_ai, trip_budget, trip_routes
from app.services.plan_schedule import place_name, trip_start_date
from app.services.walking_preferences import WalkingPreferences, from_planning

router = APIRouter(prefix="/api/trips", tags=["trips"])


class TripCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    destination: str = Field(default="", max_length=100)
    start_date: date | None = None
    days: int = Field(default=1, ge=1, le=30)
    request_id: uuid.UUID | None = None


class TripCreateOnce(TripCreate):
    request_id: uuid.UUID


class Versioned(BaseModel):
    version: int = Field(ge=1)


class TripPatch(Versioned):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    destination: str | None = Field(default=None, max_length=100)
    start_date: date | None = None
    status: Literal["planning", "upcoming", "completed"] | None = None
    walking_preferences: WalkingPreferences | None = None


class DayPatch(Versioned):
    theme: str = Field(max_length=100)


class BudgetReviewWrite(Versioned):
    checklist: trip_budget.BudgetChecklist


class RoutePreferenceWrite(Versioned):
    origin_id: int = Field(gt=0)
    destination_id: int = Field(gt=0)
    mode: route_preferences.RouteMode | None


class ItemData(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    kind: str = Field(default="地点", max_length=30)
    start_time: str = Field(default="", pattern=r"^$|^(?:[01]\d|2[0-3]):[0-5]\d$")
    duration_min: int | None = Field(default=None, ge=0, le=1440)
    address: str = Field(default="", max_length=300)
    lng: float | None = Field(default=None, ge=-180, le=180)
    lat: float | None = Field(default=None, ge=-90, le=90)
    amap_id: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=4000)
    activity: str = Field(default="", max_length=4000)
    source: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def coordinates_together(self):
        if (self.lng is None) != (self.lat is None):
            raise ValueError("经纬度必须同时填写")
        if not self.name.strip():
            raise ValueError("名称不能为空")
        return self


class ItemWrite(ItemData, Versioned):
    pass


class MoveIn(Versioned):
    day_id: int
    index: int = Field(ge=0)


class PlaceWrite(Versioned):
    name: str = Field(min_length=1, max_length=160)
    address: str = Field(default="", max_length=300)
    lng: float | None = Field(default=None, ge=-180, le=180)
    lat: float | None = Field(default=None, ge=-90, le=90)
    amap_id: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=4000)
    source: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def validate_place(self):
        if (self.lng is None) != (self.lat is None) or not self.name.strip():
            raise ValueError("名称不能为空，经纬度必须同时填写")
        return self


class SchedulePlace(Versioned):
    day_id: int


class ProposalIn(Versioned):
    request: str = Field(min_length=1, max_length=500)


# Lease-backed jobs survive restarts. A lease exceeds the model timeout, and
# the token fences a worker that returns after its lease has been replaced.
AI_SESSION_FACTORY = SessionLocal
AI_LEASE_SECONDS = 180
AI_MAX_ATTEMPTS = 2
AI_CONCURRENCY = 20
_ai_worker_tasks = set()
_ai_scheduler = None
_logger = logging.getLogger('uvicorn.error')


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _job_snapshot(job, db, trip):
    proposal = None
    if job.status == 'done' and job.proposal_id:
        saved = db.get(TripProposal, job.proposal_id)
        if saved and saved.status == 'pending' and saved.base_version == trip.version:
            try:
                _, changes = trip_ai.simulate(_serialize(trip), json.loads(saved.operations_json))
                proposal = {'id': saved.id, 'base_version': saved.base_version, 'changes': changes}
            except (ValueError, TypeError):
                pass
    status = 'running' if job.status in ('queued', 'running') else job.status
    return {'status': status, 'version': job.version, 'request': job.request,
            'proposal': proposal, 'error': job.error}


def _claim_ai_jobs(factory, capacity=AI_CONCURRENCY):
    now = _utcnow()
    claimed = []
    with factory() as db:
        # A second crash or lost lease ends the job instead of retrying forever.
        db.execute(update(TripAiJob).where(TripAiJob.status == 'running', TripAiJob.lease_until < now,
                                         TripAiJob.attempts >= AI_MAX_ATTEMPTS)
                   .values(status='error', error='任务多次中断，请重新生成建议', snapshot_json='',
                           lease_token='', lease_until=None, updated_at=now))
        db.commit()
        candidates = (db.query(TripAiJob.id)
                      .filter(or_(TripAiJob.status == 'queued',
                                  (TripAiJob.status == 'running') & (TripAiJob.lease_until < now)))
                      .order_by(TripAiJob.updated_at, TripAiJob.id).limit(capacity).all())
        for (job_id,) in candidates:
            token = uuid.uuid4().hex
            result = db.execute(update(TripAiJob).where(
                TripAiJob.id == job_id, TripAiJob.attempts < AI_MAX_ATTEMPTS,
                or_(TripAiJob.status == 'queued',
                    (TripAiJob.status == 'running') & (TripAiJob.lease_until < now)))
                .values(status='running', lease_token=token,
                        lease_until=now + timedelta(seconds=AI_LEASE_SECONDS),
                        attempts=TripAiJob.attempts + 1, updated_at=now))
            db.commit()
            if result.rowcount:
                claimed.append((job_id, token))
    return claimed


async def _run_ai_job(job_id, token, factory):
    try:
        with factory() as db:
            job = db.get(TripAiJob, job_id)
            if not job or job.status != 'running' or job.lease_token != token:
                return
            user = db.get(User, job.user_id)
            if not user or not user.is_active:
                raise HTTPException(404, '旅行不存在')
            trip = _trip(db, user, job.trip_id)
            if trip.version != job.version:
                raise HTTPException(409, '生成期间行程已更新，请重新生成建议')
            snapshot, request = json.loads(job.snapshot_json), job.request
        operations, _ = await asyncio.wait_for(trip_ai.propose(snapshot, request), timeout=150)
        with factory() as db:
            # Acquire the lease row for the duration of the result transaction.
            result = db.execute(update(TripAiJob).where(
                TripAiJob.id == job_id, TripAiJob.status == 'running', TripAiJob.lease_token == token,
                TripAiJob.lease_until > _utcnow()).values(status='finishing'))
            if not result.rowcount:
                db.rollback(); return
            job = db.get(TripAiJob, job_id)
            user = db.get(User, job.user_id)
            if not user or not user.is_active:
                raise HTTPException(404, '旅行不存在')
            trip = _trip(db, user, job.trip_id)
            if trip.version != job.version:
                raise HTTPException(409, '生成期间行程已更新，请重新生成建议')
            proposal = TripProposal(trip_id=trip.id, base_version=trip.version, request=job.request,
                                    operations_json=json.dumps(operations, ensure_ascii=False))
            db.add(proposal); db.flush()
            job.status = 'done'; job.proposal_id = proposal.id; job.snapshot_json = ''
            job.lease_token = ''; job.lease_until = None; job.updated_at = _utcnow()
            db.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        message = exc.detail if isinstance(exc, HTTPException) else 'AI 建议生成失败，请稍后重试或缩小修改范围'
        try:
            with factory() as db:
                db.execute(update(TripAiJob).where(TripAiJob.id == job_id, TripAiJob.status == 'running',
                                                   TripAiJob.lease_token == token)
                           .values(status='error', error=message, snapshot_json='', lease_token='',
                                   lease_until=None, updated_at=_utcnow()))
                db.commit()
        except Exception:
            _logger.exception('Failed to record AI job failure')


async def _ai_scheduler_loop():
    while True:
        try:
            capacity = max(0, AI_CONCURRENCY - len(_ai_worker_tasks))
            if capacity:
                for job_id, token in _claim_ai_jobs(AI_SESSION_FACTORY, capacity):
                    task = asyncio.create_task(_run_ai_job(job_id, token, AI_SESSION_FACTORY))
                    _ai_worker_tasks.add(task)
                    task.add_done_callback(_ai_worker_tasks.discard)
        except Exception:
            _logger.exception('AI job scheduler failed')
        await asyncio.sleep(1)


def start_ai_scheduler():
    global _ai_scheduler
    _ai_scheduler = asyncio.create_task(_ai_scheduler_loop())


async def stop_ai_scheduler():
    global _ai_scheduler
    tasks = list(_ai_worker_tasks)
    if _ai_scheduler:
        _ai_scheduler.cancel(); tasks.append(_ai_scheduler); _ai_scheduler = None
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _ai_worker_tasks.clear()


def _trip(db: Session, user: User, trip_id: int) -> Trip:
    trip = (db.query(Trip).options(selectinload(Trip.days).selectinload(TripDay.items),
                                   selectinload(Trip.places))
            .filter(Trip.id == trip_id, _accessible(user.id)).first())
    if trip is None:
        raise HTTPException(404, "旅行不存在")
    return trip


def _accessible(user_id: int):
    return or_(Trip.user_id == user_id,
               exists().where(TripMember.trip_id == Trip.id, TripMember.user_id == user_id,
                              TripMember.role == 'editor'))


def _claim(db: Session, trip: Trip, version: int):
    result = db.execute(update(Trip).where(Trip.id == trip.id, Trip.version == version)
                        .values(version=Trip.version + 1, updated_at=now_local())
                        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        raise HTTPException(409, "行程已在其他页面更新，请刷新后重试")
    db.expire(trip, ["version", "updated_at"])


def _day(trip: Trip, day_id: int) -> TripDay:
    day = next ( (d for d in trip.days if d.id == day_id), None)
    if not day:
        raise HTTPException(404, "日期不存在")
    return day


def _item(trip: Trip, item_id: int):
    for day in trip.days:
        for item in day.items:
            if item.id == item_id:
                return day, item
    raise HTTPException(404, "行程项目不存在")


def _location_candidate_labels(rows):
    if not isinstance(rows, list):
        return []
    labels = []
    seen = set()
    for row in rows[:5]:
        if not isinstance(row, dict):
            continue
        label = {key: row[key].strip()[:limit] if isinstance(row.get(key), str) else ''
                 for key, limit in (('name', 160), ('address', 300), ('district', 100))}
        identity = tuple(label.values())
        if label['name'] and identity not in seen:
            labels.append(label)
            seen.add(identity)
    return labels


def _serialize(trip: Trip, *, include_location_candidates=True, include_budget_review=True):
    def fields(obj, names):
        return {key: getattr(obj, key) for key in names.split()}
    def itinerary_item(item):
        info = fields(item, "id sort_order name kind start_time duration_min address lng lat amap_id note activity source")
        if include_location_candidates and item.location_candidates:
            info['location_candidates'] = _location_candidate_labels(item.location_candidates)
        return info
    def itinerary_day(day):
        items = route_preferences.ordered(day.items)
        choices = route_preferences.active_selections(day.route_preferences, items)
        return {**fields(day, "id day_number theme"),
                "route_preferences": {key: value['mode'] for key, value in choices.items()},
                "items": [itinerary_item(item) for item in items]}
    return {**fields(trip, "id title destination start_date status source_plan_id version"),
            "walking_preferences": trip.walking_preferences or {},
            **({"budget_review": trip.budget_review or {}} if include_budget_review else {}),
            "created_at": trip.created_at.isoformat(), "updated_at": trip.updated_at.isoformat(),
            "days": [itinerary_day(day) for day in trip.days],
            "places": [fields(p, "id name address lng lat amap_id note source") for p in trip.places]}


@router.get("")
def list_trips(response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    response.headers['Cache-Control'] = 'no-store'
    rows = (db.query(Trip).options(selectinload(Trip.days).selectinload(TripDay.items), selectinload(Trip.places))
            .filter(_accessible(user.id)).order_by(Trip.updated_at.desc(), Trip.id.desc()).all())
    return {"items": [_serialize(t, include_budget_review=False) for t in rows]}


@router.get('/create-capabilities')
def create_capabilities(response: Response, user: User = Depends(get_current_user)):
    response.headers['Cache-Control'] = 'no-store'
    return {'idempotent_creation': True}


@router.post("", status_code=201)
def create_trip(body: TripCreate, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not body.title.strip():
        raise HTTPException(422, "旅行名称不能为空")
    response.headers['Cache-Control'] = 'no-store'
    payload = body.model_dump(mode='json', exclude={'request_id'})
    payload.update(title=body.title.strip(), destination=body.destination.strip())
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                       separators=(',', ':')).encode('utf-8')).hexdigest()
    def previous():
        return db.query(TripCreationRequest).filter_by(user_id=user.id, request_id=str(body.request_id)).first()
    def replay(saved):
        if saved.payload_hash != digest:
            raise HTTPException(409, '这次创建的信息与之前提交的不一致，请先查看已有旅行。')
        if saved.trip_id is None or db.get(Trip, saved.trip_id) is None:
            raise HTTPException(410, '这次创建的旅行已不存在，请重新创建。')
        response.status_code = 200
        return _serialize(_trip(db, user, saved.trip_id))
    if body.request_id:
        saved = previous()
        if saved:
            return replay(saved)
    trip = Trip(user_id=user.id, title=body.title.strip(), destination=body.destination.strip(),
                start_date=body.start_date.isoformat() if body.start_date else None,
                days=[TripDay(day_number=i) for i in range(1, body.days + 1)])
    db.add(trip)
    try:
        db.flush()
        if body.request_id:
            db.add(TripCreationRequest(user_id=user.id, request_id=str(body.request_id),
                                       payload_hash=digest, trip_id=trip.id))
        db.commit()
    except IntegrityError:
        db.rollback()
        saved = previous() if body.request_id else None
        if not saved:
            raise
        return replay(saved)
    return _serialize(_trip(db, user, trip.id))


@router.post('/create', status_code=201)
def create_trip_once(body: TripCreateOnce, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # A distinct route makes old servers reject this operation rather than
    # silently ignoring request_id and performing an unprotected legacy create.
    return create_trip(body, response, db, user)


@router.post("/from-plan/{plan_id}", status_code=201)
def import_plan(plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    plan = db.query(AiPlan).filter(AiPlan.id == plan_id, AiPlan.user_id == user.id,
                                   AiPlan.status == "completed").first()
    if not plan:
        raise HTTPException(404, "规划记录不存在")
    existing = db.query(Trip).filter(Trip.source_plan_id == plan.id).first()
    if existing:
        raise HTTPException(409, f"此规划已转为旅行（ID {existing.id}）")
    trip = _trip_from_plan(plan)
    db.add(trip)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "此规划已转为旅行，请刷新列表")
    return _serialize(_trip(db, user, trip.id))


def _trip_from_plan(plan):
    """Build a copy without changing the original planning snapshot."""
    try:
        result, intent = json.loads(plan.result_json or "{}"), json.loads(plan.intent_json or "{}")
        if not isinstance(result, dict) or not isinstance(intent, dict):
            raise ValueError()
        days = result.get("days")
        if not isinstance(days, list) or not days or len(days) > 30 or not all(isinstance(d, dict) for d in days):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, "历史规划数据不完整，无法转换；原记录未受影响")
    trip = Trip(user_id=plan.user_id, source_plan_id=plan.id,
                title=str(plan.title or result.get("title") or "我的旅行")[:100],
                destination=str(intent.get("destination") or intent.get("city") or "")[:100],
                start_date=trip_start_date(intent.get('dates'), len(days)),
                budget_review=trip_budget.from_plan(intent, result),
                walking_preferences=from_planning(intent.get('preferences')))
    for index, old in enumerate(days, 1):
        day = TripDay(day_number=index, theme=str(old.get("theme") or "")[:100])
        spots = old.get("spots") if isinstance(old.get("spots"), list) else []
        raw_items = old.get("items") if isinstance(old.get("items"), list) else []
        # Verified spots are authoritative; keep non-POI activities too.
        entries = [s for s in spots if isinstance(s, dict)] if spots else [s for s in raw_items if isinstance(s, dict)]
        if spots:
            entries += [s for s in raw_items if isinstance(s, dict) and not place_name(s)]
        entries.sort(key=lambda s: str(s.get("time") or "99:99"))
        for pos, raw in enumerate(entries):
            kind = str(raw.get("type") or "地点")[:30]
            no_place = not place_name(raw)
            time_block = no_place and kind in ('休息', '餐饮')
            activity_name = str(raw.get('activity') or '').strip()
            name = str(raw.get("name") or raw.get("poi") or activity_name or "未命名活动")[:160]
            if time_block:
                name = (activity_name or kind)[:160]
            time = str(raw.get("time") or "")
            import re
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time):
                time = ""
            lng, lat = raw.get("lng"), raw.get("lat")
            unverified = raw.get('verification_status') == 'unverified'
            if unverified or time_block:
                lng = lat = None
            if not (isinstance(lng, (float, int)) and isinstance(lat, (float, int)) and -180 <= lng <= 180 and -90 <= lat <= 90):
                lng, lat = None, None
            duration = raw.get('duration_min')
            duration = duration if type(duration) is int and 0 <= duration <= 1440 else None
            day.items.append(TripItem(sort_order=pos, name=name, kind=kind, start_time=time, duration_min=duration,
                                      address='' if unverified or time_block else str(raw.get("address") or "")[:300],
                                      lng=lng, lat=lat, amap_id='' if unverified or time_block else str(raw.get("amap_id") or "")[:100],
                                      note=str(raw.get("note") or "")[:4000], activity=str(raw.get("activity") or "")[:4000],
                                      source="AI 规划历史（地点待核实）" if unverified else "AI 规划历史",
                                      location_candidates=_location_candidate_labels(raw.get('verification_candidates')) if unverified else []))
        trip.days.append(day)
    return trip


@router.get("/{trip_id}")
def get_trip(trip_id: int, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    response.headers['Cache-Control'] = 'private, no-store'
    return _serialize(_trip(db, user, trip_id))


def _route_pair(day, origin_id, destination_id):
    for origin, destination in route_preferences.adjacent(route_preferences.ordered(day.items)):
        if origin.id == origin_id and destination.id == destination_id:
            return origin, destination
    raise HTTPException(404, "相邻行程地点不存在，请刷新行程")


@router.patch("/{trip_id}/days/{day_id}/route-preference")
def set_route_preference(trip_id: int, day_id: int, body: RoutePreferenceWrite,
                         db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    day = _day(trip, day_id)
    _claim(db, trip, body.version)
    origin, destination = _route_pair(day, body.origin_id, body.destination_id)
    if body.mode and not (route_preferences.located(origin) and route_preferences.located(destination)):
        raise HTTPException(422, '请先确认这段路线的两个地点')
    current = route_preferences.active_selections(day.route_preferences, route_preferences.ordered(day.items))
    key = route_preferences.leg_key(origin, destination)
    if body.mode is None:
        current.pop(key, None)
    else:
        current[key] = route_preferences.selection(origin, destination, body.mode)
    day.route_preferences = current
    db.commit()
    return _serialize(trip)


@router.get("/{trip_id}/days/{day_id}/route")
def get_day_route(trip_id: int, day_id: int, origin_id: int, destination_id: int,
                  response: Response, version: int | None = Query(default=None, ge=1),
                  mode: Literal["walking", "driving"] = "walking",
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    request_version = trip.version
    if version is not None and request_version != version:
        raise HTTPException(409, "行程已更新，请刷新后重新查询路线")
    day = _day(trip, day_id)
    pair = _route_pair(day, origin_id, destination_id)
    # Do not hold the database transaction open during the external provider request.
    db.close()
    try:
        route = trip_routes.get_route(pair[0], pair[1], mode)
    except trip_routes.RouteUnavailable as exc:
        route = None
        unavailable_message = str(exc)
    current = _trip(db, user, trip_id)
    if current.version != request_version:
        raise HTTPException(409, "路线查询期间行程已更新，请刷新后重试")
    response.headers['Cache-Control'] = 'private, no-store'
    if route is None:
        return {"status": "unavailable", "message": unavailable_message, "origin_id": origin_id,
                "destination_id": destination_id, "mode": mode, "trip_version": request_version}
    return {"status": "ok", "origin_id": origin_id, "destination_id": destination_id,
            "mode": mode, "trip_version": request_version, "source": "高德地图", **route}


@router.post("/{trip_id}/ai-jobs", status_code=202)
async def start_ai_job(trip_id: int, body: ProposalIn, response: Response,
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    request = body.request.strip()
    if not request:
        raise HTTPException(422, '请填写希望 AI 如何调整行程')
    trip = _trip(db, user, trip_id)
    if trip.version != body.version:
        raise HTTPException(409, '行程已在其他页面更新，请刷新后重试')
    current = db.query(TripAiJob).filter_by(user_id=user.id, trip_id=trip_id).first()
    if current and current.status in ('queued', 'running'):
        if current.version != body.version or current.request != request:
            raise HTTPException(409, '已有建议正在生成，请等待完成')
        response.headers['Cache-Control'] = 'no-store'
        return _job_snapshot(current, db, trip)
    if db.query(TripAiJob).filter(TripAiJob.status.in_(['queued', 'running'])).count() >= 100:
        raise HTTPException(503, '当前生成任务较多，请稍后再试')
    snapshot = json.dumps(_serialize(trip, include_location_candidates=False, include_budget_review=False), ensure_ascii=False)
    if len(snapshot) > 60000:
        raise HTTPException(422, '行程内容过长，请先精简后再使用 AI 助手')
    values = dict(version=trip.version, request=request, snapshot_json=snapshot, status='queued',
                  lease_token='', lease_until=None, attempts=0, proposal_id=None, error='', updated_at=_utcnow())
    if current:
        result = db.execute(update(TripAiJob).where(TripAiJob.id == current.id,
                            TripAiJob.status.in_(['done', 'error'])).values(**values))
        if not result.rowcount:
            db.rollback(); raise HTTPException(409, '已有建议正在生成，请等待完成')
    else:
        db.add(TripAiJob(user_id=user.id, trip_id=trip_id, **values))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '已有建议正在生成，请等待完成') from None
    current = db.query(TripAiJob).filter_by(user_id=user.id, trip_id=trip_id).one()
    response.headers['Cache-Control'] = 'no-store'
    return _job_snapshot(current, db, trip)


@router.get("/{trip_id}/ai-jobs/latest")
def latest_ai_job(trip_id: int, response: Response, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    job = db.query(TripAiJob).filter_by(user_id=user.id, trip_id=trip_id).first()
    response.headers['Cache-Control'] = 'no-store'
    if job is None:
        return {'job': None}
    if job.version != trip.version:
        # An outdated in-flight job still occupies this user's single job slot.
        # Do not expose its old request or proposal, but allow the UI to wait.
        if job.status in ('queued', 'running'):
            return {'job': {'status': 'stale_running', 'version': job.version}}
        return {'job': None}
    return {'job': _job_snapshot(job, db, trip)}


@router.get("/{trip_id}/ai-proposals/latest")
def latest_proposal(trip_id: int, response: Response, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """Recover the newest unapplied preview for the current trip version."""
    trip = _trip(db, user, trip_id)
    response.headers['Cache-Control'] = 'no-store'
    proposal = (db.query(TripProposal)
                .filter_by(trip_id=trip.id, base_version=trip.version, status='pending')
                .order_by(TripProposal.id.desc()).first())
    if proposal is None:
        return {"proposal": None}
    try:
        _, changes = trip_ai.simulate(_serialize(trip), json.loads(proposal.operations_json))
    except (ValueError, TypeError):
        return {"proposal": None}
    return {"proposal": {"id": proposal.id, "base_version": proposal.base_version, "changes": changes}}


@router.post("/{trip_id}/ai-proposals", status_code=201)
async def create_proposal(trip_id: int, body: ProposalIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not body.request.strip():
        raise HTTPException(422, "请填写希望 AI 如何调整行程")
    trip = _trip(db, user, trip_id)
    if trip.version != body.version:
        raise HTTPException(409, "行程已在其他页面更新，请刷新后重试")
    snapshot = _serialize(trip, include_location_candidates=False, include_budget_review=False)
    # Do not hold a database connection while waiting on an external model.
    db.close()
    try:
        operations, changes = await trip_ai.propose(snapshot, body.request.strip())
    except Exception:
        raise HTTPException(502, "AI 建议生成失败，请稍后重试或缩小修改范围") from None
    trip = _trip(db, user, trip_id)
    if trip.version != body.version:
        raise HTTPException(409, "生成期间行程已更新，请重新生成建议")
    proposal = TripProposal(trip_id=trip.id, base_version=trip.version, request=body.request.strip(),
                            operations_json=json.dumps(operations, ensure_ascii=False))
    db.add(proposal)
    db.commit()
    return {"id": proposal.id, "base_version": proposal.base_version, "changes": changes}


@router.post("/{trip_id}/ai-proposals/{proposal_id}/apply")
def apply_proposal(trip_id: int, proposal_id: int, body: Versioned,
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    proposal = db.query(TripProposal).filter(TripProposal.id == proposal_id, TripProposal.trip_id == trip.id).first()
    if proposal is None:
        raise HTTPException(404, "建议不存在")
    if proposal.status != "pending" or body.version != proposal.base_version:
        raise HTTPException(409, "建议已应用或行程已更新，请重新生成建议")
    try:
        final, changes = trip_ai.simulate(_serialize(trip), json.loads(proposal.operations_json))
    except (ValueError, TypeError):
        raise HTTPException(409, "建议已失效，请重新生成") from None
    if not changes:
        raise HTTPException(422, "建议没有可应用的修改")
    _claim(db, trip, proposal.base_version)
    from app.db.trip_expense_models import TripExpense
    existing = {item.id: item for day in trip.days for item in day.items}
    kept_ids = set()
    for day in final['days']:
        for position, data in enumerate(day['items']):
            if data['id'] > 0:
                item = existing[data['id']]
                kept_ids.add(item.id)
                for name in ('start_time', 'duration_min', 'note', 'activity'):
                    setattr(item, name, data[name])
                if item.day_id != day['id']:
                    db.query(TripExpense).filter_by(trip_id=trip_id, item_id=item.id).update({TripExpense.day_id: day['id']}, synchronize_session=False)
                item.day_id = day['id']
                item.sort_order = position
            else:
                values = {name: data[name] for name in ('name', 'kind', 'start_time', 'duration_min', 'note', 'activity', 'address', 'lng', 'lat', 'amap_id', 'source')}
                db.add(TripItem(day_id=day['id'], sort_order=position, **values))
    for item_id, item in existing.items():
        if item_id not in kept_ids:
            from app.db.footprint_models import TripFootprint
            from app.db.reservation_models import TripReservation
            db.query(TripExpense).filter_by(trip_id=trip_id, item_id=item_id).update({TripExpense.item_id: None, TripExpense.day_id: item.day_id}, synchronize_session=False)
            db.query(TripFootprint).filter_by(trip_id=trip_id, item_id=item_id).update({TripFootprint.item_id: None}, synchronize_session=False)
            db.query(TripReservation).filter_by(trip_id=trip_id, item_id=item_id).update({TripReservation.item_id: None}, synchronize_session=False)
            db.delete(item)
    if any(day.route_preferences for day in trip.days):
        db.flush()
        for day in trip.days:
            if day.route_preferences:
                db.expire(day, ['items'])
                day.route_preferences = route_preferences.active_selections(day.route_preferences, route_preferences.ordered(day.items))
    proposal.status = 'applied'
    db.commit()
    db.expire_all()
    return _serialize(_trip(db, user, trip_id))


@router.patch("/{trip_id}/budget-review")
def save_budget_review(trip_id: int, body: BudgetReviewWrite, response: Response,
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    _claim(db, trip, body.version)
    trip.budget_review = {
        **(trip.budget_review or {}),
        'checklist': body.checklist.model_dump(mode='json'),
        'checklist_scope': {'start_date': trip.start_date, 'destination': trip.destination, 'days': len(trip.days)},
    }
    db.commit()
    response.headers['Cache-Control'] = 'private, no-store'
    return _serialize(trip)


@router.patch("/{trip_id}")
def update_trip(trip_id: int, body: TripPatch, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    data = body.model_dump(exclude_unset=True, exclude={"version"})
    if "title" in data and (data["title"] is None or not data["title"].strip()):
        raise HTTPException(422, "旅行名称不能为空")
    if any(data.get(k, "valid") is None for k in ("destination", "status")):
        raise HTTPException(422, "目的地和状态不能为 null")
    if 'walking_preferences' in data:
        prefs = body.walking_preferences
        data['walking_preferences'] = prefs.model_dump(exclude_defaults=True) if prefs else {}
    _claim(db, trip, body.version)
    if 'destination' in data and data['destination'].strip() != trip.destination.strip():
        for day in trip.days:
            for item in day.items:
                if item.location_candidates:
                    item.location_candidates = []
    for key, value in data.items():
        setattr(trip, key, value.isoformat() if isinstance(value, date) else value)
    db.commit()
    return _serialize(_trip(db, user, trip_id))


@router.post("/{trip_id}/days", status_code=201)
def add_day(trip_id: int, body: Versioned, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    if len(trip.days) >= 30:
        raise HTTPException(422, "一段旅行最多 30 天")
    _claim(db, trip, body.version)
    db.add(TripDay(trip_id=trip.id, day_number=len(trip.days) + 1))
    db.commit()
    db.expire_all()
    return _serialize(_trip(db, user, trip_id))


@router.patch("/{trip_id}/days/{day_id}")
def update_day(trip_id: int, day_id: int, body: DayPatch, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    day = _day(trip, day_id)
    _claim(db, trip, body.version)
    day.theme = body.theme.strip()
    db.commit(); db.expire_all()
    return _serialize(_trip(db, user, trip_id))


@router.post("/{trip_id}/days/{day_id}/remove")
def remove_day(trip_id: int, day_id: int, body: Versioned, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.db.trip_expense_models import TripExpense
    from app.db.footprint_models import TripFootprint
    trip = _trip(db, user, trip_id)
    day = _day(trip, day_id)
    if len(trip.days) <= 1 or day.id != trip.days[-1].id:
        raise HTTPException(422, "只能删除最后一天，且旅行至少保留一天")
    if (day.items or day.theme.strip() or
        db.query(TripExpense.id).filter_by(trip_id=trip_id, day_id=day.id).first() or
        db.query(TripFootprint.id).filter_by(trip_id=trip_id, day_number=day.day_number).first()):
        raise HTTPException(409, "这一天仍有关联内容，请先处理安排、主题、账目或足迹")
    _claim(db, trip, body.version)
    db.delete(day)
    db.commit()
    db.expire_all()
    return _serialize(_trip(db, user, trip_id))


@router.post("/{trip_id}/days/{day_id}/items", status_code=201)
def add_item(trip_id: int, day_id: int, body: ItemWrite, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    day = _day(trip, day_id)
    _claim(db, trip, body.version)
    db.add(TripItem(day_id=day.id, sort_order=len(day.items), **body.model_dump(exclude={"version"})))
    db.commit()
    db.expire_all()
    return _serialize(_trip(db, user, trip_id))


@router.patch("/{trip_id}/items/{item_id}")
def update_item(trip_id: int, item_id: int, body: ItemWrite, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    day, item = _item(trip, item_id)
    _claim(db, trip, body.version)
    if body.name != item.name or body.lng is not None or body.amap_id:
        item.location_candidates = []
    for key, value in body.model_dump(exclude={"version"}).items():
        setattr(item, key, value)
    if day.route_preferences:
        day.route_preferences = route_preferences.active_selections(day.route_preferences, route_preferences.ordered(day.items))
    db.commit()
    return _serialize(trip)


@router.post("/{trip_id}/items/{item_id}/move")
def move_item(trip_id: int, item_id: int, body: MoveIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    origin, item = _item(trip, item_id)
    target = _day(trip, body.day_id)
    _claim(db, trip, body.version)
    source_items = [i for i in origin.items if i.id != item_id]
    target_items = source_items if origin.id == target.id else list(target.items)
    target_items.insert(min(body.index, len(target_items)), item)
    item.day_id = target.id
    for n, i in enumerate(target_items):
        i.sort_order = n
    if origin.id != target.id:
        from app.db.trip_expense_models import TripExpense
        db.query(TripExpense).filter_by(trip_id=trip_id, item_id=item_id).update({TripExpense.day_id: target.id}, synchronize_session=False)
        for n, i in enumerate(source_items):
            i.sort_order = n
        if origin.route_preferences:
            origin.route_preferences = route_preferences.active_selections(origin.route_preferences, source_items)
    if target.route_preferences:
        target.route_preferences = route_preferences.active_selections(target.route_preferences, target_items)
    db.commit()
    db.expire_all()
    return _serialize(_trip(db, user, trip_id))


@router.post("/{trip_id}/items/{item_id}/delete")
def delete_item(trip_id: int, item_id: int, body: Versioned, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    day, item = _item(trip, item_id)
    _claim(db, trip, body.version)
    from app.db.trip_expense_models import TripExpense
    from app.db.footprint_models import TripFootprint
    from app.db.reservation_models import TripReservation
    db.query(TripExpense).filter_by(trip_id=trip_id, item_id=item_id).update({TripExpense.item_id: None, TripExpense.day_id: day.id}, synchronize_session=False)
    db.query(TripFootprint).filter_by(trip_id=trip_id, item_id=item_id).update({TripFootprint.item_id: None}, synchronize_session=False)
    db.query(TripReservation).filter_by(trip_id=trip_id, item_id=item_id).update({TripReservation.item_id: None}, synchronize_session=False)
    kept = route_preferences.ordered(i for i in day.items if i.id != item_id)
    for n, remaining in enumerate(kept):
        remaining.sort_order = n
    if day.route_preferences:
        day.route_preferences = route_preferences.active_selections(day.route_preferences, kept)
    db.delete(item)
    db.commit()
    db.expire_all()
    return _serialize(_trip(db, user, trip_id))


@router.post("/{trip_id}/places", status_code=201)
def add_place(trip_id: int, body: PlaceWrite, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    if body.amap_id and any(p.amap_id == body.amap_id for p in trip.places):
        raise HTTPException(409, "该地点已在待安排列表中")
    _claim(db, trip, body.version)
    db.add(TripPlace(trip_id=trip.id, **body.model_dump(exclude={"version"})))
    db.commit()
    db.expire_all()
    return _serialize(_trip(db, user, trip_id))


@router.post("/{trip_id}/places/{place_id}/schedule")
def schedule_place(trip_id: int, place_id: int, body: SchedulePlace, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    day = _day(trip, body.day_id)
    place = next((p for p in trip.places if p.id == place_id), None)
    if place is None:
        raise HTTPException(404, "待安排地点不存在")
    _claim(db, trip, body.version)
    db.add(TripItem(day_id=day.id, sort_order=len(day.items), name=place.name,
                    address=place.address, lng=place.lng, lat=place.lat,
                    amap_id=place.amap_id, note=place.note, source=place.source))
    db.delete(place)
    db.commit()
    db.expire_all()
    return _serialize(_trip(db, user, trip_id))


@router.post("/{trip_id}/places/{place_id}/delete")
def delete_place(trip_id: int, place_id: int, body: Versioned, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = _trip(db, user, trip_id)
    place = next((p for p in trip.places if p.id == place_id), None)
    if place is None:
        raise HTTPException(404, "待安排地点不存在")
    _claim(db, trip, body.version)
    db.delete(place)
    db.commit()
    db.expire_all()
    return _serialize(_trip(db, user, trip_id))
