"""Model consumer: shared decision parsing, private drafts, no UI side effects."""
import argparse
import json
import os
import signal
import threading
import time
from pathlib import Path

from wxbot_framework.natural_chat import Decision
from .inbox import DurableInbox
from .replies import ReplyQueue


def process_one(queue,client,systems,*,group_settings=None,router=None,image_requests=None,active=lambda:True):
    message=queue.claim()
    if message is None:return None
    room=message['chat_id'];ident=message['server_id']
    try:
        if room not in systems:raise ValueError('Group has no configured prompt')
        explicit=message['text'].strip().split(maxsplit=1)
        if image_requests is not None and explicit and explicit[0]=='/画图':
            if not active():queue.fail_model(message,retryable=True);return {'state':'cancelled','source_id':ident}
            reply=image_requests.submit(message,explicit[1],systems[room]) if len(explicit)==2 else '用法：/画图 图片描述；也可以直接对机器人说想画什么。'
            queue.stage(message,'direct',reply,media_mode='text',owner_wxid=(group_settings or {}).get(room,{}).get('owner_wxid',''))
            return {'state':'image_request','source_id':ident}
        from wxbot_framework.text import GROUP_CONTROL_COMMANDS,FOUR_CHINESE
        import unicodedata
        normalized=unicodedata.normalize('NFKC',message['text'].strip())
        game_word=bool(FOUR_CHINESE.fullmatch(normalized)) and not normalized.startswith(('画个','画一','画只','画张','画幅','画出','帮画','请画','生成','创作','设计','做张','做幅','绘制','来张','来幅'))
        route_first=(image_requests is None or not image_requests.enabled or message['text'].lstrip().startswith('/') or
            game_word or message['text'].strip() in GROUP_CONTROL_COMMANDS or message['text'].strip().lower()=='test')
        if route_first and router is not None and router(message) is True:
            queue.stage(message,'ignore','')
            return {'state':'plugin_claimed','source_id':ident}
        source_row=queue.db.execute('SELECT rowid FROM inbox WHERE room=? AND server_id=?',(room,ident)).fetchone()[0]
        rows=queue.db.execute('SELECT payload FROM inbox WHERE room=? AND rowid<=? ORDER BY rowid DESC LIMIT 12',(room,source_row)).fetchall()
        context=[]
        from .ownership import owner_policy,speaker_identity,guard_owner_reply
        owner_settings=(group_settings or {}).get(room,{})
        for row in reversed(rows):
            value=json.loads(row[0])
            context.append({'speaker':value['sender_id'],'text':value['text'][:500],'bot':False,
                            'is_owner':speaker_identity(value['sender_id'],owner_settings)['is_owner']})
            reply=queue.get(room,value['server_id'])
            if reply and reply['state']=='sent':context.append({'speaker':'机器人','text':reply['reply'],'bot':True})
        from .image_requests import IMAGE_POLICY,image_prompt
        binding=owner_policy(owner_settings)
        system=systems[room] if systems[room].endswith(binding) else systems[room]+binding
        result=client.complete_json(system+(IMAGE_POLICY if image_requests is not None else ''),json.dumps({'messages':context[-12:],'current_at_bot':message.get('is_at_self',False),
            'current_speaker':speaker_identity(message['sender_id'],owner_settings)},ensure_ascii=False))
        if not active():
            queue.fail_model(message,retryable=True)
            return {'state':'cancelled','source_id':ident}
        newer=queue.db.execute('SELECT 1 FROM inbox WHERE room=? AND rowid>? LIMIT 1',(room,source_row)).fetchone()
        prompt=image_prompt(result) if image_requests is not None and not newer else None
        if prompt is not None:
            reply=image_requests.submit(message,prompt,systems[room])
            queue.stage(message,'direct',reply,media_mode='text',owner_wxid=(group_settings or {}).get(room,{}).get('owner_wxid',''))
            return {'state':'image_request','source_id':ident}
        if not route_first and router is not None and router(message) is True:
            queue.stage(message,'ignore','')
            return {'state':'plugin_claimed','source_id':ident}
        decision=Decision('ignore','') if newer else Decision.parse(result)
        if decision.kind!='ignore':decision=Decision(decision.kind,guard_owner_reply(decision.reply,message['sender_id'],owner_settings))
        if newer:decision=Decision('ignore','')
        from .voice_replies import reply_mode
        mode=reply_mode((group_settings or {}).get(room,{}).get('reply_mode','text'),message)
        queue.stage(message,decision.kind,decision.reply,media_mode=mode,owner_wxid=owner_settings.get('owner_wxid',''))
        return {'state':'ignored' if decision.kind=='ignore' else 'draft','decision':decision.kind,'source_id':ident}
    except Exception as error:
        from wxbot_framework.llm import LLMError
        retryable=isinstance(error,LLMError) and any(marker in str(error) for marker in (
            'HTTP 5','HTTP 429','连接失败或超时','空结果','响应格式不正确'))
        queue.fail_model(message,retryable=retryable)
        raise


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    stopping=[False]
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stopping.__setitem__(0,True))
    if not args.once:
        def parent_watch():
            sys.stdin.readline();stopping[0]=True
            # Network calls cannot be interrupted cleanly; bound orphan lifetime.
            timer=threading.Timer(10,lambda:os._exit(0));timer.daemon=True;timer.start()
        import sys
        threading.Thread(target=parent_watch,daemon=True).start()
    state=Path.home()/'.local/share/wechat-access'
    import fcntl
    lock=(state/'ai.lock').open('a+b');fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    options=json.loads((state/'ai_config.json').read_text())
    box=DurableInbox(state/'inbox.sqlite',options['account'],options['systems'])
    queue=ReplyQueue(box);queue.recover(delivery=False)
    from wxbot_framework.llm import LLMClient
    from .plugin_runtime import PluginRuntime
    from .plugin_events import EventStore
    plugins=PluginRuntime(state,options['account'],current=lambda:not stopping[0])
    from .image_requests import ImageRequests
    images=ImageRequests(state,options['account'])
    images.start(active=lambda:not stopping[0])
    events=EventStore(state,options['account'])
    try:
        while not stopping[0]:
            options=json.loads((state/'ai_config.json').read_text())
            box.groups=frozenset(options['systems'])
            plugins.refresh(options)
            with box.db:box.db.execute("INSERT INTO metadata VALUES('ai_heartbeat',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(str(time.time()),))
            if options.get('enabled') is not True:
                if args.once:break
                time.sleep(2);continue
            for event in events.pending(30):
                payload=event['payload']
                if payload.get('chat_id') in box.groups:
                    if payload.get('recall'):
                        original_id=str(payload['recall'].get('message_id',''))
                        for original in events.originals():
                            if original.get('chat_id')==payload['chat_id'] and str(original.get('server_id'))==original_id:plugins.warm_original(original);break
                    plugins.route(payload)
                events.finish(event['id'])
            # Reload each job so a model/key change needs no receiver restart.
            ai_version=(state/'ai_config.json').stat().st_mtime_ns
            model_version=(state/'llm_config.json').stat().st_mtime_ns
            client=LLMClient.from_file(state/'llm_config.json')
            try:
                result=process_one(queue,client,options['systems'],group_settings=options.get('group_settings'),router=plugins.route,image_requests=images,active=lambda:not stopping[0] and
                    (state/'ai_config.json').stat().st_mtime_ns==ai_version and
                    (state/'llm_config.json').stat().st_mtime_ns==model_version)
                if result:print(json.dumps(result),flush=True)
                from .voice_replies import VoiceReplies
                from .asr_worker import configuration_guard
                guard=configuration_guard(state,lambda:not stopping[0])
                tts_version=(state/'tts_config.json').read_bytes()
                result=VoiceReplies(queue).synthesize_one(state,lambda:guard() and (state/'tts_config.json').read_bytes()==tts_version)
                if result:print(json.dumps(result),flush=True)
            except Exception as error:print(json.dumps({'draft':'failed','error_type':type(error).__name__}),flush=True)
            if args.once:break
            time.sleep(5)
    finally:plugins.close();events.close();box.close();lock.close()


if __name__=='__main__':main()
