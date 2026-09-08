"""全套 ORM 模型（v0.5 数据底座）：对齐 PROJECT_BRIEF.md 的 8 个后台模块。

表清单：
- users          用户（注册登录、个人中心）
- spots          景点（高德 POI 采集入库）
- favorites      景点收藏
- comments       景点评论
- guides         旅游攻略
- guide_images   攻略图片（多张）
- guide_spots    攻略关联景点（多对多）
- ai_plans       AI 路线规划记录（管理员后台查看）
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

# 攻略↔景点 多对多关联表
guide_spots_table = Table(
    "guide_spots",
    Base.metadata,
    Column("guide_id", ForeignKey("guides.id"), primary_key=True),
    Column("spot_id", ForeignKey("spots.id"), primary_key=True),
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    nickname: Mapped[str] = mapped_column(String(50), default="")
    avatar: Mapped[str] = mapped_column(String(500), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # 管理员可停用账户
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    favorites: Mapped[list["Favorite"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    guides: Mapped[list["Guide"]] = relationship(back_populates="user")
    ai_plans: Mapped[list["AiPlan"]] = relationship(back_populates="user")


class Spot(Base):
    __tablename__ = "spots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    city: Mapped[str] = mapped_column(String(30), index=True)
    district: Mapped[str] = mapped_column(String(50), default="")  # 区县，如"青羊区"
    address: Mapped[str] = mapped_column(String(200), default="")
    tags: Mapped[str] = mapped_column(String(200), default="")  # 逗号分隔，如"历史街区,美食"
    description: Mapped[str] = mapped_column(Text, default="")
    is_free: Mapped[bool] = mapped_column(Boolean, default=False)  # 采集时按类型粗判，后台可改
    price: Mapped[float | None] = mapped_column(Float, nullable=True)  # 门票参考价，未知为 NULL
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="高德地图开放平台")
    source_url: Mapped[str] = mapped_column(String(500), default="")
    image_url: Mapped[str] = mapped_column(String(500), default="")  # POI 自带照片，无图留空
    amap_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)  # 防重复入库
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    comments: Mapped[list["Comment"]] = relationship(back_populates="spot", cascade="all, delete-orphan")


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "spot_id", name="uq_user_spot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    spot_id: Mapped[int] = mapped_column(ForeignKey("spots.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    user: Mapped["User"] = relationship(back_populates="favorites")
    spot: Mapped["Spot"] = relationship()


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    spot_id: Mapped[int] = mapped_column(ForeignKey("spots.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5 分，可空
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)  # 审核删除=软删除
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    spot: Mapped["Spot"] = relationship(back_populates="comments")


class Guide(Base):
    __tablename__ = "guides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    city: Mapped[str] = mapped_column(String(30), default="", index=True)
    cover_image: Mapped[str] = mapped_column(String(500), default="")
    views: Mapped[int] = mapped_column(Integer, default=0)  # v0.8：浏览量（详情页每次打开 +1）
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False)  # v0.8：草稿不进公开列表
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship(back_populates="guides")
    images: Mapped[list["GuideImage"]] = relationship(back_populates="guide", cascade="all, delete-orphan")
    spots: Mapped[list["Spot"]] = relationship(secondary=guide_spots_table)


class GuideImage(Base):
    __tablename__ = "guide_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guide_id: Mapped[int] = mapped_column(ForeignKey("guides.id"), index=True)
    image_url: Mapped[str] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    guide: Mapped["Guide"] = relationship(back_populates="images")


class AiPlan(Base):
    """AI 路线规划记录：管理员后台可查规划状态、意图数据、过程与结果。"""

    __tablename__ = "ai_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(50), index=True)  # 对应一次规划会话
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft/completed/failed
    intent_json: Mapped[str] = mapped_column(Text, default="{}")  # 澄清后的需求（城市/天数/偏好等）
    process_json: Mapped[str] = mapped_column(Text, default="[]")  # 规划过程（LangGraph 节点轨迹）
    result_json: Mapped[str] = mapped_column(Text, default="{}")  # 最终行程
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    user: Mapped["User"] = relationship(back_populates="ai_plans")
