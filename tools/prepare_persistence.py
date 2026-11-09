"""Generate local-development secrets once; production secrets are provisioned separately."""
from pathlib import Path
from sqlalchemy import URL
import secrets
root=Path('var/persistence-dev');root.mkdir(parents=True,exist_ok=True);root.chmod(0o700)
for name in ('owner.password','app.password','admin.token','backup.key'):
 p=root/name
 if not p.exists():
  data=secrets.token_bytes(32) if name=='backup.key' else secrets.token_urlsafe(32).encode()
  with p.open('xb') as f:f.write(data)
 p.chmod(0o600)
 if name=='backup.key' and len(p.read_bytes())!=32:raise ValueError('existing backup key invalid; preserve and investigate')
for filename,user,host,port,passwordfile in [('test-admin.url','sporton_owner','127.0.0.1',55432,'owner.password'),('compose-owner.url','sporton_owner','postgres',5432,'owner.password'),('compose-app.url','sporton_app','postgres',5432,'app.password')]:
 p=root/filename;value=URL.create('postgresql+psycopg',username=user,password=(root/passwordfile).read_text().strip(),host=host,port=port,database='sporton').render_as_string(hide_password=False)
 if p.exists() and p.read_text().strip()!=value:raise ValueError('existing selector differs; preserve '+filename)
 if not p.exists():p.write_text(value)
 p.chmod(0o600)
print('Development credential/key files ready; existing values preserved; production not configured.')
