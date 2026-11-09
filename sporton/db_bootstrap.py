"""Owner-only schema setup; runtime apps receive SELECT/INSERT privileges."""
import os
from pathlib import Path
from sqlalchemy import make_url
from .storage import Store


def main():
    import psycopg
    from psycopg import sql
    selector=os.environ['SPORTON_OWNER_DATABASE_URL_FILE'];url=make_url(Path(selector).read_text().strip())
    Store(url,initialize=True)
    password=Path(os.environ['SPORTON_APP_PASSWORD_FILE']).read_text().strip()
    with psycopg.connect(host=url.host,port=url.port or 5432,dbname=url.database,user=url.username,password=url.password,
        **dict(url.query),autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='sporton_app'").fetchone():
            conn.execute(sql.SQL('CREATE ROLE sporton_app LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE').format(sql.Literal(password)))
        conn.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        conn.execute('GRANT USAGE ON SCHEMA public TO sporton_app')
        conn.execute('GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA public TO sporton_app')
        conn.execute(sql.SQL('ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public GRANT SELECT,INSERT ON TABLES TO sporton_app').format(sql.Identifier(url.username)))
    print('Schema initialized; app role limited to SELECT/INSERT; no credentials logged.')

if __name__=='__main__':main()
