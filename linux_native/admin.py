"""Loopback administration; validated fixed config paths and masked secrets."""
import copy
import json
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from wxbot_framework.llm import LLMClient
from wxbot_framework.natural_settings import normalize_settings
from wxbot_framework.cloud_audio import ASRClient,tts_from_options
from .policy import build_system
from .image_client import DEFAULTS as IMAGE_DEFAULTS,normalize_image_config
from .ownership import member_choices,resolve_owner


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as out:
            json.dump(value,out,ensure_ascii=False,allow_nan=False);out.flush();os.fsync(out.fileno())
        os.chmod(name,0o600);os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


class RuntimeController:
    def __init__(self,state):
        self.state=Path(state)
        self.project_root=Path(__file__).resolve().parent.parent
        self.root=self.project_root/'linux_native/admin_assets'
        self.lock=threading.RLock()

    def read(self,name,default=None):
        path=self.state/name
        if not path.exists():return copy.deepcopy(default or {})
        return json.loads(path.read_text(encoding='utf-8-sig'))

    def chats(self):
        catalog=self.read('groups.json')
        ai=self.read('ai_config.json')
        if catalog.get('account')!=ai.get('account'):return []
        return catalog.get('groups',[])

    def configuration(self):
        with self.lock:
            ai=self.read('ai_config.json');saved=self.read('admin_config.json')
            rooms=list(ai.get('group_settings',{}))
            if saved:
                groups=copy.deepcopy(saved.get('groups',[]));personas=copy.deepcopy(saved.get('personas',{}))
            else:
                groups=[dict(row,id=room) for room,row in ai.get('group_settings',{}).items()]
                options=normalize_settings({'enabled':False,'personas':{},'group_settings':{},'persona_file':None},[],self.project_root)
                personas=options['personas']
            asr=self.read('asr_config.json',{'enabled':False,'provider':'dashscope','model':'qwen-audio-3.1-asr-flash-message'})
            for group in groups:group['asr_enabled']=group['id'] in asr.get('groups',[]) and asr.get('enabled') is True
            owners=member_choices(self.read('group_members.json'),ai.get('account'),[g['id'] for g in groups])
            model=self.read('llm_config.json')
            tts=self.read('tts_config.json',{'enabled':False,'provider':'minimax','model':'speech-2.8-hd','default_voice':'Chinese_sweet_girl_nv1'})
            image=self.read('image_config.json',IMAGE_DEFAULTS)
            if 'enabled' not in tts:tts['enabled']=bool(tts.get('api_key'))
            for value in (model,asr,tts,image):
                value['api_key_set']=bool(value.get('api_key'));value['api_key']=''
                # Saved provider profiles can contain other credentials; never expose them.
                value.pop('profiles',None)
            return {'account':ai.get('account',''),'enabled':ai.get('enabled',False),'draft_only':ai.get('draft_only',True),
                    'groups':groups,'personas':personas,'model':model,'asr':asr,'tts':tts,'image':image,'owner_members':owners}

    def _provider(self,new,filename):
        old=self.read(filename)
        if not isinstance(new,dict):raise ValueError('服务配置必须为对象')
        value={k:v for k,v in new.items() if k not in ('api_key_set','profiles')}
        default_provider='fish' if filename=='tts_config.json' else 'dashscope' if filename=='asr_config.json' else ''
        provider_changed=value.get('provider',default_provider)!=old.get('provider',default_provider)
        def endpoint(data):
            if filename=='asr_config.json':return data.get('websocket_url','wss://dashscope.aliyuncs.com/api-ws/v1/inference').rstrip('/')
            if filename=='image_config.json':return data.get('base_url','').rstrip('/')
            default='https://api.deepseek.com' if filename=='llm_config.json' else (
                'https://api.minimax.cn/v1' if data.get('provider','fish')=='minimax' else 'https://api.fish.audio')
            return data.get('base_url',default).rstrip('/')
        endpoint_changed=endpoint(value)!=endpoint(old)
        supplied=value.get('api_key','')
        if not supplied:
            if old.get('api_key') and (provider_changed or endpoint_changed):raise ValueError('更换服务商或地址时请重新填写对应密钥')
            value['api_key']=old.get('api_key','')
        return value

    def save_configuration(self,data):
        with self.lock:
            if not isinstance(data,dict):raise ValueError('配置必须为对象')
            if type(data.get('enabled')) is not bool or type(data.get('draft_only')) is not bool:raise ValueError('开关值无效')
            ai=self.read('ai_config.json');account=ai['account']
            if data.get('account',account)!=account:raise ValueError('不可通过配置切换绑定账号')
            catalog={row['id']:row for row in self.chats()}
            for room,row in ai.get('group_settings',{}).items():catalog.setdefault(room,dict(row,id=room))
            rows=data.get('groups',[])
            if not isinstance(rows,list) or len(rows)>1000:raise ValueError('群列表无效')
            groups={};asr_groups=[];owners={}
            candidates=member_choices(self.read('group_members.json'),account,catalog)
            for row in rows:
                if not isinstance(row,dict) or row.get('id') not in catalog or row['id'] in groups:raise ValueError('群未识别或重复，请先刷新群列表')
                room=row['id'];fields={k:row[k] for k in ('enabled','persona_id','cooldown_seconds','hourly_limit','reply_mode') if k in row}
                fields['name']=catalog[room]['name'];groups[room]=fields
                previous=ai.get('group_settings',{}).get(room,{})
                owners[room]=resolve_owner(row.get('owner_wxid',previous.get('owner_wxid','')),previous,candidates.get(room,[]),account)
                if row.get('asr_enabled') is True and row.get('enabled',True):asr_groups.append(room)
            options=normalize_settings({'enabled':data['enabled'],'dry_run':data['draft_only'],
                'personas':data.get('personas',{}),'group_settings':groups,'persona_file':None},list(groups),self.project_root)
            for room,binding in owners.items():options['group_settings'][room].update(binding)
            model=self._provider(data.get('model',{}),'llm_config.json')
            if model.get('api_key'):LLMClient(model)
            elif data['enabled']:raise ValueError('启用前请填写模型密钥')
            asr=self._provider(data.get('asr',{}),'asr_config.json');asr['groups']=asr_groups
            if asr.get('enabled') is True:ASRClient(**{k:asr[k] for k in ('api_key','model','websocket_url','timeout_seconds') if k in asr})
            tts=self._provider(data.get('tts',{}),'tts_config.json')
            if tts.get('enabled') is True:tts_from_options(tts)
            image=normalize_image_config(self._provider(data.get('image',self.read('image_config.json',IMAGE_DEFAULTS)),'image_config.json'))
            systems={room:build_system('' if row['persona_id']=='neutral' else options['personas'][row['persona_id']]['text'],row)
                     for room,row in options['group_settings'].items() if row['enabled']}
            next_ai=dict(ai,enabled=data['enabled'],draft_only=data['draft_only'],systems=systems,group_settings=options['group_settings'],
                         authorization_generation=int(ai.get('authorization_generation',0))+1)
            activation=dict(ai.get('activation_after',{}))
            for room in systems:
                if room not in ai.get('systems',{}):activation[room]=int(time.time())
            next_ai['activation_after']=activation
            saved={'groups':[dict(row,id=room) for room,row in options['group_settings'].items()],'personas':options['personas']}
            # Each service credentials/address pair is one atomic file. Scope+persona
            # changes commit last together, so no intermediate group mapping is exposed.
            for filename,value in (('llm_config.json',model),('asr_config.json',asr),('tts_config.json',tts),('image_config.json',image),('admin_config.json',saved),('ai_config.json',next_ai)):
                atomic_json(self.state/filename,value)
            return {'ok':True}

    def snapshot(self):
        ai=self.read('ai_config.json');result={'enabled':ai.get('enabled',False),'draft_only':ai.get('draft_only',True),
            'account':ai.get('account'),'groups':len(ai.get('systems',{})),'reply_states':{},'audio_states':{},'voice_states':{}}
        path=self.state/'inbox.sqlite'
        if path.exists():
            db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
            try:
                tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table,key in (('replies','reply_states'),('audio_inbox','audio_states'),('voice_replies','voice_states')):
                    if table in tables:result[key]=dict(db.execute('SELECT state,COUNT(*) FROM '+table+' GROUP BY state'))
                result['heartbeats']={name:float(value) for name,value in db.execute("SELECT name,value FROM metadata WHERE name IN ('receiver_heartbeat','ai_heartbeat')")}
            finally:db.close()
        result['image_states']={}
        image_path=self.state/'image_requests.sqlite'
        if image_path.exists():
            db=sqlite3.connect('file:'+str(image_path)+'?mode=ro',uri=True)
            try:result['image_states']=dict(db.execute('SELECT state,COUNT(*) FROM requests GROUP BY state'))
            finally:db.close()
        return result

    def start(self):
        value=self.configuration();value['enabled']=True;self.save_configuration(value)

    def stop(self):
        with self.lock:
            ai=self.read('ai_config.json');ai['enabled']=False
            ai['authorization_generation']=int(ai.get('authorization_generation',0))+1
            atomic_json(self.state/'ai_config.json',ai)

    def plugin_catalog(self):
        from .plugin_admin import PluginAdmin
        with self.lock:return PluginAdmin(self.state,self.project_root).catalog()

    def plugin_action(self,data):
        from .plugin_admin import PluginAdmin
        with self.lock:return PluginAdmin(self.state,self.project_root).action(data)

    def plugin_business(self,name):
        from .plugin_admin import PluginAdmin
        with self.lock:return PluginAdmin(self.state,self.project_root).business(name)

    def save_plugin_business(self,data):
        from .plugin_admin import PluginAdmin
        with self.lock:return PluginAdmin(self.state,self.project_root).save_business(data)

    def plugin_editor(self,name):
        from wxbot_framework.panel_common import PanelError
        raise PanelError('Linux 仅开放受限业务函数编辑',403)

    def save_plugin_source(self,*args,**kwargs):
        from wxbot_framework.panel_common import PanelError
        raise PanelError('Linux 仅开放受限业务函数编辑',403)

    def accounts(self):return [{'wxid':self.read('ai_config.json')['account'],'name':'Linux 机器人'}]


def main():
    import argparse
    from wxbot_framework.panel_http import PanelServer
    parser=argparse.ArgumentParser();parser.add_argument('--state',type=Path,required=True);parser.add_argument('--port',type=int,default=18767)
    args=parser.parse_args();os.umask(0o077)
    server=PanelServer(RuntimeController(args.state),args.port)
    try:server.serve_forever(poll_interval=.5)
    finally:server.server_close()


if __name__=='__main__':main()
