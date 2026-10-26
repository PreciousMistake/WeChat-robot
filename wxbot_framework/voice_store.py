"""Read a specific voice from an authenticated WCDB media snapshot, in memory.

SQLCipher 4 page layout and WCDB schema references:
https://www.zetetic.net/sqlcipher/design/
https://github.com/fanyuantaier/wechatauto-replica/blob/main/wechatauto/db.py
No database is written back to WeChat and no plaintext DB is saved.
"""
import hashlib
import hmac
import re
import sqlite3
import struct
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAX_DATABASE_BYTES = 64 * 1024 * 1024
MAX_VOICE_BYTES = 1024 * 1024

class VoiceStoreError(RuntimeError):
    pass

def verify_key(key, page):
    if len(key)!=32 or len(page)!=4096:return False
    mac=hashlib.pbkdf2_hmac('sha512',key,bytes(x^0x3a for x in page[:16]),2,32)
    return hmac.compare_digest(hmac.new(mac,page[16:4032]+struct.pack('<I',1),hashlib.sha512).digest(),page[4032:])

def _checksum(data, endian, state=(0,0)):
    a,b=state
    for x,y in struct.iter_unpack(endian+'II',data):
        a=(a+x+b)&0xffffffff;b=(b+y+a)&0xffffffff
    return a,b

def decode_snapshot(raw, wal, key):
    if (not raw or len(raw)%4096 or len(raw)>MAX_DATABASE_BYTES or
            len(wal)>MAX_DATABASE_BYTES or not verify_key(key,raw[:4096])):
        raise VoiceStoreError('语音数据库大小或密钥校验失败')
    mac=hashlib.pbkdf2_hmac('sha512',key,bytes(x^0x3a for x in raw[:16]),2,32)
    def page(data,number):
        offset=16 if number==1 else 0
        expected=hmac.new(mac,data[offset:4032]+struct.pack('<I',number),hashlib.sha512).digest()
        if len(data)!=4096 or not hmac.compare_digest(expected,data[4032:]):
            raise VoiceStoreError('语音数据库页面完整性校验失败')
        dec=Cipher(algorithms.AES(key),modes.CBC(data[4016:4032])).decryptor()
        return (b'SQLite format 3\0' if number==1 else b'')+dec.update(data[offset:4016])+dec.finalize()+bytes(80)
    result=bytearray().join(page(raw[i:i+4096],i//4096+1) for i in range(0,len(raw),4096))
    if not wal:return bytes(result)
    if len(wal)<32:raise VoiceStoreError('语音数据库日志不完整')
    magic,version,size=struct.unpack('>III',wal[:12])
    if magic not in (0x377f0682,0x377f0683) or version!=3007000 or size!=4096:
        raise VoiceStoreError('语音数据库日志格式不支持')
    endian='<' if magic==0x377f0682 else '>'
    state=_checksum(wal[:24],endian)
    if state!=struct.unpack('>II',wal[24:32]):raise VoiceStoreError('语音数据库日志头校验失败')
    pending=[]
    for i in range(32,len(wal)-4119,4120):
        header=wal[i:i+24];data=wal[i+24:i+4120]
        if header[8:16]!=wal[16:24]:break  # stale frames after a checkpoint
        state=_checksum(header[:8]+data,endian,state)
        if state!=struct.unpack('>II',header[16:24]):raise VoiceStoreError('语音数据库日志校验失败')
        number,commit=struct.unpack('>II',header[:8])
        if not 0<number<=MAX_DATABASE_BYTES//4096 or commit>MAX_DATABASE_BYTES//4096:
            raise VoiceStoreError('语音数据库日志超出读取范围')
        pending.append((number,page(data,number)))
        if commit:
            for number,decoded in pending:
                end=number*4096
                if end>len(result):result.extend(bytes(end-len(result)))
                result[end-4096:end]=decoded
            del result[commit*4096:]
            pending=[]
    return bytes(result)

def query_voice(conn, room, ident):
    if not isinstance(ident,str) or not re.fullmatch(r'[1-9][0-9]{0,18}',ident) or int(ident)>2**63-1:
        raise VoiceStoreError('语音原始消息编号无效')
    try:
        rows=conn.execute('SELECT rowid FROM Name2Id WHERE user_name=? LIMIT 2',(room,)).fetchall()
        if len(rows)!=1:return None
        rows=conn.execute('SELECT voice_data FROM VoiceInfo WHERE chat_name_id=? AND svr_id=? LIMIT 2',
                          (rows[0][0],int(ident))).fetchall()
        if not rows:return None
        if len(rows)!=1:raise VoiceStoreError('语音消息关联不唯一')
        audio=rows[0][0]
        if not audio:return None
        if not isinstance(audio,bytes) or len(audio)>MAX_VOICE_BYTES or not audio.startswith((b'\x02#!SILK_V3',b'#!SILK_V3')):
            raise VoiceStoreError('语音格式或大小不支持')
        return audio
    except sqlite3.Error:raise VoiceStoreError('语音数据库结构不支持') from None

def read_voice(path, key, room, ident):
    from pathlib import Path
    path=Path(path);wal_path=Path(str(path)+'-wal')
    def snapshot():
        if path.stat().st_size>MAX_DATABASE_BYTES or (wal_path.exists() and wal_path.stat().st_size>MAX_DATABASE_BYTES):
            raise VoiceStoreError('语音数据库超出读取预算')
        with path.open('rb') as f:raw=f.read(MAX_DATABASE_BYTES+1)
        if wal_path.exists():
            with wal_path.open('rb') as f:wal=f.read(MAX_DATABASE_BYTES+1)
        else:wal=b''
        return raw,wal
    try:
        raw,wal=snapshot();plain=bytearray(decode_snapshot(raw,wal,key))
        if (raw,wal)!=snapshot():raise VoiceStoreError('语音数据库正在变化，请稍后重试')
        plain[18:20]=b'\x01\x01'
        conn=sqlite3.connect(':memory:')
        try:
            conn.deserialize(bytes(plain));conn.execute('PRAGMA query_only=ON')
            if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise VoiceStoreError('语音数据库快照无效')
            return query_voice(conn,room,ident)
        finally:conn.close()
    except (OSError,sqlite3.Error):raise VoiceStoreError('无法读取当前账号语音数据库') from None
