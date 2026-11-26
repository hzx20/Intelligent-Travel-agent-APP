"""Local photo QA with real routes and an ephemeral database; never production."""
import os
from pathlib import Path
import secrets
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['JWT_SECRET'] = secrets.token_urlsafe(48)
os.environ['ZHIPU_API_KEY'] = ''
os.environ['AMAP_API_KEY'] = ''

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db.database import Base, get_db
from app.db import models, trip_models, trip_expense_models, guest_import_models, sharing_models, footprint_models, reservation_models
from app.routers.trip_footprints import router
from app.routers.trips import _serialize, _trip
from app.security import create_token

engine = create_engine('sqlite://', connect_args={'check_same_thread':False}, poolclass=StaticPool)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
app = FastAPI(title='Isolated photo QA')
app.include_router(router)
def qa_db():
    with Session() as db: yield db
app.dependency_overrides[get_db] = qa_db
with Session() as db:
    user = models.User(username='photo_qa',password_hash='unused',nickname='隔离测试账号')
    db.add(user); db.flush()
    trip = trip_models.Trip(user_id=user.id,title='照片端到端验收',destination='测试城市')
    db.add(trip); db.flush()
    day = trip_models.TripDay(trip_id=trip.id,day_number=1)
    db.add(day); db.flush()
    item = trip_models.TripItem(day_id=day.id,sort_order=0,name='测试地点')
    db.add(item); db.flush()
    db.add(footprint_models.TripFootprint(user_id=user.id,trip_id=trip.id,item_id=item.id,day_number=1,place_name=item.name,city='测试城市'))
    db.commit()

@app.get('/api/qa-bootstrap')
def bootstrap():
    with Session() as db:
        user = db.query(models.User).one()
        return {'token':create_token(user.id),'user':{'id':user.id},'trip':_serialize(_trip(db,user,1))}

@app.get('/api/qa-status')
def photo_status():
    from io import BytesIO
    from PIL import Image
    with Session() as db:
        photos = db.query(footprint_models.FootprintPhoto).all()
        details = []
        for photo in photos:
            with Image.open(BytesIO(photo.data)) as image:
                details.append({'format':image.format,'size':list(image.size),'exif_entries':len(image.getexif())})
        return {'photos':details,'trip_version':db.get(trip_models.Trip,1).version}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=18962,log_level='warning')
