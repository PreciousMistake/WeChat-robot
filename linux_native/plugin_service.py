"""Plugin SDK adapter: persist actions, then wait for the sole receiver's proof."""
import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager, nullcontext
from pathlib import Path

from wxbot_framework.sdk import CapabilityError


def system_hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else (default or {})


def member_guards(path, account, room):
    """Generation numbers only; message bodies never enter the delivery guard."""
    if not Path(path).is_file():return {}
    db=sqlite3.connect('file:'+Path(path).as_posix()+'?mode=ro',uri=True)
    try:
        result={'members_db':str(Path(path).resolve())}
        records=db.execute('SELECT local_day,generation FROM chat_buffers WHERE account=? AND room=?',(account,room)).fetchone()
        personality=db.execute('SELECT name,generation FROM room_personalities WHERE account=? AND room=?',(account,room)).fetchone()
        voice=db.execute('SELECT voice_id,generation,enabled FROM room_voices WHERE account=? AND room=?',(account,room)).fetchone()
        if records:result['records']={'day':records[0],'generation':records[1]}
        result['personality']={'name':personality[0],'generation':personality[1]} if personality else {'name':'默认','generation':0}
        result['voice']={'voice_id':voice[0],'generation':voice[1],'enabled':bool(voice[2])} if voice else {'voice_id':'','generation':0,'enabled':True}
        return result
    finally:db.close()


def persistent_guards_valid(state, account, room, guards):
    try:
        options=_read(Path(state)/'ai_config.json')
        if guards.get('account')!=account or guards.get('session')!='linux:'+account:return False
        if room not in options.get('systems',{}) or system_hash(options['systems'][room])!=guards.get('systems_hash'):return False
        model=Path(state)/'llm_config.json'
        if 'model_hash' in guards and hashlib.sha256(model.read_bytes() if model.exists() else b'').hexdigest()!=guards['model_hash']:return False
        if 'members_db' in guards:
            # Paths are framework-owned, never a job-provided arbitrary database.
            path=(Path(state)/'.runtime/plugin_members.sqlite3').resolve()
            if Path(guards['members_db']).resolve()!=path:return False
            latest=member_guards(path,account,room)
            if guards.get('records',{}).get('day') not in (None,datetime.now(timezone(timedelta(hours=8))).date().isoformat()):return False
            for key in ('records','personality','voice'):
                if key in guards and latest.get(key)!=guards[key]:return False
        return True
    except (OSError,ValueError,sqlite3.Error,KeyError,TypeError):return False


class PluginService:
    def __init__(self,state,account,jobs,current,options,*,members=None,name=None,version=None):
        self.state=Path(state);self.account=account;self.jobs=jobs;self.current=current
        self.options=options;self.members=members;self.name=name;self.version=version
        self.local=threading.local()
        model=self.state/'llm_config.json'
        self.model_hash=hashlib.sha256(model.read_bytes() if model.exists() else b'').hexdigest()

    def for_plugin(self,name,options):
        return PluginService(self.state,self.account,self.jobs,self.current,self.options,members=self.members,
                             name=name,version=self.jobs.plugin_version(self.state,name))

    @contextmanager
    def handling(self,message):
        previous=getattr(self.local,'context',None)
        self.local.context=[message,0]
        try:yield
        finally:self.local.context=previous

    def session_is_current(self,session_id):
        return bool(self.current() and session_id=='linux:'+self.account)

    def accepts(self,message):
        return bool(message.is_group and not message.is_self and self.session_is_current(message.session_id) and self._valid(message.chat_id))

    def _valid(self,room):
        try:
            options=_read(self.state/'ai_config.json',self.options())
            plugin_options=_read(self.state/'plugin_config.json').get('plugins',{}).get(self.name,{})
            scopes=plugin_options.get('groups')
            model=self.state/'llm_config.json'
            return bool(self.current() and options.get('enabled') is True and room in options.get('systems',{}) and
                        hashlib.sha256(model.read_bytes() if model.exists() else b'').hexdigest()==self.model_hash and
                        (scopes is None or isinstance(scopes,list) and room in scopes) and
                        self.jobs.plugin_version(self.state,self.name)==self.version and
                        plugin_options.get('enabled') is True)
        except (ValueError,OSError):return False

    def _send(self,room,kind,payload,*,expected_session=None,delivery_guard=None):
        context=getattr(self.local,'context',None)
        if context is None:raise RuntimeError('Plugin send requires an active source message')
        message,index=context
        if room!=message.chat_id:raise ValueError('Plugin actions must remain in their source group')
        if expected_session is not None and expected_session!=message.session_id:raise RuntimeError('Stale source session')
        if not self.session_is_current(message.session_id) or not self._valid(room):raise RuntimeError('Plugin configuration is no longer current')
        source=str(message.data.get('server_id') or message.sequence)
        if not source.isascii() or not source.isdecimal() or int(source)<=0:raise ValueError('Stable source message id required')
        with delivery_guard() if delivery_guard else nullcontext():
            guards={'account':self.account,'session':message.session_id,'systems_hash':system_hash(self.options()['systems'][room]),'model_hash':self.model_hash}
            if self.members is not None:guards.update(member_guards(self.members.path,self.account,room))
            job=self.jobs.enqueue(room,source,self.name,self.version,index,kind,payload,guards=guards)
            context[1]+=1
        # Never hold MemberStore/personality locks while the receiver sends.
        return self.jobs.wait(job,valid=lambda:self._valid(room),timeout=90)

    def send_text(self,room,text,*,expected_session=None,delivery_guard=None):
        if not isinstance(text,str) or not text or len(text.encode('utf-8'))>1023 or '\0' in text:raise ValueError('Text must contain 1–1023 UTF-8 bytes')
        return self._send(room,'text',{'text':text},expected_session=expected_session,delivery_guard=delivery_guard)

    def send_image(self,room,path,**kwargs):return self._send(room,'image',{'path':str(path)},**kwargs)
    def send_file(self,room,path,**kwargs):return self._send(room,'file',{'path':str(path)},**kwargs)
    def send_voice(self,room,path,**kwargs):return self._send(room,'voice',{'path':str(path)},**kwargs)
    def send_emoticon(self,room,path,**kwargs):return self._send(room,'emoticon',{'path':str(path)},**kwargs)
    def send_at_text(self,room,text,members,*,expected_session=None,display_names=None,delivery_guard=None):
        from wxbot_framework.text import validate_mentions
        validate_mentions(room,members)
        if not isinstance(text,str) or not text.strip() or len(text.encode('utf-8'))>1023:raise ValueError('Invalid mention body')
        return self._send(room,'at_text',{'text':text,'mentions':list(members)},expected_session=expected_session,delivery_guard=delivery_guard)
    def accept_friend(self,*args,**kwargs):raise CapabilityError('Linux friend acceptance is not available')
    def export_emoticons(self,*args,**kwargs):raise CapabilityError('Linux emoticon export is not available')
    def group_members(self,room,*,expected_session=None):
        if expected_session and not self.session_is_current(expected_session):raise RuntimeError('Stale source session')
        if not self._valid(room):raise ValueError('Group not enabled for this plugin')
        path=self.state/'group_members.json'
        if path.exists():
            snapshot=_read(path)
            if snapshot.get('account')!=self.account:raise ValueError('Member snapshot belongs to another account')
            rows=snapshot.get('groups',{}).get(room)
            if rows is not None:
                if not isinstance(rows,list):raise ValueError('Invalid group member snapshot')
                result=[]
                for item in rows:
                    if not isinstance(item,dict) or not isinstance(item.get('wxid'),str) or not item['wxid']:raise ValueError('Invalid group member identity')
                    row=dict(item)
                    if row.get('role') not in ('owner','admin','member'):row['role']='unknown'
                    member=self.members.find_by_wxid(row['wxid'],room) if self.members is not None else None
                    if member is not None and member.self_named:row['nickname']=member.display_name
                    result.append(row)
                return result
        if self.members is None:return None
        return [{'wxid':m.wxid,'nickname':m.display_name,'role':'unknown'} for m in self.members.list_members(room) if m.wxid]
    def group_member_name(self,room,wxid,**kwargs):
        return next((r.get('nickname') or r.get('alias') or r.get('wechat_name') for r in self.group_members(room,**kwargs) or () if r['wxid']==wxid),None)
    def group_member_role(self,room,wxid,**kwargs):
        return next((r['role'] for r in self.group_members(room,**kwargs) or () if r['wxid']==wxid),'unknown')
