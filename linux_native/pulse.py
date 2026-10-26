"""Isolated synthetic microphone; no host audio devices are opened."""
import json
import os
import subprocess
import time
import uuid


class VoiceSource:
    def __init__(self,*,call=None):
        self.environment=dict(os.environ,PULSE_SERVER='unix:/tmp/runtime-wechat/pulse/native',XDG_RUNTIME_DIR='/tmp/runtime-wechat')
        self.call=call or self._call
        self.sink='whale_'+uuid.uuid4().hex[:12]
        self.source=self.sink+'_mic'
        self.modules=[];self.previous=None;self.stream=None

    def _call(self,*args):
        return subprocess.run(['pactl',*map(str,args)],env=self.environment,capture_output=True,text=True,check=True,timeout=5).stdout.strip()

    def recordings(self):
        rows=json.loads(self.call('-f','json','list','source-outputs'))
        return [row for row in rows if row.get('properties',{}).get('application.process.binary','').lower() in ('wechat','weixin')]

    def __enter__(self):
        if self.recordings():raise ValueError('Client is already recording')
        self.previous=self.call('get-default-source')
        try:
            self.modules.append(self.call('load-module','module-null-sink','sink_name='+self.sink,'rate=24000','channels=1'))
            self.modules.append(self.call('load-module','module-remap-source','master='+self.sink+'.monitor','source_name='+self.source,
                'source_properties=device.description=WhaleVoice'))
            self.call('set-default-source',self.source)
            return self
        except Exception:
            self.__exit__(None,None,None)
            raise

    def route(self,*,active=lambda:True):
        until=time.monotonic()+3
        while active() and time.monotonic()<until:
            rows=self.recordings()
            if len(rows)>1:raise ValueError('Ambiguous client recording')
            if rows:
                self.stream=rows[0]['index']
                self.call('move-source-output',str(self.stream),self.source)
                self.call('set-source-output-mute',str(self.stream),'0')
                self.call('set-source-output-volume',str(self.stream),'100%')
                return
            time.sleep(.1)
        raise ValueError('Client recording did not start')

    def play(self,path,duration,*,active=lambda:True):
        if not 0<duration<=55:raise ValueError('Voice duration must be within 55 seconds')
        process=subprocess.Popen(['paplay','--device='+self.sink,'--latency-msec=50',str(path)],env=self.environment,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            until=time.monotonic()+duration+3
            while process.poll() is None:
                if not active() or time.monotonic()>until:raise ValueError('Voice playback cancelled')
                time.sleep(.1)
            if process.returncode:raise ValueError('Voice playback failed')
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=3)
        rows=self.recordings()
        if len(rows)!=1 or rows[0]['index']!=self.stream:raise ValueError('Client recording changed')

    def __exit__(self,*_):
        failed=False
        if self.previous:
            try:self.call('set-default-source',self.previous)
            except Exception:failed=True
        for module in reversed(self.modules):
            try:self.call('unload-module',module)
            except Exception:failed=True
        self.modules.clear()
        if failed:raise RuntimeError('Virtual microphone cleanup failed')
