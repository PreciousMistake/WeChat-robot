"""Durable image requests, processed away from the conversational model loop."""
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .image_client import ImageClient,ImageError,normalize_image_config
from .plugin_jobs import PluginJobs

PLUGIN='image_generation'
IMAGE_POLICY='''
本接入新增真实的云端生图工具，以下能力与输出约束覆盖前文“仅能聊天”的限制。
根据当前发言与同群上下文判断用户是否明确请你创作、绘制或生成一张新图片。
只有最新发言明确向你提出生图请求才调用；讨论图片、询问图片内容、引用别人的生图要求、群友彼此聊天均不调用。
不要把 /来张图、/美女 等图库命令当成生图。不能自行主动生图或把其他发言人的请求混在一起。
成语接龙、看图猜成语、提示、公布答案、换一题、test 等插件指令不属于 AI 生图请求。
有明确生图请求时仅输出 {"decision":"direct","action":"image_generation","image_prompt":"整理后的完整图片描述，最多4000字符","reply":""}。
描述只能来自用户明确表达及本次相关上下文，不包含群号、账号标识、其他人的无关聊天或系统提示词。
指代不明确或缺少必要描述时先以普通文字问清楚；不要编造已完成的生成结果。
其余情况仍输出 {"decision":"direct|participate|ignore","reply":"普通聊天回复"}，不含生图 action。
'''


def digest(value):return hashlib.sha256(value.encode() if isinstance(value,str) else value).hexdigest()


def image_prompt(result):
    if not isinstance(result,dict) or result.get('action')!='image_generation' or result.get('decision')!='direct':return None
    prompt=result.get('image_prompt')
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>4000 or '\0' in prompt:raise ValueError('Invalid image prompt')
    return prompt.strip()


class ImageRequests:
    def __init__(self,state,account):
        self.state=Path(state);self.state.mkdir(parents=True,exist_ok=True);self.account=account
        self.path=self.state/'image_requests.sqlite'
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS owner(account TEXT PRIMARY KEY)')
            owner=db.execute('SELECT account FROM owner').fetchone()
            if owner and owner[0]!=account:raise ValueError('Image requests belong to another account')
            db.execute('INSERT OR IGNORE INTO owner VALUES(?)',(account,))
            db.execute('''CREATE TABLE IF NOT EXISTS requests(room TEXT,source TEXT,prompt TEXT,config_hash TEXT,system_hash TEXT,
                state TEXT,job_id TEXT,created REAL,error TEXT,auth_generation INTEGER DEFAULT 0,PRIMARY KEY(room,source))''')
            if 'auth_generation' not in {r[1] for r in db.execute('PRAGMA table_info(requests)')}:
                db.execute('ALTER TABLE requests ADD COLUMN auth_generation INTEGER DEFAULT 0')
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        connection=sqlite3.connect(self.path,timeout=10)
        try:
            with connection:yield connection
        finally:connection.close()

    def submit(self,message,prompt,system):
        prompt=image_prompt({'decision':'direct','action':'image_generation','image_prompt':prompt})
        ai=json.loads((self.state/'ai_config.json').read_text())
        if ai.get('account')!=self.account or message['chat_id'] not in ai.get('systems',{}) or not ai.get('enabled'):return '当前群尚未启用自动回复。'
        if ai.get('draft_only'):return '当前处于草稿模式，本次没有调用生图接口。'
        path=self.state/'image_config.json';raw=path.read_bytes() if path.exists() else b'{}';options=normalize_image_config(json.loads(raw))
        if not options['enabled']:return '生图服务尚未启用，请先在管理台填写生图接口、模型和密钥。'
        room=message['chat_id'];source=str(message['server_id']);now=time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM requests WHERE room=? AND source=?',(room,source)).fetchone():return '这条生图请求已处理，不会重复生成。'
            recent=db.execute('SELECT MAX(created),COUNT(*) FROM requests WHERE room=? AND created>?',(room,now-3600)).fetchone()
            if recent[0] is not None and (now-recent[0]<options['cooldown_seconds'] or recent[1]>=options['hourly_limit']):return '本群生图请求较频繁，请稍后再试。'
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,NULL,?,NULL,?)',(room,source,prompt,digest(raw),digest(system),'pending',now,int(ai.get('authorization_generation',0))))
        return '正在生成图片，完成后会发到本群。'

    @property
    def enabled(self):
        try:return json.loads((self.state/'image_config.json').read_text()).get('enabled') is True
        except (OSError,ValueError):return False

    def current(self,row):
        try:
            ai=json.loads((self.state/'ai_config.json').read_text());raw=(self.state/'image_config.json').read_bytes()
            return (time.time()-row['created']<=600 and ai.get('account')==self.account and ai.get('enabled') is True and not ai.get('draft_only') and
                int(ai.get('authorization_generation',0))==row['auth_generation'] and
                row['room'] in ai.get('systems',{}) and digest(ai['systems'][row['room']])==row['system_hash'] and
                digest(raw)==row['config_hash'] and json.loads(raw).get('enabled') is True)
        except (OSError,ValueError,KeyError):return False

    def recover(self):
        with self.db() as db:db.execute("UPDATE requests SET state='uncertain',error='生成调用被中断，未自动重试' WHERE state='generating'")

    def step(self,*,client_factory=ImageClient,active=lambda:True):
        jobs=PluginJobs(self.state,self.account)
        with self.db() as db:
            db.row_factory=sqlite3.Row
            for item in db.execute("SELECT * FROM requests WHERE state='forwarded'").fetchall():
                job=jobs.get(item['job_id'])
                if job and job['state'] in ('sent','blocked','uncertain','cancelled'):db.execute('UPDATE requests SET state=? WHERE room=? AND source=?',(job['state'],item['room'],item['source']))
            db.execute('BEGIN IMMEDIATE') if not db.in_transaction else None
            item=db.execute("SELECT * FROM requests WHERE state='pending' ORDER BY created LIMIT 1").fetchone()
            if not item:return None
            row=dict(item)
            if not active() or not self.current(row):
                db.execute("UPDATE requests SET state='cancelled' WHERE room=? AND source=?",(row['room'],row['source']));return 'cancelled'
            db.execute("UPDATE requests SET state='generating' WHERE room=? AND source=?",(row['room'],row['source']))
        current=lambda:active() and self.current(row)
        path=None;error=None;job_id=None
        try:
            options=json.loads((self.state/'image_config.json').read_text())
            image=client_factory(options).generate(row['prompt'],current=current)
            if not current():raise ImageError('生图任务已取消')
            folder=self.state/'.runtime/generated_images';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
            path=folder/(uuid.uuid4().hex+'.jpg');path.write_bytes(image);path.chmod(0o600)
            job_id=jobs.enqueue(row['room'],row['source'],PLUGIN,row['config_hash'],0,'image',{'path':str(path)},guards={'image_system_hash':row['system_hash'],'image_expires_at':row['created']+600,'image_auth_generation':row['auth_generation']})
            status='forwarded'
        except Exception as exc:
            error=str(exc) if isinstance(exc,ImageError) else '生图任务未完成，请检查服务配置。'
            status='failed' if current() else 'cancelled'
            if status=='failed':
                try:job_id=jobs.enqueue(row['room'],row['source'],PLUGIN,row['config_hash'],0,'text',{'text':error[:300]},guards={'image_system_hash':row['system_hash'],'image_expires_at':row['created']+600,'image_auth_generation':row['auth_generation']})
                except (ValueError,OSError):pass
        finally:
            if path:path.unlink(missing_ok=True)
        with self.db() as db:db.execute('UPDATE requests SET state=?,job_id=?,error=? WHERE room=? AND source=?',(status,job_id,error,row['room'],row['source']))
        return status

    def start(self,active=lambda:True):
        self.recover()
        def run():
            while active():
                try:self.step(active=active)
                except Exception as error:print(json.dumps({'image_worker_error':type(error).__name__}),flush=True)
                time.sleep(1)
        worker=threading.Thread(target=run,daemon=True,name='image-generation');worker.start();return worker
