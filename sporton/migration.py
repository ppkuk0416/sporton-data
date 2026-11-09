"""Idempotent, exact event migration; source files and timestamps are retained."""
from sqlalchemy import text,inspect
from .storage import ENTITIES,digest
from .schema import TYPED_TABLES
from .contracts import utcnow


def jsonable(value):
    from datetime import datetime,timezone
    from decimal import Decimal
    if isinstance(value,datetime) and value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
    if isinstance(value,Decimal):return str(value)
    if hasattr(value,'isoformat'):return value.isoformat()
    if isinstance(value,bytes):return value.hex()
    return value


def table_evidence(conn):
    out={}
    for name in sorted(inspect(conn).get_table_names()):
        rows=conn.execute(text(f'SELECT * FROM "{name}" ORDER BY id')).mappings().all()
        normalized=[{k:jsonable(v) for k,v in row.items()} for row in rows]
        normalized.sort(key=lambda r:str(r.get('id',digest(r))))
        out[name]={'count':len(rows),'sha256':digest(normalized)}
    return out


def migrate(source,target):
    result={'at':utcnow().isoformat(),'source_engine':source.engine.dialect.name,'target_engine':target.engine.dialect.name,'entities':{}}
    with source.engine.connect() as src, target.transaction() as dst:
        for table in ENTITIES:
            rows=src.execute(text(f'SELECT id,created_at,payload,sha256 FROM {table} ORDER BY id')).mappings().all()
            for row in rows:
                existing=dst.execute(text(f'SELECT id,created_at,payload,sha256 FROM {table} WHERE id=:id'),{'id':row['id']}).mappings().first()
                if existing:
                    if dict(existing)!=dict(row):raise ValueError(f'migration conflict {table}:{row["id"]}')
                else:dst.execute(text(f'INSERT INTO {table}(id,created_at,payload,sha256) VALUES(:id,:created_at,:payload,:sha256)'),dict(row))
            actual=dst.execute(text(f'SELECT id,created_at,payload,sha256 FROM {table} ORDER BY id')).mappings().all()
            expected={r['id']:dict(r) for r in rows};subset=[dict(r) for r in actual if r['id'] in expected]
            if {r['id']:r for r in subset}!=expected:raise ValueError('migration verification mismatch '+table)
            result['entities'][table]={'count':len(rows),'sha256':digest([dict(r) for r in rows])}
        for table in TYPED_TABLES:
            rows=src.execute(table.select()).mappings().all()
            for row in rows:
                exists=dst.execute(table.select().where(table.c.id==row['id'])).mappings().first()
                if not exists:dst.execute(table.insert().values(**dict(row)))
                elif {k:jsonable(v) for k,v in exists.items()}!={k:jsonable(v) for k,v in row.items()}:raise ValueError('typed migration conflict '+table.name)
    result['verified']=True
    return result
