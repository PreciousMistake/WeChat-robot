"""Windows image queue: one worker, durable deduplication, no automatic retry."""
from contextlib import contextmanager
import json
from pathlib import Path
import queue
import re
import sqlite3
import threading
import time
import uuid

from linux_native.image_client import ImageClient, normalize_image_config
from linux_native.image_requests import IMAGE_POLICY, image_prompt
from .members import SnapshotExpired


def image_intent(text):
    return bool(isinstance(text,str) and (text.strip().startswith('/画图 ') or
        (not text.lstrip().startswith('/') and re.search(r'(?:画|绘制|生成|创作).{0,120}(?:图|画)',text))))


class ImageGeneration:
    def __init__(self, workspace, *, client_factory=ImageClient):
        self.workspace=Path(workspace)
        self.path=self.workspace/'image_config.json'
        self.runtime=self.workspace/'.runtime/generated_images'
        self.runtime.mkdir(parents=True,exist_ok=True)
        self.database=self.runtime.parent/'image_requests.sqlite3'
        self.factory=client_factory
        self.queue=queue.Queue(maxsize=16)
        self.lock=threading.RLock()
        self.stop_event=threading.Event()
        self.generation=0
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS requests(room TEXT,source TEXT,created REAL,state TEXT,PRIMARY KEY(room,source))')
            db.execute("UPDATE requests SET state='interrupted' WHERE state IN ('pending','generating')")

    @contextmanager
    def db(self):
        connection=sqlite3.connect(self.database)
        try:
            with connection:yield connection
        finally:connection.close()

    def read(self):
        raw=self.path.read_bytes() if self.path.exists() else b'{}'
        return raw,normalize_image_config(json.loads(raw.decode('utf-8-sig')))

    def submit(self, *, room, source, prompt, current, deliver):
        image_prompt({'decision':'direct','action':'image_generation','image_prompt':prompt})
        raw,options=self.read()
        if not options['enabled'] or not current() or self.stop_event.is_set():return False
        with self.lock, self.db() as db:
            now=time.time()
            if db.execute('SELECT 1 FROM requests WHERE room=? AND source=?',(room,source)).fetchone():return False
            count,last=db.execute('SELECT COUNT(*),MAX(created) FROM requests WHERE room=? AND created>?',(room,now-3600)).fetchone()
            if count>=options['hourly_limit'] or (last is not None and now-last<options['cooldown_seconds']) or self.queue.full():return False
            db.execute('INSERT INTO requests VALUES(?,?,?,?)',(room,source,now,'pending'))
            self.queue.put_nowait((room,source,prompt,raw,current,deliver,time.monotonic(),self.generation))
        return True

    def step(self):
        try:room,source,prompt,raw,authorized,deliver,created,generation=self.queue.get_nowait()
        except queue.Empty:return
        def current():
            try:
                return (generation==self.generation and not self.stop_event.is_set() and time.monotonic()-created<=600 and
                        authorized() and self.read()[0]==raw)
            except Exception:return False
        @contextmanager
        def guard():
            if not current():raise SnapshotExpired('生图任务已取消')
            yield
        state='cancelled';path=None
        try:
            if not current():return
            with self.db() as db:db.execute("UPDATE requests SET state='generating' WHERE room=? AND source=?",(room,source))
            image=self.factory(json.loads(raw.decode('utf-8-sig'))).generate(prompt,current=current)
            if not current():return
            path=self.runtime/(uuid.uuid4().hex+'.jpg')
            path.write_bytes(image)
            with guard():
                result=deliver(path,guard)
            state='sent' if result is not False else 'failed'
        except Exception:
            # Never retry a possibly charged request, or echo provider errors into chats.
            state='failed' if current() else 'cancelled'
        finally:
            if path:path.unlink(missing_ok=True)
            with self.db() as db:db.execute('UPDATE requests SET state=? WHERE room=? AND source=?',(state,room,source))
            self.queue.task_done()

    def invalidate(self):
        with self.lock:self.generation+=1

    def start(self):
        def run():
            while not self.stop_event.wait(.25):self.step()
        self.worker=threading.Thread(target=run,name='NaturalChat.images',daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        if hasattr(self,'worker'):self.worker.join(timeout=1)
