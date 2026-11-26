"""模拟渠道的权限、数据校验、幂等与事务回滚。"""
from sqlalchemy.orm import Session as SqlSession

from app.db.models import ChannelInventory, User
from app.security import create_token

BASE = "/api/admin/channel-inventory"


def auth(session_factory, admin=True):
    with session_factory() as db:
        user = User(username="channel_admin" if admin else "channel_user", password_hash="unused",
                    is_admin=admin)
        db.add(user)
        db.commit()
        return {"Authorization": "Bearer " + create_token(user.id)}


def payload(request_id="batch-1"):
    return {"request_id": request_id, "items": [
        {"hotel_id": "h1", "hotel_name": "Demo Hotel", "room_id": "r1", "room_name": "Standard",
         "date": "2027-01-15", "available": 5, "price_minor": 12900, "currency": "USD"},
        {"hotel_id": "h1", "hotel_name": "Demo Hotel", "room_id": "r1", "room_name": "Standard",
         "date": "2027-01-16", "available": 3, "price_minor": 13500, "currency": "USD"},
    ]}


def test_sync_permissions_and_data_lifecycle(client):
    c, factory = client
    admin = auth(factory)
    normal = auth(factory, False)
    assert c.get(BASE + "/inventory").status_code == 401
    assert c.post(BASE + "/sync", headers=normal, json=payload()).status_code == 403
    first = c.post(BASE + "/sync", headers=admin, json=payload())
    assert first.status_code == 200 and first.json()["replayed"] is False
    replay = c.post(BASE + "/sync", headers=admin, json=payload())
    assert replay.json()["replayed"] is True
    changed = payload()
    changed["items"][0]["available"] = 1
    assert c.post(BASE + "/sync", headers=admin, json=changed).status_code == 409
    changed["request_id"] = "batch-2"
    assert c.post(BASE + "/sync", headers=admin, json=changed).status_code == 200
    inventory = c.get(BASE + "/inventory", headers=admin, params={"stay_date": "2027-01-15"}).json()
    assert len(inventory["items"]) == 1 and inventory["items"][0]["available"] == 1
    assert inventory["items"][0]["price_minor"] == 12900
    with factory() as db:
        assert db.query(ChannelInventory).count() == 2
    assert len(c.get(BASE + "/sync-runs", headers=admin).json()["items"]) == 2


def test_invalid_batch_does_not_write(client):
    c, factory = client
    admin = auth(factory)
    bad = payload()
    bad["items"][1]["available"] = -1
    assert c.post(BASE + "/sync", headers=admin, json=bad).status_code == 422
    duplicate = payload()
    duplicate["items"][1] = duplicate["items"][0].copy()
    assert c.post(BASE + "/sync", headers=admin, json=duplicate).status_code == 422
    assert c.get(BASE + "/inventory", headers=admin).json()["items"] == []
    assert c.get(BASE + "/sync-runs", headers=admin).json()["items"] == []


def test_failure_rolls_back_and_same_request_can_retry(client, monkeypatch):
    c, factory = client
    admin = auth(factory)
    original = SqlSession.commit
    failed = False

    def fail_once(self):
        nonlocal failed
        if not failed and any(isinstance(x, ChannelInventory) for x in self.new):
            failed = True
            self.flush()  # 模拟库存已写入事务、提交前发生故障
            raise RuntimeError("injected storage failure")
        return original(self)

    monkeypatch.setattr(SqlSession, "commit", fail_once)
    assert c.post(BASE + "/sync", headers=admin, json=payload()).status_code == 503
    assert c.get(BASE + "/inventory", headers=admin).json()["items"] == []
    runs = c.get(BASE + "/sync-runs", headers=admin).json()["items"]
    assert runs[0]["status"] == "failed" and runs[0]["attempts"] == 1
    retried = c.post(BASE + "/sync", headers=admin, json=payload()).json()
    assert retried["status"] == "success" and retried["attempts"] == 2
    assert len(c.get(BASE + "/inventory", headers=admin).json()["items"]) == 2
