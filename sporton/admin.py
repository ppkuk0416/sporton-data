"""Authenticated, read-only persistence administration and original-record traceability."""
from pathlib import Path
from datetime import datetime
import os,hmac
from fastapi import APIRouter,Depends,HTTPException,Header
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from .storage import Store,ENTITIES
from .schema import TYPED_TABLES
from .governance import effective_get,observation_state
from .durability import status,Policy

router=APIRouter()


def authorized(authorization:str|None=Header(default=None)):
    path=os.getenv('SPORTON_ADMIN_TOKEN_FILE')
    if not path or not Path(path).is_file():raise HTTPException(503,'admin authentication not configured')
    if not authorization or not authorization.startswith('Bearer ') or not hmac.compare_digest(authorization[7:],Path(path).read_text().strip()):
        raise HTTPException(401,'admin authentication required',headers={'WWW-Authenticate':'Bearer'})


@router.get('/admin',response_class=HTMLResponse)
def admin_page():return Path(__file__).with_name('admin.html').read_text()


@router.get('/api/admin/status',dependencies=[Depends(authorized)])
def admin_status():
    s=Store();counts={}
    with s.engine.connect() as c:
        for table in [*ENTITIES,*[t.name for t in TYPED_TABLES]]:counts[table]=c.execute(text(f'SELECT count(*) FROM {table}')).scalar_one()
        pg=None
        if s.engine.dialect.name=='postgresql':
            row=c.execute(text('SELECT archived_count,failed_count,last_archived_time,last_failed_time FROM pg_stat_archiver')).mappings().first()
            pg={k:v.isoformat() if hasattr(v,'isoformat') else v for k,v in row.items()}
    reports=s.rows('professional_reports');reviews=s.rows('critical_reviews');observations=s.rows('source_observations')
    groups={(r['payload']['league'],r['payload']['entity_id'],r['payload']['field'],r['payload']['effective_at']) for r in observations}
    issues=[]
    for league,entity,field,at in sorted(groups):
        state=observation_state(s,league,entity,field,at)
        if state['status']!='VERIFIED':issues.append({'league':league,'entity_id':entity,'field':field,'effective_at':at,**state})
    try:backups=status(s)
    except Exception as exc:backups={'configuration_error':str(exc),'rpo_warning':True,'latest_backup_restore_verified':False}
    from .analytics import performance
    return {'database_engine':s.engine.dialect.name,'counts':counts,'data_issues':issues,
        'validation_events':s.rows('validation_events')[-100:],'corrections':s.rows('record_revisions')[-100:],
        'reports':[{'id':r['id'],'kind':r['payload']['kind'],'created_at':r['created_at'],'fixture_id':r['payload']['fixture_id'],'data_mode':r['payload']['data_mode']} for r in reports],
        'review_counts':{'PASS':sum(r['payload']['verdict']=='PASS' for r in reviews),'NO':sum(r['payload']['verdict']=='NO' for r in reviews)},
        'model_versions':s.rows('model_versions'),'training_runs':s.rows('training_runs')[-30:],
        'backup':backups,'wal_archiver':pg,'performance':performance(s),
        'last_source_updates':s.rows('source_collections')[-30:],'source_errors':s.rows('source_failures')[-50:],
        'independent_host_durability_verified':False}


@router.get('/api/admin/reports/{report_id}',dependencies=[Depends(authorized)])
def trace_report(report_id:str):
    s=Store();r=s.get('professional_reports',report_id)
    if not r:raise HTTPException(404,'report not found')
    pid=r['prediction_id'];prediction=s.get('model_predictions',pid) if pid else None;fid=r['fixture_id']
    return {'report':r,'original_prediction':prediction,'feature_snapshot':s.get('feature_snapshots',pid) if pid else None,
        'model_version':s.get('model_versions',r['model_id']) if r['model_id'] else None,
        'market':s.get('market_snapshots',prediction['market_id']) if prediction and prediction.get('market_id') else None,
        'reviews':[x for x in s.rows('critical_reviews') if x['payload']['prediction_id']==pid],
        'original_result':s.get('actual_results',fid) if fid else None,'effective_result':effective_get(s,'actual_results',fid) if fid else None,
        'original_evaluation':s.get('prediction_evaluations',pid) if pid else None,
        'correction_evaluations':[x for x in s.rows('settlement_events') if x['payload'].get('prediction_id')==pid],
        'error_hypotheses':[x for x in s.rows('error_memory') if x['payload'].get('prediction_id')==pid],
        'revisions':[x for x in s.rows('record_revisions') if x['payload']['target_id'] in (fid,pid)]}
