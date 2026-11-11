import argparse
import json
from pathlib import Path
from .contracts import Fixture
from .data import ingest, collect_nba, add_market
from .engine import forecast, audit, daily
from .models import train, walk_forward, promote, rollback
from .storage import Store
from .sports import LEAGUES
from .sports.contracts import parse_fixture


def main():
    parser=argparse.ArgumentParser(description='Sporton multi-sport research. Test fixtures must use a separate SPORTON_HOME/database.')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('init')
    sub.add_parser('telegram-setup')
    sub.add_parser('telegram-test')
    p=sub.add_parser('secure-backup');p.add_argument('--policy')
    p=sub.add_parser('restore-test');p.add_argument('backup_id');p.add_argument('--policy')
    p=sub.add_parser('persistence-worker');p.add_argument('--policy');p.add_argument('--once',action='store_true')
    p=sub.add_parser('lake-export');p.add_argument('--dataset');p.add_argument('--entity')
    sub.add_parser('lake-summary')
    p=sub.add_parser('revise');p.add_argument('path',type=Path)
    p=sub.add_parser('observe');p.add_argument('path',type=Path)
    p=sub.add_parser('statistics');p.add_argument('path',type=Path)
    p=sub.add_parser('collect'); p.add_argument('--source',action='append'); p.add_argument('--force',action='store_true')
    sub.add_parser('archive-research')
    p=sub.add_parser('briefing'); p.add_argument('--test-mode',action='store_true'); p.add_argument('--inbox',default=None); p.add_argument('--no-refresh',action='store_true')
    p=sub.add_parser('briefing-daemon'); p.add_argument('--test-mode',action='store_true'); p.add_argument('--inbox',default=None); p.add_argument('--once',action='store_true'); p.add_argument('--retrain',action='store_true')
    p=sub.add_parser('briefing-note'); p.add_argument('text')
    for name in ('ingest','market','injury','context','fixture','forecast'):
        p=sub.add_parser(name); p.add_argument('path',type=Path)
        if name!='fixture': p.add_argument('--test-mode',action='store_true')
        if name=='forecast':
            p.add_argument('--model'); p.add_argument('--market-id')
    for name in ('train','backtest','daily'):
        p=sub.add_parser(name); p.add_argument('--test-mode',action='store_true'); p.add_argument('--league',choices=LEAGUES,default='NBA')
        if name in ('train','backtest','daily'):p.add_argument('--deep-learning',action='store_true')
        if name=='daily': p.add_argument('--retrain',action='store_true')
    p=sub.add_parser('collect-nba'); p.add_argument('--seasons',nargs='+',default=['2023-24','2024-25','2025-26'])
    p=sub.add_parser('promote'); p.add_argument('model_id')
    p=sub.add_parser('rollback'); p.add_argument('--league',choices=LEAGUES,default='NBA'); sub.add_parser('audit')
    p=sub.add_parser('backup'); p.add_argument('destination')
    p=sub.add_parser('worker'); p.add_argument('--league',choices=LEAGUES,default='NBA'); p.add_argument('--inbox',default='var/inbox'); p.add_argument('--test-mode',action='store_true'); p.add_argument('--retrain',action='store_true'); p.add_argument('--once',action='store_true'); p.add_argument('--interval',type=int,default=3600)
    p=sub.add_parser('serve'); p.add_argument('--host',default='127.0.0.1'); p.add_argument('--port',type=int,default=8000)
    args=parser.parse_args(); store=Store(initialize=args.command=='init' or None); result=None
    try:
        if args.command=='init': result={'status':'initialized','database':store.engine.dialect.name}
        elif args.command in ('telegram-setup','telegram-test'):
            from .telegram import setup,send_test
            result=setup() if args.command=='telegram-setup' else send_test()
        elif args.command in ('secure-backup','restore-test','persistence-worker'):
            from .durability import Policy,create_backup,restore_test,periodic,archive_wal,retention
            policy=Policy.load(args.policy)
            if args.command=='secure-backup':result=create_backup(store,policy)
            elif args.command=='restore-test':result=restore_test(store,args.backup_id,policy)
            else:
                import time
                while True:
                    try:result={'backup':periodic(store,policy),'wal':archive_wal(store,policy),'retention':retention(store,policy,apply=True)}
                    except Exception as exc:result={'status':'FAILED','error':str(exc)}
                    print(json.dumps(result,ensure_ascii=False),flush=True)
                    if args.once:return
                    time.sleep(60)
        elif args.command=='lake-export':
            from .lake import export_archive,export_events
            if bool(args.dataset)==bool(args.entity):raise ValueError('choose exactly one --dataset/--entity')
            result=export_archive(store,args.dataset) if args.dataset else export_events(store,args.entity)
        elif args.command=='lake-summary':
            from .lake import summary
            result=summary(store)
        elif args.command=='revise':
            from .governance import append_revision
            result={'revision_id':append_revision(store,**json.loads(args.path.read_text()))}
        elif args.command=='observe':
            from .governance import record_observation
            key,state=record_observation(store,json.loads(args.path.read_text()));result={'observation_id':key,**state}
        elif args.command=='statistics':
            from .governance import record_statistics
            key,state=record_statistics(store,**json.loads(args.path.read_text()));result={'statistics_id':key,**state}
        elif args.command=='collect':
            from .collection import collect_due
            result=collect_due(store,args.force,args.source)
        elif args.command=='archive-research':
            from .archive_research import run_epl_research
            result=run_epl_research(store)
        elif args.command=='briefing':
            from .briefing import create_briefing,reconcile_markdown
            report=create_briefing(store,test_mode=args.test_mode,refresh=not args.no_refresh,inbox=args.inbox)
            result={'briefing_id':report['id'],'markdown_path':reconcile_markdown(report),'generated_at':report['generated_at']}
        elif args.command=='briefing-daemon':
            from .briefing import daemon
            daemon(store,args.inbox,args.test_mode,args.once,args.retrain)
            return
        elif args.command=='briefing-note':
            from .briefing import add_note
            result={'note_id':add_note(store,args.text)}
        elif args.command=='ingest': result={'ingested':ingest(store,args.path,args.test_mode)}
        elif args.command=='collect-nba': result=collect_nba(store,args.seasons)
        elif args.command=='market': result={'market_id':add_market(store,json.loads(args.path.read_text()),args.test_mode)}
        elif args.command=='injury':
            from .data import add_injury
            result={'injury_id':add_injury(store,json.loads(args.path.read_text()),args.test_mode)}
        elif args.command=='context':
            from .data import add_baseball_context
            result={'context_id':add_baseball_context(store,json.loads(args.path.read_text()),args.test_mode)}
        elif args.command=='fixture':
            f=parse_fixture(json.loads(args.path.read_text())); result={'fixture_id':store.append('fixtures',f.game_id,f.model_dump(mode='json'),idempotent=True)}
        elif args.command=='train':
            from .sports.lifecycle import train_sport
            result=train(store,args.test_mode,args.deep_learning) if args.league=='NBA' else train_sport(store,args.league,args.test_mode,args.deep_learning)
        elif args.command=='backtest':
            from .sports.lifecycle import backtest_sport
            result=walk_forward(store,args.test_mode,args.deep_learning) if args.league=='NBA' else backtest_sport(store,args.league,args.test_mode,args.deep_learning)
        elif args.command=='forecast': result=forecast(store,json.loads(args.path.read_text()),args.model,market_id=args.market_id,test_mode=args.test_mode)
        elif args.command=='audit': result=audit(store)
        elif args.command=='daily': result=daily(store,args.test_mode,args.retrain,args.league,args.deep_learning)
        elif args.command=='promote': result={'champion':promote(store,args.model_id)}
        elif args.command=='rollback': result={'champion':rollback(store,args.league)}
        elif args.command=='backup':
            from .maintenance import backup
            result=backup(store,args.destination)
        elif args.command=='worker':
            from .maintenance import tick
            import time
            if args.interval<60: raise ValueError('worker interval must be >=60 seconds')
            while True:
                result=tick(store,args.inbox,args.test_mode,args.retrain,args.league)
                if args.once: break
                print(json.dumps(result,ensure_ascii=False),flush=True)
                time.sleep(args.interval)
        elif args.command=='serve':
            import uvicorn
            uvicorn.run('sporton.api:app',host=args.host,port=args.port)
            return
        print(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False))
        if args.command=='collect' and any(r['status']=='failed' for r in result):raise SystemExit(1)
        if args.command=='collect-nba' and any(r['status']=='failed' for r in result):
            raise SystemExit(1)
    except (ValueError,FileNotFoundError) as exc:
        parser.exit(2,f'ERROR: {exc}\n')

if __name__=='__main__': main()
