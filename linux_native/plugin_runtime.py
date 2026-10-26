"""Linux host for the original business plugins; never imports the Windows runtime."""
import hashlib
import json
import os
import sqlite3
import threading
from pathlib import Path
from types import MappingProxyType

from wxbot_framework.members import MemberStore
from wxbot_framework.plugins import PluginManager
from wxbot_framework.sdk import Message
from .plugin_jobs import PluginJobs
from .plugin_service import PluginService


MANAGED=frozenset({'natural_chat','mention_chat','personality','voice_reply'})


def _json(path,default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else (default or {})


def _atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    os.chmod(temporary,0o600);os.replace(temporary,path)


class PluginConfig:
    def __init__(self,path):self.path=path
    def load(self):return _json(self.path,{'generation':0,'plugins':{}})
    def set_enabled(self,name,enabled):
        options=self.load();options.setdefault('plugins',{}).setdefault(name,{})['enabled']=bool(enabled)
        _atomic(self.path,options);return options


def plugin_persona(state,options,room,sender=None):
    from .ownership import owner_policy,speaker_identity
    settings=options.get('group_settings',{}).get(room,{})
    identifier=settings.get('persona_id','neutral')
    value='' if identifier=='neutral' else _json(Path(state)/'admin_config.json').get('personas',{}).get(identifier,{}).get('text','')
    text=value if isinstance(value,str) and len(value)<=20000 else ''
    return text+owner_policy(settings)+'\n当前调用者身份（程序提供）：'+json.dumps({'current_speaker':speaker_identity(sender,settings)},ensure_ascii=False)


class GroupModel:
    def __init__(self,client,prompt,current):self.client=client;self.prompt=prompt;self.current=current
    def complete_json(self,system,user):
        if not self.current():raise RuntimeError('Group model configuration changed')
        result=self.client.complete_json('当前群表达要求：\n'+self.prompt+'\n功能与输出结构要求：\n'+system,user)
        if not self.current():raise RuntimeError('Group model configuration changed')
        return result


class PluginRuntime:
    def __init__(self,state,account,current=lambda:True):
        self.state=Path(state);self.state.mkdir(parents=True,exist_ok=True)
        self.account=account;self.current=current;self.options={};self.manager=None;self.members=None
        self.jobs=PluginJobs(self.state,account);self.closed=False;self.versions={};self.signature=None
        self.member_names_signature=None
        self.lock=threading.RLock()
        self.routes=sqlite3.connect(self.state/'plugin_routes.sqlite',check_same_thread=False)
        self.routes.execute('CREATE TABLE IF NOT EXISTS routes(account TEXT,room TEXT,source TEXT,claimed INTEGER,PRIMARY KEY(account,room,source))')
        self.routes.commit()

    def refresh(self,ai_options):
        with self.lock:
            self.options=dict(ai_options)
            if self.closed:return
            if ai_options.get('account',self.account)!=self.account:raise ValueError('Plugin runtime account cannot change')
            rooms=set(ai_options.get('systems',{}))
            members_path=self.state/'plugin_members.json'
            member_options={'database_file':'.runtime/plugin_members.sqlite3','history_enabled':True,'daily_clear':True,'groups':{r:[] for r in sorted(rooms)}}
            if not members_path.exists() or _json(members_path).get('groups')!=member_options['groups']:
                _atomic(members_path,member_options)
                if self.members is not None:self.members.reload_configuration()
            if self.members is None:
                self.members=MemberStore(members_path,self.account);self.members.initialize()
            member_cache=_json(self.state/'group_members.json')
            names_signature=hashlib.sha256(json.dumps(member_cache,sort_keys=True).encode()).hexdigest()
            if member_cache.get('account')==self.account and names_signature!=self.member_names_signature:
                for room,rows in member_cache.get('groups',{}).items():
                    if room not in rooms:continue
                    for row in rows:
                        nickname=row.get('alias') or row.get('nickname')
                        if nickname and len(nickname)<=64:self.members.remember_group_nickname(row['wxid'],room,nickname)
                self.member_names_signature=names_signature
            store=PluginConfig(self.state/'plugin_config.json');config=store.load()
            config.setdefault('plugins',{})
            capabilities=frozenset(_json(self.state/'capabilities.json').get('capabilities',[]))
            model=(self.state/'llm_config.json').read_bytes() if (self.state/'llm_config.json').exists() else b''
            signature=hashlib.sha256(model+json.dumps(ai_options.get('systems',{}),sort_keys=True).encode()).hexdigest()
            if self.manager is None:
                service=PluginService(self.state,self.account,self.jobs,lambda:not self.closed and self.current(),lambda:self.options,members=self.members)
                self.manager=PluginManager(Path(__file__).resolve().parent.parent/'plugins',store,service,capabilities,member_store=self.members)
            changed=self.signature!=signature or self.manager.capabilities!=capabilities
            self.manager.capabilities=capabilities;self.manager.config=config
            specs=self.manager.discover()
            for name in sorted(specs):
                options=config['plugins'].get(name,{})
                enabled=name not in MANAGED and options.get('enabled') is True and ai_options.get('enabled') is True
                if specs[name].requires-capabilities:
                    enabled=False
                version=self.jobs.plugin_version(self.state,name)
                if not enabled:
                    if name in self.manager.records:
                        try:self.manager.disable(name,persist=False)
                        except (Exception,SystemExit) as error:self.manager.load_errors[name]=type(error).__name__
                    self.versions.pop(name,None);continue
                if changed or self.versions.get(name)!=version or not self.manager.is_active(name):
                    try:
                        self.manager.load_one(name)
                        context=self.manager.records[name].context
                        def llm_for(message,*,personality=None,ctx=context):
                            prompt=self.options.get('systems',{}).get(message.chat_id,'')
                            return GroupModel(ctx.llm,plugin_persona(self.state,self.options,message.chat_id,message.sender_id),lambda:self.options.get('systems',{}).get(message.chat_id)==prompt and not self.closed and self.current())
                        context.llm_for=llm_for
                        self.versions[name]=version
                    except (Exception,SystemExit) as error:self.manager.load_errors[name]=type(error).__name__
            self.signature=signature
            self.publish()

    def _message(self,payload):
        room=payload.get('chat_id');source=str(payload.get('server_id',''))
        message_type=payload.get('type',1)
        if (room not in self.options.get('systems',{}) or not source.isascii() or not source.isdecimal() or int(source)<=0 or
                type(message_type) is not int or message_type not in (1,3,34,43,47,49,62,10000,10002)):
            return None
        data=dict(payload)
        data.setdefault('msg',payload.get('text',''))
        data.setdefault('room_wxid',room)
        data.setdefault('from_wxid',payload.get('sender_id',''))
        data.setdefault('native_message_id',source)
        data.setdefault('self_wxid',self.account)
        if message_type!=1:data.setdefault('raw_content',payload.get('text',''))
        return Message(text=payload.get('text','') if message_type==1 else '',chat_id=room,sender_id=payload.get('sender_id',''),type=message_type,
                       is_self=bool(payload.get('is_self') or payload.get('sender_id')==self.account),is_group=True,
                       sequence=int(source),session_id='linux:'+self.account,data=MappingProxyType(data))

    def warm_original(self,payload):
        """Restore only recall caches, FIFO before the actual recall event."""
        with self.lock:
            if self.closed or not self.current() or self.options.get('enabled') is not True or self.manager is None:return 0
            message=self._message(payload)
            if message is None or message.type in (10000,10002):return 0
            accepted=0
            for name in ('anti_recall','anti_recall_file','anti_recall_image'):
                record=self.manager.records.get(name)
                if not record or not record.active or message.type not in record.spec.message_types:continue
                checker=getattr(record.context._service,'accepts',None)
                if not callable(checker) or not checker(message):continue
                accepted+=int(record.enqueue(message) is not False)
            return accepted

    def route(self,payload):
        with self.lock:
            # Read config before each source; filesystem configuration changes need no restart.
            self.refresh(_json(self.state/'ai_config.json',self.options))
            room=payload.get('chat_id');source=str(payload.get('server_id',''))
            if self.closed or not self.current() or self.options.get('enabled') is not True or room not in self.options.get('systems',{}):return False
            if not source.isascii() or not source.isdecimal() or int(source)<=0:return False
            if payload.get('sender_id')==self.account or payload.get('is_self'):return False
            route_source=source+(':ready' if any(payload.get(k)=='ready' for k in ('file_status','image_status')) else '')
            row=self.routes.execute('SELECT claimed FROM routes WHERE account=? AND room=? AND source=?',(self.account,room,route_source)).fetchone()
            if row:return bool(row[0])
            message=self._message(payload)
            if message is None:return False
            # Reserve before any callback can run. An interrupted dispatch remains
            # conservatively claimed: never replay an ambiguous game opening.
            with self.routes:
                self.routes.execute('INSERT INTO routes VALUES(?,?,?,1)',(self.account,room,route_source))
            self.members.record(message)
            result=self.manager.dispatch(message)
            claimed=bool(result and result.get('claimed'))
            with self.routes:self.routes.execute('UPDATE routes SET claimed=? WHERE account=? AND room=? AND source=?',(int(claimed),self.account,room,route_source))
            self.publish();return claimed

    def publish(self):
        if self.manager is None:return
        rows=self.manager.status()
        for row in rows:
            row['enabled']=row['name'] not in MANAGED and self.manager.config.get('plugins',{}).get(row['name'],{}).get('enabled') is True
            if row['name'] in MANAGED:row['managed']=True
            if row.get('error'):row['error']='PluginError'
        _atomic(self.state/'plugin_status.json',{'account':self.account,'plugins':rows})

    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True
            if self.manager is not None:self.manager.shutdown()
            if self.members is not None:self.members.close()
            self.routes.close()
