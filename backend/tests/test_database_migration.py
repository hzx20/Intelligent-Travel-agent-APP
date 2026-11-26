"""Rehearse the supported legacy-column upgrade on a restored, isolated DB."""
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.db import database
from app.db.models import User, Guide, AiPlan, Spot, Favorite, Comment
from app.db.trip_models import Trip, TripDay, TripItem
from app.db.guest_import_models import GuestTripImport
from tools.backup_db import backup_database, verify_database
from app.routers.auth import get_current_user
from app.routers.trips import router as trip_router


def test_legacy_backup_restore_upgrade_is_idempotent_and_preserves_data(tmp_path, monkeypatch):
    legacy_path, snapshot, restored_path = [tmp_path/name for name in ('legacy.db','backup.db','restored.db')]
    legacy = create_engine(f'sqlite:///{legacy_path}')
    database.Base.metadata.create_all(legacy)
    with Session(legacy) as db:
        user = User(username='legacy_owner', password_hash='unchanged-hash')
        db.add(user); db.flush()
        spot = Spot(name='旧收藏景点',city='杭州',amap_id='legacy-favorite')
        db.add(spot); db.flush()
        db.add(Favorite(user_id=user.id,spot_id=spot.id))
        db.add(Comment(user_id=user.id,spot_id=spot.id,content='保留旧评论'))
        db.add(Guide(user_id=user.id,title='保留攻略',content='原始正文',city='杭州'))
        db.add(AiPlan(user_id=user.id,session_id='legacy-session',status='completed',result_json='{"days":[]}'))
        trip = Trip(user_id=user.id,title='已有旅行',destination='杭州')
        db.add(trip); db.flush()
        day = TripDay(trip_id=trip.id,day_number=1)
        db.add(day); db.flush()
        db.add(TripItem(day_id=day.id,sort_order=0,name='西湖',note='保留备注'))
        db.add(GuestTripImport(user_id=user.id,guest_id='guest-legacy',guest_version=1,payload_hash='unused',trip_id=trip.id))
        db.commit()
    # Synthesize exactly the legacy shapes handled by _add_missing_columns.
    missing = {'users':['profile_version'], 'guides':['views','is_draft','version','revision'], 'ai_plans':['title','summary','chat_json','map_json','version','revision','restore_until'],
               'trips':['walking_preferences', 'budget_review'], 'trip_days':['route_preferences'], 'trip_items':['source', 'location_candidates'], 'guest_trip_imports':['payload_hash']}
    added_tables = ('trip_ai_jobs','trip_proposals','trip_expenses','trip_members','trip_share_links',
                    'footprint_photos','trip_footprints','trip_reservations','trip_creation_requests','guide_creation_requests',
                    'spot_favorite_states','comment_creation_requests','plan_creation_requests','plan_generation_jobs','plan_trip_conversions')
    with legacy.begin() as conn:
        for table, columns in missing.items():
            for column in columns: conn.execute(text(f'ALTER TABLE {table} DROP COLUMN {column}'))
        for table in added_tables: conn.execute(text(f'DROP TABLE {table}'))
    originals = {}
    with legacy.connect() as conn:
        for table in ('users','spots','favorites','comments','guides','ai_plans','trips','trip_days','trip_items','guest_trip_imports'):
            columns = [column['name'] for column in inspect(legacy).get_columns(table)]
            originals[table] = (columns, conn.execute(text(f'SELECT {",".join(columns)} FROM {table} ORDER BY id')).fetchall())
    legacy.dispose()
    backup_database(legacy_path,snapshot); backup_database(snapshot,restored_path)
    restored = create_engine(f'sqlite:///{restored_path}')
    monkeypatch.setattr(database,'engine',restored)
    monkeypatch.setattr(database,'DATA_DIR',tmp_path)
    try:
        database.init_db()
        with restored.connect() as conn:
            first_revision = conn.execute(text('SELECT revision FROM guides WHERE id = 1')).scalar_one()
            first_plan_revision=conn.execute(text('SELECT revision FROM ai_plans WHERE id = 1')).scalar_one()
        database.init_db()
        assert set(added_tables) <= set(inspect(restored).get_table_names())
        with restored.connect() as conn:
            for table,(columns,rows) in originals.items():
                assert conn.execute(text(f'SELECT {",".join(columns)} FROM {table} ORDER BY id')).fetchall() == rows
        with Session(restored) as db:
            assert db.query(User).one().password_hash == 'unchanged-hash'
            assert db.query(User).one().profile_version == 1
            assert db.query(Guide).one().views == 0
            assert db.query(Guide).one().is_draft is False
            assert db.query(Guide).one().version == 1
            guide_revision = db.query(Guide).one().revision
            assert len(guide_revision) == 36 and guide_revision == first_revision
            assert db.query(AiPlan).one().chat_json == '[]'
            assert db.query(AiPlan).one().version==1 and db.query(AiPlan).one().revision==first_plan_revision and len(first_plan_revision)==36
            assert db.query(TripItem).one().source == ''
            assert db.query(TripItem).one().location_candidates == []
            assert db.query(Trip).one().walking_preferences == {}
            assert db.query(Trip).one().budget_review == {}
            assert db.query(TripDay).one().route_preferences == {}
            assert db.query(GuestTripImport).one().payload_hash == ''
        app = FastAPI()
        from app.routers.trip_plan_import import router as plan_conversion_router
        app.include_router(plan_conversion_router)
        app.include_router(trip_router)
        from app.routers.guides import router as guide_router
        app.include_router(guide_router)
        from app.routers.me import router as me_router
        app.include_router(me_router)
        from app.routers.spots import router as spot_router
        app.include_router(spot_router)
        from app.routers.planner import router as planner_router
        from app.routers.auth import get_current_user_optional
        app.include_router(planner_router)
        def get_restored_db():
            with Session(restored) as db: yield db
        def restored_user(db=Depends(database.get_db)):
            return db.query(User).one()
        app.dependency_overrides[database.get_db] = get_restored_db
        app.dependency_overrides[get_current_user] = restored_user
        app.dependency_overrides[get_current_user_optional] = restored_user
        with TestClient(app) as c:
            old_plan=c.get('/api/plan/1').json()
            changed_plan=c.patch('/api/plan/1/snapshot/edit',json={'version':old_plan['version'],'revision':old_plan['revision'],'messages':[{'role':'ai','text':'升级后快照'}]})
            assert changed_plan.status_code==200 and changed_plan.json()['version']==2
            deleted_plan=c.delete('/api/plan/1/content',params={'version':2,'revision':old_plan['revision']})
            assert deleted_plan.status_code==200 and deleted_plan.json()['status']=='deleted'
            assert c.post('/api/plan/1/content/restore',json={'version':3,'revision':old_plan['revision']}).json()['version']==4
            loaded = c.get('/api/trips/1')
            assert loaded.status_code == 200
            trip = loaded.json()
            assert trip['title'] == '已有旅行'
            assert trip['days'][0]['items'][0]['note'] == '保留备注'
            updated = c.post(f"/api/trips/1/days/{trip['days'][0]['id']}/items",
                             json={'version':trip['version'],'name':'升级后新增','source':'手动'})
            assert updated.status_code == 201
            assert updated.json()['days'][0]['items'][-1]['source'] == '手动'
            body = {'title': '升级后可靠创建', 'request_id': '11111111-1111-4111-8111-111111111111'}
            created = c.post('/api/trips/create', json=body)
            replayed = c.post('/api/trips/create', json=body)
            assert created.status_code == 201 and replayed.status_code == 200
            assert created.json()['id'] == replayed.json()['id']
            with Session(restored) as db:
                fresh_plan=AiPlan(user_id=db.query(User).one().id,session_id='restored-conversion',status='completed',result_json='{"days":[{}]}')
                db.add(fresh_plan);db.commit();fresh_id,fresh_revision=fresh_plan.id,fresh_plan.revision
            conversion_body={'version':1,'revision':fresh_revision}
            converted=c.post(f'/api/trips/from-plan/{fresh_id}/convert',json=conversion_body)
            assert converted.status_code==201
            assert c.post(f'/api/trips/from-plan/{fresh_id}/convert',json=conversion_body).json()['trip']['id']==converted.json()['trip']['id']
            guide_body = {'title':'升级后攻略','content':'保留正文','version':1,'revision':guide_revision}
            changed = c.patch('/api/guides/1/content',json=guide_body)
            assert changed.status_code == 200 and changed.json()['version'] == 2
            guide_body = {'title':'升级后新攻略','content':'新正文','request_id':'22222222-2222-4222-8222-222222222222'}
            first = c.post('/api/guides/create',json=guide_body)
            second = c.post('/api/guides/create',json=guide_body)
            assert first.status_code == 201 and second.status_code == 200 and first.json() == second.json()
            changed_profile = c.patch('/api/me/profile/edit',json={'nickname':'升级后昵称','version':1})
            assert changed_profile.status_code == 200 and changed_profile.json()['version'] == 2
            assert c.patch('/api/me/profile/edit',json={'nickname':'旧版本覆盖','version':1}).status_code == 409
            old_favorite = c.get('/api/spots/1/favorite-state')
            assert old_favorite.status_code == 200 and old_favorite.json()['favorited'] is True and old_favorite.json()['favorite_version'] == 0
            updated_favorite = c.put('/api/spots/1/favorite-state',json={'favorited':False,'version':0})
            assert updated_favorite.status_code == 200 and updated_favorite.json()['favorite_version'] == 1
            comment_body = {'content':'升级后可靠评论','request_id':'44444444-4444-4444-8444-444444444444'}
            first_comment = c.post('/api/spots/1/comments/create',json=comment_body)
            replay_comment = c.post('/api/spots/1/comments/create',json=comment_body)
            assert first_comment.status_code == 201 and replay_comment.status_code == 200 and first_comment.json() == replay_comment.json()
        verify_database(restored_path)
        # Migration ran only on the restored copy, not on its source or backup.
        for path in (legacy_path,snapshot):
            engine = create_engine(f'sqlite:///{path}')
            try:
                assert 'source' not in {c['name'] for c in inspect(engine).get_columns('trip_items')}
                assert 'revision' not in {c['name'] for c in inspect(engine).get_columns('guides')}
                assert 'profile_version' not in {c['name'] for c in inspect(engine).get_columns('users')}
            finally: engine.dispose()
    finally:
        restored.dispose()
