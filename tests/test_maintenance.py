import hashlib
import sqlite3
import pytest
from sporton.maintenance import tick, backup
from conftest import make_games,write_csv


def test_inbox_repeatability_and_backup(store,tmp_path):
    inbox=tmp_path/'inbox';inbox.mkdir()
    write_csv(inbox/'TEST_FIXTURE.csv',make_games(5))
    assert len(tick(store,inbox,True)['imported'])==1
    assert tick(store,inbox,True)['imported']==[]
    (tmp_path/'raw').mkdir();(tmp_path/'raw'/'TEST-source.txt').write_text('TEST/FIXTURE source archive')
    manifest=backup(store,tmp_path/'backup')
    assert (tmp_path/'backup'/'raw'/'TEST-source.txt').read_text()=='TEST/FIXTURE source archive'
    for name,sha in manifest['files'].items():
        assert hashlib.sha256((tmp_path/'backup'/name).read_bytes()).hexdigest()==sha
    with sqlite3.connect(tmp_path/'backup'/'sporton.db') as connection:
        assert connection.execute('select count(*) from actual_results').fetchone()[0]==5
    with pytest.raises(ValueError,match='already exists'):backup(store,tmp_path/'backup')
