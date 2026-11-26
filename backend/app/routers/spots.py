"""景点接口（v0.6）：列表筛选分页 / 热门推荐 / 猜你喜欢 / 详情 / 收藏 / 评论。"""
from typing import Literal, Optional
import hashlib
import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.db.database import get_db
from app.db.models import Comment, CommentCreationRequest, Favorite, SpotFavoriteState, Spot, User
from app.routers.auth import bearer, get_current_user, get_current_user_optional
from app.security import decode_token

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
    lng: Optional[float] = None  # v0.7：给静态地图用（详情页单点图、行程图标点）
    lat: Optional[float] = None
    amap_id: str = ""  # 可用于旅行待安排清单的地点去重

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
    favorited: bool = False  # 当前用户是否已收藏（游客恒为 False）
    favorite_version: int = 0
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


@router.get("/regions")
def list_regions(db: Session = Depends(get_db)):
    """目的地联想候选：按城市聚合区县（区县按景点数降序）。
    数据直接来自 spots 表，与数据底座自动保持同步。"""
    rows = (
        db.query(Spot.city, Spot.district, func.count(Spot.id).label("n"))
        .filter(Spot.city.isnot(None), Spot.city != "")
        .group_by(Spot.city, Spot.district)
        .all()
    )
    regions: dict[str, list] = {}
    counts: dict[str, int] = {}
    for city, district, n in rows:
        regions.setdefault(city, [])
        counts[city] = counts.get(city, 0) + n
        if district:
            regions[city].append({"name": district, "count": n})
    for city in regions:
        regions[city].sort(key=lambda d: -d["count"])
    # 城市按景点总数排序，热门城市靠前
    ordered = sorted(regions.items(), key=lambda kv: (-counts[kv[0]], kv[0]))
    return {
        "cities": [
            {"city": city, "districts": districts, "count": counts[city]}
            for city, districts in ordered
        ],
        "total": db.query(Spot).count(),
    }


@router.get("/hot", response_model=list[SpotItem])
def hot_spots(db: Session = Depends(get_db)):
    """首页"热门景区推荐"：收藏 Top4（冷启动按 id 兜底）。"""
    ids = _hot_spot_ids(db, 4)
    spots = db.query(Spot).filter(Spot.id.in_(ids)).all() if ids else []
    spots.sort(key=lambda s: ids.index(s.id))
    return [SpotItem.model_validate(s) for s in spots]


@router.get("/for-you", response_model=SpotListOut)
def for_you(response: Response, page: int = Query(1, ge=1, le=3), db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    """首页"猜你喜欢"（登录态）：按收藏标签偏好优先，热度兜底，池子 36 条分 3 页。"""
    PAGE_SIZE = 12
    response.headers['Cache-Control'] = 'private, no-store'
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


@router.get('/write-capabilities')
def write_capabilities(response: Response, user: User = Depends(get_current_user)):
    response.headers['Cache-Control'] = 'no-store'
    return {'idempotent_comments': True, 'versioned_favorites': True}


@router.get("/{spot_id}", response_model=SpotDetailOut)
def spot_detail(
    spot_id: int,
    response: Response,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    spot = (
        db.query(Spot).options(joinedload(Spot.comments))
        .filter(Spot.id == spot_id).first()
    )
    if spot is None:
        raise HTTPException(404, "景点不存在")
    fav_count = db.query(Favorite).filter(Favorite.spot_id == spot_id).count()
    favorited = bool(
        user
        and db.query(Favorite)
        .filter(Favorite.user_id == user.id, Favorite.spot_id == spot_id)
        .first()
    )
    state = db.query(SpotFavoriteState).filter_by(user_id=user.id,spot_id=spot_id).first() if user else None
    if state is not None: favorited = state.favorited
    response.headers['Cache-Control'] = 'no-store'
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
        favorited=favorited,
        favorite_version=state.version if state else 0,
        comments=[
            CommentOut(
                id=c.id, content=c.content, rating=c.rating,
                created_at=c.created_at.strftime("%Y-%m-%d %H:%M"),
                username=name,
            )
            for c, name in comments
        ],
    )


class CommentIn(BaseModel):
    content: str = Field(min_length=1, max_length=500)
    rating: Optional[int] = Field(default=None, ge=1, le=5)

    @field_validator("content")
    @classmethod
    def reject_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("评论内容不能为空")
        return v


class CommentCreate(CommentIn):
    request_id: UUID | None = None


class CommentCreateOnce(CommentCreate):
    request_id: UUID


class ReliableCommentOut(CommentOut):
    request_id: UUID
    spot_id: int


class FavoriteWrite(BaseModel):
    favorited: bool
    version: int = Field(ge=0)


def _favorite_out(db, user_id, spot_id, favorited, version):
    return {'favorited':favorited,'favorite_version':version,
            'favorite_count':db.query(Favorite).filter_by(spot_id=spot_id).count()}


@router.get('/{spot_id}/favorite-state')
def favorite_state(spot_id: int, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if db.get(Spot,spot_id) is None: raise HTTPException(404,'景点不存在')
    state = db.query(SpotFavoriteState).filter_by(user_id=user.id,spot_id=spot_id).first()
    favorited = state.favorited if state else bool(db.query(Favorite).filter_by(user_id=user.id,spot_id=spot_id).first())
    response.headers['Cache-Control'] = 'no-store'
    return _favorite_out(db,user.id,spot_id,favorited,state.version if state else 0)


@router.put('/{spot_id}/favorite-state')
def set_favorite(spot_id: int, body: FavoriteWrite, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if db.get(Spot,spot_id) is None: raise HTTPException(404,'景点不存在')
    response.headers['Cache-Control'] = 'no-store'
    for _ in range(3):
        state = db.query(SpotFavoriteState).filter_by(user_id=user.id,spot_id=spot_id).first()
        if state is None:
            state = SpotFavoriteState(user_id=user.id,spot_id=spot_id,version=0,
                                      favorited=bool(db.query(Favorite).filter_by(user_id=user.id,spot_id=spot_id).first()))
            db.add(state)
            try: db.flush()
            except IntegrityError:
                db.rollback(); continue
        if body.version > state.version or (body.version != state.version and body.favorited != state.favorited):
            db.rollback()
            raise HTTPException(409,'收藏状态已更新，本次未覆盖。请核对后再决定。')
        version = state.version + int(body.favorited != state.favorited)
        claim = db.execute(update(SpotFavoriteState).where(SpotFavoriteState.id==state.id,SpotFavoriteState.version==state.version)
                           .values(favorited=body.favorited,version=version).execution_options(synchronize_session=False))
        if claim.rowcount != 1:
            db.rollback(); continue
        saved = db.query(Favorite).filter_by(user_id=user.id,spot_id=spot_id).first()
        if body.favorited and saved is None: db.add(Favorite(user_id=user.id,spot_id=spot_id))
        if not body.favorited and saved is not None: db.delete(saved)
        try:
            db.flush()
            result = _favorite_out(db,user.id,spot_id,body.favorited,version)
            db.commit()
            return result
        except IntegrityError: db.rollback()
    raise HTTPException(409,'收藏状态正在变化，请核对后再决定。')


@router.post("/{spot_id}/favorite")
def toggle_favorite(
    spot_id: int,
    response: Response,
    body: FavoriteWrite | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Legacy path requires an explicit desired state and version too."""
    if db.get(Spot, spot_id) is None:
        raise HTTPException(404, "景点不存在")
    if body is None: raise HTTPException(428,'请更新客户端，明确提交收藏目标及原版本。')
    return set_favorite(spot_id,body,response,db,user)


@router.post("/{spot_id}/comments", status_code=201, response_model=CommentOut)
def add_comment(
    spot_id: int,
    body: CommentCreate,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """发表景点评论（1-500 字，可选 1-5 星评分）。"""
    if db.get(Spot, spot_id) is None:
        raise HTTPException(404, "景点不存在")
    response.headers['Cache-Control'] = 'no-store'
    digest = hashlib.sha256(json.dumps({'content':body.content,'rating':body.rating},ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def previous():
        return db.query(CommentCreationRequest).filter_by(user_id=user.id,request_id=str(body.request_id)).first()
    def result(comment):
        return {'id':comment.id,'content':comment.content,'rating':comment.rating,
                'created_at':comment.created_at.strftime('%Y-%m-%d %H:%M'),'username':user.username,
                'request_id':str(body.request_id) if body.request_id else None,'spot_id':spot_id}
    def replay(saved):
        if saved.spot_id != spot_id or saved.payload_hash != digest:
            raise HTTPException(409,'这次评论的信息或景点与之前不一致，请先核对。')
        comment = db.get(Comment,saved.comment_id) if saved.comment_id is not None else None
        if comment is None or comment.is_deleted:
            raise HTTPException(410,'这次评论已被删除，不会重新发布。')
        response.status_code = 200
        return result(comment)
    if body.request_id:
        saved = previous()
        if saved: return replay(saved)
    comment = Comment(
        user_id=user.id,
        spot_id=spot_id,
        content=body.content.strip(),
        rating=body.rating,
    )
    db.add(comment)
    try:
        db.flush()
        if body.request_id: db.add(CommentCreationRequest(user_id=user.id,spot_id=spot_id,request_id=str(body.request_id),payload_hash=digest,comment_id=comment.id))
        db.commit()
    except IntegrityError:
        db.rollback()
        saved = previous() if body.request_id else None
        if saved: return replay(saved)
        raise
    return result(comment)


@router.post('/{spot_id}/comments/create',status_code=201,response_model=ReliableCommentOut)
def add_comment_once(spot_id: int, body: CommentCreateOnce, response: Response, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return add_comment(spot_id,body,response,db,user)
