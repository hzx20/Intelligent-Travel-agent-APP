"""数据库连接层：SQLite + SQLAlchemy 2.0。

预留切换：换 PostgreSQL/MySQL 只改 DATABASE_URL（连接串），模型零改动。
"""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# 本文件位于 backend/app/db/，项目根在上三级
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "backend" / "data"
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
    """老库补列：SQLite 的 ALTER TABLE 只支持加列，这里按需补齐（幂等）。

    为什么需要：v0.8 给 guides 表新增 views / is_draft 两列，而 create_all
    对"表已存在"的情况不会自动加列；不补的话查询会报 no such column。
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "guides" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("guides")}
    with engine.begin() as conn:
        if "views" not in existing:
            conn.execute(text("ALTER TABLE guides ADD COLUMN views INTEGER DEFAULT 0"))
        if "is_draft" not in existing:
            conn.execute(text("ALTER TABLE guides ADD COLUMN is_draft BOOLEAN DEFAULT 0"))


def init_db():
    """建库建表（幂等：已存在的表跳过，缺的列自动补齐）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    from app.db import models  # noqa: F401 确保模型注册到 Base.metadata

    Base.metadata.create_all(engine)
    _add_missing_columns()
