"""Start one current-environment NBA worker; preserve its first active date across restarts."""
from pathlib import Path
from datetime import timedelta,time
import fcntl,json,os,subprocess,sys

root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root));os.chdir(root)
from sporton.storage import home
from sporton.contracts import utcnow
from sporton.dispatch import KST
from sporton.telegram import settings

if not settings():raise SystemExit('Telegram not configured')
with (home()/'nba-daemon.lock').open('a') as handle:
    try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        print(json.dumps({'status':'already_running'}));raise SystemExit(0)
    finally:fcntl.flock(handle,fcntl.LOCK_UN)
config=home()/'nba-daemon-service.json'
if config.exists():start=json.loads(config.read_text())['start_date']
else:
    local=utcnow().astimezone(KST);start=str(local.date()+timedelta(days=int(local.time()>time(6,30))))
    config.write_text(json.dumps({'start_date':start,'scope':'current_cloud_environment_only'}))
log=home()/'nba-daemon.log'
with log.open('ab',buffering=0) as out:
    process=subprocess.Popen([sys.executable,'-m','sporton.dispatch','--daemon','--date',start],cwd=root,
        stdin=subprocess.DEVNULL,stdout=out,stderr=out,start_new_session=True)
print(json.dumps({'status':'started','pid':process.pid,'first_date_kst':start,'log':str(log),'cloud_session_lifetime_only':True}))
