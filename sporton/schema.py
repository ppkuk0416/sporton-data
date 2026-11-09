"""Versioned relational projections beside immutable JSON originals."""
from sqlalchemy import MetaData,Table,Column,String,Float,Integer,Boolean,ForeignKey,CheckConstraint,UniqueConstraint,DateTime

metadata=MetaData()
# Existing event entities are referenced without recreating their schema here.
for name in ('fixtures','model_versions','model_predictions','critical_reviews','professional_reports','source_observations','market_snapshots'):
    Table(name,metadata,Column('id',String,primary_key=True))

report_index=Table('report_index',metadata,
    Column('id',String,ForeignKey('professional_reports.id'),primary_key=True),
    Column('report_kind',String,nullable=False),Column('fixture_id',String,ForeignKey('fixtures.id')),
    Column('prediction_id',String,ForeignKey('model_predictions.id')),Column('model_id',String,ForeignKey('model_versions.id')),
    Column('review_id',String,ForeignKey('critical_reviews.id')),Column('created_at',DateTime(timezone=True),nullable=False),
    Column('data_mode',String,nullable=False),Column('original_hash',String,nullable=False),
    CheckConstraint("CAST(report_kind AS TEXT) IN (CAST('PRE_GAME' AS TEXT),CAST('LIVE' AS TEXT),CAST('POST_GAME' AS TEXT),CAST('AUDIT' AS TEXT),CAST('RESEARCH' AS TEXT))"),
    CheckConstraint("report_kind <> 'PRE_GAME' OR (fixture_id IS NOT NULL AND prediction_id IS NOT NULL AND model_id IS NOT NULL AND review_id IS NOT NULL)"),
    UniqueConstraint('report_kind','prediction_id'))

observation_index=Table('observation_index',metadata,
    Column('id',String,ForeignKey('source_observations.id'),primary_key=True),Column('league',String,nullable=False),
    Column('entity_id',String,nullable=False),Column('field',String,nullable=False),Column('source',String,nullable=False),
    Column('source_url',String,nullable=False),Column('collected_at',DateTime(timezone=True),nullable=False),
    Column('published_at',DateTime(timezone=True)),Column('status',String,nullable=False),Column('validator',String,nullable=False),
    Column('fixture_id',String,ForeignKey('fixtures.id')),Column('value_hash',String,nullable=False),
    CheckConstraint("CAST(status AS TEXT) IN (CAST('VERIFIED' AS TEXT),CAST('UNVERIFIED' AS TEXT),CAST('MISSING' AS TEXT),CAST('CONFLICTED' AS TEXT),CAST('TEST/FIXTURE' AS TEXT))"),
    CheckConstraint('published_at IS NULL OR published_at <= collected_at'))

COMMON=[lambda:Column('id',String,primary_key=True),lambda:Column('observation_id',String,ForeignKey('source_observations.id'),nullable=False),
    lambda:Column('league',String,nullable=False),lambda:Column('entity_id',String,nullable=False)]
SPORT_METRICS={
 'basketball':{'ortg':Float,'drtg':Float,'net_rtg':Float,'pace':Float,'efg_pct':Float,'ts_pct':Float,'three_par':Float,'ftr':Float,'oreb_pct':Float,'dreb_pct':Float,'tov_pct':Float,'usage_pct':Float,'on_off':Float,'possessions':Float,'lineup_id':String,'rotation':String,'period':Integer,'replacement_contribution':Float,'matchup':String},
 'baseball':{'starter_id':String,'fip':Float,'xfip':Float,'siera':Float,'k_bb_pct':Float,'xwoba':Float,'wrc_plus':Float,'exit_velocity':Float,'hard_hit_pct':Float,'pitch_type':String,'velocity':Float,'pitch_count':Integer,'bullpen_pitches':Integer,'inning':Integer,'runs_for':Integer,'runs_against':Integer,'park_factor':Float,'temperature_c':Float,'lineup_id':String},
 'football':{'xg':Float,'xga':Float,'xgd':Float,'ppda':Float,'pressing_efficiency':Float,'transition_efficiency':Float,'formation':String,'lineup_id':String,'player_position':String,'tactical_role':String,'home':Boolean,'rest_days':Float,'travel_km':Float,'minute_start':Integer,'minute_end':Integer,'goals':Integer},
 'volleyball':{'serve_points':Integer,'serve_errors':Integer,'receive_efficiency':Float,'blocks':Integer,'attack_efficiency':Float,'attack_errors':Integer,'set_number':Integer,'set_points':Integer,'rotation':String,'player_points':Integer},
 'esports':{'game_title':String,'patch_version':String,'roster_id':String,'map_name':String,'side':String,'champion_agent':String,'objectives':Integer,'teamfight_efficiency':Float,'series_format':String,'tournament':String}
}
sport_tables={sport:Table('sport_'+sport+'_statistics',metadata,*[c() for c in COMMON],
    *[Column(k,v) for k,v in metrics.items()]) for sport,metrics in SPORT_METRICS.items()}
TYPED_TABLES=[report_index,observation_index,*sport_tables.values()]


def initialize(conn):
    metadata.create_all(conn,tables=TYPED_TABLES)
    if conn.dialect.name=='postgresql':
        from sqlalchemy import text
        # Equivalent explicit TEXT casts survive pg_dump/restore without deparser cast redistribution.
        specs=[('report_index','report_index_report_kind_check',"CAST(report_kind AS TEXT) IN (CAST('PRE_GAME' AS TEXT),CAST('LIVE' AS TEXT),CAST('POST_GAME' AS TEXT),CAST('AUDIT' AS TEXT),CAST('RESEARCH' AS TEXT))"),
               ('observation_index','observation_index_status_check',"CAST(status AS TEXT) IN (CAST('VERIFIED' AS TEXT),CAST('UNVERIFIED' AS TEXT),CAST('MISSING' AS TEXT),CAST('CONFLICTED' AS TEXT),CAST('TEST/FIXTURE' AS TEXT))")]
        for table,name,expression in specs:
            definition=conn.execute(text('SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname=:name AND conrelid=CAST(:table AS regclass)'),{'name':name,'table':table}).scalar()
            if definition and 'character varying' in definition:
                conn.execute(text(f'ALTER TABLE {table} DROP CONSTRAINT {name}'))
                conn.execute(text(f'ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expression})'))


def install_postgres_report_gate(conn):
    from sqlalchemy import text
    if conn.dialect.name!='postgresql':return
    conn.execute(text("""
    CREATE OR REPLACE FUNCTION sporton_report_gate() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
    DECLARE p jsonb; f jsonb; prediction jsonb; review jsonb; model jsonb; blind jsonb; expected_hash text; start_at timestamptz;
    BEGIN
      p := NEW.payload::jsonb;
      IF p->>'kind' NOT IN ('PRE_GAME','LIVE','POST_GAME','AUDIT','RESEARCH') THEN RAISE EXCEPTION 'invalid report kind'; END IF;
      IF p->>'kind' IN ('PRE_GAME','LIVE','POST_GAME') THEN
        SELECT payload::jsonb INTO f FROM fixtures WHERE id=p->>'fixture_id';
        IF f IS NULL THEN RAISE EXCEPTION 'fixture missing'; END IF;
        SELECT COALESCE((SELECT payload::jsonb->'replacement' FROM record_revisions
          WHERE payload::jsonb->>'target_table'='fixtures' AND payload::jsonb->>'target_id'=p->>'fixture_id'
          ORDER BY (payload::jsonb->>'recorded_at')::timestamptz DESC LIMIT 1),f) INTO f;
        start_at := (f->>'starts_at')::timestamptz;
      END IF;
      IF p->>'kind'='PRE_GAME' THEN
        IF clock_timestamp() >= start_at THEN RAISE EXCEPTION 'PRE_GAME after actual start'; END IF;
        IF (p->>'created_at')::timestamptz > clock_timestamp()+interval '1 second'
           OR (p->>'created_at')::timestamptz < clock_timestamp()-interval '1 minute' THEN RAISE EXCEPTION 'report timestamp not current'; END IF;
        SELECT payload::jsonb,sha256 INTO prediction,expected_hash FROM model_predictions WHERE id=p->>'prediction_id';
        SELECT payload::jsonb INTO review FROM critical_reviews WHERE id=p->>'review_id';
        SELECT payload::jsonb INTO model FROM model_versions WHERE id=p->>'model_id';
        IF prediction IS NULL OR review IS NULL OR model IS NULL THEN RAISE EXCEPTION 'prediction/review/model missing'; END IF;
        IF review->>'verdict'<>'PASS' OR review->>'prediction_id'<>p->>'prediction_id' OR review->>'report_hash'<>expected_hash THEN RAISE EXCEPTION 'matching PASS required'; END IF;
        IF p->>'original_hash'<>expected_hash OR p->'content'->'original_prediction' IS DISTINCT FROM prediction THEN RAISE EXCEPTION 'original prediction mismatch'; END IF;
        IF prediction->'fixture' IS DISTINCT FROM f OR prediction->>'model_id'<>p->>'model_id' THEN RAISE EXCEPTION 'fixture/model mismatch'; END IF;
        IF (prediction->>'created_at')::timestamptz>=start_at OR (review->>'at')::timestamptz>=start_at THEN RAISE EXCEPTION 'late prediction/review'; END IF;
        SELECT payload::jsonb INTO blind FROM review_evidence WHERE id=review->>'evidence_id';
        IF blind IS NULL OR blind->>'fixture_id'<>p->>'fixture_id' OR (blind->>'created_at')::timestamptz>(prediction->>'created_at')::timestamptz THEN RAISE EXCEPTION 'blind evidence missing/late'; END IF;
        IF model->>'league'<>f->>'league' OR model->>'data_mode'<>p->>'data_mode' OR (model->>'trained_at')::timestamptz>(prediction->'snapshot'->>'feature_cutoff_timestamp')::timestamptz THEN RAISE EXCEPTION 'model mode/time mismatch'; END IF;
        IF NOT EXISTS (SELECT 1 FROM feature_snapshots WHERE id=p->>'prediction_id' AND payload::jsonb=prediction->'snapshot') THEN RAISE EXCEPTION 'feature lineage missing'; END IF;
      ELSIF p->>'kind'='LIVE' THEN
        IF clock_timestamp()<start_at OR EXISTS(SELECT 1 FROM actual_results WHERE id=p->>'fixture_id') THEN RAISE EXCEPTION 'invalid live phase'; END IF;
      ELSIF p->>'kind'='POST_GAME' THEN
        IF NOT EXISTS(SELECT 1 FROM actual_results WHERE id=p->>'fixture_id' AND (payload::jsonb->>'ends_at')::timestamptz<=clock_timestamp()
          AND (payload::jsonb->>'published_at')::timestamptz<=clock_timestamp()) THEN RAISE EXCEPTION 'confirmed final missing'; END IF;
      END IF;
      RETURN NEW;
    END; $$;
    DROP TRIGGER IF EXISTS report_gate ON professional_reports;
    CREATE TRIGGER report_gate BEFORE INSERT ON professional_reports FOR EACH ROW EXECUTE FUNCTION sporton_report_gate();
    CREATE OR REPLACE FUNCTION sporton_report_projection() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
    DECLARE p jsonb;
    BEGIN
      p := NEW.payload::jsonb;
      INSERT INTO report_index(id,report_kind,fixture_id,prediction_id,model_id,review_id,created_at,data_mode,original_hash)
      VALUES(NEW.id,p->>'kind',p->>'fixture_id',p->>'prediction_id',p->>'model_id',p->>'review_id',(p->>'created_at')::timestamptz,p->>'data_mode',p->>'original_hash');
      RETURN NEW;
    END; $$;
    DROP TRIGGER IF EXISTS report_projection ON professional_reports;
    CREATE TRIGGER report_projection AFTER INSERT ON professional_reports FOR EACH ROW EXECUTE FUNCTION sporton_report_projection();
    """))
