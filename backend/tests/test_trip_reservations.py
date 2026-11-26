from test_trips import account


def test_reservation_lifecycle_permissions_and_detached_item(client):
    c, _ = client
    owner, other = account(c, 'reservation_owner'), account(c, 'reservation_other')
    trip = c.post('/api/trips', headers=owner, json={'title': '旅行', 'days': 1}).json()
    tid, day = trip['id'], trip['days'][0]['id']
    trip = c.post(f'/api/trips/{tid}/days/{day}/items', headers=owner, json={'name':'酒店地点', 'version':trip['version']}).json()
    item = trip['days'][0]['items'][0]['id']
    path = f'/api/trips/{tid}/reservations'
    entry = {'version':trip['version'], 'title':'酒店', 'kind':'住宿', 'status':'已预订', 'item_id':item, 'reference':'secret-order', 'start_date':'2026-12-01', 'end_date':'2026-12-02'}
    assert c.get(path, headers=other).status_code == 404
    assert c.post(path, headers=other, json=entry).status_code == 404
    assert c.post(path, headers=owner, json={**entry, 'item_id':999999}).status_code == 404
    assert c.post(path, headers=owner, json={**entry, 'end_date':'2026-11-01'}).status_code == 422
    response = c.post(path, headers=owner, json=entry)
    assert response.status_code == 201, response.text
    trip = response.json()
    assert c.post(path, headers=owner, json=entry).status_code == 409
    response = c.get(path, headers=owner)
    assert response.headers['cache-control'] == 'no-store'
    row = response.json()['items'][0]
    assert row['item_name'] == '酒店地点' and row['reference'] == 'secret-order'
    rid = row['id']
    trip = c.post(f'/api/trips/{tid}/items/{item}/delete', headers=owner, json={'version':trip['version']}).json()
    row = c.get(path, headers=owner).json()['items'][0]
    assert row['item_id'] is None and row['item_name'] == '酒店地点'
    response = c.patch(f'{path}/{rid}', headers=owner, json={**entry, 'version':trip['version'], 'item_id':None, 'title':'更新'})
    assert response.status_code == 200, response.text
    assert c.get(path, headers=owner).json()['items'][0]['item_name'] == '酒店地点'
    assert c.post(f'{path}/{rid}/delete', headers=owner, json={'version':response.json()['version']}).status_code == 200
    assert c.get(path, headers=owner).json()['items'] == []


def test_editor_access_and_read_only_share_excludes_reservations(client):
    c, _ = client
    owner, editor = account(c, 'reservation_share_owner'), account(c, 'reservation_share_editor')
    trip = c.post('/api/trips', headers=owner, json={'title':'旅行', 'days':1}).json()
    tid = trip['id']; path = f'/api/trips/{tid}/reservations'; sharing = f'/api/trips/{tid}/sharing'
    trip = c.post(path, headers=owner, json={'title':'酒店', 'reference':'private-reference', 'version':trip['version']}).json()
    view = c.post(f'{sharing}/links', headers=owner, json={'mode':'view'}).json()
    public = c.post('/api/trip-sharing/view', json={'token':view['token']}).json()
    assert 'private-reference' not in str(public) and 'reservations' not in public
    invite = c.post(f'{sharing}/links', headers=owner, json={'mode':'edit'}).json()
    assert c.post('/api/trip-sharing/accept', headers=editor, json={'token':invite['token']}).status_code == 200
    record = c.get(path, headers=editor).json()['items'][0]
    assert record['reference'] == 'private-reference'
    current = c.get(f'/api/trips/{tid}', headers=editor).json()
    assert c.patch(f"{path}/{record['id']}", headers=editor, json={'title':'同行更新', 'version':current['version']}).status_code == 200
    member = c.get(sharing, headers=owner).json()['members'][0]['user_id']
    assert c.delete(f'{sharing}/members/{member}', headers=owner).status_code == 204
    assert c.get(path, headers=editor).status_code == 404
