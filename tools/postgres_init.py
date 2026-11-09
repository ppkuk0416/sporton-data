"""Initialize development owner schema and least-privilege app role. No secrets logged."""
from pathlib import Path
import os,json
from sqlalchemy import URL,text
import psycopg
from psycopg import sql
from sporton.storage import Store,ENTITIES
from sporton.schema import TYPED_TABLES
root=Path('/workspace/sporton-data/var/persistence-dev')
owner=root.joinpath('owner.password').read_text().strip();app=root.joinpath('app.password').read_text().strip()
url=URL.create('postgresql+psycopg',username='sporton_owner',password=owner,host='127.0.0.1',port=55432,database='sporton')
s=Store(url,initialize=True)
with psycopg.connect(host='127.0.0.1',port=55432,user='sporton_owner',password=owner,dbname='sporton',autocommit=True) as c:
 if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='sporton_app'").fetchone():
  c.execute(sql.SQL('CREATE ROLE sporton_app LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE').format(sql.Literal(app)))
 c.execute(sql.SQL('ALTER SYSTEM SET archive_command = {}').format(sql.Literal('if test -f /wal-archive/%f; then cmp -s %p /wal-archive/%f; else cp %p /wal-archive/%f && chmod 640 /wal-archive/%f; fi')))
 c.execute('SELECT pg_reload_conf()')
 c.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
 c.execute('GRANT USAGE ON SCHEMA public TO sporton_app')
 c.execute('GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA public TO sporton_app')
 c.execute('ALTER DEFAULT PRIVILEGES FOR ROLE sporton_owner IN SCHEMA public GRANT SELECT, INSERT ON TABLES TO sporton_app')
print(json.dumps({'postgres':'initialized','app_role':'SELECT/INSERT only','tables':len(ENTITIES)+len(TYPED_TABLES)}))
