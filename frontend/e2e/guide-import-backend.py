"""Isolated real import APIs using synthetic provider responses."""
import asyncio
import json
import os
from pathlib import Path
import sys

root = Path(os.environ['TRAVEL_DATA_DIR'])
assert root.is_absolute() and root.parent.name.startswith('travel-browser-accounts-')
assert not (root / 'app.db').exists()
assert not any(os.environ.get(key) for key in ('ZHIPU_API_KEY', 'AMAP_API_KEY', 'AMAP_JS_KEY'))
sys.path.insert(0, str(Path.cwd()))
from app.db.database import init_db, SessionLocal
from app.db.models import Guide, Spot, User
from app.security import hash_password
from app.services import guide_ocr, trip_guide_import, wikivoyage
init_db()
with SessionLocal() as db:
    author = User(username='import_author', nickname='合成作者', password_hash=hash_password('isolated-author-password'))
    db.add(author)
    db.flush()
    db.add(Guide(user_id=author.id, title='合成公园攻略', city='杭州', content='合成湖边公园适合散步。合成街区适合闲逛。'))
    db.add(Spot(name='合成湖边公园', city='杭州', amap_id='QA-OCR-1', lng=120, lat=30, source='隔离验收'))
    db.commit()

async def synthetic_ocr(data, mime):
    # Actual request decoding, image validation and permission/version checks
    # still run in the router. No image bytes leave this test process.
    await asyncio.sleep(0.25)
    return '合成湖边公园适合散步。\n合成街区适合闲逛。'

async def synthetic_chat(messages, **kwargs):
    await asyncio.sleep(0.2)
    return json.dumps({'places': [{'name': '合成湖边公园', 'note': '适合散步'},
                                  {'name': '合成街区', 'note': '适合闲逛'}]}, ensure_ascii=False)

guide_ocr.recognize = synthetic_ocr
trip_guide_import.chat = synthetic_chat
wikivoyage.fetch_guide = lambda title: wikivoyage.PublicGuide(title, 123, ('合成湖边公园', '合成街区'))
from app.main import app
from app.routers import trip_imports

@app.post('/api/qa-guide-import/reset-limits')
def reset_test_counters():
    # Only this isolated fixture exposes QA control. Production limits and
    # endpoint responses are unchanged; each browser case gets a fresh window.
    with trip_imports._guest_ocr_lock:
        trip_imports._guest_ocr_peers.clear()
    with trip_imports._guest_preview_lock:
        trip_imports._guest_preview_peers.clear()
    with trip_imports._public_guide_lock:
        trip_imports._public_guide_peers.clear()
    return {'reset': True}

import uvicorn
uvicorn.run('app.main:app', host='127.0.0.1', port=18963, log_level='warning')
