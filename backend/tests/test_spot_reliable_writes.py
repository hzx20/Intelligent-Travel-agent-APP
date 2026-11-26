from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from app.db.models import Comment, CommentCreationRequest, Favorite, SpotFavoriteState, Spot, User
from test_trips import account

def setup(client):
    c,sessions=client;auth=account(c,'spot_reliable')
    with sessions() as db:
        a,b=Spot(name='景点A',city='杭州',amap_id='reliable-A'),Spot(name='景点B',city='杭州',amap_id='reliable-B')
        db.add_all([a,b]);db.commit();return c,sessions,auth,f'/api/spots/{a.id}',f'/api/spots/{b.id}'

def test_comment_creation_replays_original_and_rejects_changed_payload_or_spot(client):
    c,sessions,auth,path,other=setup(client);body={'request_id':str(uuid4()),'content':' 可靠评论 ','rating':4}
    assert c.get('/api/spots/write-capabilities').status_code==401
    assert c.get('/api/spots/write-capabilities',headers=auth).json()['idempotent_comments'] is True
    assert c.post(path+'/comments/create',json=body).status_code==401
    assert c.post(path+'/comments/create',headers=auth,json={'content':'缺标识'}).status_code==422
    first=c.post(path+'/comments/create',headers=auth,json=body)
    second=c.post(path+'/comments/create',headers=auth,json={**body,'content':'可靠评论'})
    assert first.status_code==201 and second.status_code==200 and first.json()==second.json()
    assert first.json()['request_id']==body['request_id'] and first.headers['cache-control']=='no-store'
    assert c.post(path+'/comments/create',headers=auth,json={**body,'rating':5}).status_code==409
    assert c.post(other+'/comments/create',headers=auth,json=body).status_code==409
    with sessions() as db:assert db.query(Comment).count()==db.query(CommentCreationRequest).count()==1

def test_concurrent_comment_is_once_and_deleted_comment_never_republishes(client):
    c,sessions,auth,path,_=setup(client);body={'request_id':str(uuid4()),'content':'并发评论'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:c.post(path+'/comments/create',headers=auth,json=body),range(2)))
    assert sorted(r.status_code for r in responses)==[200,201] and responses[0].json()==responses[1].json()
    cid=responses[0].json()['id']
    with sessions() as db:
        row=db.get(Comment,cid);row.is_deleted=True;db.commit()
    assert c.post(path+'/comments/create',headers=auth,json=body).status_code==410
    with sessions() as db:
        db.delete(db.get(Comment,cid));db.commit();assert db.query(CommentCreationRequest).one().comment_id is None
    replacement=c.post(path+'/comments',headers=auth,json={'content':'后来的另一条'})
    assert replacement.status_code==201
    assert c.post(path+'/comments/create',headers=auth,json=body).status_code==410
    with sessions() as db:assert db.query(Comment).one().content=='后来的另一条'

def test_comment_keys_are_account_scoped_and_legacy_creation_is_explicit(client):
    c,sessions,auth,path,_=setup(client);other=account(c,'other_comment');body={'request_id':str(uuid4()),'content':'同标识不同账号'}
    a=c.post(path+'/comments/create',headers=auth,json=body);b=c.post(path+'/comments/create',headers=other,json=body)
    assert a.status_code==b.status_code==201 and a.json()['id']!=b.json()['id']
    for _ in range(2):assert c.post(path+'/comments',headers=auth,json={'content':'旧调用'}).status_code==201
    with sessions() as db:assert db.query(Comment).count()==4 and db.query(CommentCreationRequest).count()==2

def test_favorite_desired_state_replays_without_toggling_and_rejects_old_opposite(client):
    c,sessions,auth,path,_=setup(client)
    assert c.post(path+'/favorite',headers=auth).status_code==428
    first=c.put(path+'/favorite-state',headers=auth,json={'favorited':True,'version':0})
    repeated=c.put(path+'/favorite-state',headers=auth,json={'favorited':True,'version':0})
    assert first.status_code==repeated.status_code==200 and first.json()==repeated.json()
    assert first.json()['favorite_version']==1 and first.json()['favorite_count']==1
    assert c.put(path+'/favorite-state',headers=auth,json={'favorited':False,'version':1}).status_code==200
    assert c.put(path+'/favorite-state',headers=auth,json={'favorited':True,'version':0}).status_code==409
    assert c.put(path+'/favorite-state',headers=auth,json={'favorited':False,'version':999}).status_code==409
    current=c.get(path+'/favorite-state',headers=auth)
    assert current.headers['cache-control']=='no-store' and current.json()=={'favorited':False,'favorite_count':0,'favorite_version':2}
    assert c.get(path,headers=auth).json()['favorite_version']==2
    with sessions() as db:assert db.query(Favorite).count()==0 and db.query(SpotFavoriteState).one().version==2

def test_concurrent_same_favorite_intent_creates_one_row_and_increments_once(client):
    c,sessions,auth,path,_=setup(client)
    for desired,version in [(True,0),(False,1)]:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda _:c.put(path+'/favorite-state',headers=auth,json={'favorited':desired,'version':version}),range(2)))
        assert all(r.status_code==200 for r in responses)
        assert all(r.json()['favorite_version']==version+1 for r in responses)
    with sessions() as db:assert db.query(Favorite).count()==0 and db.query(SpotFavoriteState).count()==1

def test_legacy_favorite_is_adopted_and_account_states_are_separate(client):
    c,sessions,auth,path,_=setup(client);other=account(c,'other_favorite')
    with sessions() as db:
        db.add(Favorite(user_id=db.query(User).filter_by(username='spot_reliable').one().id,spot_id=int(path.split('/')[-1])));db.commit()
    before=c.get(path+'/favorite-state',headers=auth)
    assert before.json()=={'favorited':True,'favorite_count':1,'favorite_version':0}
    assert c.put(path+'/favorite-state',headers=auth,json={'favorited':False,'version':0}).json()['favorite_version']==1
    assert c.get(path+'/favorite-state',headers=other).json()['favorite_version']==0
    assert c.put(path+'/favorite-state',json={'favorited':True,'version':0}).status_code==401
    assert c.post(path+'/favorite',headers=auth,json={'favorited':True,'version':1}).json()['favorite_version']==2
    assert len(c.get('/api/me/favorites',headers=auth).json())==1
