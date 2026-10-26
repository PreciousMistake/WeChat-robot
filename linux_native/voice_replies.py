"""Durable voice parts: synthesis never blocks the receiving thread."""
import io
import json
import time
import wave


def reply_mode(mode,message):
    return ('voice' if message.get('source_type',message.get('type'))==34 else 'text') if mode=='auto' else mode


class VoiceReplies:
    def __init__(self,queue):
        self.queue=queue;self.db=queue.db
        self.db.execute('''CREATE TABLE IF NOT EXISTS voice_replies(room TEXT,source_id TEXT,mode TEXT,state TEXT,
          wav BLOB,duration REAL,after_local_id INTEGER,outgoing_id TEXT,attempts INTEGER DEFAULT 0,
          PRIMARY KEY(room,source_id))''');self.db.commit()

    def get(self,room,ident):
        row=self.db.execute('SELECT mode,state,wav,duration,after_local_id,outgoing_id FROM voice_replies WHERE room=? AND source_id=?',(room,str(ident))).fetchone()
        return dict(zip(('mode','state','wav','duration','after_local_id','outgoing_id'),row)) if row else None

    def request(self,room,ident,mode):
        if mode not in ('voice','both') or room not in self.queue.box.groups:raise ValueError('Invalid voice mode')
        self.db.execute("INSERT INTO voice_replies(room,source_id,mode,state) VALUES(?,?,?,'pending')",(room,str(ident),mode))
        self.db.commit()

    def ready(self,room,ident,wav):
        from wxbot_framework.tts import normalize_wav
        wav=normalize_wav(wav)
        with wave.open(io.BytesIO(wav),'rb') as f:duration=f.getnframes()/f.getframerate()
        if not 0<duration<=50:raise ValueError('Synthesized voice exceeds 50 seconds')
        with self.db:
            changed=self.db.execute("UPDATE voice_replies SET state='ready',wav=?,duration=? WHERE room=? AND source_id=? AND state='pending'",(wav,duration,room,str(ident))).rowcount
            if changed!=1:raise ValueError('Voice is no longer pending')

    def begin(self,room,ident,after):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            part=self.get(room,ident)
            if not part or part['state']!='ready' or room not in self.queue.box.groups or type(after) is not int or after<0:raise ValueError('Voice is not ready')
            parent=self.queue.get(room,ident)
            if parent['state']!=('draft' if part['mode']=='voice' else 'sent'):raise ValueError('Voice parent is not ready')
            self.db.execute("UPDATE voice_replies SET state='sending',after_local_id=? WHERE room=? AND source_id=?",(after,room,str(ident)))
            if part['mode']=='voice':self.db.execute("UPDATE replies SET state='sending',after_local_id=? WHERE room=? AND source_id=?",(after,room,str(ident)))

    def confirm(self,room,ident,proof):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE');part=self.get(room,ident)
            server=str(proof.get('server_id',''));duration=proof.get('voice_duration_ms',0)
            if (not part or part['state'] not in ('sending','uncertain') or proof.get('chat_id')!=room or proof.get('is_self') is not True or
                proof.get('type')!=34 or type(proof.get('local_id')) is not int or proof['local_id']<=part['after_local_id'] or
                not server.isascii() or not server.isdecimal() or int(server)<=0 or type(duration) is not int or
                not part['duration']*1000-300<=duration<=part['duration']*1000+4000):raise ValueError('Voice proof does not match')
            if self.db.execute('SELECT 1 FROM voice_replies WHERE room=? AND outgoing_id=?',(room,server)).fetchone():raise ValueError('Voice proof already used')
            self.db.execute("UPDATE voice_replies SET state='sent',outgoing_id=?,wav=NULL WHERE room=? AND source_id=?",(server,room,str(ident)))
            if part['mode']=='voice':
                self.db.execute("UPDATE replies SET state='sent',outgoing_id=? WHERE room=? AND source_id=?",(server,room,str(ident)))
                self.db.execute("UPDATE inbox SET state='done' WHERE room=? AND server_id=?",(room,str(ident)))
                kind=self.queue.get(room,ident)['decision']
                self.db.execute('INSERT INTO delivery_events VALUES(?,?,?,?)',(room,str(ident),kind,time.time()))

    def uncertain(self,room,ident):
        with self.db:
            self.db.execute("UPDATE voice_replies SET state='uncertain' WHERE room=? AND source_id=? AND state='sending'",(room,str(ident)))
            self.db.execute("UPDATE replies SET state='uncertain' WHERE room=? AND source_id=? AND state='sending'",(room,str(ident)))

    def reconcile(self,settings):
        rows=self.db.execute("SELECT v.room,v.source_id,i.payload FROM voice_replies v JOIN inbox i ON i.room=v.room AND i.server_id=v.source_id WHERE v.state IN ('pending','ready')").fetchall()
        with self.db:
            for room,ident,payload in rows:
                if room not in settings:
                    self.db.execute("UPDATE voice_replies SET state='archived',wav=NULL WHERE room=? AND source_id=?",(room,ident))
                elif reply_mode(settings[room].get('reply_mode','text'),json.loads(payload))=='text':
                    self.db.execute("UPDATE voice_replies SET state='cancelled',mode='text',wav=NULL WHERE room=? AND source_id=?",(room,ident))

    def synthesize_one(self,state,active):
        row=self.db.execute("SELECT v.room,v.source_id,r.reply FROM voice_replies v JOIN replies r ON r.room=v.room AND r.source_id=v.source_id WHERE v.state='pending' AND r.state IN ('draft','sent') AND v.attempts<3 ORDER BY r.rowid LIMIT 1").fetchone()
        if not row:return None
        room,ident,text=row
        if room not in self.queue.box.groups:return None
        try:
            from wxbot_framework.cloud_audio import tts_from_options
            options=json.loads((state/'tts_config.json').read_text())
            if options.get('enabled',bool(options.get('api_key'))) is not True:return {'tts':'paused','source_id':ident}
            if not active():return None
            client=tts_from_options(options);wav=client.synthesize(text,client.default_voice)
            if not active():return None
            self.ready(room,ident,wav)
            return {'tts':'ready','source_id':ident}
        except Exception as error:
            with self.db:
                self.db.execute('UPDATE voice_replies SET attempts=attempts+1 WHERE room=? AND source_id=?',(room,ident))
                self.db.execute("UPDATE voice_replies SET state='failed' WHERE room=? AND source_id=? AND attempts>=3",(room,ident))
                if self.get(room,ident)['state']=='failed':
                    self.db.execute("UPDATE replies SET state='blocked' WHERE room=? AND source_id=? AND state='draft' AND EXISTS(SELECT 1 FROM voice_replies WHERE room=? AND source_id=? AND mode='voice')",(room,ident,room,ident))
            return {'tts':'failed','source_id':ident,'error_type':type(error).__name__}
