"""Narrow experimental Linux client text driver; exact bot group checks."""
import io
import re
import subprocess
import time
import uuid
import unicodedata
import csv


def compact_name(value):return re.sub(r'\s+','',unicodedata.normalize('NFKC',value)).casefold()


def wait_clipboard(read,expected,*,timeout=1.5,interval=.05):
    until=time.monotonic()+timeout
    while True:
        value=read()
        if value==expected:return value
        if time.monotonic()>=until:raise ValueError('Input text does not match draft')
        time.sleep(interval)


def chat_regions(width,height):
    if not (800<=width<=1400 and 550<=height<=1000):raise ValueError('Unsupported client geometry')
    return {'title':(300,40,min(width-130,630),74),'search':(160,55),'input':(300+(width-300)/2,height-95)}


class CaptionAmbiguous(ValueError):pass


def first_group_result_position(tsv,*,min_left=0):
    """Navigation only: the caller must still verify the destination title."""
    lines={}
    for row in csv.DictReader(io.StringIO(tsv),delimiter='\t'):
        if row.get('level')!='5' or not row.get('text','').strip():continue
        key=tuple(row[k] for k in ('page_num','block_num','par_num','line_num'))
        lines.setdefault(key,[]).append(row)
    end=None
    for words in sorted(lines.values(),key=lambda rows:min(int(w['top']) for w in rows)):
        label=compact_name(''.join(w['text'] for w in words if float(w['conf'])>=30))
        top=min(int(w['top']) for w in words)
        bottom=max(int(w['top'])+int(w['height']) for w in words)
        if label in ('groupchats','groups','群聊'):end=bottom;continue
        if end is None:continue
        if label in ('contacts','chathistory','articles','functions','recentsearches','联系人','聊天记录','功能','最近搜索'):return None
        candidates=[w for w in words if int(w['left'])>=min_left]
        if not candidates:continue
        top=min(int(w['top']) for w in candidates)
        bottom=max(int(w['top'])+int(w['height']) for w in candidates)
        if top>end+210:return None
        if top>=end:return (min_left+75,(top+bottom)/2)
    return None


def caption_position(tsv,name,*,group_only=False,min_left=0):
    lines={}
    for row in csv.DictReader(io.StringIO(tsv),delimiter='\t'):
        if row.get('level')!='5' or not row.get('text','').strip():continue
        if float(row['conf'])<30:continue
        key=tuple(row[k] for k in ('page_num','block_num','par_num','line_num'))
        lines.setdefault(key,[]).append(row)
    matches=[];group_section=False
    for words in sorted(lines.values(),key=lambda rows:min(int(w['top']) for w in rows)):
        words=list(words)
        while words and re.fullmatch(r'(?:[0-2]?[0-9]:[0-5][0-9]|Yesterday|Today|昨天|今天)',words[-1]['text'],re.I):words.pop()
        if not words:continue
        label=compact_name(''.join(w['text'] for w in words))
        if label in ('groupchats','groups','群聊'):
            group_section=True;continue
        if label in ('contacts','chathistory','articles','functions','recentsearches','联系人','聊天记录','功能','最近搜索'):
            group_section=False;continue
        if group_only and not group_section:continue
        words=[word for word in words if int(word['left'])>=min_left]
        if not words:continue
        if compact_name(''.join(w['text'] for w in words))!=compact_name(name):continue
        left=min(int(w['left']) for w in words);top=min(int(w['top']) for w in words)
        right=max(int(w['left'])+int(w['width']) for w in words);bottom=max(int(w['top'])+int(w['height']) for w in words)
        matches.append(((left+right)/2,(top+bottom)/2))
    if len(matches)>1:raise CaptionAmbiguous('Search result caption is ambiguous')
    return matches[0] if matches else None


def resolve_group(db,room):
    row=db.execute('SELECT nick_name,remark,is_in_chat_room FROM contact WHERE username=?',(room,)).fetchone()
    if not row or row[2]!=1 or row[1]:raise ValueError('Group is not joined or has an unverified remark')
    name=row[0]
    if not isinstance(name,str) or not name.strip() or len(name)>80 or any(ord(c)<32 for c in name):raise ValueError('Unsupported group name')
    count=db.execute('SELECT COUNT(*) FROM contact WHERE (nick_name=? OR remark=?) AND username LIKE ?', (name,name,'%@chatroom')).fetchone()[0]
    if count!=1:raise ValueError('Group name is ambiguous')
    for username,nickname,remark in db.execute('SELECT username,nick_name,remark FROM contact'):
        if username==room:continue
        for display in (nickname,remark):
            compact=compact_name(display or '')
            if compact==compact_name(name) or re.fullmatch(re.escape(compact_name(name))+r'\([1-9][0-9]*\)',compact):
                raise ValueError('A contact can mimic the target group')
    return name


def verify_title(text,name):
    compact=compact_name(text)
    if not re.fullmatch(re.escape(compact_name(name))+r'\([1-9][0-9]*\)',compact):
        raise ValueError('Active chat title does not prove the target group')


def verify_empty_input(value,sentinel):
    # Qt can copy one invisible whitespace character from an otherwise empty editor.
    if value!=sentinel and value.strip():raise ValueError('Client contains an unsent draft')


class ClientDriver:
    def __init__(self,pid):
        self.active=lambda:True
        self.pid=pid
        ids=self.command('search','--all','--onlyvisible','--pid',str(pid),'--class','^wechat$').split()
        candidates=[]
        for window in dict.fromkeys(ids):
            try:
                if self.command('getwindowpid',window).strip()!=str(pid):continue
                geometry=dict(line.split('=',1) for line in self.command('getwindowgeometry','--shell',window).splitlines() if '=' in line)
                if 800<=int(geometry['WIDTH'])<=1400 and 550<=int(geometry['HEIGHT'])<=1000:candidates.append((window,geometry))
            except subprocess.CalledProcessError:continue
        if len(candidates)!=1:raise ValueError('Expected one visible main client window')
        self.window,values=candidates[0]
        self.x,self.y,self.width,self.height=(int(values[k]) for k in ('X','Y','WIDTH','HEIGHT'))
        if not (800<=self.width<=1400 and 550<=self.height<=1000):raise ValueError('Unsupported client geometry')

    def command(self,*args):
        return subprocess.run(['xdotool',*args],capture_output=True,text=True,check=True,timeout=5).stdout

    def assert_focus(self):
        if not self.active():raise ValueError('Delivery cancelled')
        if self.command('getactivewindow').strip()!=self.window:raise ValueError('Client window lost focus')
        if self.command('getwindowpid',self.window).strip()!=str(self.pid):raise ValueError('Client process changed')
        values=dict(line.split('=',1) for line in self.command('getwindowgeometry','--shell',self.window).splitlines() if '=' in line)
        if tuple(int(values[k]) for k in ('X','Y','WIDTH','HEIGHT'))!=(self.x,self.y,self.width,self.height):
            raise ValueError('Client geometry changed')

    def key(self,*keys):
        self.assert_focus()
        self.command('key','--window',self.window,'--clearmodifiers',*keys)

    def assert_no_modal(self):
        windows=self.command('search','--all','--onlyvisible','--pid',str(self.pid)).split()
        for window in dict.fromkeys(windows):
            if window==self.window:continue
            properties=subprocess.run(['xprop','-id',window,'_NET_WM_STATE'],capture_output=True,text=True,check=True,timeout=5).stdout
            if '_NET_WM_STATE_MODAL' in properties:raise ValueError('Client contains a modal dialog')

    def click(self,x,y):
        self.assert_focus()
        self.command('mousemove',str(self.x+round(self.width*x)),str(self.y+round(self.height*y)),'click','1')

    def clipboard(self,text):
        subprocess.run(['xclip','-selection','clipboard','-target','UTF8_STRING','-in'],input=text.encode(),
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=5)

    def title(self):
        from PIL import ImageGrab,ImageOps
        left,top,right,bottom=chat_regions(self.width,self.height)['title']
        image=ImageGrab.grab(xdisplay=':99').crop((self.x+left,self.y+top,self.x+right,self.y+bottom))
        image=ImageOps.grayscale(image).resize((image.width*3,image.height*3))
        data=io.BytesIO();image.save(data,format='PNG')
        language='chi_sim+eng' if any(ord(c)>127 for c in getattr(self,'expected_name','')) else 'eng'
        return subprocess.run(['tesseract','stdin','stdout','-l',language,'--psm','7'],input=data.getvalue(),
            capture_output=True,check=True,timeout=5).stdout.decode().strip()

    def select(self,name):
        self.expected_name=name
        # Only operate the already focused, identity-checked client. Activation
        # is not proof that a same-process modal has been dismissed.
        self.assert_focus()
        self.assert_no_modal()
        try:
            verify_title(self.title(),name);return
        except ValueError:pass
        try:chosen=self._choose_caption(name,(100,76,295,self.height-30))
        except CaptionAmbiguous:chosen=False
        if chosen:
            time.sleep(.4);verify_title(self.title(),name);return
        point=chat_regions(self.width,self.height)['search']
        self.click(point[0]/self.width,point[1]/self.height);self.key('ctrl+a')
        self.clipboard(name);self.key('ctrl+v')
        self.clipboard(uuid.uuid4().hex);self.key('ctrl+a','ctrl+c')
        wait_clipboard(self.read_clipboard,name)
        self.key('End')
        time.sleep(1)
        until=time.monotonic()+5
        while not self._choose_caption(name,(60,76,295,self.height-20),group_only=True):
            if time.monotonic()>=until:raise ValueError('Exact group search result was not found')
            time.sleep(.15)
        time.sleep(.7)
        verify_title(self.title(),name)

    def _choose_caption(self,name,rectangle,*,group_only=False):
        from PIL import ImageGrab,ImageOps
        left,top,right,bottom=rectangle
        image=ImageGrab.grab(xdisplay=':99').crop((self.x+left,self.y+top,self.x+right,self.y+bottom))
        image=ImageOps.grayscale(image).resize((image.width*3,image.height*3))
        data=io.BytesIO();image.save(data,format='PNG')
        language='chi_sim+eng' if any(ord(c)>127 for c in name) else 'eng'
        result=subprocess.run(['tesseract','stdin','stdout','-l',language,'--psm','6','tsv'],input=data.getvalue(),capture_output=True,check=True,timeout=5)
        min_left=max(0,(115-left)*3)
        point=caption_position(result.stdout.decode(),name,group_only=group_only,min_left=min_left)
        if point is None and group_only:
            point=first_group_result_position(result.stdout.decode(),min_left=min_left)
        if point is None:return False
        self.click((left+point[0]/3)/self.width,(top+point[1]/3)/self.height)
        return True

    def prepare(self,name,text):
        if not text.strip() or len(text)>getattr(self,'max_text_chars',400) or any(ord(c)<32 and c!='\n' for c in text):raise ValueError('Invalid outgoing text')
        self.select(name)
        self.click(chat_regions(self.width,self.height)['input'][0]/self.width,chat_regions(self.width,self.height)['input'][1]/self.height)
        self.ensure_empty_input()
        self.clipboard(text);self.key('ctrl+v')
        # A failed Ctrl+C must not pass by reading our original paste payload.
        self.clipboard(uuid.uuid4().hex)
        self.key('ctrl+a','ctrl+c')
        wait_clipboard(self.read_clipboard,text)
        self.key('End');verify_title(self.title(),name)
        self.prepared_name=name

    def commit(self):
        self.assert_focus();self.assert_no_modal();verify_title(self.title(),self.prepared_name)
        self.key('Return')


    def read_clipboard(self):
        self.assert_focus()
        return subprocess.run(['xclip','-selection','clipboard','-target','UTF8_STRING','-out'],capture_output=True,check=True,timeout=5).stdout.decode()

    def ensure_empty_input(self):
        sentinel=uuid.uuid4().hex;self.clipboard(sentinel);self.key('ctrl+a','ctrl+c')
        time.sleep(.25)
        existing=subprocess.run(['xclip','-selection','clipboard','-target','UTF8_STRING','-out'],capture_output=True,check=True,timeout=5).stdout
        verify_empty_input(existing.decode(),sentinel)


    def prepare_voice(self,name,wav,duration):
        import json,sys,tempfile
        from pathlib import Path
        from .pulse import VoiceSource
        if not 0<duration<=50:raise ValueError('Voice duration exceeds limit')
        self.voice_source=None;self.voice_guard=None;self.voice_file=None;self.voice_recording=False
        self.select(name);self.click(chat_regions(self.width,self.height)['input'][0]/self.width,chat_regions(self.width,self.height)['input'][1]/self.height);self.ensure_empty_input()
        self.prepared_name=name
        try:
            self.voice_source=VoiceSource();source=self.voice_source;source.__enter__()
            self.voice_guard=subprocess.Popen([sys.executable,'-m','linux_native.voice_guardian'],stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=source.environment,text=True)
            self.voice_guard.stdin.write(json.dumps(dict(window=self.window,pid=self.pid,previous=source.previous,modules=source.modules))+'\n');self.voice_guard.stdin.flush()
            fd,filename=tempfile.mkstemp(suffix='.wav',dir='/tmp/runtime-wechat')
            import os
            with os.fdopen(fd,'wb') as out:out.write(wav)
            self.voice_file=Path(filename)
            verify_title(self.title(),name)
            self.click((self.width-98)/self.width,(self.height-28)/self.height)
            self.voice_recording=True
            source.route(active=self.active)
            source.play(self.voice_file,duration,active=self.active)
            verify_title(self.title(),name)
        except Exception:
            self.cleanup_voice();raise

    def commit_voice(self):
        self.assert_focus();verify_title(self.title(),self.prepared_name)
        if not self.voice_recording or len(self.voice_source.recordings())!=1:raise ValueError('Native recording is not ready')
        self.click((self.width-35)/self.width,(self.height-28)/self.height)
        time.sleep(.2)
        if self.voice_source.recordings():raise ValueError('Native recording did not finish')
        self.voice_recording=False

    def cleanup_voice(self):
        # Escape only the same process/window; never press Return during cleanup.
        if getattr(self,'voice_recording',False):
            try:
                if self.command('getwindowpid',self.window).strip()==str(self.pid):self.command('key','--window',self.window,'Escape')
            finally:self.voice_recording=False
        guard=getattr(self,'voice_guard',None)
        if guard:
            try:guard.stdin.write('done\n');guard.stdin.flush();guard.stdin.close();guard.wait(timeout=5)
            except (BrokenPipeError,subprocess.TimeoutExpired):pass
            self.voice_guard=None
        source=getattr(self,'voice_source',None)
        if source:
            self.voice_source=None;source.__exit__(None,None,None)
        file=getattr(self,'voice_file',None)
        if file:file.unlink(missing_ok=True);self.voice_file=None
