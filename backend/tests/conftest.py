"""pytest 共享夹具：每个测试独立内存库，认证接口测试不落盘。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_db
from app.main import app
from app.services import plan_jobs


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override

    # 后台任务落库走的是模块级 SESSION_FACTORY（请求的 db 早就关了，用不了），
    # 这里指到同一个内存库；否则测试会写进真实的 backend/data/app.db。
    _old_factory = plan_jobs.SESSION_FACTORY
    plan_jobs.SESSION_FACTORY = TestSession
    plan_jobs._jobs.clear()   # 任务表是进程级全局的，测试之间必须清干净

    try:
        with TestClient(app) as c:
            yield c, TestSession
    finally:
        plan_jobs._jobs.clear()
        plan_jobs.SESSION_FACTORY = _old_factory
        app.dependency_overrides.clear()
