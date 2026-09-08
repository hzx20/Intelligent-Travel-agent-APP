"""攻略接口（v0.8「游记笔墨」）：列表检索 / 详情 / 发布 / 编辑 / 删除 / 关联景点。

权限口径（与全站一致）：
- 看：游客可看公开攻略；草稿只有作者本人和管理员能看
- 写/改/删：必须登录，且只能是作者本人或管理员
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session, joinedload

from app.db.database import get_db
from app.db.models import Guide, GuideImage, Spot, User, guide_spots_table
from app.routers.auth import get_current_user, get_current_user_optional
from app.routers.spots import SpotItem

router = APIRouter(prefix="/api/guides", tags=["guides"])

SUMMARY_LEN = 90


class GuideOut(BaseModel):
    """列表卡片：封面 + 标题 + 作者 + 摘要（对齐原型单列大图版式）。"""

    id: int
    title: str
    city: str
    cover_image: str
    author: str
    created_at: str
    views: int
    is_draft: bool = False
    summary: str
    spot_count: int


class GuideListOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[GuideOut]


class GuideDetailOut(BaseModel):
    id: int
    title: str
    content: str
    city: str
    cover_image: str
    author: str
    author_id: int
    created_at: str
    updated_at: str
    views: int
    is_draft: bool
    is_owner: bool = False
    images: list[str] = []
    spots: list[SpotItem] = []


class GuideIn(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=20000)
    city: str = Field(default="", max_length=30)
    images: list[str] = Field(default=[], max_length=30)
    spot_ids: list[int] = Field(default=[], max_length=10)
    is_draft: bool = False

    @field_validator("title", "content")
    @classmethod
    def reject_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("标题和正文不能为空")
        return v


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def _summary(content: str) -> str:
    """摘要：压平换行取前 90 字，给列表卡片用。"""
    flat = " ".join(content.split())
    return flat[:SUMMARY_LEN] + ("…" if len(flat) > SUMMARY_LEN else "")


def _author_name(guide: Guide) -> str:
    """作者展示名：有昵称用昵称，没有用账号名。"""
    u = guide.user
    return (u.nickname or u.username) if u else "匿名"


def _to_out(guide: Guide) -> GuideOut:
    return GuideOut(
        id=guide.id,
        title=guide.title,
        city=guide.city,
        cover_image=guide.cover_image,
        author=_author_name(guide),
        created_at=_fmt(guide.created_at),
        views=guide.views or 0,
        is_draft=guide.is_draft,
        summary=_summary(guide.content),
        spot_count=len(guide.spots),
    )


def _apply_search(db: Session, q, keyword: str):
    """多关键词检索（空格分隔，"与"关系）。

    判定规则：每个词都必须在"标题/正文/城市"或"任一关联景点名"中出现。
    实现：先算每个词命中的攻略 id 集合，再取交集（纯 Python 集合运算，
    当前量级足够；将来数据大了可换全文索引）。
    """
    words = [w for w in keyword.split() if w]
    if not words:
        return q
    hit_sets = []
    for w in words:
        like = f"%{w}%"
        base = {
            gid
            for (gid,) in db.query(Guide.id).filter(
                Guide.title.like(like) | Guide.content.like(like) | Guide.city.like(like)
            ).all()
        }
        by_spot = {
            r[0]
            for r in db.query(guide_spots_table.c.guide_id)
            .join(Spot, Spot.id == guide_spots_table.c.spot_id)
            .filter(Spot.name.like(like))
            .all()
        }
        hit_sets.append(base | by_spot)
    ids = set.intersection(*hit_sets) if hit_sets else set()
    return q.filter(Guide.id.in_(ids))


@router.get("", response_model=GuideListOut)
def list_guides(
    city: str = "",
    keyword: str = "",
    sort: str = "new",
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """攻略列表：城市筛选 + 多关键词全文检索 + 最新/最热排序。草稿不公开。"""
    q = db.query(Guide).options(joinedload(Guide.user), joinedload(Guide.spots))
    q = q.filter(Guide.is_draft.is_(False))
    if city:
        q = q.filter(Guide.city == city)
    if keyword.strip():
        q = _apply_search(db, q, keyword.strip())
    total = q.count()
    q = q.order_by(Guide.views.desc() if sort == "hot" else Guide.created_at.desc())
    items = q.offset((page - 1) * page_size).limit(page_size).all()
    return GuideListOut(
        total=total, page=page, page_size=page_size,
        items=[_to_out(g) for g in items],
    )


def _load_guide(db: Session, guide_id: int) -> Guide:
    g = (
        db.query(Guide)
        .options(joinedload(Guide.user), joinedload(Guide.spots), joinedload(Guide.images))
        .filter(Guide.id == guide_id)
        .first()
    )
    if g is None:
        raise HTTPException(404, "攻略不存在")
    return g


def _visible(guide: Guide, user: User | None) -> bool:
    """草稿仅作者本人/管理员可见。"""
    return (not guide.is_draft) or bool(
        user and (user.id == guide.user_id or user.is_admin)
    )


@router.get("/{guide_id}", response_model=GuideDetailOut)
def guide_detail(
    guide_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    """攻略详情：图片画廊 + 正文 + 关联景点。非作者每次打开浏览量 +1。"""
    g = _load_guide(db, guide_id)
    if not _visible(g, user):
        raise HTTPException(404, "攻略不存在")
    is_owner = bool(user and (user.id == g.user_id or user.is_admin))
    if not is_owner:
        g.views = (g.views or 0) + 1
        db.commit()
    images = sorted(g.images, key=lambda i: i.sort_order)
    return GuideDetailOut(
        id=g.id,
        title=g.title,
        content=g.content,
        city=g.city,
        cover_image=g.cover_image,
        author=_author_name(g),
        author_id=g.user_id,
        created_at=_fmt(g.created_at),
        updated_at=_fmt(g.updated_at),
        views=g.views or 0,
        is_draft=g.is_draft,
        is_owner=is_owner,
        images=[i.image_url for i in images],
        spots=[SpotItem.model_validate(s) for s in g.spots],
    )


def _save_images(db: Session, guide: Guide, urls: list[str]):
    """图片整体替换：先清后写，首张自动作封面。"""
    db.query(GuideImage).filter(GuideImage.guide_id == guide.id).delete()
    for idx, url in enumerate([u for u in urls if u.strip()][:30]):
        db.add(GuideImage(guide_id=guide.id, image_url=url.strip(), sort_order=idx))
    guide.cover_image = urls[0].strip() if urls else ""


def _save_spots(db: Session, guide: Guide, spot_ids: list[int]):
    """关联景点整体替换（最多 10 个，自动丢弃不存在的 id）。"""
    guide.spots = []
    valid = (
        db.query(Spot).filter(Spot.id.in_(spot_ids)).all() if spot_ids else []
    )
    guide.spots = valid[:10]


@router.post("", status_code=201)
def create_guide(
    body: GuideIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """发布/存草稿攻略（需登录）。"""
    g = Guide(
        user_id=user.id,
        title=body.title,
        content=body.content,
        city=body.city.strip()[:30],
        is_draft=body.is_draft,
    )
    db.add(g)
    db.flush()  # 先拿到 g.id，再写图片/关联
    _save_images(db, g, body.images)
    _save_spots(db, g, body.spot_ids)
    db.commit()
    return {"id": g.id, "is_draft": g.is_draft}


def _ensure_owner(guide: Guide, user: User):
    if guide.user_id != user.id and not user.is_admin:
        raise HTTPException(403, "只能操作自己的攻略")


@router.patch("/{guide_id}")
def update_guide(
    guide_id: int,
    body: GuideIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """编辑攻略（作者本人或管理员）。"""
    g = _load_guide(db, guide_id)
    _ensure_owner(g, user)
    g.title = body.title
    g.content = body.content
    g.city = body.city.strip()[:30]
    g.is_draft = body.is_draft
    _save_images(db, g, body.images)
    _save_spots(db, g, body.spot_ids)
    db.commit()
    return {"id": g.id, "is_draft": g.is_draft}


@router.delete("/{guide_id}", status_code=204)
def delete_guide(
    guide_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删除攻略（作者本人或管理员）；图片与关联记录随级联清理。"""
    g = _load_guide(db, guide_id)
    _ensure_owner(g, user)
    db.delete(g)
    db.commit()
