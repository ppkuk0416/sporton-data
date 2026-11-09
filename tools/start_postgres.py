"""Start/reuse only this project's pinned local PostgreSQL container, preserving its data."""
from pathlib import Path
import subprocess,os,time
root=Path(__file__).resolve().parents[1]
environment=os.environ.copy()
for name in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):environment.pop(name,None)
base=['docker','--host=unix:///var/run/docker.sock'];name='sporton-pgdev'
image='postgres@sha256:748a18a54d3842d43bc156f0840d78029b8d349e0feca98c12b9c4ce3404a6b3'
def run(args,**kwargs):return subprocess.run(base+args,env=environment,check=True,**kwargs)
run(['info','--format','{{.ServerVersion}}'])
exists=subprocess.run(base+['inspect','--format','{{.Config.Image}}',name],env=environment,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
data=root/'var/postgres/data';wal=root/'var/postgres/wal-archive'
for path in (data,wal):path.mkdir(parents=True,exist_ok=True)
if exists.returncode==0:
 if exists.stdout.decode().strip()!=image:raise ValueError('existing container image differs; do not replace automatically')
 mounted=run(['inspect','--format','{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Source}}{{end}}{{end}}',name],stdout=subprocess.PIPE).stdout.decode().strip()
 if Path(mounted).resolve()!=data:raise ValueError('existing data mount differs; preserve container')
 run(['start',name],stdout=subprocess.DEVNULL)
else:
 run(['pull',image])
 run(['run','-d','--name',name,'--restart','unless-stopped','--label','io.sporton.environment=development','-p','127.0.0.1:55432:5432',
  '--mount',f'type=bind,src={data},dst=/var/lib/postgresql/data','--mount',f'type=bind,src={wal},dst=/wal-archive',
  '--mount',f'type=bind,src={root}/var/persistence-dev/owner.password,dst=/run/secrets/owner_password,readonly',
  '-e','POSTGRES_USER=sporton_owner','-e','POSTGRES_DB=sporton','-e','POSTGRES_PASSWORD_FILE=/run/secrets/owner_password',image,
  '-c','wal_level=replica','-c','archive_mode=on','-c','archive_timeout=3600',
  '-c','archive_command=if test -f /wal-archive/%f; then cmp -s %p /wal-archive/%f; else cp %p /wal-archive/%f && chmod 640 /wal-archive/%f; fi'])
run(['exec','--user','root',name,'sh','-c','chown postgres:1000 /wal-archive && chmod 2770 /wal-archive'])
for attempt in range(60):
 result=subprocess.run(base+['exec','--user','postgres',name,'pg_isready','-U','sporton_owner','-d','sporton'],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 if result.returncode==0:print('PostgreSQL ready; persistent data retained.');break
 time.sleep(1)
else:raise RuntimeError('PostgreSQL did not become ready; inspect logs, never reset existing data')
