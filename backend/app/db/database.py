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


def init_db():
    """建库建表（幂等：已存在的表跳过）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    from app.db import models  # noqa: F401 确保模型注册到 Base.metadata

    Base.metadata.create_all(engine)
