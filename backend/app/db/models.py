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
from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
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


def now_local() -> datetime:
    """本地时间（服务器与开发机都按 Asia/Shanghai 运行）。

    早先这里存的是 UTC，但所有接口都用 strftime 原样输出，导致页面时间比
    本地时间早 8 小时。改成直接存本地时间，展示层不再需要额外转换。
    """
    return datetime.now()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    nickname: Mapped[str] = mapped_column(String(50), default="")
    profile_version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    avatar: Mapped[str] = mapped_column(String(500), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # 管理员可停用账户
    created_at: Mapped[datetime] = mapped_column(default=now_local)

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
    created_at: Mapped[datetime] = mapped_column(default=now_local)

    comments: Mapped[list["Comment"]] = relationship(back_populates="spot", cascade="all, delete-orphan")


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "spot_id", name="uq_user_spot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    spot_id: Mapped[int] = mapped_column(ForeignKey("spots.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=now_local)

    user: Mapped["User"] = relationship(back_populates="favorites")
    spot: Mapped["Spot"] = relationship()


class SpotFavoriteState(Base):
    __tablename__ = 'spot_favorite_states'
    __table_args__ = (UniqueConstraint('user_id','spot_id',name='uq_spot_favorite_state'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'),index=True)
    spot_id: Mapped[int] = mapped_column(ForeignKey('spots.id'),index=True)
    favorited: Mapped[bool] = mapped_column(Boolean,default=False)
    version: Mapped[int] = mapped_column(Integer,default=0)


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    spot_id: Mapped[int] = mapped_column(ForeignKey("spots.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5 分，可空
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)  # 审核删除=软删除
    created_at: Mapped[datetime] = mapped_column(default=now_local)

    spot: Mapped["Spot"] = relationship(back_populates="comments")
    creation_requests: Mapped[list["CommentCreationRequest"]] = relationship(back_populates='comment')


class CommentCreationRequest(Base):
    __tablename__ = 'comment_creation_requests'
    __table_args__ = (UniqueConstraint('user_id','request_id',name='uq_comment_creation_request'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'),index=True)
    spot_id: Mapped[int] = mapped_column(ForeignKey('spots.id'),index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    payload_hash: Mapped[str] = mapped_column(String(64))
    comment_id: Mapped[int | None] = mapped_column(ForeignKey('comments.id',ondelete='SET NULL'),nullable=True)
    comment: Mapped["Comment | None"] = relationship(back_populates='creation_requests')


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
    version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    revision: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(default=now_local)
    updated_at: Mapped[datetime] = mapped_column(default=now_local, onupdate=now_local)

    user: Mapped["User"] = relationship(back_populates="guides")
    images: Mapped[list["GuideImage"]] = relationship(back_populates="guide", cascade="all, delete-orphan")
    spots: Mapped[list["Spot"]] = relationship(secondary=guide_spots_table)
    creation_requests: Mapped[list["GuideCreationRequest"]] = relationship(back_populates='guide')


class GuideCreationRequest(Base):
    __tablename__ = 'guide_creation_requests'
    __table_args__ = (UniqueConstraint('user_id', 'request_id', name='uq_guide_creation_request'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    payload_hash: Mapped[str] = mapped_column(String(64))
    guide_id: Mapped[int | None] = mapped_column(ForeignKey('guides.id', ondelete='SET NULL'), nullable=True)
    guide: Mapped["Guide | None"] = relationship(back_populates='creation_requests')


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
    version: Mapped[int] = mapped_column(Integer,default=1,server_default='1')
    revision: Mapped[str] = mapped_column(String(36),default=lambda:str(uuid4()))
    restore_until: Mapped[float | None] = mapped_column(Float,nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft/completed/failed
    intent_json: Mapped[str] = mapped_column(Text, default="{}")  # 澄清后的需求（城市/天数/偏好等）
    process_json: Mapped[str] = mapped_column(Text, default="[]")  # 规划过程（LangGraph 节点轨迹）
    result_json: Mapped[str] = mapped_column(Text, default="{}")  # 最终行程
    # v1.1 快照：历史记录要能"完整回到当时"——聊天内容 + 地图视角一起存
    title: Mapped[str] = mapped_column(String(100), default="")    # 行程标题（历史列表展示用）
    summary: Mapped[str] = mapped_column(String(300), default="")  # 一句话摘要（历史列表展示用）
    chat_json: Mapped[str] = mapped_column(Text, default="[]")     # 对话快照 [{role,text,logs}]
    map_json: Mapped[str] = mapped_column(Text, default="{}")      # 地图状态 {center,zoom}
    created_at: Mapped[datetime] = mapped_column(default=now_local)

    user: Mapped["User"] = relationship(back_populates="ai_plans")


class PlanGenerationJob(Base):
    """Original /plan jobs; only execution handles remain process-local."""
    __tablename__ = 'plan_generation_jobs'
    id: Mapped[str] = mapped_column(String(32),primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'),nullable=True,index=True)
    session_id: Mapped[str] = mapped_column(String(50),default='')
    status: Mapped[str] = mapped_column(String(20),default='running',index=True)
    seq: Mapped[int] = mapped_column(Integer,default=0)
    events_json: Mapped[str] = mapped_column(Text,default='[]')
    text: Mapped[str] = mapped_column(Text,default='')
    result_json: Mapped[str | None] = mapped_column(Text,nullable=True)
    request_json: Mapped[str] = mapped_column(Text)
    plan_id: Mapped[int | None] = mapped_column(Integer,nullable=True)
    error: Mapped[str] = mapped_column(String(300),default='')
    attempts: Mapped[int] = mapped_column(Integer,default=0)
    lease_token: Mapped[str] = mapped_column(String(32),default='')
    lease_until: Mapped[float] = mapped_column(Float,default=0,index=True)
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float,index=True)
    creation_requests: Mapped[list['PlanCreationRequest']] = relationship(back_populates='job')


class PlanCreationRequest(Base):
    __tablename__ = 'plan_creation_requests'
    __table_args__ = (UniqueConstraint('owner_key','request_id',name='uq_plan_creation_request'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_key: Mapped[str] = mapped_column(String(80))
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'),nullable=True,index=True)
    request_id: Mapped[str] = mapped_column(String(36))
    payload_hash: Mapped[str] = mapped_column(String(64))
    job_id: Mapped[str | None] = mapped_column(ForeignKey('plan_generation_jobs.id',ondelete='SET NULL'),nullable=True,index=True)
    cancelled: Mapped[bool] = mapped_column(Boolean,default=False)
    job: Mapped['PlanGenerationJob | None'] = relationship(back_populates='creation_requests')


class ChannelInventory(Base):
    """模拟渠道按酒店、房型、日期维护的库存快照。价格以货币最小单位保存。"""
    __tablename__ = "channel_inventory"
    __table_args__ = (UniqueConstraint("channel", "hotel_id", "room_id", "stay_date", name="uq_channel_inventory"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(40), index=True)
    hotel_id: Mapped[str] = mapped_column(String(80))
    hotel_name: Mapped[str] = mapped_column(String(160))
    room_id: Mapped[str] = mapped_column(String(80))
    room_name: Mapped[str] = mapped_column(String(160))
    stay_date: Mapped[str] = mapped_column(String(10), index=True)
    available: Mapped[int] = mapped_column(Integer)
    price_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    updated_at: Mapped[datetime] = mapped_column(default=now_local, onupdate=now_local)


class ChannelSyncRun(Base):
    """同步结果与请求摘要；不保存完整外部数据。"""
    __tablename__ = "channel_sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(default=now_local)
    updated_at: Mapped[datetime] = mapped_column(default=now_local, onupdate=now_local)


class KnowledgeChunk(Base):
    """知识库片段：攻略与景点统一切成片段后供语义检索（RAG 检索层）。

    为什么不把向量存进 spots / guides：业务表被检索层反复改写会让后续迁移和
    后台编辑都变复杂。单独一张表可以整表重建（灌库脚本先清后插），业务数据零影响。
    embedding 存 float32 原始字节（1024 维 = 4KB/条），不存 JSON：
    同样内容 JSON 要 20KB 上下，几千条就会把 SQLite 撑大几倍。
    """

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "chunk_index", name="uq_knowledge_chunk"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(String(20), index=True)   # guide / spot
    source_id: Mapped[int] = mapped_column(Integer, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)       # 同一来源内的第几块
    city: Mapped[str] = mapped_column(String(30), default="", index=True)
    title: Mapped[str] = mapped_column(String(200), default="")        # 小节标题或景点名
    content: Mapped[str] = mapped_column(Text)                         # 片段正文
    tags: Mapped[str] = mapped_column(String(200), default="")         # 逗号分隔
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    source_url: Mapped[str] = mapped_column(String(500), default="")   # 出处链接
    updated_at: Mapped[datetime] = mapped_column(default=now_local, onupdate=now_local)


class LlmUsage(Base):
    """LLM 调用计量：每次 chat 调用落一行（2026-11-27 新增）。

    为什么单独一张表而不是塞进 ai_plans：调用计量是横切数据，澄清/行程/攻略
    哪条链路都会产生 token 消耗，按行追加最简单，也方便按天聚合。
    只增不改：写路径只有 INSERT，聚合走 SQL，不存在并发写冲突。
    ts 存本地时间 ISO 字符串（SQLite date() 可直接解析），按天分组够用。
    """

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(String(32), index=True)            # ISO 本地时间
    route: Mapped[str] = mapped_column(String(60), default="")         # 调用场景标签（可选）
    model: Mapped[str] = mapped_column(String(60), default="")         # 模型名
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)     # 输入 token
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0) # 输出 token
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)      # 总 token
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)        # 调用耗时
    ok: Mapped[int] = mapped_column(Integer, default=1)                # 1 成功 / 0 失败
    error: Mapped[str] = mapped_column(String(120), default="")        # 失败类型名
