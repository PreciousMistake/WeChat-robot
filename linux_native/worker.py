"""Non-root receiver. One key bundle arrives via stdin, never via disk/argv."""
import ctypes
import ctypes.util
import hashlib
import json
import os
import re
import signal
import sqlite3
import sys
import threading
import time
from pathlib import Path

from .inbox import DurableInbox


def text_value(value):
    if isinstance(value,str):return value
    if not isinstance(value,bytes):return ''
    if value.startswith(b'\x28\xb5\x2f\xfd'):
        z=ctypes.CDLL(ctypes.util.find_library('zstd'))
        z.ZSTD_decompress.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_size_t]
        z.ZSTD_decompress.restype=ctypes.c_size_t
        buf=ctypes.create_string_buffer(1024*1024)
        size=z.ZSTD_decompress(buf,len(buf),value,len(value))
        if size>len(buf):raise ValueError('Compressed text exceeds limit or is invalid')
        value=buf.raw[:size]
    return value.decode('utf-8')


def voice_duration(content):
    match=re.search(r'\bvoicelength=["\']([0-9]{1,6})["\']',content)
    return int(match[1]) if match else 0


def native_mentions(source):
    from .plugin_events import _xml
    from wxbot_framework.text import WXID
    root=_xml(source)
    if root is None or root.tag!='msgsource':return []
    lists=root.findall('atuserlist')
    if len(lists)!=1 or len(lists[0]):return []
    values=[v.strip() for v in (lists[0].text or '').split(',') if v.strip()]
    if len(values)>64 or any(not WXID.fullmatch(v) or v.endswith('@chatroom') for v in values):return []
    return list(dict.fromkeys(values))


def table_name(room):return 'Msg_'+hashlib.md5(room.encode()).hexdigest()


def read_rows(db,room,after,limit=1000,min_create_time=None,include_metadata=False):
    table=table_name(room)
    if not db.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?',('table',table)).fetchone():return None
    columns={row[1] for row in db.execute('PRAGMA table_info(Name2Id)')}
    username=next((name for name in ('user_name','username','UsrName') if name in columns),None)
    if username is None:raise ValueError('Unsupported sender identity schema')
    rows=[]
    timing=' AND create_time>=?' if min_create_time is not None else ''
    arguments=[after]+([min_create_time] if min_create_time is not None else [])+[limit]
    fields={r[1] for r in db.execute('PRAGMA table_info('+table+')')}
    extra_names=(['create_time'] if include_metadata and 'create_time' in fields else [])+(['source'] if 'source' in fields else [])
    extra=''.join(',CAST(source AS BLOB)' if name=='source' else ',create_time' for name in extra_names)
    for raw_row in db.execute(
            'SELECT local_id,server_id,real_sender_id,local_type,CAST(message_content AS BLOB),CAST(compress_content AS BLOB)'+extra+' FROM '+table+
            ' WHERE local_id>?'+timing+' ORDER BY local_id LIMIT ?',arguments):
        local,server,sender,kind,content,compressed=raw_row[:6]
        extra_values=dict(zip(extra_names,raw_row[6:]))
        subtype=kind>>32;kind=kind&0xffffffff
        identity=db.execute('SELECT '+username+' FROM Name2Id WHERE rowid=?',(sender,)).fetchone()
        sender=identity[0] if identity and isinstance(identity[0],str) else ''
        error=None
        try:
            text=text_value(content) if kind==1 else ''
            if kind==1 and not text and compressed:text=text_value(compressed)
        except (UnicodeError,ValueError):text='';error='invalid_text'
        prefix=sender+':\n'
        if sender and text.startswith(prefix):text=text[len(prefix):]
        item={'local_id':local,'server_id':str(server),'sender_id':sender,'type':kind,'text':text}
        try:
            mentions=native_mentions(text_value(extra_values.get('source') or b''))
            if mentions:item['at_user_ids']=mentions
        except (UnicodeError,ValueError):pass
        if include_metadata:
            item.update(native_message_id=str(server),create_time=extra_values.get('create_time',0))
            if subtype:item['app_type']=subtype
            if kind not in (1,34):
                try:
                    raw=text_value(content or compressed or b'')
                    if raw.startswith(prefix):raw=raw[len(prefix):]
                    if len(raw.encode('utf-8'))<=65536:
                        item['raw_content']=raw
                        from .plugin_events import _xml,parse_file_metadata
                        if kind==3:
                            xml=_xml(raw);nodes=xml.findall('.//img') if xml is not None else []
                            if len(nodes)==1 and re.fullmatch(r'[0-9a-fA-F]{32}',nodes[0].get('md5','')):item['image_md5']=nodes[0].get('md5').lower()
                        elif kind==49:
                            metadata=parse_file_metadata(raw)
                            if metadata:item.update(file_md5=metadata['md5'],file_size=metadata['size'],file_name=metadata['file_name'],app_type=6)
                except (UnicodeError,ValueError):pass
        if kind==34:
            try:item['voice_duration_ms']=voice_duration(text_value(content or compressed or b''))
            except (UnicodeError,ValueError):item['voice_duration_ms']=0
        if error:item['decode_error']=error
        rows.append(item)
    return rows


def ingest_database(db,box,stream,room,min_create_time=None):
    cursor=box.cursor(stream,room)
    if cursor is None and not box.bootstrap_complete:
        table=table_name(room)
        exists=db.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?',('table',table)).fetchone()
        maximum=(db.execute('SELECT MAX(local_id) FROM '+table).fetchone()[0] or 0) if exists else 0
        box.ingest(stream,room,[{'local_id':maximum}])
        return True
    if cursor is None:box.ingest(stream,room,[]);cursor=0
    rows=read_rows(db,room,cursor,min_create_time=min_create_time)
    if rows is None:return True
    for row in rows:
        if row.get('at_user_ids'):row['is_at_self']=box.account in row['at_user_ids']
    box.ingest(stream,room,rows)
    return len(rows)<1000


def fingerprint(path):
    rows=[]
    for p in (path,Path(str(path)+'-wal')):
        try:s=p.stat();rows.append((s.st_ino,s.st_size,s.st_mtime_ns))
        except FileNotFoundError:rows.append(None)
    return rows


def snapshot(path,key):
    from .voice_store import decode_snapshot,MAX_DATABASE_BYTES
    for _ in range(3):
        before=fingerprint(path)
        if any(stat and stat[1]>MAX_DATABASE_BYTES for stat in before):raise ValueError('Snapshot exceeds limit')
        raw=path.read_bytes();wal=Path(str(path)+'-wal')
        journal=wal.read_bytes() if wal.exists() else b''
        if before!=fingerprint(path):continue
        plain=bytearray(decode_snapshot(raw,journal,key));plain[18:20]=b'\x01\x01'
        db=sqlite3.connect(':memory:')
        db.deserialize(bytes(plain));db.execute('PRAGMA query_only=ON')
        return db,before
    raise ValueError('Database snapshot is changing')


def main():
    os.umask(0o077)
    config=json.loads(sys.stdin.readline(32768))
    home=Path('/home/wechat').resolve()
    root=(home/'xwechat_files'/config['storage']/'db_storage').resolve()
    if not root.is_relative_to(home/'xwechat_files'):raise ValueError('Invalid account storage')
    account=config['account'];groups=config['groups']
    if not account.startswith('wxid_') or not groups or any(not re.fullmatch(r'[0-9]+@chatroom',g) for g in groups):
        raise ValueError('Invalid account or groups')
    supplied=config.pop('keys')
    contact_key=bytes.fromhex(supplied['contact/contact.db']) if 'contact/contact.db' in supplied else None
    media_keys={name:bytes.fromhex(value) for name,value in supplied.items() if re.fullmatch(r'message/media_[0-9]+\.db',name)}
    keys={name:bytes.fromhex(value) for name,value in supplied.items()
          if re.fullmatch(r'message/message_[0-9]+\.db',name)}
    del supplied
    if not keys:raise ValueError('No authenticated message keys')
    state=home/'.local/share/wechat-access'
    state.mkdir(parents=True,exist_ok=True,mode=0o700)
    import fcntl
    lock=(state/'receiver.lock').open('a+b')
    fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    box=DurableInbox(state/'inbox.sqlite',account,groups)
    from .receiver_plugins import PluginObservations
    from .group_contacts import ContactGroups
    from .plugin_jobs import PluginJobs
    from .admin import atomic_json
    observations=PluginObservations(state,account,root.parent)
    contact_groups=ContactGroups(state,account,observations.events)
    group_snapshot={}
    PluginJobs(state,account).recover()
    atomic_json(state/'capabilities.json',{'account':account,'capabilities':['text.receive','text.send','group.text.receive','group.text.send','file.receive','image.send','group.text.mention','group.events']})
    from .replies import ReplyQueue
    ReplyQueue(box).recover(model=False)
    stopping=[False]
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stopping.__setitem__(0,True))
    def watch_parent():
        # A dead supervisor closes stdin; orphan workers stop rather than duplicate polling.
        sys.stdin.readline()
        stopping[0]=True
    threading.Thread(target=watch_parent,daemon=True).start()
    if media_keys:
        from .asr_worker import RecognitionWorker
        RecognitionWorker(state,root,account,media_keys,lambda:not stopping[0]).start()
    until=time.monotonic()+config.get('run_seconds',0) if config.get('run_seconds') else float('inf')
    stamps={};polls=0;last_status=0;last_scope=None;contact_stamp=None;last_plugin_scan={}
    try:
        while not stopping[0] and time.monotonic()<until:
            options=state/'ai_config.json'
            ai=json.loads(options.read_text()) if options.exists() else {'account':account,'systems':{g:'' for g in groups}}
            if ai.get('account')!=account:raise ValueError('Configuration account changed')
            next_groups=list(ai['systems'])
            if any(not re.fullmatch(r'[0-9]+@chatroom',g) for g in next_groups):raise ValueError('Invalid group scope')
            scope=(tuple(sorted(next_groups)),json.dumps(ai.get('activation_after',{}),sort_keys=True))
            if scope!=last_scope:
                removed=box.groups-set(next_groups)
                with box.db:
                    for room in removed:
                        box.db.execute("UPDATE inbox SET state='done' WHERE room=? AND state IN ('pending','processing','draft')",(room,))
                        box.db.execute("UPDATE replies SET state='archived' WHERE room=? AND state='draft'",(room,))
                        box.db.execute("UPDATE audio_inbox SET state='disabled' WHERE room=? AND state IN ('pending','processing')",(room,))
                        box.db.execute("UPDATE voice_replies SET state='archived',wav=NULL WHERE room=? AND state IN ('pending','ready')",(room,))
                groups=next_groups;box.groups=frozenset(groups);stamps.clear();last_scope=scope;contact_stamp=None
            asr_path=state/'asr_config.json'
            asr=json.loads(asr_path.read_text()) if asr_path.exists() else {}
            box.voice_groups=frozenset(asr.get('groups',[])) if asr.get('enabled') is True else frozenset()
            if contact_key and fingerprint(root/'contact/contact.db')!=contact_stamp:
                contacts,contact_stamp=snapshot(root/'contact/contact.db',contact_key)
                try:
                    catalog=[{'id':row[0],'name':row[1],'remark':row[2] or ''} for row in contacts.execute(
                        "SELECT username,nick_name,remark FROM contact WHERE username LIKE '%@chatroom' AND is_in_chat_room=1") if isinstance(row[1],str) and row[1]]
                    group_snapshot=contact_groups.read(contacts,groups)
                finally:contacts.close()
                from .admin import atomic_json
                atomic_json(state/'groups.json',{'account':account,'groups':catalog})
            contact_groups.update(group_snapshot,groups)
            files={str(p.relative_to(root)) for p in (root/'message').glob('message_*.db')
                   if re.fullmatch(r'message_[0-9]+\.db',p.name)}
            if files-keys.keys():raise RuntimeError('Message shard changed; rediscovery required')
            media_files={str(p.relative_to(root)) for p in (root/'message').glob('media_*.db') if re.fullmatch(r'media_[0-9]+\.db',p.name)}
            if asr.get('enabled') is True and media_files-media_keys.keys():raise RuntimeError('Media shard changed; rediscovery required')
            for name,key in keys.items():
                path=root/name
                if stamps.get(name)==fingerprint(path) and time.monotonic()-last_plugin_scan.get(name,0)<10:continue
                db,stamp=snapshot(path,key)
                complete=True
                try:
                    for room in groups:
                        if not ingest_database(db,box,name,room,min_create_time=ai.get('activation_after',{}).get(room)):complete=False
                        observations.observe(db,name,room)
                finally:db.close()
                last_plugin_scan[name]=time.monotonic()
                if complete:stamps[name]=stamp
            if not box.bootstrap_complete:box.finish_bootstrap()
            if options.exists():
                from .dispatch import step
                version=options.stat().st_mtime_ns
                tts_path=state/'tts_config.json'
                tts_bytes=tts_path.read_bytes() if tts_path.exists() else b'{}'
                result=step(box,root,keys,contact_key,json.loads(options.read_text()),tts_options=json.loads(tts_bytes),active=lambda:not stopping[0] and options.stat().st_mtime_ns==version and (tts_path.read_bytes() if tts_path.exists() else b'{}')==tts_bytes)
                if result:print(json.dumps(result),flush=True)
                from .plugin_dispatch import step as plugin_step
                result=plugin_step(state,box,root,keys,contact_key,active=lambda:not stopping[0])
                if result:print(json.dumps(result),flush=True)
            polls+=1
            if time.monotonic()-last_status>=30:
                with box.db:box.db.execute("INSERT INTO metadata VALUES('receiver_heartbeat',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(str(time.time()),))
                print(json.dumps({'receiver':'ready','polls':polls,**box.stats()}),flush=True)
                last_status=time.monotonic()
            time.sleep(2)
        print(json.dumps({'receiver':'stopped','polls':polls,**box.stats()}),flush=True)
    finally:
        observations.close();box.close();lock.close()


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'receiver':'failed','error_type':type(error).__name__}),flush=True)
        raise SystemExit(1)
