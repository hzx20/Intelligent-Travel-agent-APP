"""Footprint photos are private, bounded and follow footprint lifecycle."""
from io import BytesIO
from PIL import Image
from app.db.footprint_models import FootprintPhoto
from test_trips import account


def photo_bytes():
    output = BytesIO()
    image = Image.new('RGB', (8, 8), 'green')
    exif = Image.Exif()
    exif[270] = 'private camera description'
    image.save(output, format='JPEG', exif=exif)
    return output.getvalue()


def test_footprint_photo_lifecycle_privacy_and_metadata(client):
    c, Session = client
    owner, stranger = account(c, 'photo_owner'), account(c, 'photo_stranger')
    trip = c.post('/api/trips', headers=owner, json={'title':'旅行', 'days':1}).json()
    trip = c.post(f"/api/trips/{trip['id']}/days/{trip['days'][0]['id']}/items", headers=owner,
                  json={'version':trip['version'], 'name':'西湖'}).json()
    trip = c.post(f"/api/trips/{trip['id']}/footprints", headers=owner,
                  json={'version':trip['version'], 'item_id':trip['days'][0]['items'][0]['id']}).json()
    assert c.get('/api/footprints', headers=owner).headers['cache-control'] == 'private, no-store'
    assert c.get(f"/api/trips/{trip['id']}/footprints", headers=owner).headers['cache-control'] == 'private, no-store'
    footprint = c.get(f"/api/trips/{trip['id']}/footprints", headers=owner).json()['items'][0]
    path = f"/api/trips/{trip['id']}/footprints/{footprint['id']}/photos"
    headers = {**owner, 'Content-Type':'image/jpeg', 'X-Trip-Version':str(trip['version'])}
    assert c.post(path, headers={**headers, **stranger}, content=photo_bytes()).status_code == 404
    assert c.post(path, headers=headers, content=b'not an image').status_code == 422
    assert c.post(path, headers=headers, content=b'x' * (4 * 1024 * 1024 + 1)).status_code == 413
    assert c.post(path, headers={**headers, 'Content-Type':'image/svg+xml'}, content=b'<svg/>').status_code == 415
    added = c.post(path, headers=headers, content=photo_bytes())
    assert added.status_code == 201, added.text
    trip = added.json()
    row = c.get(f"/api/trips/{trip['id']}/footprints", headers=owner).json()['items'][0]
    photo_id = row['photo_ids'][0]
    assert c.get(f'{path}/{photo_id}', headers=stranger).status_code == 404
    photo = c.get(f'{path}/{photo_id}', headers=owner)
    assert photo.status_code == 200 and photo.headers['cache-control'] == 'private, no-store'
    assert not Image.open(BytesIO(photo.content)).getexif()
    assert c.post(path, headers=headers, content=photo_bytes()).status_code == 409
    edit_path = f"/api/trips/{trip['id']}/footprints/{footprint['id']}"
    edit = {'version': trip['version'], 'city': '杭州', 'visited_on': '2026-11-01', 'note': '湖边散步'}
    assert c.patch(edit_path, headers=stranger, json=edit).status_code == 404
    changed = c.patch(edit_path, headers=owner, json=edit)
    assert changed.status_code == 200
    trip = changed.json()
    assert c.patch(edit_path, headers=owner, json=edit).status_code == 409
    updated_record = c.get(f"/api/trips/{trip['id']}/footprints", headers=owner).json()['items'][0]
    assert (updated_record['city'], updated_record['visited_on'], updated_record['note']) == ('杭州', '2026-11-01', '湖边散步')
    assert updated_record['photo_ids'] == [photo_id]
    assert c.get(f'{path}/{photo_id}', headers=owner).status_code == 200
    for _ in range(2):
        headers['X-Trip-Version'] = str(trip['version'])
        trip = c.post(path, headers=headers, content=photo_bytes()).json()
    headers['X-Trip-Version'] = str(trip['version'])
    assert c.post(path, headers=headers, content=photo_bytes()).status_code == 409
    assert c.get(f"/api/trips/{trip['id']}", headers=owner).json()['version'] == trip['version']
    deleted = c.post(f'{path}/{photo_id}/delete', headers=owner, json={'version':trip['version']})
    assert deleted.status_code == 200
    trip = deleted.json()
    assert c.get(f'{path}/{photo_id}', headers=owner).status_code == 404
    assert c.post(f"/api/trips/{trip['id']}/footprints/{footprint['id']}/delete", headers=owner,
                  json={'version':trip['version']}).status_code == 200
    assert c.get(f'{path}/{photo_id}', headers=owner).status_code == 404
    with Session() as db:
        assert db.query(FootprintPhoto).count() == 0


def test_photos_stay_private_from_other_trip_editors_and_after_revocation(client):
    c, _ = client
    owner, editor = account(c, 'photo_trip_owner'), account(c, 'photo_editor')
    trip = c.post('/api/trips', headers=owner, json={'title': '旅行'}).json()
    trip = c.post(f"/api/trips/{trip['id']}/days/{trip['days'][0]['id']}/items", headers=owner,
                  json={'version':trip['version'], 'name':'西湖'}).json()
    share = f"/api/trips/{trip['id']}/sharing"
    invitation = c.post(f'{share}/links', headers=owner, json={'mode':'edit'}).json()
    assert c.post('/api/trip-sharing/accept', headers=editor, json={'token': invitation['token']}).status_code == 200
    trip = c.get(f"/api/trips/{trip['id']}", headers=editor).json()
    trip = c.post(f"/api/trips/{trip['id']}/footprints", headers=editor,
                  json={'version':trip['version'], 'item_id':trip['days'][0]['items'][0]['id']}).json()
    record = c.get(f"/api/trips/{trip['id']}/footprints", headers=editor).json()['items'][0]
    path = f"/api/trips/{trip['id']}/footprints/{record['id']}/photos"
    added = c.post(path, headers={**editor, 'Content-Type':'image/jpeg', 'X-Trip-Version':str(trip['version'])}, content=photo_bytes())
    assert added.status_code == 201
    photo_id = c.get(f"/api/trips/{trip['id']}/footprints", headers=editor).json()['items'][0]['photo_ids'][0]
    assert c.get(f'{path}/{photo_id}', headers=owner).status_code == 404
    member = c.get(share, headers=owner).json()['members'][0]
    assert c.delete(f"{share}/members/{member['user_id']}", headers=owner).status_code == 204
    assert c.get(f'{path}/{photo_id}', headers=editor).status_code == 404
