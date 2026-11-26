import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine
from app.db import database
from tools.deployment_check import check_schema,check_frontend,check_runtime,required_schema


@pytest.fixture()
def release_inputs(tmp_path):
    db=tmp_path/'app.db';engine=create_engine(f'sqlite:///{db}')
    required_schema().create_all(engine);engine.dispose()
    dist=tmp_path/'dist';(dist/'assets').mkdir(parents=True);(dist/'.vite').mkdir()
    (dist/'index.html').write_text('<script type="module" src="/travel/assets/main.js"></script>',encoding='utf-8')
    for name in ('main.js','trip.js','trip.css'):(dist/'assets'/name).write_text('synthetic',encoding='utf-8')
    manifest={'index.html':{'file':'assets/main.js','isEntry':True,'dynamicImports':['trip']},
              'trip':{'file':'assets/trip.js','css':['assets/trip.css']}}
    (dist/'.vite/manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    return db,dist,manifest


def test_schema_and_complete_build_are_read_only(release_inputs):
    db,dist,_=release_inputs;before=hashlib.sha256(db.read_bytes()).digest()
    assert check_schema(db)==[] and check_frontend(dist)==[]
    assert hashlib.sha256(db.read_bytes()).digest()==before
    with pytest.raises(TimeoutError):check_schema(db,timeout=1e-12)
    assert hashlib.sha256(db.read_bytes()).digest()==before


def test_missing_migration_column_is_reported_then_repaired_only_by_explicit_init(release_inputs,monkeypatch):
    db,_,_=release_inputs
    with sqlite3.connect(db) as c:c.execute('ALTER TABLE guides DROP COLUMN revision')
    before=db.read_bytes();assert 'missing column: guides.revision' in check_schema(db)
    assert db.read_bytes()==before
    engine=create_engine(f'sqlite:///{db}');monkeypatch.setattr(database,'engine',engine);monkeypatch.setattr(database,'DATA_DIR',db.parent)
    try:database.init_db();assert check_schema(db)==[]
    finally:engine.dispose()


def test_missing_unique_index_and_foreign_key_do_not_pass(release_inputs):
    db,_,_=release_inputs
    with sqlite3.connect(db) as c:
        c.execute('DROP INDEX ix_users_username')
        c.execute('DROP TABLE comment_creation_requests')
        c.execute('CREATE TABLE comment_creation_requests(id INTEGER,user_id INTEGER,request_id TEXT,payload_hash TEXT,comment_id INTEGER)')
    errors=check_schema(db)
    assert 'missing unique index: users' in errors
    assert any(e.startswith('missing foreign key: comment_creation_requests') for e in errors)
    assert 'incorrect primary key: comment_creation_requests' in errors


def test_missing_lazy_chunk_wrong_base_and_escape_are_rejected(release_inputs):
    _,dist,manifest=release_inputs
    (dist/'assets/trip.js').unlink()
    assert 'missing or unsafe manifest asset' in check_frontend(dist)
    (dist/'assets/trip.js').write_text('restored',encoding='utf-8')
    manifest['trip']['file']='../outside.js';(dist.parent/'outside.js').write_text('outside',encoding='utf-8')
    (dist/'.vite/manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    assert 'missing or unsafe manifest asset' in check_frontend(dist)
    (dist/'index.html').write_text('<script src="/assets/main.js"></script>',encoding='utf-8')
    assert 'incorrect frontend base path' in check_frontend(dist)


def test_runtime_switch_combinations_and_invalid_parameters():
    assert check_runtime(1,{})==[]
    assert check_runtime(2,{})
    values={k:'true' for k in ('TRAVEL_SHARED_LIMITS','TRAVEL_SHARED_CACHE','TRAVEL_PROVIDER_BUDGET')}
    assert check_runtime(2,values)==[]
    assert check_runtime(2,{**values,'TRAVEL_SHARED_CACHE':'treu'})
    assert check_runtime(1,{'TRAVEL_AMAP_INTERVAL':'nan'})
    assert check_runtime(1,{'TRAVEL_CHAT_CONCURRENCY':'0'})


def test_cli_does_not_initialize_application_or_expose_environment(release_inputs,tmp_path):
    db,dist,_=release_inputs;uninitialized=tmp_path/'must-not-be-created'
    command=[sys.executable,str(Path(__file__).parents[1]/'tools/deployment_check.py'),'--database',str(db),'--frontend',str(dist)]
    environment={**os.environ,'TRAVEL_DATA_DIR':str(uninitialized),'JWT_SECRET':'never-print-test-secret',
                 'TRAVEL_SHARED_LIMITS':'0','TRAVEL_SHARED_CACHE':'0','TRAVEL_PROVIDER_BUDGET':'0'}
    before=db.read_bytes()
    result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',env=environment,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
    report=json.loads(result.stdout);assert report['local_checks_passed'] is True and report['not_checked']
    assert 'never-print-test-secret' not in result.stdout+result.stderr
    assert not uninitialized.exists() and db.read_bytes()==before
    missing=tmp_path/'missing.db';command[command.index('--database')+1]=str(missing)
    result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',env=environment,timeout=15)
    assert result.returncode==1 and not missing.exists()
