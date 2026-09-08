"""个人中心接口（v0.6）：我的收藏 / 我发布的攻略 / 编辑资料。"""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Favorite, Guide, Spot, User
from app.routers.auth import get_current_user
from app.routers.spots import SpotItem

router = APIRouter(prefix="/api/me", tags=["me"])


class FavoriteOut(BaseModel):
    spot: SpotItem
    created_at: str

    model_config = {"from_attributes": True}


class GuideItem(BaseModel):
    id: int
    title: str
    city: str
    cover_image: str
    created_at: str
    views: int = 0
    is_draft: bool = False  # v0.8：草稿标记（个人中心可见，公开列表不可见）

    model_config = {"from_attributes": True}


class ProfileOut(BaseModel):
    id: int
    username: str
    nickname: str
    avatar: str
    is_admin: bool
    created_at: str
    favorite_count: int
    guide_count: int

    model_config = {"from_attributes": True}


class ProfileIn(BaseModel):
    nickname: Optional[str] = Field(default=None, min_length=1, max_length=50)
    avatar: Optional[str] = Field(default=None, max_length=500)

    @field_validator("nickname")
    @classmethod
    def reject_blank(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("昵称不能为空")
        return v


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


@router.get("/profile", response_model=ProfileOut)
def my_profile(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """个人中心总览：资料 + 收藏/攻略统计。"""
    return ProfileOut(
        id=user.id,
        username=user.username,
        nickname=user.nickname,
        avatar=user.avatar,
        is_admin=user.is_admin,
        created_at=_fmt(user.created_at),
        favorite_count=db.query(Favorite).filter(Favorite.user_id == user.id).count(),
        guide_count=db.query(Guide).filter(Guide.user_id == user.id).count(),
    )


@router.get("/favorites", response_model=list[FavoriteOut])
def my_favorites(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """我收藏的景点（按收藏时间倒序）。"""
    rows = (
        db.query(Favorite, Spot)
        .join(Spot, Favorite.spot_id == Spot.id)
        .filter(Favorite.user_id == user.id)
        .order_by(Favorite.created_at.desc())
        .all()
    )
    return [
        FavoriteOut(spot=SpotItem.model_validate(spot), created_at=_fmt(fav.created_at))
        for fav, spot in rows
    ]


@router.get("/guides", response_model=list[GuideItem])
def my_guides(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """我发布的攻略。"""
    guides = (
        db.query(Guide)
        .filter(Guide.user_id == user.id)
        .order_by(Guide.created_at.desc())
        .all()
    )
    return [
        GuideItem(
            id=g.id, title=g.title, city=g.city,
            cover_image=g.cover_image, created_at=_fmt(g.created_at),
            views=g.views or 0, is_draft=g.is_draft,
        )
        for g in guides
    ]


@router.patch("/profile", response_model=ProfileOut)
def update_profile(
    body: ProfileIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """编辑个人资料（昵称/头像，只改传了的字段）。"""
    if body.nickname is not None:
        user.nickname = body.nickname.strip() or user.nickname
    if body.avatar is not None:
        user.avatar = body.avatar
    db.commit()
    db.refresh(user)
    return my_profile(db, user)
