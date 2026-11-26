"""Synthetic public browse data, exclusively in a fresh generated QA directory."""
import os
from pathlib import Path
import sys
root=Path(os.environ['TRAVEL_DATA_DIR'])
assert root.is_absolute() and root.parent.name.startswith('travel-browser-accounts-')
assert not (root/'app.db').exists()
assert not os.environ.get('AMAP_API_KEY') and not os.environ.get('ZHIPU_API_KEY')
sys.path.insert(0,str(Path.cwd()))
from app.db.database import init_db,SessionLocal
from app.db.models import Spot,User,Guide
from app.security import hash_password
init_db()
with SessionLocal() as db:
    author=User(username='qa_discovery_author',nickname='隔离作者',password_hash=hash_password('isolated-author-password'))
    db.add(author);db.flush()
    for i in range(1,15):
        db.add(Spot(name=f'隔离景点{i:02}',city='杭州' if i<=7 else '成都',district='合成区域' if i>1 else '',
            tags='合成自然' if i%2 else '合成历史',is_free=bool(i%2),amap_id=f'QA-DISC-{i}',source='合成验收'))
    for i in range(1,13):
        db.add(Guide(user_id=author.id,title=f'隔离攻略{i:02}',content='合成攻略正文，用于列表和键盘验收。',city='杭州' if i<=6 else '成都',views=100-i))
    db.commit()
import uvicorn
uvicorn.run('app.main:app',host='127.0.0.1',port=18963,log_level='warning')
