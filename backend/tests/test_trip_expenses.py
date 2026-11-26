"""Expense amounts remain exact and separate by currency, with trip authorization."""
from test_trips import account


def test_expense_lifecycle_and_currency_totals(client):
    c, _ = client
    owner, other = account(c, 'expense_owner'), account(c, 'expense_other')
    trip = c.post('/api/trips', headers=owner, json={'title': '旅行', 'days': 1}).json()
    day = trip['days'][0]['id']
    trip = c.post(f"/api/trips/{trip['id']}/days/{day}/items", headers=owner,
                  json={'name': '西湖', 'version': trip['version']}).json()
    item_id = trip['days'][0]['items'][0]['id']
    path = f"/api/trips/{trip['id']}/expenses"
    entry = {'version': trip['version'], 'amount_minor': 1250, 'currency': 'CNY', 'category': '门票',
             'day_id': day, 'item_id': item_id, 'spent_on': '2026-11-08', 'note': '门票'}
    assert c.get(path, headers=other).status_code == 404
    assert c.post(path, headers=other, json=entry).status_code == 404
    assert c.post(path, headers=owner, json={**entry, 'item_id': 9999}).status_code == 404
    assert c.post(path, headers=owner, json={**entry, 'amount_minor': 0}).status_code == 422
    added = c.post(path, headers=owner, json=entry)
    assert added.status_code == 201
    trip = added.json()
    assert c.post(path, headers=owner, json=entry).status_code == 409
    trip = c.post(path, headers=owner, json={'version': trip['version'], 'amount_minor': 900, 'currency': 'JPY'}).json()
    ledger = c.get(path, headers=owner).json()
    assert ledger['totals'] == {'CNY': 1250, 'JPY': 900}
    cny = next(item for item in ledger['items'] if item['currency'] == 'CNY')
    assert cny['item_name'] == '西湖' and cny['note'] == '门票'
    updated = c.patch(f"{path}/{cny['id']}", headers=owner,
                      json={**entry, 'version': trip['version'], 'amount_minor': 1300}).json()
    assert c.get(path, headers=owner).json()['totals']['CNY'] == 1300
    # Deleting an itinerary item must not erase the expense or its useful label.
    updated = c.post(f"/api/trips/{trip['id']}/items/{item_id}/delete", headers=owner,
                     json={'version': updated['version']}).json()
    ledger = c.get(path, headers=owner).json()
    retained = next(item for item in ledger['items'] if item['currency'] == 'CNY')
    assert retained['item_name'] == '西湖' and retained['item_id'] is None
    updated = c.patch(f"{path}/{cny['id']}", headers=owner,
                      json={**entry, 'version': updated['version'], 'item_id': None, 'note': '更新备注'}).json()
    assert next(item for item in c.get(path, headers=owner).json()['items'] if item['currency'] == 'CNY')['item_name'] == '西湖'
    assert c.post(f"{path}/{cny['id']}/delete", headers=other, json={'version': updated['version']}).status_code == 404
    deleted = c.post(f"{path}/{cny['id']}/delete", headers=owner, json={'version': updated['version']})
    assert deleted.status_code == 200
    assert c.get(path, headers=owner).json()['totals'] == {'JPY': 900}


def test_moving_item_keeps_expense_association_and_actual_date(client, monkeypatch):
    import json
    from app.db.trip_expense_models import TripExpense
    from app.services import trip_ai
    c, sessions = client
    owner = account(c, 'expense_move_owner')
    trip = c.post('/api/trips', headers=owner, json={'title':'旅行', 'days':2}).json()
    tid, first, second = trip['id'], trip['days'][0]['id'], trip['days'][1]['id']
    trip = c.post(f'/api/trips/{tid}/days/{first}/items', headers=owner, json={'version':trip['version'], 'name':'公园'}).json()
    item = trip['days'][0]['items'][0]['id']; path = f'/api/trips/{tid}/expenses'
    entry = {'version':trip['version'], 'amount_minor':3000, 'day_id':first, 'item_id':item, 'spent_on':'2026-11-08'}
    trip = c.post(path, headers=owner, json=entry).json()
    record = c.get(path, headers=owner).json()['items'][0]
    move = c.post(f'/api/trips/{tid}/items/{item}/move', headers=owner, json={'version':trip['version'], 'day_id':second, 'index':0})
    assert move.status_code == 200
    trip = move.json()
    ledger = c.get(path, headers=owner)
    assert ledger.headers['cache-control'] == 'private, no-store'
    updated = ledger.json()['items'][0]
    assert updated['day_id'] == second and updated['item_id'] == item and updated['spent_on'] == '2026-11-08'
    with sessions() as db:
        db.get(TripExpense, record['id']).day_id = first
        db.commit()
    # Existing inconsistent rows display the actual linked day without writing on a GET.
    assert c.get(path, headers=owner).json()['items'][0]['day_id'] == second
    edited = c.patch(f"{path}/{record['id']}", headers=owner, json={**entry, 'version':trip['version'], 'day_id':second})
    assert edited.status_code == 200
    trip = edited.json()
    async def fake_chat(messages, **kwargs):
        return json.dumps({'operations':[{'action':'move', 'item_id':item, 'day_id':first, 'index':0}]})
    monkeypatch.setattr(trip_ai, 'chat', fake_chat)
    proposals = f'/api/trips/{tid}/ai-proposals'
    preview = c.post(proposals, headers=owner, json={'version':trip['version'], 'request':'移回第一天'}).json()
    applied = c.post(f"{proposals}/{preview['id']}/apply", headers=owner, json={'version':trip['version']})
    assert applied.status_code == 200
    record = c.get(path, headers=owner).json()['items'][0]
    assert record['day_id'] == first and record['item_id'] == item and record['spent_on'] == '2026-11-08'
    with sessions() as db:
        assert db.get(TripExpense, record['id']).day_id == first
        db.get(TripExpense, record['id']).day_id = second
        db.commit()
    deleted = c.post(f'/api/trips/{tid}/items/{item}/delete', headers=owner, json={'version':applied.json()['version']})
    assert deleted.status_code == 200
    retained = c.get(path, headers=owner).json()['items'][0]
    assert retained['item_id'] is None and retained['day_id'] == first and retained['item_name'] == '公园'
