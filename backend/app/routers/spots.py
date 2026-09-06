"""景点只读接口（v0.6）：列表筛选分页 / 热门推荐 / 猜你喜欢 / 详情。"""
import random
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.db.database import get_db
from app.db.models import Comment, Favorite, Spot, User
from app.routers.auth import get_current_user

router = APIRouter(prefix="/api/spots", tags=["spots"])


class SpotItem(BaseModel):
    id: int
    name: str
    city: str
    district: str
    tags: str
    is_free: bool
    price: Optional[float] = None
    image_url: str

    model_config = {"from_attributes": True}


class SpotListOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[SpotItem]


class CommentOut(BaseModel):
    id: int
    content: str
    rating: Optional[int] = None
    created_at: str
    username: str

    model_config = {"from_attributes": True}


class SpotDetailOut(BaseModel):
    spot: SpotItem
    address: str
    description: str
    source: str
    source_url: str
    favorite_count: int
    comments: list[CommentOut]


def _apply_filters(db: Session, city: str, keyword: str, is_free: str):
    """列表筛选：城市精确 + 关键词模糊（名称/标签/区县）。"""
    q = db.query(Spot)
    if city:
        q = q.filter(Spot.city == city)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(Spot.name.like(like) | Spot.tags.like(like) | Spot.district.like(like))
    if is_free == "free":
        q = q.filter(Spot.is_free.is_(True))
    elif is_free == "paid":
        q = q.filter(Spot.is_free.is_(False))
    return q


@router.get("", response_model=SpotListOut)
def list_spots(
    city: str = "",
    keyword: str = "",
    is_free: Literal["all", "free", "paid"] = "all",
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
    db: Session = Depends(get_db),
):
    q = _apply_filters(db, city, keyword, is_free)
    total = q.count()
    items = (
        q.order_by(Spot.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return SpotListOut(
        total=total, page=page, page_size=page_size,
        items=[SpotItem.model_validate(s) for s in items],
    )


def _hot_spot_ids(db: Session, limit: int) -> list[int]:
    """按收藏次数排热点位；无收藏数据时按 id 兜底。"""
    rows = (
        db.query(Favorite.spot_id, func.count(Favorite.id).label("n"))
        .group_by(Favorite.spot_id)
        .order_by(func.count(Favorite.id).desc())
        .limit(limit)
        .all()
    )
    ids = [r[0] for r in rows]
    if len(ids) < limit:
        existing = set(ids)
        for (sid,) in db.query(Spot.id).order_by(Spot.id).limit(limit * 2):
            if sid not in existing:
                ids.append(sid)
                if len(ids) >= limit:
                    break
    return ids[:limit]


@router.get("/hot", response_model=list[SpotItem])
def hot_spots(db: Session = Depends(get_db)):
    """首页"热门景区推荐"：收藏 Top4（冷启动按 id 兜底）。"""
    ids = _hot_spot_ids(db, 4)
    spots = db.query(Spot).filter(Spot.id.in_(ids)).all() if ids else []
    spots.sort(key=lambda s: ids.index(s.id))
    return [SpotItem.model_validate(s) for s in spots]


@router.get("/for-you", response_model=SpotListOut)
def for_you(page: int = Query(1, ge=1, le=3), db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    """首页"猜你喜欢"（登录态）：按收藏标签偏好优先，热度兜底，池子 36 条分 3 页。"""
    PAGE_SIZE = 12
    pool = _preference_pool(db, user)
    page_items = pool[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    return SpotListOut(total=len(pool), page=page, page_size=PAGE_SIZE,
                       items=[SpotItem.model_validate(s) for s in page_items])


def _preference_pool(db: Session, user: User) -> list[Spot]:
    """偏好池：收藏标签命中的景点优先，热度/序位兜底补齐到 36 条。"""
    POOL = 36
    tag_likes = []
    my_spots = (
        db.query(Spot).join(Favorite, Favorite.spot_id == Spot.id)
        .filter(Favorite.user_id == user.id).all()
    )
    for s in my_spots:
        tag_likes.extend(t for t in s.tags.split(",") if t)
    result: list[Spot] = []
    seen: set[int] = set()
    if tag_likes:
        from sqlalchemy import or_
        likes = list({f"%{t}%" for t in tag_likes})
        picked = (
            db.query(Spot)
            .filter(or_(*[Spot.tags.like(l) for l in likes]))
            .order_by(Spot.id)
            .limit(POOL)
            .all()
        )
        result = list(picked)
        seen = {s.id for s in result}
    if len(result) < POOL:
        for sid in _hot_spot_ids(db, POOL * 2):
            if sid in seen:
                continue
            s = db.get(Spot, sid)
            if s:
                result.append(s)
                seen.add(s.id)
            if len(result) >= POOL:
                break
    return result[:POOL]


@router.get("/for-you-guest", response_model=SpotListOut)
def for_you_guest(page: int = Query(1, ge=1, le=3), db: Session = Depends(get_db)):
    """游客"猜你喜欢"：全站收藏热度优先，冷启动按序补齐，池子 36 条分 3 页。"""
    PAGE_SIZE = 12
    POOL = 36
    pool: list[Spot] = []
    seen: set[int] = set()
    for sid in _hot_spot_ids(db, POOL):
        s = db.get(Spot, sid)
        if s:
            pool.append(s)
            seen.add(s.id)
    if len(pool) < POOL:
        for s in db.query(Spot).order_by(Spot.id).limit(POOL * 2):
            if s.id not in seen:
                pool.append(s)
                seen.add(s.id)
            if len(pool) >= POOL:
                break
    total = min(len(pool), POOL)
    page_items = pool[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    return SpotListOut(total=total, page=page, page_size=PAGE_SIZE,
                       items=[SpotItem.model_validate(s) for s in page_items])


@router.get("/{spot_id}", response_model=SpotDetailOut)
def spot_detail(spot_id: int, db: Session = Depends(get_db)):
    spot = (
        db.query(Spot).options(joinedload(Spot.comments))
        .filter(Spot.id == spot_id).first()
    )
    if spot is None:
        raise HTTPException(404, "景点不存在")
    fav_count = db.query(Favorite).filter(Favorite.spot_id == spot_id).count()
    comments = (
        db.query(Comment, User.username)
        .join(User, Comment.user_id == User.id)
        .filter(Comment.spot_id == spot_id, Comment.is_deleted.is_(False))
        .order_by(Comment.created_at.desc())
        .limit(20)
        .all()
    )
    return SpotDetailOut(
        spot=SpotItem.model_validate(spot),
        address=spot.address,
        description=spot.description,
        source=spot.source,
        source_url=spot.source_url,
        favorite_count=fav_count,
        comments=[
            CommentOut(
                id=c.id, content=c.content, rating=c.rating,
                created_at=c.created_at.strftime("%Y-%m-%d %H:%M"),
                username=name,
            )
            for c, name in comments
        ],
    )
