"""管理员后台接口（v0.6 收尾）：对齐 BRIEF 8 模块，攻略图片/关联景点并入攻略详情返回。"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.db.database import get_db
from app.db.models import AiPlan, Comment, Favorite, Guide, GuideImage, Spot, User
from app.routers.auth import get_current_user
from app.routers.spots import SpotItem


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    """管理员守卫：非管理员一律 403。"""
    if not user.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return user


router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[
    Depends(get_current_admin),  # 整组统一守卫（依赖缓存，无额外开销）
])


class AdminUserOut(BaseModel):
    id: int
    username: str
    nickname: str
    is_admin: bool
    is_active: bool
    created_at: str
    favorite_count: int

    model_config = {"from_attributes": True}


class AdminSpotIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=100)
    tags: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    is_free: Optional[bool] = None
    price: Optional[float] = None


class AdminSpotOut(BaseModel):
    id: int
    name: str
    city: str
    district: str
    tags: str
    is_free: bool
    price: Optional[float] = None
    favorite_count: int

    model_config = {"from_attributes": True}


class AdminGuideOut(BaseModel):
    id: int
    title: str
    city: str
    author: str
    created_at: str
    images: list[str]
    spots: list[SpotItem]

    model_config = {"from_attributes": True}


class AdminAiPlanOut(BaseModel):
    id: int
    username: str
    session_id: str
    status: str
    intent: str
    created_at: str

    model_config = {"from_attributes": True}


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


# ── 用户管理 ──
@router.get("/users", response_model=list[AdminUserOut])
def admin_users(
    keyword: str = "",
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    q = db.query(User)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(User.username.like(like) | User.nickname.like(like))
    users = q.order_by(User.id).limit(200).all()
    out = []
    for u in users:
        out.append(AdminUserOut(
            id=u.id, username=u.username, nickname=u.nickname,
            is_admin=u.is_admin, is_active=u.is_active,
            created_at=_fmt(u.created_at),
            favorite_count=db.query(Favorite).filter(Favorite.user_id == u.id).count(),
        ))
    return out


@router.patch("/users/{user_id}", response_model=AdminUserOut)
def admin_update_user(
    user_id: int,
    is_active: Optional[bool] = None,
    is_admin: Optional[bool] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """启用/停用账户、授/收管理员。不允许管理员停用自己。"""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")
    if target.id == admin.id and is_active is False:
        raise HTTPException(400, "不能停用自己的账户")
    if is_active is not None:
        target.is_active = is_active
    if is_admin is not None:
        target.is_admin = is_admin
    db.commit()
    db.refresh(target)
    return AdminUserOut(
        id=target.id, username=target.username, nickname=target.nickname,
        is_admin=target.is_admin, is_active=target.is_active,
        created_at=_fmt(target.created_at),
        favorite_count=db.query(Favorite).filter(Favorite.user_id == target.id).count(),
    )


# ── 景点管理 ──
@router.get("/spots", response_model=list[AdminSpotOut])
def admin_spots(
    keyword: str = "",
    city: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    q = db.query(Spot)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(Spot.name.like(like) | Spot.tags.like(like))
    if city:
        q = q.filter(Spot.city == city)
    rows = (
        q.order_by(Spot.id).offset((page - 1) * page_size).limit(page_size).all()
    )
    return [
        AdminSpotOut(
            id=s.id, name=s.name, city=s.city, district=s.district, tags=s.tags,
            is_free=s.is_free, price=s.price,
            favorite_count=db.query(Favorite).filter(Favorite.spot_id == s.id).count(),
        )
        for s in rows
    ]


@router.patch("/spots/{spot_id}", response_model=AdminSpotOut)
def admin_update_spot(
    spot_id: int,
    body: AdminSpotIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """编辑景点基础数据（名称/标签/简介/免费标记/票价——含修正采集粗判）。"""
    spot = db.get(Spot, spot_id)
    if spot is None:
        raise HTTPException(404, "景点不存在")
    for field in ("name", "tags", "description", "is_free", "price"):
        val = getattr(body, field)
        if val is not None:
            setattr(spot, field, val)
    db.commit()
    db.refresh(spot)
    return AdminSpotOut(
        id=spot.id, name=spot.name, city=spot.city, district=spot.district,
        tags=spot.tags, is_free=spot.is_free, price=spot.price,
        favorite_count=db.query(Favorite).filter(Favorite.spot_id == spot.id).count(),
    )


# ── 景点收藏（查看）──
@router.get("/favorites")
def admin_favorites(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db), _admin: User = Depends(get_current_admin),
):
    q = (
        db.query(Favorite, User.username, Spot.name)
        .join(User, Favorite.user_id == User.id)
        .join(Spot, Favorite.spot_id == Spot.id)
        .order_by(Favorite.created_at.desc())
    )
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total,
        "items": [
            {"id": f.id, "username": name, "spot": spot_name, "created_at": _fmt(f.created_at)}
            for f, name, spot_name in rows
        ],
    }


# ── 景点评论（审核删除=软删）──
@router.get("/comments")
def admin_comments(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db), _admin: User = Depends(get_current_admin),
):
    q = (
        db.query(Comment, User.username, Spot.name)
        .join(User, Comment.user_id == User.id)
        .join(Spot, Comment.spot_id == Spot.id)
        .filter(Comment.is_deleted.is_(False))
        .order_by(Comment.created_at.desc())
    )
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total,
        "items": [
            {"id": c.id, "username": name, "spot": spot_name, "content": c.content,
             "rating": c.rating, "created_at": _fmt(c.created_at)}
            for c, name, spot_name in rows
        ],
    }


@router.delete("/comments/{comment_id}", status_code=204)
def admin_delete_comment(
    comment_id: int, db: Session = Depends(get_db), _admin: User = Depends(get_current_admin),
):
    c = db.get(Comment, comment_id)
    if c is None:
        raise HTTPException(404, "评论不存在")
    c.is_deleted = True
    db.commit()


# ── 旅游攻略（图片/关联景点并入详情）──
@router.get("/guides", response_model=list[AdminGuideOut])
def admin_guides(
    keyword: str = "", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db), _admin: User = Depends(get_current_admin),
):
    q = (
        db.query(Guide).options(joinedload(Guide.user), joinedload(Guide.spots), joinedload(Guide.images))
    )
    if keyword:
        q = q.filter(Guide.title.like(f"%{keyword}%"))
    guides = (
        q.order_by(Guide.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size).all()
    )
    return [
        AdminGuideOut(
            id=g.id, title=g.title, city=g.city, author=g.user.username,
            created_at=_fmt(g.created_at),
            images=[i.image_url for i in sorted(g.images, key=lambda x: x.sort_order)],
            spots=[SpotItem.model_validate(s) for s in g.spots],
        )
        for g in guides
    ]


# ── AI 规划记录 ──
@router.get("/ai-plans", response_model=list[AdminAiPlanOut])
def admin_ai_plans(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db), _admin: User = Depends(get_current_admin),
):
    rows = (
        db.query(AiPlan, User.username)
        .join(User, AiPlan.user_id == User.id)
        .order_by(AiPlan.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size).all()
    )
    import json as _json

    return [
        AdminAiPlanOut(
            id=p.id, username=name, session_id=p.session_id, status=p.status,
            intent=(_json.loads(p.intent_json or "{}").get("city") or "未记录城市"),
            created_at=_fmt(p.created_at),
        )
        for p, name in rows
    ]
