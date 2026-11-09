"""Read-only multi-sport dashboard; mutations are explicit CLI operations."""
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime
from fastapi import FastAPI,HTTPException
from fastapi.responses import HTMLResponse,PlainTextResponse
from .storage import Store
from .models import champion
from .engine import drift
from .sports import LEAGUES,sport_for,plugin

app=FastAPI(title='Sporton Multi-Sport Intelligence',version='0.3.0')


@app.get('/health')
def health():
    Store().rows('leagues')
    return {'status':'ok','timezone':'Asia/Seoul'}


@app.get('/',response_class=HTMLResponse)
def dashboard():return Path(__file__).with_name('dashboard.html').read_text()


@app.get('/api/capabilities')
def capabilities():
    result=[]
    s=Store()
    for league in LEAGUES:
        adapter=plugin(league) if league!='NBA' else None
        result.append({'league':league,'sport':sport_for(league),'implemented':True,
            'analyst':adapter.analyst.name if adapter else 'nba-team-form-v1',
            'feature_version':adapter.version if adapter else 'nba-rolling-v1',
            'outcomes':adapter.outcomes if adapter else ['away','home'],
            'scope':adapter.scope if adapter else 'including_overtime',
            'model_kinds':list(adapter.kinds) if adapter else ['elo','logistic','lightgbm'],
            'trained_models':sum(r['payload']['league']==league for r in s.rows('model_versions')),
            'verified_games':sum(r['payload']['league']==league and r['payload']['quality']=='verified' for r in s.rows('historical_game_results')),
            'optional_deep_learning_kind':'pytorch_mlp','deep_learning_flag':'--deep-learning',
            'generative_llm_connected':False,'live_performance_validated':False})
    return result


@app.get('/api/overview')
def overview(league:str='NBA'):
    import numpy as np
    if league not in LEAGUES:raise HTTPException(400,'unsupported league')
    s=Store();reports={r['id']:r['payload'] for r in s.rows('model_predictions')}
    def row_league(r):
        p=r['payload']
        return p.get('league') or reports.get(p.get('prediction_id'),{}).get('fixture',{}).get('league','NBA')
    def scoped(table):return [r for r in s.rows(table) if row_league(r)==league]
    evaluations=scoped('prediction_evaluations');production=[r['payload'] for r in evaluations if r['payload']['data_mode']=='real']
    versions=scoped('model_versions');fixtures=scoped('fixtures');games=scoped('historical_game_results')
    today=datetime.now(ZoneInfo('Asia/Seoul')).date()
    today_games=[r for r in fixtures if datetime.fromisoformat(r['payload']['starts_at']).astimezone(ZoneInfo('Asia/Seoul')).date()==today]
    bets=[r['market_settlement']['unit_profit'] for r in production if r.get('market_settlement') and r['market_settlement']['official_bet']]
    current=champion(s,league)
    return {'timezone':'Asia/Seoul','league':league,'sport':sport_for(league),'champion':current,
        'drift':drift(s,current) if current else {'warning':False,'status':'insufficient_data','n':0},
        'counts':{'verified_games':sum(r['payload']['quality']=='verified' for r in games),'test_games':sum(r['payload']['quality']=='TEST/FIXTURE' for r in games),
                  'predictions':sum(r['fixture']['league']==league for r in reports.values()),'audited_real':len(production)},
        'performance':{'accuracy':float(np.mean([r['correct'] for r in production])) if production else None,
            'brier':float(np.mean([r['brier'] for r in production])) if production else None,
            'score_mae':float(np.mean([r['score_mae'] for r in production])) if production else None,
            'unit_roi':float(np.mean(bets)) if bets else None,'bet_n':len(bets)},
        'today':today_games,'models':versions,'reviews':scoped('critical_reviews'),'decisions':scoped('official_recommendations'),
        'evaluations':evaluations,'failures':scoped('source_failures'),'errors':scoped('error_memory'),
        'updated_at':max([r['created_at'] for r in games],default=None),
        'scope_notice':'NBA / football / baseball use separate features, models and critical rules. Research validation does not establish live accuracy.'}


@app.get('/api/predictions/{prediction_id}')
def prediction(prediction_id:str):
    s=Store();report=s.get('model_predictions',prediction_id)
    if not report:raise HTTPException(404,'prediction not found')
    return {'report':report,'reviews':[r for r in s.rows('critical_reviews') if r['payload']['prediction_id']==prediction_id],
            'evaluation':s.get('prediction_evaluations',prediction_id)}


@app.get('/api/briefings')
def briefings():
    s=Store()
    return [{k:v for k,v in r['payload'].items() if k not in ('markdown','snapshot')} for r in reversed(s.rows('briefings')) if r['payload']['data_mode']=='real']


@app.get('/api/briefing-status')
def briefing_status():
    from .briefing import scheduler_status
    return scheduler_status()


@app.get('/api/briefings/{briefing_id}')
def briefing_detail(briefing_id:str):
    p=Store().get('briefings',briefing_id)
    if not p or p['data_mode']!='real':raise HTTPException(404,'briefing not found')
    return p


@app.get('/api/briefings/{briefing_id}/markdown',response_class=PlainTextResponse)
def briefing_markdown(briefing_id:str):
    return briefing_detail(briefing_id)['markdown']


@app.get('/api/data-sources')
def data_sources():
    from .collection import status
    return status(Store())


@app.get('/api/archive-research')
def archive_research():
    return [r for r in Store().rows('training_runs') if r['payload'].get('purpose')=='date_only_retrospective_research' and 'test' in r['payload']]


from .admin import router as admin_router
app.include_router(admin_router)
