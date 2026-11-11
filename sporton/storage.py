"""Append-only event store. Updates/deletes rejected at the database layer."""
import hashlib
import json
import os
from pathlib import Path
from sqlalchemy import create_engine, event, text, make_url
from sqlalchemy.exc import IntegrityError
from .contracts import utcnow

ENTITIES = '''sports leagues teams players seasons fixtures lineups injuries team_statistics
player_statistics historical_game_results historical_market_odds market_snapshots feature_snapshots
model_versions model_predictions analyst_reports critical_reviews official_recommendations
actual_results prediction_evaluations error_memory training_runs model_promotions
raw_data source_failures review_evidence briefings briefing_notes briefing_events data_sources source_collections archive_datasets record_revisions validation_events source_observations identity_aliases professional_reports market_selections forecast_components settlement_events backup_runs restore_tests lake_exports access_events schema_versions job_executions data_quality_logs learning_feedback'''.split()


def canonical(payload):
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(payload):
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def home():
    path = Path(os.getenv('SPORTON_HOME', 'var'))
    path.mkdir(parents=True, exist_ok=True)
    return path


class Store:
    def __init__(self, url=None, initialize=None):
        selector=os.getenv('SPORTON_DATABASE_URL_FILE')
        self.url = url or (Path(selector).read_text().strip() if selector else None) or os.getenv('SPORTON_DATABASE_URL') or f'sqlite:///{home() / "sporton.db"}'
        self.engine = create_engine(self.url,pool_pre_ping=True)
        if os.getenv('SPORTON_ENV')=='production':
            if self.engine.dialect.name!='postgresql':raise ValueError('production requires PostgreSQL')
            if make_url(self.url).query.get('sslmode')!='verify-full':raise ValueError('production PostgreSQL requires sslmode=verify-full')
        if self.engine.dialect.name == 'sqlite':
            @event.listens_for(self.engine, 'connect')
            def configure(connection, _):
                connection.execute('PRAGMA foreign_keys=ON')
                connection.execute('PRAGMA busy_timeout=10000')
                connection.execute('PRAGMA journal_mode=WAL')
        if initialize is None:initialize=self.engine.dialect.name=='sqlite'
        if initialize:self._init()

    def _init(self):
        with self.engine.begin() as conn:
            for name in ENTITIES:
                conn.execute(text(f'CREATE TABLE IF NOT EXISTS {name} (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL)'))
                if self.engine.dialect.name == 'sqlite':
                    for operation in ('UPDATE', 'DELETE'):
                        conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS immutable_{name}_{operation} BEFORE {operation} ON {name} BEGIN SELECT RAISE(ABORT, 'append-only'); END"))
            if self.engine.dialect.name=='sqlite':
                conn.execute(text("CREATE INDEX IF NOT EXISTS raw_source_lookup ON raw_data(json_extract(payload, '$.source_url'), created_at)"))
            from .schema import initialize,TYPED_TABLES
            initialize(conn)
            if self.engine.dialect.name == 'sqlite':
                for table in TYPED_TABLES:
                    for operation in ('UPDATE','DELETE'):
                        conn.execute(text(f"CREATE TRIGGER IF NOT EXISTS immutable_{table.name}_{operation} BEFORE {operation} ON {table.name} BEGIN SELECT RAISE(ABORT, 'append-only'); END"))
            if self.engine.dialect.name == 'postgresql':
                conn.execute(text("CREATE OR REPLACE FUNCTION sporton_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'append-only'; END; $$"))
                for name in ENTITIES+[t.name for t in TYPED_TABLES]:
                    conn.execute(text(f'DROP TRIGGER IF EXISTS immutable_{name} ON {name}'))
                    conn.execute(text(f'CREATE TRIGGER immutable_{name} BEFORE UPDATE OR DELETE OR TRUNCATE ON {name} FOR EACH STATEMENT EXECUTE FUNCTION sporton_immutable()'))
                from .schema import install_postgres_report_gate
                install_postgres_report_gate(conn)

    def transaction(self):
        return self.engine.begin()

    def append(self, table, key, payload, idempotent=False, conn=None):
        if table not in ENTITIES:raise ValueError('unknown entity')
        encoded=canonical(payload)
        def insert(c):
            clause=' ON CONFLICT(id) DO NOTHING' if idempotent else ''
            result=c.execute(text(f'INSERT INTO {table} (id, created_at, payload, sha256) VALUES (:id, :at, :p, :h)'+clause),
                {'id':str(key),'at':utcnow().isoformat(),'p':encoded,'h':digest(payload)})
            if idempotent and result.rowcount==0 and self.get(table,key,conn=c)!=payload:
                raise ValueError(f'duplicate/conflicting {table}: {key}')
        try:
            if conn is not None:insert(conn)
            else:
                with self.engine.begin() as c:insert(c)
        except IntegrityError:
            raise ValueError(f'duplicate/conflicting {table}: {key}') from None
        return str(key)

    def get(self,table,key,conn=None):
        if table not in ENTITIES:raise ValueError('unknown entity')
        def query(c):return c.execute(text(f'SELECT payload,sha256 FROM {table} WHERE id=:id'),{'id':str(key)}).mappings().first()
        if conn is not None:row=query(conn)
        else:
            with self.engine.connect() as c:row=query(c)
        if row is None:return None
        p=json.loads(row['payload'])
        if digest(p)!=row['sha256']:raise ValueError('integrity hash mismatch')
        return p

    def rows(self,table,conn=None):
        if table not in ENTITIES:raise ValueError('unknown entity')
        def query(c):return c.execute(text(f'SELECT id, created_at, payload, sha256 FROM {table} ORDER BY created_at, id')).mappings().all()
        if conn is not None:rows=query(conn)
        else:
            with self.engine.connect() as c:rows=query(c)
        out=[]
        for row in rows:
            p=json.loads(row['payload'])
            if digest(p)!=row['sha256']:raise ValueError('integrity hash mismatch')
            out.append({'id':row['id'],'created_at':row['created_at'],'payload':p})
        return out


    def latest_source(self,url):
        """Find one source snapshot without decoding the entire historical archive."""
        expression="json_extract(payload, '$.source_url')" if self.engine.dialect.name=='sqlite' else "CAST(payload AS jsonb)->>'source_url'"
        with self.engine.connect() as conn:
            row=conn.execute(text('SELECT id FROM raw_data WHERE '+expression+'=:url ORDER BY created_at DESC,id DESC LIMIT 1'),{'url':url}).first()
            return {'id':row[0],'payload':self.get('raw_data',row[0],conn=conn)} if row else None
