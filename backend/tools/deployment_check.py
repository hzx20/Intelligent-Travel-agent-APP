"""Read-only local release checks; never migrates, starts or publishes an app."""
import argparse
from contextlib import closing
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import unquote, urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.backup_db import verify_database


def required_schema():
    # Register models, not app.main/init_db. Engine creation does not connect or
    # initialize the configured application DB.
    from app.db.database import Base
    from app.db import models, trip_models, trip_expense_models, guest_import_models, sharing_models, footprint_models, reservation_models
    return Base.metadata


def quote(name):return '"'+name.replace('"','""')+'"'


def check_schema(path,timeout=60):
    if not math.isfinite(timeout) or timeout<=0:raise ValueError('invalid check timeout')
    deadline=time.monotonic()+timeout
    verify_database(path,deadline=deadline)
    from sqlalchemy import UniqueConstraint
    failures=[]
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        db.set_progress_handler(lambda:int(time.monotonic()>=deadline),1000)
        for table in required_schema().sorted_tables:
            if time.monotonic()>=deadline:raise TimeoutError('Schema check timed out')
            if table.name not in tables:failures.append('missing table: '+table.name);continue
            info=db.execute('PRAGMA table_info('+quote(table.name)+')').fetchall()
            columns={r[1] for r in info}
            actual_pk=tuple(r[1] for r in sorted(info,key=lambda r:r[5]) if r[5])
            if actual_pk!=tuple(c.name for c in table.primary_key.columns):failures.append('incorrect primary key: '+table.name)
            failures.extend('missing column: '+table.name+'.'+c.name for c in table.columns if c.name not in columns)
            indexes=db.execute('PRAGMA index_list('+quote(table.name)+')').fetchall()
            unique={frozenset(r[1] for r in info if r[5])}
            actual_indexes=set()
            for index in indexes:
                if index[4]:continue  # A partial index does not enforce global uniqueness.
                fields=tuple(r[2] for r in db.execute('PRAGMA index_info('+quote(index[1])+')'))
                actual_indexes.add(fields)
                if index[2]:unique.add(frozenset(fields))
            for constraint in table.constraints:
                if isinstance(constraint,UniqueConstraint) and frozenset(c.name for c in constraint.columns) not in unique:
                    failures.append('missing unique key: '+table.name+'('+','.join(c.name for c in constraint.columns)+')')
            for index in table.indexes:
                fields=tuple(c.name for c in index.columns)
                if fields not in actual_indexes:failures.append('missing index: '+table.name+'('+','.join(fields)+')')
                if index.unique and frozenset(fields) not in unique:failures.append('missing unique index: '+table.name)
            foreign={}
            for row in db.execute('PRAGMA foreign_key_list('+quote(table.name)+')'):
                foreign.setdefault(row[0],[]).append(row)
            actual_fk={tuple((r[3],r[2],r[4],r[6].upper()) for r in sorted(rows,key=lambda r:r[1])) for rows in foreign.values()}
            for fk in table.foreign_key_constraints:
                fields=tuple((e.parent.name,e.column.table.name,e.column.name,(fk.ondelete or 'NO ACTION').upper()) for e in fk.elements)
                if fields not in actual_fk:failures.append('missing foreign key: '+table.name+'('+','.join(e.parent.name for e in fk.elements)+')')
    return failures


class Entries(HTMLParser):
    def __init__(self):super().__init__();self.urls=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='script' and a.get('src'):self.urls.append(a['src'])
        if tag=='link' and a.get('rel')=='stylesheet' and a.get('href'):self.urls.append(a['href'])


def check_frontend(directory,base='/travel/'):
    root=Path(directory).resolve();failures=[]
    def asset(name):
        path=(root/unquote(name)).resolve()
        return path.is_relative_to(root) and path.is_file()
    try:
        parser=Entries();parser.feed((root/'index.html').read_text(encoding='utf-8'))
        if not parser.urls:failures.append('no built frontend entries')
        entry_files=[]
        for url in parser.urls:
            value=urlsplit(url)
            if value.scheme or value.netloc or not value.path.startswith(base):failures.append('incorrect frontend base path');continue
            name=value.path[len(base):]
            if not asset(name):failures.append('missing or unsafe frontend entry: '+name)
            entry_files.append(name)
        manifest=json.loads((root/'.vite/manifest.json').read_text(encoding='utf-8'))
        if not isinstance(manifest,dict) or not manifest:raise ValueError('empty manifest')
        entries=[]
        for key,item in manifest.items():
            if not isinstance(item,dict) or not isinstance(item.get('file'),str):raise ValueError('invalid manifest entry')
            for name in [item['file'],*item.get('css',[]),*item.get('assets',[])]:
                if not isinstance(name,str) or not asset(name):failures.append('missing or unsafe manifest asset')
            for dependency in [*item.get('imports',[]),*item.get('dynamicImports',[])]:
                if dependency not in manifest:failures.append('unresolved frontend dependency')
            if item.get('isEntry'):entries.append(item['file'])
        if not entries or any(name not in entry_files for name in entries):failures.append('manifest entry does not match HTML')
    except (OSError,ValueError,TypeError):failures.append('frontend build or manifest unreadable')
    return failures


def check_runtime(workers,environ):
    failures=[];flags={}
    for name in ('TRAVEL_SHARED_LIMITS','TRAVEL_SHARED_CACHE','TRAVEL_PROVIDER_BUDGET'):
        value=environ.get(name,'').lower()
        if value not in ('','0','false','no','off','1','true','yes'):failures.append('invalid switch: '+name)
        flags[name]=value in ('1','true','yes')
    if not 1<=workers<=16:failures.append('workers must be between 1 and 16')
    if workers>1 and not all(flags.values()):failures.append('multiple workers require all three shared controls')
    for group in ('AMAP','CHAT','OCR'):
        try:
            capacity=int(environ.get('TRAVEL_'+group+'_CONCURRENCY','1'))
            interval=float(environ.get('TRAVEL_'+group+'_INTERVAL','0'))
            if not 1<=capacity<=16 or not math.isfinite(interval) or not 0<=interval<=60:raise ValueError()
        except ValueError:failures.append('invalid provider budget configuration: '+group)
    return failures


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--frontend',required=True,type=Path)
    parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--timeout',type=float,default=60)
    args=parser.parse_args()
    results={}
    try:results['database']=check_schema(args.database,args.timeout)
    except (OSError,ValueError,sqlite3.Error):results['database']=['database validation failed']
    results['frontend']=check_frontend(args.frontend)
    results['runtime']=check_runtime(args.workers,os.environ)
    passed=not any(results.values())
    print(json.dumps({'local_checks_passed':passed,'checks':results,'not_checked':[
        'production backup and restore','real proxy/HTTPS and nginx -t','live service configuration and capacity',
        'provider entitlements and real external calls','real devices and release approval']},ensure_ascii=False,indent=2))
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
