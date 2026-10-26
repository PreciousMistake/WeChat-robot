"""Observe recent row mutations and resolve already-downloaded native attachments."""
import hashlib
from pathlib import Path
import shutil
from .plugin_events import EventStore,parse_file_metadata,resolve_file


class PluginObservations:
    def __init__(self,state,account,account_root):
        self.state=Path(state);self.account=account;self.root=Path(account_root)
        self.events=EventStore(self.state,account)
        with self.events.db:self.events.db.execute('CREATE TABLE IF NOT EXISTS receiver_baselines(stream TEXT,room TEXT,PRIMARY KEY(stream,room))')

    def observe(self,db,stream,room):
        from .worker import read_rows,table_name
        table=table_name(room)
        if not db.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():return
        maximum=db.execute('SELECT MAX(local_id) FROM '+table).fetchone()[0] or 0
        rows=read_rows(db,room,max(0,maximum-128),limit=128,include_metadata=True) or []
        rows=[row for row in rows if row.get('type') in (10000,10002) or (row.get('sender_id') and row['sender_id']!=self.account)]
        for row in rows:
            if row['type']==49:
                metadata=parse_file_metadata(row.get('raw_content',''))
                if metadata:
                    metadata['create_time']=row.get('create_time');path=resolve_file(self.root,metadata)
                    row.update(app_type=6,file_name=metadata['file_name'],md5=metadata['md5'],file_size=metadata['size'],file_status='pending')
                    if path is not None:
                        cache=self.state/'.runtime/plugin_receive';cache.mkdir(parents=True,exist_ok=True,mode=0o700)
                        target=cache/(metadata['md5']+metadata['extension'])
                        if not target.exists():
                            temporary=target.with_suffix(target.suffix+'.tmp')
                            shutil.copyfile(path,temporary)
                            if temporary.stat().st_size!=metadata['size'] or hashlib.md5(temporary.read_bytes()).hexdigest()!=metadata['md5']:
                                temporary.unlink(missing_ok=True);continue
                            temporary.chmod(0o600);temporary.replace(target)
                        row.update(file_status='ready',file_path=str(target))
        baseline=self.events.db.execute('SELECT 1 FROM receiver_baselines WHERE stream=? AND room=?',(stream,room)).fetchone() is None
        self.events.observe(stream,room,rows,baseline=baseline)
        with self.events.db:self.events.db.execute('INSERT OR IGNORE INTO receiver_baselines VALUES(?,?)',(stream,room))

    def close(self):self.events.close()
