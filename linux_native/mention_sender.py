"""Select real group members in the native @ popup, never type fake mentions."""
import io
import re
import subprocess
import time
import uuid
from .sender import ClientDriver,chat_regions,compact_name,caption_position,verify_title


def resolve_members(cache,account,room,identifiers):
    if cache.get('account')!=account or not isinstance(identifiers,list) or not 1<=len(identifiers)<=8 or len(set(identifiers))!=len(identifiers):raise ValueError('Invalid mention scope')
    rows=cache.get('groups',{}).get(room,[]);result=[]
    for identifier in identifiers:
        target=[row for row in rows if row.get('wxid')==identifier]
        if len(target)!=1 or identifier==account:raise ValueError('Mention target is not a verified other member')
        display=target[0].get('alias') or target[0].get('nickname') or ''
        if not display or len(display)>64 or any(ord(c)<32 for c in display):raise ValueError('Native member display name is unavailable')
        for row in rows:
            if row.get('wxid')==identifier:continue
            if any(compact_name(value)==compact_name(display) for value in (row.get('alias',''),row.get('nickname','')) if value):raise ValueError('Native mention name is ambiguous')
        result.append({'wxid':identifier,'display':display})
    return result


def verify_at_draft(copied,text,members):
    if not isinstance(copied,str) or not copied.endswith(text) or len(copied.encode('utf-8'))>1023:raise ValueError('Mention input differs from intended text')
    prefix=copied[:-len(text)]
    for member in members:
        token='@'+member['display']
        if prefix.count(token)!=1:raise ValueError('Selected native mention is absent from input')
        prefix=prefix.replace(token,'',1)
    if prefix.strip(' \t\r\n\u2005\u00a0'):raise ValueError('Mention input contains unexpected prefix')
    return True


class MentionDriver(ClientDriver):
    def _popups(self):
        result=[]
        for window in set(self.command('search','--all','--onlyvisible','--pid',str(self.pid)).split()):
            try:
                if self.command('getwindowpid',window).strip()!=str(self.pid):continue
                values=dict(line.split('=',1) for line in self.command('getwindowgeometry','--shell',window).splitlines() if '=' in line)
                x,y,w,h=(int(values[k]) for k in ('X','Y','WIDTH','HEIGHT'))
                if 100<=w<=400 and 24<=h<=400 and self.x+150<=x<=self.x+self.width and self.y+self.height-500<=y<=self.y+self.height-30:result.append((window,x,y,w,h))
            except subprocess.CalledProcessError:continue
        return result

    def _select_member(self,member):
        from PIL import ImageGrab,ImageOps
        self.key('at');self.owned_draft=self.owned_draft+'@'+member['display']
        self.clipboard(member['display']);self.key('ctrl+v');time.sleep(.25)
        until=time.monotonic()+2
        while True:
            popups=self._popups()
            if len(popups)==1:break
            if time.monotonic()>=until:raise ValueError('Unique native mention popup not found')
            time.sleep(.1)
        window,x,y,w,h=popups[0]
        image=ImageOps.grayscale(ImageGrab.grab(xdisplay=':99').crop((x+32,y+2,x+w-3,y+h-2)))
        image=image.resize((image.width*3,image.height*3));data=io.BytesIO();image.save(data,format='PNG')
        language='chi_sim+eng' if any(ord(c)>127 for c in member['display']) else 'eng'
        raw=subprocess.run(['tesseract','stdin','stdout','-l',language,'--psm','6','tsv'],input=data.getvalue(),capture_output=True,check=True,timeout=5).stdout.decode()
        point=caption_position(raw,member['display'])
        if point is None:raise ValueError('Native mention caption does not match member')
        self.assert_focus()
        if popups!=self._popups():raise ValueError('Native mention popup changed')
        self.command('mousemove',str(round(x+32+point[0]/3)),str(round(y+2+point[1]/3)),'click','1')
        until=time.monotonic()+1
        while any(p[0]==window for p in self._popups()):
            if time.monotonic()>=until:raise ValueError('Native mention selection did not finish')
            time.sleep(.05)
        self.key('ctrl+a','ctrl+c');time.sleep(.15);self.owned_draft=self.read_clipboard();self.key('End')

    def prepare_at(self,name,text,members):
        if not isinstance(text,str) or not text.strip() or len(text.encode())>1023:raise ValueError('Invalid mention text')
        self.select(name);point=chat_regions(self.width,self.height)['input'];self.click(point[0]/self.width,point[1]/self.height);self.ensure_empty_input()
        self.owned_draft='';self.prepared_name=name
        for member in members:self._select_member(member)
        self.clipboard(text);self.key('ctrl+v');self.key('ctrl+a','ctrl+c')
        until=time.monotonic()+1.5
        while True:
            copied=self.read_clipboard()
            if copied.endswith(text):break
            if time.monotonic()>=until:raise ValueError('Mention input differs from intended text')
            time.sleep(.05)
        verify_at_draft(copied,text,members);self.prepared_text=copied;self.owned_draft=copied
        self.key('End');verify_title(self.title(),name)

    def cleanup_at(self):
        # Only remove an exact draft we staged, never arbitrary user input.
        try:
            if self.command('getactivewindow').strip()!=self.window or self.command('getwindowpid',self.window).strip()!=str(self.pid):return
            verify_title(self.title(),self.prepared_name)
            # Escape on the idle main window opens the logout dialog in this
            # client. Leave an open member popup untouched rather than guessing.
            if self._popups():return
            self.assert_no_modal()
            self.clipboard(uuid.uuid4().hex)
            self.command('key','--window',self.window,'ctrl+a','ctrl+c');time.sleep(.15)
            copied=subprocess.run(['xclip','-selection','clipboard','-target','UTF8_STRING','-out'],capture_output=True,check=True,timeout=3).stdout.decode()
            if copied and copied==getattr(self,'owned_draft',None):self.command('key','--window',self.window,'BackSpace')
            else:self.command('key','--window',self.window,'End')
        except (ValueError,subprocess.CalledProcessError):pass
