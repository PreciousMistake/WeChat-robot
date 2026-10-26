"""Cloud speech adapters. Provider responses and credentials never enter errors."""
from dataclasses import dataclass, field
from io import BytesIO
import json
import math
from pathlib import Path
import re
import time
import uuid
import urllib.request
import urllib.parse
import wave

from .tts import FishTTS, TTSError, Voice, VoicePage, normalize_wav, _NoRedirect

class ASRError(RuntimeError):
    pass

def _secret(value, error):
    if not isinstance(value,str) or not value.strip() or len(value)>4096 or any(ord(c)<32 or ord(c)==127 for c in value):
        raise error('请填写有效的语音服务密钥')
    return value.strip()

def _endpoint(value,scheme,error):
    if not isinstance(value,str) or any(ord(c)<33 for c in value):raise error('语音服务地址无效')
    p=urllib.parse.urlsplit(value)
    if p.scheme!=scheme or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise error('语音服务地址必须使用加密连接且不含凭证或查询参数')
    return value.rstrip('/')

def pcm16(data):
    """Validate a <=60-second mono WAV and resample locally to 16 kHz PCM."""
    if not isinstance(data,bytes) or not 44<=len(data)<=20*1024*1024:raise ASRError('识别音频大小无效')
    try:
        with wave.open(BytesIO(data),'rb') as w:
            rate,frames=w.getframerate(),w.getnframes()
            if (w.getnchannels()!=1 or w.getsampwidth()!=2 or w.getcomptype()!='NONE' or
                    rate not in (8000,12000,16000,24000,32000,44100,48000) or not 0<frames<=rate*60):
                raise ASRError('识别需要60秒以内的单声道16位PCM音频')
            pcm=w.readframes(frames)
            if len(pcm)!=frames*2:raise ASRError('识别音频数据不完整')
        if rate!=16000:
            import audioop
            pcm,_=audioop.ratecv(pcm,2,1,rate,16000,None)
        return pcm
    except (wave.Error,EOFError,ValueError):raise ASRError('识别音频不是有效WAV') from None

@dataclass(frozen=True)
class ASRClient:
    api_key: str=field(repr=False)
    model: str='qwen-audio-3.1-asr-flash-message'
    websocket_url: str='wss://dashscope.aliyuncs.com/api-ws/v1/inference'
    timeout_seconds: float=45

    def __post_init__(self):
        _secret(self.api_key,ASRError);_endpoint(self.websocket_url,'wss',ASRError)
        if self.model!='qwen-audio-3.1-asr-flash-message':raise ASRError('识别模型暂只支持千问Message')
        if type(self.timeout_seconds) not in (int,float) or not math.isfinite(self.timeout_seconds) or not 1<=self.timeout_seconds<=60:
            raise ASRError('识别超时应为1至60秒')

    @classmethod
    def from_file(cls,path):
        try:data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        except (OSError,ValueError):raise ASRError('无法读取语音识别配置') from None
        if not isinstance(data,dict):raise ASRError('语音识别配置格式无效')
        return cls(**{k:data[k] for k in ('api_key','model','websocket_url','timeout_seconds') if k in data})

    def transcribe(self,data,*,is_current=lambda:True):
        pcm=pcm16(data)
        ws=None;deadline=time.monotonic()+self.timeout_seconds;task=str(uuid.uuid4());texts=[]
        def active():
            if not is_current():raise ASRError('识别会话已失效')
            left=deadline-time.monotonic()
            if left<=0:raise ASRError('语音识别超时')
            return left
        def send(action,payload):
            ws.send(json.dumps({'header':{'action':action,'task_id':task,'streaming':'duplex'},'payload':payload}))
        try:
            import websocket
            ws=websocket.create_connection(self.websocket_url,
                header={'Authorization':'Bearer '+self.api_key},timeout=active())
            send('run-task',{'task_group':'audio','task':'asr','function':'recognition','model':self.model,
                'parameters':{'format':'pcm','sample_rate':16000,'disfluency_removal_enabled':False},'input':{}})
            started=False
            for _ in range(512):
                ws.settimeout(active());raw=ws.recv()
                if not isinstance(raw,str) or len(raw)>262144:raise ASRError('识别响应格式无效')
                event=json.loads(raw);header=event.get('header',{})
                if header.get('task_id',task)!=task:raise ASRError('识别任务编号不匹配')
                kind=header.get('event')
                if kind=='task-failed':raise ASRError('语音识别服务拒绝任务，请检查模型权限、余额和配置')
                if kind=='task-started':
                    if started:raise ASRError('识别服务重复启动任务')
                    started=True
                    for start in range(0,len(pcm),3200):
                        active();ws.send_binary(pcm[start:start+3200])
                    send('finish-task',{'input':{}})
                elif kind=='result-generated' and started:
                    sentence=event.get('payload',{}).get('output',{}).get('sentence',{})
                    text=sentence.get('text','')
                    if sentence.get('sentence_end') is True and isinstance(text,str):
                        texts.append(text)
                        if sum(map(len,texts))>8000:raise ASRError('识别结果过长')
                elif kind=='task-finished' and started:
                    active();result=''.join(texts).strip()
                    if not result:raise ASRError('语音未识别到有效文字')
                    if any(ord(c)<32 and c not in '\n\r\t' for c in result):raise ASRError('识别结果格式无效')
                    return result
            raise ASRError('语音识别事件超过上限')
        except ASRError:raise
        except Exception:raise ASRError('语音识别连接失败或响应无效') from None
        finally:
            if ws:
                try:ws.close()
                except Exception:pass

MINIMAX_VOICE=re.compile(r'[A-Za-z0-9_-]{1,128}\Z')

@dataclass(frozen=True)
class MiniMaxTTS:
    api_key: str=field(repr=False)
    base_url: str='https://api.minimax.cn/v1'
    model: str='speech-2.8-hd'
    default_voice: str='Chinese_sweet_girl_nv1'
    timeout_seconds: float=45
    max_chars_per_clip: int=160

    @classmethod
    def from_options(cls,data):
        if not isinstance(data,dict):raise TTSError('语音合成配置格式无效')
        key=_secret(data.get('api_key'),TTSError)
        endpoint=_endpoint(data.get('base_url','https://api.minimax.cn/v1'),'https',TTSError)
        model=data.get('model','speech-2.8-hd')
        if model not in ('speech-2.8-hd','speech-2.8-turbo','speech-2.6-hd','speech-2.6-turbo','speech-02-hd','speech-02-turbo'):
            raise TTSError('MiniMax语音模型不在支持列表中')
        voice=data.get('default_voice','Chinese_sweet_girl_nv1')
        if not isinstance(voice,str) or not MINIMAX_VOICE.fullmatch(voice):raise TTSError('MiniMax音色ID无效')
        timeout=data.get('timeout_seconds',45);limit=data.get('max_chars_per_clip',160)
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 1<=timeout<=60:raise TTSError('语音超时应为1至60秒')
        if type(limit) is not int or not 1<=limit<=400:raise TTSError('单段语音文字应为1至400字符')
        return cls(key,endpoint,model,voice,timeout,limit)

    def public_voice(self,identifier):
        if not isinstance(identifier,str) or not MINIMAX_VOICE.fullmatch(identifier):raise TTSError('MiniMax音色ID无效')
        return Voice(identifier,identifier)

    def voices(self):return (self.public_voice(self.default_voice),)

    def public_voices(self,*,page=1,keyword='',page_size=10,all_languages=False):
        voices=self.voices() if page==1 and keyword in self.default_voice else ()
        return VoicePage(voices,page,page_size,False)

    def synthesize(self,text,voice_id):
        self.public_voice(voice_id)
        if not isinstance(text,str) or not text.strip() or len(text)>self.max_chars_per_clip or '\0' in text:raise TTSError('合成文字为空或超过长度限制')
        payload={'model':self.model,'text':text,'stream':False,'output_format':'hex',
            'voice_setting':{'voice_id':voice_id,'speed':1,'vol':1,'pitch':0},
            'audio_setting':{'sample_rate':24000,'format':'wav','channel':1}}
        req=urllib.request.Request(self.base_url+'/t2a_v2',data=json.dumps(payload).encode(),
            headers={'Authorization':'Bearer '+self.api_key,'Content-Type':'application/json'})
        try:
            with urllib.request.build_opener(_NoRedirect()).open(req,timeout=self.timeout_seconds) as response:
                raw=response.read(40*1024*1024+1)
            if len(raw)>40*1024*1024:raise TTSError('MiniMax音频响应过大')
            body=json.loads(raw)
            if body.get('base_resp',{}).get('status_code')!=0:raise TTSError('MiniMax语音合成失败，请检查音色、权限和余额')
            return normalize_wav(bytes.fromhex(body['data']['audio']))
        except TTSError:raise
        except Exception:raise TTSError('MiniMax语音请求失败或音频无效') from None

def tts_from_options(options):
    provider=options.get('provider','fish') if isinstance(options,dict) else None
    if provider=='minimax':return MiniMaxTTS.from_options(options)
    if provider=='fish':return FishTTS.from_options(options)
    raise TTSError('语音合成服务应为fish或minimax')

def tts_from_file(path):
    try:options=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):raise TTSError('无法读取语音合成配置') from None
    return tts_from_options(options)

def select_tts_profile(options,provider):
    """Switch local provider settings without borrowing another service's key."""
    from copy import deepcopy
    if provider not in ('fish','minimax'):raise TTSError('不支持的语音合成服务')
    current=options.get('provider','fish')
    profiles=deepcopy(options.get('profiles',{}))
    if not isinstance(profiles,dict):raise TTSError('语音服务配置格式无效')
    profiles[current]={k:v for k,v in options.items() if k not in ('profiles','provider')}
    defaults=({'base_url':'https://api.minimax.cn/v1','model':'speech-2.8-hd',
        'default_voice':'Chinese_sweet_girl_nv1'} if provider=='minimax' else
        {'base_url':'https://api.fish.audio','model':'s2.1-pro-free','default_voice':''})
    result=dict(defaults,api_key='',timeout_seconds=45,max_chars_per_clip=160)
    result.update(profiles.get(provider,{}))
    result.update(provider=provider,profiles=profiles)
    return result
