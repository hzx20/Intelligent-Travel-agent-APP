"""管理员模拟渠道房态同步 API。此接口不连接任何真实 OTA。"""
import hashlib
import json
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import ChannelInventory, ChannelSyncRun
from app.routers.admin import get_current_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin/channel-inventory", tags=["模拟酒店渠道"],
                   dependencies=[Depends(get_current_admin)])
CHANNEL = "demo_overseas"


class InventoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hotel_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    hotel_name: str = Field(min_length=1, max_length=160)
    room_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    room_name: str = Field(min_length=1, max_length=160)
    date: date
    available: int = Field(strict=True, ge=0, le=100000)
    price_minor: int = Field(strict=True, ge=0, le=1000000000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @field_validator("hotel_name", "room_name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("名称不能为空")
        return value.strip()


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    items: list[InventoryItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def distinct_keys(self):
        keys = [(item.hotel_id, item.room_id, item.date) for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("同一酒店、房型和日期不能重复")
        return self


def _run_dict(run):
    return {"request_id": run.request_id, "status": run.status, "item_count": run.item_count,
            "attempts": run.attempts, "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat()}


@router.post("/sync")
def sync_inventory(body: SyncRequest, db: Session = Depends(get_db)):
    """模拟外部渠道推送快照；同请求 ID 同内容可安全重放。"""
    raw = json.dumps(body.model_dump(mode="json", exclude={"request_id"}),
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    run = db.query(ChannelSyncRun).filter_by(request_id=body.request_id).first()
    if run and run.payload_hash != digest:
        raise HTTPException(409, "request_id 已用于不同内容")
    if run and run.status == "success":
        return {**_run_dict(run), "replayed": True, "channel": CHANNEL}
    attempts = (run.attempts + 1) if run else 1
    try:
        if run is None:
            run = ChannelSyncRun(request_id=body.request_id, payload_hash=digest,
                                 status="success", item_count=len(body.items), attempts=attempts)
            db.add(run)
            # 尽早取得唯一键，避免并发重复执行。
            db.flush()
        else:
            run.status, run.item_count, run.attempts = "success", len(body.items), attempts
        for item in body.items:
            row = db.query(ChannelInventory).filter_by(
                channel=CHANNEL, hotel_id=item.hotel_id, room_id=item.room_id,
                stay_date=item.date.isoformat()).first()
            if row is None:
                row = ChannelInventory(channel=CHANNEL, hotel_id=item.hotel_id,
                                       room_id=item.room_id, stay_date=item.date.isoformat())
                db.add(row)
            row.hotel_name = item.hotel_name
            row.room_name = item.room_name
            row.available = item.available
            row.price_minor = item.price_minor
            row.currency = item.currency
        db.commit()
        db.refresh(run)
        logger.info("channel sync succeeded request_id=%s count=%s", body.request_id, len(body.items))
        return {**_run_dict(run), "replayed": False, "channel": CHANNEL}
    except IntegrityError:
        db.rollback()
        # 并发同 ID、不同批次同时写相同库存时，交由客户端重试；绝不部分写入。
        logger.warning("channel sync conflict request_id=%s", body.request_id)
        raise HTTPException(409, "同步冲突，请使用相同请求重试")
    except Exception:
        db.rollback()
        logger.exception("channel sync failed request_id=%s", body.request_id)
        # 独立事务记录失败；库存事务已完整回滚，相同请求可重试。
        try:
            failed = db.query(ChannelSyncRun).filter_by(request_id=body.request_id).first()
            if failed is None:
                failed = ChannelSyncRun(request_id=body.request_id, payload_hash=digest,
                                        status="failed", item_count=0, attempts=attempts)
                db.add(failed)
            elif failed.payload_hash == digest and failed.status != "success":
                failed.status, failed.attempts = "failed", attempts
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("unable to record sync failure request_id=%s", body.request_id)
        raise HTTPException(503, "同步失败，请用相同请求重试")


@router.get("/inventory")
def list_inventory(hotel_id: str | None = Query(default=None, max_length=80),
                   stay_date: date | None = None, limit: int = Query(default=50, ge=1, le=100),
                   offset: int = Query(default=0, ge=0), db: Session = Depends(get_db)):
    q = db.query(ChannelInventory).filter_by(channel=CHANNEL)
    if hotel_id:
        q = q.filter_by(hotel_id=hotel_id)
    if stay_date:
        q = q.filter_by(stay_date=stay_date.isoformat())
    rows = q.order_by(ChannelInventory.id).offset(offset).limit(limit).all()
    return {"channel": CHANNEL, "items": [{"hotel_id": r.hotel_id, "hotel_name": r.hotel_name,
        "room_id": r.room_id, "room_name": r.room_name, "date": r.stay_date,
        "available": r.available, "price_minor": r.price_minor, "currency": r.currency,
        "updated_at": r.updated_at.isoformat()} for r in rows], "limit": limit, "offset": offset}


@router.get("/sync-runs")
def list_sync_runs(limit: int = Query(default=50, ge=1, le=100),
                   offset: int = Query(default=0, ge=0), db: Session = Depends(get_db)):
    runs = db.query(ChannelSyncRun).order_by(ChannelSyncRun.id.desc()).offset(offset).limit(limit).all()
    return {"items": [_run_dict(run) for run in runs], "limit": limit, "offset": offset}
