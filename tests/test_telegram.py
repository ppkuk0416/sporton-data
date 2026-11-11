import json
import os
from datetime import timedelta

import httpx
import pytest

from sporton.contracts import utcnow
from sporton.telegram import TelegramError,api,deliver,setup,send_test,settings,write_private


@pytest.fixture(autouse=True)
def isolate_telegram_secrets(monkeypatch):
    monkeypatch.delenv('SPORTON_TELEGRAM_BOT_TOKEN',raising=False)
    monkeypatch.delenv('SPORTON_TELEGRAM_CHAT_ID',raising=False)


def configure(tmp_path,monkeypatch):
    token=tmp_path/'token';chat=tmp_path/'chat';config=tmp_path/'telegram.json'
    write_private(token,'123456:TEST_ONLY_SECRET');write_private(chat,'12345')
    config.write_text(json.dumps({'token_file':str(token),'chat_id_file':str(chat)}))
    monkeypatch.setenv('SPORTON_TELEGRAM_CONFIG',str(config))
    return token


def record():
    return {'id':'TEST-BRIEFING','data_mode':'real','markdown':'# TEST/FIXTURE\n'+'내용'*5000}


def test_delivery_document_survives_restart_and_not_sent_twice(store,tmp_path,monkeypatch):
    configure(tmp_path,monkeypatch);calls=[]
    def respond(req):
        calls.append(req)
        assert req.url.path.endswith('/sendDocument')
        assert 'multipart/form-data' in req.headers['content-type']
        assert record()['markdown'].encode() in req.content
        return httpx.Response(200,json={'ok':True,'result':{'message_id':123}})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert deliver(store,record(),client)['status']=='sent'
        from sporton.storage import Store
        reopened=Store(store.url)
        assert deliver(reopened,record(),client)['status']=='already_sent'
    assert len(calls)==1
    assert 'TEST_ONLY_SECRET' not in json.dumps(store.rows('briefing_events'))


def test_transport_error_redacted_and_retry_backoff(store,tmp_path,monkeypatch):
    configure(tmp_path,monkeypatch)
    def fail(req):raise httpx.ConnectError(str(req.url),request=req)
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        assert deliver(store,record(),client)['status']=='failed'
        assert deliver(store,record(),client)['status']=='retry_wait'
        assert not store.get('briefing_events','telegram:sent:TEST-BRIEFING')
        events=json.dumps(store.rows('briefing_events'))
        assert 'TEST_ONLY_SECRET' not in events and 'api.telegram.org/bot' not in events
        import sporton.telegram as telegram
        monkeypatch.setattr(telegram,'utcnow',lambda:utcnow()+timedelta(minutes=6))
        assert deliver(store,record(),client)['status']=='failed'


def test_unconfigured_and_fixtures_never_send(store,monkeypatch,tmp_path):
    monkeypatch.setenv('SPORTON_TELEGRAM_CONFIG',str(tmp_path/'absent'))
    assert deliver(store,record())['status']=='not_configured'
    assert deliver(store,{**record(),'data_mode':'TEST/FIXTURE'})['status']=='disabled_test_fixture'


def test_permissions_and_error_response_redaction(tmp_path,monkeypatch):
    token=configure(tmp_path,monkeypatch);token.chmod(0o644)
    with pytest.raises(TelegramError,match='private'):settings()
    token.chmod(0o600)
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(401,json={'ok':False,'description':'123456:TEST_ONLY_SECRET'}))) as client:
        with pytest.raises(TelegramError) as error:send_test(client)
    assert 'TEST_ONLY_SECRET' not in str(error.value)


def test_setup_discovers_only_unique_private_start(store,tmp_path,monkeypatch):
    monkeypatch.setattr('sporton.telegram.getpass.getpass',lambda _: '123456:TEST_ONLY_SECRET')
    def respond(req):
        if req.url.path.endswith('/getMe'):return httpx.Response(200,json={'ok':True,'result':{'id':123456}})
        assert req.url.path.endswith('/getUpdates')
        return httpx.Response(200,json={'ok':True,'result':[
            {'message':{'chat':{'id':-4,'type':'group'},'text':'/start'}},
            {'message':{'chat':{'id':12345,'type':'private'},'text':'/start'}}]})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert setup(client)['status']=='configured'
    assert settings()==('123456:TEST_ONLY_SECRET','12345')
    with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'ok':True,'result':{'message_id':1}}))) as client:
        assert send_test(client)['status']=='sent'


def test_setup_rejects_multiple_recipients(store,monkeypatch):
    monkeypatch.setattr('sporton.telegram.getpass.getpass',lambda _: '123456:TEST_ONLY_SECRET')
    def respond(req):
        result={} if req.url.path.endswith('/getMe') else [{'message':{'chat':{'id':i,'type':'private'},'text':'/start'}} for i in (12,34)]
        return httpx.Response(200,json={'ok':True,'result':result})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(TelegramError,match='one private'):setup(client)


def test_existing_briefing_retries_delivery_without_regeneration(store,monkeypatch):
    import sporton.briefing as briefing
    import sporton.telegram as telegram
    now=utcnow();key=briefing.slot_key(briefing.latest_slot(now))
    saved={**record(),'id':key}
    store.append('briefings',key,saved)
    calls=[]
    monkeypatch.setattr(briefing,'ensure_backup',lambda *args:None)
    monkeypatch.setattr(telegram,'deliver',lambda s,r:calls.append(r['id']))
    assert briefing.run_due(store) is None
    assert calls==[key]
    assert len(store.rows('briefings'))==1


def test_environment_secret_setup_does_not_prompt_or_persist_token(store,monkeypatch):
    monkeypatch.setenv('SPORTON_TELEGRAM_BOT_TOKEN','123456:TEST_ENV_SECRET')
    monkeypatch.setattr('sporton.telegram.getpass.getpass',lambda _:pytest.fail('must use registered environment secret'))
    def respond(req):
        result={} if req.url.path.endswith('/getMe') else [{'message':{'chat':{'id':123,'type':'private'},'text':'/start'}}]
        return httpx.Response(200,json={'ok':True,'result':result})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result=setup(client)
    config=json.loads(__import__('pathlib').Path(result['config_file']).read_text())
    assert config['token_env']=='SPORTON_TELEGRAM_BOT_TOKEN' and 'token_file' not in config
    assert settings()==('123456:TEST_ENV_SECRET','123')
    monkeypatch.delenv('SPORTON_TELEGRAM_BOT_TOKEN')
    with pytest.raises(TelegramError,match='not available'):settings()
