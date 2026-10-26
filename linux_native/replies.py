"""Durable model/delivery states; ambiguous UI sends never auto-retry."""
import json
import time


class ReplyQueue:
    def __init__(self,inbox):
        self.box=inbox;self.db=inbox.db
        self.db.execute('''CREATE TABLE IF NOT EXISTS replies(
            room TEXT,source_id TEXT,state TEXT,decision TEXT,reply TEXT,
            outgoing_id TEXT,after_local_id INTEGER DEFAULT 0,
            PRIMARY KEY(room,source_id))''')
        self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS unique_outgoing ON replies(room,outgoing_id) WHERE outgoing_id IS NOT NULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS reply_owner_bindings(room TEXT,source_id TEXT,owner_wxid TEXT,PRIMARY KEY(room,source_id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS model_attempts(room TEXT,source_id TEXT,attempts INTEGER,next_retry REAL,PRIMARY KEY(room,source_id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS delivery_events(room TEXT,source_id TEXT,kind TEXT,sent_at REAL,PRIMARY KEY(room,source_id))')
        self.db.commit()
        from .voice_replies import VoiceReplies
        VoiceReplies(self)

    def recover(self,*,model=True,delivery=True):
        # Only the exclusive AI worker may invoke recovery.
        with self.db:
            if model:self.db.execute("UPDATE inbox SET state='pending' WHERE state='processing'")
            if delivery:
                self.db.execute("UPDATE replies SET state='uncertain' WHERE state='sending'")
                self.db.execute("UPDATE voice_replies SET state='uncertain' WHERE state='sending'")

    def uncertain(self,room,ident):
        if room not in self.box.groups:raise ValueError('Group not allowlisted')
        with self.db:self.db.execute("UPDATE replies SET state='uncertain' WHERE room=? AND source_id=? AND state='sending'",(room,str(ident)))

    def claim(self,now=None):
        now=time.time() if now is None else now
        rooms=sorted(self.box.groups)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute("SELECT i.room,i.server_id,i.payload FROM inbox i LEFT JOIN model_attempts m ON m.room=i.room AND m.source_id=i.server_id WHERE i.state='pending' AND (m.next_retry IS NULL OR m.next_retry<=?) AND i.room IN ("+
                ','.join('?' for _ in rooms)+') ORDER BY i.created_at,CAST(json_extract(i.payload,\'$.local_id\') AS INTEGER) LIMIT 1',[now,*rooms]).fetchone()
            if not row:return None
            self.db.execute("UPDATE inbox SET state='processing' WHERE room=? AND server_id=?",row[:2])
            return json.loads(row[2])

    def fail_model(self,message,*,retryable,now=None):
        now=time.time() if now is None else now
        room,ident=message['chat_id'],str(message['server_id'])
        if room not in self.box.groups:raise ValueError('Group not allowlisted')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT attempts FROM model_attempts WHERE room=? AND source_id=?',(room,ident)).fetchone()
            attempts=(row[0] if row else 0)+1
            self.db.execute('INSERT INTO model_attempts VALUES(?,?,?,?) ON CONFLICT(room,source_id) DO UPDATE SET attempts=excluded.attempts,next_retry=excluded.next_retry',
                (room,ident,attempts,now+min(120,5*2**min(attempts-1,5))))
            self.db.execute("UPDATE inbox SET state=? WHERE room=? AND server_id=? AND state='processing'",
                ('pending' if retryable and attempts<3 else 'failed',room,ident))

    def stage(self,message,decision,reply,*,media_mode='text',owner_wxid=''):
        room=message['chat_id'];ident=str(message['server_id'])
        if room not in self.box.groups:raise ValueError('Group not allowlisted')
        if decision not in ('ignore','direct','participate') or not isinstance(reply,str):raise ValueError('Invalid decision')
        if decision!='ignore' and (not reply.strip() or len(reply)>400):raise ValueError('Invalid reply')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT state FROM inbox WHERE room=? AND server_id=?',(room,ident)).fetchone()
            if row!=('processing',):raise ValueError('Source is not claimed')
            state='ignored' if decision=='ignore' else 'draft'
            self.db.execute('INSERT INTO replies(room,source_id,state,decision,reply) VALUES(?,?,?,?,?)',
                (room,ident,state,decision,'' if decision=='ignore' else reply.strip()))
            self.db.execute('INSERT INTO reply_owner_bindings VALUES(?,?,?)',(room,ident,owner_wxid))
            self.db.execute('UPDATE inbox SET state=? WHERE room=? AND server_id=?',('done' if decision=='ignore' else 'draft',room,ident))
            if decision!='ignore' and media_mode in ('voice','both'):
                self.db.execute("INSERT INTO voice_replies(room,source_id,mode,state) VALUES(?,?,?,'pending')",(room,ident,media_mode))

    def get(self,room,ident):
        if room not in self.box.groups:raise ValueError('Group not allowlisted')
        row=self.db.execute('SELECT state,decision,reply,outgoing_id,after_local_id FROM replies WHERE room=? AND source_id=?',(room,str(ident))).fetchone()
        return dict(zip(('state','decision','reply','outgoing_id','after_local_id'),row)) if row else None

    def begin_send(self,room,ident,after_local_id):
        if room not in self.box.groups or type(after_local_id) is not int or after_local_id<0:raise ValueError('Invalid send target')
        with self.db:
            changed=self.db.execute("UPDATE replies SET state='sending',after_local_id=? WHERE room=? AND source_id=? AND state='draft'",
                (after_local_id,room,str(ident))).rowcount
            if changed!=1:raise ValueError('Reply is not a fresh draft')
        return self.get(room,ident)['reply']

    def confirm(self,room,ident,proof):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.get(room,ident)
            outgoing=str(proof.get('server_id',''))
            if (not row or row['state'] not in ('sending','uncertain') or proof.get('chat_id')!=room or
                proof.get('text')!=row['reply'] or proof.get('is_self') is not True or
                not outgoing.isascii() or not outgoing.isdecimal() or int(outgoing)<=0 or
                type(proof.get('local_id')) is not int or proof['local_id']<=row['after_local_id']):
                raise ValueError('Outgoing message proof does not match')
            if self.db.execute('SELECT 1 FROM replies WHERE room=? AND outgoing_id=?',(room,outgoing)).fetchone():
                raise ValueError('Outgoing message is already correlated')
            self.db.execute("UPDATE replies SET state='sent',outgoing_id=? WHERE room=? AND source_id=?",(outgoing,room,str(ident)))
            self.db.execute("UPDATE inbox SET state='done' WHERE room=? AND server_id=?",(room,str(ident)))
            self.db.execute('INSERT INTO delivery_events VALUES(?,?,?,?)',(room,str(ident),row['decision'],time.time()))
