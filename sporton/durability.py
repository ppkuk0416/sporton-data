"""Authenticated encrypted backups; verification requires an actual isolated restore."""
from pathlib import Path
from datetime import datetime,timedelta
from tempfile import TemporaryDirectory
from uuid import uuid4
import os,json,hashlib,shutil,sqlite3,subprocess,tarfile,time,stat
from pydantic import BaseModel,ConfigDict,Field
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from sqlalchemy import create_engine,make_url,text
from .storage import Store,home,digest
from .contracts import utcnow
from .migration import table_evidence

MAGIC=b'SPORTON-AES256-GCM-v1\n';CHUNK=1024*1024


class Policy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    environment:str='development'
    backup_root:str
    key_file:str
    interval_minutes:int=Field(default=60,ge=1)
    retention_days:int=Field(default=30,ge=1)
    rpo_minutes:int=Field(default=60,ge=1)
    rto_seconds:int=Field(default=300,ge=1)
    failure_retry_minutes:int=Field(default=15,ge=1)
    pg_tools_prefix:list[str]=[]
    wal_staging:str|None=None
    @classmethod
    def load(cls,path=None):
        path=path or os.getenv('SPORTON_PERSISTENCE_CONFIG','config/persistence.dev.json')
        p=cls.model_validate_json(Path(path).read_text())
        root=Path(p.backup_root).resolve();key=Path(p.key_file).resolve()
        if root==key or root in key.parents:raise ValueError('encryption key must be outside backup root')
        if p.environment=='production':
            if home().resolve()==root or home().resolve() in root.parents:raise ValueError('production backup storage must be a separate mount outside data home')
            if p.pg_tools_prefix:raise ValueError('Docker tool prefix is development-only')
        return p


def key_bytes(policy):
    p=Path(policy.key_file)
    if not p.is_file() or p.is_symlink():raise ValueError('private encryption key file missing/unsafe')
    if stat.S_IMODE(p.stat().st_mode)&0o077:raise ValueError('encryption key permissions must be 0600 or stricter')
    data=p.read_bytes()
    if len(data)!=32:raise ValueError('AES-256 key must contain exactly 32 bytes')
    return data


def file_sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(CHUNK),b''):h.update(block)
    return h.hexdigest()


def encrypt(source,dest,key):
    nonce=os.urandom(12);cipher=Cipher(algorithms.AES(key),modes.GCM(nonce)).encryptor();cipher.authenticate_additional_data(MAGIC)
    with Path(source).open('rb') as src,Path(dest).open('xb') as out:
        os.chmod(dest,0o600);out.write(MAGIC+nonce)
        for block in iter(lambda:src.read(CHUNK),b''):out.write(cipher.update(block))
        out.write(cipher.finalize());out.write(cipher.tag)


def decrypt(source,dest,key):
    source=Path(source);size=source.stat().st_size
    if size<len(MAGIC)+28:raise ValueError('invalid encrypted backup')
    with source.open('rb') as src:
        if src.read(len(MAGIC))!=MAGIC:raise ValueError('invalid backup header')
        nonce=src.read(12);src.seek(-16,2);tag=src.read(16);src.seek(len(MAGIC)+12)
        cipher=Cipher(algorithms.AES(key),modes.GCM(nonce,tag)).decryptor();cipher.authenticate_additional_data(MAGIC)
        remaining=size-len(MAGIC)-28
        with Path(dest).open('xb') as out:
            os.chmod(dest,0o600)
            while remaining:
                block=src.read(min(CHUNK,remaining));remaining-=len(block);out.write(cipher.update(block))
            out.write(cipher.finalize()) # Authentication must succeed before extraction.


def pg_command(tool,url,policy,extra=(),stdin=False):
    parsed=make_url(url)
    if policy.pg_tools_prefix:
        # Only local development container; password never appears in argv.
        prefix=list(policy.pg_tools_prefix)
        if stdin:prefix.insert(prefix.index('exec')+1,'-i')
        return prefix+[tool,'--host=/var/run/postgresql','--username='+parsed.username,'--dbname='+parsed.database,*extra],os.environ.copy()
    environment=os.environ.copy()
    environment.update(PGHOST=parsed.host or '',PGPORT=str(parsed.port or 5432),PGUSER=parsed.username or '',PGDATABASE=parsed.database or '')
    if parsed.password:environment['PGPASSWORD']=parsed.password
    for field in ('sslmode','sslrootcert'):
        if parsed.query.get(field):environment['PG'+field.upper()]=str(parsed.query[field])
    if policy.environment=='production' and environment.get('PGSSLMODE')!='verify-full':raise ValueError('production PostgreSQL requires sslmode=verify-full')
    return [tool,*extra],environment


def run_tool(command,environment,output=None,input_file=None):
    with (open(output,'wb') if output else open(os.devnull,'wb')) as out:
        result=subprocess.run(command,env=environment,stdin=open(input_file,'rb') if input_file else subprocess.DEVNULL,
            stdout=out,stderr=subprocess.PIPE,check=False)
    if result.returncode:raise ValueError('database backup/restore tool failed (exit '+str(result.returncode)+'); inspect controlled server diagnostics')


def copy_assets(stage):
    for name in ('models','raw','research','lake'):
        root=home()/name
        if root.exists():
            if any(p.is_symlink() for p in root.rglob('*')):raise ValueError('asset symlink rejected')
            shutil.copytree(root,stage/name)



def schema_evidence(conn):
    if conn.dialect.name=='postgresql':
        rows=conn.execute(text("SELECT 'constraint' kind,n.nspname||'.'||c.relname object,pg_get_constraintdef(x.oid) definition FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' UNION ALL SELECT 'trigger',n.nspname||'.'||c.relname,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal UNION ALL SELECT 'function',n.nspname||'.'||p.proname,pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' ORDER BY 1,2,3")).mappings().all()
    else:rows=conn.execute(text("SELECT type kind,name object,sql definition FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name")).mappings().all()
    return {'objects':len(rows),'sha256':digest([dict(r) for r in rows]),'definitions':[dict(r) for r in rows]}

def create_backup(store,policy=None):
    policy=policy or Policy.load();key=key_bytes(policy)
    if policy.environment=='production' and store.engine.dialect.name!='postgresql':raise ValueError('production backup requires PostgreSQL')
    backup_id=str(uuid4());root=Path(policy.backup_root);root.mkdir(parents=True,exist_ok=True);root.chmod(0o700)
    started=utcnow();destination=root/(backup_id+'.sporton.enc')
    try:
        with TemporaryDirectory(prefix='sporton-backup-') as tmp:
            stage=Path(tmp)/'payload';stage.mkdir(mode=0o700)
            if store.engine.dialect.name=='sqlite':
                with sqlite3.connect(store.engine.url.database) as src,sqlite3.connect(stage/'database.sqlite') as dst:src.backup(dst)
                copied=Store('sqlite:///'+str(stage/'database.sqlite'),initialize=False)
                with copied.engine.connect() as conn:evidence=table_evidence(conn);schema=schema_evidence(conn)
                copied.engine.dispose();db_file='database.sqlite'
            elif store.engine.dialect.name=='postgresql':
                with store.engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn:
                    transaction=conn.begin();snapshot=conn.execute(text('SELECT pg_export_snapshot()')).scalar_one()
                    evidence=table_evidence(conn);schema=schema_evidence(conn)
                    command,environment=pg_command('pg_dump',store.url,policy,['--format=custom','--no-owner','--no-acl','--snapshot='+snapshot])
                    run_tool(command,environment,output=stage/'database.dump');transaction.rollback()
                db_file='database.dump'
            else:raise ValueError('unsupported database backend')
            copy_assets(stage)
            files={str(p.relative_to(stage)):file_sha(p) for p in stage.rglob('*') if p.is_file()}
            manifest={'schema_version':1,'backup_id':backup_id,'at':started.isoformat(),'engine':store.engine.dialect.name,'db_file':db_file,
                'tables':evidence,'schema':schema,'files':files,'restore_paths':{n:str((home()/n).resolve()) for n in ('models','raw','research','lake')},
                'verified_restore':False}
            (stage/'manifest.json').write_text(json.dumps(manifest,indent=2))
            tar_path=Path(tmp)/'backup.tar'
            with tarfile.open(tar_path,'w') as tar:tar.add(stage,arcname='payload',recursive=True)
            encrypt(tar_path,destination,key)
        record={'backup_id':backup_id,'status':'CREATED_UNVERIFIED','at':started.isoformat(),'completed_at':utcnow().isoformat(),
            'path':str(destination.resolve()),'cipher_sha256':file_sha(destination),'engine':store.engine.dialect.name,
            'key_fingerprint':hashlib.sha256(key).hexdigest()[:16],'tables':{n:v['count'] for n,v in evidence.items()},
            'encryption':'AES-256-GCM','restore_verified':False,'environment':policy.environment}
        store.append('backup_runs',backup_id,record)
        return record
    except Exception as exc:
        store.append('backup_runs',backup_id,{'backup_id':backup_id,'status':'FAILED','at':utcnow().isoformat(),'error_type':type(exc).__name__,'error':str(exc),'restore_verified':False})
        raise


def safe_extract(tar_path,destination):
    with tarfile.open(tar_path) as tar:
        for member in tar.getmembers():
            path=(destination/member.name).resolve()
            if destination.resolve() not in path.parents or member.issym() or member.islnk() or member.isdev():raise ValueError('unsafe archive member')
        tar.extractall(destination,filter='data')


def restore_test(store,backup_id,policy=None,admin_url=None):
    policy=policy or Policy.load();record=store.get('backup_runs',backup_id)
    if not record or record.get('status')!='CREATED_UNVERIFIED':raise ValueError('usable backup not found')
    started=time.monotonic();test_id=str(uuid4());scratch_name=None;admin=None
    try:
        if file_sha(record['path'])!=record['cipher_sha256']:raise ValueError('ciphertext checksum mismatch')
        with TemporaryDirectory(prefix='sporton-restore-') as tmp:
            tmp=Path(tmp);archive=tmp/'decrypted.tar';decrypt(record['path'],archive,key_bytes(policy));safe_extract(archive,tmp)
            stage=tmp/'payload';manifest=json.loads((stage/'manifest.json').read_text())
            if manifest['backup_id']!=backup_id:raise ValueError('backup identity mismatch')
            for name,sha in manifest['files'].items():
                path=(stage/name).resolve()
                if stage.resolve() not in path.parents or file_sha(path)!=sha:raise ValueError('asset checksum mismatch')
            if manifest['engine']=='sqlite':
                restored=Store('sqlite:///'+str(stage/manifest['db_file']),initialize=False)
                with restored.engine.connect() as c:
                    if c.execute(text('PRAGMA integrity_check')).scalar_one()!='ok':raise ValueError('SQLite integrity failed')
                    if c.execute(text('PRAGMA foreign_key_check')).fetchall():raise ValueError('foreign key validation failed')
                    actual=table_evidence(c);actual_schema=schema_evidence(c)
                restored.engine.dispose()
            else:
                admin_url=admin_url or store.url
                parsed=make_url(admin_url);admin=create_engine(parsed.set(database='postgres'),isolation_level='AUTOCOMMIT')
                scratch_name='sporton_restore_'+uuid4().hex
                with admin.connect() as c:c.execute(text('CREATE DATABASE "'+scratch_name+'"'))
                target=parsed.set(database=scratch_name)
                command,environment=pg_command('pg_restore',target,policy,['--exit-on-error','--no-owner','--no-acl'],stdin=True)
                run_tool(command,environment,input_file=stage/manifest['db_file'])
                restored=Store(target,initialize=False)
                with restored.engine.connect() as c:actual=table_evidence(c);actual_schema=schema_evidence(c)
                restored.engine.dispose()
            if actual!=manifest['tables']:raise ValueError('restored row counts/content hashes differ')
            if actual_schema!=manifest.get('schema'):
                Path('/tmp/sporton-schema-diff.json').write_text(json.dumps({'expected':manifest.get('schema'),'actual':actual_schema},indent=2))
                raise ValueError('restored constraints/triggers/functions differ or were not captured')
            # A successful tool exit alone is insufficient: every event hash is revalidated.
            restored=Store('sqlite:///'+str(stage/manifest['db_file']),initialize=False) if manifest['engine']=='sqlite' else Store(target,initialize=False)
            from .storage import ENTITIES
            for table in ENTITIES:
                for row in restored.rows(table):
                    def check_refs(value):
                        if isinstance(value,dict):
                            path=value.get('path') or value.get('artifact_path');expected=value.get('sha256') or value.get('artifact_sha256')
                            if path and expected:
                                matches=[(name,Path(base)) for name,base in manifest['restore_paths'].items() if Path(base)==Path(path) or Path(base) in Path(path).parents]
                                if matches:
                                    name,base=matches[0];copy=stage/name/Path(path).relative_to(base)
                                    if not copy.is_file() or file_sha(copy)!=expected:raise ValueError('restored database asset reference invalid')
                                    if copy.suffix=='.parquet':
                                        import pyarrow.parquet as pq
                                        if value.get('row_count') is not None and pq.read_metadata(copy).num_rows!=value['row_count']:raise ValueError('restored Parquet row count invalid')
                            for child in value.values():check_refs(child)
                        elif isinstance(value,list):
                            for child in value:check_refs(child)
                    check_refs(row['payload'])
            restored.engine.dispose()
        elapsed=time.monotonic()-started
        result={'test_id':test_id,'backup_id':backup_id,'status':'RESTORE_VERIFIED','at':utcnow().isoformat(),
            'duration_seconds':elapsed,'rto_target_seconds':policy.rto_seconds,'rto_met_in_this_drill':elapsed<=policy.rto_seconds,
            'tables_verified':len(actual),'schema_objects_verified':actual_schema['objects'],'assets_verified':len(manifest['files']),'isolated_target':True,
            'verification':'actual pg_restore/SQLite open, all table row/content hashes and file hashes; source unchanged'}
        store.append('restore_tests',test_id,result)
        return result
    except Exception as exc:
        store.append('restore_tests',test_id,{'test_id':test_id,'backup_id':backup_id,'status':'FAILED','at':utcnow().isoformat(),'error_type':type(exc).__name__,'error':str(exc),'duration_seconds':time.monotonic()-started})
        raise
    finally:
        if scratch_name and admin:
            with admin.connect() as c:c.execute(text('DROP DATABASE "'+scratch_name+'" WITH (FORCE)'))
        if admin:admin.dispose()


def status(store,policy=None):
    policy=policy or Policy.load();backups=store.rows('backup_runs');tests=store.rows('restore_tests')
    successes=[r['payload'] for r in backups if r['payload']['status']=='CREATED_UNVERIFIED']
    latest_test={r['payload']['backup_id']:r['payload'] for r in tests}
    verified={key for key,value in latest_test.items() if value['status']=='RESTORE_VERIFIED'}
    latest=successes[-1] if successes else None
    latest_verified=next((r for r in reversed(successes) if r['backup_id'] in verified),None)
    age=(utcnow()-datetime.fromisoformat(latest_verified['at'])).total_seconds()/60 if latest_verified else None
    return {'environment':policy.environment,'policy':policy.model_dump(exclude={'key_file'}),
        'latest_backup':latest,'latest_verified_backup':latest_verified,'restore_tests':[r['payload'] for r in tests],
        'latest_backup_restore_verified':bool(latest and latest['backup_id'] in verified),
        'verified_backup_age_minutes':age,'rpo_warning':age is None or age>policy.rpo_minutes,
        'failures':[r['payload'] for r in backups if r['payload']['status']=='FAILED'],
        'pitr_restore_tested':False,'independent_remote_storage_verified':False}


def periodic(store,policy=None):
    policy=policy or Policy.load();state=status(store,policy);latest=state['latest_backup']
    if latest and (utcnow()-datetime.fromisoformat(latest['at'])).total_seconds()<policy.interval_minutes*60:
        if not state['latest_backup_restore_verified']:
            failures=[r for r in state['restore_tests'] if r['backup_id']==latest['backup_id'] and r['status']=='FAILED']
            if failures:
                last=failures[-1]
                if (utcnow()-datetime.fromisoformat(last['at'])).total_seconds()<policy.failure_retry_minutes*60:return {'status':'blocked_after_failed_restore','backup_id':latest['backup_id'],'retry_minutes':policy.failure_retry_minutes}
                fresh=create_backup(store,policy)
                return restore_test(store,fresh['backup_id'],policy)
            return restore_test(store,latest['backup_id'],policy)
        return {'status':'not_due','backup_id':latest['backup_id']}
    backup=create_backup(store,policy)
    return restore_test(store,backup['backup_id'],policy)


def archive_wal(store,policy=None):
    policy=policy or Policy.load();staging=Path(policy.wal_staging) if policy.wal_staging else None
    if not staging or not staging.is_dir():return {'status':'not_configured','pitr_restore_tested':False}
    import re
    root=Path(policy.backup_root)/'wal';root.mkdir(parents=True,exist_ok=True);key=key_bytes(policy);count=0
    for source in staging.iterdir():
        if not re.fullmatch(r'[0-9A-F]{24}(?:\.[0-9A-F]{8}\.backup)?|[0-9A-F]{8}\.history',source.name):continue
        dest=root/(source.name+'.enc')
        event_key='wal:'+source.name
        existing=store.get('backup_runs',event_key)
        if existing:
            if existing['plain_sha256']!=file_sha(source) or existing['cipher_sha256']!=file_sha(dest):raise ValueError('WAL archive integrity mismatch')
            continue
        encrypt(source,dest,key)
        store.append('backup_runs',event_key,{'backup_id':event_key,'status':'WAL_ENCRYPTED','at':utcnow().isoformat(),'path':str(dest.resolve()),
            'plain_sha256':file_sha(source),'cipher_sha256':file_sha(dest),'pitr_restore_tested':False})
        count+=1
    return {'status':'encrypted','new_segments':count,'pitr_restore_tested':False}


def retention(store,policy=None,apply=False):
    policy=policy or Policy.load();latest_test={r['payload']['backup_id']:r['payload'] for r in store.rows('restore_tests')}
    verified={key for key,value in latest_test.items() if value['status']=='RESTORE_VERIFIED'}
    backups=[r['payload'] for r in store.rows('backup_runs') if r['payload']['status']=='CREATED_UNVERIFIED' and r['payload']['backup_id'] in verified]
    backups.sort(key=lambda r:r['at']);protected={r['backup_id'] for r in backups[-2:]};expired=[]
    for b in backups:
        if b['backup_id'] in protected or utcnow()-datetime.fromisoformat(b['at'])<=timedelta(days=policy.retention_days):continue
        path=Path(b['path']).resolve();root=Path(policy.backup_root).resolve()
        if root not in path.parents:raise ValueError('backup path outside configured root')
        if not path.exists():continue
        if file_sha(path)!=b['cipher_sha256']:raise ValueError('expired backup hash mismatch; preserve evidence')
        expired.append(b['backup_id'])
        if apply:
            path.unlink()
            store.append('backup_runs','expired:'+b['backup_id'],{'backup_id':b['backup_id'],'status':'EXPIRED_BY_POLICY','at':utcnow().isoformat(),'retention_days':policy.retention_days})
    return {'expired_candidates':expired,'applied':apply,'protected_verified_backups':sorted(protected),'original_records_deleted':False}
