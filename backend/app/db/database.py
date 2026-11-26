"""数据库连接层：SQLite + SQLAlchemy 2.0。

预留切换：换 PostgreSQL/MySQL 只改 DATABASE_URL（连接串），模型零改动。
"""
from pathlib import Path
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# 本文件位于 backend/app/db/，项目根在上三级
PROJECT_ROOT = Path(__file__).resolve().parents[3]
_configured_data_dir = os.environ.get('TRAVEL_DATA_DIR', '')
if _configured_data_dir and not Path(_configured_data_dir).is_absolute():
    raise ValueError('TRAVEL_DATA_DIR must be an absolute path')
DATA_DIR = Path(_configured_data_dir).resolve() if _configured_data_dir else PROJECT_ROOT / "backend" / "data"
DATABASE_PATH = DATA_DIR / "app.db"
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

# check_same_thread=False：FastAPI 多线程访问 SQLite 的标准做法
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """全部 ORM 模型的基类。"""


def get_db():
    """FastAPI 依赖注入：每个请求一个会话，用完自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_missing_columns():
    """老库补列：使用 ALTER TABLE ADD COLUMN 按需补齐（幂等）。

    为什么需要：create_all 对"表已存在"的情况不会自动加列；不补的话查询会报
    no such column。v0.8 补过 guides，v1.1 补 ai_plans 快照四列。
    """
    from sqlalchemy import inspect, text
    from uuid import uuid4

    insp = inspect(engine)
    tables = insp.get_table_names()
    with engine.begin() as conn:
        if 'users' in tables:
            existing = {c['name'] for c in insp.get_columns('users')}
            if 'profile_version' not in existing:
                conn.execute(text('ALTER TABLE users ADD COLUMN profile_version INTEGER NOT NULL DEFAULT 1'))
        if "guides" in tables:
            existing = {c["name"] for c in insp.get_columns("guides")}
            if "views" not in existing:
                conn.execute(text("ALTER TABLE guides ADD COLUMN views INTEGER DEFAULT 0"))
            if "is_draft" not in existing:
                conn.execute(text("ALTER TABLE guides ADD COLUMN is_draft BOOLEAN DEFAULT 0"))
            if "version" not in existing:
                conn.execute(text("ALTER TABLE guides ADD COLUMN version INTEGER NOT NULL DEFAULT 1"))
            if "revision" not in existing:
                conn.execute(text("ALTER TABLE guides ADD COLUMN revision VARCHAR(36) NOT NULL DEFAULT ''"))
            # Stable identity protects a stale editor if SQLite reuses a deleted id.
            for guide_id, in conn.execute(text("SELECT id FROM guides WHERE revision = '' OR revision IS NULL")).fetchall():
                conn.execute(text('UPDATE guides SET revision = :revision WHERE id = :id'), {'revision':str(uuid4()), 'id':guide_id})
        if "ai_plans" in tables:
            existing = {c["name"] for c in insp.get_columns("ai_plans")}
            for col, ddl in [
                ("title", "VARCHAR(100) DEFAULT ''"),
                ("summary", "VARCHAR(300) DEFAULT ''"),
                ("chat_json", "TEXT DEFAULT '[]'"),
                ("map_json", "TEXT DEFAULT '{}'"),
                ('version', 'INTEGER NOT NULL DEFAULT 1'),
                ('revision', "VARCHAR(36) NOT NULL DEFAULT ''"),
                ('restore_until', 'FLOAT'),
            ]:
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE ai_plans ADD COLUMN {col} {ddl}"))
            for plan_id, in conn.execute(text("SELECT id FROM ai_plans WHERE revision = '' OR revision IS NULL")).fetchall():
                conn.execute(text('UPDATE ai_plans SET revision=:revision WHERE id=:id'),{'revision':str(uuid4()),'id':plan_id})
        if 'trips' in tables and 'walking_preferences' not in {c['name'] for c in insp.get_columns('trips')}:
            conn.execute(text("ALTER TABLE trips ADD COLUMN walking_preferences JSON NOT NULL DEFAULT '{}'"))
        if 'trips' in tables and 'budget_review' not in {c['name'] for c in insp.get_columns('trips')}:
            conn.execute(text("ALTER TABLE trips ADD COLUMN budget_review JSON NOT NULL DEFAULT '{}'"))
        if 'trip_days' in tables and 'route_preferences' not in {c['name'] for c in insp.get_columns('trip_days')}:
            conn.execute(text("ALTER TABLE trip_days ADD COLUMN route_preferences JSON NOT NULL DEFAULT '{}'"))
        if "trip_items" in tables:
            existing = {c["name"] for c in insp.get_columns("trip_items")}
            if "source" not in existing:
                conn.execute(text("ALTER TABLE trip_items ADD COLUMN source VARCHAR(200) DEFAULT ''"))
            if 'location_candidates' not in existing:
                conn.execute(text("ALTER TABLE trip_items ADD COLUMN location_candidates JSON NOT NULL DEFAULT '[]'"))
        if "guest_trip_imports" in tables:
            existing = {c["name"] for c in insp.get_columns("guest_trip_imports")}
            if "payload_hash" not in existing:
                conn.execute(text("ALTER TABLE guest_trip_imports ADD COLUMN payload_hash VARCHAR(64) DEFAULT ''"))


def init_db():
    """建库建表（幂等：已存在的表跳过，缺的列自动补齐）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    from app.db import models, trip_models, trip_expense_models, guest_import_models, sharing_models, footprint_models, reservation_models  # noqa: F401 确保模型注册到 Base.metadata

    Base.metadata.create_all(engine)
    _add_missing_columns()
