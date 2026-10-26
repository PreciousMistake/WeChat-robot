"""Native attachment staging, checked clipboard and one explicit send commit."""
import io
from pathlib import Path
import subprocess
import time
import uuid
from urllib.parse import urlsplit
from urllib.request import url2pathname
from .sender import ClientDriver,verify_title,wait_clipboard,chat_regions


def verify_staged_media(kind,path,formats):
    path=Path(path)
    if kind=='image':
        from PIL import Image
        raw=formats.get('image/png',b'')
        if not raw or len(raw)>20*1024*1024:raise ValueError('No bounded staged image')
        with Image.open(path) as expected,Image.open(io.BytesIO(raw)) as staged:
            if expected.width*expected.height>40_000_000 or staged.size!=expected.size or staged.convert('RGBA').tobytes()!=expected.convert('RGBA').tobytes():raise ValueError('Staged image differs from artifact')
        plain=formats.get('text/plain',b'').decode('utf-8',errors='strict').strip()
        if plain not in ('','\ufffc','[图片]','[Image]',str(path),path.name,path.as_uri()):raise ValueError('Staged image includes unexpected text')
        return True
    lines=[line.strip() for line in formats.get('text/uri-list',b'').decode('utf-8').splitlines() if line.strip() and not line.startswith('#')]
    if len(lines)!=1:raise ValueError('Expected one staged local file')
    parsed=urlsplit(lines[0])
    if parsed.scheme!='file' or parsed.netloc not in ('','localhost') or Path(url2pathname(parsed.path)).resolve()!=path.resolve():raise ValueError('Staged file differs from artifact')
    return True


class MediaDriver(ClientDriver):
    def dialog(self):
        self.assert_active_configuration()
        window=self.command('getactivewindow').strip()
        if self.command('getwindowpid',window).strip()!=str(self.pid) or self.command('getwindowname',window).strip() not in ('Select File','选择文件'):raise ValueError('Unexpected native file dialog')
        return window

    def assert_active_configuration(self):
        if not self.active():raise ValueError('Delivery cancelled')
        if self.command('getwindowpid',self.window).strip()!=str(self.pid):raise ValueError('Client process changed')

    def dialog_key(self,*keys):
        window=self.dialog();self.command('key','--window',window,'--clearmodifiers',*keys)

    def dialog_click(self,x,y):
        window=self.dialog()
        self.command('mousemove','--window',window,str(round(x)),str(round(y)),'click','1')

    def clip(self,target):
        self.assert_active_configuration()
        response=subprocess.run(['xclip','-selection','clipboard','-target',target,'-out'],capture_output=True,check=True,timeout=5)
        if len(response.stdout)>20*1024*1024:raise ValueError('Clipboard artifact exceeds limit')
        return response.stdout

    def prepare_media(self,name,path,kind):
        path=Path(path)
        if kind not in ('image','file') or not path.is_absolute() or not path.is_file() or path.stat().st_size>50*1024*1024:raise ValueError('Invalid media artifact')
        self.select(name)
        point=chat_regions(self.width,self.height)['input'];self.click(point[0]/self.width,point[1]/self.height);self.ensure_empty_input()
        self.prepared_name=name;self.media_path=path;self.media_kind=kind
        self.click(400/self.width,(self.height-28)/self.height)
        until=time.monotonic()+3
        while True:
            try:window=self.dialog();break
            except ValueError:
                if time.monotonic()>=until:raise
                time.sleep(.1)
        values=dict(line.split('=',1) for line in self.command('getwindowgeometry','--shell',window).splitlines() if '=' in line)
        x,y,w,h=(int(values[k]) for k in ('X','Y','WIDTH','HEIGHT'))
        if not 500<=w<=900 or not 350<=h<=650:raise ValueError('Unsupported file dialog geometry')
        self.dialog_click(200,h-58)
        self.clipboard(str(path));self.dialog_key('ctrl+a','ctrl+v')
        self.clipboard(uuid.uuid4().hex);self.dialog_key('ctrl+a','ctrl+c')
        wait_clipboard(lambda:self.clip('UTF8_STRING').decode(),str(path))
        self.dialog_key('End')
        # Leave the filename completer before clicking Open; otherwise its popup
        # can consume the first Return/click without opening the selected file.
        self.dialog_key('Tab')

    def commit_media(self):
        # The job is already marked sending before opening the selected file.
        window=self.dialog()
        values=dict(line.split('=',1) for line in self.command('getwindowgeometry','--shell',window).splitlines() if '=' in line)
        w,h=(int(values[k]) for k in ('WIDTH','HEIGHT'))
        if not 500<=w<=900 or not 350<=h<=650:raise ValueError('Unsupported file dialog geometry')
        self.dialog_click(w-50,h-58)
        until=time.monotonic()+3
        while self.command('getactivewindow').strip()!=self.window:
            self.assert_active_configuration()
            if time.monotonic()>=until:raise ValueError('File dialog did not return to target chat')
            time.sleep(.1)
        self.assert_focus();verify_title(self.title(),self.prepared_name)
        point=chat_regions(self.width,self.height)['input'];self.click(point[0]/self.width,point[1]/self.height)
        self.clipboard('media-verify-sentinel');self.key('ctrl+a','ctrl+c');time.sleep(.25)
        targets=self.clip('TARGETS').decode().splitlines()
        formats={name:self.clip(name) for name in ('image/png','text/plain','text/uri-list') if name in targets}
        verify_staged_media(self.media_kind,self.media_path,formats)
        self.key('End');self.commit()

    def commit(self):
        # Selected inline images can consume Return without sending. The native
        # Send button commits the already verified attachment regardless of that
        # editor selection; outgoing database proof is still required.
        self.assert_focus();self.assert_no_modal();verify_title(self.title(),self.prepared_name)
        self.click((self.width-49)/self.width,(self.height-28)/self.height)

    def cancel_media(self):
        # Only close our verified chooser. Leave unknown drafts for inspection.
        try:
            window=self.dialog()
            values=dict(line.split('=',1) for line in self.command('getwindowgeometry','--shell',window).splitlines() if '=' in line)
            w,h=(int(values[k]) for k in ('WIDTH','HEIGHT'))
            if 500<=w<=900 and 350<=h<=650:self.dialog_click(w-50,h-28)
        except (ValueError,subprocess.CalledProcessError):pass
