"""One bounded OpenAI-compatible image generation call; no automatic retries."""
import base64
import binascii
import json
import math
import re
import http.client
import ipaddress
import socket
import ssl
import time
import threading
from contextlib import contextmanager
from urllib.parse import urlsplit,urljoin


DEFAULTS={'enabled':False,'protocol':'auto','base_url':'','model':'','api_key':'','size':'1024x1024',
          'timeout_seconds':120,'cooldown_seconds':30,'hourly_limit':10,'extra_body':{}}


class ImageError(RuntimeError):pass


def normalize_image_config(options):
    if not isinstance(options,dict):raise ValueError('生图配置必须为对象')
    value=dict(DEFAULTS,**options)
    if value['protocol'] not in ('auto','openai','dashscope','minimax'):raise ValueError('生图协议应为 auto、openai、dashscope 或 minimax')
    if type(value['enabled']) is not bool:raise ValueError('生图开关无效')
    for field in ('base_url','model','api_key','size'):
        if not isinstance(value[field],str):raise ValueError('生图配置字段应为文本')
    value['base_url']=value['base_url'].strip().rstrip('/')
    if value['base_url']:
        try:
            url=urlsplit(value['base_url'])
            if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or not (url.port is None or 1<=url.port<=65535):raise ValueError()
            if any(ord(c)<=32 for c in value['base_url']):raise ValueError()
        except ValueError:raise ValueError('生图 API 地址应为不含密钥和查询参数的 HTTPS 地址') from None
    if value['enabled'] and any(not value[k].strip() for k in ('base_url','model','api_key')):raise ValueError('启用生图前请填写 API 地址、模型与密钥')
    if len(value['model'])>128 or any(ord(c)<32 for c in value['api_key']):raise ValueError('生图模型或密钥格式无效')
    size=value['size']
    if size not in ('','auto'):
        match=re.fullmatch(r'(\d{2,4})x(\d{2,4})',size)
        if not match or any(not 16<=int(n)<=4096 for n in match.groups()):raise ValueError('生图尺寸应为 auto 或宽x高，例如 1024x1024')
    for field,low,high in (('timeout_seconds',5,300),('cooldown_seconds',0,3600),('hourly_limit',1,100)):
        n=value[field]
        if type(n) not in (int,float) or not math.isfinite(n) or not low<=n<=high:raise ValueError('生图 '+field+' 超出范围')
    if type(value['hourly_limit']) is not int:raise ValueError('生图每小时上限应为整数')
    extra=value['extra_body']
    if (not isinstance(extra,dict) or len(json.dumps(extra,allow_nan=False))>16000 or
            set(extra)&{'model','prompt','n','api_key','headers','authorization','stream','base_url','url','input','parameters','messages'}):
        raise ValueError('生图扩展参数不可覆盖模型、描述、数量、密钥或地址')
    return value


@contextmanager
def monitored_connection(connection,deadline,current):
    stopped=threading.Event()
    def watch():
        while not stopped.wait(.1):
            if time.monotonic()>=deadline or not current():
                try:
                    active_socket=connection.sock or getattr(connection,'_image_io_socket',None)
                    if active_socket:active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                connection.close();return
    watcher=threading.Thread(target=watch,daemon=True,name='image-api-deadline');watcher.start()
    try:yield
    finally:
        stopped.set();connection.close()
        response=getattr(connection,'_image_io_response',None)
        if response:response.close()
        active_socket=getattr(connection,'_image_io_socket',None)
        if active_socket:active_socket.close()


def tracked_response(connection):
    # Connection: close detaches this socket from HTTPConnection, while a
    # chunked HTTPResponse can still block reading it. Keep it for cancellation.
    connection._image_io_socket=connection.sock
    response=connection.getresponse()
    connection._image_io_response=response
    return response


def remaining_timeout(connection,deadline):
    active_socket=connection.sock or getattr(connection,'_image_io_socket',None)
    if active_socket:active_socket.settimeout(max(.05,deadline-time.monotonic()))


class ImageClient:
    def __init__(self,options):
        self.options=normalize_image_config(options)
        if not self.options['enabled']:raise ValueError('生图服务尚未启用')
        base=self.options['base_url']
        host=urlsplit(base).hostname or ''
        native_host=host in ('dashscope.aliyuncs.com','dashscope-intl.aliyuncs.com') or host.endswith('.maas.aliyuncs.com')
        self.protocol=self.options['protocol']
        if self.protocol=='auto':
            self.protocol=('dashscope' if native_host and self.options['model'].startswith('qwen-image') else
                           'minimax' if host in ('api.minimax.cn','api.minimaxi.com','api.minimax.io') and self.options['model'] in ('image-01','image-01-live') else 'openai')
        if self.protocol=='dashscope':
            suffix='/api/v1/services/aigc/multimodal-generation/generation'
            if base.endswith(suffix):self.endpoint=base
            else:
                if base.endswith('/images/generations'):base=base[:-len('/images/generations')]
                for ending in ('/compatible-mode/v1','/api/v1','/v1'):
                    if base.endswith(ending):base=base[:-len(ending)];break
                self.endpoint=base+suffix
        elif self.protocol=='minimax':
            if base.endswith('/v1/image_generation'):self.endpoint=base
            else:self.endpoint=base+('/image_generation' if base.endswith('/v1') else '/v1/image_generation')
        else:self.endpoint=base if base.endswith('/images/generations') else base+'/images/generations'

    def _download(self,url,*,current):
        from plugins.image_gallery.source import checked_url
        deadline=time.monotonic()+min(30,self.options['timeout_seconds'])
        for _ in range(4):
            checked_url(url);parsed=urlsplit(url);remaining=deadline-time.monotonic()
            if remaining<=0 or not current():raise ImageError('生图图片下载已超时或取消')
            addresses=socket.getaddrinfo(parsed.hostname,parsed.port or 443,type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):raise ImageError('生图图片下载地址不可用')
            # Pin the checked public address, retaining certificate hostname/SNI.
            conn=http.client.HTTPSConnection(parsed.hostname,parsed.port or 443,timeout=remaining)
            raw_socket=None
            try:
                raw_socket=socket.create_connection((addresses[0][4][0],parsed.port or 443),timeout=remaining)
                conn.sock=ssl.create_default_context().wrap_socket(raw_socket,server_hostname=parsed.hostname)
                with monitored_connection(conn,deadline,current):
                    conn.request('GET',(parsed.path or '/')+('?' + parsed.query if parsed.query else ''),headers={'Accept':'image/*','Accept-Encoding':'identity'})
                    remaining_timeout(conn,deadline)
                    response=tracked_response(conn)
                    if response.status in (301,302,303,307,308):
                        location=response.getheader('Location')
                        if not location:raise ImageError('生图图片下载地址不可用')
                        url=urljoin(url,location);continue
                    if response.status!=200:raise ImageError('生图图片下载失败')
                    length=response.getheader('Content-Length')
                    if length is not None and (not length.isdecimal() or int(length)>20*1024*1024):raise ImageError('生图图片超过大小限制')
                    if response.getheader('Content-Encoding','identity').lower() not in ('','identity'):raise ImageError('生图图片编码不受支持')
                    chunks=[];total=0
                    while True:
                        if not current() or time.monotonic()>=deadline:raise ImageError('生图图片下载已超时或取消')
                        remaining_timeout(conn,deadline)
                        read=getattr(response,'read1',response.read)
                        chunk=read(min(65536,20*1024*1024-total+1))
                        if not chunk:break
                        chunks.append(chunk);total+=len(chunk)
                        if total>20*1024*1024:raise ImageError('生图图片超过大小限制')
                    if length is not None and total!=int(length):raise ImageError('生图图片下载不完整')
                    return b''.join(chunks)
            finally:
                conn.close()
                if raw_socket:raw_socket.close()
        raise ImageError('生图图片重定向过多')

    def _post(self,payload,*,current=lambda:True):
        parsed=urlsplit(self.endpoint);timeout=self.options['timeout_seconds'];deadline=time.monotonic()+timeout
        conn=http.client.HTTPSConnection(parsed.hostname,parsed.port or 443,timeout=timeout)
        try:
            with monitored_connection(conn,deadline,current):
                conn.connect()
                if time.monotonic()>=deadline or not current():raise ImageError('生图请求已超时或取消，本次不会自动重试')
                conn.request('POST',parsed.path or '/',body=json.dumps(payload,ensure_ascii=False).encode(),headers={
                    'Authorization':'Bearer '+self.options['api_key'],'Content-Type':'application/json','Accept':'application/json','Accept-Encoding':'identity'})
                remaining_timeout(conn,deadline)
                response=tracked_response(conn)
                if response.status!=200:
                    code=''
                    try:
                        body=json.loads(response.read(8192));detail=body.get('error',body);candidate=detail.get('code','') if isinstance(detail,dict) else ''
                        if isinstance(candidate,str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,99}',candidate) and 'sk-' not in candidate.lower() and self.options['api_key'] not in candidate:code='，错误码 '+candidate
                    except (ValueError,TypeError,OSError,http.client.HTTPException):pass
                    raise ImageError('生图接口请求失败（HTTP '+str(response.status)+code+'），本次不会自动重试')
                if response.getheader('Content-Encoding','identity').lower() not in ('','identity'):raise ImageError('生图响应编码不受支持')
                length=response.getheader('Content-Length')
                if length is not None and (not length.isdecimal() or int(length)>32*1024*1024):raise ImageError('生图响应超过大小限制')
                blocks=[];total=0;read=getattr(response,'read1',response.read)
                while True:
                    if time.monotonic()>=deadline or not current():raise ImageError('生图请求已超时或取消，本次不会自动重试')
                    remaining_timeout(conn,deadline)
                    chunk=read(min(65536,32*1024*1024-total+1))
                    if not chunk:break
                    blocks.append(chunk);total+=len(chunk)
                    if total>32*1024*1024:raise ImageError('生图响应超过大小限制')
                if length is not None and total!=int(length):raise ImageError('生图响应不完整')
                return json.loads(b''.join(blocks))
        except (OSError,TimeoutError,http.client.HTTPException):raise ImageError('生图请求连接失败或超时；为避免重复计费，本次不会自动重试') from None
        except (ValueError,RecursionError):raise ImageError('生图接口未返回有效 JSON') from None

    def generate(self,prompt,*,current=lambda:True):
        if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>4000:raise ImageError('生图描述应为 1–4000 字符')
        if not current():raise ImageError('生图任务已取消')
        payload=dict(self.options['extra_body'],model=self.options['model'],prompt=prompt.strip(),n=1)
        if self.options['size']:payload['size']=self.options['size']
        if self.protocol=='dashscope':
            parameters=dict(self.options['extra_body'],n=1)
            if self.options['size'] not in ('','auto'):parameters['size']=self.options['size'].replace('x','*')
            payload={'model':self.options['model'],'input':{'messages':[{'role':'user','content':[{'text':prompt.strip()}]}]},'parameters':parameters}
        if self.protocol=='minimax':
            if len(prompt.strip())>1500:raise ImageError('MiniMax 生图描述最长 1500 字符')
            payload.pop('size',None);payload.setdefault('response_format','url')
            if self.options['size'] not in ('','auto'):
                width,height=map(int,self.options['size'].split('x'))
                if self.options['model']=='image-01-live':
                    ratios={(1024,1024):'1:1',(1280,720):'16:9',(1152,864):'4:3',(1248,832):'3:2',(832,1248):'2:3',(864,1152):'3:4',(720,1280):'9:16'}
                    if (width,height) not in ratios:raise ImageError('image-01-live 请使用标准宽高比尺寸或 auto')
                    payload.setdefault('aspect_ratio',ratios[(width,height)])
                else:
                    if any(not 512<=n<=2048 or n%8 for n in (width,height)):raise ImageError('MiniMax 图片宽高须为 512–2048 且为 8 的倍数')
                    payload.update(width=width,height=height)
        result=self._post(payload,current=current)
        if self.protocol=='minimax':
            status=result.get('base_resp',{}).get('status_code',0) if isinstance(result,dict) else None
            if status!=0:
                code=str(status) if type(status) is int else 'unknown'
                raise ImageError('MiniMax 生图接口业务失败（错误码 '+code+'），本次不会自动重试')
        if not current():raise ImageError('生图任务已取消')
        try:
            if self.protocol=='dashscope':
                content=result['output']['choices'][0]['message']['content']
                item={'url':next(value['image'] for value in content if isinstance(value,dict) and value.get('image'))}
            elif self.protocol=='minimax':
                data=result['data']
                item={'b64_json':data['image_base64'][0]} if data.get('image_base64') else {'url':data['image_urls'][0]}
            else:item=result['data'][0]
            if not isinstance(item,dict) or not (item.get('b64_json') or item.get('url')):raise ValueError()
        except (KeyError,IndexError,TypeError,ValueError,StopIteration):
            detail=''
            if self.protocol=='minimax' and isinstance(result,dict):
                meta=result.get('metadata',{})
                if isinstance(meta,dict):
                    counts=[str(meta.get(k,'')) for k in ('success_count','failed_count')]
                    if all(re.fullmatch(r'[0-9]{1,5}',v) for v in counts):detail='（成功 '+counts[0]+'，失败 '+counts[1]+'）'
            raise ImageError('生图服务未返回图片'+detail+'，本次不会自动重试') from None
        if item.get('b64_json'):
            try:
                encoded=item['b64_json']
                if not isinstance(encoded,str) or len(encoded)>28*1024*1024:raise ValueError()
                raw=base64.b64decode(encoded,validate=True)
            except (TypeError,ValueError,binascii.Error):raise ImageError('生图接口返回的 Base64 图片无效') from None
        else:
            try:raw=self._download(item['url'],current=current)
            except ImageError:raise
            except Exception:raise ImageError('生图已返回图片地址，但下载失败，本次不会重新生成') from None
        try:
            if len(raw)>20*1024*1024:raise ValueError()
            from plugins.image_gallery.source import GalleryClient
            return GalleryClient._image(None,raw)
        except Exception:raise ImageError('图片已取得，但格式或完整性校验失败，本次不会重新生成') from None
