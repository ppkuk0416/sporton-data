"""Run a development command with file-based credentials. Never prints their values."""
from pathlib import Path
import os,sys
from sqlalchemy import URL
root=Path('/workspace/sporton-data/var/persistence-dev')
role='sporton_owner' if '--owner' in sys.argv else 'sporton_app'
args=[x for x in sys.argv[1:] if x!='--owner']
if not args:raise SystemExit('usage: python tools/runtime.py [--owner] command ...')
password=(root/('owner.password' if role=='sporton_owner' else 'app.password')).read_text().strip()
environment=os.environ.copy();environment['PATH']=str(Path(sys.executable).parent)+os.pathsep+environment.get('PATH','');environment['SPORTON_DATABASE_URL']=URL.create('postgresql+psycopg',username=role,password=password,host='127.0.0.1',port=55432,database='sporton').render_as_string(hide_password=False)
environment['SPORTON_ENV']='development';environment['SPORTON_PERSISTENCE_CONFIG']='config/persistence.dev.json'
environment['SPORTON_ADMIN_TOKEN_FILE']=str(root/'admin.token')
os.execvpe(args[0],args,environment)
