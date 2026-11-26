from app.db.models import Spot
from test_trips import account


def test_regions_include_city_without_district_and_accurate_total(client):
    c,sessions=client
    with sessions() as db:
        db.add_all([Spot(name='有区域',city='杭州',district='西湖区',amap_id='DISC-1'),Spot(name='无区域',city='杭州',district='',amap_id='DISC-2'),Spot(name='无城市',city='',district='',amap_id='DISC-3')]);db.commit()
    response=c.get('/api/spots/regions');assert response.status_code==200
    body=response.json();assert body['total']==3
    assert body['cities']==[{'city':'杭州','count':2,'districts':[{'name':'西湖区','count':1}]}]


def test_personal_recommendations_disable_caching_and_keep_guest_path(client):
    c,sessions=client;a=account(c,'discovery_private')
    with sessions() as db:db.add(Spot(name='合成景点',city='杭州',amap_id='DISC-PRIVATE'));db.commit()
    r=c.get('/api/spots/for-you',headers=a)
    assert r.status_code==200 and r.headers['cache-control']=='private, no-store'
    assert c.get('/api/spots/for-you').status_code==401
    assert c.get('/api/spots/for-you-guest').json()['total']==1
