"""Complete plugin catalogue, verified-capability gating, restricted business editor."""
import copy
import hashlib
import json
import time
from pathlib import Path
from wxbot_framework.config import validate_name
from wxbot_framework.plugin_sources import PluginSources
from wxbot_framework.plugin_business import view,merge
from wxbot_framework.panel_common import PanelError
from .admin import atomic_json

MANAGED={'natural_chat':'Linux 自然聊天','mention_chat':'Linux 自然聊天','personality':'群与人格','voice_reply':'模型与语音'}
SECRET_KEYS={'api_key','password','token','secret','authorization','ticket'}


def public_options(value):
    if isinstance(value,dict):return {k:('' if k.casefold() in SECRET_KEYS else public_options(v)) for k,v in value.items()}
    if isinstance(value,list):return [public_options(v) for v in value]
    return value


def restore_options(new,old):
    if not isinstance(new,dict):raise ValueError('插件配置应为 JSON 对象')
    result=copy.deepcopy(new)
    for key,value in result.items():
        if key.casefold() in SECRET_KEYS and not value and old.get(key):result[key]=old[key]
        elif isinstance(value,dict):result[key]=restore_options(value,old.get(key,{}) if isinstance(old.get(key),dict) else {})
    return result


class PluginAdmin:
    def __init__(self,state,project):
        self.state=Path(state);self.project=Path(project);self.sources=PluginSources(self.project/'plugins',self.state)

    def read(self,name,default=None):
        path=self.state/name
        return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else copy.deepcopy(default or {})

    def specs(self):
        result={}
        for path in sorted((self.project/'plugins').glob('*/plugin.json')):
            if path.parent.name.startswith('_'):continue
            name=validate_name(path.parent.name)
            result[name]=json.loads((self.sources.path(name)/'plugin.json').read_text(encoding='utf-8-sig'))
        return result

    def options(self,name,config):
        value=copy.deepcopy(config.get('plugins',{}).get(name))
        if value is None:
            try:value=copy.deepcopy(self.sources.defaults(name))
            except (OSError,ValueError):value={}
            value['enabled']=False
        return value

    def catalog(self):
        config=self.read('plugin_config.json',{'generation':0,'plugins':{}})
        capabilities=set(self.read('capabilities.json').get('capabilities',[]));ai=self.read('ai_config.json')
        status=self.read('plugin_status.json');path=self.state/'plugin_status.json'
        running=status.get('account')==ai.get('account') and path.exists() and time.time()-path.stat().st_mtime<30
        live={r['name']:r for r in status.get('plugins',[])} if running else {}
        rows=[]
        for name,spec in self.specs().items():
            options=self.options(name,config);row=live.get(name,{})
            missing=sorted(set(spec.get('requires',['text.receive']))-capabilities)
            managed=MANAGED.get(name)
            rows.append({'name':name,'description':spec.get('description',''),'enabled':bool(options.get('enabled')) if not managed else bool(ai.get('enabled')),
                'active':bool(row.get('active')) if not managed else bool(ai.get('enabled') and running),
                'missing_capabilities':missing if not managed else [],'managed_by':managed,
                'error':'PluginError' if row.get('error') else '', 'handled':row.get('handled',0),'errors':row.get('errors',0),'dropped':row.get('dropped',0),
                'options':public_options(options)})
        return {'plugins':rows,'runtime':{'running':bool(running),'generation':config.get('generation',0),'verified_capabilities':len(capabilities)}}

    def validate(self,name,options):
        specs=self.specs()
        if name not in specs:raise ValueError('未找到该插件')
        if name in MANAGED:raise ValueError('此功能已由 Linux 核心管理，请在对应栏目设置')
        if not isinstance(options,dict) or type(options.get('enabled',False)) is not bool:raise ValueError('插件配置应为对象，enabled 应为布尔值')
        json.dumps(options,allow_nan=False)
        if 'groups' in options:
            ai=self.read('ai_config.json');catalog=self.read('groups.json').get('groups',[]);known=set(ai.get('systems',{}))|{r['id'] for r in catalog}
            if not isinstance(options['groups'],list) or any(not isinstance(r,str) or r not in known for r in options['groups']):raise ValueError('插件群范围必须选择已识别的群 ID')
        if options.get('enabled'):
            missing=set(specs[name].get('requires',['text.receive']))-set(self.read('capabilities.json').get('capabilities',[]))
            if missing:raise ValueError('缺少已验证的 Linux 能力：'+', '.join(sorted(missing)))

    def persist(self,name,options,*,reload=False):
        self.validate(name,options);config=self.read('plugin_config.json',{'generation':0,'plugins':{}})
        config.setdefault('plugins',{})[name]=options;config['generation']=int(config.get('generation',0))+1
        if reload:config.setdefault('reloads',{})[name]=config['generation']
        atomic_json(self.state/'plugin_config.json',config)

    def action(self,data):
        name=validate_name(data.get('name'));action=data.get('action')
        if action not in ('enable','disable','reload','configure'):raise ValueError('不支持的插件操作')
        config=self.read('plugin_config.json');options=self.options(name,config)
        if action=='configure':options=restore_options(data.get('options'),options)
        elif action!='reload':options['enabled']=action=='enable'
        self.persist(name,options,reload=action=='reload')
        return {'ok':True,'pending':True}

    def business(self,name):
        name=validate_name(name)
        if name not in self.specs() or name in MANAGED:raise ValueError('此插件不提供独立业务编辑')
        options=self.options(name,self.read('plugin_config.json'))
        snapshot=self.sources.snapshot(name,options);result=view(snapshot)
        result['options']=public_options(options)
        row=next(r for r in self.catalog()['plugins'] if r['name']==name)
        result.update(active=row['active'],running=self.catalog()['runtime']['running'])
        return result

    def save_business(self,data):
        name=validate_name(data.get('name'));action=data.get('action','save')
        if action not in ('save','reset'):raise ValueError('不支持的业务编辑操作')
        before=self.read('plugin_config.json');options=self.options(name,before)
        if name in MANAGED or name not in self.specs():raise ValueError('此插件不提供独立业务编辑')
        snapshot=self.sources.snapshot(name,options)
        if data.get('revision')!=snapshot['revision']:raise PanelError('插件已被其他页面修改，请重新读取后再保存',409)
        if action=='reset':
            candidate=copy.deepcopy(self.sources.defaults(name));candidate['enabled']=options.get('enabled',False);revision=None
        else:
            candidate=restore_options(data.get('options'),options)
            self.validate(name,candidate);revision=self.sources.prepare(name,merge(snapshot['files'],data.get('functions')))
        self.validate(name,candidate);previous=self.sources.pointer(name)
        try:
            self.sources.activate(name,revision);self.persist(name,candidate,reload=True)
        except Exception:
            self.sources.activate(name,previous);raise
        result=self.business(name);result['applied']=False;result['pending']=True;return result
