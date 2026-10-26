"""Receiver-side sender using its in-memory authenticated key bundle."""
import json
import time
from pathlib import Path

from .delivery import deliver
from .replies import ReplyQueue
from .sender import ClientDriver,resolve_group


def select_room_shard(root,keys,table,snapshot):
    matches=[]
    for filename,key in keys.items():
        db,_=snapshot(root/filename,key)
        try:
            if db.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?',('table',table)).fetchone():
                matches.append((filename,key))
        finally:db.close()
    if len(matches)!=1:raise ValueError('Target group message shard is missing or ambiguous')
    return matches[0]


def step(box,root,keys,contact_key,options,*,tts_options=None,active=lambda:True):
    if options.get('enabled') is not True or options.get('draft_only',True):return None
    from .worker import snapshot,read_rows,table_name
    queue=ReplyQueue(box)
    from .voice_replies import VoiceReplies
    voices=VoiceReplies(queue)
    voices.reconcile({r:v for r,v in options.get('group_settings',{}).items() if r in box.groups})
    tts_enabled=bool(tts_options and tts_options.get('enabled',bool(tts_options.get('api_key'))))
    row=box.db.execute("""SELECT r.room,r.source_id,r.decision FROM replies r
        LEFT JOIN voice_replies v ON v.room=r.room AND v.source_id=r.source_id
        WHERE (r.state='draft' AND (v.mode IS NULL OR v.mode IN ('text','both') OR (v.mode='voice' AND v.state='ready' AND ?)))
        OR (r.state='sent' AND v.mode='both' AND v.state='ready' AND ?) ORDER BY r.rowid LIMIT 1""",(tts_enabled,tts_enabled)).fetchone()
    if not row:return None
    room,ident,kind=row
    part=voices.get(room,ident)
    voice_part=bool(part and (part['mode']=='voice' or (part['mode']=='both' and queue.get(room,ident)['state']=='sent')))
    if room not in box.groups or room not in options['systems']:return None
    from .ownership import guard_owner_reply
    settings=options.get('group_settings',{}).get(room,{})
    binding=box.db.execute('SELECT owner_wxid FROM reply_owner_bindings WHERE room=? AND source_id=?',(room,ident)).fetchone()
    source=box.db.execute('SELECT payload FROM inbox WHERE room=? AND server_id=?',(room,ident)).fetchone()
    reply=queue.get(room,ident)['reply']
    if ((binding[0] if binding else '')!=settings.get('owner_wxid','') or not source or
        guard_owner_reply(reply,json.loads(source[0])['sender_id'],settings)!=reply):
        with box.db:
            box.db.execute("UPDATE replies SET state='blocked' WHERE room=? AND source_id=? AND state='draft'",(room,ident))
            box.db.execute("UPDATE voice_replies SET state='blocked',wav=NULL WHERE room=? AND source_id=? AND state IN ('pending','ready')",(room,ident))
            box.db.execute("UPDATE inbox SET state='done' WHERE room=? AND server_id=?",(room,ident))
        return {'delivery':'owner_binding_changed','source_id':ident}

    try:
        if not keys or not contact_key:raise ValueError('Verified sender context required')
        settings=options['group_settings'][room]
        if kind=='participate' and queue.get(room,ident)['state']!='sent':
            recent=box.db.execute("SELECT sent_at FROM delivery_events WHERE room=? AND kind='participate' AND sent_at>? ORDER BY sent_at DESC",
                (room,time.time()-3600)).fetchall()
            if len(recent)>=settings['hourly_limit'] or (recent and time.time()-recent[0][0]<settings['cooldown_seconds']):
                with box.db:
                    box.db.execute("UPDATE replies SET state='rate_limited' WHERE room=? AND source_id=?",(room,ident))
                    box.db.execute("UPDATE inbox SET state='done' WHERE room=? AND server_id=?",(room,ident))
                return {'delivery':'rate_limited','source_id':ident}
        db,_=snapshot(root/'contact/contact.db',contact_key)
        try:name=resolve_group(db,room)
        finally:db.close()
        pids=[]
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():continue
            try:
                if (proc/'comm').read_text().strip()=='wechat':pids.append(int(proc.name))
            except OSError:pass
        if len(pids)!=1:raise ValueError('Main client process is ambiguous')
        driver=ClientDriver(pids[0])
        driver.active=active
        filename,key=select_room_shard(root,keys,table_name(room),snapshot);path=root/filename
        def watermark():
            db,_=snapshot(path,key)
            try:return db.execute('SELECT MAX(local_id) FROM '+table_name(room)).fetchone()[0] or 0
            finally:db.close()
        def proofs(after):
            db,_=snapshot(path,key)
            try:rows=read_rows(db,room,after) or []
            finally:db.close()
            return [dict(row,chat_id=room,is_self=row['sender_id']==box.account) for row in rows]
        if voice_part:
            from .voice_delivery import deliver_voice
            deliver_voice(voices,room,ident,name,driver,watermark,proofs,active=active)
        else:deliver(queue,room,ident,name,driver,watermark,proofs,active=active)
        return {'delivery':'voice_sent' if voice_part else 'sent','source_id':ident,'outgoing_id':(voices if voice_part else queue).get(room,ident)['outgoing_id']}
    except Exception as error:
        with box.db:
            box.db.execute("UPDATE replies SET state='blocked' WHERE room=? AND source_id=? AND state='draft'",(room,ident))
            if voice_part:box.db.execute("UPDATE voice_replies SET state='blocked' WHERE room=? AND source_id=? AND state='ready'",(room,ident))
        return {'delivery':'blocked_or_uncertain','source_id':ident,'error_type':type(error).__name__}
