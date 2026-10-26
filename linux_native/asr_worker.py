"""Nonblocking cloud ASR worker; only original allowlisted voice IDs are read."""
import json
import hashlib
import threading
import time

from .inbox import DurableInbox


def configuration_guard(state,current):
    paths=[state/'ai_config.json',state/'asr_config.json']
    versions=[hashlib.sha256(path.read_bytes()).digest() for path in paths]
    def active():
        try:return current() and versions==[hashlib.sha256(path.read_bytes()).digest() for path in paths]
        except OSError:return False
    return active


def process_audio(box,read,decode,transcribe,*,active=lambda:True):
    message=box.claim_audio()
    if message is None:return None
    room,ident=message['chat_id'],message['server_id']
    try:
        if not active():raise ValueError('ASR cancelled')
        audio=read(message)
        if audio is None:raise ValueError('Voice data is not yet available')
        text=transcribe(decode(audio))
        if not active():raise ValueError('ASR cancelled')
        box.finish_audio(message,text)
        return {'state':'recognized','source_id':ident,'characters':len(text)}
    except Exception:
        box.fail_audio(message)
        raise


class RecognitionWorker:
    def __init__(self,state,root,account,media_keys,current):
        self.state,self.root,self.account=state,root,account
        self.media_keys=dict(media_keys);self.current=current
        self.thread=threading.Thread(target=self.run,name='LinuxASR',daemon=True)

    def start(self):self.thread.start()

    def run(self):
        from .voice_store import read_voice
        from wxbot_framework.voice_inbox import decode_silk
        from wxbot_framework.cloud_audio import ASRClient
        box=DurableInbox(self.state/'inbox.sqlite',self.account,[])
        with box.db:box.db.execute("UPDATE audio_inbox SET state='pending' WHERE state='processing'")
        try:
            while self.current():
                try:
                    active=configuration_guard(self.state,self.current)
                    options=json.loads((self.state/'asr_config.json').read_text())
                    ai=json.loads((self.state/'ai_config.json').read_text())
                    box.groups=frozenset(ai['systems'])
                    box.voice_groups=frozenset(options.get('groups',[])) if options.get('enabled') is True else frozenset()
                    if ai.get('enabled') is not True or not box.voice_groups:time.sleep(1);continue
                    client=ASRClient(**{name:options[name] for name in ('api_key','model','websocket_url','timeout_seconds') if name in options})
                    def read(message):
                        found=[]
                        for filename,key in self.media_keys.items():
                            data=read_voice(self.root/filename,key,message['chat_id'],message['server_id'])
                            if data is not None:found.append(data)
                        if len(found)>1:raise ValueError('Voice identity is ambiguous')
                        return found[0] if found else None
                    result=process_audio(box,read,decode_silk,lambda wav:client.transcribe(wav,is_current=active),active=active)
                    if result:print(json.dumps({'asr':result}),flush=True)
                except Exception as error:
                    print(json.dumps({'asr':'failed','error_type':type(error).__name__}),flush=True)
                time.sleep(1)
        finally:box.close()
