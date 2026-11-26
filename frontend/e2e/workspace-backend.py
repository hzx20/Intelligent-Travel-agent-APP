"""Real workspace APIs and jobs, with synthetic seed data and only model transport replaced."""
import asyncio
import json
import os
from pathlib import Path
import sys
root=Path(os.environ['TRAVEL_DATA_DIR'])
assert root.is_absolute() and root.parent.name.startswith('travel-browser-accounts-')
assert not (root/'app.db').exists()
assert not os.environ.get('AMAP_API_KEY') and not os.environ.get('ZHIPU_API_KEY')
sys.path.insert(0,str(Path.cwd()))
from app.db.database import init_db,SessionLocal
from app.db.models import Spot,Guide,User
from app.security import hash_password
from app.services import trip_ai
init_db()
with SessionLocal() as db:
    author=User(username='workspace_author',nickname='合成作者',password_hash=hash_password('isolated-author-password'))
    db.add(author);db.flush()
    db.add_all([Spot(name='合成湖边步道',city='杭州',district='合成区域',amap_id='QA-WORK-1',lng=120,lat=30,tags='合成自然',source='隔离验收'),
                Spot(name='合成城市公园',city='杭州',district='合成区域',amap_id='QA-WORK-2',lng=120.01,lat=30.01,tags='合成自然',source='隔离验收')])
    db.add(Guide(user_id=author.id,title='合成周末攻略',city='杭州',content='这是合成攻略正文，用于从详情进入旅行助手。不需要调用真实模型。'))
    db.commit()
async def synthetic_chat(messages,**kwargs):
    data=json.loads(messages[-1]['content']);snapshot=data['trip']
    await asyncio.sleep(1.5 if '等待' in data['request'] else 0.3)
    return json.dumps({'operations':[{'action':'add','day_id':snapshot['days'][0]['id'],
        'index':len(snapshot['days'][0]['items']),'item':{'name':'合成散步安排','kind':'自由活动','note':'隔离模型输出，用户确认后应用'}}]},ensure_ascii=False)
trip_ai.chat=synthetic_chat
import uvicorn
uvicorn.run('app.main:app',host='127.0.0.1',port=18963,log_level='warning')
