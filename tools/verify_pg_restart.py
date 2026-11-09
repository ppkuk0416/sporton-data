"""Restart only the development DB and compare all application tables before/after."""
import os,subprocess,time,json
from pathlib import Path
from sporton.storage import Store
from sporton.migration import table_evidence
s=Store()
with s.engine.connect() as c:before=table_evidence(c)
s.engine.dispose()
environment=os.environ.copy()
for k in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):environment.pop(k,None)
base=['docker','--host=unix:///var/run/docker.sock']
subprocess.run(base+['restart','sporton-pgdev'],env=environment,check=True,stdout=subprocess.DEVNULL)
for _ in range(60):
 if subprocess.run(base+['exec','--user','postgres','sporton-pgdev','pg_isready','-U','sporton_owner','-d','sporton'],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:break
 time.sleep(1)
else:raise RuntimeError('database not ready after restart; never reset data')
with s.engine.connect() as c:after=table_evidence(c)
if before!=after:raise ValueError('restart persistence evidence differs')
result={'verified':True,'tables':len(before),'method':'actual container restart, all table row counts and content hashes unchanged','host_disaster_recovery_verified':False}
Path('var/persistence-dev/restart-check.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
