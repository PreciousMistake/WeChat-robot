"""Bounded group voice recognition worker, isolated from the receive callback."""
from collections import deque
from dataclasses import replace
from io import BytesIO
import logging
import queue
import threading
import time
from types import MappingProxyType
import wave

logger=logging.getLogger('VoiceReceive')

def decode_silk(audio):
    from .voices import _inspect_data
    import pysilk
    _inspect_data(audio)  # validates packet boundaries, duration and size first
    class Output(BytesIO):
        def write(self,data):
            if self.tell()+len(data)>24000*2*60:raise ValueError('语音解码超出时长上限')
            return super().write(data)
    pcm=Output();pysilk.decode(BytesIO(audio),pcm,24000)
    data=pcm.getvalue()
    if not data or len(data)%2:raise ValueError('语音解码数据无效')
    output=BytesIO()
    with wave.open(output,'wb') as w:
        w.setparams((1,2,24000,0,'NONE','not compressed'));w.writeframes(data)
    return output.getvalue()

class VoiceInbox:
    def __init__(self,reader,client,deliver,current,groups,*,decode=decode_silk):
        self.reader,self.client,self.deliver,self.current=reader,client,deliver,current
        self.groups=frozenset(groups);self.decode=decode
        self.stop=threading.Event();self.queue=queue.Queue(maxsize=8)
        self.seen=deque(maxlen=1024);self.lock=threading.Lock()
        self.worker=threading.Thread(target=self._run,name='VoiceReceive.worker',daemon=True)
        self.worker.start()

    def _active(self,message):
        return not self.stop.is_set() and self.current() and 0<=time.monotonic()-message.received_at<60

    def submit(self,message):
        ident=message.data.get('native_message_id')
        if (message.type!=34 or not message.is_group or message.is_self or
                message.chat_id not in self.groups or not ident or str(ident)=='0' or not self._active(message)):
            return False
        key=(message.session_id,message.chat_id,str(ident))
        with self.lock:
            if key in self.seen:return False
            try:self.queue.put_nowait(message)
            except queue.Full:
                logger.warning('语音识别队列已满，本条未处理');return False
            self.seen.append(key)
        return True

    def _handle(self,message):
        active=lambda:self._active(message)
        if not active():return
        try:
            audio=None
            # WCDB may commit the row shortly after the native message callback.
            for _ in range(4):
                audio=self.reader.get(message.chat_id,str(message.data['native_message_id']),current=active)
                if audio or not active():break
                if self.stop.wait(.75):return
            if not audio:raise ValueError('语音尚未保存到本地数据库')
            if not active():return
            wav=self.decode(audio)
            if not active():return
            text=self.client.transcribe(wav,is_current=active)
            if not isinstance(text,str) or not text.strip() or len(text)>8000:raise ValueError('语音识别结果无效')
            # Only normalized metadata and transcription reach ordinary chat plugins.
            data={key:value for key,value in message.data.items() if key in (
                'native_message_id','sender_wechat_name','sender_id','is_at_self','sequence')}
            data.update(source_type=34,voice_status='ready',monitor_only=False,dispatch_to_plugins=True)
            result=replace(message,type=1,text=text.strip(),data=MappingProxyType(data))
        except Exception:
            data={'native_message_id':message.data.get('native_message_id'),
                  'monitor_only':True,'voice_status':'failed','voice_error':'语音读取或识别失败'}
            result=replace(message,data=MappingProxyType(data))
            logger.warning('群语音读取或识别失败：群ID=%s',message.chat_id)
        if active():self.deliver(result)

    def _run(self):
        try:
            while not self.stop.is_set():
                try:message=self.queue.get(timeout=.2)
                except queue.Empty:continue
                try:self._handle(message)
                except Exception:logger.warning('语音识别结果暂时无法投递')
                finally:self.queue.task_done()
        finally:self.reader.close()

    def close(self):
        self.stop.set()
        self.worker.join(timeout=1)
