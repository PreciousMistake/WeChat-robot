"""Multi-action plugin outbox, shared across callbacks and the sole UI sender."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import time
import uuid


class PluginJobs:
    def __init__(self,state,account):
        self.state=Path(state);self.account=account;self.path=self.state/'plugin_jobs.sqlite'
        self.state.mkdir(parents=True,exist_ok=True)
        with self.connection() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS owner(account TEXT PRIMARY KEY);
              CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,account TEXT,room TEXT,source_id TEXT,plugin TEXT,version TEXT,
              action_index INTEGER,kind TEXT,payload TEXT,guards TEXT,state TEXT,after_local_id INTEGER,outgoing_id TEXT,
              UNIQUE(account,room,source_id,plugin,action_index));
              CREATE UNIQUE INDEX IF NOT EXISTS proof_once ON jobs(account,room,outgoing_id) WHERE outgoing_id IS NOT NULL;''')
            row=db.execute('SELECT account FROM owner').fetchone()
            if row and row[0]!=account:raise ValueError('Plugin queue belongs to another account')
            db.execute('INSERT OR IGNORE INTO owner VALUES(?)',(account,))
        os.chmod(self.path,0o600)

    def connection(self):
        from contextlib import closing
        return _Database(self.path)

    @staticmethod
    def plugin_version(state,name):
        from wxbot_framework.config import validate_name
        validate_name(name);state=Path(state)
        config=json.loads((state/'plugin_config.json').read_text(encoding='utf-8-sig')) if (state/'plugin_config.json').exists() else {}
        pointer=state/'.runtime/plugin_code'/name/'active.json'
        value={'options':config.get('plugins',{}).get(name,{}),'source':pointer.read_text() if pointer.exists() else '','reload':config.get('reloads',{}).get(name,0)}
        return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

    def valid(self,job):
        if not job or job['account']!=self.account:return False
        try:
            ai=json.loads((self.state/'ai_config.json').read_text(encoding='utf-8-sig'))
            if job['plugin']=='image_generation':
                raw=(self.state/'image_config.json').read_bytes()
                return bool(ai.get('account')==self.account and ai.get('enabled') is True and not ai.get('draft_only') and
                    job['room'] in ai.get('systems',{}) and json.loads(raw).get('enabled') is True and
                    hashlib.sha256(raw).hexdigest()==job['version'] and
                    time.time()<=job.get('guards',{}).get('image_expires_at',0) and
                    job.get('guards',{}).get('image_auth_generation',0)==int(ai.get('authorization_generation',0)) and
                    job.get('guards',{}).get('image_system_hash')==hashlib.sha256(ai['systems'][job['room']].encode()).hexdigest())
            config=json.loads((self.state/'plugin_config.json').read_text(encoding='utf-8-sig'))
            options=config.get('plugins',{}).get(job['plugin'],{});scope=options.get('groups')
            valid=ai.get('account',self.account)==self.account and ai.get('enabled') is True and ai.get('draft_only',False) is not True and job['room'] in ai.get('systems',{}) and options.get('enabled') is True and (scope is None or isinstance(scope,list) and job['room'] in scope) and job['version']==self.plugin_version(self.state,job['plugin'])
            if valid and job.get('guards'):
                from .plugin_service import persistent_guards_valid
                valid=persistent_guards_valid(self.state,self.account,job['room'],job['guards'])
            return bool(valid)
        except (OSError,ValueError,TypeError,KeyError):return False

    def enqueue(self,room,source_id,plugin,version,index,kind,payload,*,guards=None):
        if type(index) is not int or index<0 or kind not in ('text','at_text','image','file','voice','emoticon','friend'):raise ValueError('Invalid plugin action')
        job=dict(account=self.account,room=room,source_id=str(source_id),plugin=plugin,version=version,guards=guards or {})
        if not self.valid(job):raise ValueError('Plugin action no longer authorized')
        if not isinstance(payload,dict):raise ValueError('Invalid action payload')
        with self.connection() as db:
            prior=db.execute('SELECT id FROM jobs WHERE account=? AND room=? AND source_id=? AND plugin=? AND action_index=?',(self.account,room,str(source_id),plugin,index)).fetchone()
            if prior:return prior[0]
        value=dict(payload)
        if kind in ('text','at_text'):
            text=value.get('text')
            if not isinstance(text,str) or not text.strip() or len(text.encode('utf-8'))>1023 or '\0' in text:raise ValueError('Invalid plugin text')
            if kind=='at_text':
                from wxbot_framework.text import validate_mentions
                validate_mentions(room,value.get('mentions',[]))
                if not 1<=len(value.get('mentions',[]))<=8:raise ValueError('At most eight native mentions supported')
        elif kind!='friend':
            source=Path(value.get('path','')).resolve()
            if not source.is_absolute() or not source.is_file() or source.is_symlink():raise ValueError('Artifact is not a regular file')
            maximum=50*1024*1024
            spool=self.state/'.runtime/plugin_spool';spool.mkdir(parents=True,exist_ok=True,mode=0o700)
            filename=uuid.uuid4().hex+source.suffix[:12];target=spool/filename
            if kind=='file':
                from wxbot_framework.files import safe_file_name
                directory=spool/uuid.uuid4().hex;directory.mkdir(mode=0o700);target=directory/safe_file_name(source.name)
            fd=os.open(str(source),os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_size>maximum:raise ValueError('Artifact is invalid or too large')
                with os.fdopen(fd,'rb',closefd=False) as src,target.open('xb') as dst:
                    copied=0;digest=hashlib.sha256()
                    for chunk in iter(lambda:src.read(1024*1024),b''):
                        copied+=len(chunk)
                        if copied>maximum:raise ValueError('Artifact grew past the limit')
                        digest.update(chunk);dst.write(chunk)
                os.chmod(target,0o600)
            finally:os.close(fd)
            value.update(path=str(target),sha256=digest.hexdigest(),md5=hashlib.md5(target.read_bytes()).hexdigest(),size=copied)
        ident=uuid.uuid4().hex
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)',(ident,self.account,room,str(source_id),plugin,version,index,kind,json.dumps(value,ensure_ascii=False),json.dumps(guards or {}),'ready'))
            return db.execute('SELECT id FROM jobs WHERE account=? AND room=? AND source_id=? AND plugin=? AND action_index=?',(self.account,room,str(source_id),plugin,index)).fetchone()[0]

    def get(self,ident):
        with self.connection() as db:
            db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM jobs WHERE id=?',(ident,)).fetchone()
        if not row:return None
        result=dict(row);encoded=result['payload'];result['payload']=json.loads(encoded);result['guards']=json.loads(result['guards'])
        payload=result['payload']
        # Upgrade pre-media-checksum jobs only from their private, hash-bound spool.
        if result['kind'] in ('image','file') and not payload.get('md5') and payload.get('path') and payload.get('sha256'):
            path=Path(payload['path'])
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to((self.state/'.runtime/plugin_spool').resolve()) and path.stat().st_size<=50*1024*1024:
                data=path.read_bytes()
                if hashlib.sha256(data).hexdigest()==payload['sha256']:
                    payload.update(md5=hashlib.md5(data).hexdigest(),size=len(data))
                    with self.connection() as db:db.execute('UPDATE jobs SET payload=? WHERE id=? AND payload=?',(json.dumps(payload,ensure_ascii=False),ident,encoded))
        return result

    def next(self):
        with self.connection() as db:rows=db.execute("SELECT id FROM jobs WHERE state='ready' ORDER BY rowid LIMIT 100").fetchall()
        for (ident,) in rows:
            job=self.get(ident)
            if self.valid(job):return job
            self.cancel(ident)
        return None

    def begin(self,ident,after):
        job=self.get(ident)
        if type(after) is not int or after<0 or not self.valid(job):raise ValueError('Plugin job is no longer current')
        with self.connection() as db:
            if db.execute("UPDATE jobs SET state='sending',after_local_id=? WHERE id=? AND state='ready'",(after,ident)).rowcount!=1:raise ValueError('Plugin action already attempted')

    def set_prepared_text(self,ident,text):
        job=self.get(ident)
        if not job or job['kind']!='at_text' or job['state']!='ready' or not self.valid(job) or not isinstance(text,str) or not text.endswith(job['payload']['text']) or len(text.encode('utf-8'))>1023:raise ValueError('Invalid prepared mention text')
        job['payload']['prepared_text']=text
        with self.connection() as db:
            if db.execute("UPDATE jobs SET payload=? WHERE id=? AND state='ready'",(json.dumps(job['payload'],ensure_ascii=False),ident)).rowcount!=1:raise ValueError('Mention job already attempted')

    @staticmethod
    def proof_matches(job,proof):
        server=str(proof.get('server_id',''))
        if (not job or job['state'] not in ('sending','uncertain') or proof.get('chat_id')!=job['room'] or proof.get('is_self') is not True or type(proof.get('local_id')) is not int or proof['local_id']<=job['after_local_id'] or not server.isascii() or not server.isdecimal() or int(server)<=0):return False
        kind,payload=job['kind'],job['payload']
        if kind=='text':return proof.get('type')==1 and proof.get('text')==payload['text']
        if kind=='at_text':return proof.get('type')==1 and isinstance(proof.get('text'),str) and proof['text'].strip()==payload.get('prepared_text','').strip() and proof['text'].endswith(payload['text']) and sorted(proof.get('at_user_ids',[]))==sorted(payload.get('mentions',[]))
        if kind=='image':return proof.get('type')==3 and proof.get('image_md5')==payload.get('md5')
        if kind=='file':return proof.get('type')==49 and proof.get('app_type')==6 and proof.get('file_md5')==payload.get('md5') and proof.get('file_size')==payload.get('size')
        return False

    def receipt_available(self,job,proof):
        with self.connection() as db:
            used=db.execute('SELECT id FROM jobs WHERE account=? AND room=? AND outgoing_id=? AND id<>?',
                (job['account'],job['room'],str(proof.get('server_id','')),job['id'])).fetchone()
        return used is None

    def confirm(self,ident,proof):
        job=self.get(ident)
        if not self.proof_matches(job,proof):raise ValueError('Plugin receipt does not match')
        try:
            with self.connection() as db:db.execute("UPDATE jobs SET state='sent',outgoing_id=? WHERE id=?",(str(proof['server_id']),ident))
        except sqlite3.IntegrityError as error:
            raise ValueError('Receipt already used by another plugin action') from error
        if job['kind'] in ('image','file','voice','emoticon'):
            path=Path(job['payload']['path'])
            if path.resolve().is_relative_to((self.state/'.runtime/plugin_spool').resolve()):path.unlink(missing_ok=True)

    def cancel(self,ident):
        with self.connection() as db:db.execute("UPDATE jobs SET state='cancelled' WHERE id=? AND state='ready'",(ident,))

    def fail(self,ident):
        with self.connection() as db:db.execute("UPDATE jobs SET state=CASE WHEN state='sending' THEN 'uncertain' ELSE 'blocked' END WHERE id=? AND state IN ('ready','sending')",(ident,))

    def recover(self):
        with self.connection() as db:db.execute("UPDATE jobs SET state='uncertain' WHERE state='sending'")

    def wait(self,ident,*,valid=lambda:True,timeout=90):
        until=time.monotonic()+timeout
        while True:
            job=self.get(ident)
            if not job:return False
            if job['state']=='sent':return True
            if job['state'] not in ('ready','sending'):return False
            if not valid() or not self.valid(job) or time.monotonic()>=until:
                self.cancel(ident);return False
            time.sleep(.1)


class _Database:
    def __init__(self,path):self.path=path
    def __enter__(self):
        self.db=sqlite3.connect(self.path,timeout=10);self.db.execute('PRAGMA synchronous=FULL');return self.db
    def __exit__(self,typ,value,tb):
        try:self.db.rollback() if typ else self.db.commit()
        finally:self.db.close()
