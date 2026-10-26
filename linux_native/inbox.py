"""Transactional allowlisted inbox; encryption keys never enter this store."""
import json
import os
import sqlite3
import time
from pathlib import Path


class DurableInbox:
    def __init__(self,path,account,groups):
        self.account=account;self.groups=frozenset(groups)
        self.voice_groups=frozenset()
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.db=sqlite3.connect(path)
        os.chmod(path,0o600)
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS owner(account TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS metadata(name TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS cursors(stream TEXT,room TEXT,last_id INTEGER NOT NULL,
                PRIMARY KEY(stream,room));
            CREATE TABLE IF NOT EXISTS inbox(room TEXT,server_id TEXT,payload TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(room,server_id));
            CREATE TABLE IF NOT EXISTS quarantine(stream TEXT,room TEXT,local_id INTEGER,reason TEXT,
                PRIMARY KEY(stream,room,local_id));
            CREATE TABLE IF NOT EXISTS audio_inbox(room TEXT,server_id TEXT,payload TEXT,state TEXT,
                PRIMARY KEY(room,server_id));
            CREATE TABLE IF NOT EXISTS audio_attempts(room TEXT,server_id TEXT,attempts INTEGER,next_retry REAL,
                PRIMARY KEY(room,server_id));
        ''')
        owner=self.db.execute('SELECT account FROM owner').fetchone()
        if owner and owner[0]!=account:
            self.db.close();raise ValueError('Inbox belongs to another account')
        with self.db:
            if not owner:self.db.execute('INSERT INTO owner VALUES(?)',(account,))

    def cursor(self,stream,room):
        row=self.db.execute('SELECT last_id FROM cursors WHERE stream=? AND room=?',(stream,room)).fetchone()
        return row[0] if row else None

    @property
    def bootstrap_complete(self):
        return self.db.execute("SELECT 1 FROM metadata WHERE name='bootstrap_complete'").fetchone() is not None

    def finish_bootstrap(self):
        with self.db:self.db.execute("INSERT OR IGNORE INTO metadata VALUES('bootstrap_complete','1')")

    def ingest(self,stream,room,rows):
        if room not in self.groups:raise ValueError('Group is not allowlisted')
        rows=list(rows)
        if len(rows)>10000:raise ValueError('Batch too large')
        for row in rows:
            if type(row.get('local_id')) is not int or row['local_id']<0:raise ValueError('Invalid local id')
        rows.sort(key=lambda row:row['local_id'])
        count=0
        with self.db:
            previous=self.cursor(stream,room)
            highest=max([row['local_id'] for row in rows]+[previous or 0])
            if previous is not None:
                for row in rows:
                    if row['local_id']<=previous:continue
                    if row.get('decode_error'):
                        self.db.execute('INSERT OR IGNORE INTO quarantine VALUES(?,?,?,?)',
                            (stream,room,row['local_id'],'invalid_text'))
                        continue
                    sender=row.get('sender_id')
                    server=str(row.get('server_id',''))
                    if (not sender or sender==self.account or not server.isascii() or
                            not server.isdecimal() or int(server)<=0):continue
                    if row.get('type')==34:
                        self.db.execute('INSERT OR IGNORE INTO audio_inbox VALUES(?,?,?,?)',
                            (room,server,json.dumps(dict(row,chat_id=room),ensure_ascii=False),
                             'pending' if room in self.voice_groups else 'disabled'))
                        continue
                    if row.get('type')!=1 or not isinstance(row.get('text'),str) or not row['text'].strip():continue
                    payload=dict(row,chat_id=room)
                    result=self.db.execute('INSERT OR IGNORE INTO inbox(room,server_id,payload) VALUES(?,?,?)',
                        (room,server,json.dumps(payload,ensure_ascii=False)))
                    count+=result.rowcount
            self.db.execute('INSERT INTO cursors VALUES(?,?,?) ON CONFLICT(stream,room) DO UPDATE SET last_id=excluded.last_id',
                (stream,room,highest))
        return count

    def pending(self,limit=100):
        rows=self.db.execute("SELECT payload FROM inbox WHERE state='pending' AND room IN ("+
            ','.join('?' for _ in self.groups)+') ORDER BY created_at,server_id LIMIT ?',(*sorted(self.groups),limit))
        return [json.loads(row[0]) for row in rows]

    def claim_audio(self,now=None):
        now=time.time() if now is None else now
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            rooms=sorted(self.voice_groups & self.groups)
            if not rooms:return None
            row=self.db.execute("SELECT a.room,a.server_id,a.payload FROM audio_inbox a LEFT JOIN audio_attempts f ON f.room=a.room AND f.server_id=a.server_id WHERE a.state='pending' AND (f.next_retry IS NULL OR f.next_retry<=?) AND a.room IN ("+
                ','.join('?' for _ in rooms)+') ORDER BY a.rowid LIMIT 1',[now,*rooms]).fetchone()
            if not row:return None
            self.db.execute("UPDATE audio_inbox SET state='processing' WHERE room=? AND server_id=?",row[:2])
            return json.loads(row[2])

    def fail_audio(self,message):
        room,ident=message['chat_id'],str(message['server_id'])
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            state=self.db.execute('SELECT state FROM audio_inbox WHERE room=? AND server_id=?',(room,ident)).fetchone()
            if state!=('processing',):return
            row=self.db.execute('SELECT attempts FROM audio_attempts WHERE room=? AND server_id=?',(room,ident)).fetchone()
            attempts=(row[0] if row else 0)+1
            self.db.execute('INSERT INTO audio_attempts VALUES(?,?,?,?) ON CONFLICT(room,server_id) DO UPDATE SET attempts=excluded.attempts,next_retry=excluded.next_retry',
                (room,ident,attempts,time.time()+min(60,5*2**min(attempts-1,4))))
            self.db.execute('UPDATE audio_inbox SET state=? WHERE room=? AND server_id=?',('pending' if attempts<3 else 'failed',room,ident))

    def finish_audio(self,message,text):
        room,ident=message['chat_id'],str(message['server_id'])
        if room not in self.groups or room not in self.voice_groups:raise ValueError('Voice group is disabled')
        if not isinstance(text,str) or not text.strip() or len(text)>4000:raise ValueError('Invalid transcript')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT state FROM audio_inbox WHERE room=? AND server_id=?',(room,ident)).fetchone()
            if row!=('processing',):raise ValueError('Voice not claimed')
            payload=dict(message,text=text.strip(),type=1,source_type=34)
            self.db.execute('INSERT OR IGNORE INTO inbox(room,server_id,payload) VALUES(?,?,?)',
                (room,ident,json.dumps(payload,ensure_ascii=False)))
            self.db.execute("UPDATE audio_inbox SET state='done' WHERE room=? AND server_id=?",(room,ident))

    def stats(self):
        return {'streams':self.db.execute('SELECT COUNT(*) FROM cursors').fetchone()[0],
                'pending':self.db.execute("SELECT COUNT(*) FROM inbox WHERE state='pending'").fetchone()[0],
                'quarantined':self.db.execute('SELECT COUNT(*) FROM quarantine').fetchone()[0]}

    def close(self):self.db.close()
