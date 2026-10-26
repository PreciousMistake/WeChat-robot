"""Bounded Linux process scan, authenticated against this account's DB pages."""
import hashlib
import hmac
import re
import struct
import time
from pathlib import Path


def valid_key(key,page):
    if len(key)!=32 or len(page)!=4096:return False
    mac=hashlib.pbkdf2_hmac('sha512',key,bytes(x^0x3a for x in page[:16]),2,32)
    return hmac.compare_digest(hmac.new(mac,page[16:4032]+struct.pack('<I',1),hashlib.sha512).digest(),page[4032:])


def decode_buffer(blob,page):
    if len(blob)!=99 or len(page)!=4096:return None
    pad=bytearray(32)
    for index,value in enumerate(page[:16].hex().encode(),66):pad[index%32]=blob[index]^value
    literal=bytes(value^pad[index%32] for index,value in enumerate(blob))
    if not re.fullmatch(rb"x'[0-9a-fA-F]{96}'",literal):return None
    key=bytes.fromhex(literal[2:66].decode())
    return (key,bytes(pad)) if valid_key(key,page) else None


def masked_pattern(pad):
    pattern=b''
    for index in range(99):
        values=b'x' if index==0 else b"'" if index in (1,98) else b'0123456789abcdefABCDEF'
        pattern+=b'['+b''.join(('\\x%02x'%(value^pad[index%32])).encode() for value in values)+b']'
    return re.compile(pattern)


def regions(pid):
    readable=[];writable=[]
    for line in Path(f'/proc/{pid}/maps').read_text().splitlines():
        fields=line.split();start,end=(int(v,16) for v in fields[0].split('-'))
        if 'r' in fields[1]:readable.append((start,end))
        if 'r' in fields[1] and 'w' in fields[1]:writable.append((start,end))
    resident=[]
    with open(f'/proc/{pid}/pagemap','rb',buffering=0) as pages:
        for start,end in writable:
            run=None
            for base in range(start,end,4096*65536):
                count=min(65536,(end-base)//4096)
                pages.seek(base//4096*8)
                for index,(entry,) in enumerate(struct.iter_unpack('<Q',pages.read(count*8))):
                    address=base+index*4096
                    if entry&(1<<63):
                        if run is None:run=address
                    elif run is not None:resident.append((run,address));run=None
            if run is not None:resident.append((run,end))
    return readable,sorted(resident,key=lambda pair:pair[1]-pair[0])


def scan(pid,pages,required,timeout=35):
    deadline=time.monotonic()+timeout
    readable,resident=regions(pid);pads=set();found={};seen=set()
    def match(key):
        for name in required:
            if name not in found and valid_key(key,pages[name]):found[name]=key
    def chunks(memory):
        scanned=0
        for start,end in resident:
            pos=start;carry=b''
            while pos<end and time.monotonic()<deadline and scanned<1024**3:
                try:memory.seek(pos);chunk=memory.read(min(1024*1024,end-pos))
                except OSError:break
                if not chunk:break
                data=carry+chunk;scanned+=len(chunk)
                yield data
                carry=data[-128:];pos+=len(chunk)
    with open(f'/proc/{pid}/mem','rb',buffering=0) as memory:
        # Seed a pad using pointer/length pairs, then search full masked literals.
        for data in chunks(memory):
            for hit in re.finditer(rb'[\x63\x64]\x00{7}',data):
                offset=hit.start()
                for relative in (-8,8,-16,16):
                    where=offset+relative
                    if where<0 or where+8>len(data):continue
                    pointer=struct.unpack_from('<Q',data,where)[0]
                    if pointer in seen or not any(a<=pointer and pointer+99<=b for a,b in readable):continue
                    seen.add(pointer)
                    if len(seen)>100000:raise RuntimeError('Candidate budget exceeded')
                    try:memory.seek(pointer);blob=memory.read(99)
                    except OSError:continue
                    for page in pages.values():
                        result=decode_buffer(blob,page)
                        if result:
                            key,pad=result;pads.add(pad);match(key);break
                    if pads:break
                if pads:break
            if pads or required<=found.keys():break
        patterns=[(pad,masked_pattern(pad)) for pad in pads]
        for data in chunks(memory):
            for hit in re.finditer(rb"x'([0-9a-fA-F]{64,192})'",data):
                match(bytes.fromhex(hit.group(1)[:64].decode()))
            for pad,pattern in patterns:
                for hit in pattern.finditer(data):
                    blob=hit.group()
                    literal=bytes(value^pad[index%32] for index,value in enumerate(blob))
                    match(bytes.fromhex(literal[2:66].decode()))
            if required<=found.keys():break
    if not required<=found.keys():raise RuntimeError('Authenticated message keys unavailable')
    return found
