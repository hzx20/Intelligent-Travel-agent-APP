"""Each test uses a temporary SQLite file with independent connection transactions."""
import sys
import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

# app.main initializes its database at import time, before client overrides run.
# Keep that bootstrap (including a fallback JWT secret) in a temporary directory.
_previous_data_dir = os.environ.get('TRAVEL_DATA_DIR')
_bootstrap_dir = tempfile.TemporaryDirectory(prefix='travel-pytest-bootstrap-')
os.environ['TRAVEL_DATA_DIR'] = _bootstrap_dir.name

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base, get_db
from app.main import app
from app.services import plan_jobs
from app.routers import trips


def pytest_unconfigure(config):
    from app.db.database import engine
    engine.dispose()
    _bootstrap_dir.cleanup()
    if _previous_data_dir is None:
        os.environ.pop('TRAVEL_DATA_DIR', None)
    else:
        os.environ['TRAVEL_DATA_DIR'] = _previous_data_dir


@pytest.fixture()
def client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test-app.db'}", connect_args={"check_same_thread": False}
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
    # 这里指到同一个临时库；否则测试会写进真实的 backend/data/app.db。
    # 文件库允许请求与后台任务使用独立连接，避免 StaticPool 共用连接时
    # 一个 Session 的 rollback 撤销另一个 Session 尚未提交的写入。
    _old_factory = plan_jobs.SESSION_FACTORY
    old_ai_factory = trips.AI_SESSION_FACTORY
    trips.AI_SESSION_FACTORY = TestSession
    plan_jobs.SESSION_FACTORY = TestSession
    plan_jobs._jobs.clear()   # Only local executor handles; job data is in this test DB.

    try:
        with TestClient(app) as c:
            yield c, TestSession
    finally:
        plan_jobs._jobs.clear()
        plan_jobs.SESSION_FACTORY = _old_factory
        trips.AI_SESSION_FACTORY = old_ai_factory
        app.dependency_overrides.clear()
        engine.dispose()
