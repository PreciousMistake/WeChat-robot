"""Bound-account read-only WCDB voice lookup; keys live only in this instance.

Config.Cipher layout reference (Apache-2.0):
https://github.com/fanyuantaier/wechatauto-replica/blob/main/wechatauto/db.py
"""
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import re
import struct
import time

from run_selftest import K, check
from text_service import inspect_account
from wxbot_framework.voice_store import VoiceStoreError, verify_key, read_voice, MAX_DATABASE_BYTES

MARK=b'com.Tencent.WCDB.Config.Cipher'
MASK=bytes.fromhex('d2c7442458020000004889442450488b450048844c2448488944254048584c24')
class _MBI(c.Structure):
    _fields_=[('base',c.c_void_p),('allocation',c.c_void_p),('allocation_protect',w.DWORD),
        ('partition',w.DWORD),('size',c.c_size_t),('state',w.DWORD),('protect',w.DWORD),('type',w.DWORD)]
K.VirtualQueryEx.argtypes=[w.HANDLE,c.c_void_p,c.POINTER(_MBI),c.c_size_t]
K.VirtualQueryEx.restype=c.c_size_t

class VoiceReader:
    def __init__(self,pid,account,directory):
        from wxbot_framework.account import validate_account
        self.account=validate_account(account);self.pid=pid
        self.directory=Path(directory).resolve(strict=True)
        if not (self.directory.name==account or self.directory.name.startswith(account+'_')):
            raise VoiceStoreError('语音数据目录与绑定账号不一致')
        self.keys={}

    def _active(self,current):
        if not current() or inspect_account(self.pid)!=self.account:
            self.keys.clear();raise VoiceStoreError('语音读取会话已失效')

    def _databases(self):
        root=(self.directory/'db_storage/message').resolve(strict=True)
        if not root.is_relative_to(self.directory):raise VoiceStoreError('语音数据库目录超出账号范围')
        paths=[]
        for p in sorted(root.glob('media_*.db')):
            if not re.fullmatch(r'media_[0-9]+\.db',p.name):continue
            if p.resolve().parent!=root or p.stat().st_size>MAX_DATABASE_BYTES:
                raise VoiceStoreError('语音数据库路径或大小无效')
            paths.append(p)
        if len(paths)>32:raise VoiceStoreError('语音数据库分片超出读取预算')
        return paths

    def _discover(self,paths,current):
        self._active(current);deadline=time.monotonic()+30
        handle=check(K.OpenProcess(0x410,False,self.pid),'只读语音数据')
        def read(addr,size):
            if not 0x10000<=addr<0x800000000000 or not 0<size<=2*1024*1024:return b''
            buf=c.create_string_buffer(size);count=c.c_size_t()
            K.ReadProcessMemory(handle,addr,buf,size,c.byref(count))
            return buf.raw[:count.value]
        def scan(needles):
            hits={needle:[] for needle in needles};addr=0;budget=0
            while True:
                if not current() or time.monotonic()>deadline:raise VoiceStoreError('语音密钥读取已取消或超时')
                info=_MBI()
                if not K.VirtualQueryEx(handle,addr,c.byref(info),c.sizeof(info)):break
                base=info.base or 0;following=base+info.size
                if following<=addr:break
                if info.state==0x1000 and info.protect&0xff in (2,4,8,0x20,0x40,0x80) and not info.protect&0x100:
                    for offset in range(0,info.size,1024*1024):
                        if not current() or time.monotonic()>deadline:raise VoiceStoreError('语音读取已取消或超时')
                        length=min(info.size-offset,1024*1024+128);budget+=length
                        if budget>2*1024**3:raise VoiceStoreError('语音密钥读取超出预算')
                        data=read(base+offset,length)
                        for needle in needles:
                            pos=data.find(needle)
                            while pos>=0:
                                if len(hits[needle])>=512:raise VoiceStoreError('语音密钥对象数量异常')
                                hits[needle].append(base+offset+pos);pos=data.find(needle,pos+1)
                addr=following
            return hits
        try:
            locations=scan([MARK])[MARK]
            if not locations:raise VoiceStoreError('当前微信语音数据库密钥尚未就绪')
            refs=scan([struct.pack('<QQ',addr,len(MARK)) for addr in locations]);candidates=set()
            for positions in refs.values():
                for addr in positions:
                    node=read(addr-16,80)
                    if len(node)!=80:continue
                    obj=read(struct.unpack_from('<Q',node,40)[0]+0x88,40)
                    if len(obj)!=40:continue
                    pointer,length=struct.unpack_from('<QQ',obj,8)
                    if not 0<length<=1024:continue
                    blob=read(pointer,length)
                    decoded=bytes(v^MASK[i%len(MASK)] for i,v in enumerate(blob))
                    for match in re.finditer(rb"[xX]'([0-9a-fA-F]{64,192})'",decoded):
                        material=match.group(1)
                        for start in range(0,len(material)-63,32):
                            candidates.add(bytes.fromhex(material[start:start+64].decode()))
            for path in paths:
                with path.open('rb') as f:page=f.read(4096)
                for key in candidates:
                    if verify_key(key,page):self.keys[path]=key;break
            self._active(current)
        finally:K.CloseHandle(handle)

    def get(self,room,ident,*,current=lambda:True):
        from wxbot_framework.group_management import validate_group_id
        validate_group_id(room);self._active(current)
        paths=self._databases()
        if any(path not in self.keys for path in paths):self._discover(paths,current)
        results=[]
        for path in paths:
            self._active(current)
            key=self.keys.get(path)
            if key is None:raise VoiceStoreError('部分语音数据库密钥尚未就绪')
            try:audio=read_voice(path,key,room,ident)
            except VoiceStoreError:
                self.keys.pop(path,None);raise
            if audio:results.append(audio)
        self._active(current)
        if len(results)>1:raise VoiceStoreError('语音消息跨分片关联不唯一')
        return results[0] if results else None

    def close(self):
        self.keys.clear()
