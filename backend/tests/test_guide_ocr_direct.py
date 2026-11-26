"""Exercise input, ownership and privacy boundaries without a network event loop."""
import base64
import json
from io import BytesIO
from PIL import Image
import pytest
import httpx
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.database import Base
from app.db.models import User
from app.db.trip_models import Trip, TripDay
from app.routers.trip_imports import image_text
from app.services import guide_ocr


class FakeRequest:
    def __init__(self, value): self.value = value
    async def stream(self): yield self.value


def sample_image(format='PNG', size=(32, 32)):
    output = BytesIO()
    Image.new('RGB', size, '#90b49b').save(output, format=format)
    return output.getvalue()


def finish(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    finally:
        coroutine.close()
    raise AssertionError('unexpected suspension in test')


def test_image_requires_owner_and_returns_only_text(monkeypatch):
    engine = create_engine('sqlite://'); Base.metadata.create_all(engine)
    calls = []
    async def fake_ocr(data, mime): calls.append((data, mime)); return '西湖'
    monkeypatch.setattr(guide_ocr, 'recognize', fake_ocr)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        user = User(username='ocruser', password_hash='x', nickname='x')
        other = User(username='ocrdifferent', password_hash='x', nickname='y')
        db.add_all([user, other]); db.flush()
        trip = Trip(user_id=user.id, title='旅行', days=[TripDay(day_number=1)])
        db.add(trip); db.commit()
        payload = {'version': 1, 'image': base64.b64encode(sample_image()).decode()}
        raw = json.dumps(payload).encode()
        with pytest.raises(HTTPException) as denied:
            finish(image_text(trip.id, FakeRequest(raw), Response(), db, other))
        assert denied.value.status_code == 404 and not calls
        result = finish(image_text(trip.id, FakeRequest(raw), Response(), db, user))
        assert result == {'version': 1, 'text': '西湖'} and calls[0][1] == 'image/png'
        bad = json.dumps({'version': 1, 'image': base64.b64encode(b'not an image').decode()}).encode()
        with pytest.raises(HTTPException) as invalid:
            finish(image_text(trip.id, FakeRequest(bad), Response(), db, user))
        assert invalid.value.status_code == 422 and len(calls) == 1
    engine.dispose()


def test_provider_error_is_safely_translated():
    response = httpx.Response(400, json={'message': '您的账户已欠费，请充值后重试。'})
    with pytest.raises(guide_ocr.OcrUnavailable, match='账户已欠费'):
        guide_ocr.parse_response(response)
    response = httpx.Response(400, json={'message': 'arbitrary provider text'})
    with pytest.raises(guide_ocr.OcrUnavailable) as error:
        guide_ocr.parse_response(response)
    assert 'arbitrary provider text' not in str(error.value)
    response = httpx.Response(200, json={'status': 'succeeded', 'words_result': [{'words': '西湖'}, {'words': '公园'}]})
    assert guide_ocr.parse_response(response) == '西湖\n公园'


def test_guest_image_ocr_validates_input_and_returns_only_text(client, monkeypatch):
    from app.routers import trip_imports
    c, _ = client
    trip_imports._guest_ocr_peers.clear()
    received = []
    async def fake_ocr(image, mime):
        received.append((image, mime))
        return '测试西湖\n湖边散步'
    monkeypatch.setattr(guide_ocr, 'recognize', fake_ocr)
    image = sample_image()
    body = {'version':3, 'image':base64.b64encode(image).decode()}
    assert c.post('/api/trips/guest-guide-image-text', json={**body, 'version':0}).status_code == 422
    assert c.post('/api/trips/guest-guide-image-text', json={**body, 'image':'***'}).status_code == 422
    assert c.post('/api/trips/guest-guide-image-text', json={**body, 'image':base64.b64encode(b'not-image').decode()}).status_code == 422
    oversized = b'\x89PNG\r\n\x1a\n' + b'x' * (4 * 1024 * 1024)
    assert c.post('/api/trips/guest-guide-image-text', json={**body, 'image':base64.b64encode(oversized).decode()}).status_code == 413
    assert c.post('/api/trips/guest-guide-image-text', content=b'x' * 5_600_001).status_code == 413
    response = c.post('/api/trips/guest-guide-image-text', json=body)
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    assert response.json() == {'version':3, 'text':'测试西湖\n湖边散步'}
    assert received == [(image, 'image/png')]
    assert c.post('/api/trips/guest-guide-image-text', json=body).status_code == 200
    assert c.post('/api/trips/guest-guide-image-text', json=body).status_code == 429
    trip_imports._guest_ocr_peers.clear()


def test_guest_image_ocr_failure_is_safe_and_capacity_recovers(client, monkeypatch):
    from app.routers import trip_imports
    c, _ = client
    trip_imports._guest_ocr_peers.clear()
    async def fake_ocr(image, mime): raise guide_ocr.OcrUnavailable('测试服务不可用')
    monkeypatch.setattr(guide_ocr, 'recognize', fake_ocr)
    body = {'version':1, 'image':base64.b64encode(sample_image('JPEG')).decode()}
    response = c.post('/api/trips/guest-guide-image-text', json=body)
    assert response.status_code == 502 and response.json()['detail'] == '测试服务不可用'
    assert trip_imports._guest_ocr_capacity.acquire(blocking=False)
    try: assert c.post('/api/trips/guest-guide-image-text', json=body).status_code == 429
    finally: trip_imports._guest_ocr_capacity.release()
    trip_imports._guest_ocr_peers.clear()


def test_guest_busy_ocr_does_not_read_another_large_body():
    from types import SimpleNamespace
    from app.routers import trip_imports
    class TrackedRequest:
        client = SimpleNamespace(host='test-peer')
        read = False
        async def stream(self):
            self.read = True
            yield b'not-used'
    request = TrackedRequest()
    assert trip_imports._guest_ocr_capacity.acquire(blocking=False)
    try:
        with pytest.raises(HTTPException) as error:
            finish(trip_imports.guest_image_text(request, Response()))
        assert error.value.status_code == 429 and not request.read
    finally:
        trip_imports._guest_ocr_capacity.release()


@pytest.mark.parametrize('image', [b'\x89PNG\r\n\x1a\nexample', b'\xff\xd8\xffimage', sample_image()[:40]])
def test_fake_or_truncated_images_never_reach_provider(client, monkeypatch, image):
    from app.routers import trip_imports
    c, _ = client
    calls = []
    async def fake_ocr(*args): calls.append(args); return '不得调用'
    monkeypatch.setattr(guide_ocr, 'recognize', fake_ocr)
    trip_imports._guest_ocr_peers.clear()
    result = c.post('/api/trips/guest-guide-image-text', json={'version': 1, 'image': base64.b64encode(image).decode()})
    assert result.status_code == 422 and not calls
    assert not trip_imports._guest_ocr_peers


def test_decoded_pixel_limit_and_jpeg_validation():
    guide_ocr.validate_image(sample_image('JPEG'), 'image/jpeg')
    with pytest.raises(ValueError):
        guide_ocr.validate_image(sample_image('PNG'), 'image/jpeg')
    with pytest.raises(ValueError, match='2000 万像素'):
        guide_ocr.validate_image(sample_image(size=(5000, 4001)), 'image/png')
