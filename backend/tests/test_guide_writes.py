from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from app.db.models import Guide, GuideCreationRequest, GuideImage, Spot
from app.routers import guides
from test_trips import account


def body(**changes):
    return {'title':'可靠攻略','content':'正文','city':'杭州','images':[' http://img/a.jpg '],**changes}


def test_creation_retry_returns_current_metadata_and_never_overwrites_later_edit(client):
    c,sessions=client;auth=account(c,'guide_once')
    data=body(request_id=str(uuid4()))
    first=c.post('/api/guides/create',headers=auth,json=data)
    assert first.status_code==201 and first.json()['version']==1
    gid=first.json()['id']
    edited=c.patch(f'/api/guides/{gid}/content',headers=auth,json=body(title='后续编辑',version=1,revision=first.json()['revision'],is_draft=True))
    assert edited.status_code==200 and edited.json()['version']==2
    replay=c.post('/api/guides/create',headers=auth,json={**data,'title':' 可靠攻略 ','images':['http://img/a.jpg']})
    assert replay.status_code==200 and replay.json()==edited.json()
    assert replay.headers['cache-control']=='no-store'
    assert c.get(f'/api/guides/{gid}',headers=auth).json()['title']=='后续编辑'
    assert c.post('/api/guides/create',headers=auth,json={**data,'content':'不同正文'}).status_code==409
    with sessions() as db:
        assert db.query(Guide).count()==1 and db.query(GuideCreationRequest).count()==1
        assert db.query(GuideImage).count()==1


def test_concurrent_creation_has_one_guide_and_no_orphan_images(client):
    c,sessions=client;auth=account(c,'guide_race');data=body(request_id=str(uuid4()))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:c.post('/api/guides/create',headers=auth,json=data),range(2)))
    assert sorted(r.status_code for r in results)==[200,201] and results[0].json()==results[1].json()
    with sessions() as db:
        assert db.query(Guide).count()==db.query(GuideImage).count()==db.query(GuideCreationRequest).count()==1


def test_creation_keys_are_account_scoped_required_on_dedicated_route_and_legacy_stays_explicit(client):
    c,sessions=client;a,b=account(c,'guide_a'),account(c,'guide_b');data=body(request_id=str(uuid4()))
    assert c.get('/api/guides/write-capabilities').status_code==401
    caps=c.get('/api/guides/write-capabilities',headers=a)
    assert caps.status_code==200 and caps.json()=={'idempotent_creation':True,'versioned_writes':True}
    assert c.post('/api/guides/create',json=data).status_code==401
    assert c.post('/api/guides/create',headers=a,json=body()).status_code==422
    assert c.post('/api/guides/create',headers=a,json={**data,'request_id':'bad'}).status_code==422
    first=c.post('/api/guides/create',headers=a,json=data)
    second=c.post('/api/guides/create',headers=b,json=data)
    assert first.status_code==second.status_code==201 and first.json()['id']!=second.json()['id']
    for _ in range(2):assert c.post('/api/guides',headers=a,json=body()).status_code==201
    with sessions() as db:assert db.query(Guide).count()==4 and db.query(GuideCreationRequest).count()==2


def test_deleted_receipt_does_not_recreate_or_return_a_reused_id(client):
    c,sessions=client;auth=account(c,'guide_deleted');data=body(request_id=str(uuid4()))
    created=c.post('/api/guides/create',headers=auth,json=data).json();gid=created['id'];revision=created['revision']
    assert c.delete(f'/api/guides/{gid}/content?version=1&revision={revision}',headers=auth).status_code==204
    with sessions() as db:assert db.query(GuideCreationRequest).one().guide_id is None
    replacement=c.post('/api/guides',headers=auth,json=body(title='后来攻略'))
    assert replacement.status_code==201 and replacement.json()['id']==gid  # SQLite reuses this id.
    assert c.patch(f'/api/guides/{gid}/content',headers=auth,json=body(version=1,revision=revision)).status_code==409
    assert c.delete(f'/api/guides/{gid}/content?version=1&revision={revision}',headers=auth).status_code==409
    assert c.post('/api/guides/create',headers=auth,json=data).status_code==410
    with sessions() as db:assert db.query(Guide).one().title=='后来攻略'


def test_concurrent_versioned_edits_are_atomic_and_stale_deletion_is_rejected(client):
    c,sessions=client;auth=account(c,'guide_versions');other=account(c,'guide_outsider')
    created=c.post('/api/guides',headers=auth,json=body()).json();gid=created['id'];revision=created['revision'];path=f'/api/guides/{gid}'
    assert c.patch(path,headers=auth,json=body()).status_code==428
    assert c.patch(path+'/content',headers=auth,json=body()).status_code==422
    assert c.patch(path+'/content',headers=other,json=body(version=1,revision=revision)).status_code==403
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda n:c.patch(path+'/content',headers=auth,json=body(title=f'并发{n}',version=1,revision=revision,images=[f'http://img/{n}.jpg'])),range(2)))
    assert sorted(r.status_code for r in results)==[200,409]
    winner=next(i for i,r in enumerate(results) if r.status_code==200)
    loaded=c.get(path,headers=auth).json()
    assert loaded['version']==2 and loaded['title']==f'并发{winner}' and loaded['images']==[f'http://img/{winner}.jpg']
    assert c.delete(path,headers=auth).status_code==428
    assert c.delete(path+f'/content?version=1&revision={revision}',headers=auth).status_code==409
    assert c.get(path).json()['version']==2  # Views do not invalidate content versions.
    assert c.patch(path,headers=auth,json=body(version=1,revision=revision)).status_code==409
    assert c.delete(path+f'/content?version=2&revision={revision}',headers=auth).status_code==204
    with sessions() as db:assert db.query(GuideImage).count()==0


def test_failed_child_write_rolls_back_version_content_and_images(client,monkeypatch):
    c,sessions=client;auth=account(c,'guide_atomic')
    created=c.post('/api/guides',headers=auth,json=body()).json();gid=created['id']
    def fail(*args):raise RuntimeError('isolated child failure')
    monkeypatch.setattr(guides,'_save_spots',fail)
    with pytest.raises(RuntimeError,match='isolated child failure'):
        c.patch(f'/api/guides/{gid}/content',headers=auth,json=body(version=1,revision=created['revision'],title='不能保留',images=['http://img/failed.jpg']))
    with sessions() as db:
        saved=db.get(Guide,gid);assert saved.version==1 and saved.title=='可靠攻略'
        assert db.query(GuideImage).one().image_url=='http://img/a.jpg'
