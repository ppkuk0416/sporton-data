"""Private Telegram delivery. Credentials stay in owner-only files, never event payloads."""
import getpass
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx

from .contracts import utcnow
from .storage import home


class TelegramError(ValueError):
    pass


def private_read(path):
    p=Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_mode & 0o077:
        raise TelegramError('Telegram secret must be a private regular file (0600)')
    return p.read_text().strip()


def api(token,method,*,data=None,files=None,client=None):
    """Never propagate HTTP exceptions: Telegram URLs contain the bot token."""
    if not token or any(c.isspace() for c in token) or '/' in token:
        raise TelegramError('Invalid Telegram token format')
    try:
        if client is None:
            with httpx.Client(timeout=30,follow_redirects=False,trust_env=True) as c:
                return api(token,method,data=data,files=files,client=c)
        response=client.post('https://api.telegram.org/bot'+token+'/'+method,data=data,files=files)
        if response.status_code!=200:raise TelegramError('Telegram request rejected; HTTP '+str(response.status_code))
        body=response.json()
        if body.get('ok') is not True:raise TelegramError('Telegram API rejected request')
        return body['result']
    except TelegramError:raise
    except Exception:raise TelegramError('Telegram request failed; delivery may be uncertain') from None


def settings():
    direct_token=os.getenv('SPORTON_TELEGRAM_BOT_TOKEN','').strip()
    direct_chat=os.getenv('SPORTON_TELEGRAM_CHAT_ID','').strip()
    if direct_token and direct_chat:
        if not direct_chat.isdigit():raise TelegramError('Only an explicit private chat ID is supported')
        return direct_token,direct_chat
    path=Path(os.getenv('SPORTON_TELEGRAM_CONFIG',home()/'telegram.json'))
    if not path.exists():return None
    config=json.loads(path.read_text())
    token=os.getenv(config['token_env'],'').strip() if config.get('token_env') else private_read(config['token_file'])
    if not token:raise TelegramError('Telegram bot token secret is not available in this environment')
    chat=private_read(config['chat_id_file'])
    if not chat.isdigit():raise TelegramError('Only an explicit private chat ID is supported')
    return token,chat


def write_private(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as out:out.write(value)


def setup(client=None):
    """One local interactive command; discover only a unique private /start sender."""
    env_token=os.getenv('SPORTON_TELEGRAM_BOT_TOKEN','').strip()
    token=env_token or getpass.getpass('Telegram bot token (hidden): ').strip()
    api(token,'getMe',client=client)
    updates=api(token,'getUpdates',data={'timeout':0,'allowed_updates':'["message"]'},client=client)
    chats=set()
    for update in updates:
        message=update.get('message',{});chat=message.get('chat',{})
        if chat.get('type')=='private' and message.get('text','').split('@')[0].strip()=='/start':chats.add(str(chat['id']))
    if len(chats)!=1:raise TelegramError('Expected one private /start sender; send /start to the bot and retry. Multiple senders require an explicitly configured chat ID.')
    root=home().resolve()/'secrets';config_path=Path(os.getenv('SPORTON_TELEGRAM_CONFIG',home()/'telegram.json'))
    if config_path.exists():raise TelegramError('Telegram config already exists; preserve it or select a new SPORTON_TELEGRAM_CONFIG path')
    suffix=uuid4().hex;token_path=root/('telegram-'+suffix+'.token');chat_path=root/('telegram-'+suffix+'.chat')
    write_private(chat_path,chats.pop())
    binding={'chat_id_file':str(chat_path)}
    if env_token:binding['token_env']='SPORTON_TELEGRAM_BOT_TOKEN'
    else:
        write_private(token_path,token);binding['token_file']=str(token_path)
    write_private(config_path,json.dumps(binding))
    return {'status':'configured','config_file':str(config_path),'test_message_sent':False}


def send_test(client=None):
    config=settings()
    if not config:raise TelegramError('Telegram is not configured; run telegram-setup')
    result=api(config[0],'sendMessage',data={'chat_id':config[1],'text':'SPORTON 연결 테스트: 한국시간 매일 06:30까지 NBA 보고서 전달을 위한 테스트입니다. 정기 실행 상태는 별도 확인이 필요합니다.'},client=client)
    return {'status':'sent','message_id':result['message_id']}


def deliver(store,record,client=None):
    if record['data_mode']!='real':return {'status':'disabled_test_fixture'}
    key='telegram:sent:'+record['id']
    if store.get('briefing_events',key):return {'status':'already_sent'}
    config=settings()
    if not config:return {'status':'not_configured'}
    # Retry at most once per five minutes; successful deliveries survive restarts.
    failures=[r for r in store.rows('briefing_events') if r['payload'].get('action')=='telegram_failed' and r['payload'].get('briefing_id')==record['id']]
    if failures:
        from datetime import datetime
        if (utcnow()-datetime.fromisoformat(failures[-1]['payload']['at'])).total_seconds()<300:return {'status':'retry_wait'}
    try:
        result=api(config[0],'sendDocument',data={'chat_id':config[1],'caption':'SPORTON 데일리 브리핑 · '+record['id']},
            files={'document':('sporton-'+record['id']+'.md',record['markdown'].encode('utf-8'),'text/plain')},client=client)
    except TelegramError as exc:
        store.append('briefing_events',str(uuid4()),{'action':'telegram_failed','briefing_id':record['id'],'at':utcnow().isoformat(),'error':str(exc),'delivery_uncertain':True})
        return {'status':'failed'}
    store.append('briefing_events',key,{'action':'telegram_sent','briefing_id':record['id'],'at':utcnow().isoformat(),'message_id':result['message_id']},idempotent=True)
    return {'status':'sent'}
