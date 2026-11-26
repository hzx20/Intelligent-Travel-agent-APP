from concurrent.futures import ThreadPoolExecutor
from app.db.models import User
from test_trips import account


def test_profile_writes_require_original_version_and_keep_unrelated_fields(client):
    c,sessions=client;auth=account(c,'profile_owner')
    original=c.get('/api/me/profile',headers=auth)
    assert original.status_code==200 and original.json()['version']==1 and original.headers['cache-control']=='no-store'
    assert c.patch('/api/me/profile',headers=auth,json={'nickname':'旧客户端'}).status_code==428
    assert c.patch('/api/me/profile/edit',headers=auth,json={'nickname':'缺版本'}).status_code==422
    first=c.patch('/api/me/profile/edit',headers=auth,json={'nickname':' 新昵称 ','avatar':'/avatar.png','version':1})
    assert first.status_code==200 and first.json()['version']==2 and first.json()['nickname']=='新昵称'
    assert first.headers['cache-control']=='no-store'
    assert c.patch('/api/me/profile/edit',headers=auth,json={'nickname':'重复保存','version':1}).status_code==409
    second=c.patch('/api/me/profile',headers=auth,json={'nickname':'再次编辑','version':2})
    assert second.status_code==200 and second.json()['version']==3 and second.json()['avatar']=='/avatar.png'
    assert c.get('/api/auth/me',headers=auth).json()['profile_version']==3
    for path in ['/api/me/favorites','/api/me/guides']:
        assert c.get(path,headers=auth).headers['cache-control']=='no-store'
    with sessions() as db:
        saved=db.query(User).one();assert saved.nickname=='再次编辑' and saved.profile_version==3


def test_concurrent_profile_updates_have_one_winner_without_mixed_fields(client):
    c,sessions=client;auth=account(c,'profile_concurrent')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda n:c.patch('/api/me/profile/edit',headers=auth,
                         json={'nickname':f'并发{n}','avatar':f'/{n}.png','version':1}),range(2)))
    assert sorted(r.status_code for r in results)==[200,409]
    winner=next(i for i,r in enumerate(results) if r.status_code==200)
    with sessions() as db:
        saved=db.query(User).one();assert saved.nickname==f'并发{winner}' and saved.avatar==f'/{winner}.png' and saved.profile_version==2


def test_profile_is_account_scoped_and_invalid_fields_do_not_claim_version(client):
    c,_=client;first=account(c,'profile_first');second=account(c,'profile_second')
    assert c.patch('/api/me/profile/edit',json={'nickname':'未登录','version':1}).status_code==401
    for data in [{'nickname':'  ','version':1},{'nickname':'x'*51,'version':1},{'avatar':'x'*501,'version':1},{'nickname':'名字','version':0}]:
        assert c.patch('/api/me/profile/edit',headers=first,json=data).status_code==422
    assert c.get('/api/me/profile',headers=first).json()['version']==1
    assert c.patch('/api/me/profile/edit',headers=second,json={'nickname':'另一个账号','version':1}).status_code==200
    assert c.get('/api/me/profile',headers=first).json()['version']==1
