"""Run a restored snapshot as a separate application, leaving the live DB alone."""
import hashlib
import socket

from test_service_restart import running_backend
from test_trips import account
from test_footprint_photos import photo_bytes
from tools.backup_db import backup_database, verify_database


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def test_online_snapshot_restore_serves_private_data_and_original_attempts(tmp_path):
    source = tmp_path/'source-data'
    restored = tmp_path/'restored-data'
    restored.mkdir()
    snapshot = tmp_path/'verified-snapshot.db'
    with running_backend(source, port()) as live:
        owner, editor, stranger = (account(live, name) for name in ('restore_owner','restore_editor','restore_stranger'))
        creation = {'title':'快照中的原旅行','days':1,'request_id':'11111111-1111-4111-8111-111111111111'}
        result = live.post('/api/trips/create', headers=owner, json=creation)
        assert result.status_code == 201
        trip = result.json()
        trip = live.post(f"/api/trips/{trip['id']}/days/{trip['days'][0]['id']}/items", headers=owner,
                         json={'version':trip['version'],'name':'合成散步地点'}).json()
        trip = live.post(f"/api/trips/{trip['id']}/reservations", headers=owner,
                         json={'version':trip['version'],'title':'合成酒店','reference':'RESTORE-PRIVATE'}).json()
        trip = live.post(f"/api/trips/{trip['id']}/footprints", headers=owner,
                         json={'version':trip['version'],'item_id':trip['days'][0]['items'][0]['id']}).json()
        feet = f"/api/trips/{trip['id']}/footprints"
        footprint = live.get(feet, headers=owner).json()['items'][0]
        photos = f"{feet}/{footprint['id']}/photos"
        photo = live.post(photos, headers={**owner,'Content-Type':'image/jpeg','X-Trip-Version':str(trip['version'])}, content=photo_bytes())
        assert photo.status_code == 201
        trip = photo.json()
        photo_id = live.get(feet, headers=owner).json()['items'][0]['photo_ids'][0]
        photo_url = f'{photos}/{photo_id}'
        before_photo = live.get(photo_url, headers=owner)
        assert before_photo.status_code == 200 and before_photo.headers['content-type'] == 'image/jpeg'
        trip_url = f"/api/trips/{trip['id']}"
        quote = {'status':'quoted','amount_minor':98000,'source':'合成住宿报价','checked_on':'2026-11-18'}
        budget = {'version':trip['version'],'checklist':{'limit_minor':300000,'entries':{'lodging':quote}}}
        quoted = live.patch(trip_url+'/budget-review', headers=owner, json=budget)
        assert quoted.status_code == 200
        assert quoted.json()['budget_review']['checklist']['entries']['lodging'] == quote
        assert live.get(trip_url+'/expenses', headers=owner).json()['items'] == []
        day = trip['days'][0]
        paid = live.post(trip_url+'/expenses', headers=owner, json={
            'version':quoted.json()['version'],'amount_minor':1200,'category':'交通',
            'day_id':day['id'],'item_id':day['items'][0]['id'],
        })
        assert paid.status_code == 201
        original_ledger = live.get(trip_url+'/expenses', headers=owner).json()
        assert original_ledger['totals'] == {'CNY':1200}
        assert [(row['item_id'], row['amount_minor']) for row in original_ledger['items']] == [(day['items'][0]['id'], 1200)]
        sharing = f"/api/trips/{trip['id']}/sharing"
        view = live.post(sharing+'/links', headers=owner, json={'mode':'view'}).json()
        invite = live.post(sharing+'/links', headers=owner, json={'mode':'edit'}).json()
        assert live.post('/api/trip-sharing/accept', headers=editor, json={'token':invite['token']}).status_code == 200
        baseline = live.get(f"/api/trips/{trip['id']}", headers=owner).json()
        backup_database(source/'app.db', snapshot)
        digest = hashlib.sha256(snapshot.read_bytes()).digest()
        backup_database(snapshot, restored/'app.db')
        live_update = live.patch(f"/api/trips/{trip['id']}", headers=owner,
                                json={'version':baseline['version'],'title':'快照之后的原库修改'})
        assert live_update.status_code == 200
        with running_backend(restored, port()) as recovered:
            replay = recovered.post('/api/trips/create', headers=owner, json=creation)
            assert replay.status_code == 200 and replay.json() == baseline
            assert len(recovered.get('/api/trips', headers=owner).json()['items']) == 1
            assert recovered.get(f"/api/trips/{trip['id']}", headers=stranger).status_code == 404
            status = recovered.post('/api/trip-sharing/invitation-status', headers=editor, json={'token':invite['token']})
            assert status.json() == {'state':'joined','trip_id':trip['id'],'role':'editor'}
            assert recovered.get(f"/api/trips/{trip['id']}/reservations", headers=editor).json()['items'][0]['reference'] == 'RESTORE-PRIVATE'
            shared = recovered.post('/api/trip-sharing/view', json={'token':view['token']})
            assert shared.status_code == 200 and 'RESTORE-PRIVATE' not in shared.text
            restored_photo = recovered.get(photo_url, headers=owner)
            assert restored_photo.status_code == 200 and restored_photo.content == before_photo.content
            assert recovered.get(trip_url, headers=owner).json()['budget_review']['checklist']['entries']['lodging'] == quote
            assert recovered.get(trip_url+'/expenses', headers=owner).json() == original_ledger
            assert recovered.patch(trip_url+'/budget-review', headers=owner, json=budget).status_code == 409
            assert recovered.get(trip_url+'/expenses', headers=stranger).status_code == 404
            assert recovered.get(photo_url, headers=editor).status_code == 404
            assert recovered.get(photo_url).status_code == 401
            member_id = recovered.get(sharing, headers=owner).json()['members'][0]['user_id']
            assert recovered.delete(f'{sharing}/members/{member_id}', headers=stranger).status_code == 404
            assert recovered.delete(f'{sharing}/members/{member_id}', headers=owner).status_code == 204
            assert recovered.get(f"/api/trips/{trip['id']}", headers=editor).status_code == 404
            assert recovered.post('/api/trip-sharing/invitation-status', headers=editor,
                                  json={'token':invite['token']}).json() == {'state':'not_joined'}
            changed = recovered.patch(f"/api/trips/{trip['id']}", headers=owner,
                                      json={'version':baseline['version']+1,'title':'仅恢复副本修改'})
            assert changed.status_code == 200
        assert live.get(f"/api/trips/{trip['id']}", headers=owner).json()['title'] == '快照之后的原库修改'
        assert hashlib.sha256(snapshot.read_bytes()).digest() == digest
    verify_database(snapshot)
    verify_database(restored/'app.db')
