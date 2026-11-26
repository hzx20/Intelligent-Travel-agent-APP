from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib

from app.db.sharing_models import TripShareLink
from app.routers import trip_sharing as sharing
from app.security import decode_token
from test_trips import account


def setup(c):
    owner=account(c,'prepared_owner')
    trip=c.post('/api/trips',headers=owner,json={'title':'可靠分享'}).json()
    return owner,f"/api/trips/{trip['id']}/sharing"


def test_prepare_has_no_grant_and_replay_preserves_one_hash_only_link(client):
    c,sessions=client
    owner,path=setup(c)
    response=c.post(path+'/links/prepare',headers=owner,json={'mode':'view'})
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    proof=response.json()['preparation']
    assert decode_token(proof) is None
    claims=sharing._prepared_link(proof)
    assert response.json()['token']==claims.token
    assert c.post('/api/trip-sharing/view',json={'token':claims.token}).status_code==404
    with sessions() as db: assert db.query(TripShareLink).count()==0
    first=c.post(path+'/links/activate',headers=owner,json={'preparation':proof})
    second=c.post(path+'/links/activate',headers=owner,json={'preparation':proof})
    assert first.status_code==201 and second.status_code==200 and first.json()==second.json()
    assert second.headers['cache-control']=='no-store'
    assert c.post('/api/trip-sharing/view',json={'token':first.json()['token']}).status_code==200
    with sessions() as db:
        row=db.query(TripShareLink).one()
        assert row.token_hash==hashlib.sha256(first.json()['token'].encode()).hexdigest()
        assert proof not in str(row.__dict__) and first.json()['token'] not in str(row.__dict__)
    info=str(c.get(path,headers=owner).json())
    assert proof not in info and first.json()['token'] not in info


def test_preparation_cannot_cross_account_trip_or_be_tampered(client):
    c,_=client
    owner,path=setup(c)
    stranger=account(c,'prepared_stranger')
    other=c.post('/api/trips',headers=owner,json={'title':'另一旅行'}).json()
    other_path=f"/api/trips/{other['id']}/sharing"
    assert c.post(path+'/links/prepare',headers=stranger,json={'mode':'edit'}).status_code==404
    assert c.post(path+'/links/prepare',json={'mode':'view'}).status_code==401
    proof=c.post(path+'/links/prepare',headers=owner,json={'mode':'edit'}).json()['preparation']
    assert c.post(path+'/links/activate',headers=stranger,json={'preparation':proof}).status_code==404
    assert c.post(other_path+'/links/activate',headers=owner,json={'preparation':proof}).status_code==404
    parts=proof.split('.')
    changed=parts[0]+'.'+parts[1]+'.'+('A' if parts[2][0]!='A' else 'B')+parts[2][1:]
    assert c.post(path+'/links/activate',headers=owner,json={'preparation':changed}).status_code==422
    login_token=owner['Authorization'].split(' ',1)[1]
    assert c.post(path+'/links/activate',headers=owner,json={'preparation':login_token}).status_code==422


def test_expired_unused_preparation_fails_but_created_result_can_be_recovered(client,monkeypatch):
    c,sessions=client
    owner,path=setup(c)
    now=sharing._now()
    unused=c.post(path+'/links/prepare',headers=owner,json={'mode':'view'}).json()['preparation']
    used=c.post(path+'/links/prepare',headers=owner,json={'mode':'edit','expires_days':1}).json()['preparation']
    first=c.post(path+'/links/activate',headers=owner,json={'preparation':used}).json()
    monkeypatch.setattr(sharing,'_now',lambda:now+timedelta(minutes=16))
    assert c.post(path+'/links/activate',headers=owner,json={'preparation':unused}).status_code==410
    recovered=c.post(path+'/links/activate',headers=owner,json={'preparation':used})
    assert recovered.status_code==200 and recovered.json()==first
    monkeypatch.setattr(sharing,'_now',lambda:now+timedelta(days=2))
    assert c.post(path+'/links/activate',headers=owner,json={'preparation':used}).status_code==409
    with sessions() as db: assert db.query(TripShareLink).count()==1


def test_concurrent_activation_and_revocation_never_recreate_link(client):
    c,sessions=client
    owner,path=setup(c)
    proof=c.post(path+'/links/prepare',headers=owner,json={'mode':'view'}).json()['preparation']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:c.post(path+'/links/activate',headers=owner,json={'preparation':proof}),range(2)))
    assert sorted(result.status_code for result in results)==[200,201]
    assert results[0].json()['id']==results[1].json()['id']
    link=results[0].json()
    assert c.delete(path+f"/links/{link['id']}",headers=owner).status_code==204
    assert c.post(path+'/links/activate',headers=owner,json={'preparation':proof}).status_code==409
    with sessions() as db:
        assert db.query(TripShareLink).count()==1
        assert db.query(TripShareLink).one().revoked_at is not None
