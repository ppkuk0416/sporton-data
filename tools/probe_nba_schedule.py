"""Run one delayed Telegram scheduling probe, without marking any daily report sent."""
import json,time
from pathlib import Path
from uuid import uuid4
from sporton.storage import Store,home
from sporton.contracts import utcnow
from sporton.telegram import deliver

began=utcnow();time.sleep(10)
preview=home()/'daily-reports'/'2026-11-12-preview.md'
record={'id':'nba-scheduler-probe-'+uuid4().hex,'data_mode':'real',
    'markdown':'# 자동 예약 실행 검증용 보고서\n\n10초 지연 후 별도 프로세스가 실행한 시험입니다. 최종 06:30 보고서가 아닙니다. 외부 상시 예약은 아직 활성화 검증 전입니다.\n\n'+preview.read_text()}
s=Store();outcome=deliver(s,record)
(home()/'nba-schedule-probe.json').write_text(json.dumps({'started':began.isoformat(),'executed':utcnow().isoformat(),'outcome':outcome,'report_id':record['id']}))
print(json.dumps(outcome))
