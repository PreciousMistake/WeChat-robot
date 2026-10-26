"""The receiver is the only process allowed to commit plugin UI actions."""
from pathlib import Path
from .plugin_jobs import PluginJobs
from .sender import ClientDriver,resolve_group
from .dispatch import select_room_shard
from .delivery import deliver


class TextAction:
    def __init__(self,jobs,ident):self.jobs=jobs;self.ident=ident
    def get(self,*args):
        row=self.jobs.get(self.ident)
        return dict(state='draft' if row['state']=='ready' else row['state'],reply=row['payload']['text'],outgoing_id=row['outgoing_id'])
    def begin_send(self,room,source,after):self.jobs.begin(self.ident,after)
    def confirm(self,room,source,proof):self.jobs.confirm(self.ident,proof)
    def uncertain(self,*args):self.jobs.fail(self.ident)


def reconcile_media(jobs,box,root,keys):
    from .worker import snapshot,read_rows,table_name
    with jobs.connection() as db:rows=db.execute("SELECT id FROM jobs WHERE state='uncertain' AND kind IN ('image','file','at_text') ORDER BY rowid LIMIT 5").fetchall()
    for (ident,) in rows:
        job=jobs.get(ident);room=job['room']
        if room not in box.groups:continue
        try:
            filename,key=select_room_shard(root,keys,table_name(room),snapshot)
            db,_=snapshot(root/filename,key)
            try:proofs=read_rows(db,room,job['after_local_id'],include_metadata=True) or []
            finally:db.close()
            matches=[]
            for proof in proofs:
                value=dict(proof,chat_id=room,is_self=proof.get('sender_id')==box.account)
                if jobs.proof_matches(job,value) and jobs.receipt_available(job,value):matches.append(value)
            if len(matches)==1:
                jobs.confirm(ident,matches[0]);return {'plugin_delivery':'late_confirmed','plugin':job['plugin'],'source_id':job['source_id'],'outgoing_id':matches[0]['server_id']}
        except (ValueError,OSError):continue
    return None


def step(state,box,root,keys,contact_key,*,active=lambda:True):
    from .worker import snapshot,read_rows,table_name
    jobs=PluginJobs(state,box.account)
    reconciled=reconcile_media(jobs,box,root,keys)
    if reconciled:return reconciled
    job=jobs.next()
    if not job:return None
    ident=job['id'];room=job['room']
    current=lambda:active() and jobs.valid(jobs.get(ident))
    stage='scope'
    try:
        if room not in box.groups or not contact_key:raise ValueError('Plugin group not verified')
        if job['kind'] not in ('text','at_text','image','file'):raise ValueError('Plugin media adapter is not yet verified')
        if job['kind'] in ('image','file') and job['kind']+'.send' not in __import__('json').loads((state/'capabilities.json').read_text()).get('capabilities',[]):raise ValueError('Plugin media adapter is not yet verified')
        stage='resolve_group'
        contacts,_=snapshot(root/'contact/contact.db',contact_key)
        try:name=resolve_group(contacts,room)
        finally:contacts.close()
        filename,key=select_room_shard(root,keys,table_name(room),snapshot)
        pids=[]
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():continue
            try:
                if (proc/'comm').read_text().strip()=='wechat':pids.append(int(proc.name))
            except OSError:pass
        if len(pids)!=1:raise ValueError('Client process is ambiguous')
        stage='prepare_and_send'
        from .media_sender import MediaDriver
        from .mention_sender import MentionDriver
        driver=(MentionDriver if job['kind']=='at_text' else MediaDriver if job['kind'] in ('image','file') else ClientDriver)(pids[0]);driver.max_text_chars=1023;driver.active=current
        def watermark():
            db,_=snapshot(root/filename,key)
            try:return db.execute('SELECT MAX(local_id) FROM '+table_name(room)).fetchone()[0] or 0
            finally:db.close()
        def proofs(after):
            db,_=snapshot(root/filename,key)
            try:rows=read_rows(db,room,after,include_metadata=True) or []
            finally:db.close()
            return [dict(row,chat_id=room,is_self=row['sender_id']==box.account) for row in rows]
        if job['kind']=='text':deliver(TextAction(jobs,ident),room,ident,name,driver,watermark,proofs,active=current)
        elif job['kind']=='at_text':
            from .mention_sender import resolve_members
            from .mention_delivery import deliver_mentions
            import json
            members=resolve_members(json.loads((state/'group_members.json').read_text()),box.account,room,job['payload']['mentions'])
            deliver_mentions(jobs,job,name,members,driver,watermark,proofs,active=current)
        else:
            from .media_delivery import deliver_media
            deliver_media(jobs,job,name,driver,watermark,proofs,active=current)
        return {'plugin_delivery':'sent','plugin':job['plugin'],'source_id':job['source_id'],'outgoing_id':jobs.get(ident)['outgoing_id']}
    except Exception as error:
        jobs.fail(ident)
        reason=str(error) if str(error) in ('Input text does not match draft','Active chat title does not prove the target group','Client contains an unsent draft','Client window lost focus','Client geometry changed','Delivery cancelled','Plugin job is no longer current','Not a fresh draft','Expected one visible main client window','Exact group search result was not found','Staged image includes unexpected text','Staged image differs from artifact','No bounded staged image','Unexpected native file dialog','File dialog did not return to target chat','Staged file differs from artifact','Expected one staged local file','Media send not confirmed','Native mention send not confirmed','Mention input differs from intended text','Selected native mention is absent from input','Unique native mention popup not found','Native mention caption does not match member','Native mention popup changed','Native mention selection did not finish') else ''
        return {'plugin_delivery':'blocked_or_uncertain','plugin':job['plugin'],'error_type':type(error).__name__,'stage':stage,'reason':reason}
